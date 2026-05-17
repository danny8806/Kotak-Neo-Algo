# Kotak Neo AI Algo Trading Terminal

Full-stack algorithmic trading terminal for Kotak Neo with AI-powered analysis, NSE public option chain data, risk management, and automated strategy execution.

## Features

### 📊 NSE Public Option Chain
- Free option chain data (no API key) — indices + 200+ stocks
- Real-time PCR, Max Pain, OI concentration, IV skew
- 17-column side-by-side CE/PE table: LTP, OI, OI Change, IV, Bid, Ask, Volume, Change
- Underlying separator line — visual strike split at spot price
- Auto-refresh (3s toggle)
- Expiry date dropdown populated from NSE API

### 🤖 AI Analysis (Groq)
- Option chain analysis: market bias, support/resistance, OI clustering, IV skew
- AI chat, market analysis, strategy generation, risk assessment, sentiment analysis
- Trade journal analysis (identifies mistakes, patterns, improvements)

### 📈 Technical Analysis + Charts
- Interactive candlestick charts (Lightweight Charts)
- RSI, MACD, EMA 20/50, Bollinger Bands, SuperTrend, VWAP, ATR
- Ichimoku Cloud (Tenkan/Kijun + Senkou Span A/B)
- Fibonacci retracement levels overlay
- All computed client-side for speed

### 🔄 Algo Trading Engine
- Background thread evaluates 14 condition types every 60s
- Strategy builder with buy/sell conditions: EMA cross, RSI, MACD, BB, SuperTrend, volume spike, VWAP
- 4 pre-built strategy templates
- Backtesting engine with win rate, PnL, trade log
- Kill switch (cancels all orders + blocks placement)

### ⚙️ Risk Management
- Daily loss limits, max trades, position size caps
- Kill switch with one-click activation
- AI-powered risk advisor

### 📱 Telegram Alerts
- Price alerts with desktop Notification API + Telegram fallback
- Configurable bot token and chat ID

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, Flask, Flask-SocketIO |
| Frontend | Vanilla JS, HTML, CSS (dark terminal theme) |
| Database | JSON files (algo strategies, trade logs) |
| AI | Groq Responses API (`openai/gpt-oss-20b`) |
| Broker | Kotak Neo API v2 SDK |
| Charts | Lightweight Charts (TradingView) |
| Historical | yfinance (free, no auth) |
| Option Chain | NSE public API (free, no key) |
| Notifications | Telegram Bot API + Browser Notification API |

## Project Structure

```
├── app.py                 # Flask app — all API routes (56+ endpoints)
├── templates/
│   └── index.html         # Single-page UI (~2850 lines)
├── algo_engine.py         # Strategy engine, backtesting, indicator computation
├── ai_assistant.py        # Groq AI integration (TradingAIAssistant class)
├── websocket_handler.py   # Live data WebSocket handler (stub)
├── .env                   # Credentials (gitignored)
├── .gitignore
```

## Setup

```bash
# 1. Clone & enter
git clone https://github.com/danny8806/Kotak-Neo-Algo.git
cd Kotak-Neo-Algo

# 2. Virtual environment
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install
pip install -r requirements.txt

# 4. Create .env (see below)

# 5. Run
python app.py
```

## `.env` Configuration

```env
GROQ_API_KEY=gsk_xxx              # Required for AI features
CONSUMER_KEY=xxx                  # Kotak Neo consumer key
MOBILE_NUMBER=+918xxxxxxxxx       # Kotak Neo registered mobile
UCC=XXXXX                         # Kotak Neo UCC
MPIN=123456                       # Kotak Neo MPIN
TELEGRAM_BOT_TOKEN=xxx            # Optional: Telegram alerts
TELEGRAM_CHAT_ID=xxx              # Optional: Telegram alerts
```

## IP Whitelisting

Kotak Neo requires your public IP to be whitelisted in their dashboard. Run:

```bash
curl ifconfig.me
```

Add the returned IP at [Kotak Neo Dashboard](https://neo.kotaksecurities.com).

## Key API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/nse/option-chain` | NSE public option chain (indices + stocks) |
| `GET /api/nse/symbols` | List all indices and stocks |
| `GET /api/nse/expiry-dates` | Expiry dates for a symbol |
| `GET /api/historical` | OHLCV data via yfinance |
| `GET /api/option-chain` | Kotak Neo option chain (requires login) |
| `POST /api/ai/chat` | AI chat with trading context |
| `POST /api/algo/backtest` | Run strategy backtest |
| `POST /api/kill-switch` | Toggle kill switch |
| `POST /api/place-order` | Place order via NeoAPI |
| `GET /api/live-pnl` | Real-time P&L from positions |

## Screenshots

<!-- Add screenshots here -->

## Disclaimer

For educational and personal use. Live trading involves real financial risk. Verify broker API behavior, order payloads, and account protections before placing live orders.
