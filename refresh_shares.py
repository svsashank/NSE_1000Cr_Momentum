"""
Weekly shares-outstanding fetcher.

Market cap = shares_outstanding × latest_close. Shares outstanding changes
rarely (buybacks, splits, FPOs) — fetching it weekly and caching to
shares_outstanding.json lets the main screener compute mcap instantly from
data it already has (close prices), eliminating the ~2200-ticker sequential
yf.Ticker() loop that previously took ~15 min per run.

Run via a separate weekly workflow (shares_refresh.yml).
"""

import json, time, os
import yfinance as yf

UNIVERSE_FILE = os.path.join(os.path.dirname(__file__), 'nse_universe.json')
OUTPUT_FILE   = os.path.join(os.path.dirname(__file__), 'shares_outstanding.json')


def main():
    with open(UNIVERSE_FILE) as f:
        tickers = json.load(f)

    print(f'⏳ Fetching shares outstanding for {len(tickers)} tickers...')

    # Load existing cache so a partial run doesn't lose previously-fetched values
    shares = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            shares = json.load(f).get('shares', {})
        print(f'   Loaded {len(shares)} cached values')

    for i, ticker in enumerate(tickers):
        for attempt in range(3):
            try:
                val = yf.Ticker(ticker).fast_info.shares
                if val and val > 0:
                    shares[ticker] = float(val)
                break
            except:
                time.sleep(1)
        if (i + 1) % 200 == 0:
            print(f'   {i+1}/{len(tickers)} done — {len(shares)} found')
            time.sleep(1)

    pct = len(shares) / len(tickers) * 100 if tickers else 0
    print(f'✅ Shares outstanding: {len(shares)}/{len(tickers)} ({pct:.0f}% coverage)')

    with open(OUTPUT_FILE, 'w') as f:
        json.dump({'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    'shares': shares}, f)
    print(f'✅ Wrote {OUTPUT_FILE}')


if __name__ == '__main__':
    main()
