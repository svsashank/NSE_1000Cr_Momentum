"""
Portfolio Snapshot — runs after every screen, writes one row per user to portfolio_history.

Reads portfolio table (owner, ticker, shares, avg_cost), prices from stock_snapshots
(just refreshed) + yfinance for tickers not in universe (GOLDBEES, LIQUIDCASE etc.),
fetches Nifty 50 / Nifty 500 closes, upserts into portfolio_history.

cost_basis = sum(shares × avg_cost) — recorded automatically on every run.
The frontend uses day-over-day changes in cost_basis to detect capital injections
and compute time-weighted returns, with no manual input required from the user.
"""

import os, math, warnings
import numpy as np
import yfinance as yf
from datetime import date, datetime
from supabase import create_client

warnings.filterwarnings('ignore')

SUPABASE_URL   = os.environ['SUPABASE_URL']
SUPABASE_KEY   = os.environ['SUPABASE_KEY']

NIFTY50_SYM    = '^NSEI'
# Nifty 500: yfinance ticker is inconsistent; try each in order
NIFTY500_SYMS  = ['^CNX500', 'CNX500.NS', '^NSEI500']


def fetch_single_close(sym):
    """Fetch last close for a single ticker. Returns None on failure."""
    try:
        t    = yf.Ticker(sym)
        hist = t.history(period='5d')
        if not hist.empty and not hist['Close'].dropna().empty:
            return float(hist['Close'].dropna().iloc[-1])
    except Exception:
        pass
    return None


def fetch_benchmark_closes():
    """Fetch last close for Nifty 50 and Nifty 500."""
    n50 = fetch_single_close(NIFTY50_SYM)

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
        key = r['ticker'].replace('.NS', '').upper()
        if r['price']:
            price_map[key] = float(r['price'])
    print(f'  Loaded {len(price_map)} prices from stock_snapshots')

    # ── 2. Fetch benchmark closes ─────────────────────────────────────────────
    nifty50_close, nifty500_close = fetch_benchmark_closes()

    # ── 3. Process each user ─────────────────────────────────────────────────
    owners        = ['sashank', 'sneha', 'abhilash']
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
                'cost_basis'    : None,
                'num_holdings'  : 0,
                'nifty50_close' : round(nifty50_close, 2) if nifty50_close else None,
                'nifty500_close': round(nifty500_close, 2) if nifty500_close else None,
            })
            continue

        total_value  = 0.0
        cost_basis   = 0.0   # sum(shares × avg_cost) — invested capital
        num_holdings = 0
        missing      = []

        for h in holdings:
            ticker   = (h['ticker'] or '').strip()
            shares   = float(h['shares']   or 0)
            avg_cost = float(h['avg_cost'] or 0)

            if shares <= 0:
                continue

            key = ticker.replace('.NS', '').upper()

            # LIQUIDCASE is cash-equivalent — value at avg_cost
            if key == 'LIQUIDCASE':
                price = avg_cost
            else:
                price = price_map.get(key)
                if price is None:
                    yf_sym = ticker if ticker.endswith('.NS') else f'{ticker}.NS'
                    price  = fetch_single_close(yf_sym)
                    if price:
                        print(f'    {owner} / {ticker}: fetched via yf fallback ₹{price:.2f}')
                if price is None:
                    price = avg_cost   # last resort: cost basis
                    missing.append(ticker)

            total_value += price    * shares
            cost_basis  += avg_cost * shares   # always uses avg_cost regardless of price
            num_holdings += 1

        if missing:
            print(f'  {owner}: ⚠ price fallback to avg_cost for {missing}')

        print(f'  {owner}: market ₹{total_value:,.2f} | invested ₹{cost_basis:,.2f} | {num_holdings} holdings')

        snapshot_rows.append({
            'owner'         : owner,
            'snapshot_date' : today,
            'total_value'   : round(total_value, 2),
            'cost_basis'    : round(cost_basis,  2),
            'num_holdings'  : num_holdings,
            'nifty50_close' : round(nifty50_close,  2) if nifty50_close  else None,
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
