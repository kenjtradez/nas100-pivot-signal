# NAS100 Pivot S/R — Forward Test (Fresh Start)

Long-only, volatility-targeted Pivot Support/Resistance signal on NAS100.
Backtest: Sharpe 1.25, CAGR 15.1%, max DD -16.2%, profit factor 2.24
(2016–2026 daily data, single train/test split — see the original
backtest files for the full breakdown before trusting these numbers
further).

**This is a signal logger only. It places no trades.**

## What "fresh" means here

`nas100_signal_log.csv` is seeded with 10 years of price and volatility
history — needed so the vol-targeting math has a real "typical vol"
reference from day one instead of starting blind. But the **position
state is reset to flat (0)**, not inherited from the backtest. The
first real entry/exit is decided live, going forward, with no
knowledge of what the backtest already knew. This is what makes it an
honest forward test rather than a continuation of the fitting sample.

Forward test start date: **the first day this workflow runs after
setup.**

## Setup

1. Push this folder as a new GitHub repo (or a subfolder of an existing one).
2. Add repo secrets (Settings → Secrets and variables → Actions):
   - `TWELVE_DATA_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Trigger the workflow manually once (Actions tab → "NAS100 Pivot Signal Log" → Run workflow) to confirm it runs clean before letting the cron take over.
4. From then on it runs automatically after each daily close (cron: `15 22 * * 1-5`, adjust for your broker's actual daily bar close / DST).

## Known limitation — data source

Twelve Data's free tier doesn't carry the raw NAS100/NDX symbol, so
`SIGNAL_SYMBOL` defaults to `QQQ` as a proxy (same workaround as your
`market_brief.py`). This is fine for validating whether the LONG/FLAT
signal itself fires correctly, but the logged pivot/resistance price
levels will not match your broker's NAS100 CFD quote. Swap in your
actual NAS100 feed (Databento, broker API, etc.) when available —
change `SIGNAL_SYMBOL` and adjust `fetch_latest_daily_bar()` accordingly.

## Files

- `daily_signal_update.py` — the daily job: fetch price, compute signal, log, alert
- `nas100_signal_log.csv` — persistent log, updated (committed back) by the workflow each day
- `nas100_state.json` — current position state (0=flat, 1=long), reset to 0
- `.github/workflows/nas100_signal.yml` — the cron job
