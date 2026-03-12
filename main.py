import os
import re
import base64
import datetime
import requests
import concurrent.futures
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app) # Keeps the browser from blocking the connection

@app.route('/')
def home():
    return "LOGISTICS_BRAIN_ACTIVE"

# 1. CREDENTIALS
SS_API_KEY = os.environ.get("SHIPSTATION_API_KEY")
SS_API_SECRET = os.environ.get("SHIPSTATION_API_SECRET")

def get_auth_header() -> dict:
    if not SS_API_KEY or not SS_API_SECRET:
        return {}
    creds = base64.b64encode(f"{SS_API_KEY.strip()}:{SS_API_SECRET.strip()}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

# 2. YOUR ORIGINAL WORKING CARRIERS
CARRIERS = {
    "UPS": "ups_walleted",
    "Canada Post": "canada_post_walleted",
    "FedEx": "fedex_walleted",
    "Purolator": "purolator_walleted"
}

def tomorrow_iso() -> str:
    return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

def detect_country(zip_code: str) -> str:
    z = zip_code.strip().replace(" ", "")
    return "CA" if re.match(r"^[A-Za-z]\d[A-Za-z]\d[A-Za-z]\d$", z) else "US"

def fetch_carrier_rates(carrier_name, carrier_code, weight, zip_code, to_country, ship_date):
    try:
        headers = get_auth_header()
        
        payload = {
            "carrierCode": carrier_code,
            "fromPostalCode": "M5V2A1", # Your original origin
            "toCountry": to_country,
            "toPostalCode": zip_code,
            "weight": {"value": float(weight), "units": "pounds"},
            "residential": False, # Reverted to your original setting
            "confirmation": "none",
            "shipDate": ship_date,
        }
        
        resp = requests.post(
            "https://ssapi.shipstation.com/shipments/getrates",
            json=payload,
            headers=headers,
            timeout=15
        )
        
        # No more character limit on errors
        if resp.status_code != 200:
            return carrier_name, None, 0.0, f"Error {resp.status_code}: {resp.text}"

        raw_rates = resp.json()
        if not raw_rates:
            return carrier_name, None, 0.0, "No rates available"

        # Sort and grab cheapest
        raw_rates.sort(key=lambda r: r.get("shipmentCost", 0.0))
        best = raw_rates[0]
        cost = best.get("shipmentCost", 0.0) + best.get("otherCost", 0.0)
        
        return carrier_name, {"service": best.get("serviceName"), "total_cost": round(cost, 2)}, round(cost, 2), None

    except Exception as e:
        return carrier_name, None, 0.0, str(e)

@app.route("/api/get-totals", methods=["POST"])
def get_totals():
    data = request.get_json()
    if not data: return jsonify({"error": "No Data"}), 400

    weights = data.get("weights", [10])
    zip_code = data.get("zip", "M5V2A1")
    to_country = detect_country(zip_code)
    ship_date = tomorrow_iso()

    final_results = {"grand_totals": {}, "breakdown": {}, "errors": []}

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for weight in weights:
            for name, code in CARRIERS.items():
                if to_country == "US" and name in ["Canada Post", "Purolator"]: continue
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

    for name in final_results["grand_totals"]:
        final_results["grand_totals"][name] = round(final_results["grand_totals"][name], 2)

    return jsonify(final_results)

if __name__ == "__main__":
    # The one thing we must keep so the server doesn't 404
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
