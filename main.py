from flask import Flask, request, jsonify
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)

# 1. THE FRONT PORCH (To kill the 404)
@app.route('/')
def home():
    return "Logistics Brain is ONLINE. Use /api/health to check status."

# 2. THE HEALTH CHECK
@app.route('/api/health')
def health():
    return jsonify({"status": "Logistics Brain Awake", "version": "V20.21"})

# 3. THE RATE ENGINE
@app.route('/api/get-totals', methods=['POST'])
def get_totals():
    try:
        data = request.json
        # This is the test response to verify connection
        return jsonify({
            "status": "connected",
            "received_zip": data.get('zip'),
            "message": "ShipStation Handshake Ready"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
   if __name__ == "__main__":
    app.run()
