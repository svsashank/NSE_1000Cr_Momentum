# NSE Quantitative Momentum — Live Screening & Rebalancing Engine

A live, operational (not backtest) Python pipeline that screens the NSE equity
universe daily, applies a 7-stage momentum funnel, compares results against
your current `holdings.csv`, generates explicit BUY/SELL/HOLD signals, and
publishes a dashboard to GitHub Pages — triggered manually from the GitHub
Actions tab.

## Quick Start

1. **Create a new GitHub repo** and push everything in this folder (preserve
   the `.github/workflows/` structure).

2. **Enable GitHub Pages**:
   - Repo Settings → Pages → Source → "GitHub Actions"

3. **Set initial portfolio config** (optional fallback):
   - Edit `portfolio_config.json` with your starting `Total_Portfolio_Value`
     and `Unallocated_Cash`. (You can also pass these as workflow inputs each
     run — recommended, since they change daily.)

4. **First run (empty portfolio)**:
   - `holdings.csv` ships empty (header row only). The first run will deploy
     up to 15 positions from scratch using your `Total_Portfolio_Value`.

5. **Trigger a run**:
   - Go to **Actions → NSE Momentum Screener — Live Run → Run workflow**
   - Enter:
     - `total_portfolio_value`: e.g. `1000000` (₹10 lakh)
     - `unallocated_cash`: cash not currently invested (e.g. `1000000` for
       first run, or `0`/small amount for subsequent runs)
     - `max_universe_tickers`: leave blank for the full NSE universe, or set
       e.g. `50` for a quick test run
   - Click **Run workflow**

6. **View results**:
   - Dashboard: `https://<your-username>.github.io/<repo-name>/`
   - `holdings.csv` is automatically updated and committed back to the repo
   - `screen_results.csv` contains the full ranked list of all stocks that
     passed the funnel today (for audit)

## How It Works

### 7-Stage Screening Funnel (applied fresh each run)
1. Market Cap > ₹1,000 Cr (Close × Shares Outstanding via yfinance)
2. 3-Month ADV > 1,000,000 shares
3. Annualized volatility (252d) < 75%
4. RSI(14) > 50
5. Close > SMA(30)
6. Close ≥ 0.75 × 52-week high
7. CMF(20) > 0

### Ranking
Stocks passing all 7 filters are ranked by `(SMA21 - SMA200) / SMA200`,
descending. Top 15 = today's ideal target portfolio.

### Rebalance Decision Tree (Retention Buffer Rule)
For each currently-held stock:
- **HOLD** if it still passes the funnel **and** ranks ≤ 25 today
- **SELL ALL** otherwise (proceeds computed at today's close, minus 0.23%
  sell friction)

New entrants fill vacated slots from today's Top 15 (highest rank first,
excluding names already held). Combined freed + unallocated cash is split
equally among new entrants:

```
Shares = floor((Allocated_Cash × (1 - 0.0013)) / Current_Price)
```

If fewer than 15 qualified names exist in total, leftover cash is reported
as **Cash Hoarding (Liquid Funds)**.

## File Structure

```
.
├── .github/workflows/run_screener.yml   # GitHub Actions workflow (workflow_dispatch)
├── run_screener.py                      # Main entrypoint / orchestration
├── nse_universe.py                      # NSE ticker universe (live + fallback)
├── universe_fallback.csv                # Static fallback universe (~400 NSE tickers)
├── data_fetcher.py                      # yfinance data fetching with retries
├── indicators.py                        # Vectorized technical indicators
├── screener_engine.py                   # 7-stage funnel + momentum ranking
├── portfolio_engine.py                  # Rebalancing decision logic
├── dashboard.py                         # HTML dashboard generator
├── holdings.csv                         # Current portfolio state (auto-updated)
├── portfolio_config.json                # Fallback portfolio value/cash inputs
└── requirements.txt
```

## Important Notes & Caveats

- **NSE universe source**: NSE's official equity list (`EQUITY_L.csv`) is
  fetched live with browser-like headers; if NSE blocks the request (common
  for scripted access), the pipeline falls back to `universe_fallback.csv`
  (~400 liquid NSE names). For production use, periodically refresh this file
  from a reliable source.
- **Shares outstanding / market cap**: sourced from `yfinance`'s `.info` /
  `.fast_info`, which can be slow or occasionally missing for some tickers.
  If shares outstanding can't be determined, that stock conservatively
  **fails** the market cap filter.
- **Held tickers with no current data**: if a currently-held stock can't be
  fetched today (delisting, data issue), it is conservatively marked
  **SELL_ALL** using its average buy price as a proxy, and a warning is
  logged — **manual review recommended** in this edge case.
- **Runtime**: fetching ~400+ tickers' full history + shares outstanding via
  yfinance can take significant time (potentially 20-40+ minutes). The
  workflow timeout is set to 60 minutes. Use `max_universe_tickers` to test
  with a smaller subset first.
- **Not investment advice**: this is a decision-support tool. Always review
  the generated BUY/SELL/HOLD checklist before placing any real orders.
