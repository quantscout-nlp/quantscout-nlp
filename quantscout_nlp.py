# quantscout_nlp.py - Cloud-Optimized Version (No Async Issues)
import streamlit as st
import pandas as pd
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import yfinance as yf
import time

st.set_page_config(page_title="QuantScout Live Monitor", layout="wide")
st.title("🛡️ QuantScout NLP Institutional Live Monitor")

analyzer = SentimentIntensityAnalyzer()

# Secrets
try:
    ALPACA_KEY = st.secrets["api_keys"]["alpaca_key"]
    ALPACA_SECRET = st.secrets["api_keys"]["alpaca_secret"]
    TIINGO_KEY = st.secrets["api_keys"]["tiingo_key"]
except:
    st.error("API keys missing! Add them in Settings > Secrets.")
    st.stop()

TICKERS = ["TSLA", "SNOW", "DUOL", "ORCL", "RDDT", "SHOP", "MU", "DASH", "ARM", "RKLB"]

with st.sidebar:
    st.header("Controls")
    auto_refresh = st.checkbox("Auto Live Mode", value=True)
    refresh_sec = st.slider("Refresh (sec)", 5, 60, 10)
    user_tickers = st.text_input("Tickers", value=" ".join(TICKERS))

tickers = [t.strip().upper() for t in user_tickers.replace(",", " ").split() if t.strip()]

@st.cache_data(ttl=60)
def get_tiingo_news(symbol):
    url = f"https://api.tiingo.com/tiingo/news?tickers={symbol}&token={TIINGO_KEY}"
    try:
        resp = requests.get(url, timeout=10).json()
        if resp:
            title = resp[0]['title']
            desc = resp[0].get('description', '')
            text = title + " " + desc
            score = analyzer.polarity_scores(text)['compound']
            return score, title[:100]
    except:
        pass
    return 0.0, "No news"

def get_price(symbol):
    try:
        data = yf.Ticker(symbol).info
        return data.get('regularMarketPrice') or data.get('previousClose')
    except:
        return None

def scan():
    results = []
    for sym in tickers:
        sentiment, news = get_tiingo_news(sym)
        price = get_price(sym)
        confidence = abs(sentiment) * 100
        decision = "BUY" if sentiment > 0.2 else "SELL" if sentiment < -0.2 else "HOLD"
        results.append({
            "Symbol": sym,
            "Decision": decision,
            "Confidence": round(confidence),
            "Sentiment": round(sentiment, 3),
            "Price": round(price, 2) if price else None,
            "TopNews": news
        })
    return pd.DataFrame(results)

df = scan()

# Metrics
cols = st.columns(4)
cols[0].metric("Tickers", len(df))
cols[1].metric("BUY", len(df[df.Decision=="BUY"]))
cols[2].metric("SELL", len(df[df.Decision=="SELL"]))
cols[3].metric("HOLD", len(df[df.Decision=="HOLD"]))

# Actionable
action = df[df.Decision != "HOLD"]
if not action.empty:
    st.subheader("🚀 Actionable Signals")
    st.dataframe(action, use_container_width=True)

st.subheader("Full Results")
st.dataframe(df, use_container_width=True)

# Charts
if not df.empty:
    st.plotly_chart(px.scatter(df, x="Sentiment", y="Confidence", color="Decision", hover_data=["Symbol", "Price"]))
    st.plotly_chart(px.bar(df, x="Symbol", y="Price", color="Decision"))

st.download_button("Download CSV", df.to_csv(index=False), "quantscout.csv")

if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()
