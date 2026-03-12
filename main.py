import os
import re
import base64
import datetime
import requests
import concurrent.futures
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return "LOGISTICS_BRAIN_ACTIVE"

# 1. CREDENTIALS - Synced to your Railway Variable names
SS_API_KEY = os.environ.get("SHIPSTATION_API_KEY")
SS_API_SECRET = os.environ.get("SHIPSTATION_API_SECRET")

def get_auth_header() -> dict:
    if not SS_API_KEY or not SS_API_SECRET:
        return {}
    # Encode credentials for Basic Auth
    auth_str = f"{SS_API_KEY.strip()}:{SS_API_SECRET.strip()}"
    creds = base64.b64encode(auth_str.encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json"
    }

# 2. CARRIER CONFIGURATION
CARRIERS = {
    "UPS": "ups",
    "Canada Post": "canada_post",
    "FedEx": "fedex",
    "Purolator": "purolator_ca"
}

def tomorrow_iso() -> str:
    return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

def detect_country(zip_code: str) -> str:
    z = zip_code.strip().replace(" ", "")
    # Simple regex for Canadian Postal Code
    if re.match(r"^[A-Za-z]\d[A-Za-z]\d[A-Za-z]\d$", z):
        return "CA"
    return "US"

def fetch_carrier_rates(carrier_name, carrier_code, weight, zip_code, to_country, ship_date):
    try:
        headers = get_auth_header()
        if not headers:
            return carrier_name, None, 0.0, "Railway Variables (API Keys) are missing or not found."

        payload = {
            "carrierCode": carrier_code,
            "fromPostalCode": "M5V2A1", # Warehouse Origin
            "toCountry": to_country,
            "toPostalCode": zip_code,
            "weight": {"value": float(weight), "units": "pounds"},
            "residential": True,
            "confirmation": "none",
            "shipDate": ship_date
        }
        
        resp = requests.post(
            "https://ssapi.shipstation.com/shipments/getrates",
            json=payload,
            headers=headers,
            timeout=15
        )
        
        if resp.status_code != 200:
            # Returning FULL text to diagnose if it fails
            return carrier_name, None, 0.0, f"Status {resp.status_code}: {resp.text}"

        raw_rates = resp.json()
        if not raw_rates:
            return carrier_name, None, 0.0, "ShipStation returned 0 rates for this carrier."

        # Sort by cost (cheapest first)
        raw_rates.sort(key=lambda r: r.get("shipmentCost", 0.0))
        best = raw_rates[0]
        total = best.get("shipmentCost", 0.0) + best.get("otherCost", 0.0)
        
        return carrier_name, {"service": best.get("serviceName"), "total_cost": round(total, 2)}, round(total, 2), None

    except Exception as e:
        return carrier_name, None, 0.0, str(e)

@app.route("/api/get-totals", methods=["POST"])
def get_totals():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body found"}), 400

    weights = data.get("weights", [10])
    zip_code = data.get("zip", "M5V2A1")
    to_country = detect_country(zip_code)
    ship_date = tomorrow_iso()

    final_results = {"grand_totals": {}, "breakdown": {}, "errors": []}

    # Parallel requests for speed
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for weight in weights:
            for name, code in CARRIERS.items():
                # Skip Canadian-only carriers for US destinations
                if to_country == "US" and name in ["Canada Post", "Purolator"]:
                    continue
                futures.append(executor.submit(fetch_carrier_rates, name, code, weight, zip_code, to_country, ship_date))

        for future in concurrent.futures.as_completed(futures):
            name, service_data, cost, err = future.result()
            if err:
                final_results["errors"].append({name: err})
            elif service_data:
                if name not in final_results["grand_totals"]:
                    final_results["grand_totals"][name] = 0
                    final_results["breakdown"][name] = []
                final_results["grand_totals"][name] += cost
                final_results["breakdown"][name].append(service_data)

    # Round final totals
    for name in final_results["grand_totals"]:
        final_results["grand_totals"][name] = round(final_results["grand_totals"][name], 2)

    return jsonify(final_results)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
