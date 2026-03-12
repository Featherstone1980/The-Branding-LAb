import os
import requests
import base64
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# 1. SHIPSTATION CREDENTIALS (Pulled from your Railway Variables)
API_KEY = os.environ.get('SHIPSTATION_API_KEY')
API_SECRET = os.environ.get('SHIPSTATION_API_SECRET')

def get_auth_header():
    auth_str = f"{API_KEY}:{API_SECRET}"
    encoded_auth = base64.b64encode(auth_str.encode()).decode()
    return {"Authorization": f"Basic {encoded_auth}", "Content-Type": "application/json"}

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "Logistics Brain Awake", "version": "V1.0-Live"})

@app.route('/api/get-totals', methods=['POST'])
def get_totals():
    try:
        payload = request.json
        dest_zip = payload.get('zip')
        weights = payload.get('weights', [1.0]) # Defaults to 1lb if empty

        total_shipping_cost = 0
        all_rates = []

        # We loop through each box weight and get a rate from ShipStation
        for weight in weights:
            ss_payload = {
                "carrierCode": "fedex", # Change to 'ups' or 'canada_post' as needed
                "fromPostalCode": "V6B 1A1", # <--- CHANGE THIS TO YOUR WAREHOUSE ZIP
                "toPostalCode": dest_zip,
                "toCountry": "CA",
                "weight": {"value": weight, "units": "pounds"},
                "dimensions": {"units": "inches", "length": 12, "width": 12, "height": 12},
                "confirmation": "none",
                "residential": True
            }

            response = requests.post(
                "https://ssapi.shipstation.com/shipments/getrates",
                headers=get_auth_header(),
                json=ss_payload
            )
            
            if response.status_code == 200:
                rates = response.json()
                if rates:
                    # We pick the first (usually cheapest) rate option
                    best_rate = rates[0].get('shipmentCost', 0)
                    total_shipping_cost += best_rate
                    all_rates.append(best_rate)
            else:
                return jsonify({"error": "ShipStation API Error", "details": response.text}), response.status_code

        return jsonify({
            "total_shipping": round(total_shipping_cost, 2),
            "breakdown": all_rates,
            "currency": "CAD"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
