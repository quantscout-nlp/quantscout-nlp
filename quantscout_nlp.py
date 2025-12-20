import streamlit as st
import pandas as pd
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import time

# Alpaca real-time
from alpaca.data import StockHistoricalDataClient, StockLatestQuoteRequest

# SendGrid email
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except ImportError:
    st.error("sendgrid package missing — add 'sendgrid' to requirements.txt")
    st.stop()

st.set_page_config(page_title="QuantScout Live Monitor", layout="wide")
st.title("🛡️ QuantScout NLP Institutional Live Monitor")

analyzer = SentimentIntensityAnalyzer()

# Secrets
try:
    ALPACA_KEY = st.secrets["api_keys"]["alpaca_key"]
    ALPACA_SECRET = st.secrets["api_keys"]["alpaca_secret"]
    TIINGO_KEY = st.secrets["api_keys"]["tiingo_key"]
    TELEGRAM_TOKEN = st.secrets["api_keys"]["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_CHAT_ID = st.secrets["api_keys"]["TELEGRAM_CHAT_ID"]
    SENDGRID_KEY = st.secrets["api_keys"].get("SENDGRID_API_KEY")
    FROM_EMAIL = st.secrets["api_keys"].get("FROM_EMAIL", "alerts@quantscout.app")
    TO_EMAIL = st.secrets["api_keys"].get("TO_EMAIL", "quantradingsystem@gmail.com")
except KeyError as e:
    st.error(f"Missing secret: {e}. Add in Settings > Secrets.")
    st.stop()

# Alpaca client
stock_client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

def send_email(df):
    if SENDGRID_KEY is None:
        return
    actionable = df[df.Decision != "HOLD"]
    if actionable.empty:
        subject = "QuantScout: No Actionable Signals"
        body = "<p>All neutral — monitoring continues.</p>"
    else:
        subject = f"QuantScout ALERT: {len(actionable)} Signals!"
        body = actionable.to_html(index=False, border=0)
    
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject=subject,
        html_content=f"<h2>QuantScout Report</h2>{body}"
    )
    try:
        sg = SendGridAPIClient(SENDGRID_KEY)
        sg.send(message)
        st.success("📧 Email alert sent!")
    except Exception as e:
        st.warning(f"Email failed: {e}")

with st.sidebar:
    st.header("Controls")
    auto_refresh = st.checkbox("Auto Live Mode", value=True)
    refresh_sec = st.slider("Refresh (sec)", 5, 60, 10)
    user_tickers = st.text_input("Tickers", value="TSLA SNOW DUOL ORCL RDDT SHOP MU DASH ARM RKLB")
    
    st.markdown("---")
    if st.button("📧 Send Email Alert Now"):
        current_df = scan()
        send_email(current_df)

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

def get_alpaca_price(symbol):
    try:
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = stock_client.get_stock_latest_quote(request)[symbol]
        if quote.bid_price and quote.ask_price and quote.bid_price > 0 and quote.ask_price > 0:
            return (quote.bid_price + quote.ask_price) / 2
        return quote.ask_price or quote.bid_price or quote.last_price
    except:
        return None

def get_price(symbol):
    price = get_alpaca_price(symbol)
    if price:
        return price
    # Fallback yFinance
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

# MAIN SCAN — CORRECT INDENTATION
df = scan()

# Alerts
strong = df[(df.Decision != "HOLD") & (df.Confidence >= 60)]
if not strong.empty and "alert_sent" not in st.session_state:
    msg = "🚨 <b>QuantScout Strong Signals!</b>\n\n"
    for _, row in strong.iterrows():
        msg += f"• <b>{row.Decision}</b> {row.Symbol} ({row.Confidence}% conf)\n"
        msg += f"   Sentiment: {row.Sentiment} | Price: ${row.Price}\n"
        msg += f"   {row.TopNews}\n\n"
    send_telegram(msg)
    send_email(df)
    st.session_state.alert_sent = True
elif strong.empty:
    st.session_state.alert_sent = False

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
