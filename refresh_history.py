"""
refresh_history.py -- Fetch multi-year OHLCV for the full NSE universe and
store it in Supabase Storage (parquet), merging with existing stored history.

Also refreshes nse_universe.json first (from NSE EQUITY_L.csv) so the
monthly history pull always covers the current ticker set, including any
new listings or re-rated stocks that crossed the MCap threshold.

Run periodically (monthly) via GitHub Actions.
"""

import os, json, time, sys, subprocess, warnings
from datetime import datetime
from supabase import create_client

from core.data_fetcher import fetch_ohlcv
from core.history_store import (
    load_history, save_history, merge_history, raw_multiindex_to_fields
)

warnings.filterwarnings('ignore')

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
with open(CONFIG_FILE) as f:
    CONFIG = json.load(f)

UNIVERSE_NAME = CONFIG['universe_name']
UNIVERSE_FILE = os.path.join(os.path.dirname(__file__), 'nse_universe.json')

HISTORY_YEARS = int(os.environ.get('HISTORY_YEARS', '7'))


def refresh_universe():
    """Refresh nse_universe.json from NSE's live listing before fetching history."""
    result = subprocess.run([sys.executable, "refresh_universe.py"],
                            capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"⚠ Universe refresh exited {result.returncode} — using existing file")
        if result.stderr:
            print(result.stderr[:500])
    else:
        print("✅ Universe refresh complete")


def main():
    t0 = time.time()
    print('='*60)
    print('  NSE OHLCV HISTORY REFRESH — Supabase Storage')
    print(f'  Universe: {UNIVERSE_NAME}')
    print(f'  {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}')
    print('='*60)

    # Step 0: Refresh universe (runs before history fetch so new tickers get history too)
    print('\n⏳ Step 0: Refreshing NSE universe...')
    refresh_universe()

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    with open(UNIVERSE_FILE) as f:
        tickers = json.load(f)
    print(f'Universe: {len(tickers)} tickers, fetching {HISTORY_YEARS} years')

    raw, available = fetch_ohlcv(tickers, lookback_days=HISTORY_YEARS * 365,
                                  batch_size=50, recover_time_budget=1200)
    fresh = raw_multiindex_to_fields(raw)

    print('\nLoading existing stored history (if any)...')
    existing = load_history(supabase, UNIVERSE_NAME)
    if existing is not None:
        print(f'   Existing: {existing["Close"].shape[1]} tickers, '
              f'{existing["Close"].index[0].date()} -> {existing["Close"].index[-1].date()}')
    else:
        print('   No existing history -- first run')

    print('\nMerging...')
    merged = merge_history(existing, fresh)

    print('\nSaving to Supabase Storage...')
    save_history(supabase, UNIVERSE_NAME, merged)

    print(f'\nDone in {(time.time()-t0)/60:.1f} min')


if __name__ == '__main__':
    main()
