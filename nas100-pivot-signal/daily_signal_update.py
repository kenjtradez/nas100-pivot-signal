"""
NAS100 Pivot S/R (long-only, vol-targeted) — daily forward-test signal logger.

Run this once per day, after the NAS100 daily close, via cron or GitHub Actions
(same pattern as your market_brief.py). It:
  1. Pulls yesterday's confirmed daily OHLC for NAS100
  2. Computes pivot, resistance, realized vol, vol-scale, and today's state
  3. Appends a row to the persistent log CSV
  4. Sends a Telegram message with the signal
  5. Does NOT place any trades — logging only, for forward-test tracking

Requires env vars (set as GitHub Actions secrets, matching your existing setup):
  TWELVE_DATA_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

CAVEAT (matches your prior finding on market_brief.py): Twelve Data's free
tier doesn't support the raw NDX/NAS100 symbol. If you're using QQQ as a
proxy, the pivot/resistance PRICE LEVELS will not match your broker's
NAS100 CFD quote. This is fine for tracking whether the LONG/FLAT signal
and vol-scale are directionally correct, but do not use the logged
pivot/resistance price levels as actual order levels against your broker
feed. Swap in your broker's real NAS100 OHLC (or Databento, matching your
gold-oi-dashboard setup) if you want exact price-level fidelity.
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

TD_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SYMBOL = os.environ.get("SIGNAL_SYMBOL", "QQQ")  # swap to your real feed when available


def fetch_latest_daily_bar(symbol):
    """Pull the most recent confirmed daily OHLC bar from Twelve Data."""
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": 3,  # need a couple bars for prior-day reference
        "apikey": TD_API_KEY,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise RuntimeError(f"Twelve Data error: {data}")
    bars = data["values"]
    # bars[0] = most recent, bars[1] = day before
    latest = bars[0]
    prior = bars[1]
    return {
        "date": latest["datetime"],
        "close": float(latest["close"]),
        "prior_high": float(prior["high"]),
        "prior_low": float(prior["low"]),
        "prior_close": float(prior["close"]),
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
