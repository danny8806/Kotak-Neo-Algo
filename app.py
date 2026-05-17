from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv
load_dotenv()
from neo_api_client import NeoAPI
import pyotp
import time
import os
import json
import requests
from datetime import datetime
from websocket_handler import init_live_data_handler, get_live_data_handler
from ai_assistant import get_ai_assistant
from algo_engine import get_strategies, save_strategies, get_trade_log, add_trade_entry, run_backtest, start_engine, stop_engine, engine_state
import pandas as pd

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

CONSUMER_KEY = "373e2f89-7c71-4665-be61-887c6aec0d68"
MOBILE_NUMBER = "+918459857355"
UCC = "W9VOM"
MPIN = "123456"

client = None
session_data = None
live_data_handler = None
ai_assistant = get_ai_assistant()
kill_switch_active = False

EXCHANGE_MAP = {
    "NSE": "nse_cm", "BSE": "bse_cm", "NFO": "nse_fo",
    "BFO": "bse_fo", "CDS": "cde_fo", "MCX": "mcx_fo"
}
TX_MAP = {"BUY": "B", "SELL": "S"}
ORDER_TYPE_MAP = {
    "MARKET": "MKT", "LIMIT": "L", "SL": "SL", "SL-M": "SL-M"
}
PRODUCT_MAP = {"MIS": "MIS", "NRML": "NRML", "CNC": "CNC", "CO": "CO", "BO": "BO"}

def _map_exchange(seg):
    return EXCHANGE_MAP.get(seg, seg)

def _map_tx(tx):
    return TX_MAP.get(tx, tx)

def _map_order_type(ot):
    return ORDER_TYPE_MAP.get(ot, ot)

def _build_ai_context():
    ctx = {
        "current_time": str(datetime.now()),
        "logged_in": session_data is not None
    }
    if session_data:
        ctx["user"] = session_data.get("greetingName", "User")
    return ctx

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

@socketio.on('subscribe_live_data')
def handle_subscribe_live_data(data):
    try:
        if not live_data_handler:
            emit('error', {'message': 'Live data handler not initialized'})
            return
        instrument_tokens = data.get('tokens', [])
        exchange_segment = data.get('exchange_segment', 'nse_cm')
        if not instrument_tokens:
            emit('error', {'message': 'No instrument tokens provided'})
            return
        if not live_data_handler.connected:
            if not live_data_handler.connect():
                emit('error', {'message': 'Failed to connect to live data feed'})
                return
        if live_data_handler.subscribe(instrument_tokens, exchange_segment):
            emit('subscription_success', {'tokens': instrument_tokens, 'exchange_segment': exchange_segment})
        else:
            emit('error', {'message': 'Failed to subscribe to live data'})
    except Exception as e:
        emit('error', {'message': str(e)})

@socketio.on('unsubscribe_live_data')
def handle_unsubscribe_live_data(data):
    try:
        if not live_data_handler:
            emit('error', {'message': 'Live data handler not initialized'})
            return
        instrument_tokens = data.get('tokens', [])
        if not instrument_tokens:
            emit('error', {'message': 'No instrument tokens provided'})
            return
        if live_data_handler.unsubscribe(instrument_tokens):
            emit('unsubscription_success', {'tokens': instrument_tokens})
        else:
            emit('error', {'message': 'Failed to unsubscribe from live data'})
    except Exception as e:
        emit('error', {'message': str(e)})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    totp = data.get('totp', '').strip()
    if not totp or len(totp) != 6:
        return jsonify({'error': 'A valid 6-digit TOTP is required'}), 400
    env = data.get('environment', 'prod').strip().lower()
    if env not in ('prod', 'uat'):
        return jsonify({'error': 'Environment must be prod or uat'}), 400
    global client, session_data
    try:
        client = NeoAPI(
            environment=env,
            access_token=None,
            neo_fin_key=None,
            consumer_key=CONSUMER_KEY
        )
    except Exception as e:
        return jsonify({'error': 'Failed to initialize NeoAPI: %s' % e}), 500
    try:
        login_response = client.totp_login(
            mobile_number=MOBILE_NUMBER,
            ucc=UCC,
            totp=totp
        )
    except Exception as e:
        client = None
        return jsonify({'error': 'TOTP login failed: %s' % e}), 401
    if login_response.get('data', {}).get('status') != 'success':
        msg = login_response.get('data', {}).get('message', 'Invalid TOTP or credentials')
        client = None
        return jsonify({'error': msg}), 401
    try:
        session_response = client.totp_validate(mpin=MPIN)
    except Exception as e:
        client = None
        return jsonify({'error': 'MPIN validation failed: %s' % e}), 401
    if session_response.get('data', {}).get('status') != 'success':
        msg = session_response.get('data', {}).get('message', 'MPIN validation failed')
        client = None
        return jsonify({'error': msg}), 401
    session_data = session_response.get('data', {})
    # Fix: SDK sends empty hsServerId as sId= query param — use data_center as fallback
    if not client.configuration.serverId and client.configuration.data_center:
        client.configuration.serverId = client.configuration.data_center
    global live_data_handler
    live_data_handler = init_live_data_handler(client)
    live_data_handler.set_socketio(socketio)
    global ai_assistant
    ai_assistant = get_ai_assistant()
    # Also store env for session info
    session_data['_env'] = env
    return jsonify({
        'success': True,
        'message': 'Login successful',
        'environment': env,
        'user': {
            'ucc': session_data.get('ucc'),
            'name': session_data.get('greetingName'),
            'clientType': session_data.get('clientType'),
            'isTrialAccount': session_data.get('isTrialAccount'),
            'dormancyStatus': session_data.get('dormancyStatus'),
            'dataCenter': session_data.get('dataCenter')
        }
    })

