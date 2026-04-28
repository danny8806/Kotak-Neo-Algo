# Kotak Algo

Kotak Algo is a local broker-integrated trading web app for Kotak Neo option workflows. It combines market structure analytics, strategy staging, broker login helpers, saved connection setups, and an AI copilot workspace inside one Flask website.

## Highlights

- Broker-integrated order workflow for Kotak Neo
- Saved broker setups for reusing old credentials locally
- Guided TOTP and diagnostics flow
- Option-chain analytics from local market data
- Strategy staging, payoff view, margin preview, modify/cancel flows
- AI Copilot tab for market, strategy, execution, and risk guidance
- Local-first secret handling through `.env` and ignored local setup storage

## Screenshots

### Trade Terminal

![Trade Terminal](assets/screenshots/trade-terminal.png)

### AI Copilot

![AI Copilot](assets/screenshots/ai-copilot.png)

## Project Structure

```text
app.py                 Flask app and API routes
trading_platform.py    Trading logic, broker integration, analytics, saved setups
templates/index.html   Main web UI
static/app.js          Frontend interactions
static/styles.css      UI styling
march_options.csv      Local option-chain source data
kotak.py               Legacy Streamlit-based implementation
kotak.ipynb            Legacy notebook, sanitized for repo safety
```

## Requirements

- Python 3.11 recommended
- Kotak Neo API access
- TOTP-enabled Kotak Neo account

## Local Setup

Create a virtual environment and install dependencies:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a local `.env` file from `.env.example` and add your own credentials:

```env
KOTAK_ENVIRONMENT=prod
KOTAK_CONSUMER_KEY=your_consumer_key
KOTAK_MOBILE_NUMBER=+91xxxxxxxxxx
KOTAK_UCC=your_ucc
KOTAK_MPIN=your_mpin
KOTAK_TOTP_SECRET=your_totp_secret
OPENAI_API_KEY=your_optional_ai_key
```

## Run

```bash
.venv/bin/python app.py
```

Open:

- `http://127.0.0.1:5000`

## Using Saved Setups

Inside the Broker Control panel:

1. Enter credentials and a setup name
2. Click `Register New`
3. Pick the saved setup later from `Saved Setups`
4. Click `Use Old Setup`
5. Run `Test`, generate or paste TOTP, then `Connect`

Saved setups are stored only in local `.broker_setups.json`, which is ignored by Git.

## Notes

- `.env` is not committed and should stay local.
- The notebook was sanitized before publication to remove embedded credentials and session output.
- `kotak.py` is kept as a legacy implementation; the primary app is the Flask website.

## Disclaimer

This project is for educational and personal workflow use. Live trading involves real financial risk. Verify broker API behavior, order payloads, limits, and account protections before placing live orders.
