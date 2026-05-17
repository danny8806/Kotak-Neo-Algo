import requests, json
from dotenv import load_dotenv
load_dotenv()
import os

ENV = input("Environment (prod/uat) [prod]: ").strip() or "prod"
CONSUMER_KEY = os.getenv("CONSUMER_KEY") or input("Consumer key: ").strip()
MOBILE = os.getenv("MOBILE_NUMBER") or input("Mobile: ").strip()
UCC = os.getenv("UCC") or input("UCC: ").strip()
MPIN = os.getenv("MPIN") or input("MPIN: ").strip()
TOTP = input("TOTP (6-digit): ").strip()

AUTH_BASE = "https://mis.kotaksecurities.com"
TRADE_BASE = "https://e21.kotaksecurities.com"

# ---- STEP 1: TOTP Login (PROD uses tradeApiLogin not v6/totp/login) ----
login_url = AUTH_BASE + "/login/1.0/tradeApiLogin"
login_headers = {
    "Authorization": CONSUMER_KEY,
    "Content-Type": "application/json",
    "neo-fin-key": "neotradeapi"
}
login_body = {"mobileNumber": MOBILE, "ucc": UCC, "totp": TOTP}
r1 = requests.post(login_url, headers=login_headers, json=login_body)
print("=== TOTP LOGIN ===")
print(f"URL: {r1.url}")
print(f"Status: {r1.status_code}")
try:
    d1 = r1.json()
    print(json.dumps(d1, indent=2)[:600])
    if d1.get("data", {}).get("status") != "success":
        print("LOGIN FAILED:", d1.get("data", {}).get("message", "unknown")); exit(1)
except Exception as e:
    print(f"Raw: {r1.text[:200]}"); exit(1)

view_token = d1["data"]["token"]
sid = d1["data"]["sid"]

# ---- STEP 2: TOTP Validate ----
validate_url = AUTH_BASE + "/login/1.0/tradeApiValidate"
validate_headers = {
    "Authorization": CONSUMER_KEY,
    "sid": sid,
    "Auth": view_token,
    "neo-fin-key": "neotradeapi",
    "Content-Type": "application/json"
}
validate_body = {"mpin": MPIN}
r2 = requests.post(validate_url, headers=validate_headers, json=validate_body)
print("\n=== TOTP VALIDATE ===")
print(f"URL: {r2.url}")
print(f"Status: {r2.status_code}")
try:
    d2 = r2.json()
    print(json.dumps(d2, indent=2)[:600])
    if d2.get("data", {}).get("status") != "success":
        print("VALIDATE FAILED:", d2.get("data", {}).get("message", "unknown")); exit(1)
except Exception as e:
    print(f"Raw: {r2.text[:200]}"); exit(1)

edit_token = d2["data"]["token"]
edit_sid = d2["data"]["sid"]
base_url = d2["data"].get("baseUrl", TRADE_BASE)
data_center = d2["data"].get("dataCenter", "")
print(f"\nbase_url: {base_url}, data_center: {data_center}")
print(f"edit_sid: {edit_sid[:30]}...")
# Also print the full raw response for debugging
print(f"\nFull raw d2 data keys: {list(d2.get('data', {}).keys())}")

# ---- STEP 3: Place Order (raw API call) ----
order_url = base_url + "/quick/order/rule/ms/place"
order_headers = {
    "Sid": edit_sid,
    "Auth": edit_token,
    "Content-Type": "application/x-www-form-urlencoded",
    "neo-fin-key": "neotradeapi"
}
order_body = {
    "am": "NO", "dq": "0", "es": "nse_cm", "mp": "0", "pc": "MIS",
    "pf": "N", "pr": "0", "pt": "MKT", "qt": "1", "rt": "DAY",
    "tp": "0", "ts": "SBIN-EQ", "tt": "B", "os": "NEOTRADEAPI"
}
jdata = {"jData": json.dumps(order_body)}
query = {"sId": data_center}

r3 = requests.post(order_url, headers=order_headers, params=query, data=jdata)
print(f"\n=== PLACE ORDER (direct, jData wrapper) ===")
print(f"URL: {r3.url}")
print(f"Status: {r3.status_code}")
try:
    print(f"Response: {json.dumps(r3.json(), indent=2)}")
except:
    print(f"Raw: {r3.text[:500]}")

# Without jData wrapper  
print("\n--- without jData wrapper ---")
r4 = requests.post(order_url, headers=order_headers, params=query, data=order_body)
print(f"Status: {r4.status_code}")
try:
    print(f"Response: {json.dumps(r4.json(), indent=2)}")
except:
    print(f"Raw: {r4.text[:500]}")

# Try without Content-Type override
print("\n--- with Content-Type: application/json ---")
h5 = dict(order_headers)
h5["Content-Type"] = "application/json"
# Remove jData, send order_body directly as JSON
r5 = requests.post(order_url, headers=h5, params=query, json=order_body)
print(f"Status: {r5.status_code}")
try:
    print(f"Response: {json.dumps(r5.json(), indent=2)}")
except:
    print(f"Raw: {r5.text[:500]}")
