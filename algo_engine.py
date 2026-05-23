import yfinance as yf
import pandas as pd
import numpy as np
import json
import time
import threading
import os
from datetime import datetime, timedelta
from collections import defaultdict

STRATEGIES_FILE = 'algo_strategies.json'
TRADE_LOG_FILE = 'algo_trade_log.json'

engine_state = {
    'running': False, 'thread': None, 'kill_switch_getter': None, 'positions': {},
    'sim_running': False, 'sim_thread': None,
    'sim_positions': {},  # sid -> {side, entry_price, entry_time, symbol, qty}
    'sim_stats': {'total_trades': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'total_pnl_pct': 0.0, 'equity_curve': []},
}

# Try to use C++ accelerated module
try:
    from algo_core import (
        compute_all_params as _cpp_compute_all_params,
        run_backtest as _cpp_run_backtest,
        evaluate_condition as _cpp_evaluate_condition,
        compute_max_pain as _cpp_max_pain,
        parallel_backtest as _py_parallel_backtest,
        parallel_evaluate_strategies as _py_parallel_strategies,
        is_cpp as _is_cpp,
    )
    USE_CPP = _is_cpp()
except ImportError:
    USE_CPP = False

def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default

def _save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def get_strategies():
    return _load_json(STRATEGIES_FILE, [])

def save_strategies(strats):
    _save_json(STRATEGIES_FILE, strats)

def get_trade_log():
    return _load_json(TRADE_LOG_FILE, [])

def save_trade_log(log):
    _save_json(TRADE_LOG_FILE, log)

def add_trade_entry(entry):
    log = get_trade_log()
    entry['timestamp'] = datetime.now().isoformat()
    log.append(entry)
    if len(log) > 500:
        log = log[-500:]
    save_trade_log(log)

# Technical indicators
def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_macd(series, fast=12, slow=26, signal=9):
    ema_fast = compute_ema(series, fast)
    ema_slow = compute_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def compute_bollinger(series, period=20, std_dev=2):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    return upper, sma, lower

def compute_supertrend(high, low, close, period=10, multiplier=3):
    hl = (high + low) / 2
    atr = (high - low).rolling(window=period).mean()
    upper_band = hl + multiplier * atr
    lower_band = hl - multiplier * atr
    supertrend = pd.Series(index=close.index, dtype=float)
    direction = pd.Series(index=close.index, dtype=int)
    for i in range(period, len(close)):
        if i == period:
            supertrend.iloc[i] = upper_band.iloc[i]
            direction.iloc[i] = 1
        elif close.iloc[i-1] <= supertrend.iloc[i-1]:
            if close.iloc[i] > upper_band.iloc[i]:
                direction.iloc[i] = 1
                supertrend.iloc[i] = lower_band.iloc[i]
            else:
                direction.iloc[i] = -1
                supertrend.iloc[i] = min(upper_band.iloc[i], supertrend.iloc[i-1])
        else:
            if close.iloc[i] < lower_band.iloc[i]:
                direction.iloc[i] = -1
                supertrend.iloc[i] = upper_band.iloc[i]
            else:
                direction.iloc[i] = 1
                supertrend.iloc[i] = max(lower_band.iloc[i], supertrend.iloc[i-1])
    return supertrend, direction

def compute_vwap(high, low, close, volume):
    tp = (high + low + close) / 3
    return (tp * volume).cumsum() / volume.cumsum()

# Angel One data source
ANGEL_TOKEN_MAP = {
    "ADANIENT": "25", "ADANIGAS": "6066", "ADANIGREEN": "3563",
    "ADANIPORTS": "15083", "ADANIPOWER": "17388", "ADANITRANS": "10217",
    "AXISBANK": "5900", "BAJAJFINSV": "16675", "BPCL": "526",
    "CANBK": "10794", "DLF": "14732", "HAL": "2303",
    "HCLTECH": "7229", "HDFCBANK": "1333", "HDFCLIFE": "467",
    "HINDUNILVR": "1394", "ICICIBANK": "4963", "INDUSINDBK": "5258",
    "INFY": "1594", "ITC": "1660", "KOTAKBANK": "1922",
    "LT": "11483", "MARUTI": "10999", "NCC": "2319",
    "NTPC": "11630", "ONGC": "2475", "RELIANCE": "2885",
    "SBIN": "3045", "TATACOMM": "3721", "TATAPOWER": "3426",
    "TATASTEEL": "3499", "TCS": "11536", "WIPRO": "3787"
}
ANGEL_INTERVAL_MAP = {
    '1m': 'ONE_MINUTE', '3m': 'THREE_MINUTE', '5m': 'FIVE_MINUTE', '10m': 'TEN_MINUTE',
    '15m': 'FIFTEEN_MINUTE', '30m': 'THIRTY_MINUTE', '1h': 'ONE_HOUR', '1d': 'ONE_DAY'
}
_angel_client = None

