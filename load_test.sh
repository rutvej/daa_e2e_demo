#!/bin/bash
# Generate a large payload.json (100KB) to hit the 50MB Redis limit quickly
python3 -c 'import json; data = {"user_id": "usr_loadtest", "cart_total": 99.99, "currency": "USD", "items": ["X" * 100000]}; json.dump(data, open("payload.json", "w"))'

# Run Apache Benchmark
echo "Starting load test using Apache Benchmark (ab)..."
ab -n 1000 -c 50 -p payload.json -T application/json http://localhost:8001/checkout