@app.route('/api/profile', methods=['GET'])
def get_profile():
    try:
        if not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        return jsonify({
            'success': True,
            'profile': {
                'ucc': session_data.get('ucc'),
                'name': session_data.get('greetingName'),
                'clientType': session_data.get('clientType'),
                'dataCenter': session_data.get('dataCenter'),
                'dormancyStatus': session_data.get('dormancyStatus'),
                'kId': session_data.get('kId'),
                'isTrialAccount': session_data.get('isTrialAccount'),
                'environment': session_data.get('_env', 'prod')
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/session-debug', methods=['GET'])
def session_debug():
    if not client or not session_data:
        return jsonify({'error': 'Not logged in'}), 401
    try:
        cfg = client.configuration
        return jsonify({
            'success': True,
            'environment': getattr(cfg, 'host', None),
            'has_edit_token': bool(getattr(cfg, 'edit_token', None)),
            'has_edit_sid': bool(getattr(cfg, 'edit_sid', None)),
            'has_server_id': bool(getattr(cfg, 'serverId', None)),
            'has_view_token': bool(getattr(cfg, 'view_token', None)),
            'has_consumer_key': bool(getattr(cfg, 'consumer_key', None)),
            'data_center': getattr(cfg, 'data_center', None),
            'base_url': getattr(cfg, 'base_url', None),
            'session_ucc': session_data.get('ucc'),
            'is_trial': session_data.get('isTrialAccount'),
            'dormant': session_data.get('dormancyStatus'),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/holdings', methods=['GET'])
def get_holdings():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        holdings = client.holdings()
        summary = ""
        if ai_assistant and holdings:
            ctx = _build_ai_context()
            ai_assistant.set_trading_context(ctx)
            summary = ai_assistant.chat_query(
                f"Summarize this portfolio holdings data in 2 lines: {holdings}"
            )
        return jsonify({
            'success': True,
            'holdings': holdings,
            'ai_summary': summary
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders', methods=['GET'])
def get_orders():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        orders = client.order_report()
        summary = ""
        if ai_assistant and orders:
            ctx = _build_ai_context()
            ai_assistant.set_trading_context(ctx)
            summary = ai_assistant.chat_query(
                f"Summarize this order book in 1-2 lines: {orders}"
            )
        return jsonify({
            'success': True,
            'orders': orders,
            'ai_summary': summary
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/place-order', methods=['POST'])
def place_order():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        if kill_switch_active:
            return jsonify({'error': 'Kill switch is ON. Turn it off to place trades.', 'kill_switch_blocked': True}), 403
        data = request.json
        exchange_segment = _map_exchange(data.get('exchange_segment', 'NSE'))
        transaction_type = _map_tx(data.get('transaction_type', 'BUY'))
        order_type = _map_order_type(data.get('order_type', 'MARKET'))
        product = data.get('product', 'MIS')
        quantity = str(int(data.get('quantity', 1)))
        price = str(data.get('price', '0'))
        trigger_price = str(data.get('trigger_price', '0'))
        validity = data.get('validity', 'DAY')
        trading_symbol = data.get('trading_symbol', '')
        amo = data.get('amo', 'NO')
        disclosed_quantity = str(data.get('disclosed_quantity', '0'))
        market_protection = str(data.get('market_protection', '0'))
        pf = data.get('pf', 'N')
        tag = data.get('tag')
        blocked = _enforce_risk("trade")
        if blocked:
            return jsonify({"error": blocked, "risk_blocked": True}), 403
        risk_msg = None
        if risk_config["enabled"] and risk_config["max_position_size"] > 0:
            pos_val = float(price) * int(quantity) if price and price != '0' else 0
            if pos_val > risk_config["max_position_size"]:
                risk_msg = "Position size %s exceeds max %s. Reduce quantity or disable risk management." % (pos_val, risk_config["max_position_size"])
        if risk_msg:
            ctx = _build_ai_context()
            ai_assistant.set_trading_context(ctx)
            risk_advice = ai_assistant.chat_query(
                f"Risk alert: {risk_msg}. Advise the user in 1 line."
            )
            return jsonify({"error": risk_msg, "risk_blocked": True, "ai_advice": risk_advice}), 403
        order_kwargs = dict(
            exchange_segment=exchange_segment,
            product=product,
            price=price,
            order_type=order_type,
            quantity=quantity,
            validity=validity,
            trading_symbol=trading_symbol,
            transaction_type=transaction_type,
            amo=amo,
            disclosed_quantity=disclosed_quantity,
            market_protection=market_protection,
            pf=pf,
            trigger_price=trigger_price,
            tag=tag,
        )
        if product in ("CO", "BO"):
            order_kwargs["square_off_value"] = str(data.get("square_off", "0"))
            order_kwargs["stop_loss_value"] = str(data.get("stop_loss", "0"))
            order_kwargs["square_off_type"] = "Absolute"
            order_kwargs["stop_loss_type"] = "Absolute"
        if product == "BO":
            trail_enabled = data.get("trailing_sl", "").strip()
            if trail_enabled:
                order_kwargs["trailing_stop_loss"] = trail_enabled
                order_kwargs["trailing_sl_value"] = str(data.get("trailing_sl_val", "0"))
            scrip_tok = data.get("scrip_token", "").strip()
            if scrip_tok:
                order_kwargs["scrip_token"] = scrip_tok
        order_response = client.place_order(**order_kwargs)
        _check_risk()
        # Check if SDK response indicates failure
        if isinstance(order_response, dict):
            stat = order_response.get("stat", "")
            err_msg = order_response.get("errMsg", "")
            st_code = order_response.get("stCode", "")
            if stat == "Not_Ok" or err_msg:
                risk_state["trade_count"] += 1
                advice = ""
                if ai_assistant:
                    ai_assistant.set_trading_context(_build_ai_context())
                    advice = ai_assistant.chat_query(
                        f"Order failed: {err_msg} (code {st_code}). Advise in 1 line."
                    )
                return jsonify({
                    "success": False,
                    "error": "Order rejected: %s (code %s)" % (err_msg, st_code),
                    "order": order_response,
                    "ai_advice": advice
                }), 200
        risk_state["trade_count"] += 1
        ai_note = ""
        if ai_assistant and order_response:
            ctx = _build_ai_context()
            ai_assistant.set_trading_context(ctx)
            ai_note = ai_assistant.chat_query(
                f"Order placed: {transaction_type} {quantity} of {trading_symbol} at {price}. "
                f"Give a 1-line risk note: {order_response}"
            )
        return jsonify({
            'success': True,
            'order': order_response,
            'ai_note': ai_note
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/quotes', methods=['GET'])
def get_quotes():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        instrument_tokens = request.args.get('tokens', '').split(',')
        exchange_segment = request.args.get('exchange', 'nse_cm')
        quote_type = request.args.get('type', 'all')
        tokens = [{"instrument_token": token.strip(), "exchange_segment": exchange_segment} 
                  for token in instrument_tokens if token.strip()]
        quotes = client.quotes(instrument_tokens=tokens, quote_type=quote_type)
        insight = ""
        if ai_assistant and quotes:
            ctx = _build_ai_context()
            ai_assistant.set_trading_context(ctx)
            insight = ai_assistant.chat_query(
                f"Give a 1-line insight on this quote data: {quotes}"
            )
        return jsonify({
            'success': True,
            'quotes': quotes,
            'ai_insight': insight
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/positions', methods=['GET'])
def get_positions():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        positions = client.positions()
        analysis = ""
        if ai_assistant and positions:
            ctx = _build_ai_context()
            ai_assistant.set_trading_context(ctx)
            analysis = ai_assistant.chat_query(
                f"Analyze these open positions in 2 lines and suggest action: {positions}"
            )
        return jsonify({
            'success': True,
            'positions': positions,
            'ai_analysis': analysis
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/limits', methods=['GET'])
def get_limits():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        segment = request.args.get('segment', 'ALL')
        exchange = request.args.get('exchange', 'ALL')
        product = request.args.get('product', 'ALL')
        limits = client.limits(segment=segment, exchange=exchange, product=product)
        return jsonify({
            'success': True,
            'limits': limits
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/margin-required', methods=['POST'])
def get_margin_required():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        data = request.json
        margin = client.margin_required(
            exchange_segment=_map_exchange(data.get('exchange_segment', 'NSE')),
            price=data.get('price', '0'),
            order_type=_map_order_type(data.get('order_type', 'MARKET')),
            product=data.get('product', 'MIS'),
            quantity=data.get('quantity', '1'),
            instrument_token=data.get('instrument_token', ''),
            transaction_type=_map_tx(data.get('transaction_type', 'BUY'))
        )
        return jsonify({
            'success': True,
            'margin': margin
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/scrip-master', methods=['GET'])
def get_scrip_master():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        exchange_segment = request.args.get('exchange_segment', None)
        if exchange_segment:
            scrips = client.scrip_master(exchange_segment=_map_exchange(exchange_segment))
        else:
            scrips = client.scrip_master()
        return jsonify({
            'success': True,
            'scrips': scrips
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/search-scrip', methods=['GET'])
def search_scrip():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        exchange_segment = request.args.get('exchange_segment')
        results = client.search_scrip(
            exchange_segment=_map_exchange(exchange_segment),
            symbol=request.args.get('symbol', ''),
            expiry=request.args.get('expiry', ''),
            option_type=request.args.get('option_type', ''),
            strike_price=request.args.get('strike_price', '')
        )
        return jsonify({
            'success': True,
            'results': results
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/modify-order', methods=['POST'])
def modify_order():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        data = request.json
        response = client.modify_order(
            order_id=data.get('order_id', ''),
            price=str(data.get('price', '0')),
            quantity=str(int(data.get('quantity', 1))),
            trigger_price=str(data.get('trigger_price', '0')),
            validity=data.get('validity', 'DAY'),
            order_type=_map_order_type(data.get('order_type', 'LIMIT')),
            disclosed_quantity=str(data.get('disclosed_quantity', '0')),
            amo=data.get('amo', 'NO')
        )
        return jsonify({
            'success': True,
            'response': response
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cancel-order', methods=['POST'])
def cancel_order_route():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        data = request.json
        is_verify = data.get('isVerify', 'true').lower() == 'true'
        response = client.cancel_order(
            order_id=data.get('order_id', ''),
            amo=data.get('amo', 'NO'),
            isVerify=is_verify
        )
        return jsonify({
            'success': True,
            'response': response
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cancel-cover-order', methods=['POST'])
def cancel_cover_order_route():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        data = request.json
        response = client.cancel_cover_order(
            order_id=data.get('order_id', ''),
            amo=data.get('amo', ''),
            isVerify=data.get('isVerify', 'false').lower() == 'true'
        )
        return jsonify({
            'success': True,
            'response': response
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cancel-bracket-order', methods=['POST'])
def cancel_bracket_order_route():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        data = request.json
        response = client.cancel_bracket_order(
            order_id=data.get('order_id', ''),
            amo=data.get('amo', ''),
            isVerify=data.get('isVerify', 'false').lower() == 'true'
        )
        return jsonify({
            'success': True,
            'response': response
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/order-history', methods=['GET'])
def get_order_history():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        order_id = request.args.get('order_id')
        history = client.order_history(order_id=order_id)
        return jsonify({
            'success': True,
            'history': history
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/trade-report', methods=['GET'])
def get_trade_report():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        order_id = request.args.get('order_id', None)
        if order_id:
            trades = client.trade_report(order_id=order_id)
        else:
            trades = client.trade_report()
        return jsonify({
            'success': True,
            'trades': trades
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/subscribe-order-feed', methods=['POST'])
def subscribe_order_feed():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        client.subscribe_to_orderfeed()
        return jsonify({'success': True, 'message': 'Order feed subscribed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai/analyze-market', methods=['POST'])
def analyze_market():
    try:
        if not ai_assistant:
            return jsonify({'error': 'AI assistant not initialized'}), 500
        data = request.json
        symbol = data.get('symbol', '')
        market_data = data.get('market_data', {})
        ai_assistant.set_trading_context(_build_ai_context())
        analysis = ai_assistant.analyze_market_data(symbol, market_data)
        return jsonify({'success': True, 'analysis': analysis, 'symbol': symbol})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai/generate-strategy', methods=['POST'])
def generate_strategy():
    try:
        if not ai_assistant:
            return jsonify({'error': 'AI assistant not initialized'}), 500
        data = request.json
        symbol = data.get('symbol', '')
        timeframe = data.get('timeframe', '1D')
        risk_tolerance = data.get('risk_tolerance', 'medium')
        strategy = ai_assistant.generate_trading_strategy(symbol, timeframe, risk_tolerance)
        return jsonify({'success': True, 'strategy': strategy, 'symbol': symbol, 'timeframe': timeframe, 'risk_tolerance': risk_tolerance})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai/risk-assessment', methods=['POST'])
def risk_assessment():
    try:
        if not ai_assistant:
            return jsonify({'error': 'AI assistant not initialized'}), 500
        data = request.json
        portfolio_data = data.get('portfolio', {})
        proposed_trade = data.get('trade', {})
        assessment = ai_assistant.risk_assessment(portfolio_data, proposed_trade)
        return jsonify({'success': True, 'assessment': assessment})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai/sentiment-analysis', methods=['POST'])
def sentiment_analysis():
    try:
        if not ai_assistant:
            return jsonify({'error': 'AI assistant not initialized'}), 500
        data = request.json
        news_data = data.get('news', [])
        market_data = data.get('market_data', {})
        analysis = ai_assistant.market_sentiment_analysis(news_data, market_data)
        return jsonify({'success': True, 'analysis': analysis})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai/analyze-trades', methods=['POST'])
def ai_analyze_trades():
    try:
        if not ai_assistant:
            return jsonify({'error': 'AI assistant not initialized'}), 500
        trades = []
        source = 'manual'
        manual_trades = request.json.get('trades', [])
        if manual_trades:
            trades = manual_trades
            source = 'manual'
        elif client and session_data:
            try:
                raw = client.trade_report()
                data = raw.get('data', raw) if isinstance(raw, dict) else raw
                if isinstance(data, list):
                    for t in data:
                        trades.append({
                            'symbol': t.get('tradingSymbol', t.get('sym', '')),
                            'side': 'BUY' if t.get('flBuyQty', '0') != '0' else 'SELL',
                            'qty': int(t.get('flBuyQty', t.get('flSellQty', '0'))),
                            'price': float(t.get('avgPrice', t.get('flPrc', 0))),
                            'time': t.get('tradeTime', t.get('fillTime', '')),
                            'product': t.get('product', ''),
                        })
                source = 'neokotak'
            except:
                pass
        if not trades:
            from algo_engine import get_trade_log
            algo_logs = get_trade_log()[-50:]
            for t in algo_logs:
                if t.get('action') in ('BUY', 'SELL', 'BUY_SIGNAL', 'SELL_SIGNAL'):
                    trades.append({
                        'symbol': t.get('symbol', ''),
                        'side': t.get('action', '').replace('_SIGNAL', '').replace('_FAILED', ''),
                        'qty': t.get('qty', 0),
                        'price': t.get('price', 0),
                        'time': t.get('timestamp', ''),
                        'mode': t.get('mode', 'live'),
                    })
            source = 'algo_log'
        if not trades:
            return jsonify({'error': 'No trades found to analyze. Place some trades or enter manually.'}), 400
        summary = f"Trade Journal Analysis — {len(trades)} trades from {source}\n\n"
        for i, t in enumerate(trades[:30], 1):
            summary += f"{i}. {t.get('time','')[:10]} | {t.get('symbol','')} | {t.get('side','')} {t.get('qty',0)} @ {t.get('price',0)}\n"
        prompt = f"""You are a professional trading psychologist and journal analyst. Analyze these trades and provide:
1. MISTAKES: What mistakes did the trader make?
2. IMPROVEMENTS: What can be improved?
3. PATTERNS: Any recurring patterns (good or bad)?
4. SUGGESTIONS: Specific actionable advice for better trading.

Trades:
{summary}

Focus on actual trading mistakes, risk management, and discipline. Be honest but constructive."""
        ai_assistant.set_trading_context(_build_ai_context())
        analysis = ai_assistant.chat_query(prompt)
        return jsonify({'success': True, 'analysis': analysis, 'trade_count': len(trades), 'source': source, 'trades': trades[:30]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai/chat', methods=['POST'])
def ai_chat():
    try:
        if not ai_assistant:
            return jsonify({'error': 'AI assistant not initialized'}), 500
        data = request.json
        message = data.get('message', '')
        response = ai_assistant.chat_query(message, _build_ai_context())
        return jsonify({'success': True, 'response': response, 'timestamp': str(datetime.now())})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai/analyze-portfolio', methods=['GET'])
def ai_analyze_portfolio():
    try:
        if not client or not session_data or not ai_assistant:
            return jsonify({'error': 'Login required'}), 401
        holdings = client.holdings()
        positions = client.positions()
        orders = client.order_report()
        ctx = _build_ai_context()
        ai_assistant.set_trading_context(ctx)
        analysis = ai_assistant.chat_query(
            f"Analyze this portfolio. Holdings: {holdings}. Positions: {positions}. Orders: {orders}. "
            f"Give a 3-4 line summary with recommendations."
        )
        return jsonify({'success': True, 'analysis': analysis})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==========================================
# RISK MANAGEMENT STATE
# ==========================================
risk_config = {
    "daily_loss_limit": 0,
    "max_trades_per_day": 0,
    "max_position_size": 0,
    "enabled": False
}
risk_state = {
    "trade_count": 0,
    "daily_pnl": 0,
    "day": None
}

def _check_risk():
    today = datetime.now().strftime("%Y-%m-%d")
    if risk_state["day"] != today:
        risk_state["day"] = today
        risk_state["trade_count"] = 0
        risk_state["daily_pnl"] = 0

def _enforce_risk(action="trade"):
    if not risk_config["enabled"]:
        return None
    _check_risk()
    if action == "trade":
        if risk_config["max_trades_per_day"] > 0 and risk_state["trade_count"] >= risk_config["max_trades_per_day"]:
            return "Daily trade limit reached (%s). Increase limit in Risk Settings or disable risk management." % risk_config["max_trades_per_day"]
        if risk_config["daily_loss_limit"] > 0 and risk_state["daily_pnl"] <= -risk_config["daily_loss_limit"]:
            return "Daily loss limit reached (-%s). Trading blocked until reset." % risk_config["daily_loss_limit"]
    return None

@app.route('/api/risk/config', methods=['GET', 'POST'])
def risk_config_endpoint():
    global risk_config
    if request.method == 'POST':
        data = request.json
        risk_config["daily_loss_limit"] = float(data.get("daily_loss_limit", 0))
        risk_config["max_trades_per_day"] = int(data.get("max_trades_per_day", 0))
        risk_config["max_position_size"] = float(data.get("max_position_size", 0))
        risk_config["enabled"] = data.get("enabled", False)
        advice = ""
        if ai_assistant and risk_config["enabled"]:
            ctx = _build_ai_context()
            ai_assistant.set_trading_context(ctx)
            advice = ai_assistant.chat_query(
                f"Risk management enabled: daily loss limit {risk_config['daily_loss_limit']}, "
                f"max trades {risk_config['max_trades_per_day']}, max position size {risk_config['max_position_size']}. "
                f"Give 1 line of advice on these limits."
            )
        return jsonify({"success": True, "config": risk_config, "ai_advice": advice})
    _check_risk()
    return jsonify({
        "success": True,
        "config": risk_config,
        "state": {"trade_count": risk_state["trade_count"], "daily_pnl": risk_state["daily_pnl"], "day": risk_state["day"]}
    })

@app.route('/api/risk/update-trade', methods=['POST'])
def risk_update_trade():
    if not client or not session_data:
        return jsonify({"error": "Not logged in"}), 401
    data = request.json
    pnl_change = float(data.get("pnl", 0))
    _check_risk()
    risk_state["trade_count"] += 1
    risk_state["daily_pnl"] += pnl_change
    blocked = _enforce_risk("trade")
    return jsonify({
        "success": True,
        "blocked": blocked is not None,
        "block_reason": blocked,
        "state": {"trade_count": risk_state["trade_count"], "daily_pnl": risk_state["daily_pnl"]}
    })

@app.route('/api/risk/reset', methods=['POST'])
def risk_reset():
    risk_state["trade_count"] = 0
    risk_state["daily_pnl"] = 0
    risk_state["day"] = datetime.now().strftime("%Y-%m-%d")
    return jsonify({"success": True, "message": "Risk counters reset"})

@app.route('/api/kill-switch', methods=['GET', 'POST'])
def kill_switch():
    global kill_switch_active
    if request.method == 'GET':
        return jsonify({'active': kill_switch_active})
    try:
        kill_switch_active = not kill_switch_active
        if not kill_switch_active:
            return jsonify({'success': True, 'active': False, 'message': 'Kill switch deactivated. Trading resumed.'})
        cancelled = 0
        errors = []
        if client and session_data and kill_switch_active:
            orders = client.order_report()
            open_orders = []
            orders_data = orders.get("data", orders) if isinstance(orders, dict) else orders
            if isinstance(orders_data, list):
                for o in orders_data:
                    status = (o.get("ordSt") or o.get("orderStatus") or "").lower()
                    if status in ("open", "open pending", "validation pending", "put order req received", "pending"):
                        open_orders.append(o.get("nOrdNo") or o.get("orderId"))
            for oid in open_orders:
                try:
                    client.cancel_order(order_id=oid, amo="", isVerify=False)
                    cancelled += 1
                except Exception as e:
                    errors.append(str(e))
        advice = ""
        if ai_assistant:
            ctx = _build_ai_context()
            ai_assistant.set_trading_context(ctx)
            advice = ai_assistant.chat_query(
                f"Kill switch activated. {cancelled} orders cancelled. "
                f"Give 1 line of advice on next steps."
            )
        return jsonify({
            "success": True,
            "active": True,
            "cancelled": cancelled,
            "errors": errors,
            "ai_advice": advice
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==========================================
# OPTION CHAIN
# ==========================================

@app.route('/api/option-chain', methods=['GET'])
def get_option_chain():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        symbol = request.args.get('symbol', 'NIFTY')
        expiry = request.args.get('expiry', '')
        mapped_seg = _map_exchange(request.args.get('exchange_segment', 'NFO'))
        ce_scrips = client.search_scrip(exchange_segment=mapped_seg, symbol=symbol, expiry=expiry, option_type='CE')
        pe_scrips = client.search_scrip(exchange_segment=mapped_seg, symbol=symbol, expiry=expiry, option_type='PE')
        def _list(data):
            d = data.get('data', data) if isinstance(data, dict) else data
            return d if isinstance(d, list) else [d] if d else []
        ce_list = _list(ce_scrips)
        pe_list = _list(pe_scrips)
        all_tokens = []
        ce_by_strike, pe_by_strike = {}, {}
        for s in ce_list:
            if not isinstance(s, dict): continue
            strike = float(s.get('dStrikePrice') or s.get('strike_price') or 0)
            tok = str(s.get('pSymbol', ''))
            if tok:
                all_tokens.append({"instrument_token": tok, "exchange_segment": mapped_seg})
                ce_by_strike.setdefault(strike, []).append(s)
        for s in pe_list:
            if not isinstance(s, dict): continue
            strike = float(s.get('dStrikePrice') or s.get('strike_price') or 0)
            tok = str(s.get('pSymbol', ''))
            if tok:
                all_tokens.append({"instrument_token": tok, "exchange_segment": mapped_seg})
                pe_by_strike.setdefault(strike, []).append(s)
        quotes_data = client.quotes(instrument_tokens=all_tokens, quote_type='all')
        quotes_map = {}
        if isinstance(quotes_data, dict):
            for tok_str, q in quotes_data.items():
                if isinstance(q, dict):
                    quotes_map[tok_str] = q
        all_strikes = sorted(set(list(ce_by_strike.keys()) + list(pe_by_strike.keys())))
        strikes = []
        total_ce_oi = total_pe_oi = 0
        for strike in all_strikes:
            entry = {"strike": strike}
            if strike in ce_by_strike:
                s = ce_by_strike[strike][0]
                tok = str(s.get('pSymbol', ''))
                q = quotes_map.get(tok, {})
                oi = float(q.get('oi', q.get('openInterest', 0)))
                total_ce_oi += oi
                entry['ce'] = dict(ltp=q.get('ltp', q.get('LTP', 0)), oi=oi, iv=q.get('iv', q.get('impliedVolatility', 0)), volume=q.get('volume', 0), chg=q.get('chg', q.get('change', 0)), bid=q.get('bid', q.get('bidPrice', 0)), ask=q.get('ask', q.get('askPrice', 0)))
            if strike in pe_by_strike:
                s = pe_by_strike[strike][0]
                tok = str(s.get('pSymbol', ''))
                q = quotes_map.get(tok, {})
                oi = float(q.get('oi', q.get('openInterest', 0)))
                total_pe_oi += oi
                entry['pe'] = dict(ltp=q.get('ltp', q.get('LTP', 0)), oi=oi, iv=q.get('iv', q.get('impliedVolatility', 0)), volume=q.get('volume', 0), chg=q.get('chg', q.get('change', 0)), bid=q.get('bid', q.get('bidPrice', 0)), ask=q.get('ask', q.get('askPrice', 0)))
            strikes.append(entry)
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0
        # Max Pain: find strike where total buyer loss is minimal
        max_pain = None
        if strikes:
            losses = {}
            for s in strikes:
                loss = 0
                sp = s['strike']
                for o in strikes:
                    if 'ce' in o:
                        if o['strike'] <= sp:
                            loss += o['ce']['oi'] * (sp - o['strike'])
                    if 'pe' in o:
                        if o['strike'] >= sp:
                            loss += o['pe']['oi'] * (o['strike'] - sp)
                losses[sp] = loss
            max_pain = min(losses, key=losses.get)
        analysis = ""
        if ai_assistant:
            ctx = _build_ai_context()
            ai_assistant.set_trading_context(ctx)
            analysis = ai_assistant.chat_query(f"Analyze this option chain: symbol={symbol}, PCR={pcr}, max_pain={max_pain}, {len(strikes)} strikes. Give 2 lines of insight.")
        return jsonify(dict(success=True, symbol=symbol, pcr=pcr, max_pain=max_pain, strikes=strikes, total_ce_oi=total_ce_oi, total_pe_oi=total_pe_oi, ai_analysis=analysis))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live-pnl', methods=['GET'])
def live_pnl():
    try:
        if not client or not session_data:
            return jsonify({'error': 'Not logged in'}), 401
        positions = client.positions()
        limits = client.limits()
        total_pnl = 0
        pos_list = []
        pdata = positions.get('data', positions) if isinstance(positions, dict) else positions
        if isinstance(pdata, list):
            for p in pdata:
                if isinstance(p, dict):
                    pnl = float(p.get('pnl', p.get('pandl', 0)))
                    total_pnl += pnl
                    pos_list.append(dict(sym=p.get('sym', p.get('trdSym', '--')), qty=int(p.get('qty', 0)), pnl=pnl, product=p.get('prod', '--')))
        lim_data = limits.get('data', limits) if isinstance(limits, dict) else limits
        avail_margin = 0
        if isinstance(lim_data, dict):
            avail_margin = float(lim_data.get('availableCash', lim_data.get('cash', lim_data.get('availableMargin', 0))))
        return jsonify(dict(success=True, total_pnl=round(total_pnl, 2), positions=pos_list, available_margin=avail_margin))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/historical', methods=['GET'])
def get_historical():
    try:
        import yfinance as yf
        symbol = request.args.get('symbol', 'SBIN.NS')
        interval = request.args.get('interval', '1d')
        period = request.args.get('period', '1mo')
        symbol = symbol.upper().replace('-EQ', '')
        if not symbol.endswith('.NS') and not symbol.endswith('.BO'):
            symbol = symbol + '.NS'
        df = yf.download(tickers=symbol, period=period, interval=interval, progress=False)
        if df.empty:
            return jsonify({'success': False, 'error': 'No data for ' + symbol}), 404
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        records = []
        for _, row in df.iterrows():
            ts = row['Date' if 'Date' in df.columns else 'Datetime']
            if hasattr(ts, 'isoformat'):
                ts = ts.isoformat()
            records.append({
                'timestamp': str(ts),
                'open': float(row.get('Open', 0)),
                'high': float(row.get('High', 0)),
                'low': float(row.get('Low', 0)),
                'close': float(row.get('Close', 0)),
                'volume': int(row.get('Volume', 0))
            })
        return jsonify({'success': True, 'symbol': symbol, 'interval': interval, 'data': records})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/telegram-config', methods=['GET'])
def telegram_config():
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
    return jsonify({
        'success': True,
        'configured': bool(token),
        'token_prefix': token[:10] + '...' if token else '',
        'chat_id': chat_id,
    })

@app.route('/api/send-telegram', methods=['POST'])
def send_telegram():
    try:
        data = request.json
        bot_token = data.get('bot_token', '') or os.getenv('TELEGRAM_BOT_TOKEN', '')
        chat_id = data.get('chat_id', '') or os.getenv('TELEGRAM_CHAT_ID', '')
        message = data.get('message', '')
        if not bot_token or not chat_id or not message:
            return jsonify({'success': False, 'error': 'bot_token, chat_id, and message required'}), 400
        import requests
        r = requests.post(f'https://api.telegram.org/bot{bot_token}/sendMessage', json=dict(chat_id=chat_id, text=message, parse_mode='Markdown'), timeout=10)
        if r.status_code == 200:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': r.text}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    try:
        global client, session_data, live_data_handler, ai_assistant
        if client:
            client.logout()
        if live_data_handler:
            live_data_handler.disconnect()
        if ai_assistant:
            ai_assistant.clear_conversation_history()
        client = None
        session_data = None
        live_data_handler = None
        ai_assistant = None
        return jsonify({'success': True, 'message': 'Logged out'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==========================================
# ALGO TRADING
# ==========================================

@app.route('/api/algo/templates', methods=['GET'])
def algo_templates():
    from algo_engine import STRATEGY_TEMPLATES
    return jsonify({'success': True, 'templates': STRATEGY_TEMPLATES})

@app.route('/api/algo/strategies', methods=['GET', 'POST'])
def algo_strategies():
    if request.method == 'GET':
        return jsonify({'success': True, 'strategies': get_strategies()})
    data = request.json
    strats = get_strategies()
    if data.get('id') is None:
        data['id'] = int(time.time() * 1000)
        data['enabled'] = False
        data['created'] = datetime.now().isoformat()
        strats.append(data)
    else:
        for i, s in enumerate(strats):
            if s.get('id') == data['id']:
                strats[i] = data
                break
    save_strategies(strats)
    return jsonify({'success': True, 'strategy': data})

@app.route('/api/algo/strategies/<int:sid>', methods=['DELETE'])
def algo_delete_strategy(sid):
    strats = get_strategies()
    strats = [s for s in strats if s.get('id') != sid]
    save_strategies(strats)
    return jsonify({'success': True})

@app.route('/api/algo/strategies/<int:sid>/toggle', methods=['POST'])
def algo_toggle_strategy(sid):
    strats = get_strategies()
    for s in strats:
        if s.get('id') == sid:
            s['enabled'] = not s.get('enabled', False)
            save_strategies(strats)
            return jsonify({'success': True, 'enabled': s['enabled']})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/algo/status', methods=['GET'])
def algo_status():
    return jsonify({
        'running': engine_state['running'],
        'active_strategies': sum(1 for s in get_strategies() if s.get('enabled')),
        'total_strategies': len(get_strategies()),
    })

@app.route('/api/algo/start', methods=['POST'])
def algo_start():
    started = start_engine(lambda: client, lambda: kill_switch_active)
    if started:
        add_trade_entry({'action': 'ENGINE_STARTED'})
        return jsonify({'success': True, 'status': 'started'})
    return jsonify({'success': True, 'status': 'already_running'})

@app.route('/api/algo/stop', methods=['POST'])
def algo_stop():
    stop_engine()
    add_trade_entry({'action': 'ENGINE_STOPPED'})
    return jsonify({'success': True, 'status': 'stopped'})

@app.route('/api/algo/logs', methods=['GET'])
def algo_logs():
    return jsonify({'success': True, 'logs': get_trade_log()[-100:]})

@app.route('/api/algo/clear-logs', methods=['POST'])
def algo_clear_logs():
    from algo_engine import save_trade_log
    save_trade_log([])
    return jsonify({'success': True})

@app.route('/api/algo/backtest', methods=['POST'])
def algo_backtest():
    data = request.json
    strategy = data.get('strategy', {})
    symbol = data.get('symbol', '')
    interval = data.get('interval', '1h')
    period = data.get('period', '60d')
    if not strategy or not symbol:
        return jsonify({'error': 'Strategy and symbol required'}), 400
    result = run_backtest(strategy, symbol, interval, period)
    return jsonify({'success': True, 'result': result})

# ============= NSE Option Chain (Public API) =============
NSE_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "accept-language": "en,gu;q=0.9,hi;q=0.8",
    "accept-encoding": "gzip, deflate",
}
_nse_session = None

def _get_nse_session():
    global _nse_session
    if _nse_session is None:
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        s.get("https://www.nseindia.com/option-chain", timeout=10)
        s.get("https://www.nseindia.com/api/underlying-information", timeout=10)
        _nse_session = s
    return _nse_session

def _nse_init():
    try:
        _get_nse_session()
        return True
    except:
        return False

@app.route('/api/nse/symbols', methods=['GET'])
def nse_symbols():
    try:
        s = _get_nse_session()
        r = s.get("https://www.nseindia.com/api/underlying-information", timeout=10)
        indices, stocks = [], []
        if r.status_code == 200:
            d = r.json()
            for idx in d.get("data", {}).get("IndexList", []):
                indices.append({"symbol": idx["symbol"], "name": idx.get("name", idx["symbol"])})
            for stk in d.get("data", {}).get("UnderlyingList", []):
                stocks.append({"symbol": stk["symbol"], "name": stk.get("name", stk["symbol"])})
        else:
            indices = [{"symbol": s, "name": s} for s in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]]
        return jsonify({"success": True, "indices": indices, "stocks": stocks})
    except Exception as e:
        indices = [{"symbol": s, "name": s} for s in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]]
        return jsonify({"success": True, "indices": indices, "stocks": []})

@app.route('/api/nse/expiry-dates', methods=['GET'])
def nse_expiry_dates():
    try:
        symbol = request.args.get('symbol', 'NIFTY')
        s = _get_nse_session()
        r = s.get(f"https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}", timeout=10)
        if r.status_code == 200:
            d = r.json()
            return jsonify({"success": True, "expiry_dates": d.get("expiryDates", [])})
        return jsonify({"success": False, "error": "NSE API error"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/nse/option-chain', methods=['GET'])
def nse_option_chain():
    try:
        symbol = request.args.get('symbol', 'NIFTY')
        mode = request.args.get('mode', 'Indices')
        expiry = request.args.get('expiry', '')
        s = _get_nse_session()

        # Get expiry if not provided
        if not expiry:
            r = s.get(f"https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}", timeout=10)
            if r.status_code == 200:
                expiries = r.json().get("expiryDates", [])
                expiry = expiries[0] if expiries else ''
            if not expiry:
                return jsonify({"success": False, "error": "No expiry dates"}), 404

        # Fetch option chain
        url = f"https://www.nseindia.com/api/option-chain-v3?type={mode}&symbol={symbol}&expiry={expiry}"
        r = s.get(url, timeout=10)
        if r.status_code != 200:
            return jsonify({"success": False, "error": f"NSE API returned {r.status_code}"}), 502

        data = r.json()
        records = data.get("records", {})
        underlying_value = records.get("underlyingValue", 0)
        timestamp = records.get("timestamp", "")
        data_rows = records.get("data", [])

        strikes = []
        total_ce_oi = total_pe_oi = 0

        for item in data_rows:
            strike = item.get("strikePrice")
            ce = item.get("CE")
            pe = item.get("PE")
            entry = {"strike": strike}

            if ce:
                oi = float(ce.get("openInterest", 0) or 0)
                total_ce_oi += oi
                entry["ce"] = {
                    "ltp": ce.get("lastPrice"),
                    "oi": oi,
                    "chg_oi": ce.get("changeinOpenInterest"),
                    "pct_chg_oi": ce.get("pchangeinOpenInterest"),
                    "iv": ce.get("impliedVolatility"),
                    "volume": ce.get("totalTradedVolume"),
                    "change": ce.get("change"),
                    "bid": ce.get("bidprice", ce.get("buyPrice1")),
                    "ask": ce.get("askPrice", ce.get("sellPrice1")),
                    "bid_qty": ce.get("bidquantity", ce.get("buyQuantity1")),
                    "ask_qty": ce.get("askquantity", ce.get("sellQuantity1")),
                }
            if pe:
                oi = float(pe.get("openInterest", 0) or 0)
                total_pe_oi += oi
                entry["pe"] = {
                    "ltp": pe.get("lastPrice"),
                    "oi": oi,
                    "chg_oi": pe.get("changeinOpenInterest"),
                    "pct_chg_oi": pe.get("pchangeinOpenInterest"),
                    "iv": pe.get("impliedVolatility"),
                    "volume": pe.get("totalTradedVolume"),
                    "change": pe.get("change"),
                    "bid": pe.get("bidprice", pe.get("buyPrice1")),
                    "ask": pe.get("askPrice", pe.get("sellPrice1")),
                    "bid_qty": pe.get("bidquantity", pe.get("buyQuantity1")),
                    "ask_qty": pe.get("askquantity", pe.get("sellQuantity1")),
                }
            strikes.append(entry)

        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0

        # Max Pain
        max_pain = None
        if strikes:
            losses = {}
            for s_ in strikes:
                sp = s_["strike"]
                loss = 0
                for o_ in strikes:
                    if "ce" in o_ and o_["strike"] <= sp:
                        loss += o_["ce"]["oi"] * (sp - o_["strike"])
                    if "pe" in o_ and o_["strike"] >= sp:
                        loss += o_["pe"]["oi"] * (o_["strike"] - sp)
                losses[sp] = loss
            max_pain = min(losses, key=losses.get)

        ai_analysis = ""
        if ai_assistant and underlying_value and strikes:
            try:
                uv = float(underlying_value)
                sorted_ce = [s for s in strikes if "ce" in s and s["ce"].get("oi", 0) > 0]
                sorted_pe = [s for s in strikes if "pe" in s and s["pe"].get("oi", 0) > 0]
                otm_calls = [s for s in sorted_ce if s["strike"] >= uv][:8]
                otm_puts = [s for s in sorted_pe if s["strike"] <= uv][-8:]
                total_ce = total_ce_oi or 1
                total_pe = total_pe_oi or 1
                info = f"Symbol:{symbol} Underlying:{uv} PCR:{pcr} MaxPain:{max_pain} Expiry:{expiry} TotalCE_OI:{total_ce_oi} TotalPE_OI:{total_pe_oi}\n\n"

                info += "--- OTM CALLS (above UV) ---\n"
                info += "Strike|LTP|OI|OI_Chg|OI_Conc%|IV|Vol|Bid|Ask|Chg\n"
                for s in otm_calls:
                    c = s["ce"]
                    oi = c.get('oi', 0) or 0
                    conc = round(oi / total_ce * 100, 1) if total_ce else 0
                    info += f"{s['strike']}|{c.get('ltp','?')}|{oi}|{c.get('chg_oi',0)}|{conc}%|{c.get('iv','?')}|{c.get('volume',0)}|{c.get('bid','?')}|{c.get('ask','?')}|{c.get('change',0)}\n"

                info += "\n--- OTM PUTS (below UV) ---\n"
                info += "Strike|LTP|OI|OI_Chg|OI_Conc%|IV|Vol|Bid|Ask|Chg\n"
                for s in otm_puts:
                    p = s["pe"]
                    oi = p.get('oi', 0) or 0
                    conc = round(oi / total_pe * 100, 1) if total_pe else 0
                    info += f"{s['strike']}|{p.get('ltp','?')}|{oi}|{p.get('chg_oi',0)}|{conc}%|{p.get('iv','?')}|{p.get('volume',0)}|{p.get('bid','?')}|{p.get('ask','?')}|{p.get('change',0)}\n"

                # IV skew (difference between highest OTM call IV and nearest put IV)
                if otm_calls and otm_puts:
                    far_call_iv = otm_calls[-1]["ce"].get("iv", 0) or 0
                    near_put_iv = otm_puts[-1]["pe"].get("iv", 0) or 0
                    info += f"\nIV_Skew:{round(far_call_iv - near_put_iv, 2)} (FarCallIV:{far_call_iv} NearPutIV:{near_put_iv})"

                ai_assistant.clear_conversation_history()
                ai_result = ai_assistant.chat_query(
                    "You are an expert options analyst. Analyze this NSE option chain data and provide:\n"
                    "1. Market Bias (Bullish/Bearish/Neutral with confidence %)\n"
                    "2. PCR & Max Pain Interpretation\n"
                    "3. OI Concentration Analysis (where is smart money positioned?)\n"
                    "4. IV Analysis (skew, expensive/cheap sides)\n"
                    "5. Key Support & Resistance Levels (from OI clusters)\n"
                    "6. Volume & Activity Analysis\n"
                    "7. Expected Expiry Range\n"
                    "8. Recommended Option Strategies (2-3 strategies with rationale)\n"
                    "9. Risk Warnings\n"
                    "Data:\n" + info
                )
                if ai_result:
                    ai_analysis = ai_result
            except Exception as e:
                app.logger.error(f"NSE AI analysis error: {e}")
                ai_analysis = f"AI temporarily unavailable: {str(e)[:100]}"

        return jsonify({
            "success": True,
            "symbol": symbol,
            "mode": mode,
            "expiry": expiry,
            "underlying_value": underlying_value,
            "timestamp": timestamp,
            "pcr": pcr,
            "max_pain": max_pain,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "strikes": strikes,
            "ai_analysis": ai_analysis
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=8080, allow_unsafe_werkzeug=True)