def _get_angel_client():
    global _angel_client
    if _angel_client is not None:
        return _angel_client
    angel_env = os.path.join(os.path.dirname(__file__), '.env.angel')
    if os.path.exists(angel_env):
        from dotenv import load_dotenv
        load_dotenv(angel_env, override=True)
    api_key = os.getenv('ANGEL_API_KEY')
    client_id = os.getenv('ANGEL_CLIENT_ID')
    password = os.getenv('ANGEL_PASSWORD')
    totp_secret = os.getenv('ANGEL_TOTP_SECRET')
    if not all([api_key, client_id, password, totp_secret]):
        return None
    try:
        from SmartApi import SmartConnect
        import pyotp
        totp = pyotp.TOTP(totp_secret).now()
        client = SmartConnect(api_key=api_key)
        data = client.generateSession(client_id, password, totp)
        if not data.get('status'):
            return None
        _angel_client = client
        return _angel_client
    except Exception:
        _angel_client = None
        return None

ANGEL_MAX_DAYS = {'1m': 30, '3m': 60, '5m': 100, '10m': 100, '15m': 200, '30m': 200, '1h': 400, '1d': 2000}

def _period_to_days(period):
    m = {'1d': 1, '2d': 2, '5d': 7, '1mo': 30, '2mo': 60, '3mo': 90, '6mo': 180, '1y': 365, '2y': 730}
    return m.get(period, 30)

def fetch_data_angel(symbol, interval='5m', period='5d', fromdate=None, todate=None, exchange='NSE'):
    base = symbol.upper().replace('-EQ', '').replace('.NS', '').replace('.BO', '')
    if base in ('^NSEI', '^NSEBANK', '^BSESN') or base not in ANGEL_TOKEN_MAP:
        return None
    client = _get_angel_client()
    if not client:
        return None
    angel_interval = ANGEL_INTERVAL_MAP.get(interval)
    if not angel_interval:
        return None
    try:
        token = ANGEL_TOKEN_MAP[base]
        max_days = ANGEL_MAX_DAYS.get(interval, 100)
        if fromdate and todate:
            from_dt = datetime.strptime(fromdate, '%Y-%m-%d') if isinstance(fromdate, str) and ' ' not in fromdate else datetime.strptime(fromdate.split(' ')[0], '%Y-%m-%d')
            to_dt = datetime.strptime(todate, '%Y-%m-%d') if isinstance(todate, str) and ' ' not in todate else datetime.strptime(todate.split(' ')[0], '%Y-%m-%d')
            if (to_dt - from_dt).days > max_days:
                from_dt = to_dt - timedelta(days=max_days)
        else:
            days = min(_period_to_days(period), max_days)
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=days)
        params = {
            "exchange": exchange.upper(),
            "symboltoken": token,
            "interval": angel_interval,
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M")
        }
        candles = client.getCandleData(params)
        if not candles.get("status") or not candles.get("data"):
            return None
        records = []
        for c in candles["data"]:
            records.append({
                'Open': float(c[1]), 'High': float(c[2]), 'Low': float(c[3]),
                'Close': float(c[4]), 'Volume': int(float(c[5])),
                'timestamp': str(pd.Timestamp(c[0]))
            })
        df = pd.DataFrame(records)
        df.set_index('timestamp', inplace=True)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        return None

