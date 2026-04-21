import os
import re
import base64
import datetime
import requests
import concurrent.futures
from flask import Flask, request, jsonify
from flask_cors import CORS

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)

# SECURE GATE: Lock down CORS exclusively to authorized studio domain to prevent API limit bleeding
CORS(app, resources={r"/api/*": {"origins": list(("https://snarkymoose.com", "https://www.snarkymoose.com"))}}, allow_headers=list(("Content-Type", "X-Snarky-Auth")))

# THE PROXY BYPASS (CRITICAL FOR RAILWAY)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# INITIALIZE THE LIMITER (IN-MEMORY)
limiter = Limiter(
    get_remote_address,
    app=app,
    # TARGETED FIX: Array brackets purged for markdown parser immunity
    default_limits=list(), 
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

def get_auth_header() -> dict:
    if not SS_API_KEY or not SS_API_SECRET:
        return {}
    creds = base64.b64encode(f"{SS_API_KEY.strip()}:{SS_API_SECRET.strip()}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

# 2. CARRIERS
# CARRIERS
# TARGETED FIX: Purged FedEx and Purolator to stop ShipStation API rate limit crashing
CARRIERS = {
    "UPS": "ups_walleted",
    "Canada Post": "canada_post_walleted"
}
def tomorrow_iso() -> str:
    return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

def detect_country(zip_code: str) -> str:
    z = zip_code.strip().replace(" ", "")
    return "CA" if re.match(r"^[A-Za-z]\d[A-Za-z]\d[A-Za-z]\d$", z) else "US"

def fetch_carrier_rates(carrier_name, carrier_code, package, box_index, zip_code, to_country, ship_date):
    try:
        headers = get_auth_header()
        
        # Base payload with weight (brackets purged for parser immunity)
        payload = dict(
            carrierCode=carrier_code,
            fromPostalCode="K8N4M7", 
            toCountry=to_country,
            toPostalCode=zip_code,
            weight=dict(value=float(package.get("weight", 1.0)), units="pounds"),
            residential=False, 
            confirmation="none",
            shipDate=ship_date
        )
        
        # Add dimensions if they exist for this specific box
        if all(k in package for k in ("length", "width", "height")):
            payload.update(dict(dimensions=dict(
                units="inches",
                length=float(package.get("length")),
                width=float(package.get("width")),
                height=float(package.get("height"))
            )))

        resp = requests.post(
            "https://ssapi.shipstation.com/shipments/getrates",
            json=payload, headers=headers, timeout=15
        )
        
        if resp.status_code != 200:
            return carrier_name, box_index, None, f"Error {resp.status_code}: {resp.text}"

        raw_rates = resp.json()
        if not raw_rates:
            return carrier_name, box_index, None, "No rates available"

        parsed_rates = list()
        for r in raw_rates:
            # TARGETED FIX: The Transit Date Black Hole (API Days + 12 Day Buffer)
            raw_transit = r.get("transitDays")
            transit_days = int(raw_transit) if raw_transit else 5 # Fallback to 5 if ShipStation returns null
            total_lead_time = transit_days + 12
            
            # Injecting directly into the service string so it reaches both the UI and Make.com
            formatted_service = f"{r.get('serviceName')} (Est. {total_lead_time} Days)"

            parsed_rates.append(dict(
                service=formatted_service,
                cost=r.get("shipmentCost", 0.0) + r.get("otherCost", 0.0)
            ))
            
        return carrier_name, box_index, parsed_rates, None

    except Exception as e:
        return carrier_name, box_index, None, str(e)


# THE API SHIELD
@app.route("/api/get-totals", methods=("POST",))
@limiter.limit("15 per 10 minute", error_message="CRITICAL SECURE: API rate limit exceeded. Please wait 10 minutes.")
def get_totals():
    data = request.get_json()
    if not data: return jsonify(dict(error="No Data")), 400

    # TARGETED FIX: Array brackets explicitly purged. Fallback tuple/dict used.
    packages = data.get("packages", list((dict(weight=1.0),)))
    
    # SECURE GATE: Hard cap package array to prevent thread exhaustion DOS
    if len(packages) > 50:
        return jsonify(dict(error="CRITICAL: Maximum package limit exceeded.")), 400

    zip_code = data.get("zip", "K8N4M7")
    to_country = detect_country(zip_code)
    ship_date = tomorrow_iso()

    results = list()
    errors = list()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = list()
        for i, pkg in enumerate(packages):
            for name, code in CARRIERS.items():
                futures.append(executor.submit(fetch_carrier_rates, name, code, pkg, i, zip_code, to_country, ship_date))

        for future in concurrent.futures.as_completed(futures):
            c_name, b_idx, rates, err = future.result()
            if err:
                errors.append(dict({c_name: err}))
            elif rates:
                results.append(dict(carrier=c_name, box_index=b_idx, rates=rates))

    # Group the services together across all boxes
    final_rates = dict()
    for name in CARRIERS:
        # TARGETED FIX: Replaced list comprehension brackets with filter()
        c_results = list(filter(lambda r: r.get('carrier') == name, results))
        if not c_results or len(c_results) < len(packages):
            continue 
            
        service_agg = dict()
        for box_data in c_results:
            b_idx = box_data.get('box_index')
            for rate in box_data.get('rates'):
                s_name = rate.get('service')
                if s_name not in service_agg:
                    service_agg.update({s_name: dict(total=0.0, breakdown=list())})
                
                # TARGETED FIX: Dictionary bracket lookups replaced with .get() and .update()
                current_total = service_agg.get(s_name).get('total')
                service_agg.get(s_name).update(total=current_total + rate.get('cost'))
                service_agg.get(s_name).get('breakdown').append(dict(box=b_idx + 1, cost=rate.get('cost')))
        
        valid_services = list()
        for s_name, s_data in service_agg.items():
            if len(s_data.get('breakdown')) == len(packages):
                valid_services.append(dict(
                    service=s_name,
                    total_cost=round(s_data.get('total'), 2),
                    breakdown=sorted(s_data.get('breakdown'), key=lambda x: x.get('box'))
                ))
        
        valid_services.sort(key=lambda x: x.get('total_cost'))
        if valid_services:
            final_rates.update({name: valid_services})

    return jsonify(dict(rates=final_rates, errors=errors))

# THE SECURE WEBHOOK PROXY
@app.route("/api/submit-order", methods=("POST",))
@limiter.limit("5 per 10 minute", error_message="CRITICAL SECURE: Transmission limit exceeded.")
def submit_order():
    data = request.get_json()
    if not data: return jsonify(dict(error="No Payload provided")), 400
    
    # SECURE GATE: Asymmetric Payload Trust Firewall
    # Force server-side verification of the client's ledger math
    ledger = data.get("ledger")
    if ledger:
        expected_total = round(
            float(ledger.get("subtotal", 0)) + 
            float(ledger.get("vector_fee", 0)) + 
            float(ledger.get("shipping", 0)) + 
            float(ledger.get("taxes", 0)), 2
        )
        client_total = round(float(ledger.get("total_capex", 0)), 2)
        
        if expected_total != client_total:
            return jsonify(dict(error="CRITICAL SECURE: Ledger math anomaly detected. Payload rejected.")), 400

    try:
        make_webhook_url = os.environ.get("MAKE_WEBHOOK_URL")
        if not make_webhook_url:
            return jsonify(dict(error="Server configuration missing")), 500
            
        resp = requests.post(make_webhook_url, json=data, timeout=15)
        
        if resp.status_code == 200:
            return jsonify(dict(status="AUTHORIZED", detail="Payload securely routed")), 200
        else:
            return jsonify(dict(error="Webhook Rejected by external server")), resp.status_code
            
    except Exception as e:
        return jsonify(dict(error=str(e))), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
