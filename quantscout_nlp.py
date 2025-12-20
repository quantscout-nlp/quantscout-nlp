import streamlit as st
import pandas as pd
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import yfinance as yf
import time
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

st.set_page_config(page_title="QuantScout Live Monitor", layout="wide")
st.title("🛡️ QuantScout NLP Institutional Live Monitor")

analyzer = SentimentIntensityAnalyzer()

# Secrets
try:
    ALPACA_KEY = st.secrets["api_keys"]["alpaca_key"]
    ALPACA_SECRET = st.secrets["api_keys"]["alpaca_secret"]
    TIINGO_KEY = st.secrets["api_keys"]["tiingo_key"]
    SENDGRID_KEY = st.secrets["api_keys"]["SENDGRID_API_KEY"]
    FROM_EMAIL = st.secrets["api_keys"]["FROM_EMAIL"]
    TO_EMAIL = st.secrets["api_keys"]["TO_EMAIL"]
except KeyError as e:
    st.error(f"Missing secret: {e}. Add in Settings > Secrets.")
    st.stop()

def send_email(df):
    actionable = df[df.Decision != "HOLD"]
    if actionable.empty:
        subject = "QuantScout: No Actionable Signals Today"
        body = "All holdings neutral — no strong BUY/SELL signals."
    else:
        subject = f"QuantScout ALERT: {len(actionable)} Actionable Signals!"
        body = actionable.to_html(index=False)
    
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject=subject,
        html_content=f"<h2>QuantScout Daily Report</h2>{body}"
    )
    try:
        sg = SendGridAPIClient(SENDGRID_KEY)
        sg.send(message)
        st.success("Email alert sent!")
    except Exception as e:
        st.error(f"Email failed: {e}")

# Sidebar
with st.sidebar:
    st.header("Controls")
    auto_refresh = st.checkbox("Auto Live Mode", value=True)
    refresh_sec = st.slider("Refresh (sec)", 5, 60, 10)
    user_tickers = st.text_input("Tickers", value="TSLA SNOW DUOL ORCL RDDT SHOP MU DASH ARM RKLB")
    if st.button("Send Email Alert Now"):
        df = scan()  # We'll define scan below
        send_email(df)

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
            return score, title[:100]
    except:
        pass
    return 0.0, "No news"

def get_price(symbol):
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.info
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

# Display same as before...
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
