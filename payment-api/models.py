from pydantic import BaseModel
from typing import List

class CheckoutRequest(BaseModel):
    user_id: str
    cart_total: float
    currency: str = "USD"
    items: List[str] = []
