import os
import re
import base64
import datetime
import requests
import concurrent.futures
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app) # This prevents the "Failed to Fetch" error in your browser

@app.route('/')
def home():
    return "LOGISTICS_BRAIN_ACTIVE"

@app.route('/api/health')
def health():
    return jsonify({"status": "Logistics Brain Awake", "version": "V2.0-Production"})

# 1. SHIPSTATION CREDENTIALS (Pulled from Railway Variables)
SS_API_KEY = os.environ.get("SHIPSTATION_API_KEY")
SS_API_SECRET = os.environ.get("SHIPSTATION_API_SECRET")

# 2. CARRIER SETTINGS 
# We use standard codes. If you use 'ShipStation Carriers', change these back to _walleted
CARRIERS = {
    "UPS": "ups",
    "Canada Post": "canada_post",
    "FedEx": "fedex",
    "Purolator": "purolator",
}

CA_ONLY_CARRIERS = {"Canada Post", "Purolator"}
DIM_FACTOR_IN = 139 

# Fallback days if ShipStation doesn't provide an ETA
DAYS_FALLBACK = {
    "ups_standard": 4, "fedex_ground": 4, "expedited_parcel": 4, "purolator_ground": 4,
    "ups_2nd_day_air": 2, "xpresspost": 2, "fedex_2day": 2, "purolator_express": 2,
    "ups_next_day_air": 1, "priority": 1, "fedex_priority_overnight": 1
}

def tomorrow_iso() -> str:
    return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

def resolve_transit(service_code: str, raw_eta, raw_days, ship_date: str) -> dict:
    days = int(round(raw_days)) if raw_days is not None else DAYS_FALLBACK.get(service_code, 4)
    eta = raw_eta if raw_eta else (datetime.date.fromisoformat(ship_date) + datetime.timedelta(days=days)).isoformat()
    return {"eta": eta, "days": days}

# Only these services will be shown to the customer
ALLOWED_SERVICE_CODES = {
    "UPS": {"ups_standard", "ups_2nd_day_air", "ups_next_day_air"},
    "Canada Post": {"expedited_parcel", "xpresspost", "priority"},
    "FedEx": {"fedex_ground", "fedex_2day", "fedex_priority_overnight"},
    "Purolator": {"purolator_ground", "purolator_express"}
}

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
            "fromPostalCode": "M5V2A1", # <--- DOUBLE CHECK THIS IS YOUR WAREHOUSE ZIP
            "toCountry": to_country,
            "toPostalCode": zip_code,
            "weight": {"value": weight, "units": "pounds"},
            "residential": True,
            "confirmation": "none",
            "shipDate": ship_date,
        }
        resp = requests.post("https://ssapi.shipstation.com/shipments/getrates", 
                             json=payload, headers=get_auth_header(), timeout=15)
        
        if resp.status_code != 200:
            return carrier_name, None, 0.0, f"SS Error: {resp.text[:100]}"

        raw = resp.json()
        allowed = ALLOWED_SERVICE_CODES.get(carrier_name, set())
        filtered = [r for r in raw if r.get("serviceCode") in allowed]
        filtered.sort(key=lambda r: r.get("shipmentCost", 0.0))

        services = []
        for r in filtered:
            cost = r.get("shipmentCost", 0.0) + r.get("otherCost", 0.0)
            transit = resolve_transit(r.get("serviceCode"), r.get("estimatedDeliveryDate"), r.get("transitDays"), ship_date)
            services.append({
                "service": r.get("serviceName"),
                "total_cost": round(cost, 2),
                "days": transit["days"]
            })

        cheapest = services[0]["total_cost"] if services else 0.0
        return carrier_name, services, cheapest, None
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

    # Parallel processing to keep it fast
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for weight in weights:
            for name, code in CARRIERS.items():
                if to_country == "US" and name in CA_ONLY_CARRIERS: continue
                futures.append(executor.submit(fetch_carrier_rates, name, code, weight, zip_code, to_country, ship_date))

        for future in concurrent.futures.as_completed(futures):
            name, services, cost, err = future.result()
            if err:
                final_results["errors"].append({name: err})
            elif services:
                if name not in final_results["grand_totals"]:
                    final_results["grand_totals"][name] = 0
                    final_results["breakdown"][name] = []
                final_results["grand_totals"][name] += cost
                final_results["breakdown"][name].append(services[0])

    return jsonify(final_results)

if __name__ == "__main__":
    # Force Port 8080 for Railway
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
