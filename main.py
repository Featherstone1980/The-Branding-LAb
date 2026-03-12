import os
import base64
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
# Allows your BigCommerce website to talk to this server safely
CORS(app)

# --- THE LEAN MATH CONSTANTS ---
MARKUP_MULTIPLIER = 1.05  # 5% Safety Shield
BOX_HANDLING_FEE = 5.00   # $5.00 per physical box

# --- CREDENTIALS (Set these in Railway Dashboard later) ---
API_KEY = os.environ.get("SHIPSTATION_API_KEY", "")
API_SECRET = os.environ.get("SHIPSTATION_API_SECRET", "")
ORIGIN_ZIP = os.environ.get("ORIGIN_ZIP", "K8N 1A1") # Belleville Default

def get_auth_header():
    credentials = f"{API_KEY}:{API_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "Logistics Brain Awake", "version": "V20.21"})

@app.route('/api/get-totals', methods=['POST'])
def get_totals():
    data = request.json
    dest_zip = data.get('zip', '').replace(' ', '')
    weights = data.get('weights', [])
    
    if not dest_zip or not weights:
        return jsonify({"error": "Missing postal code or weights"}), 400

    box_count = len(weights)
    total_handling_fee = BOX_HANDLING_FEE * box_count

    # The carriers connected to your ShipStation account
    carriers_to_check = ["canadapost", "ups", "fedex"]
    
    breakdown = {}

    for carrier in carriers_to_check:
        carrier_total_services = {}
        
        # Calculate rates for each box
        for weight in weights:
            payload = {
                "carrierCode": carrier,
                "fromPostalCode": ORIGIN_ZIP,
                "toCountry": "CA",
                "toPostalCode": dest_zip,
                "weight": {
                    "value": weight,
                    "units": "pounds"
                }
            }
            
            try:
                # Ping ShipStation
                url = "https://ssapi.shipstation.com/shipments/getrates"
                resp = requests.post(url, json=payload, headers=get_auth_header())
                
                if resp.status_code == 200:
                    rates = resp.json()
                    for rate in rates:
                        service_name = rate.get('serviceName')
                        raw_cost = rate.get('shipmentCost', 0)
                        days = rate.get('transitDays', 4)
                        
                        if service_name not in carrier_total_services:
                            carrier_total_services[service_name] = {"cost": 0.0, "days": days}
                        
                        carrier_total_services[service_name]["cost"] += raw_cost
            except Exception as e:
                print(f"Error fetching {carrier}: {e}")
                continue
        
        # Apply the Blueprint Math
        if carrier_total_services:
            formatted_services = []
            for name, details in carrier_total_services.items():
                raw_total = details["cost"]
                # THE LEAN MATH EXECUTION
                final_cost = (raw_total * MARKUP_MULTIPLIER) + total_handling_fee
                
                formatted_services.append({
                    "service": name,
                    "total_cost": round(final_cost, 2),
                    "days": details["days"]
                })
            
            # Format to perfectly match what your the-lab-checkout.js expects
            display_name = carrier.upper().replace('CANADAPOST', 'CANADA POST')
            breakdown[display_name] = [{"services": formatted_services}]

    return jsonify({"breakdown": breakdown})

if __name__ == '__main__':
    # Gunicorn will handle this in Railway
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
