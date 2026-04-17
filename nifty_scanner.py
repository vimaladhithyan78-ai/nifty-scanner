"""
NIFTY 50 + BANK NIFTY SNIPER SCANNER
- Render.com 24/7 version with Flask
- Capital: Rs.25000 | Risk: 2% per trade = Rs.500
- Intraday MIS 5x leverage = Rs.25,000 buying power
- Bull/Bear Score 7 conditions
- Direct Entry (6-7/7) + Pullback Entry (5/7)
- Dynamic ATR (1.0 or 1.5) based on ADX
- TP1 (50% exit) + TP2 (full close)
- Position sizing per signal
- Telegram alerts with TradingView link
"""

import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
import os
import json
import pandas as pd
import requests
import schedule
import time
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask


# ══════════════════════════════════════════════════════
#  IST TIMEZONE  (UTC + 5:30)
# ══════════════════════════════════════════════════════
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

# ══════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ══════════════════════════════════════════════════════
#  CAPITAL & RISK
# ══════════════════════════════════════════════════════
CAPITAL           = 25000    # Rs.25,000 your capital
RISK_PERCENT      = 2.0      # 2% risk per trade
LEVERAGE          = 5        # MIS intraday 5x
RISK_AMOUNT       = CAPITAL * (RISK_PERCENT / 100)  # Rs.500 max loss per trade
BUYING_POWER      = CAPITAL * LEVERAGE              # Rs.1,25,000 buying power

# ══════════════════════════════════════════════════════
#  SCANNER SETTINGS
# ══════════════════════════════════════════════════════
ATR_MULTIPLIER     = 1.5
SCAN_INTERVAL      = 15
TIMEFRAME          = "15m"
PERIOD             = "3d"   # reduced from 5d to save memory
DIRECT_ENTRY_SCORE = 6
PULLBACK_SCORE     = 5

# ══════════════════════════════════════════════════════
#  45 STOCKS
# ══════════════════════════════════════════════════════
# Profitable stocks only — based on 15 day performance analysis
# Removed 17 losing stocks: ADANIPORTS, MM, SAIL, DRREDDY, AXISBANK etc
STOCKS = {
    # ── TOP PERFORMERS ──────────────────
    "HDFCLIFE":   "HDFCLIFE.NS",    # W:3 L:0 +Rs.3120 ⭐
    "SBIN":       "SBIN.NS",        # W:3 L:2 +Rs.2770 ⭐
    "NTPC":       "NTPC.NS",        # W:2 L:0 +Rs.1947 ⭐
    "TATACONSUM": "TATACONSUM.NS",  # W:3 L:3 +Rs.1799
    "ICICIBANK":  "ICICIBANK.NS",   # W:3 L:1 +Rs.1593 ⭐
    "BAJAJFINSV": "BAJAJFINSV.NS",  # W:3 L:1 +Rs.1353
    "PNB":        "PNB.NS",         # W:4 L:3 +Rs.1310
    "FEDERALBNK": "FEDERALBNK.NS",  # W:2 L:2 +Rs.1164
    "ITC":        "ITC.NS",         # W:1 L:1 +Rs.1164
    "HDFCBANK":   "HDFCBANK.NS",    # W:2 L:1 +Rs.1106
    # ── GOOD PERFORMERS ─────────────────
    "TATASTEEL":  "TATASTEEL.NS",   # W:2 L:2 +Rs.905
    "KOTAKBANK":  "KOTAKBANK.NS",   # W:2 L:1 +Rs.859
    "CIPLA":      "CIPLA.NS",       # W:2 L:1 +Rs.847
    "BAJFINANCE": "BAJFINANCE.NS",  # W:2 L:2 +Rs.698
    "RELIANCE":   "RELIANCE.NS",    # W:2 L:1 +Rs.487
    "POWERGRID":  "POWERGRID.NS",   # W:2 L:2 +Rs.347
    "INFY":       "INFY.NS",        # W:1 L:2 +Rs.323
    "IOC":        "IOC.NS",         # W:2 L:2 +Rs.266
    "IDFCFIRSTB": "IDFCFIRSTB.NS",  # W:2 L:2 +Rs.256
    "TATAPOWER":  "TATAPOWER.NS",   # W:2 L:2 +Rs.250
    "BANKBARODA": "BANKBARODA.NS",  # W:2 L:1 +Rs.202
    "HINDALCO":   "HINDALCO.NS",    # W:2 L:1 +Rs.165
    "SUNPHARMA":  "SUNPHARMA.NS",   # W:2 L:2 +Rs.148
    "TECHM":      "TECHM.NS",       # W:2 L:2 +Rs.131
    "WIPRO":      "WIPRO.NS",       # W:3 L:2 +Rs.79
    "COALINDIA":  "COALINDIA.NS",   # W:1 L:0 +Rs.37
}

