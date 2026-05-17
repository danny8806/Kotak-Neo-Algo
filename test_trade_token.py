from neo_api_client import NeoAPI
import json

ENV = "prod"  # change to "uat" if needed

CONSUMER_KEY = input("Consumer key: ").strip()
EDIT_TOKEN = input("edit_token (from totp_validate response 'data.token'): ").strip()
EDIT_SID = input("edit_sid (from totp_validate response 'data.sid'): ").strip()
SERVER_ID = input("hsServerId: ").strip()
DATA_CENTER = input("dataCenter: ").strip()

client = NeoAPI(environment=ENV, access_token=None, neo_fin_key=None, consumer_key=CONSUMER_KEY)

# Manually set session tokens
client.configuration.edit_token = EDIT_TOKEN
client.configuration.edit_sid = EDIT_SID
client.configuration.serverId = SERVER_ID
client.configuration.data_center = DATA_CENTER

print("\n--- Session Info ---")
print("  has_edit_token:", bool(client.configuration.edit_token))
print("  has_edit_sid:", bool(client.configuration.edit_sid))
print("  has_serverId:", bool(client.configuration.serverId))

print("\n--- Place Order ---")
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

print("\n--- Quotes ---")
try:
    q = client.quotes(instrument_tokens=[{"instrument_token": "11915", "exchange_segment": "nse_cm"}])
    print(json.dumps(q, indent=2)[:500])
except Exception as e:
    print("EXCEPTION:", e)
