import os
import uuid
import json
import pika
from fastapi import FastAPI, HTTPException
from daa_sdk import DaaSdk           # SDK baked in
from cache import SessionCache
from database import Database
from models import CheckoutRequest

app = FastAPI(title="PayFlow API", version="1.0.0")

# Initialize connections
redis_cache = SessionCache(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", 6379))
)
db = Database(os.getenv("DATABASE_URL", "postgresql://user:pass@postgres/payflow"))
daa = DaaSdk(backend_url=os.getenv("DAA_BACKEND_API_URL"))

# RabbitMQ connection for publishing
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")

@app.post("/checkout")
def checkout(req: CheckoutRequest):
    trace_id = f"trace-{uuid.uuid4().hex[:12]}"
    txn_id = f"txn_{uuid.uuid4().hex[:8]}"
    
    try:
        # 1. Cache session in Redis
        redis_cache.cache_checkout_session(
            user_id=req.user_id,
            transaction_id=txn_id,
            session_data=req.model_dump()
        )
    except Exception as e:
        daa.capture_exception(e)  # SDK reports to DAA
        raise HTTPException(status_code=503, detail=f"Cache error: {str(e)}")
    
    try:
        # 2. Store in PostgreSQL
        db.insert_transaction(txn_id, req.user_id, req.cart_total, req.currency, "PENDING")
    except Exception as e:
        daa.capture_exception(e)
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)}")
    
    try:
        # 3. Publish to RabbitMQ
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
        channel = connection.channel()
        channel.queue_declare(queue="payment_jobs", durable=True)
        channel.basic_publish(
            exchange="",
            routing_key="payment_jobs",
            body=json.dumps({
                "transaction_id": txn_id,
                "user_id": req.user_id,
                "amount": req.cart_total,
                "currency": req.currency,
                "trace_id": trace_id
            }),
            properties=pika.BasicProperties(delivery_mode=2)  # persistent
        )
        connection.close()
    except Exception as e:
        daa.capture_exception(e)
        raise HTTPException(status_code=503, detail=f"Queue error: {str(e)}")
    
    return {"status": "PENDING", "transaction_id": txn_id, "trace_id": trace_id}

@app.get("/status/{transaction_id}")
def get_status(transaction_id: str):
    txn = db.get_transaction(transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return txn

@app.get("/health")
def health():
    return {"status": "healthy", "service": "payment-api"}
