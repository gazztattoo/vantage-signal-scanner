
import os
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "https://gazztattoo.github.io"
            ]
        }
    }
)

API_KEY = os.environ.get("TWELVE_DATA_API_KEY")


@app.route("/")
def home():
    return jsonify({
        "app": "Vantage Signal Scanner API",
        "status": "online",
        "message": "Servidor preparado"
    })


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/price")
def price():
    if not API_KEY:
        return jsonify({
            "error": "Falta configurar la clave de Twelve Data"
        }), 503

    symbol = request.args.get("symbol", "").strip().upper()

    allowed_symbols = ["EUR/USD", "XAU/USD", "BTC/USD"]

    if symbol not in allowed_symbols:
        return jsonify({
            "error": "Símbolo no permitido",
            "allowed_symbols": allowed_symbols
        }), 400

    try:
        response = requests.get(
            "https://api.twelvedata.com/price",
            params={
                "symbol": symbol,
                "apikey": API_KEY
            },
            timeout=15
        )

        data = response.json()

        if response.status_code != 200 or "price" not in data:
            return jsonify({
                "error": data.get(
                    "message",
                    "No se pudo obtener el precio"
                )
            }), 502

        return jsonify({
            "symbol": symbol,
            "price": float(data["price"])
        })

    except (requests.RequestException, ValueError):
        return jsonify({
            "error": "No se pudo consultar el proveedor de precios"
        }), 502


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
