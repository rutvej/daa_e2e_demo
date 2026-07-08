from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

def test_health():
    # Set dummy env vars for initialization so the app doesn't crash on missing config
    # (though they are loaded during module import, we have defaults or they are fetched from env)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "payment-api"}
