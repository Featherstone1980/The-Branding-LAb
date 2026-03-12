import os
import re
import base64
import datetime
import requests
import concurrent.futures
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app) # CRITICAL: Prevents BigCommerce from blocking the request

@app.route('/')
def home():
    return "LOGISTICS_BRAIN_ACTIVE"

# 1. CREDENTIALS (Checks for both naming styles just in case)
SS_API_KEY = os.environ.get("SS_API_KEY") or os.environ.get("SHIPSTATION_API_KEY")
SS_API_SECRET = os.environ.get("SS_API_SECRET") or os.environ.get("SHIPSTATION_API_SECRET")

DIM_FACTOR_IN = 139 

# 2. CARRIER CODES (Fixed: Removed _walleted to stop "Invalid Carrier" errors)
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

def calc_billed_weight(actual_lbs, length=None, width=None, height=None) -> float:
    if length and width and height:
        dim_weight = (length * width * height) / DIM_FACTOR_IN
        return round(max(actual_lbs, dim_weight), 2)
    return float(actual_lbs)

def get_auth_header() -> dict:
    creds = base64.b64encode(f"{SS_API_KEY}:{SS_API_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

def fetch_carrier_rates(carrier_name, carrier_code, actual_weight, billed_weight, zip_code, to_country, ship_date):
    """Fetch all rates and pick the cheapest one available on your account."""
    try:
        payload = {
            "carrierCode": carrier_code,
            "fromPostalCode": "M5V2A1", # <--- WAREHOUSE ZIP
            "toCountry": to_country,
            "toPostalCode": zip_code,
            "weight": {"value": billed_weight, "units": "pounds"},
            "residential": True,
            "confirmation": "none",
            "shipDate": ship_date,
        }
        resp = requests.post("https://ssapi.shipstation.com/shipments/getrates", 
                             json=payload, headers=get_auth_header(), timeout=20)
        
        if resp.status_code != 200:
            return carrier_name, None, 0.0, f"SS Error: {resp.text[:50]}"

        raw_rates = resp.json()
        if not raw_rates:
            return carrier_name, None, 0.0, "No rates found"

        # Automatically pick the cheapest rate ShipStation offers
        raw_rates.sort(key=lambda r: r.get("shipmentCost", 0.0))
        best = raw_rates[0]
        total = best.get("shipmentCost", 0.0) + best.get("otherCost", 0.0)

        entry = {
            "weight_lbs": actual_weight,
            "service": best.get("serviceName"),
            "total_cost": round(total, 2),
            "days": best.get("transitDays", "Standard")
        }
        return carrier_name, entry, entry["total_cost"], None

    except Exception as exc:
        return carrier_name, None, 0.0, str(exc)

@app.route("/api/get-totals", methods=["POST"])
def get_totals():
    data = request.get_json()
    if not data: return jsonify({"error": "No Data"}), 400

    weights = data.get("weights", [10])
    zip_code = data.get("zip", "M5V2A1")
    to_country = detect_country(zip_code)
    ship_date = tomorrow_iso()

    grand_totals = {name: 0.0 for name in CARRIERS}
    breakdown = {name: [] for name in CARRIERS}
    errors = []

    tasks = []
    for weight in weights:
        billed = calc_billed_weight(weight, data.get("length"), data.get("width"), data.get("height"))
        for name, code in CARRIERS.items():
            if to_country == "US" and name in CA_ONLY_CARRIERS: continue
            tasks.append((name, code, weight, billed, zip_code, to_country, ship_date))

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks) or 1) as executor:
        futures = {executor.submit(fetch_carrier_rates, *t): t for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            name, entry, cost, err = future.result()
            if err:
                errors.append({"carrier": name, "error": err})
            elif entry:
                grand_totals[name] += cost
                breakdown[name].append(entry)

    # Clean up response: remove empty carriers
    final_totals = {k: round(v, 2) for k, v in grand_totals.items() if v > 0}
    final_breakdown = {k: v for k, v in breakdown.items() if v}

    return jsonify({
        "zip": zip_code,
        "grand_totals": final_totals,
        "breakdown": final_breakdown,
        "errors": errors if errors else None
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
