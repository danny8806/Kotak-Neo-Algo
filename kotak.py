import importlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    import pyotp
except Exception:
    pyotp = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


DATA_FILE = Path(__file__).with_name("march_options.csv")
NIFTY_INDEX_TOKEN = "26000"

if load_dotenv is not None:
    load_dotenv(Path(__file__).with_name(".env"))


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    renamed = {}
    for column in frame.columns:
        normalized = re.sub(r"[^0-9a-zA-Z]+", "_", str(column).strip()).strip("_").lower()
        renamed[column] = normalized
    return frame.rename(columns=renamed)


@st.cache_resource(show_spinner=False)
def load_neo_api_class():
    try:
        module = importlib.import_module("neo_api_client")
        return module.NeoAPI
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def load_market_data(csv_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = pd.read_csv(csv_path)
    frame = normalize_columns(frame)

    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].astype(str).str.strip()

    numeric_columns = [
        "psymbol",
        "dstrikeprice",
        "dopeninterest",
        "dhighpricerange",
        "dlowpricerange",
        "llotsize",
        "lfreezeqty",
        "imaxordersize",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["instrument_token"] = frame["psymbol"].fillna(0).astype(int).astype(str)
    frame["expiry_code"] = frame["ptrdsymbol"].astype(str).str.extract(r"NIFTY(\d{2}[A-Z]{3})", expand=False)
    frame["strike"] = pd.to_numeric(frame["dstrikeprice"], errors="coerce").fillna(0.0) / 100.0
    frame["option_side"] = frame["poptiontype"].where(frame["poptiontype"].isin(["CE", "PE"]), "FUT")
    frame["open_interest"] = pd.to_numeric(frame["dopeninterest"], errors="coerce").fillna(0.0)
    frame["lot_size"] = pd.to_numeric(frame["llotsize"], errors="coerce").fillna(1).astype(int)
    frame["exchange_segment"] = frame["pexchseg"].fillna("nse_fo")
    frame["product_default"] = frame["pinsttype"].map(lambda value: "NRML" if "FUT" in str(value).upper() else "MIS")

    options = frame[frame["pinsttype"].astype(str).str.contains("OPT", case=False, na=False)].copy()
    futures = frame[frame["pinsttype"].astype(str).str.contains("FUT", case=False, na=False)].copy()

    options = options.sort_values(["expiry_code", "strike", "option_side"]).reset_index(drop=True)
    futures = futures.sort_values(["expiry_code", "ptrdsymbol"]).reset_index(drop=True)
    return options, futures


def infer_reference_spot(chain: pd.DataFrame) -> float:
    if chain.empty:
        return 0.0

    grouped = chain.groupby("strike", as_index=False)["open_interest"].sum()
    if grouped["open_interest"].sum() > 0:
        weighted = (grouped["strike"] * grouped["open_interest"]).sum() / grouped["open_interest"].sum()
        return float(round(weighted, 2))
    return float(round(grouped["strike"].median(), 2))


def nearest_strike(strikes: list[float], spot: float) -> float:
    if not strikes:
        return 0.0
    return min(strikes, key=lambda value: abs(value - spot))


def select_neighbors(strikes: list[float], spot: float) -> dict[str, float]:
    ordered = sorted(set(strikes))
    if not ordered:
        return {"atm": 0.0, "up_1": 0.0, "up_2": 0.0, "down_1": 0.0, "down_2": 0.0}

    atm = nearest_strike(ordered, spot)
    index = ordered.index(atm)

    def pick(offset: int) -> float:
        candidate = max(0, min(index + offset, len(ordered) - 1))
        return ordered[candidate]

    return {
        "atm": pick(0),
        "up_1": pick(1),
        "up_2": pick(2),
        "down_1": pick(-1),
        "down_2": pick(-2),
    }


def estimate_premium(strike: float, option_side: str, spot: float, volatility_factor: float = 1.0) -> float:
    intrinsic = max(spot - strike, 0.0) if option_side == "CE" else max(strike - spot, 0.0)
    distance = abs(strike - spot)
    time_value = max(12.0, spot * 0.004 * volatility_factor)
    decay_base = max(spot * 0.015, 1.0)
    time_decay = math.exp(-distance / decay_base)
    premium = intrinsic + (time_value * time_decay)
    return round(max(premium, 1.0), 2)


def compute_max_pain(chain: pd.DataFrame) -> float:
    strikes = sorted(chain["strike"].dropna().unique().tolist())
    if not strikes:
        return 0.0

    lot_size = int(chain["lot_size"].dropna().iloc[0]) if not chain["lot_size"].dropna().empty else 1
    best_strike = strikes[0]
    best_value = None
    ce = chain[chain["option_side"] == "CE"]
    pe = chain[chain["option_side"] == "PE"]

    for expiry_spot in strikes:
        ce_loss = ((expiry_spot - ce["strike"]).clip(lower=0) * ce["open_interest"]).sum()
        pe_loss = ((pe["strike"] - expiry_spot).clip(lower=0) * pe["open_interest"]).sum()
        total_loss = float((ce_loss + pe_loss) * lot_size)
        if best_value is None or total_loss < best_value:
            best_value = total_loss
            best_strike = expiry_spot
    return float(best_strike)


def oi_ladder(chain: pd.DataFrame, side: str) -> pd.DataFrame:
    subset = chain[chain["option_side"] == side][["strike", "open_interest"]].copy()
    if subset.empty:
        return subset
    return subset.sort_values("open_interest", ascending=False).head(5).sort_values("strike").reset_index(drop=True)


def locate_key_level(levels: pd.DataFrame, spot: float, side: str) -> float:
    if levels.empty:
        return 0.0
    if side == "PE":
        below = levels[levels["strike"] <= spot]
        return float((below.iloc[-1] if not below.empty else levels.iloc[-1])["strike"])
    above = levels[levels["strike"] >= spot]
    return float((above.iloc[0] if not above.empty else levels.iloc[0])["strike"])


def compute_metrics(chain: pd.DataFrame, spot: float) -> dict[str, Any]:
    ce = chain[chain["option_side"] == "CE"].copy()
    pe = chain[chain["option_side"] == "PE"].copy()
    total_ce_oi = float(ce["open_interest"].sum())
    total_pe_oi = float(pe["open_interest"].sum())
    total_pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0.0

    strikes = sorted(chain["strike"].dropna().unique().tolist())
    neighbors = select_neighbors(strikes, spot)
    window = chain[chain["strike"].between(neighbors["down_2"], neighbors["up_2"])]
    atm_ce = float(window[window["option_side"] == "CE"]["open_interest"].sum())
    atm_pe = float(window[window["option_side"] == "PE"]["open_interest"].sum())
    atm_pcr = round(atm_pe / atm_ce, 2) if atm_ce else 0.0

    max_pain = compute_max_pain(chain)
    pe_supports = oi_ladder(pe, "PE")
    ce_resistances = oi_ladder(ce, "CE")
    support = locate_key_level(pe_supports, spot, "PE")
    resistance = locate_key_level(ce_resistances, spot, "CE")
    lot_size = int(chain["lot_size"].dropna().iloc[0]) if not chain["lot_size"].dropna().empty else 1

    return {
        "spot": spot,
        "atm": neighbors["atm"],
        "lot_size": lot_size,
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "total_pcr": total_pcr,
        "atm_pcr": atm_pcr,
        "max_pain": max_pain,
        "max_pain_gap_pct": round(((spot - max_pain) / spot) * 100, 2) if spot else 0.0,
        "support": support,
        "resistance": resistance,
        "pe_supports": pe_supports,
        "ce_resistances": ce_resistances,
        "neighbors": neighbors,
        "available_strikes": len(strikes),
    }


def compute_signal_pack(metrics: dict[str, Any]) -> tuple[list[dict[str, Any]], float, str]:
    score = 0.0
    total_pcr = metrics["total_pcr"]
    atm_pcr = metrics["atm_pcr"]
    spot = metrics["spot"]
    support = metrics["support"]
    resistance = metrics["resistance"]
    max_pain_gap_pct = metrics["max_pain_gap_pct"]

    if total_pcr >= 1.1:
        score += 1
    elif total_pcr <= 0.9:
        score -= 1

    if atm_pcr >= 1.05:
        score += 1
    elif atm_pcr <= 0.95:
        score -= 1

    if resistance and spot > resistance:
        score += 2
    elif support and spot < support:
        score -= 2

    if abs(max_pain_gap_pct) <= 0.3:
        score -= 0.5

    if score >= 2:
        stance = "Bullish"
        action = "Bias to CE longs / PE spreads"
    elif score <= -2:
        stance = "Bearish"
        action = "Bias to PE longs / CE spreads"
    else:
        stance = "Range-bound"
        action = "Prefer spreads and alert-driven entries"

    signals = [
        {
            "name": "Directional engine",
            "bias": stance,
            "confidence": min(95, int(55 + abs(score) * 12)),
            "trigger": action,
            "detail": f"Total PCR {total_pcr}, ATM PCR {atm_pcr}, support {support}, resistance {resistance}",
        },
        {
            "name": "Breakout watch",
            "bias": "Active" if resistance and spot >= resistance else "Waiting",
            "confidence": 72 if resistance and spot >= resistance else 52,
            "trigger": f"Above {resistance}" if resistance else "No nearby resistance data",
            "detail": "CE writer resistance is the key upside trigger.",
        },
        {
            "name": "Breakdown watch",
            "bias": "Active" if support and spot <= support else "Waiting",
            "confidence": 72 if support and spot <= support else 52,
            "trigger": f"Below {support}" if support else "No nearby support data",
            "detail": "PE support loss is the key downside trigger.",
        },
        {
            "name": "Pin risk",
            "bias": "High" if abs(max_pain_gap_pct) <= 0.3 else "Normal",
            "confidence": 68 if abs(max_pain_gap_pct) <= 0.3 else 48,
            "trigger": f"Spot vs max pain gap {max_pain_gap_pct}%",
            "detail": "Near max pain, price often slows unless a fresh trigger appears.",
        },
    ]
    return signals, score, stance


def build_alert_feed(metrics: dict[str, Any], signal_score: float, custom_alerts: list[dict[str, Any]]) -> list[dict[str, str]]:
    alerts = []
    spot = metrics["spot"]
    support = metrics["support"]
    resistance = metrics["resistance"]
    total_pcr = metrics["total_pcr"]

    if resistance and spot >= resistance:
        alerts.append({"status": "TRIGGERED", "title": "Upside breakout", "message": f"Spot has crossed resistance {resistance}."})
    if support and spot <= support:
        alerts.append({"status": "TRIGGERED", "title": "Downside breakdown", "message": f"Spot has slipped below support {support}."})
    if total_pcr >= 1.3:
        alerts.append({"status": "WATCH", "title": "Put writing crowded", "message": f"PCR at {total_pcr} suggests crowded bullish positioning."})
    if total_pcr <= 0.75:
        alerts.append({"status": "WATCH", "title": "Call writing heavy", "message": f"PCR at {total_pcr} suggests bearish positioning pressure."})
    if abs(metrics["max_pain_gap_pct"]) <= 0.3:
        alerts.append({"status": "INFO", "title": "Pin-risk zone", "message": f"Spot is close to max pain {metrics['max_pain']}."})
    if signal_score >= 2:
        alerts.append({"status": "SETUP", "title": "Bullish setup", "message": f"Signal score {signal_score} supports bullish structures."})
    if signal_score <= -2:
        alerts.append({"status": "SETUP", "title": "Bearish setup", "message": f"Signal score {signal_score} supports bearish structures."})

    metric_values = {
        "spot": metrics["spot"],
        "total_pcr": metrics["total_pcr"],
        "atm_pcr": metrics["atm_pcr"],
        "max_pain_gap_pct": metrics["max_pain_gap_pct"],
        "support": metrics["support"],
        "resistance": metrics["resistance"],
    }
    for alert in custom_alerts:
        actual = metric_values.get(alert["metric"])
        if actual is None:
            continue
        triggered = actual >= alert["threshold"] if alert["operator"] == ">=" else actual <= alert["threshold"]
        alerts.append(
            {
                "status": "CUSTOM" if triggered else "ARMED",
                "title": alert["name"],
                "message": f"{alert['metric']} is {actual} against {alert['operator']} {alert['threshold']}.",
            }
        )
    return alerts


def payoff_for_leg(price_grid: pd.Series, side: str, strike: float, premium: float, action: str, lots: int, lot_size: int) -> pd.Series:
    intrinsic = (price_grid - strike).clip(lower=0) if side == "CE" else (strike - price_grid).clip(lower=0)
    signed = intrinsic - premium if action == "BUY" else premium - intrinsic
    return signed * lots * lot_size


def build_strategy_catalog(chain: pd.DataFrame, spot: float) -> list[dict[str, Any]]:
    strikes = sorted(chain["strike"].dropna().unique().tolist())
    levels = select_neighbors(strikes, spot)

    def premium(strike: float, side: str, factor: float = 1.0) -> float:
        return estimate_premium(strike, side, spot, factor)

    return [
        {
            "name": "Long Call",
            "outlook": "Bullish",
            "description": "Directional upside play with limited risk.",
            "legs": [{"action": "BUY", "side": "CE", "strike": levels["atm"], "premium": premium(levels["atm"], "CE", 1.1)}],
        },
        {
            "name": "Bull Call Spread",
            "outlook": "Bullish",
            "description": "Defined-risk bullish spread using ATM and OTM calls.",
            "legs": [
                {"action": "BUY", "side": "CE", "strike": levels["atm"], "premium": premium(levels["atm"], "CE", 1.0)},
                {"action": "SELL", "side": "CE", "strike": levels["up_1"], "premium": premium(levels["up_1"], "CE", 0.9)},
            ],
        },
        {
            "name": "Bull Put Spread",
            "outlook": "Bullish",
            "description": "Premium-selling bullish spread below spot.",
            "legs": [
                {"action": "SELL", "side": "PE", "strike": levels["down_1"], "premium": premium(levels["down_1"], "PE", 1.0)},
                {"action": "BUY", "side": "PE", "strike": levels["down_2"], "premium": premium(levels["down_2"], "PE", 0.85)},
            ],
        },
        {
            "name": "Long Put",
            "outlook": "Bearish",
            "description": "Directional downside play with limited risk.",
            "legs": [{"action": "BUY", "side": "PE", "strike": levels["atm"], "premium": premium(levels["atm"], "PE", 1.1)}],
        },
        {
            "name": "Bear Put Spread",
            "outlook": "Bearish",
            "description": "Defined-risk bearish spread using ATM and lower strike puts.",
            "legs": [
                {"action": "BUY", "side": "PE", "strike": levels["atm"], "premium": premium(levels["atm"], "PE", 1.0)},
                {"action": "SELL", "side": "PE", "strike": levels["down_1"], "premium": premium(levels["down_1"], "PE", 0.9)},
            ],
        },
        {
            "name": "Bear Call Spread",
            "outlook": "Bearish",
            "description": "Premium-selling bearish spread above spot.",
            "legs": [
                {"action": "SELL", "side": "CE", "strike": levels["up_1"], "premium": premium(levels["up_1"], "CE", 1.0)},
                {"action": "BUY", "side": "CE", "strike": levels["up_2"], "premium": premium(levels["up_2"], "CE", 0.85)},
            ],
        },
        {
            "name": "Iron Condor",
            "outlook": "Range-bound",
            "description": "Defined-risk premium capture between support and resistance.",
            "legs": [
                {"action": "SELL", "side": "PE", "strike": levels["down_1"], "premium": premium(levels["down_1"], "PE", 1.0)},
                {"action": "BUY", "side": "PE", "strike": levels["down_2"], "premium": premium(levels["down_2"], "PE", 0.85)},
                {"action": "SELL", "side": "CE", "strike": levels["up_1"], "premium": premium(levels["up_1"], "CE", 1.0)},
                {"action": "BUY", "side": "CE", "strike": levels["up_2"], "premium": premium(levels["up_2"], "CE", 0.85)},
            ],
        },
        {
            "name": "Long Straddle",
            "outlook": "Breakout / Volatility",
            "description": "Volatility expansion trade using both ATM options.",
            "legs": [
                {"action": "BUY", "side": "PE", "strike": levels["atm"], "premium": premium(levels["atm"], "PE", 1.15)},
                {"action": "BUY", "side": "CE", "strike": levels["atm"], "premium": premium(levels["atm"], "CE", 1.15)},
            ],
        },
        {
            "name": "Long Strangle",
            "outlook": "Breakout / Volatility",
            "description": "Cheaper volatility setup using OTM strikes.",
            "legs": [
                {"action": "BUY", "side": "PE", "strike": levels["down_1"], "premium": premium(levels["down_1"], "PE", 0.95)},
                {"action": "BUY", "side": "CE", "strike": levels["up_1"], "premium": premium(levels["up_1"], "CE", 0.95)},
            ],
        },
    ]


def strategy_payoff(strategy: dict[str, Any], spot: float, lot_size: int, lots: int) -> pd.DataFrame:
    lower = max(0.0, spot * 0.92)
    upper = spot * 1.08
    step = (upper - lower) / 69 if upper > lower else 1.0
    price_grid = pd.Series([round(lower + (step * index), 2) for index in range(70)])
    total = pd.Series([0.0] * len(price_grid))
    for leg in strategy["legs"]:
        total += payoff_for_leg(price_grid, leg["side"], leg["strike"], leg["premium"], leg["action"], lots, lot_size)
    return pd.DataFrame({"underlying": price_grid, "payoff": total})


def recommendation_table(chain: pd.DataFrame, spot: float) -> pd.DataFrame:
    data = chain[chain["option_side"].isin(["CE", "PE"])][["ptrdsymbol", "instrument_token", "option_side", "strike", "open_interest"]].copy()
    if data.empty:
        return data
    data["distance"] = (data["strike"] - spot).abs()
    oi_max = max(float(data["open_interest"].max()), 1.0)
    data["score"] = ((data["open_interest"] / oi_max) * 70) + ((1 - (data["distance"] / max(spot * 0.03, 1.0))) * 30)
    data["score"] = data["score"].clip(lower=0).round(1)
    return data.sort_values(["score", "open_interest"], ascending=[False, False]).head(12).reset_index(drop=True)


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("message", "data", "orders", "trades", "positions", "holdings", "limits", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("message", "orders", "trades", "positions", "holdings", "limits", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            return [data]
        return [payload]
    return []


def response_frame(payload: Any) -> pd.DataFrame:
    records = extract_records(payload)
    if not records:
        return pd.DataFrame()
    return normalize_columns(pd.DataFrame(records))


def env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def create_neo_client(consumer_key: str):
    neo_api = load_neo_api_class()
    if neo_api is None or not consumer_key:
        return None
    return neo_api(environment=env_value("KOTAK_ENVIRONMENT", "prod"), access_token=None, neo_fin_key=None, consumer_key=consumer_key)


def active_market_client():
    if st.session_state.get("broker_client") is not None:
        return st.session_state["broker_client"]
    quote_client = st.session_state.get("quote_client")
    if quote_client is not None:
        return quote_client
    consumer_key = env_value("KOTAK_CONSUMER_KEY")
    if not consumer_key:
        return None
    quote_client = create_neo_client(consumer_key)
    st.session_state["quote_client"] = quote_client
    return quote_client


def broker_ready() -> bool:
    return st.session_state.get("broker_client") is not None


def safe_api_call(label: str, fn, *args, **kwargs) -> tuple[Any, str | None]:
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        return None, f"{label} failed: {exc}"


def try_broker_login(totp_code: str) -> tuple[bool, str]:
    consumer_key = env_value("KOTAK_CONSUMER_KEY")
    mobile_number = env_value("KOTAK_MOBILE_NUMBER")
    ucc = env_value("KOTAK_UCC")
    mpin = env_value("KOTAK_MPIN")

    if not all([consumer_key, mobile_number, ucc, mpin]):
        return False, "Set KOTAK_CONSUMER_KEY, KOTAK_MOBILE_NUMBER, KOTAK_UCC, and KOTAK_MPIN before connecting."
    if load_neo_api_class() is None:
        return False, "neo_api_client is not installed in this Python environment."
    if not totp_code:
        return False, "TOTP is required for broker login."

    client = create_neo_client(consumer_key)
    login_response = client.totp_login(mobile_number=mobile_number, ucc=ucc, totp=totp_code)
    session_response = client.totp_validate(mpin=mpin)

    st.session_state["broker_client"] = client
    st.session_state["quote_client"] = client
    st.session_state["broker_login_response"] = login_response
    st.session_state["broker_session_response"] = session_response
    return True, "Broker session established."


def live_quote_frame(client, instruments: list[dict[str, str]], quote_type: str) -> tuple[pd.DataFrame, str | None]:
    if client is None or not instruments:
        return pd.DataFrame(), "Client or instrument list is missing."
    response, error = safe_api_call("Quotes", client.quotes, instrument_tokens=instruments, quote_type=quote_type)
    if error:
        return pd.DataFrame(), error
    frame = response_frame(response)
    if frame.empty and isinstance(response, dict):
        return pd.DataFrame(), str(response)
    return frame, None


def pick_numeric_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return None


def nifty_ltp_from_quotes(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    column = pick_numeric_column(frame, ["ltp", "last_traded_price", "lastprice", "price"])
    if column is None:
        return None
    try:
        return float(pd.to_numeric(frame[column], errors="coerce").dropna().iloc[0])
    except Exception:
        return None


def build_watchlist(chain: pd.DataFrame, metrics: dict[str, Any]) -> pd.DataFrame:
    if chain.empty:
        return chain.head(0)
    strikes = sorted(chain["strike"].dropna().unique().tolist())
    neighbors = select_neighbors(strikes, metrics["spot"])
    selected = chain[chain["strike"].isin({neighbors["down_1"], neighbors["atm"], neighbors["up_1"]})]
    return selected.sort_values(["strike", "option_side"]).copy()


def contract_row(chain: pd.DataFrame, strike: float, side: str) -> dict[str, Any] | None:
    subset = chain[(chain["strike"] == strike) & (chain["option_side"] == side)]
    if subset.empty:
        return None
    return subset.sort_values("open_interest", ascending=False).iloc[0].to_dict()


def stage_strategy(strategy: dict[str, Any], chain: pd.DataFrame, lots: int) -> tuple[list[dict[str, Any]], list[str]]:
    staged = []
    missing = []
    for leg in strategy["legs"]:
        row = contract_row(chain, leg["strike"], leg["side"])
        if row is None:
            missing.append(f"{leg['side']} {leg['strike']}")
            continue
        staged.append(
            {
                "action": leg["action"],
                "side": leg["side"],
                "strike": leg["strike"],
                "symbol": row["ptrdsymbol"],
                "instrument_token": row["instrument_token"],
                "exchange_segment": row["exchange_segment"],
                "product": row["product_default"],
                "order_type": "L",
                "price": round(float(leg["premium"]), 2),
                "trigger_price": 0.0,
                "quantity": int(lots * int(row["lot_size"])),
                "amo": "NO",
            }
        )
    return staged, missing


def order_payload(symbol: str, transaction_type: str, order_type: str, quantity: int, price: float, trigger_price: float, amo: str, exchange_segment: str, product: str) -> dict[str, str]:
    payload = {
        "exchange_segment": exchange_segment,
        "product": product,
        "price": "0" if order_type == "MKT" else str(price),
        "order_type": order_type,
        "quantity": str(quantity),
        "validity": "DAY",
        "trading_symbol": symbol,
        "transaction_type": transaction_type,
        "amo": amo,
    }
    if order_type in {"SL", "SL-M"}:
        payload["trigger_price"] = str(trigger_price)
    return payload


def margin_preview(client, payload: dict[str, str], instrument_token: str) -> Any:
    if client is None:
        return {"info": "Broker client is not connected."}
    response, error = safe_api_call(
        "Margin required",
        client.margin_required,
        exchange_segment=payload["exchange_segment"],
        price=payload["price"],
        order_type=payload["order_type"],
        product=payload["product"],
        quantity=payload["quantity"],
        instrument_token=instrument_token,
        transaction_type=payload["transaction_type"],
        trigger_price=payload.get("trigger_price"),
    )
    if error:
        return {"error": error}
    return response


def account_snapshot(client) -> dict[str, Any]:
    calls = {
        "order_book": client.order_report,
        "trade_book": client.trade_report,
        "positions": client.positions,
        "holdings": client.holdings,
        "limits": client.limits,
    }
    snapshot = {}
    for name, fn in calls.items():
        response, error = safe_api_call(name, fn)
        snapshot[name] = {"response": response, "error": error, "frame": response_frame(response)}
    return snapshot


def render_signal_cards(signals: list[dict[str, Any]]) -> None:
    columns = st.columns(len(signals))
    for column, signal in zip(columns, signals):
        with column:
            st.markdown(f"### {signal['name']}")
            st.metric("Bias", signal["bias"], f"{signal['confidence']}% confidence")
            st.caption(signal["trigger"])
            st.write(signal["detail"])


def render_alerts(alerts: list[dict[str, str]]) -> None:
    if not alerts:
        st.info("No alerts armed.")
        return
    for alert in alerts:
        st.write(f"[{alert['status']}] {alert['title']}: {alert['message']}")


def oi_chart(chain: pd.DataFrame, spot: float, max_pain: float) -> go.Figure:
    chart = chain[chain["option_side"].isin(["CE", "PE"])][["strike", "option_side", "open_interest"]].copy()
    chart["open_interest"] = chart.apply(lambda row: -row["open_interest"] if row["option_side"] == "CE" else row["open_interest"], axis=1)
    fig = px.bar(
        chart,
        x="strike",
        y="open_interest",
        color="option_side",
        color_discrete_map={"CE": "#ef4444", "PE": "#22c55e"},
        title="Open Interest Ladder",
        labels={"open_interest": "PE positive / CE negative", "strike": "Strike"},
    )
    fig.add_vline(x=spot, line_dash="dash", line_color="#f59e0b", annotation_text="Spot")
    fig.add_vline(x=max_pain, line_dash="dot", line_color="#3b82f6", annotation_text="Max Pain")
    fig.update_layout(height=420, bargap=0.12)
    return fig


def support_resistance_chart(metrics: dict[str, Any]) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Indicator(mode="number", value=metrics["spot"], title={"text": "Spot"}, domain={"x": [0.0, 0.24], "y": [0, 1]}))
    fig.add_trace(go.Indicator(mode="number", value=metrics["support"], title={"text": "Support"}, domain={"x": [0.26, 0.5], "y": [0, 1]}))
    fig.add_trace(go.Indicator(mode="number", value=metrics["resistance"], title={"text": "Resistance"}, domain={"x": [0.52, 0.76], "y": [0, 1]}))
    fig.add_trace(go.Indicator(mode="number", value=metrics["max_pain"], title={"text": "Max Pain"}, domain={"x": [0.78, 1.0], "y": [0, 1]}))
    fig.update_layout(height=220, margin={"t": 40, "b": 20, "l": 20, "r": 20})
    return fig


def signal_gauge(signal_score: float) -> go.Figure:
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=signal_score,
            title={"text": "Signal Score"},
            gauge={
                "axis": {"range": [-4, 4]},
                "bar": {"color": "#f59e0b"},
                "steps": [
                    {"range": [-4, -2], "color": "#fee2e2"},
                    {"range": [-2, 2], "color": "#fef3c7"},
                    {"range": [2, 4], "color": "#dcfce7"},
                ],
            },
        )
    )
    fig.update_layout(height=280, margin={"t": 40, "b": 20, "l": 20, "r": 20})
    return fig


def seed_state(default_spot: float) -> None:
    defaults = {
        "custom_alerts": [],
        "broker_client": None,
        "quote_client": None,
        "broker_login_response": None,
        "broker_session_response": None,
        "reference_spot": float(round(default_spot, 2)),
        "staged_legs": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main() -> None:
    st.set_page_config(page_title="NIFTY Algo Command Center", layout="wide")
    st.title("NIFTY Algo Command Center")
    st.caption("Broker-connected option analytics, live quote watchlists, signal-driven staging, and guarded order execution for Kotak Neo.")

    options, futures = load_market_data(str(DATA_FILE))
    base_chain = options.copy()
    base_spot = infer_reference_spot(base_chain)
    seed_state(base_spot)

    with st.sidebar:
        st.header("Control Tower")
        data_path = st.text_input("Option data file", value=str(DATA_FILE))
        options, futures = load_market_data(data_path)
        expiry_choices = [code for code in options["expiry_code"].dropna().unique().tolist() if code]
        expiry = st.selectbox("Expiry", expiry_choices, index=0 if expiry_choices else None)
        chain = options[options["expiry_code"] == expiry].copy() if expiry else options.copy()

        derived_spot = infer_reference_spot(chain)
        spot_mode = st.radio("Reference spot source", ["Manual", "Chain-derived"], horizontal=False)
        if spot_mode == "Chain-derived":
            st.session_state["reference_spot"] = float(round(derived_spot, 2))

        st.number_input("Reference spot", min_value=0.0, step=50.0, key="reference_spot")
        strategy_lots = st.number_input("Strategy lots", min_value=1, value=1, step=1)
        order_mode = st.radio("Execution mode", ["Paper", "Live"], horizontal=True)
        st.caption("Paper mode keeps everything broker-safe while still calculating payloads, margin calls, and staged baskets.")

    spot = float(st.session_state["reference_spot"])
    metrics = compute_metrics(chain, spot)
    signals, signal_score, stance = compute_signal_pack(metrics)
    alerts = build_alert_feed(metrics, signal_score, st.session_state["custom_alerts"])
    ideas = recommendation_table(chain, spot)
    watchlist = build_watchlist(chain, metrics)

    overview_tab, signal_tab, market_tab, strategy_tab, trade_tab, account_tab, broker_tab = st.tabs(
        ["Overview", "Signal Lab", "Market Watch", "Strategy Studio", "Trade Terminal", "Portfolio", "Broker Control"]
    )

    with overview_tab:
        kpi_1, kpi_2, kpi_3, kpi_4, kpi_5, kpi_6 = st.columns(6)
        kpi_1.metric("Reference Spot", f"{metrics['spot']:.2f}")
        kpi_2.metric("ATM Strike", f"{metrics['atm']:.0f}")
        kpi_3.metric("PCR", metrics["total_pcr"])
        kpi_4.metric("ATM PCR", metrics["atm_pcr"])
        kpi_5.metric("Max Pain", f"{metrics['max_pain']:.0f}", f"{metrics['max_pain_gap_pct']}%")
        kpi_6.metric("Market Stance", stance, f"{metrics['available_strikes']} strikes")

        left, right = st.columns([1.6, 1.0])
        with left:
            st.plotly_chart(oi_chart(chain, spot, metrics["max_pain"]), use_container_width=True)
        with right:
            st.plotly_chart(signal_gauge(signal_score), use_container_width=True)
            st.plotly_chart(support_resistance_chart(metrics), use_container_width=True)

        level_col_1, level_col_2 = st.columns(2)
        with level_col_1:
            st.markdown("#### PE support ladder")
            st.dataframe(metrics["pe_supports"], use_container_width=True, hide_index=True)
        with level_col_2:
            st.markdown("#### CE resistance ladder")
            st.dataframe(metrics["ce_resistances"], use_container_width=True, hide_index=True)

        st.subheader("Desk Briefing")
        st.write(
            f"Support sits near {metrics['support']}, resistance near {metrics['resistance']}, and the signal engine is {stance.lower()} "
            f"with score {signal_score}. Use Signal Lab to stage alerts or a strategy, and Trade Terminal to route real orders."
        )

    with signal_tab:
        st.subheader("Rule-Based Signal Engine")
        render_signal_cards(signals)
        st.markdown("#### Best execution candidates")
        st.dataframe(ideas, use_container_width=True, hide_index=True)

        recommended_outlook = "Bullish" if stance == "Bullish" else "Bearish" if stance == "Bearish" else "Range-bound"
        strategies = build_strategy_catalog(chain, spot)
        recommended = next((item for item in strategies if item["outlook"] == recommended_outlook), strategies[0] if strategies else None)
        if recommended is not None:
            stage_col, note_col = st.columns([0.8, 1.2])
            with stage_col:
                if st.button(f"Stage {recommended['name']}", use_container_width=True):
                    staged, missing = stage_strategy(recommended, chain, int(strategy_lots))
                    st.session_state["staged_legs"] = staged
                    if missing:
                        st.warning(f"Missing contracts for: {', '.join(missing)}")
                    else:
                        st.success("Recommended strategy staged in Trade Terminal.")
            with note_col:
                st.write(recommended["description"])

        st.markdown("#### Alert center")
        alert_col, form_col = st.columns([1.3, 1.0])
        with alert_col:
            render_alerts(alerts)
        with form_col:
            with st.form("custom_alert_form", clear_on_submit=True):
                alert_name = st.text_input("Alert name", value="Breakout scanner")
                metric = st.selectbox("Metric", ["spot", "total_pcr", "atm_pcr", "max_pain_gap_pct", "support", "resistance"])
                operator = st.selectbox("Operator", [">=", "<="])
                threshold = st.number_input("Threshold", value=float(metrics["resistance"] or metrics["spot"]))
                submitted = st.form_submit_button("Arm custom alert")
                if submitted:
                    st.session_state["custom_alerts"].append({"name": alert_name, "metric": metric, "operator": operator, "threshold": threshold})
                    st.success("Custom alert armed.")

    with market_tab:
        st.subheader("Live Market Watch")
        client = active_market_client()
        live_cols = st.columns([0.6, 0.6, 1.4])
        with live_cols[0]:
            refresh_spot = st.button("Fetch live NIFTY", use_container_width=True)
        with live_cols[1]:
            refresh_chain = st.button("Fetch live basket", use_container_width=True)
        with live_cols[2]:
            st.caption("Quotes need at least `KOTAK_CONSUMER_KEY`. Account and execution flows need full broker login.")

        if refresh_spot:
            quote_frame, error = live_quote_frame(
                client,
                [{"instrument_token": NIFTY_INDEX_TOKEN, "exchange_segment": "nse_cm"}],
                "ltp",
            )
            if error:
                st.error(error)
            else:
                live_spot = nifty_ltp_from_quotes(quote_frame)
                st.dataframe(quote_frame, use_container_width=True, hide_index=True)
                if live_spot is not None:
                    st.session_state["reference_spot"] = float(round(live_spot, 2))
                    st.success(f"Reference spot updated to {live_spot}.")
                    st.rerun()

        if refresh_chain:
            instruments = [
                {"instrument_token": str(row.instrument_token), "exchange_segment": str(row.exchange_segment)}
                for row in watchlist.itertuples()
            ]
            quote_frame, error = live_quote_frame(client, instruments, "ltp")
            if error:
                st.error(error)
            else:
                st.dataframe(quote_frame, use_container_width=True, hide_index=True)

        st.markdown("#### Focus watchlist")
        st.dataframe(
            watchlist[["ptrdsymbol", "instrument_token", "option_side", "strike", "open_interest"]],
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("#### Strike structure")
        pivot = watchlist.pivot_table(index="strike", columns="option_side", values="open_interest", aggfunc="sum").fillna(0).reset_index()
        if not pivot.empty:
            series = [column for column in pivot.columns if column != "strike"]
            st.plotly_chart(px.line(pivot, x="strike", y=series, markers=True, title="Strike-wise OI comparison"), use_container_width=True)

    with strategy_tab:
        st.subheader("Strategy Studio")
        strategies = build_strategy_catalog(chain, spot)
        available_outlooks = sorted({strategy["outlook"] for strategy in strategies})
        outlook = st.selectbox("Market outlook", available_outlooks, index=available_outlooks.index(recommended_outlook) if recommended_outlook in available_outlooks else 0)
        filtered = [strategy for strategy in strategies if strategy["outlook"] == outlook]
        selected_name = st.selectbox("Strategy", [strategy["name"] for strategy in filtered])
        selected_strategy = next(strategy for strategy in filtered if strategy["name"] == selected_name)

        st.write(selected_strategy["description"])
        st.dataframe(pd.DataFrame(selected_strategy["legs"]), use_container_width=True, hide_index=True)
        payoff = strategy_payoff(selected_strategy, spot, metrics["lot_size"], int(strategy_lots))
        st.plotly_chart(px.line(payoff, x="underlying", y="payoff", title=f"{selected_name} payoff"), use_container_width=True)

        action_col, info_col = st.columns([0.8, 1.2])
        with action_col:
            if st.button("Stage this strategy", use_container_width=True):
                staged, missing = stage_strategy(selected_strategy, chain, int(strategy_lots))
                st.session_state["staged_legs"] = staged
                if missing:
                    st.warning(f"Missing contracts for: {', '.join(missing)}")
                else:
                    st.success("Strategy legs staged in Trade Terminal.")
        with info_col:
            st.caption("Model premiums are placeholders. Replace them with live option prices or fetch quotes before submitting live orders.")

    with trade_tab:
        st.subheader("Trade Terminal")
        focused_symbols = watchlist["ptrdsymbol"].drop_duplicates().tolist() if not watchlist.empty else chain["ptrdsymbol"].drop_duplicates().tolist()
        if focused_symbols:
            default_symbol = ideas.iloc[0]["ptrdsymbol"] if not ideas.empty else focused_symbols[0]
            symbol = st.selectbox("Trading symbol", focused_symbols, index=focused_symbols.index(default_symbol) if default_symbol in focused_symbols else 0)
            symbol_row = chain[chain["ptrdsymbol"] == symbol].iloc[0]
            transaction_type = st.radio("Side", ["B", "S"], horizontal=True, format_func=lambda value: "Buy" if value == "B" else "Sell")
            order_type = st.selectbox("Order type", ["L", "MKT", "SL", "SL-M"])
            lots = st.number_input("Lots", min_value=1, value=1, step=1, key="single_order_lots")
            quantity = int(lots * int(symbol_row["lot_size"]))
            price = st.number_input("Price", min_value=0.0, value=float(estimate_premium(float(symbol_row["strike"]), str(symbol_row["option_side"]), spot)), step=0.5)
            trigger_price = st.number_input("Trigger price", min_value=0.0, value=max(price - 2.0, 0.0), step=0.5)
            product = st.selectbox("Product", ["MIS", "NRML", "BO"], index=0 if str(symbol_row["product_default"]) == "MIS" else 1)
            amo = st.selectbox("AMO", ["NO", "YES"])
            payload = order_payload(
                symbol=symbol,
                transaction_type=transaction_type,
                order_type=order_type,
                quantity=quantity,
                price=price,
                trigger_price=trigger_price,
                amo=amo,
                exchange_segment=str(symbol_row["exchange_segment"]),
                product=product,
            )
            st.code(json.dumps(payload, indent=2), language="json")

            preview_col, place_col, margin_col = st.columns(3)
            with preview_col:
                if st.button("Margin check", use_container_width=True):
                    preview = margin_preview(st.session_state.get("broker_client"), payload, str(symbol_row["instrument_token"]))
                    st.json(preview)
            with place_col:
                armed = st.checkbox("I confirm live single-leg order", key="confirm_single_live")
                if st.button("Send order", use_container_width=True):
                    if order_mode != "Live":
                        st.info("Paper mode enabled. Payload preview only.")
                    elif not broker_ready():
                        st.warning("Broker is not connected.")
                    elif not armed:
                        st.warning("Tick the confirmation box before sending a live order.")
                    else:
                        response, error = safe_api_call("Place order", st.session_state["broker_client"].place_order, **payload)
                        if error:
                            st.error(error)
                        else:
                            st.success("Order submitted.")
                            st.json(response)
            with margin_col:
                modify_order_id = st.text_input("Modify order id")
                if st.button("Modify order", use_container_width=True):
                    if order_mode != "Live":
                        st.info("Paper mode enabled. Modify preview only.")
                    elif not broker_ready() or not modify_order_id:
                        st.warning("Connect broker and provide an order id.")
                    else:
                        response, error = safe_api_call(
                            "Modify order",
                            st.session_state["broker_client"].modify_order,
                            order_id=modify_order_id,
                            price=str(price),
                            order_type=order_type,
                            quantity=str(quantity),
                            validity="DAY",
                            trigger_price=str(trigger_price),
                        )
                        if error:
                            st.error(error)
                        else:
                            st.success("Modify request sent.")
                            st.json(response)
        else:
            st.warning("No tradable symbols are available in the current chain view.")

        st.markdown("#### Staged basket")
        staged_legs = st.session_state.get("staged_legs", [])
        if staged_legs:
            staged_frame = pd.DataFrame(staged_legs)
            st.dataframe(staged_frame, use_container_width=True, hide_index=True)
            basket_col_1, basket_col_2, basket_col_3 = st.columns(3)
            with basket_col_1:
                confirm_basket = st.checkbox("I confirm live basket execution", key="confirm_basket_live")
                if st.button("Execute staged basket", use_container_width=True):
                    if order_mode != "Live":
                        st.info("Paper mode enabled. Basket left staged only.")
                    elif not broker_ready():
                        st.warning("Broker is not connected.")
                    elif not confirm_basket:
                        st.warning("Tick the basket confirmation before sending live orders.")
                    else:
                        results = []
                        for leg in staged_legs:
                            payload = order_payload(
                                symbol=str(leg["symbol"]),
                                transaction_type="B" if leg["action"] == "BUY" else "S",
                                order_type=str(leg["order_type"]),
                                quantity=int(leg["quantity"]),
                                price=float(leg["price"]),
                                trigger_price=float(leg["trigger_price"]),
                                amo=str(leg["amo"]),
                                exchange_segment=str(leg["exchange_segment"]),
                                product=str(leg["product"]),
                            )
                            response, error = safe_api_call("Place order", st.session_state["broker_client"].place_order, **payload)
                            results.append({"symbol": leg["symbol"], "error": error, "response": response})
                        st.json(results)
            with basket_col_2:
                if st.button("Clear staged basket", use_container_width=True):
                    st.session_state["staged_legs"] = []
                    st.success("Staged basket cleared.")
            with basket_col_3:
                if broker_ready() and st.button("Margin check staged basket", use_container_width=True):
                    previews = []
                    for leg in staged_legs:
                        payload = order_payload(
                            symbol=str(leg["symbol"]),
                            transaction_type="B" if leg["action"] == "BUY" else "S",
                            order_type=str(leg["order_type"]),
                            quantity=int(leg["quantity"]),
                            price=float(leg["price"]),
                            trigger_price=float(leg["trigger_price"]),
                            amo=str(leg["amo"]),
                            exchange_segment=str(leg["exchange_segment"]),
                            product=str(leg["product"]),
                        )
                        previews.append(
                            {
                                "symbol": leg["symbol"],
                                "margin": margin_preview(st.session_state["broker_client"], payload, str(leg["instrument_token"])),
                            }
                        )
                    st.json(previews)
        else:
            st.info("No staged basket yet. Stage one from Signal Lab or Strategy Studio.")

    with account_tab:
        st.subheader("Portfolio & Order Books")
        if broker_ready():
            if st.button("Refresh account snapshot"):
                st.session_state["account_snapshot"] = account_snapshot(st.session_state["broker_client"])
            snapshot = st.session_state.get("account_snapshot")
            if snapshot:
                for label in ["order_book", "trade_book", "positions", "holdings", "limits"]:
                    block = snapshot[label]
                    st.markdown(f"#### {label.replace('_', ' ').title()}")
                    if block["error"]:
                        st.error(block["error"])
                    elif not block["frame"].empty:
                        st.dataframe(block["frame"], use_container_width=True, hide_index=True)
                    else:
                        st.json(block["response"])

                action_col_1, action_col_2 = st.columns(2)
                with action_col_1:
                    cancel_order_id = st.text_input("Cancel order id")
                    if st.button("Cancel order", use_container_width=True):
                        response, error = safe_api_call("Cancel order", st.session_state["broker_client"].cancel_order, order_id=cancel_order_id, isVerify=True, amo="NO")
                        if error:
                            st.error(error)
                        else:
                            st.json(response)
                with action_col_2:
                    history_order_id = st.text_input("Order history id")
                    if st.button("Fetch order history", use_container_width=True):
                        response, error = safe_api_call("Order history", st.session_state["broker_client"].order_history, order_id=history_order_id)
                        if error:
                            st.error(error)
                        else:
                            history_frame = response_frame(response)
                            if history_frame.empty:
                                st.json(response)
                            else:
                                st.dataframe(history_frame, use_container_width=True, hide_index=True)
            else:
                st.info("Refresh account snapshot to load orders, trades, positions, holdings, and limits.")
        else:
            st.info("Connect the broker first to load portfolio and order-book data.")

    with broker_tab:
        st.subheader("Broker Control")
        neo_installed = load_neo_api_class() is not None
        env_status = pd.DataFrame(
            [
                {"variable": "KOTAK_CONSUMER_KEY", "present": bool(env_value("KOTAK_CONSUMER_KEY"))},
                {"variable": "KOTAK_MOBILE_NUMBER", "present": bool(env_value("KOTAK_MOBILE_NUMBER"))},
                {"variable": "KOTAK_UCC", "present": bool(env_value("KOTAK_UCC"))},
                {"variable": "KOTAK_MPIN", "present": bool(env_value("KOTAK_MPIN"))},
                {"variable": "KOTAK_TOTP_SECRET", "present": bool(env_value("KOTAK_TOTP_SECRET"))},
            ]
        )
        st.dataframe(env_status, use_container_width=True, hide_index=True)
        st.caption("Kotak Neo says trade API access requires a generated API token, TOTP registration, and static IP whitelisting. Product support and rate limits can change; check their current docs before live deployment.")

        auto_totp = ""
        if env_value("KOTAK_TOTP_SECRET") and pyotp is not None:
            auto_totp = pyotp.TOTP(env_value("KOTAK_TOTP_SECRET")).now()
        totp_code = st.text_input("TOTP", value=auto_totp, help="Auto-filled when KOTAK_TOTP_SECRET is set.")

        broker_col_1, broker_col_2, broker_col_3 = st.columns(3)
        with broker_col_1:
            if st.button("Connect broker", use_container_width=True):
                ok, message = try_broker_login(totp_code)
                if ok:
                    st.success(message)
                else:
                    st.warning(message)
        with broker_col_2:
            if st.button("Logout broker", use_container_width=True):
                if broker_ready():
                    response, error = safe_api_call("Logout", st.session_state["broker_client"].logout)
                    st.session_state["broker_client"] = None
                    st.session_state["account_snapshot"] = None
                    if error:
                        st.error(error)
                    else:
                        st.json(response)
                else:
                    st.info("Broker is already disconnected.")
        with broker_col_3:
            st.metric("SDK Status", "Installed" if neo_installed else "Missing", "Kotak Neo v2 support")

        if broker_ready():
            st.success("Broker session is live for this Streamlit session.")
            if st.button("Show last login payload"):
                st.json(
                    {
                        "login_response": st.session_state.get("broker_login_response"),
                        "session_response": st.session_state.get("broker_session_response"),
                    }
                )

            with st.expander("Live scrip search"):
                search_symbol = st.text_input("Search symbol", value="nifty")
                search_expiry = st.text_input("Expiry (YYYYMM)", value="")
                search_type = st.selectbox("Option type", ["", "CE", "PE"])
                search_strike = st.text_input("Strike", value="")
                if st.button("Search via broker API"):
                    response, error = safe_api_call(
                        "Search scrip",
                        st.session_state["broker_client"].search_scrip,
                        exchange_segment="nse_fo",
                        symbol=search_symbol,
                        expiry=search_expiry or None,
                        option_type=search_type or None,
                        strike_price=search_strike or None,
                    )
                    if error:
                        st.error(error)
                    else:
                        frame = response_frame(response)
                        if frame.empty:
                            st.json(response)
                        else:
                            st.dataframe(frame, use_container_width=True, hide_index=True)
        else:
            st.info("Broker not connected. Quotes can still work if only KOTAK_CONSUMER_KEY is set.")


if __name__ == "__main__":
    main()
