
import os
import requests
from flask import Flask, jsonify

app = Flask(__name__)

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

@app.route("/api/price/<symbol>")
def price(symbol):
    if not API_KEY:
        return jsonify({
            "error": "Falta configurar TWELVE_DATA_API_KEY en Render"
        }), 503

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
        return jsonify({"error": data.get("message", "No se pudo obtener el precio")}), 502

    return jsonify({
        "symbol": symbol.upper(),
        "price": float(data["price"])
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