def fetch_data(symbol, interval='5m', period='5d', fromdate=None, todate=None, exchange='NSE'):
    df = fetch_data_angel(symbol, interval, period, fromdate, todate, exchange)
    if df is not None and not df.empty:
        return df
    symbol = symbol.upper().replace('-EQ', '')
    if not symbol.endswith('.NS') and not symbol.endswith('.BO'):
        symbol = symbol + '.NS'
    yf_period = period
    if fromdate and todate:
        yf_period = 'max'
    df = yf.download(tickers=symbol, period=yf_period, interval=interval, progress=False)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def evaluate_condition(row, prev_row, condition, params):
    """Evaluate a single strategy condition."""
    tag = condition.get('tag', '')
    val = params.get('value', 0)
    period = int(params.get('period', 14))

    if not hasattr(row, 'shape') or row is None:
        return False

    close = float(row['Close']) if hasattr(row, 'get') or isinstance(row, pd.Series) else 0
    prev_close = float(prev_row['Close']) if prev_row is not None else close

    if tag == 'price_above_ema':
        ema = float(params.get('_ema', params.get('_ema_9', close)))
        return close > ema
    elif tag == 'price_below_ema':
        ema = float(params.get('_ema', params.get('_ema_9', close)))
        return close < ema
    elif tag == 'ema_cross_above':
        fast = float(params.get('_ema_fast', close))
        slow = float(params.get('_ema_slow', close))
        prev_fast = float(params.get('_ema_fast_prev', prev_close))
        prev_slow = float(params.get('_ema_slow_prev', prev_close))
        return prev_fast <= prev_slow and fast > slow
    elif tag == 'ema_cross_below':
        fast = float(params.get('_ema_fast', close))
        slow = float(params.get('_ema_slow', close))
        prev_fast = float(params.get('_ema_fast_prev', prev_close))
        prev_slow = float(params.get('_ema_slow_prev', prev_close))
        return prev_fast >= prev_slow and fast < slow
    elif tag == 'rsi_above':
        rsi = float(params.get('_rsi', params.get('_rsi_14', 50)))
        return rsi > val
    elif tag == 'rsi_below':
        rsi = float(params.get('_rsi', params.get('_rsi_14', 50)))
        return rsi < val
    elif tag == 'price_above_bb_upper':
        bb_u = float(params.get('_bb_upper', close))
        return close > bb_u
    elif tag == 'price_below_bb_lower':
        bb_l = float(params.get('_bb_lower', close))
        return close < bb_l
    elif tag == 'macd_cross_above':
        macd = float(params.get('_macd', 0))
        sig = float(params.get('_macd_signal', 0))
        prev_macd = float(params.get('_macd_prev', 0))
        prev_sig = float(params.get('_macd_signal_prev', 0))
        return prev_macd <= prev_sig and macd > sig
    elif tag == 'macd_cross_below':
        macd = float(params.get('_macd', 0))
        sig = float(params.get('_macd_signal', 0))
        prev_macd = float(params.get('_macd_prev', 0))
        prev_sig = float(params.get('_macd_signal_prev', 0))
        return prev_macd >= prev_sig and macd < sig
    elif tag == 'volume_spike':
        vol = float(params.get('_volume', row.get('Volume', 0)) if hasattr(row, 'get') else params.get('_volume', 0))
        avg_vol = float(params.get('_avg_volume', params.get('_avg_volume_20', 1)))
        return avg_vol > 0 and vol / avg_vol > val
    elif tag == 'price_above_vwap':
        vwap = float(params.get('_vwap', close))
        return close > vwap
    elif tag == 'price_below_vwap':
        vwap = float(params.get('_vwap', close))
        return close < vwap
    elif tag == 'supertrend_buy':
        direction = int(params.get('_st_direction', 0))
        prev_dir = int(params.get('_st_direction_prev', 0))
        return prev_dir <= 0 and direction > 0
    elif tag == 'supertrend_sell':
        direction = int(params.get('_st_direction', 0))
        prev_dir = int(params.get('_st_direction_prev', 0))
        return prev_dir >= 0 and direction < 0
    return False

def compute_strategy_params(df):
    """Pre-compute all indicators (C++ accelerated if available)."""
    if USE_CPP:
        return _cpp_compute_all_params(df)
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']

    params = {}
    params['_ema_9'] = compute_ema(close, 9)
    params['_ema_20'] = compute_ema(close, 20)
    params['_ema_50'] = compute_ema(close, 50)
    params['_ema_fast'] = compute_ema(close, 12)
    params['_ema_slow'] = compute_ema(close, 26)
    params['_ema_200'] = compute_ema(close, 200)
    params['_rsi_14'] = compute_rsi(close, 14)
    macd, sig, hist = compute_macd(close)
    params['_macd'] = macd
    params['_macd_signal'] = sig
    params['_macd_hist'] = hist
    bb_u, bb_m, bb_l = compute_bollinger(close)
    params['_bb_upper'] = bb_u
    params['_bb_mid'] = bb_m
    params['_bb_lower'] = bb_l
    params['_vwap'] = compute_vwap(high, low, close, volume)
    st, sd = compute_supertrend(high, low, close)
    params['_supertrend'] = st
    params['_st_direction'] = sd
    params['_avg_volume_20'] = volume.rolling(window=20).mean()
    return params

