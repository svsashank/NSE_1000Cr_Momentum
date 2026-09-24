"""
Portfolio Snapshot — runs after every screen, writes one row per user to portfolio_history.

Reads portfolio table (owner, ticker, shares, avg_cost), prices from stock_snapshots
(just refreshed) + yfinance for tickers not in universe (GOLDBEES, LIQUIDCASE etc.),
fetches Nifty 50 (^NSEI) and Nifty 500 (^CNX500) closes, upserts into portfolio_history.
"""

import os, math, warnings
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date, datetime
from supabase import create_client

warnings.filterwarnings('ignore')

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

GOLDBEES_SYM   = 'GOLDBEES.NS'
LIQUIDCASE_SYM = 'LIQUIDCASE.NS'
NIFTY50_SYM    = '^NSEI'
# Nifty 500: yfinance ticker is inconsistent; try multiple known symbols
NIFTY500_SYMS  = ['^CNX500', 'CNX500.NS', '^NSEI500']

def fetch_single_close(sym):
    """Fetch last close for a single ticker. Returns None on failure."""
    try:
        t = yf.Ticker(sym)
        hist = t.history(period='5d')
        if not hist.empty and not hist['Close'].dropna().empty:
            return float(hist['Close'].dropna().iloc[-1])
    except Exception:
        pass
    return None

def fetch_benchmark_closes():
    """Fetch last close for Nifty 50 and Nifty 500."""
    # Nifty 50
    n50 = fetch_single_close(NIFTY50_SYM)

    # Nifty 500 — try each known symbol until one works
    n500 = None
    for sym in NIFTY500_SYMS:
        val = fetch_single_close(sym)
        if val is not None:
            print(f'  Nifty 500 fetched via {sym}: {val}')
            n500 = val
            break
    if n500 is None:
        print(f'  ⚠ Nifty 500: all symbols failed {NIFTY500_SYMS}')

    print(f'  Nifty 50: {n50}  Nifty 500: {n500}')
    return n50, n500
    if val is None: return None
    try:
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)): return None
        if isinstance(val, (np.integer,)): return int(val)
        if isinstance(val, (np.floating,)):
            v = float(val)
            return None if (math.isnan(v) or math.isinf(v)) else round(v, 4)
    except: pass
    return val

def main():
    print('\n' + '='*50)
    print('  PORTFOLIO SNAPSHOT')
    print(f'  {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}')
    print('='*50)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    today    = str(date.today())

    # ── 1. Load latest prices from stock_snapshots (just refreshed by screener) ──
    snap_rows = supabase.table('stock_snapshots').select('ticker,price').execute().data or []
    price_map = {}
    for r in snap_rows:
        t = r['ticker']
        # Normalise: strip .NS suffix for lookup, keep original for yf fallback
        key = t.replace('.NS', '').upper()
        if r['price']:
            price_map[key] = float(r['price'])
    print(f'  Loaded {len(price_map)} prices from stock_snapshots')

    # ── 2. Fetch benchmark closes ─────────────────────────────────────────────
    nifty50_close, nifty500_close = fetch_benchmark_closes()

    # ── 3. Process each user ─────────────────────────────────────────────────
    owners = ['sashank', 'sneha', 'abhilash']
    snapshot_rows = []

    for owner in owners:
        holdings = supabase.table('portfolio') \
            .select('ticker,shares,avg_cost') \
            .eq('owner', owner) \
            .execute().data or []

        if not holdings:
            print(f'  {owner}: no holdings — writing benchmark-only row')
            snapshot_rows.append({
                'owner'         : owner,
                'snapshot_date' : today,
                'total_value'   : None,
                'num_holdings'  : 0,
                'nifty50_close' : round(nifty50_close, 2) if nifty50_close else None,
                'nifty500_close': round(nifty500_close, 2) if nifty500_close else None,
            })
            continue

        total_value  = 0.0
        num_holdings = 0
        missing      = []

        for h in holdings:
            ticker    = (h['ticker'] or '').strip()
            shares    = float(h['shares'] or 0)
            avg_cost  = float(h['avg_cost'] or 0)

            if shares <= 0:
                continue

            # Normalise key for price_map lookup
            key = ticker.replace('.NS', '').upper()

            # Special: LIQUIDCASE is cash-equivalent, value it at avg_cost
            if key == 'LIQUIDCASE':
                price = avg_cost
            else:
                price = price_map.get(key)
                # Fallback: fetch directly from yfinance for GOLDBEES etc.
                if price is None:
                    yf_sym = ticker if ticker.endswith('.NS') else f'{ticker}.NS'
                    price  = fetch_single_close(yf_sym)
                    if price:
                        print(f'    {owner} / {ticker}: fetched via yf fallback ₹{price:.2f}')

                if price is None:
                    price = avg_cost   # last resort: cost basis
                    missing.append(ticker)

            total_value  += price * shares
            num_holdings += 1

        if missing:
            print(f'  {owner}: ⚠ price fallback to avg_cost for {missing}')

        print(f'  {owner}: ₹{total_value:,.2f} across {num_holdings} holdings')

        snapshot_rows.append({
            'owner'         : owner,
            'snapshot_date' : today,
            'total_value'   : round(total_value, 2),
            'num_holdings'  : num_holdings,
            'nifty50_close' : round(nifty50_close, 2) if nifty50_close else None,
            'nifty500_close': round(nifty500_close, 2) if nifty500_close else None,
        })

    # ── 4. Upsert to portfolio_history ────────────────────────────────────────
    if snapshot_rows:
        supabase.table('portfolio_history') \
            .upsert(snapshot_rows, on_conflict='owner,snapshot_date') \
            .execute()
        print(f'\n✅ portfolio_history: upserted {len(snapshot_rows)} rows for {today}')
    else:
        print('\n⚠ No snapshot rows to write — all users have empty portfolios?')

if __name__ == '__main__':
    main()
