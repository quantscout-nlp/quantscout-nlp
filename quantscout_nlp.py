import streamlit as st
import pandas as pd
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import yfinance as yf
import time

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
    TIINGO_KEY = st.secrets["api_keys"]["tiingo_key"]
    SENDGRID_KEY = st.secrets["api_keys"]["SENDGRID_API_KEY"]
    FROM_EMAIL = st.secrets["api_keys"]["FROM_EMAIL"]
    TO_EMAIL = st.secrets["api_keys"]["TO_EMAIL"]
    TELEGRAM_TOKEN = st.secrets["api_keys"]["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_CHAT_ID = st.secrets["api_keys"]["TELEGRAM_CHAT_ID"]
except KeyError as e:
    st.error(f"Missing secret: {e}")
    st.stop()

def send_email(df):
    actionable = df[df.Decision != "HOLD"]
    if actionable.empty:
        subject = "QuantScout: No Actionable Signals"
        body = "<p>All neutral — monitoring continues.</p>"
    else:
        subject = f"QuantScout ALERT: {len(actionable)} Signals!"
        body = actionable.to_html(index=False, border=0, classes="table table-striped")
    
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject=subject,
        html_content=f"<h2>QuantScout Report - {time.strftime('%Y-%m-%d %H:%M')}</h2>{body}"
    )
    try:
        sg = SendGridAPIClient(SENDGRID_KEY)
        sg.send(message)
        st.success("📧 Email alert sent to quantradingsystem@gmail.com!")
    except Exception as e:
        st.error(f"Email failed: {e}")

# Telegram send function (keep your existing)
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

with st.sidebar:
    st.header("Controls")
    auto_refresh = st.checkbox("Auto Live Mode", value=True)
    refresh_sec = st.slider("Refresh (sec)", 5, 60, 10)
    user_tickers = st.text_input("Tickers", value="TSLA SNOW DUOL ORCL RDDT SHOP MU DASH ARM RKLB")
    
    st.markdown("---")
    if st.button("📧 Send Email Alert Now"):
        current_df = scan()
        send_email(current_df)

# Rest of your scan code (get_tiingo_news, get_price, scan function) remains the same

df = scan()

# Alerts (both Telegram + Email on strong signals)
strong = df[(df.Decision != "HOLD") & (df.Confidence >= 60)]
if not strong.empty and "alert_sent" not in st.session_state:
    # Telegram
    msg = "🚨 <b>QuantScout Strong Signals!</b>\n\n"
    for _, row in strong.iterrows():
        msg += f"• <b>{row.Decision}</b> {row.Symbol} ({row.Confidence}% conf)\n"
        msg += f"   Sentiment: {row.Sentiment} | Price: ${row.Price}\n"
        msg += f"   {row.TopNews}\n\n"
    send_telegram(msg)
    
    # Email
    send_email(df)
    
    st.session_state.alert_sent = True
elif strong.empty:
    st.session_state.alert_sent = False

# Display dashboard same as before...

if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()
