"""
Weekly shares-outstanding fetcher.
Also refreshes nse_universe.json first (from NSE EQUITY_L.csv).

Shares fetch strategy (single fast_info pass, two attribute attempts):
  Attempt A: fast_info.shares                          (direct, fast)
  Attempt B: fast_info.market_cap / fast_info.last_price  (derived, still fast)

Both use the same fast_info object — no slow .info call needed.
This handles NSE tickers (like KPL) where .shares is missing but .market_cap
is populated, without any extra HTTP round-trips or rate-limit risk.
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
        except Exception:
            pass


def get_shares(ticker):
    """
    Try two fast_info attributes to get shares outstanding.
    Returns float or None. Never raises.
    """
    for attempt in range(3):
        try:
            fi = yf.Ticker(ticker).fast_info

            # Attempt A: direct shares field
            val = fi.shares
            if val and val > 0:
                return float(val)

            # Attempt B: derive from market_cap / last_price
            mcap  = fi.market_cap
            price = fi.last_price
            if mcap and price and mcap > 0 and price > 0:
                return float(mcap / price)

            return None   # yfinance has no data for this ticker
        except Exception:
            time.sleep(1)
    return None


def main():
    print("=" * 55)
    print("  NSE Universe + Shares Outstanding Refresh")
    print("=" * 55)

    print("\nStep 1: Refreshing NSE universe...")
    refresh_universe()

    with open(UNIVERSE_FILE) as f:
        tickers = json.load(f)
    print(f"\nStep 2: Fetching shares for {len(tickers)} tickers (fast_info)...")

    # Load existing cache
    shares = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            shares = json.load(f).get('shares', {})
        print(f"   Loaded {len(shares)} cached values")

    missing = []
    for i, ticker in enumerate(tickers):
        val = get_shares(ticker)
        if val:
            shares[ticker] = val
        else:
            missing.append(ticker)

        if (i + 1) % 200 == 0:
            print(f"   {i+1}/{len(tickers)} done — {len(shares)} found, {len(missing)} missing")
            time.sleep(1)

    pct = len(shares) / len(tickers) * 100 if tickers else 0
    print(f"\nCoverage: {len(shares)}/{len(tickers)} ({pct:.1f}%)")
    if missing:
        print(f"Still missing ({len(missing)}): {missing[:30]}{'...' if len(missing)>30 else ''}")

    with open(OUTPUT_FILE, 'w') as f:
        json.dump({
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'shares': shares
        }, f)
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
