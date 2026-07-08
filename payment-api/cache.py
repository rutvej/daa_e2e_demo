import redis
import json
import time

class SessionCache:
    def __init__(self, host: str, port: int, max_memory: str = "50mb"):
        self.redis = redis.Redis(host=host, port=port, decode_responses=True)
    
    def cache_checkout_session(self, user_id: str, transaction_id: str, session_data: dict):
        """Cache the checkout session for quick status lookups."""
        # Primary session key - has proper TTL
        session_key = f"session:{transaction_id}"
        self.redis.set(session_key, json.dumps(session_data))
        self.redis.expire(session_key, 3600)  # 1 hour TTL ✓
        
        # User's active sessions index - has proper TTL
        user_key = f"user_sessions:{user_id}"
        self.redis.sadd(user_key, transaction_id)
        self.redis.expire(user_key, 86400)  # 24 hour TTL ✓
        
        # ========================================================
        # Analytics tracking — PER-REQUEST unique key, NO TTL
        # This is a realistic oversight: developer added analytics
        # tracking and forgot the expire() call. Under normal traffic
        # (10 req/min) this is invisible. Under load (1000 req/min)
        # with 50MB Redis limit, it fills up in ~5 minutes.
        # ========================================================
        analytics_key = f"analytics:checkout:{user_id}:{transaction_id}:{int(time.time())}"
        analytics_data = {
            "user_id": user_id,
            "transaction_id": transaction_id,
            "cart": session_data.get("items", []),
            "amount": session_data.get("cart_total", 0),
            "timestamp": time.time(),
            "source": session_data.get("source", "web"),
            "user_agent": session_data.get("user_agent", "unknown"),
        }
        self.redis.set(analytics_key, json.dumps(analytics_data))
        # NOTE: No self.redis.expire() here — this is the bug
        # Each key is ~200 bytes. At 1000 req/min = 200KB/min = 12MB/hr
        # With maxmemory 50mb, Redis OOM in ~4 hours normal traffic
        # With ab -n 10000 -c 50, Redis OOM in ~2 minutes
