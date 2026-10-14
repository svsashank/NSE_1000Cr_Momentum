"""
Rebuild portfolio_history.total_value from NSE's official closing files.

Why: before the valuation fix, snapshots priced held stocks that had dropped out
of the passing set at frozen stock_snapshots prices. This recomputes total_value
for past dates using the owner's CURRENT holdings and each date's official close.

SAFE BY DEFAULT: dry run (prints old vs new). Pass --apply to write.
Only use a --from date on/after the day the current holdings were uploaded,
otherwise earlier days would be valued with the wrong basket.

Usage: python backfill_portfolio_value.py --owner sashank --from 2026-10-10 [--apply]
"""
import os, sys, argparse, urllib.request
from datetime import datetime
import pandas as pd
from supabase import create_client
from core.nse_bhavcopy import URL, HEADERS, parse_bhavcopy


def bhav_for(date_str):
    """Official closes {symbol: close} for exactly date_str, else None."""
    d = datetime.strptime(date_str, '%Y-%m-%d')
    try:
        req = urllib.request.Request(URL.format(ddmmyyyy=d.strftime('%d%m%Y')), headers=HEADERS)
        recs = parse_bhavcopy(urllib.request.urlopen(req, timeout=40).read().decode('utf-8', 'ignore'))
    except Exception as e:
        print(f'  {date_str}: bhavcopy fetch failed ({e})'); return None
    if len(recs) < 500: return None
    dates = pd.Series([r['date'] for r in recs.values() if r['date']]).mode()
    if dates.empty or pd.to_datetime(dates[0], format='%d-%b-%Y').strftime('%Y-%m-%d') != date_str:
        print(f'  {date_str}: file holds a different trading day (holiday?) — skipped'); return None
    return {s: float(r['Close']) for s, r in recs.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--owner', required=True)
    ap.add_argument('--from', dest='from_date', required=True)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])
    hold = sb.table('portfolio').select('ticker,shares,avg_cost').eq('owner', a.owner).execute().data
    rows = sb.table('portfolio_history').select('snapshot_date,total_value,cost_basis') \
        .eq('owner', a.owner).gte('snapshot_date', a.from_date).order('snapshot_date').execute().data
    print(f'{a.owner}: {len(hold)} holdings, {len(rows)} history rows from {a.from_date}  '
          f'[{"APPLY" if a.apply else "DRY RUN"}]')

    for r in rows:
        d = r['snapshot_date']
        px = bhav_for(d)
        if px is None: continue
        total, missing = 0.0, []
        for h in hold:
            sym = h['ticker'].replace('.NS', '').upper().strip()
            if sym == 'LIQUIDCASE': p = float(h['avg_cost'])
            elif sym in px:         p = px[sym]
            else:                   p = None; missing.append(sym)
            if p is not None: total += p * float(h['shares'])
        if missing:
            print(f'  {d}: ⚠ no official close for {missing} — NOT touching this row'); continue
        old = float(r['total_value'] or 0)
        print(f'  {d}: {old:>12,.0f} -> {total:>12,.0f}  ({(total/old-1)*100:+.2f}%)' if old else f'  {d}: -> {total:,.0f}')
        if a.apply:
            sb.table('portfolio_history').update({'total_value': round(total, 2)}) \
              .eq('owner', a.owner).eq('snapshot_date', d).execute()
    print('done' if a.apply else 'dry run only — re-run with apply=true to write')

if __name__ == '__main__':
    main()
