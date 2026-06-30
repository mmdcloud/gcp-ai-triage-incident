import logging
import random
import time
import os
from flask import Flask, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ERROR_SCENARIOS = [
    {
        "error": "DatabaseConnectionError",
        "message": "connection pool exhausted: max_connections=20, active=20",
        "service": "postgres-primary",
        "hint": "Recent deploy increased DB query count by 3x"
    },
    {
        "error": "DatabaseConnectionError",
        "message": "connection pool exhausted: max_connections=20, active=20",
        "service": "postgres-primary",
        "hint": "Recent deploy increased DB query count by 3x"
    },
    {
        "error": "DatabaseConnectionError",
        "message": "connection pool exhausted: max_connections=20, active=20",
        "service": "postgres-primary",
        "hint": "Recent deploy increased DB query count by 3x"
    },
]

@app.route("/")
def health():
    logger.info("Health check OK")
    return jsonify({"status": "ok", "service": "payment-api"}), 200


@app.route("/pay", methods=["GET", "POST"])
def pay():
    logger.info("Processing payment request")
    time.sleep(0.1)
    return jsonify({"status": "payment processed", "transaction_id": "txn_abc123"}), 200


@app.route("/crash")
def crash():
    scenario = ERROR_SCENARIOS[0]
    logger.error(
        f"CRITICAL [{scenario['error']}] {scenario['message']} | "
        f"downstream_service={scenario['service']} | "
        f"context={scenario['hint']}"
    )
    return jsonify({
        "error": scenario["error"],
        "message": scenario["message"],
        "downstream_service": scenario["service"]
    }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)