# ══════════════════════════════════════════════════════
#  POSITION SIZING
# ══════════════════════════════════════════════════════
def calc_position(entry: float, sl: float) -> dict:
    risk_per_share = abs(entry - sl)
    if risk_per_share <= 0:
        return {"qty": 0, "capital_needed": 0, "max_loss": 0, "feasible": False, "note": "Invalid SL"}

    qty = int(RISK_AMOUNT / risk_per_share)

    if qty < 1:
        qty = 1

    capital_needed = round(qty * entry, 2)
    max_loss       = round(qty * risk_per_share, 2)
    feasible       = capital_needed <= BUYING_POWER

    note = "✅ OK" if feasible else "⚠️ Exceeds buying power"

    return {
        "qty":            qty,
        "capital_needed": capital_needed,
        "max_loss":       max_loss,
        "feasible":       feasible,
        "note":           note,
    }


# ══════════════════════════════════════════════════════
#  GOOGLE SHEETS SETUP
# ══════════════════════════════════════════════════════
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "1x5SyEDwj3OBBQRgblUffhqX2feB0jU4bkCE4SnnvnP4")

def get_sheet():
    try:
        creds_dict = {
            "type": "service_account",
            "project_id":     os.environ.get("GOOGLE_PROJECT_ID", ""),
            "private_key_id": os.environ.get("GOOGLE_PRIVATE_KEY_ID", ""),
            "private_key":    os.environ.get("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n"),
            "client_email":   os.environ.get("GOOGLE_CLIENT_EMAIL", ""),
            "client_id":      os.environ.get("GOOGLE_CLIENT_ID", ""),
            "auth_uri":       "https://accounts.google.com/o/oauth2/auth",
            "token_uri":      "https://oauth2.googleapis.com/token",
        }
        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds  = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet  = client.open_by_key(SHEET_ID).sheet1
        return sheet
    except Exception as e:
        print(f"  ❌ Google Sheets error: {e}")
        return None

def log_to_sheet(sig: dict):
    try:
        sheet = get_sheet()
        if sheet is None:
            return

        # Add header if sheet is empty
        existing = sheet.get_all_values()
        if not existing or len(existing) == 0:
            sheet.append_row([
                "Date", "Time", "Stock",
                "Entry", "SL", "TP1", "TP2", "RR",
                "Score", "ADX", "Qty",
                "TP Hit", "SL Hit", "Result", "P&L (Rs)"
            ])
        elif existing[0][0] != "Date":
            sheet.insert_row([
                "Date", "Time", "Stock",
                "Entry", "SL", "TP1", "TP2", "RR",
                "Score", "ADX", "Qty",
                "TP Hit", "SL Hit", "Result", "P&L (Rs)"
            ], 1)

        now = now_ist()

        # Calculate RR ratio
        risk   = abs(sig["price"] - sig["sl"])
        reward = abs(sig["tp1"]   - sig["price"])
        rr     = round(reward / risk, 2) if risk > 0 else 0

        # Stock name with BUY/SELL indicator
        stock_name = sig["name"] + (" 🟢" if sig["direction"] == "BUY" else " 🔴")

        row = [
            now.strftime("%d-%b-%Y"),
            now.strftime("%H:%M:%S"),
            stock_name,
            sig["price"],
            sig["sl"],
            sig["tp1"],
            sig["tp2"],
            f"1:{rr}",
            f"{sig['score']}/7",
            sig["adx"],
            sig["qty"],
            "",      # TP Hit
            "",      # SL Hit
            "OPEN",  # Result
            "",      # P&L
        ]
        sheet.append_row(row)
        sig["sheet_row"] = len(sheet.get_all_values())
        print("  ✅ Logged to Google Sheets!")
    except Exception as e:
        print(f"  ❌ Sheet log error: {e}")

# ══════════════════════════════════════════════════════
#  ALERT MEMORY
# ══════════════════════════════════════════════════════
alerted_today    = {}
pullback_waiting = {}
last_signal_state = {}  # tracks last signal per stock: 1=BUY, -1=SELL, 0=none

# Active trades tracking for TP/SL monitoring
active_trades = {}  # name -> {direction, entry, sl, tp1, tp2, qty, t1hit, t2hit}

def reset_alerts():
    global alerted_today, pullback_waiting, last_signal_state
    alerted_today     = {}
    pullback_waiting  = {}
    last_signal_state = {}
    print("🔄 Alert memory reset.")

# ══════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("  ✅ Telegram sent!")
        else:
            print(f"  ❌ Telegram error: {r.text}")
    except Exception as e:
        print(f"  ❌ Exception: {e}")

