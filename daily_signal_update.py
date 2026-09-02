
Daily signal update · PY
"""
NAS100 Pivot S/R (long-only, vol-targeted) — daily forward-test signal logger.
 
Run this once per day, after the NAS100 daily close, via cron or GitHub Actions
(same pattern as your market_brief.py). It:
  1. Pulls yesterday's confirmed daily OHLC for NAS100
  2. Computes pivot, resistance, realized vol, vol-scale, and today's state
  3. Appends a row to the persistent log CSV
  4. Sends a Telegram message with the signal
  5. Does NOT place any trades — logging only, for forward-test tracking
 
Requires env vars (set as GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
 
DATA SOURCE: Yahoo Finance (direct chart API, no API key needed), pulling
^NDX — the real Nasdaq-100 index. Twelve Data was dropped for this script:
as of writing, Twelve Data's Indices product is listed as "coming soon" —
it has NO index data at all yet, on any plan, which is why it was silently
substituting QQQ before. ^NDX trades in the ~24,000-25,000 range, much
closer to your broker's NAS100 CFD quote than QQQ's ~$700 — but it still
won't be an exact match to your specific broker's feed (different index
variants, small quote conventions, etc). Treat the logged pivot/resistance
as close-but-not-identical to your broker's actual order-book levels;
swap in your broker's own feed if you need exact fidelity.
"""
import os
import csv
import json
import requests
import pandas as pd
from pathlib import Path
 
LOG_PATH = Path(__file__).parent / "nas100_signal_log.csv"
STATE_PATH = Path(__file__).parent / "nas100_state.json"
 
VOL_LOOKBACK = 20
MEDIAN_LOOKBACK = 500
SCALE_MIN, SCALE_MAX = 0.3, 2.0
BASE_RISK_PCT = 100
 
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
 
SYMBOL = os.environ.get("SIGNAL_SYMBOL", "^NDX")  # Yahoo Finance ticker for Nasdaq-100 index
 
 
def fetch_latest_daily_bar(symbol):
    """Pull the most recent confirmed daily OHLC bar from Yahoo Finance's
    public chart API (no key required)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "10d", "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
 
    result = data.get("chart", {}).get("result")
    if not result:
        raise RuntimeError(f"Yahoo Finance error: {data}")
 
    result = result[0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    closes = quote["close"]
    highs = quote["high"]
    lows = quote["low"]
 
    # drop any trailing bar with missing data (e.g. today's still-forming bar)
    valid_idx = [i for i in range(len(closes)) if closes[i] is not None]
    if len(valid_idx) < 2:
        raise RuntimeError("Not enough confirmed daily bars returned from Yahoo Finance")
 
    latest_i = valid_idx[-1]
    prior_i = valid_idx[-2]
 
    latest_date = pd.to_datetime(timestamps[latest_i], unit="s").strftime("%Y-%m-%d")
 
    return {
        "date": latest_date,
        "close": float(closes[latest_i]),
        "prior_high": float(highs[prior_i]),
        "prior_low": float(lows[prior_i]),
        "prior_close": float(closes[prior_i]),
    }
 
 
def load_log():
    if LOG_PATH.exists():
        return pd.read_csv(LOG_PATH, parse_dates=["date"])
    raise FileNotFoundError(
        f"{LOG_PATH} not found. Copy nas100_signal_log_seed.csv here (renamed) "
        "before running the daily updater for the first time."
    )
 
 
def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    # bootstrap from last row of log
    log = load_log()
    last = log.iloc[-1]
    return {"state": int(last["state"])}
 
 
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[warn] Telegram not configured, skipping alert. Message was:\n" + msg)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
 
 
def main():
    log = load_log()
    state_data = load_state()
    prev_state = state_data["state"]
 
    bar = fetch_latest_daily_bar(SYMBOL)
 
    # skip if already logged today
    if pd.to_datetime(bar["date"]) in set(log["date"]):
        print(f"{bar['date']} already logged, skipping.")
        return
 
    pivot = (bar["prior_high"] + bar["prior_low"] + bar["prior_close"]) / 3
    resistance = 2 * pivot - bar["prior_low"]
    close = bar["close"]
 
    # realized vol over trailing window, using logged closes + today's close
    recent_closes = pd.concat([log["close"], pd.Series([close])], ignore_index=True)
    rets = recent_closes.pct_change()
    realized_vol = rets.tail(VOL_LOOKBACK).std()
    all_vols = pd.concat([
        (log["close"].pct_change()).rolling(VOL_LOOKBACK).std(),
        pd.Series([realized_vol])
    ], ignore_index=True)
    median_vol = all_vols.tail(MEDIAN_LOOKBACK).median()
 
    vol_scale = median_vol / realized_vol if realized_vol > 0 else 1.0
    vol_scale = min(max(vol_scale, SCALE_MIN), SCALE_MAX)
 
    # signal logic (long-only pivot S/R)
    new_state = prev_state
    action = "HOLD"
    if prev_state == 0 and close > pivot:
        new_state = 1
        action = "ENTER LONG"
    elif prev_state == 1 and close >= resistance:
        new_state = 0
        action = "EXIT (hit resistance)"
    elif prev_state == 1:
        action = "HOLD LONG"
    else:
        action = "FLAT"
 
    position_pct = new_state * vol_scale * (BASE_RISK_PCT / 100)
 
    row = {
        "date": bar["date"], "close": close, "pivot": pivot, "resistance": resistance,
        "realized_vol_20d": realized_vol, "median_vol_500d": median_vol,
        "vol_scale": vol_scale, "state": new_state, "position_pct": position_pct,
    }
    log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)
    log.to_csv(LOG_PATH, index=False)
    STATE_PATH.write_text(json.dumps({"state": new_state}))
 
    msg = (
        f"*NAS100 Pivot S/R Signal — {bar['date']}*\n"
        f"Close: {close:.1f}\n"
        f"Pivot: {pivot:.1f} | Resistance: {resistance:.1f}\n"
        f"Action: *{action}*\n"
        f"Vol scale: {vol_scale:.2f}x | Position size: {position_pct*100:.1f}% of equity\n"
        f"_(signal log only — no trades placed)_"
    )
    send_telegram(msg)
    print(msg)
 
 
if __name__ == "__main__":
    main()
 
