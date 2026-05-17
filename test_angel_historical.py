import requests, json, hmac, hashlib, base64, time, urllib.parse
from datetime import datetime

API_KEY = "DPUfQ4dz"
SECRET_KEY = "93b4a22a-bd91-4081-a1bb-211df8562897"

CLIENT_PUBLIC_IP = "223.228.130.8"
CLIENT_LOCAL_IP = "192.168.1.1"
MAC_ADDRESS = "00:11:22:33:44:55"

def test_historical():
    """Test Angel One Historical Candle Data API"""
    url = "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData"

    # NIFTY index token on NSE
    payload = {
        "exchange": "NSE",
        "symboltoken": "99926000",
        "interval": "ONE_DAY",
        "fromdate": "2025-05-05 09:00",
        "todate": "2025-05-12 15:30"
    }

    # Try with just API key (no Bearer token)
    headers = {
        "X-PrivateKey": API_KEY,
        "Accept": "application/json",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": CLIENT_LOCAL_IP,
        "X-ClientPublicIP": CLIENT_PUBLIC_IP,
        "X-MACAddress": MAC_ADDRESS,
        "X-UserType": "USER",
        "Content-Type": "application/json"
    }

    print(f"=== Test 1: No Bearer token ===")
    print(f"URL: {url}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    r = requests.post(url, json=payload, headers=headers, timeout=15)
    print(f"Status: {r.status_code}")
    try:
        print(f"Response: {json.dumps(r.json(), indent=2)}")
    except:
        print(f"Raw: {r.text[:500]}")

    # Try with a dummy Bearer token
    print(f"\n=== Test 2: Dummy Bearer token ===")
    headers["Authorization"] = "Bearer dummy"
    r = requests.post(url, json=payload, headers=headers, timeout=15)
    print(f"Status: {r.status_code}")
    try:
        print(f"Response: {json.dumps(r.json(), indent=2)}")
    except:
        print(f"Raw: {r.text[:500]}")

    # Try generating JWT from secret key directly (Angel One style)
    print(f"\n=== Test 3: JWT generated from secret ===")
    try:
        import jwt as pyjwt
        now = int(time.time())
        jwt_payload = {
            "clientcode": "TEST",
            "session_token": "test",
            "exp": now + 3600,
            "iat": now
        }
        token = pyjwt.encode(jwt_payload, SECRET_KEY, algorithm="HS256")
        headers["Authorization"] = f"Bearer {token}"
        print(f"JWT generated: {token[:80]}...")
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        print(f"Status: {r.status_code}")
        try:
            print(f"Response: {json.dumps(r.json(), indent=2)}")
        except:
            print(f"Raw: {r.text[:500]}")
    except ImportError:
        print("PyJWT not installed, installing...")
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyjwt"])
        print("Run script again")

if __name__ == "__main__":
    test_historical()
