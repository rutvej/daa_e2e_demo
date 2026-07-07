import os
import time
import uuid
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock Checkout Service", version="1.0.0")

PAYMENT_SERVICE_URL = os.environ.get("PAYMENT_SERVICE_URL", "http://localhost:8002/pay")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis-cache")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")

class RedisCache:
    def connect(self):
        print("Successfully connected to Redis cache client.")
        return True
        
    def connec(self):
        # Misspelled method name causing AttributeException simulation
        raise AttributeError("'RedisCache' object has no attribute 'connec'. Did you mean: 'connect'?")

class CheckoutRequest(BaseModel):
    user_id: str
    cart_total: float
    currency: str = "USD"

@app.post("/checkout")
def process_checkout(req: CheckoutRequest):
    trace_id = f"trace-{uuid.uuid4().hex[:12]}"
    print(f"[{trace_id}] Starting checkout for user {req.user_id} (${req.cart_total})")
    
    # 1. Simulate Redis connection check
    if "fail_redis" in req.user_id:
        cache = RedisCache()
        # Buggy line to be fixed by SRE agent:
        cache.connec()

    # 2. Simulate call to downstream Payment Service
    try:
        pay_res = requests.post(PAYMENT_SERVICE_URL, json={"amount": req.cart_total, "trace_id": trace_id}, timeout=3.0)
        if pay_res.status_code == 402:
            try:
                err_detail = pay_res.json().get("detail", "Card declined or insufficient funds")
            except Exception:
                err_detail = pay_res.text
            raise HTTPException(status_code=402, detail=err_detail)
        elif pay_res.status_code != 200:
            raise HTTPException(status_code=502, detail=pay_res.text)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail="Payment service unavailable")

    return {"status": "SUCCESS", "transaction_id": f"txn_{uuid.uuid4().hex[:8]}", "trace_id": trace_id}

@app.get("/health")
def health():
    return {"status": "healthy", "service": "checkout-service"}
