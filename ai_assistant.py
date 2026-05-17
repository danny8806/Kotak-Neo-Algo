from openai import OpenAI
import os
import argparse
import sys
import logging
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

class _MockResponses:
    def create(self, **kwargs):
        inp = kwargs.get("input", "")
        content = inp[-1]["content"] if isinstance(inp, list) and len(inp) else str(inp)
        class _OutputText:
            def __init__(self, text):
                self.text = text
        class _Response:
            def __init__(self, text):
                self.output_text = text
                self.output = [_OutputText(text)]
        mock_text = f"[MOCK RESPONSE] Received: {str(content)[:200]}"
        return _Response(mock_text)

class MockClient:
    def __init__(self):
        self.responses = _MockResponses()

class TradingAIAssistant:

    MODEL = "openai/gpt-oss-20b"

    def __init__(self):

        api_key = os.getenv("GROQ_API_KEY")
        base_url = "https://api.groq.com/openai/v1"

        if api_key:
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
            )
        else:
            logger.warning("GROQ_API_KEY not found — using MockClient. Set GROQ_API_KEY to use real API.")
            self.client = MockClient()

        self.conversation_history = []
        self.trading_context = {}

        logger.info("Trading AI Assistant Initialized Successfully")

    def set_trading_context(self, context: Dict[str, Any]):
        self.trading_context = context

    def _generate_response(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000
    ) -> str:

        try:
            response = self.client.responses.create(
                model=self.MODEL,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
            )
            return response.output_text

        except Exception as e:
            logger.error(f"AI Generation Error: {str(e)}")
            return f"AI Error Occurred\n\nReason:\n{str(e)}\n\nPossible Fixes:\n1. Invalid API key\n2. No internet connection\n3. Rate limit exceeded\n4. Groq API unavailable\n"

    # ==========================================
    # MARKET ANALYSIS
    # ==========================================

    def analyze_market_data(
        self,
        symbol: str,
        data: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze market data for:

SYMBOL:
{symbol}

MARKET DATA:
{json.dumps(data, indent=2)}

TRADING CONTEXT:
{json.dumps(self.trading_context, indent=2)}

Provide:

1. Trend Direction
2. Momentum Analysis
3. Support & Resistance
4. Buy/Sell/Hold Signal
5. Risk Level
6. Swing Trading Opportunity
7. Intraday Opportunity
8. Scalping Opportunity
9. Option Trading View
10. Final Conclusion
"""

        return self._generate_response(
            system_prompt="""
You are a professional market analyst with expertise in:
- Technical Analysis
- Option Trading
- Risk Management
- Smart Money Concepts
- Price Action
- Market Structure
""",
            user_prompt=prompt,
            temperature=0.7,
            max_tokens=1200
        )

    # ==========================================
    # STRATEGY GENERATOR
    # ==========================================

    def generate_trading_strategy(
        self,
        symbol: str,
        timeframe: str,
        risk_tolerance: str
    ) -> str:

        prompt = f"""
Generate a professional trading strategy.

SYMBOL:
{symbol}

TIMEFRAME:
{timeframe}

RISK TOLERANCE:
{risk_tolerance}

CURRENT CONTEXT:
{json.dumps(self.trading_context, indent=2)}

Include:

1. Strategy Name
2. Entry Rules
3. Exit Rules
4. Stop Loss Logic
5. Target Logic
6. Indicators Used
7. Win Rate Estimation
8. Risk Reward Ratio
9. Capital Requirement
10. Option Buying/Selling suitability
11. Best Market Conditions
12. Avoid Conditions
"""

        return self._generate_response(
            system_prompt="""
You are an elite quantitative trader and strategy developer.
Create practical and implementable strategies.
""",
            user_prompt=prompt,
            temperature=0.6,
            max_tokens=1500
        )

    # ==========================================
    # RISK MANAGEMENT
    # ==========================================

    def risk_assessment(
        self,
        portfolio_data: Dict[str, Any],
        proposed_trade: Dict[str, Any]
    ) -> str:

        prompt = f"""
Perform detailed risk assessment.

PORTFOLIO:
{json.dumps(portfolio_data, indent=2)}

TRADE:
{json.dumps(proposed_trade, indent=2)}

CONTEXT:
{json.dumps(self.trading_context, indent=2)}

Provide:

1. Portfolio Exposure
2. Drawdown Risk
3. Risk Reward Ratio
4. Position Size Analysis
5. Leverage Risk
6. Greeks Risk
7. Volatility Risk
8. Capital Protection Suggestions
9. Risk Rating
10. Final Recommendation
"""

        return self._generate_response(
            system_prompt="""
You are a professional risk manager focused on capital preservation.
""",
            user_prompt=prompt,
            temperature=0.4,
            max_tokens=1200
        )

    # ==========================================
    # SENTIMENT ANALYSIS
    # ==========================================

    def market_sentiment_analysis(
        self,
        news_data: List[str],
        market_data: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze market sentiment.

NEWS:
{json.dumps(news_data, indent=2)}

MARKET DATA:
{json.dumps(market_data, indent=2)}

Provide:

1. Overall Sentiment
2. Institutional Activity
3. Retail Sentiment
4. Sector Impact
5. Bullish/Bearish Score
6. Market Outlook
7. Trading Opportunities
"""

        return self._generate_response(
            system_prompt="""
You are a professional financial sentiment analyst.
""",
            user_prompt=prompt,
            temperature=0.5,
            max_tokens=1000
        )

    # ==========================================
    # OPTION CHAIN ANALYSIS
    # ==========================================

    def option_chain_analysis(
        self,
        option_data: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze this option chain.

OPTION DATA:
{json.dumps(option_data, indent=2)}

Provide:

1. PCR Analysis
2. Max Pain
3. OI Analysis
4. Call Writing
5. Put Writing
6. Support Levels
7. Resistance Levels
8. Expected Expiry Range
9. IV Analysis
10. Best Option Strategy
"""

        return self._generate_response(
            system_prompt="""
You are an expert options trader.
""",
            user_prompt=prompt,
            temperature=0.5,
            max_tokens=1200
        )

    # ==========================================
    # TRADE EXECUTION AI
    # ==========================================

    def trade_decision(
        self,
        trade_data: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze whether this trade should be executed.

TRADE DATA:
{json.dumps(trade_data, indent=2)}

Provide:

1. Trade Quality Score
2. Entry Timing
3. Risk Level
4. SL Recommendation
5. Target Recommendation
6. Probability of Success
7. Execution Recommendation
"""

        return self._generate_response(
            system_prompt="""
You are an institutional trade execution analyst.
""",
            user_prompt=prompt,
            temperature=0.4,
            max_tokens=1000
        )

    # ==========================================
    # AI CHATBOT
    # ==========================================

    def chat_query(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> str:

        if context:
            self.conversation_history.append({
                "role": "system",
                "content": json.dumps(context)
            })

        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        self.conversation_history = self.conversation_history[-10:]

        try:
            response = self.client.responses.create(
                model=self.MODEL,
                input=[
                    {
                        "role": "system",
                        "content": "You are an advanced AI trading assistant.\n\nCapabilities:\n- Technical Analysis\n- Fundamental Analysis\n- Algo Trading\n- Option Trading\n- Scalping\n- Swing Trading\n- Quantitative Trading\n- Risk Management\n- Pine Script\n- Python Trading Bots"
                    }
                ] + self.conversation_history,
                temperature=0.7,
            )

            ai_response = response.output_text

            self.conversation_history.append({
                "role": "assistant",
                "content": ai_response
            })

            return ai_response

        except Exception as e:
            logger.error(f"Chat Error: {str(e)}")
            return f"AI Chat Error: {str(e)}"

    def clear_conversation_history(self):
        self.conversation_history = []
        logger.info("Conversation history cleared")

    def health_check(self):
        try:
            response = self.client.responses.create(
                model=self.MODEL,
                input="Say system online",
            )
            return {
                "status": "online",
                "response": response.output_text,
                "time": str(datetime.now())
            }
        except Exception as e:
            return {
                "status": "offline",
                "error": str(e),
                "time": str(datetime.now())
            }

# ==========================================
# GLOBAL INSTANCE
# ==========================================

ai_assistant = TradingAIAssistant()

# ==========================================
# CLI / simple run entrypoint
# ==========================================
def get_ai_assistant():
    return ai_assistant

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Run Trading AI Assistant quick checks")
	parser.add_argument("--health", action="store_true", help="Run health check")
	parser.add_argument("--sample-analyze", action="store_true", help="Run sample market analysis")
	args = parser.parse_args()

	if args.health:
		result = ai_assistant.health_check()
		print("HEALTH CHECK:", result)
		sys.exit(0)

	if args.sample_analyze:
		sample_data = {
			"ohlc": [
				{"t":"2026-01-01T09:15:00","o":100,"h":102,"l":99,"c":101,"v":1200},
				{"t":"2026-01-01T09:30:00","o":101,"h":103,"l":100,"c":102,"v":1300}
			]
		}
		resp = ai_assistant.analyze_market_data("TESTSYM", sample_data)
		print("SAMPLE ANALYSIS:\n", resp)
		sys.exit(0)

	print("No args provided. Try --health or --sample-analyze")