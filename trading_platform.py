import importlib
import math
import os
import re
import threading
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import pyotp
except Exception:
    pyotp = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "march_options.csv"
NIFTY_INDEX_TOKEN = "26000"

if load_dotenv is not None:
    load_dotenv(ROOT / ".env")


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    renamed = {}
    for column in frame.columns:
        normalized = re.sub(r"[^0-9a-zA-Z]+", "_", str(column).strip()).strip("_").lower()
        renamed[column] = normalized
    return frame.rename(columns=renamed)


def env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_neo_api_class():
    try:
        module = importlib.import_module("neo_api_client")
        return module.NeoAPI
    except Exception:
        return None


def load_market_data(csv_path: str | Path = DATA_FILE) -> tuple[pd.DataFrame, pd.DataFrame]:
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
        "spot": round(spot, 2),
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


def strategy_payoff(strategy: dict[str, Any], spot: float, lot_size: int, lots: int) -> list[dict[str, float]]:
    lower = max(0.0, spot * 0.92)
    upper = spot * 1.08
    step = (upper - lower) / 69 if upper > lower else 1.0
    price_grid = pd.Series([round(lower + (step * index), 2) for index in range(70)])
    total = pd.Series([0.0] * len(price_grid))
    for leg in strategy["legs"]:
        total += payoff_for_leg(price_grid, leg["side"], leg["strike"], leg["premium"], leg["action"], lots, lot_size)
    return [{"underlying": float(x), "payoff": float(y)} for x, y in zip(price_grid, total)]


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


