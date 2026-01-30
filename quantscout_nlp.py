# -*- coding: utf-8 -*-
"""
QuantScout PRO TERMINAL (v5.5 - AUTO-PILOT EDITION)
Location: D:\AdvNLP&AEGIS_Model_LIVE\quantscout_nlp.py
"""

from __future__ import annotations
import os
import time
import json
import requests
import pandas as pd
import streamlit as st
import yfinance as yf
from datetime import datetime
from typing import Any, Dict, Optional

# --- GOOGLE SHEETS AUTOMATION IMPORTS ---
import gspread
from google.oauth2.service_account import Credentials
from GoogleNews import GoogleNews

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="QuantScout PRO",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="🦅"
)

# --- CLOUD BRIDGE IMPORT ---
try:
    from QC_CloudBridge import CloudBridge
except ImportError:
    CloudBridge = None

# ==========================================
# 👇 YOUR KEYS ARE PRE-LOADED 👇
# ==========================================
MY_ALPACA_ID     = "AKMQPW0T4F3BMRVA25VB"   
MY_ALPACA_SECRET = "QPSlZIJcV0S8vwc7GWB45Vorz527M5rEjhpzb4qi"   
MY_MASSIVE_KEY   = "WPJc08p6Nqp39W05pBkNY6685DL2cqlc"   
MY_TIINGO_KEY    = "bf96558968e66c6dbfa2d914b0370212b2b8a771"

# --- TELEGRAM KEYS ---
MY_TELEGRAM_TOKEN = "8585376142:AAFSk6JwHDtzCqYUvLRPCxm1N3_VZJHjdIw" 
MY_TELEGRAM_ID    = "8079429250"

# --- GOOGLE SHEET CONFIG ---
SHEET_ID = "1ivrGM18FHdP2Ky6XVBl4n1AoHlSbS-3I0AxS3JrkeBg"
# ==========================================

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except Exception:
    SentimentIntensityAnalyzer = None

# =========================
# Utils
# =========================
SESSION = requests.Session()
SESSION.headers.update({"user-agent": "QuantScoutEngine/5.5"})

def to_float(x: Any) -> Optional[float]:
    try: return float(x) if x is not None else None
    except: return None

def http_get_json(url: str, headers: Optional[Dict]=None, params: Optional[Dict]=None, timeout: float=5.0):
    try:
        r = SESSION.get(url, headers=headers, params=params, timeout=timeout)
        if r.status_code >= 400: return r.status_code, None, r.text[:200]
        return r.status_code, r.json(), ""
    except Exception as e:
        return 0, None, str(e)[:200]

def get_key(hardcoded, env_names):
    if hardcoded and "PASTE" not in hardcoded and hardcoded.strip(): return hardcoded.strip()
    for n in env_names:
        v = os.getenv(n)
        if v and str(v).strip(): return str(v).strip()
    return ""

def send_telegram_alert(message):
    if "PASTE" in MY_TELEGRAM_TOKEN or "PASTE" in MY_TELEGRAM_ID:
        return 
    url = f"https://api.telegram.org/bot{MY_TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": MY_TELEGRAM_ID, "text": message}
    try: requests.post(url, json=payload, timeout=3)
    except: pass

# =========================
# Google Sheets "Robot Arm"
# =========================
def auto_update_sheet(buy_list):
    """Writes BUY signals directly to Google Drive for QuantConnect to pick up."""
    try:
        # 1. Authenticate using Streamlit Secrets
        if "gcp_service_account" not in st.secrets:
            # Silently fail on local dev, alert on cloud
            return

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(credentials)

        # 2. Open Sheet
        sh = client.open_by_key(SHEET_ID)
        worksheet = sh.sheet1 

        # 3. Clear & Write
        worksheet.clear() 
        worksheet.update('A1', [['ticker', 'signal']]) # Header
        
        # Prepare Data
        data_rows = [[ticker, 'BUY'] for ticker in buy_list]
        
        if data_rows:
            worksheet.update('A2', data_rows)
            msg = f"🚀 BRIDGE UPDATED: Sent {len(data_rows)} tickers to AEGIS Bot!"
            st.toast(msg)
            # Only log success to UI if manually triggered to avoid loop spam
            if not st.session_state.get('auto_pilot_active', False):
                st.success(msg)
        else:
            if not st.session_state.get('auto_pilot_active', False):
                st.warning("⚠️ Bridge Cleared (No Buy Signals).")
            
    except Exception as e:
        # Don't crash the app, just show a small error
        st.toast(f"❌ Bridge Error: {str(e)[:50]}")

