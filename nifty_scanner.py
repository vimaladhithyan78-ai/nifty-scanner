"""
NIFTY 50 + BANK NIFTY SNIPER SCANNER
- Render.com 24/7 version with Flask
- Capital: Rs.5000 | Risk: 2% per trade = Rs.100
- Intraday MIS 5x leverage = Rs.25,000 buying power
- Bull/Bear Score 7 conditions
- Direct Entry (6-7/7) + Pullback Entry (5/7)
- ATR 2.0 SL + TP1 to TP5
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
CAPITAL           = 5000     # Rs.5,000 your capital
RISK_PERCENT      = 2.0      # 2% risk per trade
LEVERAGE          = 5        # MIS intraday 5x
RISK_AMOUNT       = CAPITAL * (RISK_PERCENT / 100)  # Rs.100 max loss per trade
BUYING_POWER      = CAPITAL * LEVERAGE              # Rs.25,000 buying power

# ══════════════════════════════════════════════════════
#  SCANNER SETTINGS
# ══════════════════════════════════════════════════════
ATR_MULTIPLIER     = 2.0
SCAN_INTERVAL      = 15
TIMEFRAME          = "15m"
PERIOD             = "5d"
DIRECT_ENTRY_SCORE = 6
PULLBACK_SCORE     = 5

# ══════════════════════════════════════════════════════
#  54 STOCKS
# ══════════════════════════════════════════════════════
STOCKS = {
    "RELIANCE":   "RELIANCE.NS",
    "TCS":        "TCS.NS",
    "HDFCBANK":   "HDFCBANK.NS",
    "INFY":       "INFY.NS",
    "ICICIBANK":  "ICICIBANK.NS",
    "HINDUNILVR": "HINDUNILVR.NS",
    "ITC":        "ITC.NS",
    "KOTAKBANK":  "KOTAKBANK.NS",
    "LT":         "LT.NS",
    "SBIN":       "SBIN.NS",
    "BHARTIARTL": "BHARTIARTL.NS",
    "AXISBANK":   "AXISBANK.NS",
    "ASIANPAINT": "ASIANPAINT.NS",
    "MARUTI":     "MARUTI.NS",
    "TITAN":      "TITAN.NS",
    "SUNPHARMA":  "SUNPHARMA.NS",
    "WIPRO":      "WIPRO.NS",
    "HCLTECH":    "HCLTECH.NS",
    "BAJFINANCE": "BAJFINANCE.NS",
    "NESTLEIND":  "NESTLEIND.NS",
    "DRREDDY":    "DRREDDY.NS",
    "MM":         "M&M.NS",
    "NTPC":       "NTPC.NS",
    "POWERGRID":  "POWERGRID.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
    "TATASTEEL":  "TATASTEEL.NS",
    "ADANIENT":   "ADANIENT.NS",
    "ADANIPORTS": "ADANIPORTS.NS",
    "CIPLA":      "CIPLA.NS",
    "EICHERMOT":  "EICHERMOT.NS",
    "BAJAJAUTO":  "BAJAJ-AUTO.NS",
    "JSWSTEEL":   "JSWSTEEL.NS",
    "HINDALCO":   "HINDALCO.NS",
    "ULTRACEMCO": "ULTRACEMCO.NS",
    "GRASIM":     "GRASIM.NS",
    "BPCL":       "BPCL.NS",
    "ONGC":       "ONGC.NS",
    "COALINDIA":  "COALINDIA.NS",
    "HEROMOTOCO": "HEROMOTOCO.NS",
    "DIVISLAB":   "DIVISLAB.NS",
    "APOLLOHOSP": "APOLLOHOSP.NS",
    "BAJAJFINSV": "BAJAJFINSV.NS",
    "BRITANNIA":  "BRITANNIA.NS",
    "HDFCLIFE":   "HDFCLIFE.NS",
    "LTIM":       "LTIM.NS",
    "SBILIFE":    "SBILIFE.NS",
    "SHRIRAMFIN": "SHRIRAMFIN.NS",
    "TATACONSUM": "TATACONSUM.NS",
    "TECHM":      "TECHM.NS",
    "INDUSINDBK": "INDUSINDBK.NS",
    "BANDHANBNK": "BANDHANBNK.NS",
    "FEDERALBNK": "FEDERALBNK.NS",
    "IDFCFIRSTB": "IDFCFIRSTB.NS",
    "PNB":        "PNB.NS",
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
                "Date", "Time", "Stock", "Direction", "Entry Type",
                "Entry", "SL", "TP1", "TP2", "TP3", "TP4", "TP5",
                "Score", "Bias", "RSI", "ADX", "VWAP", "MACD",
                "Qty", "Capital Needed", "Max Loss"
            ])
        elif existing[0][0] != "Date":
            sheet.insert_row([
                "Date", "Time", "Stock", "Direction", "Entry Type",
                "Entry", "SL", "TP1", "TP2", "TP3", "TP4", "TP5",
                "Score", "Bias", "RSI", "ADX", "VWAP", "MACD",
                "Qty", "Capital Needed", "Max Loss"
            ], 1)

        now = now_ist()
        row = [
            now.strftime("%d-%b-%Y"),
            now.strftime("%H:%M:%S"),
            sig["name"],
            sig["direction"],
            sig["entry_type"],
            sig["price"],
            sig["sl"],
            sig["tp1"],
            sig["tp2"],
            sig["tp3"],
            sig["tp4"],
            sig["tp5"],
            f"{sig['score']}/7",
            sig["bias"],
            sig["rsi"],
            sig["adx"],
            sig["vwap"],
            sig["macd"],
            sig["qty"],
            sig["cap_needed"],
            sig["max_loss"],
        ]
        sheet.append_row(row)
        print("  ✅ Logged to Google Sheets!")
    except Exception as e:
        print(f"  ❌ Sheet log error: {e}")

# ══════════════════════════════════════════════════════
#  ALERT MEMORY
# ══════════════════════════════════════════════════════
alerted_today    = {}
pullback_waiting = {}

def reset_alerts():
    global alerted_today, pullback_waiting
    alerted_today    = {}
    pullback_waiting = {}
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
        df = yf.download(ticker, period=PERIOD, interval=TIMEFRAME,
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 30:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df5 = yf.download(ticker, period="2d", interval="5m",
                          progress=False, auto_adjust=True)
        if isinstance(df5.columns, pd.MultiIndex):
            df5.columns = df5.columns.get_level_values(0)

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

        if buy_cross and bull >= DIRECT_ENTRY_SCORE:
            entry_type = "DIRECT"
            direction  = "BUY"
        elif sell_cross and bear >= DIRECT_ENTRY_SCORE:
            entry_type = "DIRECT"
            direction  = "SELL"
        elif buy_cross and bull == PULLBACK_SCORE:
            entry_type = "WATCH_PULLBACK"
            direction  = "BUY"
        elif sell_cross and bear == PULLBACK_SCORE:
            entry_type = "WATCH_PULLBACK"
            direction  = "SELL"
        elif name in pullback_waiting:
            pw = pullback_waiting[name]
            if pw["direction"] == "BUY" and buy_pullback:
                entry_type = "PULLBACK"
                direction  = "BUY"
                bull       = pw["score"]
                bull_pct   = pw["pct"]
                bias       = pw["bias"]
            elif pw["direction"] == "SELL" and sell_pullback:
                entry_type = "PULLBACK"
                direction  = "SELL"
                bear       = pw["score"]
                bear_pct   = pw["pct"]
                bias       = pw["bias"]

        if entry_type is None:
            return None

        risk = atr_c * ATR_MULTIPLIER
        if direction == "BUY":
            sl  = cl - risk
            tp1 = cl + risk
            tp2 = cl + risk * 2
            tp3 = cl + risk * 3
            tp4 = cl + risk * 4
            tp5 = cl + risk * 5
        else:
            sl  = cl + risk
            tp1 = cl - risk
            tp2 = cl - risk * 2
            tp3 = cl - risk * 3
            tp4 = cl - risk * 4
            tp5 = cl - risk * 5

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
            "tp3":        round(tp3, 2),
            "tp4":        round(tp4, 2),
            "tp5":        round(tp5, 2),
            "score":      score,
            "pct":        pct,
            "bias":       bias,
            "rsi":        round(rsi_c,  1),
            "rsi5m":      round(rsi5_c, 1),
            "adx":        round(adx_c,  1),
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
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} *{sig['direction']} SIGNAL — {sig['name']}*\n"
        f"{entry_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entry : `{sig['price']}`\n"
        f"🛑 SL    : `{sig['sl']}`\n"
        f"✅ TP1   : `{sig['tp1']}`\n"
        f"✅ TP2   : `{sig['tp2']}`\n"
        f"✅ TP3   : `{sig['tp3']}`\n"
        f"✅ TP4   : `{sig['tp4']}`\n"
        f"✅ TP5   : `{sig['tp5']}`\n"
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
    o = now.replace(hour=3,  minute=45, second=0, microsecond=0)
    c = now.replace(hour=10, minute=0, second=0, microsecond=0)
    return o <= now <= c

# ══════════════════════════════════════════════════════
#  MAIN SCAN
# ══════════════════════════════════════════════════════
def run_scan():
    now_str = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    if not is_market_open():
        print(f"[{now_str}] ⏸️  Market closed.")
        return

    print(f"\n{'='*52}")
    print(f"🔍 Scanning {len(STOCKS)} stocks | {now_str}")
    print(f"{'='*52}")

    signals_found = []

    for name, ticker in STOCKS.items():
        print(f"  {name}...", end=" ", flush=True)
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

            if key not in alerted_today:
                alerted_today[key] = True
                if name in pullback_waiting:
                    del pullback_waiting[name]
                print(f"🚨 {entry_type} {direction}! {result['score']}/7")
                signals_found.append(result)
                send_telegram(format_signal(result))
                log_to_sheet(result)
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
            summary += f"{e}{t} *{s['name']}* @ {s['price']} | {s['score']}/7 | Qty:{s['qty']}\n"
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
    send_telegram(
        "🔕 *Market Closed — Scanner Paused*\n"
        f"Will resume tomorrow at 9:15 AM IST\n"
        f"🕐 {now_ist().strftime('%H:%M:%S')}"
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
        "status":    "running",
        "stocks":    len(STOCKS),
        "timeframe": TIMEFRAME,
        "capital":   CAPITAL,
        "market":    "open" if is_market_open() else "closed",
        "time":      now_ist().strftime("%Y-%m-%d %H:%M:%S")
    }

def run_scheduler():
    schedule.every().day.at("03:45").do(market_open_greeting)
    schedule.every().day.at("10:00").do(market_close_message)
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
