from neo_api_client import NeoAPI
import json, os
from dotenv import load_dotenv
load_dotenv()

ENV = input("Environment (prod/uat) [prod]: ").strip() or "prod"
CONSUMER_KEY = os.getenv("CONSUMER_KEY") or input("Consumer key: ").strip()
MOBILE = os.getenv("MOBILE_NUMBER") or input("Mobile: ").strip()
UCC = os.getenv("UCC") or input("UCC: ").strip()
MPIN = os.getenv("MPIN") or input("MPIN: ").strip()
TOTP = input("TOTP (6-digit): ").strip()

client = NeoAPI(environment=ENV, access_token=None, neo_fin_key=None, consumer_key=CONSUMER_KEY)

# Step 1: TOTP Login
print("\n=== STEP 1: totp_login ===")
r1 = client.totp_login(mobile_number=MOBILE, ucc=UCC, totp=TOTP)
print(json.dumps(r1, indent=2))
if r1.get("data", {}).get("status") != "success":
    print("FAILED:", r1.get("data", {}).get("message", "Login failed"))
    exit(1)

# Step 2: Validate MPIN
print("\n=== STEP 2: totp_validate ===")
r2 = client.totp_validate(mpin=MPIN)
print(json.dumps(r2, indent=2))
data = r2.get("data", {})
if data.get("status") != "success":
    print("FAILED:", data.get("message", "Validation failed"))
    exit(1)

# Step 3: Session diagnostics
print("\n=== SESSION DIAGNOSTICS ===")
cfg = client.configuration
print(f"  environment:      {getattr(cfg, 'host', None)}")
print(f"  consumer_key:     {bool(getattr(cfg, 'consumer_key', None))}")
print(f"  edit_token:       {bool(getattr(cfg, 'edit_token', None))}")
print(f"  edit_sid:         {bool(getattr(cfg, 'edit_sid', None))}")
print(f"  serverId:         {getattr(cfg, 'serverId', None)}")
print(f"  data_center:      {getattr(cfg, 'data_center', None)}")
print(f"  base_url:         {getattr(cfg, 'base_url', None)}")
print(f"  view_token:       {bool(getattr(cfg, 'view_token', None))}")
print(f"  greetingName:     {data.get('greetingName')}")
print(f"  ucc:              {data.get('ucc')}")
print(f"  isTrialAccount:   {data.get('isTrialAccount')}")
print(f"  dormancyStatus:   {data.get('dormancyStatus')}")
print(f"  clientType:       {data.get('clientType')}")

# Step 4: Try holdings (read-only, should work if session is valid)
print("\n=== STEP 4: holdings (read-only test) ===")
try:
    h = client.holdings()
    print(json.dumps(h, indent=2)[:600])
except Exception as e:
    print(f"EXCEPTION: {e}")

# Step 5: Try limits
print("\n=== STEP 5: limits ===")
try:
    lim = client.limits()
    print(json.dumps(lim, indent=2)[:600])
except Exception as e:
    print(f"EXCEPTION: {e}")

# Step 6: Place order
print("\n=== STEP 6: place_order ===")
symbol = input("\nTrading symbol [SBIN-EQ]: ").strip() or "SBIN-EQ"
qty = input("Quantity [1]: ").strip() or "1"
tx = input("Transaction type B/S [B]: ").strip().upper() or "B"
exch = input("Exchange segment [nse_cm]: ").strip() or "nse_cm"
prod = input("Product [MIS]: ").strip() or "MIS"
otype = input("Order type L/MKT/SL/SL-M [MKT]: ").strip() or "MKT"

order_params = dict(
    exchange_segment=exch,
    product=prod,
    price="0",
    order_type=otype,
    quantity=qty,
    validity="DAY",
    trading_symbol=symbol,
    transaction_type=tx,
    amo="NO",
    disclosed_quantity="0",
    market_protection="0",
    pf="N",
    trigger_price="0",
)
print(f"\nPlacing order: {json.dumps(order_params, indent=2)}")
try:
    order = client.place_order(**order_params)
    print(f"\nResponse: {json.dumps(order, indent=2)}")
except Exception as e:
    print(f"EXCEPTION: {e}")

print("\nDone.")