# =========================
# Fetchers
# =========================
def fetch_alpaca_price(symbol, kid, sec):
    if not kid or not sec: return None, "No Keys"
    h = {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}
    sc, j, e = http_get_json(f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest", headers=h)
    if j and isinstance(j, dict) and "trade" in j and j["trade"]: 
        return to_float(j["trade"]["p"]), "Alpaca"
    return None, e

def fetch_polygon_price(symbol, key):
    if not key: return None, "No Key"
    sc, j, e = http_get_json(f"https://api.polygon.io/v2/last/trade/{symbol}", params={"apiKey": key})
    if j and isinstance(j, dict) and "results" in j and j["results"]: 
        return to_float(j["results"]["p"]), "Polygon"
    return None, e

@st.cache_data(ttl=60)
def fetch_indicators_hybrid(symbol, kid, sec):
    rsi, sma20 = 0.0, 0.0
    # 1. Try Alpaca
    if kid and sec:
        h = {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}
        params = {"timeframe": "1Day", "limit": 50, "feed": "iex"} 
        sc, j, e = http_get_json(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars", headers=h, params=params)
        if j and isinstance(j, dict) and "bars" in j and j["bars"]:
            bars = j["bars"]
            if len(bars) > 20:
                closes = pd.Series([b["c"] for b in bars])
                delta = closes.diff()
                up, down = delta.clip(lower=0), -delta.clip(upper=0)
                rs = up.ewm(alpha=1/14).mean() / down.ewm(alpha=1/14).mean()
                rsi = 100 - (100/(1+rs)).iloc[-1]
                sma20 = closes.rolling(20).mean().iloc[-1]
                return float(sma20), float(rsi), ""
    # 2. Fallback Yahoo
    try:
        hist = yf.Ticker(symbol).history(period="3mo")
        if not hist.empty and len(hist) > 20:
            closes = hist["Close"]
            delta = closes.diff()
            up, down = delta.clip(lower=0), -delta.clip(upper=0)
            rs = up.ewm(alpha=1/14).mean() / down.ewm(alpha=1/14).mean()
            rsi = 100 - (100/(1+rs)).iloc[-1]
            sma20 = closes.rolling(20).mean().iloc[-1]
            return float(sma20), float(rsi), ""
    except:
        pass
    return 0.0, 0.0, "No Data"

def fetch_news_hybrid(symbol, t_key):
    if not SentimentIntensityAnalyzer: return 0.0, "VADER Missing"
    analyzer = SentimentIntensityAnalyzer()
    
    # 1. Tiingo
    if t_key:
        sc, j, e = http_get_json("https://api.tiingo.com/tiingo/news", params={"tickers":symbol,"limit":1,"token":t_key})
        if j and isinstance(j, list) and len(j) > 0:
            title = j[0].get("title", "")
            score = analyzer.polarity_scores(title).get("compound", 0.0)
            return score, f"[Tiingo] {title}"
    # 2. Yahoo
    try:
        yf_tick = yf.Ticker(symbol)
        news_list = yf_tick.news
        if news_list and len(news_list) > 0:
            latest = news_list[0]
            title = latest.get("title", "")
            score = analyzer.polarity_scores(title).get("compound", 0.0)
            return score, f"[Yahoo] {title}"
    except: pass
    # 3. Google
    try:
        goog = GoogleNews(lang='en', period='1d')
        goog.search(f"{symbol} stock news")
        results = goog.result()
        if results and len(results) > 0:
            title = results[0].get("title", "")
            score = analyzer.polarity_scores(title).get("compound", 0.0)
            return score, f"[Google] {title}"
    except: pass

    return 0.0, "No Data"

# =========================
# UI Logic
# =========================
st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    div[data-testid="stSidebar"] { background-color: #262730; }
    .stMetric { background-color: #1f2937; padding: 10px; border-radius: 5px; border: 1px solid #374151; }
</style>
""", unsafe_allow_html=True)

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ SYSTEM CONTROL")
    
    with st.expander("🔑 API KEYS", expanded=False):
        alpaca_id = st.text_input("Alpaca ID", value=get_key(MY_ALPACA_ID, ["APCA_API_KEY_ID"]), type="password")
        alpaca_secret = st.text_input("Alpaca Secret", value=get_key(MY_ALPACA_SECRET, ["APCA_API_SECRET_KEY"]), type="password")
        polygon_key = st.text_input("Polygon Key", value=get_key(MY_MASSIVE_KEY, ["POLYGON_API_KEY"]), type="password")
        tiingo_key = st.text_input("Tiingo Key", value=get_key(MY_TIINGO_KEY, ["TIINGO_API_KEY"]), type="password")
    
    st.subheader("🤖 AUTO-PILOT")
    # THE CRITICAL CHECKBOX
    enable_autopilot = st.checkbox("FULL AUTO-PILOT (Write to Drive)", value=False, help="If checked, BUY signals are sent to the Bot automatically every minute.")
    if enable_autopilot:
        st.session_state['auto_pilot_active'] = True
        st.success("AUTONOMOUS MODE: ON")
    else:
        st.session_state['auto_pilot_active'] = False
        st.warning("MANUAL MODE: Wait for Button")

    st.subheader("📡 WATCHLIST")
    default_tickers = "TSLA, SNOW, DUOL, ORCL, RDDT, PLTR, CRWV, VST, AMD, AMAT, LYFT, SMCI, LEU, OKLO, OPEN, QS, MU, CRWD, LUNR, SOC, RKLB, ARM, HOOD, COIN, SHOP, SOFI, UBER, DASH, CCJ, TEM, RGTI, IBIT, MRVL, INTC, RIVN, MU, TSM, WULF, ASM, MRVL, HPE, SMR, UEC, FIG, NXE"
    tickers_txt = st.text_area("Symbols (CSV)", value=default_tickers, height=300)
    
    st.markdown("---")
    
    # Initialize Session State for 'Running'
    if 'is_running' not in st.session_state:
        st.session_state['is_running'] = False

    if not st.session_state['is_running']:
        if st.button("🟢 START SYSTEM (Always-On)", use_container_width=True):
            st.session_state['is_running'] = True
            st.rerun()
    else:
        st.success("SYSTEM ARMED & RUNNING")
        if st.button("🔴 STOP SYSTEM", use_container_width=True):
            st.session_state['is_running'] = False
            st.rerun()

# --- MAIN SCREEN ---
st.title("🦅 QUANTSCOUT PRO TERMINAL")
st.caption(f"Live Connection: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Mode: Institutional")

# Check if running
if st.session_state['is_running']:
    tickers = [t.strip().upper() for t in tickers_txt.split(",") if t.strip()]
    rows = []
    current_buys = []
    
    # No Progress Bar needed for Loop to avoid flickering
    with st.spinner("Scanning Market..."):
        for i, sym in enumerate(tickers):
            try:
                # Init
                price, src, sma20, rsi, sent = 0.0, "Wait", 0.0, 0.0, 0.0
                headline = ""
                decision = "HOLD"
                conf = 0.0

                # 1. Price
                price, src = fetch_alpaca_price(sym, alpaca_id, alpaca_secret)
                if not price: price, src = fetch_polygon_price(sym, polygon_key)
                
                # 2. Indicators (HYBRID)
                sma20, rsi, i_err = fetch_indicators_hybrid(sym, alpaca_id, alpaca_secret)
                
                # 3. Sentiment
                sent, headline = fetch_news_hybrid(sym, tiingo_key)
                
                # Logic
                if price and rsi > 0:
                    if price > sma20 and rsi < 70 and sent > 0.15:
                        decision = "BUY"
                        conf = 0.8 + (sent * 0.1)
                        current_buys.append(sym)
                    elif price < sma20 and rsi > 30 and sent < -0.2:
                        decision = "SELL"
                        conf = 0.8
                    elif rsi < 35: 
                        decision = "BUY"
                        conf = 0.5
                        current_buys.append(sym)

                # --- ALERTING LOGIC ---
                if decision != "HOLD":
                    # Telegram Push (Anti-Spam Cache)
                    alert_key = f"{sym}_{decision}_{datetime.now().strftime('%H:%M')}"
                    if alert_key not in st.session_state:
                        msg = f"🦅 <b>QUANTSCOUT SIGNAL</b>\n\n🔹 <b>{decision} {sym}</b>\n💵 Price: ${price}\n📊 RSI: {rsi:.1f}\n🗞️ News: {headline}"
                        send_telegram_alert(msg)
                        st.session_state[alert_key] = True

                rows.append({
                    "TICKER": sym, "PRICE": price, 
                    "RSI": round(rsi,1) if rsi else 0,
                    "SENTIMENT": round(sent,2), 
                    "SIGNAL": decision,
                    "CONF": round(conf,2), 
                    "NEWS": headline
                })
            except Exception as e:
                rows.append({"TICKER": sym, "SIGNAL": "ERR", "NEWS": str(e)})

    # Display Stats
    if rows:
        df = pd.DataFrame(rows)
        
        buys = len(df[df["SIGNAL"] == "BUY"])
        sells = len(df[df["SIGNAL"] == "SELL"])
        avg_rsi = df["RSI"].mean() if "RSI" in df.columns else 0
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Active Tickers", len(tickers))
        m2.metric("Buy Signals", buys)
        m3.metric("Sell Signals", sells)
        m4.metric("Market RSI (Avg)", round(avg_rsi, 1))

        # --- AUTO-PILOT EXECUTION ---
        st.markdown("---")
        if enable_autopilot:
            st.info(f"🤖 AUTO-PILOT: Sending {len(current_buys)} signals to Bridge...")
            auto_update_sheet(current_buys)
        else:
            # Manual Push Button
            c1, c2 = st.columns([3, 1])
            with c1:
                st.write(f"**Pending Buys:** {current_buys}")
            with c2:
                if st.button("🚀 PUSH TO BRIDGE", type="primary", use_container_width=True):
                    auto_update_sheet(current_buys)

        st.markdown("---")

        c_left, c_right = st.columns([2.5, 1])
        
        with c_left:
            st.subheader("MARKET SCANNER")
            def color_signal(val):
                color = ''
                if val == 'BUY': color = 'background-color: #1b4d3e; color: white'
                elif val == 'SELL': color = 'background-color: #4d1b1b; color: white'
                return color

            st.dataframe(
                df.style.applymap(color_signal, subset=['SIGNAL']),
                use_container_width=True,
                height=600
            )

        with c_right:
            st.subheader("ACTION LOG")
            actionable = df[df["SIGNAL"] != "HOLD"]
            if not actionable.empty:
                for index, row in actionable.iterrows():
                    if row["SIGNAL"] == "BUY":
                        st.success(f"🟢 **BUY {row['TICKER']}**")
                    else:
                        st.error(f"🔴 **SELL {row['TICKER']}**")
                    st.caption(f"Price: {row['PRICE']} | RSI: {row['RSI']}")
                    st.caption(f"News: {row['NEWS'][:60]}...")
                    st.markdown("---")
            else:
                st.info("Scanning for setups...")

    # HARDCODED AUTO REFRESH LOOP
    time.sleep(60) # Wait 60 seconds
    st.rerun()     # Restart immediately
else:
    st.info("System Standby. Click START in the sidebar to begin.")
