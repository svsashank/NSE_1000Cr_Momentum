"""
Momentum Live Screener — GitHub Actions version
NSE Full Universe · Fresh fetch each run · Supabase push
Uses shared core/ modules (indicators, screener_engine, data_fetcher).
"""

import os, json, time, math, warnings
import numpy as np
import pandas as pd
from datetime import datetime
from supabase import create_client

from core.data_fetcher import fetch_ohlcv
from core.indicators import compute_indicators
from core.screener_engine import run_screen

warnings.filterwarnings('ignore')

# ── Credentials ───────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

# ── Strategy parameters (loaded from config.json, INR) ────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
with open(CONFIG_FILE) as f:
    CONFIG = json.load(f)

UNIVERSE_NAME  = CONFIG['universe_name']
PORTFOLIO_SIZE = CONFIG['portfolio_size']
LOOKBACK_DAYS  = CONFIG['lookback_days']
ADV_DIVISOR    = CONFIG['adv_divisor']

UNIVERSE_FILE  = os.path.join(os.path.dirname(__file__), 'nse_universe.json')
SHARES_FILE    = os.path.join(os.path.dirname(__file__), 'shares_outstanding.json')


# ── Step 1: Load universe ─────────────────────────────────────────────────────
def load_universe():
    with open(UNIVERSE_FILE) as f:
        tickers = json.load(f)
    print(f'✅ Universe: {len(tickers)} tickers')
    return tickers


# ── Step 2: Load shares outstanding (cached weekly) ───────────────────────────
def load_shares_outstanding():
    """
    Loads pre-fetched shares-outstanding from shares_outstanding.json
    (refreshed weekly by refresh_shares.py). Market cap is then computed as
    shares × close for any date, using OHLCV data we already have — this
    eliminates the ~2200-ticker sequential yf.Ticker() loop (was ~15 min/run).
    """
    if not os.path.exists(SHARES_FILE):
        print('⚠ shares_outstanding.json not found — MCap filter disabled for this run')
        return {}, None

    with open(SHARES_FILE) as f:
        data = json.load(f)

    shares = data.get('shares', {})
    updated_at = data.get('updated_at', 'unknown')
    print(f'✅ Shares outstanding: {len(shares)} tickers (cache updated {updated_at})')
    return shares, updated_at


def build_mcap_matrix(close, shares_data):
    """
    MCap = shares outstanding (cached weekly, ~static) × close (live, per-date),
    expressed in INR Cr via ADV_DIVISOR.
    """
    shares_arr = np.array([float(shares_data.get(t, 0)) for t in close.columns], dtype=float)
    shares_arr[shares_arr == 0] = np.nan
    shares_row = pd.Series(shares_arr, index=close.columns)
    mcap_mat = close.mul(shares_row, axis=1) / ADV_DIVISOR  # ₹ Cr
    for chk in ['RELIANCE.NS', 'TCS.NS', 'INFY.NS']:
        if chk in close.columns:
            val = mcap_mat[chk].iloc[-1]
            if not np.isnan(val):
                print(f'     {chk}: ₹{val:,.0f} Cr')
    return mcap_mat


# ── Push to Supabase ──────────────────────────────────────────────────────────
def clean(val):
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, np.integer):  return int(val)
    if isinstance(val, np.floating):
        v = float(val)
        return None if (math.isnan(v) or math.isinf(v)) else v
    return val

def to_records(df):
    return [{k: clean(v) for k, v in row.items()} for _, row in df.iterrows()]

def push(supabase, top15, all_passing, rejections, screen_date):
    row = {
        'run_date'   : str(screen_date.date()),
        'universe'   : UNIVERSE_NAME,
        'top15'      : to_records(top15.reset_index())       if not top15.empty       else [],
        'all_passing': to_records(all_passing.reset_index()) if not all_passing.empty else [],
        'filters'    : {
            'universe': UNIVERSE_NAME, 'portfolio_size': PORTFOLIO_SIZE,
            'min_mcap_inr_cr': CONFIG['min_mcap'], 'min_adv_inr_cr': CONFIG['min_adv'],
            'max_vol': CONFIG['max_volatility'], 'rsi_threshold': CONFIG['rsi_threshold'],
            'max_from_high': CONFIG['max_from_high'], 'sma_short': CONFIG['sma_short'],
            'sma_long': CONFIG['sma_long'], 'cmf_period': CONFIG['cmf_period'],
            'cmf_threshold': CONFIG['cmf_threshold'],
            'rejections': rejections,
        },
        'run_status' : 'complete',
        'triggered_at': datetime.utcnow().isoformat(),
    }
    resp   = supabase.table('screen_runs').insert(row).execute()
    run_id = resp.data[0]['id'] if resp.data else None
    print(f'✅ screen_runs → id: {run_id}')

    if not all_passing.empty:
        top_set     = set(top15['ticker']) if not top15.empty else set()
        top_idx_map = {r['ticker']: int(i) for i, r in top15.reset_index().iterrows()}
        rows = []
        for _, r in all_passing.iterrows():
            t = r['ticker']
            rows.append({
                'ticker'    : t,
                'price'     : clean(r['price']),
                'sma21'     : clean(r['sma21']),
                'sma200'    : clean(r['sma200']),
                'rank_score': clean(r['rank_score']),
                'rsi14'     : clean(r['rsi']),
                'adv20'     : clean(r['adv_m']),
                'ann_vol'   : clean(r['volatility_pct']),
                'cmf'       : clean(r['cmf']),
                'high52w'   : clean(r['price'] / (1 + r['pct_from_high']/100)) if r['pct_from_high'] is not None else None,
                'passes_all': True,
                'in_top15'  : t in top_set,
                'top15_rank': top_idx_map.get(t),
                'updated_at': datetime.utcnow().isoformat(),
            })
        total = 0
        for i in range(0, len(rows), 200):
            supabase.table('stock_snapshots').upsert(rows[i:i+200], on_conflict='ticker').execute()
            total += min(200, len(rows) - i)
        print(f'✅ stock_snapshots → {total} upserted')

    return run_id


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    print('='*60)
    print('  NSE MOMENTUM LIVE SCREENER — GitHub Actions')
    print(f'  {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}')
    print('='*60)

    supabase             = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers              = load_universe()
    raw, available       = fetch_ohlcv(tickers, lookback_days=LOOKBACK_DAYS,
                                        batch_size=50, recover_time_budget=900)
    screen_tickers       = [t for t in tickers if t in available]
    shares, _            = load_shares_outstanding()

    # Build the per-date mcap matrix (shares x close) using close prices for
    # the screenable tickers, BEFORE ffill is applied inside compute_indicators
    # — but since ffill there only affects close/high/low used for indicators,
    # we recompute the same ffill here for consistency on the mcap matrix.
    close_for_mcap = raw['Close'][[t for t in screen_tickers if t in raw['Close'].columns]].astype(float).ffill(limit=3)
    mcap_matrix = build_mcap_matrix(close_for_mcap, shares)

    print('\n⏳ Computing indicators...')
    ind                  = compute_indicators(raw, mcap_matrix, screen_tickers, CONFIG)

    print('\n⏳ Running screen...')
    top15, all_passing, rejections, screen_date = run_screen(ind, CONFIG)

    print('\n📤 Pushing to Supabase...')
    run_id = push(supabase, top15, all_passing, rejections, screen_date)

    print(f'\n✅ Done in {(time.time()-t0)/60:.1f} min — run_id: {run_id}')

if __name__ == '__main__':
    main()
