from neo_api_client import NeoAPI
import json

ENV = "prod"  # change to "uat" if needed

CONSUMER_KEY = input("Consumer key: ").strip()
MOBILE = input("Mobile (with country code, e.g. +918806160767): ").strip()
UCC = input("UCC (e.g. XVV27): ").strip()
TOTP = input("TOTP (6-digit from authenticator): ").strip()
MPIN = input("MPIN (6-digit): ").strip()

client = NeoAPI(environment=ENV, access_token=None, neo_fin_key=None, consumer_key=CONSUMER_KEY)

print("\n--- TOTP Login ---")
r1 = client.totp_login(mobile_number=MOBILE, ucc=UCC, totp=TOTP)
print(json.dumps(r1, indent=2))

if r1.get("data", {}).get("status") != "success":
    print("LOGIN FAILED")
    exit(1)

print("\n--- TOTP Validate ---")
r2 = client.totp_validate(mpin=MPIN)
print(json.dumps(r2, indent=2))

if r2.get("data", {}).get("status") != "success":
    print("VALIDATION FAILED")
    exit(1)

print("\n--- Session Info ---")
cfg = client.configuration
print("  environment:", getattr(cfg, 'host', None))
print("  has_edit_token:", bool(getattr(cfg, 'edit_token', None)))
print("  has_edit_sid:", bool(getattr(cfg, 'edit_sid', None)))
print("  has_serverId:", bool(getattr(cfg, 'serverId', None)))
print("  data_center:", getattr(cfg, 'data_center', None))
print("  base_url:", getattr(cfg, 'base_url', None))
print("  consumer_key:", bool(getattr(cfg, 'consumer_key', None)))

session = r2.get("data", {})
print("\n  greetingName:", session.get("greetingName"))
print("  ucc:", session.get("ucc"))
print("  isTrialAccount:", session.get("isTrialAccount"))
print("  dormancyStatus:", session.get("dormancyStatus"))
print("  clientType:", session.get("clientType"))
print("  dataCenter:", session.get("dataCenter"))

print("\n--- Place Order (MIS, MARKET, NSE) ---")
try:
    order = client.place_order(
        exchange_segment="nse_cm",
        product="MIS",
        price="0",
        order_type="MKT",
        quantity="1",
        validity="DAY",
        trading_symbol="SBIN-EQ",
        transaction_type="B",
        amo="NO",
        disclosed_quantity="0",
        market_protection="0",
        pf="N",
        trigger_price="0",
    )
    print(json.dumps(order, indent=2))
except Exception as e:
    print("EXCEPTION:", e)

print("\n--- Holdings ---")
try:
    h = client.holdings()
    print(json.dumps(h, indent=2)[:500])
except Exception as e:
    print("EXCEPTION:", e)

print("\n--- Limits ---")
try:
    l = client.limits()
    print(json.dumps(l, indent=2)[:500])
except Exception as e:
    print("EXCEPTION:", e)

print("\n--- Quotes ---")
try:
    q = client.quotes(instrument_tokens=[{"instrument_token": "11915", "exchange_segment": "nse_cm"}])
    print(json.dumps(q, indent=2)[:500])
except Exception as e:
    print("EXCEPTION:", e)
