import yfinance as yf
import pandas as pd
import numpy as np
import json
import time
import threading
from datetime import datetime
from collections import defaultdict

STRATEGIES_FILE = 'algo_strategies.json'
TRADE_LOG_FILE = 'algo_trade_log.json'

engine_state = {'running': False, 'thread': None, 'kill_switch_getter': None}

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

def fetch_data(symbol, interval='5m', period='5d'):
    symbol = symbol.upper().replace('-EQ', '')
    if not symbol.endswith('.NS') and not symbol.endswith('.BO'):
        symbol = symbol + '.NS'
    df = yf.download(tickers=symbol, period=period, interval=interval, progress=False)
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
        ema = float(params.get('_ema', close))
        return close > ema
    elif tag == 'price_below_ema':
        ema = float(params.get('_ema', close))
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
        rsi = float(params.get('_rsi', 50))
        return rsi > val
    elif tag == 'rsi_below':
        rsi = float(params.get('_rsi', 50))
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
        vol = float(params.get('_volume', 0))
        avg_vol = float(params.get('_avg_volume', 1))
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
    """Pre-compute all indicators for a dataframe."""
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

def run_backtest(strategy, symbol, interval='1h', period='60d'):
    df = fetch_data(symbol, interval, period)
    if df is None or len(df) < 100:
        return {'error': 'Insufficient data', 'bars': 0}

    ind = compute_strategy_params(df)
    signals = []
    entry_price = None
    entry_bar = None
    trades = []
    position = 0

    for i in range(60, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        cond_params = {k: v.iloc[i] for k, v in ind.items() if hasattr(v, 'iloc')}
        cond_params_prev = {k: v.iloc[i-1] for k, v in ind.items() if hasattr(v, 'iloc')}

        buy_conditions = strategy.get('buy_conditions', [])
        sell_conditions = strategy.get('sell_conditions', [])
        buy = all(evaluate_condition(row, prev, c, {**cond_params, **{'value': c.get('value', 0), 'period': c.get('period', 14)}}) for c in buy_conditions) if buy_conditions else False
        sell = all(evaluate_condition(row, prev, c, {**cond_params_prev, **{'value': c.get('value', 0), 'period': c.get('period', 14)}}) for c in sell_conditions) if sell_conditions else False

        sl_pct = float(strategy.get('stop_loss', 0))
        target_pct = float(strategy.get('target', 0))

        if position == 0 and buy:
            entry_price = float(row['Close'])
            entry_bar = i
            position = 1
            signals.append({'bar': i, 'time': str(row.name), 'action': 'BUY', 'price': entry_price})
        elif position == 1:
            close_price = float(row['Close'])
            if sell:
                trades.append({'entry_bar': entry_bar, 'exit_bar': i, 'entry_price': entry_price, 'exit_price': close_price, 'pnl_pct': (close_price - entry_price) / entry_price * 100, 'reason': 'signal'})
                entry_price = None
                position = 0
            elif sl_pct > 0 and close_price <= entry_price * (1 - sl_pct / 100):
                trades.append({'entry_bar': entry_bar, 'exit_bar': i, 'entry_price': entry_price, 'exit_price': close_price, 'pnl_pct': (close_price - entry_price) / entry_price * 100, 'reason': 'stop_loss'})
                entry_price = None
                position = 0
            elif target_pct > 0 and close_price >= entry_price * (1 + target_pct / 100):
                trades.append({'entry_bar': entry_bar, 'exit_bar': i, 'entry_price': entry_price, 'exit_price': close_price, 'pnl_pct': (close_price - entry_price) / entry_price * 100, 'reason': 'target'})
                entry_price = None
                position = 0

    if position == 1:
        last = float(df.iloc[-1]['Close'])
        trades.append({'entry_bar': entry_bar, 'exit_bar': len(df)-1, 'entry_price': entry_price, 'exit_price': last, 'pnl_pct': (last - entry_price) / entry_price * 100, 'reason': 'open'})

    total_pnl = sum(t['pnl_pct'] for t in trades)
    wins = [t for t in trades if t['pnl_pct'] > 0]
    total_bars = len(df)

    return {
        'symbol': symbol,
        'interval': interval,
        'period': period,
        'total_bars': total_bars,
        'trades': len(trades),
        'wins': len(wins),
        'losses': len(trades) - len(wins),
        'win_rate': round(len(wins) / len(trades) * 100, 2) if trades else 0,
        'total_pnl_pct': round(total_pnl, 2),
        'avg_pnl_pct': round(total_pnl / len(trades), 2) if trades else 0,
        'signals': signals[:20],
        'trades': trades,
    }

def run_strategy_cycle(strategy, client):
    if engine_state.get('kill_switch_getter'):
        try:
            if engine_state['kill_switch_getter']():
                add_trade_entry({'symbol': strategy.get('symbol', '?'), 'action': 'KILL_SWITCH_BLOCKED'})
                return
        except:
            pass
    symbol = strategy.get('symbol', '')
    qty = int(strategy.get('quantity', 1))
    sl_pct = float(strategy.get('stop_loss', 0))
    target_pct = float(strategy.get('target', 0))
    product = strategy.get('product', 'MIS')

    df = fetch_data(symbol, interval='5m', period='5d')
    if df is None or len(df) < 60:
        return

    ind = compute_strategy_params(df)

    row = df.iloc[-1]
    prev = df.iloc[-2]
    cond_params = {k: v.iloc[-1] for k, v in ind.items() if hasattr(v, 'iloc')}
    cond_params_prev = {k: v.iloc[-2] for k, v in ind.items() if hasattr(v, 'iloc')}

    buy_conditions = strategy.get('buy_conditions', [])
    sell_conditions = strategy.get('sell_conditions', [])

    buy = all(evaluate_condition(row, prev, c, {**cond_params, **{'value': c.get('value', 0), 'period': c.get('period', 14)}}) for c in buy_conditions) if buy_conditions else False
    sell = all(evaluate_condition(row, prev, c, {**cond_params_prev, **{'value': c.get('value', 0), 'period': c.get('period', 14)}}) for c in sell_conditions) if sell_conditions else False

    price = float(row['Close'])
    if client and buy:
        try:
            res = client.place_order(
                exchange_segment='nse_cm',
                product=product,
                price='0',
                order_type='MKT',
                quantity=str(qty),
                validity='DAY',
                trading_symbol=symbol.replace('.NS', '-EQ'),
                transaction_type='B',
                amo='NO',
                disclosed_quantity='0',
                market_protection='0',
                pf='N',
                trigger_price='0',
            )
            add_trade_entry({'symbol': symbol, 'action': 'BUY', 'price': price, 'qty': qty, 'sl': sl_pct, 'target': target_pct, 'response': str(res)[:200]})
        except Exception as e:
            add_trade_entry({'symbol': symbol, 'action': 'BUY_FAILED', 'price': price, 'error': str(e)})
    elif client and sell:
        try:
            res = client.place_order(
                exchange_segment='nse_cm',
                product=product,
                price='0',
                order_type='MKT',
                quantity=str(qty),
                validity='DAY',
                trading_symbol=symbol.replace('.NS', '-EQ'),
                transaction_type='S',
                amo='NO',
                disclosed_quantity='0',
                market_protection='0',
                pf='N',
                trigger_price='0',
            )
            add_trade_entry({'symbol': symbol, 'action': 'SELL', 'price': price, 'qty': qty, 'response': str(res)[:200]})
        except Exception as e:
            add_trade_entry({'symbol': symbol, 'action': 'SELL_FAILED', 'price': price, 'error': str(e)})
    elif buy:
        add_trade_entry({'symbol': symbol, 'action': 'BUY_SIGNAL', 'price': price, 'qty': qty, 'mode': 'paper'})
    elif sell:
        add_trade_entry({'symbol': symbol, 'action': 'SELL_SIGNAL', 'price': price, 'qty': qty, 'mode': 'paper'})

def engine_loop(client_getter):
    while engine_state['running']:
        try:
            strategies = get_strategies()
            client = client_getter()
            for s in strategies:
                if not s.get('enabled', False):
                    continue
                try:
                    run_strategy_cycle(s, client)
                except Exception as e:
                    add_trade_entry({'symbol': s.get('symbol', '?'), 'action': 'ERROR', 'error': str(e)})
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

STRATEGY_TEMPLATES = [
    {
        "name": "EMA 12/26 Crossover",
        "description": "Buy when 12-EMA crosses above 26-EMA, sell on cross below (trend-following)",
        "symbol": "NIFTY",
        "product": "MIS",
        "quantity": 1,
        "stop_loss": 2.0,
        "target": 4.0,
        "buy_conditions": [{"tag": "ema_cross_above"}],
        "sell_conditions": [{"tag": "ema_cross_below"}],
    },
    {
        "name": "RSI Mean Reversion",
        "description": "Buy when RSI oversold (<=30), sell when overbought (>=70)",
        "symbol": "NIFTY",
        "product": "MIS",
        "quantity": 1,
        "stop_loss": 3.0,
        "target": 5.0,
        "buy_conditions": [{"tag": "rsi_below", "value": 30}],
        "sell_conditions": [{"tag": "rsi_above", "value": 70}],
    },
    {
        "name": "Bollinger Squeeze Breakout",
        "description": "Buy when price breaks above upper band, sell when breaks below lower band",
        "symbol": "NIFTY",
        "product": "MIS",
        "quantity": 1,
        "stop_loss": 3.0,
        "target": 6.0,
        "buy_conditions": [{"tag": "price_above_bb_upper"}],
        "sell_conditions": [{"tag": "price_below_bb_lower"}],
    },
    {
        "name": "MACD Crossover",
        "description": "Buy on MACD line crossing above signal line, sell on cross below",
        "symbol": "NIFTY",
        "product": "MIS",
        "quantity": 1,
        "stop_loss": 2.0,
        "target": 4.0,
        "buy_conditions": [{"tag": "macd_cross_above"}],
        "sell_conditions": [{"tag": "macd_cross_below"}],
    },
]
