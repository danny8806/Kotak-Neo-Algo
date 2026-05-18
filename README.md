# Kotak Neo AI Algo Trading Terminal

A full-stack algorithmic trading terminal for Kotak Neo Securities with AI-powered analysis, NSE public option chain data, real-time risk management, and automated strategy execution.

Built with Flask + vanilla JS — no frontend framework, no build step. Dark terminal-style UI with glassmorphism effects, mobile responsive sidebar, and real-time data streaming.

---

## 📋 Table of Contents

- [Features Overview](#features-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Pages & Tabs Walkthrough](#pages--tabs-walkthrough)
- [Option Chain (NSE Public API)](#option-chain-nse-public-api)
- [AI Assistant](#ai-assistant)
- [Algo Trading Engine](#algo-trading-engine)
- [Risk Management](#risk-management)
- [Technical Indicators & Charts](#technical-indicators--charts)
- [Kotak Neo API Integration](#kotak-neo-api-integration)
- [Telegram Alerts](#telegram-alerts)
- [Deployment](#deployment)
- [API Reference](#api-reference)
- [Screenshots](#screenshots)
- [Disclaimer](#disclaimer)

---

## Features Overview

### 📊 Dashboard
- Real-time P&L from open positions
- Portfolio summary with day change, MTM, and exposure
- Kill switch (one-click emergency stop)
- Quick-access stats cards

### 📈 Market Data
- **Quotes**: Live LTP, volume, bid/ask, change for any symbol
- **Scrip Master**: Browse all tradable instruments
- **Funds & Limits**: View margin, cash, and position limits
- **Technical Analysis**: 
  - Candlestick charts (Lightweight Charts by TradingView)
  - RSI, MACD, EMA 20/50, Bollinger Bands, SuperTrend
  - Ichimoku Cloud (Tenkan, Kijun, Senkou Span A/B)
  - Fibonacci Retracement levels overlay
  - VWAP, ATR, Volume analysis

### 🎯 Option Chain (NSE Public - Free)
- **No API key required** — uses NSE public APIs with cookie-based session
- **Indices**: NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY
- **Stocks**: 200+ underlying stocks with options
- **Weekly expiry** auto-populated from NSE
- **17-column table**: Strike | CE LTP/OI/OI Chg/IV/Bid/Ask/Vol/Chg | PE LTP/OI/OI Chg/IV/Bid/Ask/Vol/Chg
- **Underlying separator** row at the strike nearest spot price
- **Key metrics**: PCR, Max Pain, OI concentration, total CE/PE OI
- **Auto-refresh toggle** (3-second polling)
- **AI-powered analysis** of top OTM strikes

### 🤖 AI Assistant (Groq)
- **AI Chat**: Trading-aware conversational assistant
- **Market Analysis**: Trend, momentum, S/R levels, buy/sell signal
- **Strategy Generator**: Creates trading strategies based on your parameters
- **Risk Assessment**: Portfolio exposure, drawdown risk, position sizing
- **Sentiment Analysis**: Market mood, institutional activity
- **Trade Journal Analysis**: AI reviews your trades, identifies mistakes and patterns
- **Option Chain Analysis**: Detailed bias, OI clustering, IV skew, support/resistance, strategy recommendations

### 🔄 Algo Trading Engine
- **Background thread** evaluates strategies every 60 seconds
- **Strategy Builder**: Create strategies with buy/sell conditions
- **14 Condition Types**: EMA crossover, RSI, MACD, Bollinger Bands, SuperTrend, volume spike, VWAP, and more
- **4 Pre-built Templates**: EMA Crossover, RSI Mean Reversion, Bollinger Squeeze, MACD Crossover
- **Backtesting**: Run strategies against historical yfinance data (win rate, PnL, trade log)
- **Live Execution**: Auto-place orders via Kotak Neo API when conditions trigger
- **Kill Switch**: Instantly cancel all orders and block new ones

### ⚠️ Risk Management
- Daily loss limit (auto-stop trading)
- Max trades per day limit
- Max position size cap
- Kill switch (cancels all open orders + blocks placement)
- AI risk advisor for portfolio-level risk analysis
- Visual risk bar showing loss limit utilization

### 📱 Alerts & Telegram
- Price alerts with browser Notification API
- Telegram bot integration for remote notifications
- Configurable bot token and chat ID

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.13, Flask, Flask-SocketIO |
| **Frontend** | Vanilla JavaScript, HTML5, CSS3 |
| **UI Theme** | Dark terminal with glassmorphism, CSS variables |
| **Charts** | Lightweight Charts (TradingView) via CDN |
| **AI** | Groq Responses API (`openai/gpt-oss-20b`) |
| **Broker** | Kotak Neo API v2 SDK (`neo_api_client`) |
| **Historical Data** | yfinance (free, zero auth) |
| **Option Chain** | NSE Public API (free, cookie-based session) |
| **Auth** | TOTP via `pyotp` |
| **Notifications** | Telegram Bot API + Browser Notification API |
| **Data Storage** | JSON files (strategies, trade logs) |
| **Real-time** | Flask-SocketIO (WebSocket) |

---

## Project Structure

```
📁 Kotak-Neo-Algo/
├── 📄 app.py                       # Flask app — 56+ API routes
├── 📄 algo_engine.py               # Strategy engine, backtesting, indicators
├── 📄 ai_assistant.py              # Groq AI integration (TradingAIAssistant)
├── 📄 websocket_handler.py         # WebSocket live data handler stub
├── 📁 templates/
│   └── 📄 index.html              # Single-page UI (~2850 lines)
├── 📁 sensibull-bridge/            # Sensibull data bridge (experimental)
├── 📄 .env                         # Credentials (gitignored)
├── 📄 .gitignore
├── 📄 README.md
├── 📄 requirements.txt             # Python dependencies
├── 📄 algo_strategies.json         # Saved strategies (auto-generated)
├── 📄 algo_trade_log.json          # Trade logs (auto-generated)
│
# Test/utility scripts (not part of main app):
├── 📄 trade_test.py                # NeoAPI connection test
├── 📄 trade_test_fix.py            # NeoAPI test with fixes
├── 📄 test_trade.py                # Basic order placement test
├── 📄 test_trade_token.py          # Token-based auth test
├── 📄 trade_raw.py                 # Raw HTTP order placement test
└── 📄 test_angel_historical.py     # Angel One historical data test (deprecated)
```

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/danny8806/Kotak-Neo-Algo.git
cd Kotak-Neo-Algo

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your credentials (see Configuration section)

# 5. Run the server
python app.py

# 6. Open in browser
# http://localhost:8080
```

---

## Configuration

Create a `.env` file in the project root:

```env
# === GROQ AI (required for AI features) ===
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# === Kotak Neo Trading (required for orders, positions, holdings) ===
CONSUMER_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MOBILE_NUMBER=+9198xxxxxxxx
UCC=XXXXX
MPIN=123456

# === Telegram (optional — for alerts) ===
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789

# === Kotak Neo TOTP Secret (optional — for auto-login) ===
TOTP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Getting Credentials

1. **GROQ_API_KEY**: Sign up at [console.groq.com](https://console.groq.com) → Create API key
2. **CONSUMER_KEY**: Generate at [Kotak Neo Dashboard](https://neo.kotaksecurities.com) → API → Consumer Key
3. **MOBILE_NUMBER**: Your registered Kotak Neo mobile number with country code
4. **UCC**: Your Kotak Neo trading ID
5. **MPIN**: Your Kotak Neo MPIN (used for session validation)
6. **TELEGRAM**: Create bot via [@BotFather](https://t.me/BotFather) on Telegram, get chat ID from [@userinfobot](https://t.me/userinfobot)

### IP Whitelisting

Kotak Neo requires your public IP to be whitelisted. Run:

```bash
curl ifconfig.me
```

Add the returned IP at [Kotak Neo Dashboard](https://neo.kotaksecurities.com) → API → IP Whitelist.

---

## Pages & Tabs Walkthrough

### 1. Dashboard (`page-dashboard`)
- Quick stats: Connected status, funds, positions count
- Live P&L with day change (green/red)
- Portfolio exposure summary
- Kill switch toggle (bottom-right floating button)

### 2. Orders (`page-orders`)
- View all orders (open, executed, cancelled)
- Filter by status and exchange
- Place new orders with full parameter control
- Modify and cancel existing orders
- Order book with color-coded status badges

### 3. Option Chain (`page-options`)
- **Mode selector**: Indices (dropdown) / Stocks (text input with autocomplete)
- **Symbol selection**: Indices from NSE, stocks with 200+ autocomplete options
- **Expiry dropdown**: Auto-populated from NSE API, defaults to weekly near
- **Underlying value** display with separator line
- **Stats cards**: PCR, Max Pain, Total CE OI, Total PE OI
- **Full 17-column table** with CE/PE side-by-side
- **Auto-refresh toggle**: 3-second polling for live updates
- **AI Analyze button**: Sends top OTM strikes to Groq for detailed analysis
- **AI Analysis Panel**: Market bias, OI concentration, IV skew, S/R levels, strategies

### 4. Watchlist (`page-watchlist`)
- User-managed watchlist stored in localStorage
- Real-time LTP updates
- Add/remove symbols
- Color-coded change indicators

### 5. Market Data (`page-market-data`)
- **Quotes tab**: Fetch live LTP, bid/ask, volume, change for any token
- **Scrip Master tab**: Browse all instruments with search
- **Funds & Limits tab**: View margin, cash, adhoc limits
- **Technical Indicators card**:
  - Symbol + interval + period selector
  - RSI, MACD, EMA 20/50 crossover signal, Bollinger Band position
  - SuperTrend direction, VWAP, ATR
  - Day change calculation
  - **Interactive Candlestick Chart** with volume histogram
  - **EMA 20/50 overlay** on chart
  - **Ichimoku Cloud**: Tenkan/Kijun lines + Senkou Span A/B
  - **Fibonacci levels**: 0, 23.6, 38.2, 50, 61.8, 78.6, 100%

### 6. AI Tools (`page-ai-tools`)
- **AI Chat**: Free-form trading questions with context awareness
- **Market Analysis**: Submit symbol + timeframe for AI-powered analysis
- **Strategy Generation**: AI creates custom trading strategy based on your inputs
- **Risk Assessment**: AI evaluates portfolio risk
- **Trade Journal**: Paste trade data for AI review (identifies mistakes, patterns, improvements)
- **Sentiment Analysis**: AI reads market mood

### 7. Alerts (`page-alerts`)
- Set price alerts (above/below conditions)
- Desktop notifications via browser Notification API
- Telegram bot integration
- Test Telegram button
- Active alerts list with remove functionality

### 8. Algo Trading (`page-algo`)
- **Strategies list**: All saved strategies with enable/disable toggle
- **Strategy Builder**: Create/edit strategies with:
  - Symbol, quantity, product type
  - Multiple buy conditions (AND logic)
  - Multiple sell conditions (AND logic)
  - Stop loss % and target %
  - 4 pre-built templates (one-click load)
- **Backtest**: Run strategy against historical data, view:
  - Total trades, wins, losses, win rate
  - Total PnL %, average PnL %
  - Trade-by-trade breakdown
  - Signals timeline
- **Live Trading**: Enabled strategies auto-execute via background engine
- **Engine Controls**: Start/stop engine, view status
- **Trade Log**: All engine-generated trades with action, symbol, price, quantity

### 9. Risk Management (`page-risk-management`)
- Enable/disable risk management
- Daily loss limit (₹)
- Max trades per day
- Max position size (₹)
- **AI Risk Advisor**: Get AI-powered risk analysis
- **Portfolio Risk Scan**: Full portfolio risk assessment
- **Kill Switch**: Emergency stop
- **Risk Status Panel**: Today's trades, P&L, loss limit bar

---

## Option Chain (NSE Public API)

### How It Works

The NSE option chain uses a `requests.Session()` with browser-like headers to access NSE's public APIs — **no API key needed**.

### Session Initialization

```python
s = requests.Session()
s.headers.update({
    "user-agent": "Mozilla/5.0 ...",
    "accept-language": "en,gu;q=0.9,hi;q=0.8",
})
s.get("https://www.nseindia.com/option-chain")  # Gets cookies
```

### API Calls

1. **Symbols**: `GET https://www.nseindia.com/api/underlying-information`
   - Returns indices (NIFTY, BANKNIFTY, etc.) + 200+ stocks
2. **Expiry Dates**: `GET https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}`
3. **Option Chain**: `GET https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol={symbol}&expiry={date}`

### Response Fields (per strike)

| Field | Description |
|-------|-------------|
| `strike` | Strike price |
| `ce.ltp` | Call LTP |
| `ce.oi` | Call Open Interest |
| `ce.chg_oi` | Change in OI |
| `ce.iv` | Implied Volatility |
| `ce.volume` | Total traded volume |
| `ce.bid` | Bid price |
| `ce.ask` | Ask price |
| `ce.change` | Price change |
| `pe.*` | Same fields for Puts |

### Max Pain Calculation

```python
for each strike S:
    loss = 0
    for each strike O:
        if O is CE and O.strike <= S:
            loss += O.oi * (S - O.strike)
        if O is PE and O.strike >= S:
            loss += O.oi * (O.strike - S)
    losses[S] = loss
max_pain = strike with minimum loss
```

### AI Analysis Prompt

Sends top 8 OTM calls (strike ≥ UV) and top 8 OTM puts (strike ≤ UV) to Groq with:
- Strike, LTP, OI, OI Change, OI Concentration %, IV, Volume, Bid, Ask, Change
- IV skew (difference between far OTM call IV and near put IV)
- Asks for: market bias, PCR interpretation, OI concentration, IV analysis, S/R levels, strategies

---

## AI Assistant

### Architecture

```
TradingAIAssistant
├── __init__()              # Initialize Groq client or MockClient
├── chat_query()            # Main chat with conversation history
├── _generate_response()    # Core Groq API call wrapper
│
├── analyze_market_data()   # Market analysis
├── generate_trading_strategy()  # Strategy creation
├── risk_assessment()       # Portfolio risk evaluation
├── market_sentiment_analysis()  # Sentiment reading
├── option_chain_analysis() # Options data analysis
├── trade_decision()        # Trade execution evaluation
├── analyze_trades()        # Trade journal review
│
├── clear_conversation_history()
└── health_check()
```

### Model & Configuration

- **Model**: `openai/gpt-oss-20b` (via Groq)
- **API Endpoint**: `https://api.groq.com/openai/v1`
- **Temperature**: 0.4–0.7 (varies by task)
- **Max Tokens**: 1000–1500

### Rate Limit Handling

The Groq free tier has:
- **8000 Tokens Per Minute (TPM)**
- **200K Tokens Per Day (TPD)**

To stay within limits:
- Compact prompt formatting (pipe-delimited instead of JSON)
- Only 5–8 strikes per side (not full chain)
- Conversation history cleared before one-shot analyses
- System prompt kept minimal

### Mock Mode

If `GROQ_API_KEY` is not set, the AI falls back to `MockClient` which returns canned responses — useful for UI development without API access.

---

## Algo Trading Engine

### Engine Architecture

```
Background Thread (daemon=True)
└── engine_loop()
    └── Every 60 seconds:
        1. Load all enabled strategies from JSON
        2. For each strategy:
            a. Fetch latest data via yfinance
            b. Compute indicators (EMA, RSI, MACD, BB, SuperTrend, VWAP, ATR)
            c. Evaluate BUY conditions (AND logic)
            d. Evaluate SELL conditions (AND logic)
            e. Check stop loss / target
            f. Place order via NeoAPI if triggered
            g. Log trade
        3. Sleep 60 seconds
```

### 14 Condition Types

| Tag | Logic |
|-----|-------|
| `price_above_ema` | Close > EMA(current) |
| `price_below_ema` | Close < EMA(current) |
| `ema_cross_above` | Previous EMA12 <= EMA26 AND Current EMA12 > EMA26 |
| `ema_cross_below` | Previous EMA12 >= EMA26 AND Current EMA12 < EMA26 |
| `rsi_above` | RSI > threshold |
| `rsi_below` | RSI < threshold |
| `macd_cross_above` | MACD line crosses above signal |
| `macd_cross_below` | MACD line crosses below signal |
| `price_above_bb_upper` | Close > Bollinger Upper Band |
| `price_below_bb_lower` | Close < Bollinger Lower Band |
| `supertrend_up` | SuperTrend trend == 1 (uptrend) |
| `supertrend_down` | SuperTrend trend == -1 (downtrend) |
| `volume_spike` | Volume > avg_volume * multiplier |
| `price_above_vwap` | Close > VWAP |

### Strategy Templates

1. **EMA 12/26 Crossover**: Buy on golden cross, sell on death cross
2. **RSI Mean Reversion**: Buy when oversold (≤30), sell when overbought (≥70)
3. **Bollinger Squeeze Breakout**: Buy above upper band, sell below lower band
4. **MACD Crossover**: Buy on MACD signal cross above, sell on cross below

### Backtesting

```python
run_backtest(strategy, symbol, interval, period)
```

- Fetches historical data via yfinance
- Computes all indicators
- Walks through each bar sequentially (starting at index 60)
- Tracks position state, entry/exit, stop loss, target hits
- Returns: trades list, win rate, total PnL %, average PnL %

### Kill Switch Integration

```python
if kill_switch_getter and kill_switch_getter():
    # Skip strategy cycle — no orders placed
    return
```

---

## Risk Management

### Components

1. **Daily Loss Limit**: Auto-stops trading when daily P&L hits threshold
2. **Max Trades Per Day**: Prevents overtrading
3. **Max Position Size**: Caps single order value
4. **Kill Switch**: One-click emergency stop (cancels all orders + blocks placement)

### Enforcement

```python
def _enforce_risk(action="trade"):
    # Check daily loss limit
    if abs(risk_state["daily_pnl"]) >= risk_config["daily_loss_limit"]:
        return False, "Daily loss limit exceeded"
    # Check max trades
    if risk_state["trade_count"] >= risk_config["max_trades_per_day"]:
        return False, "Max trades for today"
    # Check position size
    if action == "order" and order_value > risk_config["max_position_size"]:
        return False, "Position size exceeds limit"
```

### Kill Switch Flow

1. User clicks "KILL ON" button
2. `POST /api/kill-switch` sets `kill_switch_active = True`
3. Fetches all open orders via NeoAPI
4. Cancels each order
5. Returns confirmation
6. All subsequent `place_order()` calls return 403
7. Algo engine skips all cycles
8. Button turns red with shake animation

---

## Technical Indicators & Charts

### Client-Side Computations

All technical indicators are computed client-side in JavaScript for speed:

| Function | Formula |
|----------|---------|
| `calcSMA()` | Sum of last N values / N |
| `calcEMA()` | `price * k + EMA_prev * (1 - k)` where `k = 2 / (N + 1)` |
| `calcRSI()` | `100 - 100 / (1 + avg_gain / avg_loss)` |
| `calcMACD()` | EMA12 - EMA26, then EMA9 of MACD line |
| `calcBB()` | SMA ± (`std_dev * multiplier`) |
| `calcSuperTrend()` | ATR-based bands with trend reversal logic |
| `calcIchimoku()` | Tenkan, Kijun, Senkou Span A/B, Chikou |
| `calcFibonacciLevels()` | Levels at 0, 23.6, 38.2, 50, 61.8, 78.6, 100% of range |

### Chart Overlays

- **Candlestick series**: Green/red candles
- **Volume histogram**: Color-coded by up/down
- **EMA 20**: Blue line
- **EMA 50**: Purple line
- **Ichimoku Tenkan**: Orange line (9-period)
- **Ichimoku Kijun**: Red line (26-period)
- **Ichimoku Cloud**: Green/red shaded area (Senkou Span A/B)
- **Fibonacci**: Dashed horizontal lines at each level

---

## Kotak Neo API Integration

### Login Flow

```python
client = NeoAPI(environment='prod')
# Step 1: TOTP Login
login = client.totp_login(
    mobile_number="+9198...",
    ucc="XXXXX",
    totp=pyotp.TOTP(CONSUMER_KEY).now()
)
# Step 2: Validate session
session = client.totp_validate(mpin="123456")
```

### Key SDK Methods

| Method | Purpose |
|--------|---------|
| `totp_login()` | Initiate TOTP-based login |
| `totp_validate()` | Validate session with MPIN |
| `place_order()` | Place new order |
| `modify_order()` | Modify existing order |
| `cancel_order()` | Cancel order |
| `positions()` | Get open positions |
| `holdings()` | Get portfolio holdings |
| `orders()` | Get order book |
| `quotes()` | Get live quotes |
| `limits()` | Get margin/limits |
| `scrip_master()` | Get instrument list |
| `search_scrip()` | Search instruments |
| `order_history()` | Get order history |
| `trade_report()` | Get trade report |

### Error Handling

```python
if isinstance(response, dict):
    if response.get("stCode") == "100008":
        # Session expired — needs re-login
        return {"error": "Session expired"}
```

All SDK responses are filtered through `_list()` helper to normalize inconsistent return formats:
```python
def _list(data):
    d = data.get('data', data) if isinstance(data, dict) else data
    return d if isinstance(d, list) else [d] if d else []
```

---

## Telegram Alerts

### Setup

1. Create bot: Message [@BotFather](https://t.me/BotFather) → `/newbot`
2. Get bot token (format: `123456:ABCdef`)
3. Find chat ID: Message [@userinfobot](https://t.me/userinfobot) → `/start`
4. Add to `.env`:
```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### API Call

```python
r = requests.post(
    f'https://api.telegram.org/bot{token}/sendMessage',
    json={'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'}
)
```

### Features
- Price alerts notify via Telegram + browser notification
- Test button in UI to verify bot connectivity
- Fields auto-populate from `.env` via `/api/telegram-config`
- Send custom messages via `/api/send-telegram`

---

## Deployment

### Local / Network

```bash
python app.py
# Runs on http://0.0.0.0:8080
# Access from other devices on same network via your local IP
```

### Production (Render / Railway / Fly.io)

1. Push to GitHub
2. Create `requirements.txt`:

```txt
neo-api-client==2.0.1
Flask==3.*
Flask-Cors==5.*
Flask-SocketIO==5.*
python-dotenv==1.*
pyotp==2.*
yfinance==0.*
pandas==2.*
numpy==1.*
openai==1.*
requests==2.*
gunicorn==22.*
eventlet==0.*
```

3. Create `Procfile` or use platform UI:
```
web: gunicorn -k eventlet -w 1 app:app
```

> **Note**: Kotak Neo requires IP whitelisting — cloud deployments with dynamic IPs may break login/orders. NSE option chain + AI + charts work fine from anywhere.

---

## API Reference

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Serve HTML page |
| POST | `/api/login` | Kotak Neo login |
| POST | `/api/logout` | Logout |
| GET | `/api/profile` | User profile |
| GET | `/api/session-debug` | Session debug info |

### Trading Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/place-order` | Place order |
| POST | `/api/modify-order` | Modify order |
| POST | `/api/cancel-order` | Cancel order |
| POST | `/api/cancel-cover-order` | Cancel cover order |
| POST | `/api/cancel-bracket-order` | Cancel bracket order |
| GET | `/api/orders` | Get orders |
| GET | `/api/positions` | Get positions |
| GET | `/api/holdings` | Get holdings |
| GET | `/api/quotes` | Get quotes |
| GET | `/api/limits` | Get limits |
| GET | `/api/live-pnl` | Live P&L |
| GET | `/api/order-history` | Order history |
| GET | `/api/trade-report` | Trade report |
| GET | `/api/scrip-master` | Instrument list |
| GET | `/api/search-scrip` | Search instruments |
| POST | `/api/margin-required` | Required margin |
| POST | `/api/subscribe-order-feed` | Subscribe to order feed |

### Option Chain Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/option-chain` | Kotak Neo option chain (requires login) |
| GET | `/api/nse/option-chain` | NSE public option chain (free) |
| GET | `/api/nse/symbols` | NSE symbols list |
| GET | `/api/nse/expiry-dates` | NSE expiry dates |

### AI Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ai/chat` | AI chat |
| POST | `/api/ai/analyze-market` | Market analysis |
| POST | `/api/ai/generate-strategy` | Strategy generation |
| POST | `/api/ai/risk-assessment` | Risk assessment |
| POST | `/api/ai/sentiment-analysis` | Sentiment analysis |
| POST | `/api/ai/analyze-trades` | Trade journal analysis |
| GET | `/api/ai/analyze-portfolio` | Portfolio analysis |

### Historical Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/historical` | OHLCV data via yfinance |

### Algo Trading Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/algo/templates` | Strategy templates |
| GET/POST | `/api/algo/strategies` | List/create strategies |
| DELETE | `/api/algo/strategies/<id>` | Delete strategy |
| POST | `/api/algo/strategies/<id>/toggle` | Enable/disable strategy |
| GET | `/api/algo/status` | Engine status |
| POST | `/api/algo/start` | Start engine |
| POST | `/api/algo/stop` | Stop engine |
| GET | `/api/algo/logs` | Trade logs |
| POST | `/api/algo/clear-logs` | Clear trade logs |
| POST | `/api/algo/backtest` | Run backtest |

### Risk & Alerts

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/risk/config` | Risk configuration |
| POST | `/api/risk/update-trade` | Update trade counters |
| POST | `/api/risk/reset` | Reset risk counters |
| GET/POST | `/api/kill-switch` | Kill switch toggle |
| GET | `/api/telegram-config` | Telegram configuration |
| POST | `/api/send-telegram` | Send Telegram message |

---

## Screenshots

### Dashboard Overview
![Dashboard Overview](assets/screenshots/dashboard-overview.png)

The dashboard gives a quick account snapshot with day P&L, active positions, trades today, margin, holdings status, and shortcut actions for AI portfolio analysis and risk checks.

### Trading - Place Order
![Trading Place Order](assets/screenshots/trading-place-order.png)

The trading screen provides a complete order ticket for NSE orders, including transaction side, order type, product, validity, quantity, price, trigger price, AMO selection, and margin checking.

### Orders - Order Book
![Orders Book](assets/screenshots/orders-book.png)

The order book centralizes live and historical order status with refresh and order-feed actions. When broker data is unavailable, the AI summary explains the missing state and next action.

### Account Profile
![Account Profile](assets/screenshots/account-profile.png)

The account page confirms login state, displays profile details, shows funds and limits, and summarizes recent trade report data for the connected Kotak Neo session.

### Market Data - Technical Indicators
![Technical Indicators](assets/screenshots/technical-indicators.png)

The market data module calculates technical indicators such as LTP, RSI, MACD, EMA trend, Bollinger status, SuperTrend, Ichimoku levels, Fibonacci levels, VWAP, ATR, volume, and day change.

### Option Chain Summary
![Option Chain Summary](assets/screenshots/option-chain-summary.png)

The option chain page loads NSE public option data with underlying price, PCR, max pain, total CE/PE open interest, expiry selection, and AI analysis controls.

### Option Chain Table
![Option Chain Table](assets/screenshots/option-chain-ai-analysis.png)

The option chain table highlights the underlying price row and max-pain strike while showing CE-side and PE-side market data such as LTP, OI, OI change, IV, bid, ask, volume, and price change.

### Option Chain AI Analysis
![Option Chain AI Analysis](assets/screenshots/option-chain-analysis-detail.png)

The AI option-chain analysis interprets PCR, max pain, open-interest concentration, smart-money positioning, support/resistance zones, and likely market bias.

### Option Chain AI Detail View
![Option Chain AI Detail View](assets/screenshots/option-chain-analysis-alt.png)

The detailed options view keeps the AI commentary alongside live option-chain controls so traders can refresh data, rerun analysis, and compare the narrative against current OI levels.

### AI Chat - Market Outlook
![AI Chat Market Outlook](assets/screenshots/ai-chat-outlook.png)

The AI chat assistant answers trading questions in context, including market outlooks, sector observations, macro notes, catalysts, and practical trading considerations.

### AI Strategy Generator
![AI Strategy Generator](assets/screenshots/ai-strategy-generator.png)

The strategy generator creates structured trading plans from a symbol, timeframe, and risk tolerance, including entry rules, exit rules, stop-loss logic, targets, and indicator setup.

### AI Risk Assessment
![AI Risk Assessment](assets/screenshots/ai-risk-assessment.png)

The AI risk assessment form evaluates trade-level risk using portfolio value, cash balance, symbol, quantity, price, and buy/sell side.

### AI Trade Journal
![AI Trade Journal](assets/screenshots/trade-journal.png)

The trade journal tool fetches and analyzes trade history to identify mistakes, repeated patterns, and improvement areas.

### Risk Management & Alerts
![Risk Management Alerts](assets/screenshots/risk-management-alerts.png)

The risk management page combines daily limits, trade counters, max position size controls, kill switch controls, AI risk advice, portfolio risk scans, and Telegram-enabled price alerts.

### Existing Gallery Assets

![Trade Terminal](assets/screenshots/trade-terminal.png)

![AI Copilot](assets/screenshots/ai-copilot.png)

---

## Disclaimer

**WARNING: Trading involves substantial risk of loss.**

This software is provided for **educational and personal use only**. It is not financial advice. The creators are not responsible for any financial losses incurred through the use of this software.

- Test thoroughly in a paper trading environment before live use
- Verify all orders and positions regularly
- Understand the Kotak Neo API's behavior, limits, and error handling
- The AI analysis is for reference only — always do your own research
- Option chain data from NSE is provided "as-is" with no guarantee of accuracy or timeliness

---

*Built by Dnyaneshwar Jadhav*
