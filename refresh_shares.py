"""
Weekly shares-outstanding fetcher.
Also refreshes nse_universe.json first (from NSE EQUITY_L.csv).

Shares fetch strategy (two-pass):
  Pass 1 — yfinance fast_info.shares   (fast, ~2200 tickers)
  Pass 2 — yfinance info marketCap/currentPrice fallback for any ticker
           that returned nothing in pass 1 (handles smaller NSE names like KPL)

Market cap = shares_outstanding x latest_close. Fetching weekly and caching
lets the screener compute MCap instantly without per-ticker API calls.
"""

import json, time, os, sys, subprocess
import yfinance as yf

UNIVERSE_FILE = os.path.join(os.path.dirname(__file__), 'nse_universe.json')
OUTPUT_FILE   = os.path.join(os.path.dirname(__file__), 'shares_outstanding.json')


def refresh_universe():
    """Refresh nse_universe.json from NSE EQUITY_L.csv before fetching shares."""
    result = subprocess.run(
        [sys.executable, "refresh_universe.py"],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"Warning: Universe refresh returned {result.returncode}")
        if result.stderr:
            print(result.stderr[:500])
    else:
        try:
            subprocess.run(["git", "add", "nse_universe.json"], check=False)
            print("Staged nse_universe.json for commit")
        except Exception:
            pass
        print("Universe refresh complete")


def fetch_shares_fast_info(tickers, shares):
    """
    Pass 1: fast_info.shares — fast batch-friendly path.
    Returns updated shares dict and list of tickers still missing.
    """
    missing = []
    for i, ticker in enumerate(tickers):
        got = False
        for attempt in range(3):
            try:
                val = yf.Ticker(ticker).fast_info.shares
                if val and val > 0:
                    shares[ticker] = float(val)
                    got = True
                break
            except Exception:
                time.sleep(1)
        if not got:
            missing.append(ticker)
        if (i + 1) % 200 == 0:
            print(f"   Pass 1: {i+1}/{len(tickers)} — {len(shares)} found, {len(missing)} missing so far")
            time.sleep(1)
    return shares, missing


def fetch_shares_from_mcap(tickers, shares):
    """
    Pass 2: fallback for tickers where fast_info.shares returned nothing.
    Uses info['marketCap'] / info['currentPrice'] to back-compute shares.
    Slower (one HTTP call per ticker) but much better coverage for smaller NSE names.
    """
    recovered = 0
    still_missing = []
    for i, ticker in enumerate(tickers):
        got = False
        for attempt in range(3):
            try:
                info = yf.Ticker(ticker).info
                mcap  = info.get('marketCap')
                price = info.get('currentPrice') or info.get('regularMarketPrice')
                if mcap and price and price > 0:
                    computed = mcap / price
                    if computed > 0:
                        shares[ticker] = float(computed)
                        got = True
                        recovered += 1
                break
            except Exception:
                time.sleep(1)
        if not got:
            still_missing.append(ticker)
        # Rate limit: this path is slower, be polite
        time.sleep(0.3)
        if (i + 1) % 50 == 0:
            print(f"   Pass 2: {i+1}/{len(tickers)} — recovered {recovered}, still missing {len(still_missing)}")
    return shares, still_missing, recovered


def main():
    print("=" * 55)
    print("  NSE Universe + Shares Outstanding Refresh")
    print("=" * 55)

    # Step 1: Refresh universe
    print("\nStep 1: Refreshing NSE universe...")
    refresh_universe()

    with open(UNIVERSE_FILE) as f:
        tickers = json.load(f)
    print(f"\nStep 2: Fetching shares outstanding for {len(tickers)} tickers...")

    # Load existing cache
    shares = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            shares = json.load(f).get('shares', {})
        print(f"   Loaded {len(shares)} cached values")

    # Pass 1: fast_info
    print("\n   Pass 1: fast_info.shares...")
    shares, missing_after_pass1 = fetch_shares_fast_info(tickers, shares)
    print(f"   Pass 1 done: {len(shares)} found, {len(missing_after_pass1)} still missing")

    # Pass 2: mcap/price fallback for anything still missing
    if missing_after_pass1:
        print(f"\n   Pass 2: marketCap/price fallback for {len(missing_after_pass1)} tickers...")
        shares, still_missing, recovered = fetch_shares_from_mcap(missing_after_pass1, shares)
        print(f"   Pass 2 done: recovered {recovered}, still missing {len(still_missing)}")
        if still_missing:
            print(f"   No data found for: {still_missing[:20]}{'...' if len(still_missing)>20 else ''}")
    else:
        print("   No pass-2 fallback needed — all tickers covered in pass 1")

    pct = len(shares) / len(tickers) * 100 if tickers else 0
    print(f"\nShares outstanding: {len(shares)}/{len(tickers)} ({pct:.1f}% coverage)")

    with open(OUTPUT_FILE, 'w') as f:
        json.dump({
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'shares': shares
        }, f)
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