# ══════════════════════════════════════════════════════
#  INDICATORS
# ══════════════════════════════════════════════════════
def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_atr(df, period=14):
    h  = df["High"]
    l  = df["Low"]
    c  = df["Close"]
    p  = c.shift(1)
    tr = pd.concat([h - l, (h - p).abs(), (l - p).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def calc_rsi(series, period=14):
    d = series.diff()
    g = d.clip(lower=0).ewm(span=period, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(span=period, adjust=False).mean()
    return 100 - (100 / (1 + g / l))

def calc_macd(series):
    m = calc_ema(series, 12) - calc_ema(series, 26)
    s = calc_ema(m, 9)
    return m, s

def calc_vwap(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    return (tp * df["Volume"]).cumsum() / df["Volume"].cumsum()

def calc_adx(df, period=14):
    h  = df["High"]
    l  = df["Low"]
    c  = df["Close"]
    ph = h.shift(1)
    pl = l.shift(1)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    dm_plus  = (h - ph).where((h - ph) > (pl - l), 0.0).clip(lower=0)
    dm_minus = (pl - l).where((pl - l) > (h - ph), 0.0).clip(lower=0)
    atr14    = tr.ewm(span=period, adjust=False).mean()
    di_plus  = 100 * dm_plus.ewm(span=period, adjust=False).mean() / atr14
    di_minus = 100 * dm_minus.ewm(span=period, adjust=False).mean() / atr14
    dx       = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10))
    return dx.ewm(span=period, adjust=False).mean()

# ══════════════════════════════════════════════════════
#  SCAN ONE STOCK
# ══════════════════════════════════════════════════════
def scan_stock(name: str, ticker: str):
    try:
        # Retry up to 3 times if rate limited
        df = None
        for attempt in range(2):
            try:
                df = yf.download(ticker, period=PERIOD, interval=TIMEFRAME,
                                 progress=False, auto_adjust=True)
                if df is not None and len(df) >= 30:
                    break
                time.sleep(1)
            except Exception:
                time.sleep(2)
        if df is None or len(df) < 30:
            return None
        try:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
        except Exception:
            return None
        # Verify required columns exist
        required = ["Close", "High", "Low", "Open", "Volume"]
        if not all(col in df.columns for col in required):
            return None

        df5 = None
        for attempt in range(2):
            try:
                df5 = yf.download(ticker, period="1d", interval="5m",
                                  progress=False, auto_adjust=True)
                if df5 is not None and len(df5) >= 14:
                    break
                time.sleep(1)
            except Exception:
                time.sleep(2)
        if df5 is None or len(df5) < 5:
            df5 = pd.DataFrame()
        else:
            try:
                if isinstance(df5.columns, pd.MultiIndex):
                    df5.columns = df5.columns.get_level_values(0)
            except Exception:
                df5 = pd.DataFrame()

        close = df["Close"]
        vol   = df["Volume"]

        e9      = calc_ema(close, 9)
        e21     = calc_ema(close, 21)
        atr14   = calc_atr(df, 14)
        rsi14   = calc_rsi(close, 14)
        vwap_   = calc_vwap(df)
        macd_, msig_ = calc_macd(close)
        adx_    = calc_adx(df, 14)
        vol_avg = vol.rolling(20).mean()
        rsi5m   = calc_rsi(df5["Close"], 14) if len(df5) >= 14 else None

        cl      = float(close.iloc[-1])
        op      = float(df["Open"].iloc[-1])
        e9_c    = float(e9.iloc[-1])
        e9_p    = float(e9.iloc[-2])
        e21_c   = float(e21.iloc[-1])
        e21_p   = float(e21.iloc[-2])
        atr_c   = float(atr14.iloc[-1])
        rsi_c   = float(rsi14.iloc[-1])
        vwap_c  = float(vwap_.iloc[-1])
        macd_c  = float(macd_.iloc[-1])
        msig_c  = float(msig_.iloc[-1])
        adx_c   = float(adx_.iloc[-1])
        vol_c   = float(vol.iloc[-1])
        volav_c = float(vol_avg.iloc[-1])
        rsi5_c  = float(rsi5m.iloc[-1]) if rsi5m is not None and len(rsi5m) > 0 else 50.0

        buy_cross     = (e9_p <= e21_p) and (e9_c > e21_c)
        sell_cross    = (e9_p >= e21_p) and (e9_c < e21_c)
        buy_pullback  = (e9_c > e21_c) and (float(df["Low"].iloc[-1]) <= e9_c) and (cl > e21_c)
        sell_pullback = (e9_c < e21_c) and (float(df["High"].iloc[-1]) >= e9_c) and (cl < e21_c)

        bull = 0
        bull += 1 if cl > vwap_c else 0
        bull += 1 if rsi_c > 50 else 0
        bull += 1 if macd_c > msig_c else 0
        bull += 1 if e9_c > e21_c else 0
        bull += 1 if adx_c > 25 and cl > e9_c else 0
        bull += 1 if vol_c > volav_c and cl > op else 0
        bull += 1 if rsi5_c > 50 else 0
        bull_pct = round((bull / 7) * 100)

        bear = 0
        bear += 1 if cl < vwap_c else 0
        bear += 1 if rsi_c < 50 else 0
        bear += 1 if macd_c < msig_c else 0
        bear += 1 if e9_c < e21_c else 0
        bear += 1 if adx_c > 25 and cl < e9_c else 0
        bear += 1 if vol_c > volav_c and cl < op else 0
        bear += 1 if rsi5_c < 50 else 0
        bear_pct = round((bear / 7) * 100)

        diff = bull_pct - bear_pct
        if diff >= 40:
            bias = "STRONG BULL 💪"
        elif diff <= -40:
            bias = "STRONG BEAR 🔻"
        elif bull_pct > bear_pct:
            bias = "MILD BULL 📈"
        else:
            bias = "MILD BEAR 📉"

        entry_type = None
        direction  = None

        # Get last signal state for this stock (like Pine Script lastSignalState)
        last_state = last_signal_state.get(name, 0)

        # BUY: crossover + score >= 6 + last signal was not already BUY
        if buy_cross and bull >= DIRECT_ENTRY_SCORE and last_state <= 0:
            entry_type = "DIRECT"
            direction  = "BUY"
            last_signal_state[name] = 1

        # SELL: crossunder + score >= 6 + last signal was not already SELL
        elif sell_cross and bear >= DIRECT_ENTRY_SCORE and last_state >= 0:
            entry_type = "DIRECT"
            direction  = "SELL"
            last_signal_state[name] = -1

        # WATCH PULLBACK: crossover + score == 5 + last was not BUY
        elif buy_cross and bull == PULLBACK_SCORE and last_state <= 0:
            entry_type = "WATCH_PULLBACK"
            direction  = "BUY"

        # WATCH PULLBACK: crossunder + score == 5 + last was not SELL
        elif sell_cross and bear == PULLBACK_SCORE and last_state >= 0:
            entry_type = "WATCH_PULLBACK"
            direction  = "SELL"

        # PULLBACK CONFIRMED
        elif name in pullback_waiting:
            pw = pullback_waiting[name]
            if pw["direction"] == "BUY" and buy_pullback:
                entry_type = "PULLBACK"
                direction  = "BUY"
                bull       = pw["score"]
                bull_pct   = pw["pct"]
                bias       = pw["bias"]
                last_signal_state[name] = 1
            # SELL pullback disabled
            # elif pw["direction"] == "SELL": DISABLED

        if entry_type is None:
            return None

        # Dynamic ATR based on ADX strength
        if adx_c > 30:
            dynamic_atr = 1.0   # Strong trend — tight SL, more qty
        elif adx_c >= 25:
            dynamic_atr = 1.5   # Normal trend — medium SL
        else:
            return None         # Weak trend — skip signal

        risk = atr_c * dynamic_atr
        if direction == "BUY":
            sl  = cl - risk
            tp1 = cl + risk
            tp2 = cl + risk * 2
        else:
            sl  = cl + risk
            tp1 = cl - risk
            tp2 = cl - risk * 2

        score = bull if direction == "BUY" else bear
        pct   = bull_pct if direction == "BUY" else bear_pct

        pos = calc_position(cl, sl)

        return {
            "name":       name,
            "direction":  direction,
            "entry_type": entry_type,
            "price":      round(cl,  2),
            "sl":         round(sl,  2),
            "tp1":        round(tp1, 2),
            "tp2":        round(tp2, 2),
            "score":      score,
            "pct":        pct,
            "bias":       bias,
            "rsi":        round(rsi_c,  1),
            "rsi5m":      round(rsi5_c, 1),
            "adx":        round(adx_c,  1),
            "dynamic_atr": dynamic_atr,
            "vwap":       "ABOVE ✅" if cl > vwap_c else "BELOW ❌",
            "macd":       "BULL ▲" if macd_c > msig_c else "BEAR ▼",
            "qty":        pos["qty"],
            "cap_needed": pos["capital_needed"],
            "max_loss":   pos["max_loss"],
            "feasible":   pos["feasible"],
            "pos_note":   pos["note"],
        }

    except Exception as e:
        print(f"  ❌ Error {name}: {e}")
        return None

# ══════════════════════════════════════════════════════
#  FORMAT TELEGRAM MESSAGE
# ══════════════════════════════════════════════════════
def format_signal(sig: dict) -> str:
    emoji = "🟢" if sig["direction"] == "BUY" else "🔴"
    if sig["entry_type"] == "DIRECT":
        entry_line = "⚡ Entry Type : *DIRECT ENTRY*"
    elif sig["entry_type"] == "PULLBACK":
        entry_line = "🔄 Entry Type : *PULLBACK ENTRY*"
    else:
        entry_line = "👀 Entry Type : *WAIT FOR PULLBACK*"

    tv_sym  = sig['name'].replace('&', '%26')
    tv_link = f"https://www.tradingview.com/chart/?symbol=NSE%3A{tv_sym}"

    feasible_line = sig["pos_note"]

    return (
        f"🎯 *SNIPER ENTRY/EXIT V.02*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} *{sig['direction']} — {sig['name']}*\n"
        f"{entry_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entry : `{sig['price']}`\n"
        f"🛑 SL    : `{sig['sl']}`\n"
        f"✅ TP1   : `{sig['tp1']}` (exit 50%)\n"
        f"✅ TP2   : `{sig['tp2']}` (full close)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 *POSITION SIZE*\n"
        f"📦 Qty      : *{sig['qty']} shares*\n"
        f"💰 Capital  : ₹{sig['cap_needed']}\n"
        f"❌ Max Loss : ₹{sig['max_loss']}\n"
        f"📋 Status   : {feasible_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⭐ Score : {sig['score']}/7 ({sig['pct']}%)\n"
        f"📊 Bias  : {sig['bias']}\n"
        f"📈 RSI   : {sig['rsi']}  |  5m: {sig['rsi5m']}\n"
        f"💧 VWAP  : {sig['vwap']}\n"
        f"⚡ MACD  : {sig['macd']}\n"
        f"🔥 ADX   : {sig['adx']}\n"
        f"📐 ATR   : {sig.get('dynamic_atr', 1.5)} (ADX: {sig['adx']})\n"
        f"⏰ TF    : {TIMEFRAME}\n"
        f"🕐 Time  : {now_ist().strftime('%H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 [Open Chart on TradingView]({tv_link})"
    )

# ══════════════════════════════════════════════════════
#  MARKET HOURS
# ══════════════════════════════════════════════════════
def is_market_open() -> bool:
    now = now_ist()
    if now.weekday() >= 5:
        return False
    o = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    c = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return o <= now <= c


# ══════════════════════════════════════════════════════
#  TP / SL MONITOR
# ══════════════════════════════════════════════════════

def update_sheet_result(name, result_type, pnl):
    """Update the sheet row when TP or SL is hit"""
    try:
        sheet = get_sheet()
        if sheet is None:
            return
        # Find the row with this stock that has OPEN status
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows):
            if len(row) > 13 and row[2].startswith(name) and row[13] == "OPEN":
                row_num = i + 1
                if result_type.startswith("TP"):
                    sheet.update_cell(row_num, 12, result_type)  # TP Hit col
                    sheet.update_cell(row_num, 13, "")           # SL Hit col
                    sheet.update_cell(row_num, 14, "WIN")        # Result col
                    sheet.update_cell(row_num, 15, pnl)          # P&L col
                elif result_type == "SL":
                    sheet.update_cell(row_num, 12, "")           # TP Hit col
                    sheet.update_cell(row_num, 13, "SL HIT")     # SL Hit col
                    sheet.update_cell(row_num, 14, "LOSS")       # Result col
                    sheet.update_cell(row_num, 15, pnl)          # P&L col
                print("  ✅ Sheet result updated: " + name + " " + result_type)
                break
    except Exception as e:
        print("  ❌ Sheet update error: " + str(e))

def check_active_trades():
    if not active_trades:
        return

    print("  Checking " + str(len(active_trades)) + " active trades...")
    to_close = []

    for name, trade in list(active_trades.items()):
        try:
            ticker = STOCKS.get(name)
            if not ticker:
                continue

            df = yf.download(ticker, period="1d", interval="5m",
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 1:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            high  = float(df["High"].iloc[-1])
            low   = float(df["Low"].iloc[-1])

            direction = trade["direction"]
            entry     = trade["entry"]
            sl        = trade["sl"]
            tp1       = trade["tp1"]
            tp2       = trade["tp2"]


            qty       = trade["qty"]
            t_str     = now_ist().strftime("%H:%M:%S")

            # SL Hit
            sl_hit = (direction == "BUY" and low <= sl) or (direction == "SELL" and high >= sl)
            if sl_hit:
                loss = round(abs(entry - sl) * qty, 2)
                msg = ("SL HIT - " + name + "\n"
                    + "Direction: " + direction + "\n"
                    + "Entry: " + str(entry) + "\n"
                    + "SL: " + str(sl) + "\n"
                    + "Loss: Rs." + str(loss) + "\n"
                    + "Qty: " + str(qty) + " shares\n"
                    + "Time: " + t_str + "\n"
                    + "Chart: https://www.tradingview.com/chart/?symbol=NSE:" + name)
                send_telegram(msg)
                update_sheet_result(name, "SL", -loss)
                to_close.append(name)
                continue

            # TP1
            tp1_hit = (direction == "BUY" and high >= tp1) or (direction == "SELL" and low <= tp1)
            if tp1_hit and not trade.get("t1hit"):
                trade["t1hit"] = True
                qty_exit = max(1, int(qty * 0.5))
                profit = round(abs(tp1 - entry) * qty_exit, 2)
                trade["tp1_profit"] = profit  # save for total calculation
                msg = ("TP1 HIT - " + name + "\n"
                    + "Direction: " + direction + "\n"
                    + "Entry: " + str(entry) + "\n"
                    + "TP1: " + str(tp1) + "\n"
                    + "Profit: Rs." + str(profit) + "\n"
                    + "Exit: " + str(qty_exit) + " shares (50%)\n"
                    + "ACTION: Move SL to entry " + str(entry) + "\n"
                    + "Time: " + t_str + "\n"
                    + "Chart: https://www.tradingview.com/chart/?symbol=NSE:" + name)
                send_telegram(msg)
                update_sheet_result(name, "TP1", profit)

            # TP2 - Full Close
            tp2_hit = (direction == "BUY" and high >= tp2) or (direction == "SELL" and low <= tp2)
            if tp2_hit and trade.get("t1hit") and not trade.get("t2hit"):
                trade["t2hit"] = True
                qty_exit = max(1, int(qty * 0.5))
                tp2_profit = round(abs(tp2 - entry) * qty_exit, 2)
                tp1_profit = trade.get("tp1_profit", 0)
                total_profit = round(tp1_profit + tp2_profit, 2)
                msg = ("TP2 HIT - FULL CLOSE - " + name + "\n"
                    + "Direction: " + direction + "\n"
                    + "Entry: " + str(entry) + "\n"
                    + "TP2: " + str(tp2) + "\n"
                    + "TP2 Profit: Rs." + str(tp2_profit) + "\n"
                    + "TP1 Profit: Rs." + str(tp1_profit) + "\n"
                    + "Total Profit: Rs." + str(total_profit) + "\n"
                    + "ACTION: Close all remaining shares!\n"
                    + "Time: " + t_str + "\n"
                    + "Chart: https://www.tradingview.com/chart/?symbol=NSE:" + name)
                send_telegram(msg)
                update_sheet_result(name, "TP2", total_profit)
                to_close.append(name)





        except Exception as e:
            print("  Error checking trade " + name + ": " + str(e))

    for name in to_close:
        if name in active_trades:
            del active_trades[name]
            print("  Trade closed: " + name)


# ══════════════════════════════════════════════════════
#  MAIN SCAN
# ══════════════════════════════════════════════════════
def run_scan():
    now_str = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    now     = now_ist()

    # Skip first 5 minutes — low win rate at open (33%)
    open_time  = now.replace(hour=9,  minute=20, second=0, microsecond=0)
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if market_open <= now < open_time and now.weekday() < 5:
        print(f"[{now_str}] ⏸️  Waiting 5 min after open...")
        return

    # Auto close all positions at 3:15 PM IST
    close_time = now.replace(hour=15, minute=15, second=0, microsecond=0)
    if now >= close_time and now.weekday() < 5:
        # Check if we already closed today
        today_close_key = f"closed_{now.date()}"
        if today_close_key not in alerted_today:
            alerted_today[today_close_key] = True
            print(f"[{now_str}] 🔔 3:15 PM — closing all positions")
            market_close_message()
        return

    if not is_market_open():
        print(f"[{now_str}] ⏸️  Market closed.")
        return

    # First check existing active trades for TP/SL hits
    check_active_trades()

    print(f"\n{'='*52}")
    print(f"🔍 Scanning {len(STOCKS)} stocks | {now_str}")
    print(f"{'='*52}")

    signals_found = []

    for name, ticker in STOCKS.items():
        print(f"  {name}...", end=" ", flush=True)
        time.sleep(1)  # delay to avoid Yahoo Finance rate limiting
        result = scan_stock(name, ticker)

        if result:
            entry_type = result["entry_type"]
            direction  = result["direction"]
            key = f"{name}_{direction}_{datetime.now().date()}"

            if entry_type == "WATCH_PULLBACK":
                pb_key = f"{name}_pb_{datetime.now().date()}"
                if pb_key not in alerted_today:
                    alerted_today[pb_key] = True
                    pullback_waiting[name] = {
                        "direction": direction,
                        "score":     result["score"],
                        "pct":       result["pct"],
                        "bias":      result["bias"],
                    }
                    print(f"👀 WATCH {direction}")
                    signals_found.append(result)
                    send_telegram(format_signal(result))
                    log_to_sheet(result)
                    time.sleep(1)
                else:
                    print("⏭️")
                continue

            # Block ANY signal for same stock same day (prevents duplicates)
            day_key = f"{name}_{datetime.now().date()}"
            if key not in alerted_today and day_key not in alerted_today:
                alerted_today[key] = True
                alerted_today[day_key] = True
                if name in pullback_waiting:
                    del pullback_waiting[name]
                print(f"🚨 {entry_type} {direction}! {result['score']}/7")
                signals_found.append(result)
                send_telegram(format_signal(result))
                # If PULLBACK confirmed — update existing WATCH row
                if entry_type == "PULLBACK":
                    try:
                        sheet = get_sheet()
                        if sheet:
                            all_rows = sheet.get_all_values()
                            for i, row in enumerate(all_rows):
                                if len(row) > 3 and row[2] == name and row[13] == "OPEN":
                                    sheet.update_cell(i + 1, 4,  result["price"])
                                    sheet.update_cell(i + 1, 5,  result["sl"])
                                    sheet.update_cell(i + 1, 6,  result["tp1"])
                                    sheet.update_cell(i + 1, 7,  result["tp2"])
                                    print("  ✅ Updated WATCH row to PULLBACK in sheet")
                                    break
                    except Exception as e:
                        print("  ❌ Sheet pullback update error: " + str(e))
                else:
                    log_to_sheet(result)
                # Add to active trades for TP/SL monitoring
                active_trades[name] = {
                    "direction": direction,
                    "entry":     result["price"],
                    "sl":        result["sl"],
                    "tp1":       result["tp1"],
                    "tp2":       result["tp2"],
                    "qty":       result["qty"],
                    "t1hit":     False,
                    "t2hit":     False,
                }
                time.sleep(1)
            else:
                print("⏭️")
        else:
            print("–")

    if signals_found:
        buys    = [s for s in signals_found if s["direction"] == "BUY"]
        sells   = [s for s in signals_found if s["direction"] == "SELL"]
        watches = [s for s in signals_found if s["entry_type"] == "WATCH_PULLBACK"]
        summary = (
            f"📋 *SCAN SUMMARY — {now_ist().strftime('%H:%M')}*\n"
            f"Scanned : {len(STOCKS)} stocks\n"
            f"🟢 BUY: {len(buys)} | 🔴 SELL: {len(sells)} | 👀 WATCH: {len(watches)}\n\n"
        )
        for s in signals_found:
            e = "🟢" if s["direction"] == "BUY" else "🔴"
            t = "⚡" if s["entry_type"] == "DIRECT" else "🔄" if s["entry_type"] == "PULLBACK" else "👀"
            summary += f"{e}{t} *{s['name']}* @ {s['price']} | SL:{s['sl']} | TP1:{s['tp1']} | TP2:{s['tp2']} | {s['score']}/7\n"
        send_telegram(summary)
    else:
        print("  ⚪ No signals.")

# ══════════════════════════════════════════════════════
#  SCHEDULED EVENTS
# ══════════════════════════════════════════════════════
def market_open_greeting():
    reset_alerts()
    send_telegram(
        "🔔 *Market Opening — Scanner Active!*\n"
        f"Watching : {len(STOCKS)} stocks\n"
        f"Capital  : Rs.{CAPITAL} | Risk: {RISK_PERCENT}%\n"
        f"Max Loss : Rs.{RISK_AMOUNT} per trade\n"
        f"Leverage : {LEVERAGE}x MIS\n"
        f"Timeframe: {TIMEFRAME} | Every {SCAN_INTERVAL} min\n"
        f"🕐 {now_ist().strftime('%H:%M:%S')}"
    )
    run_scan()

def market_close_message():
    pullback_waiting.clear()
    total_pnl = 0.0
    expired   = 0
    wins      = 0
    losses    = 0

    try:
        sheet = get_sheet()
        if sheet:
            all_rows = sheet.get_all_values()
            for i, row in enumerate(all_rows):
                if len(row) > 13 and row[13] == "OPEN":
                    try:
                        # Get stock name without emoji
                        name      = row[2].split(" ")[0]
                        # Detect direction from emoji
                        direction = "BUY" if "🟢" in row[2] else "SELL"
                        entry     = float(row[3]) if row[3] else 0
                        qty       = int(row[10]) if row[10] else 0
                        pnl       = 0.0

                        # Fetch current closing price
                        ticker = STOCKS.get(name)
                        if ticker and entry > 0 and qty > 0:
                            df = yf.download(ticker, period="1d",
                                           interval="1m",
                                           progress=False,
                                           auto_adjust=True)
                            if df is not None and len(df) > 0:
                                if isinstance(df.columns, pd.MultiIndex):
                                    df.columns = df.columns.get_level_values(0)
                                close_px = float(df["Close"].iloc[-1])
                                if direction == "BUY":
                                    pnl = round((close_px - entry) * qty, 2)
                                else:
                                    pnl = round((entry - close_px) * qty, 2)

                        # Update sheet
                        sheet.update_cell(i + 1, 14, "EXPIRED")
                        sheet.update_cell(i + 1, 15, pnl)
                        total_pnl += pnl
                        expired   += 1
                        if pnl > 0:
                            wins += 1
                        elif pnl < 0:
                            losses += 1
                        print("  EXPIRED: " + name + " P&L: Rs." + str(pnl))

                    except Exception as ex:
                        # Even if price fetch fails mark as EXPIRED
                        try:
                            sheet.update_cell(i + 1, 14, "EXPIRED")
                        except:
                            pass
                        expired += 1

    except Exception as e:
        print("  Sheet expire error: " + str(e))

    active_trades.clear()

    # Send day summary
    send_telegram(
        "🔕 *Market Closed — Scanner Paused*\n"
        f"🕐 {now_ist().strftime('%H:%M:%S')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Day Summary*\n"
        f"Total trades : {expired}\n"
        f"✅ Profit    : {wins}\n"
        f"❌ Loss      : {losses}\n"
        f"💰 Net P&L   : Rs.{round(total_pnl, 2)}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

# ══════════════════════════════════════════════════════
#  FLASK WEB SERVER — keeps Render alive 24/7
# ══════════════════════════════════════════════════════
app = Flask(__name__)

@app.route("/")
def home():
    return f"Nifty Scanner Running | Stocks: {len(STOCKS)} | TF: {TIMEFRAME} | Capital: Rs.{CAPITAL}"

@app.route("/status")
def status():
    return {
        "status":       "running",
        "stocks":       len(STOCKS),
        "timeframe":    TIMEFRAME,
        "capital":      CAPITAL,
        "market":       "open" if is_market_open() else "closed",
        "active_trades": len(active_trades),
        "time":         now_ist().strftime("%Y-%m-%d %H:%M:%S")
    }

@app.route("/check")
def manual_check():
    """Manually trigger TP/SL check for all OPEN trades in sheet"""
    try:
        # Reload trades from sheet first
        reload_active_trades()
        # Then check them
        check_active_trades()
        msg = "Checked " + str(len(active_trades)) + " active trades at " + now_ist().strftime("%H:%M:%S")
        send_telegram("🔄 *Manual Check Triggered*\n" + msg)
        return {"status": "ok", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.route("/reload")
def manual_reload():
    """Manually reload OPEN trades from sheet into memory"""
    try:
        before = len(active_trades)
        reload_active_trades()
        after = len(active_trades)
        msg = "Reloaded " + str(after) + " trades (was " + str(before) + ")"
        send_telegram("🔄 *Trades Reloaded from Sheet*\n" + msg)
        return {"status": "ok", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.route("/expire")
def manual_expire():
    """Manually mark all OPEN trades as EXPIRED with P&L"""
    try:
        market_close_message()
        return {"status": "ok", "message": "Expire done with P&L calculated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def reload_active_trades():
    """Load all OPEN trades from Google Sheet into memory on startup"""
    try:
        sheet = get_sheet()
        if sheet is None:
            return
        all_rows = sheet.get_all_values()
        count = 0
        for i, row in enumerate(all_rows):
            # Skip header row
            if i == 0:
                continue
            # Check if Result column is OPEN
            if len(row) > 20 and row[20] == "OPEN":
                try:
                    name      = row[2]
                    direction = row[3]
                    entry     = float(row[5])
                    sl        = float(row[6])
                    tp1       = float(row[5])
                    tp2       = float(row[6])

                    qty       = int(row[18]) if row[18] else 1
                    if name and direction and entry:
                        active_trades[name] = {
                            "direction": direction,
                            "entry":     entry,
                            "sl":        sl,
                            "tp1":       tp1,
                            "tp2":       tp2,
                            "qty":       qty,
                            "t1hit":     False,
                            "t2hit":     False,
                        }
                        count += 1
                except Exception:
                    continue
        if count > 0:
            print("  Reloaded " + str(count) + " active trades from sheet")
    except Exception as e:
        print("  Reload error: " + str(e))

def run_scheduler():
    # Reload any open trades from sheet on startup
    reload_active_trades()

    schedule.every().day.at("03:45").do(market_open_greeting)  # 9:15 AM IST
    schedule.every().day.at("09:45").do(market_close_message)  # 3:15 PM IST
    schedule.every(SCAN_INTERVAL).minutes.do(run_scan)

    send_telegram(
        "🤖 *Nifty Scanner Running 24/7*\n"
        f"Stocks   : {len(STOCKS)}\n"
        f"Capital  : Rs.{CAPITAL} | Risk: {RISK_PERCENT}%\n"
        f"Max Loss : Rs.{RISK_AMOUNT} per trade\n"
        f"ATR      : {ATR_MULTIPLIER} | TF: {TIMEFRAME}\n"
        f"Direct   : score >= {DIRECT_ENTRY_SCORE}/7\n"
        f"Pullback : score = {PULLBACK_SCORE}/7\n"
        f"Auto alert at 9:15 AM daily"
    )

    if is_market_open():
        print("📈 Market open! Running first scan...")
        run_scan()
    else:
        day = datetime.now().weekday()
        next_day = "Monday" if day >= 4 else "tomorrow"
        print(f"⏰ Market closed. Auto-scan {next_day} at 9:15 AM.")

    while True:
        schedule.run_pending()
        time.sleep(30)

# ══════════════════════════════════════════════════════
#  START
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("╔══════════════════════════════════════════╗")
    print("║      Nifty Sniper Scanner — Render       ║")
    print("╚══════════════════════════════════════════╝")
    print(f"  Stocks   : {len(STOCKS)}")
    print(f"  Capital  : Rs.{CAPITAL}")
    print(f"  Risk     : {RISK_PERCENT}% = Rs.{RISK_AMOUNT}/trade")
    print(f"  Leverage : {LEVERAGE}x MIS")
    print(f"  ATR      : {ATR_MULTIPLIER}")
    print(f"  Market   : 9:15 AM - 3:30 PM IST\n")

    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()

    port = int(__import__("os").environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
