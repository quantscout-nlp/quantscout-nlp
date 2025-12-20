# quantscout_nlp.py - Institutional-Grade QuantScout NLP Trader (Live Mode)
# Run: streamlit run quantscout_nlp.py
# Features: Multi-source fallback, VADER sentiment, diagnostics, WebSocket real-time, Plotly charts, auto-refresh

import os
import time
import json
import logging
import threading
import queue
import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from alpaca.data.live import StockDataStream
except ImportError:
    StockDataStream = None

# =========================
# Configuration & Logging
# =========================
logging.basicConfig(filename='quantscout.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

UTC = timezone.utc
vader = SentimentIntensityAnalyzer()

# Load secrets
try:
    ALPACA_KEY = st.secrets["api_keys"]["alpaca_key"]
    ALPACA_SECRET = st.secrets["api_keys"]["alpaca_secret"]
    TIINGO_KEY = st.secrets["api_keys"]["tiingo_key"]
    # Add others if needed
except Exception:
    st.error("API keys not found in secrets.toml!")
    st.stop()

# Example tickers (customize)
TICKERS = ["TSLA", "SNOW", "DUOL", "ORCL", "RDDT", "SHOP", "MU", "DASH", "ARM", "RKLB"]

# =========================
# WebSocket Real-Time Prices (Alpaca)
# =========================
price_queue = queue.Queue()

def start_ws():
    if StockDataStream is None:
        logging.warning("alpaca-py not installed for WS")
        return

    async def run_ws():
        wss = StockDataStream(ALPACA_KEY, ALPACA_SECRET)
        async for quote in wss.subscribe_quotes():
            data = {
                "symbol": quote.symbol,
                "bid": quote.bid_price,
                "ask": quote.ask_price,
                "mid": (quote.bid_price + quote.ask_price) / 2,
                "timestamp": quote.timestamp
            }
            price_queue.put(data)

    threading.Thread(target=lambda: asyncio.run(run_ws()), daemon=True).start()

# Start WS in background
start_ws()

# =========================
# Data Fetching & Sentiment
# =========================
@st.cache_data(ttl=60)
def get_tiingo_sentiment(symbol: str) -> tuple[float, str]:
    url = f"https://api.tiingo.com/tiingo/news?tickers={symbol}&token={TIINGO_KEY}"
    try:
        resp = requests.get(url, timeout=5)
        articles = resp.json()
        if articles:
            text = f"{articles[0]['title']} {articles[0].get('description', '')}"
            score = vader.polarity_scores(text)['compound']
            return score, articles[0]['title']
    except Exception as e:
        logging.error(f"Tiingo error {symbol}: {e}")
    return 0.0, "No news"

def get_price_from_ws(symbol: str) -> Optional[Dict]:
    while not price_queue.empty():
        msg = price_queue.get()
        if msg["symbol"] == symbol:
            return msg
    return None

# Main scan
def run_scan(tickers: List[str]) -> pd.DataFrame:
    results = []
    mismatches = 0
    for sym in tickers:
        sentiment, headline = get_tiingo_sentiment(sym)

        # Prefer WS price
        ws_data = get_price_from_ws(sym)
        if ws_data:
            price = ws_data["mid"]
            source = "Alpaca WS"
        else:
            # Fallback yfinance
            try:
                data = yf.Ticker(sym).info
                price = data.get("regularMarketPrice") or data.get("previousClose")
                source = "yFinance"
            except:
                price = None
                source = "Error"

        if price is None:
            decision = "HOLD"
            confidence = 0.0
        else:
            score = sentiment * 0.5 + 0.5  # Simple ensemble
            confidence = abs(score) * 100
            decision = "BUY" if score > 0.25 else "SELL" if score < -0.25 else "HOLD"

        results.append({
            "Symbol": sym,
            "Decision": decision,
            "Confidence": round(confidence, 1),
            "Sentiment": round(sentiment, 3),
            "Price": round(price, 2) if price else None,
            "Source": source,
            "TopNews": headline
        })

        if "MISMATCH" in str(source):  # Placeholder for your mismatch logic
            mismatches += 1

    df = pd.DataFrame(results)
    logging.info(f"Scan complete: {len(df[df['Decision']=='BUY'])} BUYs")
    if mismatches > 0:
        st.error(f"{mismatches} data issues detected!")
    return df

# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="QuantScout Live Monitor", layout="wide")
st.title("🛡️ QuantScout NLP Institutional Live Monitor")

with st.sidebar:
    st.header("Controls")
    auto_live = st.checkbox("Auto Live Mode (real-time refresh)", value=True)
    live_refresh_sec = st.slider("Refresh interval (sec)", 1, 30, 5)
    tickers_input = st.text_input("Tickers (comma/space)", value=" ".join(TICKERS))

tickers = [t.strip().upper() for t in tickers_input.replace(",", " ").split() if t.strip()]

if not tickers:
    st.warning("Enter tickers to scan.")
    st.stop()

df = run_scan(tickers)

# Summary metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Tickers", len(df))
c2.metric("BUY", len(df[df["Decision"]=="BUY"]))
c3.metric("SELL", len(df[df["Decision"]=="SELL"]))
c4.metric("HOLD", len(df[df["Decision"]=="HOLD"]))

# Actionable table
actionable = df[df["Decision"].isin(["BUY", "SELL"])]
if not actionable.empty:
    st.subheader("🚀 Actionable Signals")
    st.dataframe(actionable, use_container_width=True)

st.subheader("Full Scan Results")
st.dataframe(df, use_container_width=True)

# Interactive Charts
if not df.empty:
    st.subheader("Interactive Charts")
    # Sentiment vs Confidence scatter
    fig_scatter = px.scatter(df, x="Sentiment", y="Confidence", color="Decision",
                             hover_data=["Symbol", "Price"], title="Sentiment-Confidence Map")
    st.plotly_chart(fig_scatter, use_container_width=True)

    # Simple price bar
    fig_bar = px.bar(df, x="Symbol", y="Price", color="Decision", title="Current Prices")
    st.plotly_chart(fig_bar, use_container_width=True)

# Exports
st.download_button("Download CSV", df.to_csv(index=False).encode(), "quantscout.csv", "text/csv")
st.download_button("Download Logs", open("quantscout.log", "rb").read(), "quantscout.log", "text/plain")

# Auto refresh loop
if auto_live:
    st.caption(f"Auto refresh every {live_refresh_sec}s")
    time.sleep(live_refresh_sec)
    st.rerun()