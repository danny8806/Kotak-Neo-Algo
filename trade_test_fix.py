from neo_api_client import NeoAPI
import json
from dotenv import load_dotenv
load_dotenv()
import os

ENV = input("Environment (prod/uat) [prod]: ").strip() or "prod"
CONSUMER_KEY = os.getenv("CONSUMER_KEY") or input("Consumer key: ").strip()
MOBILE = os.getenv("MOBILE_NUMBER") or input("Mobile: ").strip()
UCC = os.getenv("UCC") or input("UCC: ").strip()
MPIN = os.getenv("MPIN") or input("MPIN: ").strip()
TOTP = input("TOTP (6-digit): ").strip()

client = NeoAPI(environment=ENV, access_token=None, neo_fin_key=None, consumer_key=CONSUMER_KEY)

r1 = client.totp_login(mobile_number=MOBILE, ucc=UCC, totp=TOTP)
if r1.get("data", {}).get("status") != "success":
    print("Login failed:", r1)
    exit(1)

r2 = client.totp_validate(mpin=MPIN)
if r2.get("data", {}).get("status") != "success":
    print("Validate failed:", r2)
    exit(1)

# APPLY FIX: use data_center as serverId if hsServerId is empty
cfg = client.configuration
print(f"\nBefore fix — serverId: '{getattr(cfg, 'serverId', None)}', data_center: '{getattr(cfg, 'data_center', None)}'")
if not cfg.serverId and cfg.data_center:
    cfg.serverId = cfg.data_center
    print(f"Fixed: set serverId to '{cfg.serverId}'")
print(f"After fix — serverId: '{getattr(cfg, 'serverId', None)}'")

# Place order
symbol = input("\nTrading symbol [SBIN-EQ]: ").strip() or "SBIN-EQ"
qty = input("Quantity [1]: ").strip() or "1"

print(f"\nPlacing order: {symbol} x {qty}")
try:
    order = client.place_order(
        exchange_segment="nse_cm",
        product="MIS",
        price="0",
        order_type="MKT",
        quantity=qty,
        validity="DAY",
        trading_symbol=symbol,
        transaction_type="B",
        amo="NO",
        disclosed_quantity="0",
        market_protection="0",
        pf="N",
        trigger_price="0",
    )
    print(json.dumps(order, indent=2))
except Exception as e:
    print(f"EXCEPTION: {e}")
