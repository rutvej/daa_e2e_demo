from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

def test_health():
    res = client.get("/health")
    assert res.status_code == 200

def test_checkout_success():
    res = client.post("/checkout", json={"user_id": "normal_user", "cart_total": 99.99})
    # Since normal_user doesn't trigger the fail_redis block, this should pass
    # (Note: payment-service might be down during local test run, but we mock or catch connection errors)
    # Actually, we can check if it returns 200 or downstream connection status code (503) instead of 500 AttributeError
    assert res.status_code in [200, 503]

def test_checkout_fail_redis_fixed():
    # If the bug is fixed (connec replaced with connect), this should NOT raise AttributeError (which returns 500)
    # It should either succeed (200) or get downstream connection error (503)
    res = client.post("/checkout", json={"user_id": "fail_redis", "cart_total": 99.99})
    assert res.status_code in [200, 503]
