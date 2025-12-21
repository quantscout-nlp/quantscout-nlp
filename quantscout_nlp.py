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
    TIINGO_KEY = st.secrets["api_keys"]["tiingo_key"]
    TELEGRAM_TOKEN = st.secrets["api_keys"]["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_CHAT_ID = st.secrets["api_keys"]["TELEGRAM_CHAT_ID"]
except KeyError as e:
    st.error(f"Missing secret: {e}")
    st.stop()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass  # Silent fail if offline

with st.sidebar:
    st.header("Controls")
    auto_refresh = st.checkbox("Auto Live Mode", value=True)
    refresh_sec = st.slider("Refresh (sec)", 5, 60, 10)
    user_tickers = st.text_input("Tickers", value="TSLA SNOW DUOL ORCL RDDT SHOP MU DASH ARM RKLB")

tickers = [t.strip().upper() for t in user_tickers.replace(",", " ").split() if t.strip()]

def get_tiingo_news(symbol):
    url = f"https://api.tiingo.com/tiingo/news?tickers={symbol}&token={TIINGO_KEY}"
    try:
        resp = requests.get(url, timeout=10).json()
        if resp:
            title = resp[0]['title']
            desc = resp[0].get('description', '')
            text = title + " " + desc
            score = analyzer.polarity_scores(text)['compound']
            return score, title[:120]
    except:
        pass
    return 0.0, "No news"

def get_price(symbol):
    try:
        data = yf.Ticker(symbol).info
        return data.get('regularMarketPrice') or data.get('currentPrice') or data.get('previousClose')
    except:
        return None

def scan():
    results = []
    for sym in tickers:
        sentiment, news = get_tiingo_news(sym)
        price = get_price(sym)
        confidence = int(abs(sentiment) * 100)
        decision = "BUY" if sentiment > 0.2 else "SELL" if sentiment < -0.2 else "HOLD"
        results.append({
            "Symbol": sym,
            "Decision": decision,
            "Confidence": confidence,
            "Sentiment": round(sentiment, 3),
            "Price": round(price, 2) if price else "N/A",
            "TopNews": news
        })
    return pd.DataFrame(results)

df = scan()

# Send Telegram Alert on Strong Signals
strong_signals = df[(df.Decision != "HOLD") & (df.Confidence >= 60)]
if not strong_signals.empty and "last_alert" not in st.session_state:
    alert_msg = "🚨 <b>QuantScout Strong Signals!</b>\n\n"
    for _, row in strong_signals.iterrows():
        alert_msg += f"• <b>{row.Decision}</b> {row.Symbol} ({row.Confidence}% conf)\n"
        alert_msg += f"   Sentiment: {row.Sentiment} | Price: ${row.Price}\n"
        alert_msg += f"   {row.TopNews}\n\n"
    send_telegram(alert_msg)
    st.session_state.last_alert = True
elif strong_signals.empty:
    st.session_state.last_alert = False  # Reset if no signals

# Display
cols = st.columns(4)
cols[0].metric("Tickers", len(df))
cols[1].metric("BUY", len(df[df.Decision=="BUY"]))
cols[2].metric("SELL", len(df[df.Decision=="SELL"]))
cols[3].metric("HOLD", len(df[df.Decision=="HOLD"]))

action = df[df.Decision != "HOLD"]
if not action.empty:
    st.subheader("🚀 Actionable Signals")
    st.dataframe(action, use_container_width=True)

st.subheader("Full Results")
st.dataframe(df, use_container_width=True)

st.download_button("Download CSV", df.to_csv(index=False), "quantscout_signals.csv")

if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()