def run_backtest(strategy, symbol, interval='1h', period='60d', fromdate=None, todate=None, exchange='NSE'):
    df = fetch_data(symbol, interval, period, fromdate, todate, exchange)
    if df is None or len(df) < 100:
        return {'error': 'Insufficient data', 'bars': 0}

    # Backward compat: old buy/sell → entry_long / exit_long
    entry_long = strategy.get('entry_long_conds') or strategy.get('buy_conditions', [])
    exit_long = strategy.get('exit_long_conds') or strategy.get('sell_conditions', [])
    entry_short = strategy.get('entry_short_conds', [])
    exit_short = strategy.get('exit_short_conds', [])

    sl_pct = float(strategy.get('stop_loss', 0))
    target_pct = float(strategy.get('target', 0))

    if USE_CPP and not entry_short and not exit_short:
        try:
            cpp_buy = [(c.get('tag',''), c.get('value',0), c.get('period',14), c.get('fast_period',12), c.get('slow_period',26)) for c in entry_long]
            cpp_sell = [(c.get('tag',''), c.get('value',0), c.get('period',14), c.get('fast_period',12), c.get('slow_period',26)) for c in exit_long]
            result = _cpp_run_backtest(
                np.asarray(df['Close'], dtype=np.float64),
                np.asarray(df['High'], dtype=np.float64),
                np.asarray(df['Low'], dtype=np.float64),
                np.asarray(df['Volume'], dtype=np.float64),
                cpp_buy, cpp_sell, sl_pct, target_pct, symbol, interval, period,
            )
            if 'error' in result:
                return result
            return {
                'symbol': result.get('symbol', symbol),
                'interval': result.get('interval', interval),
                'period': result.get('period', period),
                'total_bars': result.get('total_bars', len(df)),
                'trades': result.get('trades', []),
                'num_trades': result.get('num_trades', 0),
                'wins': result.get('wins', 0),
                'losses': result.get('losses', 0),
                'win_rate': result.get('win_rate', 0),
                'total_pnl_pct': result.get('total_pnl_pct', 0),
                'avg_pnl_pct': result.get('avg_pnl_pct', 0),
                'signals': result.get('signals', [])[:20],
            }
        except Exception as e:
            pass

    ind = compute_strategy_params(df)
    signals = []
    entry_price = None
    entry_bar = None
    trades = []
    position = 0  # 0=none, 1=long, -1=short

    for i in range(60, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        cond_params = {k: v.iloc[i] for k, v in ind.items() if hasattr(v, 'iloc')}
        cond_params_prev = {k: v.iloc[i-1] for k, v in ind.items() if hasattr(v, 'iloc')}

        def eval_c(c):
            return evaluate_condition(row, prev, c, {**cond_params, 'value': c.get('value', 0), 'period': c.get('period', 14)})

        long_entry = all(eval_c(c) for c in entry_long) if entry_long else False
        long_exit = all(eval_c(c) for c in exit_long) if exit_long else False
        short_entry = all(eval_c(c) for c in entry_short) if entry_short else False
        short_exit = all(eval_c(c) for c in exit_short) if exit_short else False

        close_price = float(row['Close'])

        if position == 0:
            if long_entry:
                entry_price = close_price
                entry_bar = i
                position = 1
                signals.append({'bar': i, 'time': str(row.name), 'action': 'BUY', 'price': entry_price})
            elif short_entry:
                entry_price = close_price
                entry_bar = i
                position = -1
                signals.append({'bar': i, 'time': str(row.name), 'action': 'SELL_SHORT', 'price': entry_price})
        elif position == 1:
            if long_exit:
                trades.append({'entry_bar': entry_bar, 'exit_bar': i, 'entry_price': entry_price, 'exit_price': close_price, 'pnl_pct': (close_price - entry_price) / entry_price * 100, 'reason': 'signal', 'side': 'long'})
                entry_price = None; position = 0
            elif sl_pct > 0 and close_price <= entry_price * (1 - sl_pct / 100):
                trades.append({'entry_bar': entry_bar, 'exit_bar': i, 'entry_price': entry_price, 'exit_price': close_price, 'pnl_pct': -sl_pct, 'reason': 'stop_loss', 'side': 'long'})
                entry_price = None; position = 0
            elif target_pct > 0 and close_price >= entry_price * (1 + target_pct / 100):
                trades.append({'entry_bar': entry_bar, 'exit_bar': i, 'entry_price': entry_price, 'exit_price': close_price, 'pnl_pct': target_pct, 'reason': 'target', 'side': 'long'})
                entry_price = None; position = 0
        elif position == -1:
            if short_exit:
                trades.append({'entry_bar': entry_bar, 'exit_bar': i, 'entry_price': entry_price, 'exit_price': close_price, 'pnl_pct': (entry_price - close_price) / entry_price * 100, 'reason': 'signal', 'side': 'short'})
                entry_price = None; position = 0
            elif sl_pct > 0 and close_price >= entry_price * (1 + sl_pct / 100):
                trades.append({'entry_bar': entry_bar, 'exit_bar': i, 'entry_price': entry_price, 'exit_price': close_price, 'pnl_pct': -sl_pct, 'reason': 'stop_loss', 'side': 'short'})
                entry_price = None; position = 0
            elif target_pct > 0 and close_price <= entry_price * (1 - target_pct / 100):
                trades.append({'entry_bar': entry_bar, 'exit_bar': i, 'entry_price': entry_price, 'exit_price': close_price, 'pnl_pct': target_pct, 'reason': 'target', 'side': 'short'})
                entry_price = None; position = 0

    if position != 0:
        last = float(df.iloc[-1]['Close'])
        pnl = (last - entry_price) / entry_price * 100 if position == 1 else (entry_price - last) / entry_price * 100
        trades.append({'entry_bar': entry_bar, 'exit_bar': len(df)-1, 'entry_price': entry_price, 'exit_price': last, 'pnl_pct': pnl, 'reason': 'open', 'side': 'long' if position == 1 else 'short'})

    total_pnl = sum(t['pnl_pct'] for t in trades)
    wins = [t for t in trades if t['pnl_pct'] > 0]
    total_bars = len(df)

    return {
        'symbol': symbol,
        'interval': interval,
        'period': period,
        'total_bars': total_bars,
        'num_trades': len(trades),
        'wins': len(wins),
        'losses': len(trades) - len(wins),
        'win_rate': round(len(wins) / len(trades) * 100, 2) if trades else 0,
        'total_pnl_pct': round(total_pnl, 2),
        'avg_pnl_pct': round(total_pnl / len(trades), 2) if trades else 0,
        'signals': signals[:20],
        'trades': trades,
    }

def _is_kill_switch_active():
    try:
        if engine_state.get('kill_switch_getter'):
            return engine_state['kill_switch_getter']()
    except:
        pass
    return False

def _get_strategy_id(strategy):
    return str(strategy.get('id', strategy.get('name', hash(str(strategy)))))

def _eval_conds(row, prev, conditions, cond_params, cond_params_prev):
    if not conditions:
        return False
    return all(evaluate_condition(row, prev, c, {**cond_params, **{'value': c.get('value', 0), 'period': c.get('period', 14)}}) for c in conditions)

def run_strategy_cycle(strategy, client):
    symbol = strategy.get('symbol', '')
    sid = _get_strategy_id(strategy)
    qty = int(strategy.get('quantity', 1))
    sl_pct = float(strategy.get('stop_loss', 0))
    target_pct = float(strategy.get('target', 0))
    product = strategy.get('product', 'MIS')
    interval = strategy.get('interval', '5m')

    # Backward compat: old buy/sell → entry_long / exit_long
    entry_long = strategy.get('entry_long_conds') or strategy.get('buy_conditions', [])
    exit_long = strategy.get('exit_long_conds') or strategy.get('sell_conditions', [])
    entry_short = strategy.get('entry_short_conds', [])
    exit_short = strategy.get('exit_short_conds', [])

    df = fetch_data(symbol, interval=interval, period='5d')
    if df is None or len(df) < 60:
        return

    ind = compute_strategy_params(df)
    row = df.iloc[-1]
    prev = df.iloc[-2]
    cond_params = {k: v.iloc[-1] for k, v in ind.items() if hasattr(v, 'iloc')}
    cond_params_prev = {k: v.iloc[-2] for k, v in ind.items() if hasattr(v, 'iloc')}

    price = float(row['Close'])
    kill_switch = _is_kill_switch_active()
    can_trade = client and not kill_switch
    pos = engine_state['positions'].get(sid)

    def place(side):
        if not can_trade:
            add_trade_entry({'symbol': symbol, 'action': f'{side}_SIGNAL', 'price': price, 'qty': qty, 'mode': 'paper', 'sl': sl_pct, 'target': target_pct, 'interval': interval, 'strategy': strategy.get('name','')})
            return True
        try:
            tx = 'B' if side == 'BUY' else 'S'
            res = client.place_order(
                exchange_segment='nse_cm', product=product, price='0', order_type='MKT',
                quantity=str(qty), validity='DAY', trading_symbol=symbol.replace('.NS', '-EQ'),
                transaction_type=tx, amo='NO', disclosed_quantity='0',
                market_protection='0', pf='N', trigger_price='0',
            )
            add_trade_entry({'symbol': symbol, 'action': f'{side}_LIVE', 'price': price, 'qty': qty, 'sl': sl_pct, 'target': target_pct, 'interval': interval, 'response': str(res)[:200], 'strategy': strategy.get('name','')})
            return True
        except Exception as e:
            add_trade_entry({'symbol': symbol, 'action': f'{side}_FAILED', 'price': price, 'error': str(e), 'strategy': strategy.get('name','')})
            return False

    entry_buy = _eval_conds(row, prev, entry_long, cond_params, cond_params_prev)
    entry_sell = _eval_conds(row, prev, entry_short, cond_params, cond_params_prev)
    exit_buy = _eval_conds(row, prev, exit_short, cond_params, cond_params_prev)
    exit_sell = _eval_conds(row, prev, exit_long, cond_params, cond_params_prev)

    if pos is None:
        if entry_buy:
            if place('BUY'):
                engine_state['positions'][sid] = {'side': 'long', 'entry_price': price, 'entry_time': str(datetime.now())}
        elif entry_sell:
            if place('SELL'):
                engine_state['positions'][sid] = {'side': 'short', 'entry_price': price, 'entry_time': str(datetime.now())}
    elif pos['side'] == 'long':
        if exit_sell:
            if place('SELL'):
                del engine_state['positions'][sid]
    elif pos['side'] == 'short':
        if exit_buy:
            if place('BUY'):
                del engine_state['positions'][sid]

def run_strategies_parallel(strategies, client, max_workers=None):
    """Parallel evaluation of all enabled strategies."""
    try:
        return _py_parallel_strategies(strategies, client, max_workers)
    except Exception:
        results = []
        for s in strategies:
            if not s.get('enabled', False):
                continue
            try:
                run_strategy_cycle(s, client)
                results.append((s.get('symbol', '?'), True))
            except Exception as e:
                results.append((s.get('symbol', '?'), str(e)))
        return results

def engine_loop(client_getter):
    while engine_state['running']:
        try:
            strategies = get_strategies()
            client = client_getter()
            run_strategies_parallel(strategies, client)
            time.sleep(60)
        except Exception as e:
            add_trade_entry({'action': 'ENGINE_ERROR', 'error': str(e)})
            time.sleep(60)

def start_engine(client_getter, kill_switch_getter=None):
    if engine_state['running']:
        return False
    engine_state['kill_switch_getter'] = kill_switch_getter
    engine_state['running'] = True
    engine_state['thread'] = threading.Thread(target=engine_loop, args=(client_getter,), daemon=True)
    engine_state['thread'].start()
    return True

def stop_engine():
    engine_state['running'] = False
    if engine_state['thread']:
        engine_state['thread'].join(timeout=5)
        engine_state['thread'] = None
    return True

def parallel_backtest(strategies, symbol, interval='1h', period='60d', max_workers=None):
    """Run multiple backtests in parallel."""
    try:
        return _py_parallel_backtest(strategies, symbol, interval, period, max_workers)
    except Exception:
        return [run_backtest(s, symbol, interval, period) for s in strategies]

def compute_max_pain_cpp(strikes):
    """Compute Max Pain using C++ acceleration."""
    if USE_CPP and strikes:
        try:
            mp, losses, pcr, ce_oi, pe_oi = _cpp_max_pain(strikes)
            return mp, losses, pcr, ce_oi, pe_oi
        except Exception:
            pass
    return None, {}, 0, 0, 0

STRATEGY_TEMPLATES = [
    {
        "name": "EMA 12/26 Crossover",
        "description": "Long: 12-EMA crosses above 26-EMA. Exit: cross below. Short: reverse.",
        "symbol": "SBIN", "product": "MIS", "interval": "15m",
        "quantity": 1, "stop_loss": 2.0, "target": 4.0,
        "entry_long_conds": [{"tag": "ema_cross_above", "fast_period": 12, "slow_period": 26}],
        "exit_long_conds": [{"tag": "ema_cross_below", "fast_period": 12, "slow_period": 26}],
        "entry_short_conds": [{"tag": "ema_cross_below", "fast_period": 12, "slow_period": 26}],
        "exit_short_conds": [{"tag": "ema_cross_above", "fast_period": 12, "slow_period": 26}],
    },
    {
        "name": "RSI Mean Reversion",
        "description": "Long: RSI oversold ≤30. Exit: RSI overbought ≥70. Short reverse.",
        "symbol": "SBIN", "product": "MIS", "interval": "15m",
        "quantity": 1, "stop_loss": 3.0, "target": 5.0,
        "entry_long_conds": [{"tag": "rsi_below", "period": 14, "value": 30}],
        "exit_long_conds": [{"tag": "rsi_above", "period": 14, "value": 70}],
        "entry_short_conds": [{"tag": "rsi_above", "period": 14, "value": 70}],
        "exit_short_conds": [{"tag": "rsi_below", "period": 14, "value": 30}],
    },
    {
        "name": "Bollinger Squeeze Breakout",
        "description": "Long: price > BB upper. Exit: price < BB mid. Short reverse.",
        "symbol": "SBIN", "product": "MIS", "interval": "15m",
        "quantity": 1, "stop_loss": 3.0, "target": 6.0,
        "entry_long_conds": [{"tag": "price_above_bb_upper", "period": 20}],
        "exit_long_conds": [{"tag": "price_below_bb_lower", "period": 20}],
        "entry_short_conds": [{"tag": "price_below_bb_lower", "period": 20}],
        "exit_short_conds": [{"tag": "price_above_bb_upper", "period": 20}],
    },
    {
        "name": "MACD Crossover",
        "description": "Long: MACD above signal. Exit: MACD below signal. Short reverse.",
        "symbol": "SBIN", "product": "MIS", "interval": "15m",
        "quantity": 1, "stop_loss": 2.0, "target": 4.0,
        "entry_long_conds": [{"tag": "macd_cross_above", "fast_period": 12, "slow_period": 26}],
        "exit_long_conds": [{"tag": "macd_cross_below", "fast_period": 12, "slow_period": 26}],
        "entry_short_conds": [{"tag": "macd_cross_below", "fast_period": 12, "slow_period": 26}],
        "exit_short_conds": [{"tag": "macd_cross_above", "fast_period": 12, "slow_period": 26}],
    },
    {
        "name": "SuperTrend Long Only",
        "description": "Long: SuperTrend flips buy. Exit: flips sell.",
        "symbol": "SBIN", "product": "MIS", "interval": "5m",
        "quantity": 1, "stop_loss": 0, "target": 0,
        "entry_long_conds": [{"tag": "supertrend_buy", "period": 10, "value": 3}],
        "exit_long_conds": [{"tag": "supertrend_sell", "period": 10, "value": 3}],
        "entry_short_conds": [],
        "exit_short_conds": [],
    },
    {
        "name": "VWAP + EMA Long Only",
        "description": "Long: Price > VWAP and > EMA20. Exit: Price < VWAP.",
        "symbol": "SBIN", "product": "MIS", "interval": "5m",
        "quantity": 1, "stop_loss": 1.5, "target": 3.0,
        "entry_long_conds": [{"tag": "price_above_vwap"}, {"tag": "price_above_ema", "period": 20}],
        "exit_long_conds": [{"tag": "price_below_vwap"}],
        "entry_short_conds": [],
        "exit_short_conds": [],
    },
]
