import os
import re
import base64
import datetime
import requests
import concurrent.futures
from flask import Flask, request, jsonify
from flask_cors import CORS

# 1. IMPORT THE SHIELD MODULES
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
CORS(app)

# 2. THE PROXY BYPASS (CRITICAL FOR RAILWAY)
# Tells Flask to read the actual user's IP, not the Railway Load Balancer's IP
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# 3. INITIALIZE THE LIMITER (IN-MEMORY)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[], # We leave this empty to only limit specific routes
    storage_uri="memory://",
)

@app.route('/')
def home():
    return "LOGISTICS_BRAIN_ACTIVE"

@app.route('/api/health')
def health():
    return jsonify({"status": "Logistics Brain Awake", "version": "V7.0 - Multi-Dimension Boxes"})

# 1. CREDENTIALS
SS_API_KEY = os.environ.get("SHIPSTATION_API_KEY")
SS_API_SECRET = os.environ.get("SHIPSTATION_API_SECRET")
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
    return jsonify({"status": "Logistics Brain Awake", "version": "V7.0 - Multi-Dimension Boxes"})

# 1. CREDENTIALS
SS_API_KEY = os.environ.get("SHIPSTATION_API_KEY")
SS_API_SECRET = os.environ.get("SHIPSTATION_API_SECRET")

def get_auth_header() -> dict:
    if not SS_API_KEY or not SS_API_SECRET:
        return {}
    creds = base64.b64encode(f"{SS_API_KEY.strip()}:{SS_API_SECRET.strip()}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

# 2. CARRIERS
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

def fetch_carrier_rates(carrier_name, carrier_code, package, box_index, zip_code, to_country, ship_date):
    try:
        headers = get_auth_header()
        
        # Base payload with weight
        payload = {
            "carrierCode": carrier_code,
            "fromPostalCode": "M5V2A1", 
            "toCountry": to_country,
            "toPostalCode": zip_code,
            "weight": {"value": float(package.get("weight", 1.0)), "units": "pounds"},
            "residential": False, 
            "confirmation": "none",
            "shipDate": ship_date,
        }
        
        # Add dimensions if they exist for this specific box
        if all(k in package for k in ("length", "width", "height")):
            payload["dimensions"] = {
                "units": "inches",
                "length": float(package["length"]),
                "width": float(package["width"]),
                "height": float(package["height"])
            }

        resp = requests.post(
            "https://ssapi.shipstation.com/shipments/getrates",
            json=payload, headers=headers, timeout=15
        )
        
        if resp.status_code != 200:
            return carrier_name, box_index, None, f"Error {resp.status_code}: {resp.text}"

        raw_rates = resp.json()
        if not raw_rates:
            return carrier_name, box_index, None, "No rates available"

        parsed_rates = []
        for r in raw_rates:
            parsed_rates.append({
                "service": r.get("serviceName"),
                "cost": r.get("shipmentCost", 0.0) + r.get("otherCost", 0.0)
            })
            
        return carrier_name, box_index, parsed_rates, None

    except Exception as e:
        return carrier_name, box_index, None, str(e)

@app.route("/api/get-totals", methods=["POST"])
def get_totals():
    data = request.get_json()
    if not data: return jsonify({"error": "No Data"}), 400

    # Look for an array of packages with exact sizes, fallback to a single 1lb box if empty
    packages = data.get("packages", [{"weight": 1.0}])
    zip_code = data.get("zip", "M5V2A1")
    to_country = detect_country(zip_code)
    ship_date = tomorrow_iso()

    results = []
    errors = []

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for i, pkg in enumerate(packages):
            for name, code in CARRIERS.items():
                futures.append(executor.submit(fetch_carrier_rates, name, code, pkg, i, zip_code, to_country, ship_date))

        for future in concurrent.futures.as_completed(futures):
            c_name, b_idx, rates, err = future.result()
            if err:
                errors.append({c_name: err})
            elif rates:
                results.append({"carrier": c_name, "box_index": b_idx, "rates": rates})

    # Group the services together across all boxes
    final_rates = {}
    for name in CARRIERS:
        c_results = [r for r in results if r['carrier'] == name]
        if not c_results or len(c_results) < len(packages):
            continue 
            
        service_agg = {}
        for box_data in c_results:
            b_idx = box_data['box_index']
            for rate in box_data['rates']:
                s_name = rate['service']
                if s_name not in service_agg:
                    service_agg[s_name] = {'total': 0.0, 'breakdown': []}
                service_agg[s_name]['total'] += rate['cost']
                service_agg[s_name]['breakdown'].append({"box": b_idx + 1, "cost": rate['cost']})
        
        valid_services = []
        for s_name, s_data in service_agg.items():
            if len(s_data['breakdown']) == len(packages):
                valid_services.append({
                    "service": s_name,
                    "total_cost": round(s_data['total'], 2),
                    "breakdown": sorted(s_data['breakdown'], key=lambda x: x['box'])
                })
        
        valid_services.sort(key=lambda x: x['total_cost'])
        if valid_services:
            final_rates[name] = valid_services

    return jsonify({"rates": final_rates, "errors": errors})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