def records_from_frame(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clean = frame.copy().where(pd.notna(frame), None)
    return clean.to_dict(orient="records")


def response_message(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("message", "Message", "Error", "error", "status_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value and isinstance(value[0], dict):
                nested_message = response_message(value[0])
                if nested_message:
                    return nested_message
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("message", "status", "error"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def response_success(payload: Any) -> bool:
    if payload is None:
        return False
    if isinstance(payload, dict):
        if payload.get("Error"):
            return False
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return False
        if isinstance(error, list) and error:
            return False
        status = str(payload.get("status") or payload.get("stat") or "").strip().lower()
        if status in {"ok", "success", "successful", "true", "s"}:
            return True
        if payload.get("data") is not None and not error:
            return True
        message = response_message(payload).lower()
        return "success" in message
    return bool(payload)


def friendly_sdk_error(error: str) -> str:
    if "unsupported operand type(s) for +=: 'NoneType' and 'str'" in error:
        return "Quote API is not ready yet. In Kotak Neo prod, the SDK needs a successful TOTP login before it receives the base URL used for quotes."
    return error


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
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.iloc[0])


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


class TradingPlatformState:
    def __init__(self) -> None:
        options, _ = load_market_data(DATA_FILE)
        self.lock = threading.Lock()
        self.broker_client = None
        self.quote_client = None
        self.login_response = None
        self.session_response = None
        self.custom_alerts: list[dict[str, Any]] = []
        self.staged_legs: list[dict[str, Any]] = []
        self.reference_spot = infer_reference_spot(options)
        self.credentials = {
            "environment": env_value("KOTAK_ENVIRONMENT", "prod"),
            "consumer_key": env_value("KOTAK_CONSUMER_KEY"),
            "mobile_number": env_value("KOTAK_MOBILE_NUMBER"),
            "ucc": env_value("KOTAK_UCC"),
            "mpin": env_value("KOTAK_MPIN"),
            "totp_secret": env_value("KOTAK_TOTP_SECRET"),
        }
        self.last_broker_message = ""
        self.last_diagnostic: dict[str, Any] | None = None

    def broker_ready(self) -> bool:
        return self.broker_client is not None

    def to_summary(self) -> dict[str, Any]:
        return {
            "broker_ready": self.broker_ready(),
            "sdk_installed": load_neo_api_class() is not None,
            "env_status": {
                "KOTAK_ENVIRONMENT": bool(self.credentials["environment"]),
                "KOTAK_CONSUMER_KEY": bool(self.credentials["consumer_key"]),
                "KOTAK_MOBILE_NUMBER": bool(self.credentials["mobile_number"]),
                "KOTAK_UCC": bool(self.credentials["ucc"]),
                "KOTAK_MPIN": bool(self.credentials["mpin"]),
                "KOTAK_TOTP_SECRET": bool(self.credentials["totp_secret"]),
            },
            "last_broker_message": self.last_broker_message,
        }


class TradingPlatform:
    def __init__(self) -> None:
        self.state = TradingPlatformState()

    def credentials_payload(self) -> dict[str, str]:
        return dict(self.state.credentials)

    def resolve_credentials(self, overrides: dict[str, Any] | None = None) -> dict[str, str]:
        resolved = dict(self.state.credentials)
        if overrides:
            for key in ("environment", "consumer_key", "mobile_number", "ucc", "mpin", "totp_secret"):
                value = overrides.get(key)
                if value is not None and str(value).strip():
                    resolved[key] = str(value).strip()
        if not resolved["environment"]:
            resolved["environment"] = "prod"
        return resolved

    def create_neo_client(self, consumer_key: str, environment: str | None = None):
        neo_api = load_neo_api_class()
        if neo_api is None or not consumer_key:
            return None
        return neo_api(environment=(environment or "prod"), access_token=None, neo_fin_key=None, consumer_key=consumer_key)

    def active_market_client(self):
        if self.state.broker_client is not None:
            return self.state.broker_client
        if self.state.quote_client is not None:
            return self.state.quote_client
        consumer_key = self.state.credentials["consumer_key"]
        if not consumer_key:
            return None
        self.state.quote_client = self.create_neo_client(consumer_key, self.state.credentials["environment"])
        return self.state.quote_client

    def safe_api_call(self, label: str, fn, *args, **kwargs) -> tuple[Any, str | None]:
        try:
            return fn(*args, **kwargs), None
        except Exception as exc:
            return None, f"{label} failed: {exc}"

    def quote_frame_with_client(self, client, instruments: list[dict[str, str]], quote_type: str) -> tuple[pd.DataFrame, str | None]:
        if client is None or not instruments:
            return pd.DataFrame(), "Client or instrument list is missing."
        response, error = self.safe_api_call("Quotes", client.quotes, instrument_tokens=instruments, quote_type=quote_type)
        if error:
            return pd.DataFrame(), friendly_sdk_error(error)
        frame = response_frame(response)
        if frame.empty and isinstance(response, dict):
            return pd.DataFrame(), response_message(response) or str(response)
        return frame, None

    def connect_broker(self, overrides: dict[str, Any] | None = None) -> tuple[bool, str]:
        credentials = self.resolve_credentials(overrides)
        totp_code = str((overrides or {}).get("totp") or "").strip()

        if not totp_code and credentials.get("totp_secret") and pyotp is not None:
            try:
                totp_code = pyotp.TOTP(credentials["totp_secret"]).now()
            except Exception:
                totp_code = ""

        if not all([credentials["consumer_key"], credentials["mobile_number"], credentials["ucc"], credentials["mpin"]]):
            return False, "Set KOTAK_CONSUMER_KEY, KOTAK_MOBILE_NUMBER, KOTAK_UCC, and KOTAK_MPIN first."
        if load_neo_api_class() is None:
            return False, "neo_api_client is not installed in this Python environment."
        if not totp_code:
            return False, "TOTP is required for broker login."

        client = self.create_neo_client(credentials["consumer_key"], credentials["environment"])
        if client is None:
            return False, "Could not create the Neo client. Check environment and consumer key."
        login_response = client.totp_login(mobile_number=credentials["mobile_number"], ucc=credentials["ucc"], totp=totp_code)
        if not response_success(login_response):
            message = response_message(login_response) or "TOTP login failed. Recheck your consumer key, mobile number, UCC, and current TOTP."
            with self.state.lock:
                self.state.credentials.update(credentials)
                self.state.last_broker_message = message
                self.state.last_diagnostic = {
                    "sdk_installed": True,
                    "environment": credentials["environment"],
                    "missing_fields": [],
                    "login": login_response,
                    "session": None,
                }
            return False, message

        session_response = client.totp_validate(mpin=credentials["mpin"])
        if not response_success(session_response):
            message = response_message(session_response) or "MPIN validation failed. Check the MPIN and try again."
            with self.state.lock:
                self.state.credentials.update(credentials)
                self.state.last_broker_message = message
                self.state.last_diagnostic = {
                    "sdk_installed": True,
                    "environment": credentials["environment"],
                    "missing_fields": [],
                    "login": login_response,
                    "session": session_response,
                }
            return False, message

        with self.state.lock:
            self.state.credentials.update(credentials)
            self.state.broker_client = client
            self.state.quote_client = client
            self.state.login_response = login_response
            self.state.session_response = session_response
            self.state.last_broker_message = "Broker connected successfully."
            self.state.last_diagnostic = {
                "sdk_installed": True,
                "environment": credentials["environment"],
                "missing_fields": [],
                "login": {"ok": True, "message": response_message(login_response) or "TOTP accepted."},
                "session": {"ok": True, "message": response_message(session_response) or "Session established."},
            }
        return True, "Broker session established."

    def logout_broker(self) -> dict[str, Any]:
        with self.state.lock:
            client = self.state.broker_client
            self.state.broker_client = None
            self.state.quote_client = None
            self.state.last_broker_message = "Broker disconnected."
        if client is None:
            return {"status": "ok", "message": "Broker already disconnected."}
        response, error = self.safe_api_call("Logout", client.logout)
        if error:
            return {"status": "error", "message": error}
        return {"status": "ok", "message": "Broker disconnected.", "response": response}

    def live_quote_frame(self, instruments: list[dict[str, str]], quote_type: str) -> tuple[pd.DataFrame, str | None]:
        return self.quote_frame_with_client(self.active_market_client(), instruments, quote_type)

    def test_connection(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        credentials = self.resolve_credentials(overrides)
        missing = [key for key in ("consumer_key", "mobile_number", "ucc", "mpin") if not credentials.get(key)]
        result = {
            "sdk_installed": load_neo_api_class() is not None,
            "environment": credentials["environment"],
            "missing_fields": missing,
            "client_created": False,
            "ready_to_connect": False,
            "notes": [],
            "quote_test": None,
        }
        with self.state.lock:
            self.state.credentials.update(credentials)
        if missing:
            result["notes"].append("Add the missing broker fields first, then run Test again.")
            self.state.last_diagnostic = result
            return {"status": "error", "message": f"Missing fields: {', '.join(missing)}", "diagnostic": result}

        client = self.create_neo_client(credentials["consumer_key"], credentials["environment"])
        if client is None:
            self.state.last_diagnostic = result
            return {"status": "error", "message": "Could not create Neo client.", "diagnostic": result}
        result["client_created"] = True

        if credentials["environment"].lower() == "prod":
            result["ready_to_connect"] = True
            result["notes"].append("Prod credentials look present. Next step is TOTP login.")
            result["notes"].append("The official Kotak SDK only exposes the prod quote base URL after successful TOTP + MPIN validation.")
            if not ((overrides or {}).get("totp") or credentials.get("totp_secret")):
                result["notes"].append("Enter a fresh TOTP or provide a TOTP secret, then press Connect.")
                self.state.last_diagnostic = result
                return {
                    "status": "ok",
                    "message": "Credentials loaded. Add TOTP and press Connect.",
                    "diagnostic": result,
                }
        frame, error = self.quote_frame_with_client(
            client,
            [{"instrument_token": NIFTY_INDEX_TOKEN, "exchange_segment": "nse_cm"}],
            "ltp",
        )
        result["quote_test"] = {
            "error": error,
            "records": records_from_frame(frame),
            "spot": nifty_ltp_from_quotes(frame),
        }
        self.state.last_diagnostic = result
        if error:
            return {"status": "error", "message": error, "diagnostic": result}
        return {"status": "ok", "message": "Quote API test succeeded.", "diagnostic": result}

    def load_chain(self, expiry: str | None = None, spot: float | None = None, csv_path: str | Path = DATA_FILE) -> dict[str, Any]:
        options, _ = load_market_data(csv_path)
        expiries = [code for code in options["expiry_code"].dropna().unique().tolist() if code]
        selected_expiry = expiry if expiry in expiries else (expiries[0] if expiries else None)
        chain = options[options["expiry_code"] == selected_expiry].copy() if selected_expiry else options.copy()

        reference_spot = float(spot) if spot is not None else float(self.state.reference_spot or infer_reference_spot(chain))
        metrics = compute_metrics(chain, reference_spot)
        signals, signal_score, stance = compute_signal_pack(metrics)
        alerts = build_alert_feed(metrics, signal_score, self.state.custom_alerts)
        ideas = recommendation_table(chain, reference_spot)
        watchlist = build_watchlist(chain, metrics)
        pe_supports = oi_ladder(chain, "PE")
        ce_resistances = oi_ladder(chain, "CE")

        return {
            "expiries": expiries,
            "selected_expiry": selected_expiry,
            "chain": chain,
            "metrics": metrics,
            "signals": signals,
            "signal_score": signal_score,
            "stance": stance,
            "alerts": alerts,
            "ideas": ideas,
            "watchlist": watchlist,
            "pe_supports": pe_supports,
            "ce_resistances": ce_resistances,
        }

    def dashboard_payload(self, expiry: str | None = None, spot: float | None = None) -> dict[str, Any]:
        chain_data = self.load_chain(expiry=expiry, spot=spot)
        chain = chain_data["chain"]
        metrics = chain_data["metrics"]
        strategies = build_strategy_catalog(chain, metrics["spot"])
        staged = self.state.staged_legs

        return {
            "broker": self.state.to_summary(),
            "credentials": self.credentials_payload(),
            "expiries": chain_data["expiries"],
            "selected_expiry": chain_data["selected_expiry"],
            "metrics": chain_data["metrics"],
            "signals": chain_data["signals"],
            "signal_score": chain_data["signal_score"],
            "stance": chain_data["stance"],
            "alerts": chain_data["alerts"],
            "ideas": records_from_frame(chain_data["ideas"]),
            "watchlist": records_from_frame(chain_data["watchlist"]),
            "pe_supports": records_from_frame(chain_data["pe_supports"]),
            "ce_resistances": records_from_frame(chain_data["ce_resistances"]),
            "strategies": [
                {
                    "name": strategy["name"],
                    "outlook": strategy["outlook"],
                    "description": strategy["description"],
                    "legs": strategy["legs"],
                    "payoff": strategy_payoff(strategy, chain_data["metrics"]["spot"], chain_data["metrics"]["lot_size"], 1),
                }
                for strategy in strategies
            ],
            "staged_legs": staged,
            "last_diagnostic": self.state.last_diagnostic,
        }

    def add_alert(self, name: str, metric: str, operator: str, threshold: float) -> dict[str, Any]:
        alert = {"name": name, "metric": metric, "operator": operator, "threshold": threshold}
        with self.state.lock:
            self.state.custom_alerts.append(alert)
        return alert

    def clear_staged(self) -> None:
        with self.state.lock:
            self.state.staged_legs = []

    def stage_strategy_by_name(self, expiry: str | None, spot: float | None, strategy_name: str, lots: int) -> dict[str, Any]:
        chain_data = self.load_chain(expiry=expiry, spot=spot)
        strategies = build_strategy_catalog(chain_data["chain"], chain_data["metrics"]["spot"])
        selected = next((strategy for strategy in strategies if strategy["name"] == strategy_name), None)
        if selected is None:
            return {"status": "error", "message": "Strategy not found."}
        staged, missing = stage_strategy(selected, chain_data["chain"], lots)
        with self.state.lock:
            self.state.staged_legs = staged
        return {"status": "ok", "strategy": selected, "staged_legs": staged, "missing": missing}

    def margin_preview(self, payload: dict[str, str], instrument_token: str) -> Any:
        client = self.state.broker_client
        if client is None:
            return {"info": "Broker client is not connected."}
        response, error = self.safe_api_call(
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

    def place_order(self, payload: dict[str, str]) -> dict[str, Any]:
        if self.state.broker_client is None:
            return {"status": "error", "message": "Broker is not connected."}
        response, error = self.safe_api_call("Place order", self.state.broker_client.place_order, **payload)
        if error:
            return {"status": "error", "message": error}
        return {"status": "ok", "response": response}

    def modify_order(self, order_id: str, price: str, order_type: str, quantity: str, trigger_price: str = "0") -> dict[str, Any]:
        if self.state.broker_client is None:
            return {"status": "error", "message": "Broker is not connected."}
        response, error = self.safe_api_call(
            "Modify order",
            self.state.broker_client.modify_order,
            order_id=order_id,
            price=price,
            order_type=order_type,
            quantity=quantity,
            validity="DAY",
            trigger_price=trigger_price,
        )
        if error:
            return {"status": "error", "message": error}
        return {"status": "ok", "response": response}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if self.state.broker_client is None:
            return {"status": "error", "message": "Broker is not connected."}
        response, error = self.safe_api_call("Cancel order", self.state.broker_client.cancel_order, order_id=order_id, isVerify=True, amo="NO")
        if error:
            return {"status": "error", "message": error}
        return {"status": "ok", "response": response}

    def execute_staged(self) -> list[dict[str, Any]]:
        results = []
        if self.state.broker_client is None:
            return [{"status": "error", "message": "Broker is not connected."}]
        for leg in self.state.staged_legs:
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
            response, error = self.safe_api_call("Place order", self.state.broker_client.place_order, **payload)
            results.append({"symbol": leg["symbol"], "payload": payload, "error": error, "response": response})
        return results

    def account_snapshot(self) -> dict[str, Any]:
        client = self.state.broker_client
        if client is None:
            return {"status": "error", "message": "Broker is not connected."}
        calls = {
            "order_book": client.order_report,
            "trade_book": client.trade_report,
            "positions": client.positions,
            "holdings": client.holdings,
            "limits": client.limits,
        }
        snapshot = {}
        for name, fn in calls.items():
            response, error = self.safe_api_call(name, fn)
            snapshot[name] = {"error": error, "records": records_from_frame(response_frame(response)), "raw": response}
        return {"status": "ok", "snapshot": snapshot}

    def search_scrip(self, symbol: str, expiry: str | None, option_type: str | None, strike_price: str | None) -> dict[str, Any]:
        client = self.state.broker_client
        if client is None:
            return {"status": "error", "message": "Broker is not connected."}
        response, error = self.safe_api_call(
            "Search scrip",
            client.search_scrip,
            exchange_segment="nse_fo",
            symbol=symbol,
            expiry=expiry or None,
            option_type=option_type or None,
            strike_price=strike_price or None,
        )
        if error:
            return {"status": "error", "message": error}
        return {"status": "ok", "records": records_from_frame(response_frame(response)), "raw": response}

    def fetch_spot_quote(self) -> dict[str, Any]:
        frame, error = self.live_quote_frame(
            [{"instrument_token": NIFTY_INDEX_TOKEN, "exchange_segment": "nse_cm"}],
            "ltp",
        )
        if error:
            return {"status": "error", "message": error}
        ltp = nifty_ltp_from_quotes(frame)
        if ltp is not None:
            self.state.reference_spot = ltp
        return {"status": "ok", "spot": ltp, "records": records_from_frame(frame)}

    def fetch_watchlist_quotes(self, expiry: str | None, spot: float | None) -> dict[str, Any]:
        chain_data = self.load_chain(expiry=expiry, spot=spot)
        watchlist = chain_data["watchlist"]
        instruments = [
            {"instrument_token": str(row.instrument_token), "exchange_segment": str(row.exchange_segment)}
            for row in watchlist.itertuples()
        ]
        frame, error = self.live_quote_frame(instruments, "ltp")
        if error:
            return {"status": "error", "message": error}
        return {"status": "ok", "records": records_from_frame(frame)}

    def auto_totp(self) -> str:
        secret = self.state.credentials.get("totp_secret") or env_value("KOTAK_TOTP_SECRET")
        if secret and pyotp is not None:
            try:
                return pyotp.TOTP(secret).now()
            except Exception:
                return ""
        return ""

    def current_totp(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        credentials = self.resolve_credentials(overrides)
        secret = credentials.get("totp_secret") or ""
        if not secret:
            return {"status": "error", "message": "TOTP secret is missing."}
        if pyotp is None:
            return {"status": "error", "message": "pyotp is not installed in this environment."}
        try:
            code = pyotp.TOTP(secret).now()
        except Exception as exc:
            return {"status": "error", "message": f"Could not generate TOTP: {exc}"}
        with self.state.lock:
            self.state.credentials.update(credentials)
        return {"status": "ok", "totp": code, "message": "Fresh TOTP generated."}
