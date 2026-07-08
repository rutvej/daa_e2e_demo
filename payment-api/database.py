import psycopg2
import os
from datetime import datetime

class Database:
    def __init__(self, url: str):
        self.url = url
        self._ensure_tables()
    
    def _get_conn(self):
        return psycopg2.connect(self.url)
    
    def _ensure_tables(self):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id VARCHAR(50) PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL,
                amount DECIMAL(10,2) NOT NULL,
                currency VARCHAR(3) DEFAULT 'USD',
                status VARCHAR(20) DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        conn.close()
    
    def insert_transaction(self, txn_id, user_id, amount, currency, status):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transactions (transaction_id, user_id, amount, currency, status) VALUES (%s, %s, %s, %s, %s)",
            (txn_id, user_id, amount, currency, status)
        )
        conn.commit()
        conn.close()
    
    def get_transaction(self, txn_id):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT transaction_id, user_id, amount, currency, status, created_at FROM transactions WHERE transaction_id = %s", (txn_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "transaction_id": row[0], "user_id": row[1],
            "amount": float(row[2]), "currency": row[3],
            "status": row[4], "created_at": str(row[5])
        }
    
    def update_status(self, txn_id, status):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE transactions SET status = %s, updated_at = NOW() WHERE transaction_id = %s", (status, txn_id))
        conn.commit()
        conn.close()
