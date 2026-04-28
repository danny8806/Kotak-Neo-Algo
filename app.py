from pathlib import Path

from flask import Flask, jsonify, render_template, request

from trading_platform import DATA_FILE, TradingPlatform


ROOT = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
platform = TradingPlatform()


def json_ok(**payload):
    return jsonify({"status": "ok", **payload})


def json_error(message: str, code: int = 400):
    return jsonify({"status": "error", "message": message}), code


@app.get("/")
def index():
    return render_template("index.html", data_file=str(DATA_FILE))


@app.get("/api/bootstrap")
def bootstrap():
    expiry = request.args.get("expiry") or None
    spot = request.args.get("spot", type=float)
    payload = platform.dashboard_payload(expiry=expiry, spot=spot)
    payload["auto_totp"] = platform.auto_totp()
    return jsonify(payload)


@app.post("/api/login")
def api_login():
    body = request.get_json(silent=True) or {}
    ok, message = platform.connect_broker(body)
    if not ok:
        return json_error(message)
    return json_ok(message=message, broker=platform.state.to_summary())


@app.post("/api/diagnostics")
def api_diagnostics():
    body = request.get_json(silent=True) or {}
    result = platform.test_connection(body)
    if result["status"] != "ok":
        return jsonify(result), 400
    return jsonify(result)


@app.post("/api/totp")
def api_totp():
    body = request.get_json(silent=True) or {}
    result = platform.current_totp(body)
    if result["status"] != "ok":
        return jsonify(result), 400
    return jsonify(result)


@app.post("/api/logout")
def api_logout():
    return jsonify(platform.logout_broker())


@app.get("/api/setups")
def api_setups():
    return json_ok(saved_setups=platform.saved_setup_summaries(), active_setup_name=platform.state.active_setup_name)


@app.post("/api/setups")
def api_save_setup():
    body = request.get_json(silent=True) or {}
    result = platform.save_setup(name=body.get("name") or "", overrides=body)
    if result["status"] != "ok":
        return jsonify(result), 400
    return jsonify(result)


@app.post("/api/setups/use")
def api_use_setup():
    body = request.get_json(silent=True) or {}
    result = platform.use_setup(name=body.get("name") or "")
    if result["status"] != "ok":
        return jsonify(result), 400
    return jsonify(result)


@app.post("/api/setups/delete")
def api_delete_setup():
    body = request.get_json(silent=True) or {}
    result = platform.delete_setup(name=body.get("name") or "")
    if result["status"] != "ok":
        return jsonify(result), 400
    return jsonify(result)


@app.post("/api/alerts")
def api_alerts():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    metric = (body.get("metric") or "").strip()
    operator = (body.get("operator") or "").strip()
    threshold = body.get("threshold")
    if not name or not metric or operator not in {">=", "<="}:
        return json_error("Alert fields are incomplete.")
    alert = platform.add_alert(name=name, metric=metric, operator=operator, threshold=float(threshold))
    return json_ok(alert=alert)


@app.post("/api/strategy/stage")
def api_stage_strategy():
    body = request.get_json(silent=True) or {}
    strategy_name = (body.get("strategy_name") or "").strip()
    lots = int(body.get("lots") or 1)
    expiry = body.get("expiry") or None
    spot = body.get("spot")
    if not strategy_name:
        return json_error("Strategy name is required.")
    result = platform.stage_strategy_by_name(expiry=expiry, spot=float(spot) if spot is not None else None, strategy_name=strategy_name, lots=lots)
    if result["status"] != "ok":
        return jsonify(result), 400
    return jsonify(result)


@app.post("/api/strategy/clear")
def api_clear_strategy():
    platform.clear_staged()
    return json_ok(message="Staged basket cleared.")


@app.post("/api/strategy/execute")
def api_execute_strategy():
    return jsonify({"status": "ok", "results": platform.execute_staged()})


@app.get("/api/quotes/spot")
def api_quotes_spot():
    return jsonify(platform.fetch_spot_quote())


@app.get("/api/quotes/watchlist")
def api_quotes_watchlist():
    expiry = request.args.get("expiry") or None
    spot = request.args.get("spot", type=float)
    return jsonify(platform.fetch_watchlist_quotes(expiry=expiry, spot=spot))


@app.post("/api/orders/margin")
def api_orders_margin():
    body = request.get_json(silent=True) or {}
    payload = body.get("payload") or {}
    instrument_token = str(body.get("instrument_token") or "")
    if not payload or not instrument_token:
        return json_error("Payload and instrument token are required.")
    return jsonify({"status": "ok", "preview": platform.margin_preview(payload, instrument_token)})


@app.post("/api/orders/place")
def api_orders_place():
    body = request.get_json(silent=True) or {}
    payload = body.get("payload") or {}
    if not payload:
        return json_error("Order payload is required.")
    return jsonify(platform.place_order(payload))


@app.post("/api/orders/modify")
def api_orders_modify():
    body = request.get_json(silent=True) or {}
    order_id = (body.get("order_id") or "").strip()
    price = str(body.get("price") or "")
    order_type = (body.get("order_type") or "").strip()
    quantity = str(body.get("quantity") or "")
    trigger_price = str(body.get("trigger_price") or "0")
    if not order_id:
        return json_error("Order id is required.")
    return jsonify(platform.modify_order(order_id=order_id, price=price, order_type=order_type, quantity=quantity, trigger_price=trigger_price))


@app.post("/api/orders/cancel")
def api_orders_cancel():
    body = request.get_json(silent=True) or {}
    order_id = (body.get("order_id") or "").strip()
    if not order_id:
        return json_error("Order id is required.")
    return jsonify(platform.cancel_order(order_id))


@app.get("/api/account")
def api_account():
    return jsonify(platform.account_snapshot())


@app.get("/api/search-scrip")
def api_search_scrip():
    symbol = (request.args.get("symbol") or "").strip()
    expiry = (request.args.get("expiry") or "").strip() or None
    option_type = (request.args.get("option_type") or "").strip() or None
    strike_price = (request.args.get("strike_price") or "").strip() or None
    if not symbol:
        return json_error("Symbol is required.")
    return jsonify(platform.search_scrip(symbol=symbol, expiry=expiry, option_type=option_type, strike_price=strike_price))


@app.post("/api/ai/brief")
def api_ai_brief():
    body = request.get_json(silent=True) or {}
    expiry = body.get("expiry") or None
    spot = body.get("spot")
    result = platform.ai_brief(
        expiry=expiry,
        spot=float(spot) if spot is not None else None,
        question=body.get("question"),
        focus=body.get("focus"),
    )
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
