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

@app.route('/api/health')
def health():
    return jsonify({"status": "Logistics Brain Awake", "version": "V3.0-Universal"})

# 1. SHIPSTATION CREDENTIALS
# Ensure these variables are in Railway: SHIPSTATION_API_KEY, SHIPSTATION_API_SECRET
SS_API_KEY = os.environ.get("SHIPSTATION_API_KEY")
SS_API_SECRET = os.environ.get("SHIPSTATION_API_SECRET")

# 2. CARRIER CODES (Updated for compatibility)
CARRIERS = {
    "UPS": "ups",
    "Canada Post": "canada_post",
    "FedEx": "fedex",
    "Purolator": "purolator_ca"
}

CA_ONLY_CARRIERS = {"Canada Post", "Purolator"}

def tomorrow_iso() -> str:
    return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

def detect_country(zip_code: str) -> str:
    z = zip_code.strip().replace(" ", "")
    return "CA" if re.match(r"^[A-Za-z]\d[A-Za-z]\d[A-Za-z]\d$", z) else "US"

def get_auth_header() -> dict:
    creds = base64.b64encode(f"{SS_API_KEY}:{SS_API_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

def fetch_carrier_rates(carrier_name, carrier_code, weight, zip_code, to_country, ship_date):
    try:
        payload = {
            "carrierCode": carrier_code,
            "fromPostalCode": "M5V2A1", # Ensure this matches your Warehouse Zip in ShipStation
            "toCountry": to_country,
            "toPostalCode": zip_code,
            "weight": {"value": weight, "units": "pounds"},
            "residential": True,
            "confirmation": "none",
            "shipDate": ship_date,
        }
        
        resp = requests.post(
            "https://ssapi.shipstation.com/shipments/getrates", 
            json=payload, 
            headers=get_auth_header(), 
            timeout=15
        )
        
        if resp.status_code != 200:
            return carrier_name, None, 0.0, f"Error: {resp.text[:50]}"

        raw_rates = resp.json()
        if not raw_rates:
            return carrier_name, None, 0.0, "No rates available for this carrier"

        # Sort by cost and pick the cheapest available option automatically
        raw_rates.sort(key=lambda r: r.get("shipmentCost", 0.0))
        best = raw_rates[0]
        
        cost = best.get("shipmentCost", 0.0) + best.get("otherCost", 0.0)
        
        service_data = {
            "service": best.get("serviceName"),
            "total_cost": round(cost, 2),
            "days": best.get("transitDays", "Standard")
        }

        return carrier_name, service_data, service_data["total_cost"], None
        
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
                if to_country == "US" and name in CA_ONLY_CARRIERS: continue
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

    # Round totals
    for name in final_results["grand_totals"]:
        final_results["grand_totals"][name] = round(final_results["grand_totals"][name], 2)

    return jsonify(final_results)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
