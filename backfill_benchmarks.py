"""
backfill_benchmarks.py — one-off repair of portfolio_history benchmark columns.

Rewrites nifty50_close / nifty500_close for EVERY snapshot date using NSE's
official index closes, so the whole series is in one consistent unit (index
points). The frontend indexes the series to 100 at the first date, so old ETF
prices and new index points must never be mixed — this is all-or-nothing for
Nifty 500: if any date can't be fetched, nothing is written.

DRY_RUN=1 (default): report only.  DRY_RUN=0: write to Supabase.
Writes a report to diagnostic_output/benchmark_backfill.json either way.
"""

import os, sys, json
from datetime import datetime
from supabase import create_client
from core.nse_index import fetch_index_close

DRY = os.environ.get('DRY_RUN', '1') != '0'
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

rows, start = [], 0
while True:
    page = sb.table('portfolio_history') \
        .select('owner,snapshot_date,nifty50_close,nifty500_close') \
        .order('snapshot_date').range(start, start + 999).execute().data or []
    rows += page
    if len(page) < 1000:
        break
    start += 1000

old = {}
for r in rows:
    old.setdefault(r['snapshot_date'], (r['nifty50_close'], r['nifty500_close']))
dates = sorted(old)
print(f'{"DRY RUN" if DRY else "WRITE MODE"} — {len(dates)} snapshot dates: {dates[0] if dates else "-"} → {dates[-1] if dates else "-"}')

report, new, failed = [], {}, []
for ds in dates:
    d = datetime.strptime(ds, '%Y-%m-%d').date()
    res = fetch_index_close(d)
    o50, o500 = old[ds]
    if not res or res.get('nifty500') is None or res.get('nifty50') is None:
        failed.append(ds)
        report.append({'date': ds, 'status': 'NSE file missing/unparsed', 'old_nifty50': o50, 'old_nifty500': o500})
        print(f'  {ds}: ✗ NSE index file unavailable')
        continue
    new[ds] = (round(res['nifty50'], 2), round(res['nifty500'], 2))
    report.append({'date': ds, 'status': 'ok', 'old_nifty50': o50, 'new_nifty50': new[ds][0],
                   'old_nifty500': o500, 'new_nifty500': new[ds][1]})
    print(f'  {ds}: n50 {o50} → {new[ds][0]} | n500 {o500} → {new[ds][1]}')

written = False
if failed:
    print(f'\n✗ {len(failed)} date(s) could not be fetched: {failed}. Writing NOTHING (would mix units).')
elif DRY:
    print('\nDry run complete — nothing written.')
else:
    for ds, (n50, n500) in new.items():
        sb.table('portfolio_history').update({'nifty50_close': n50, 'nifty500_close': n500}) \
            .eq('snapshot_date', ds).execute()
    written = True
    print(f'\n✅ Updated {len(new)} dates in portfolio_history.')

os.makedirs('diagnostic_output', exist_ok=True)
with open('diagnostic_output/benchmark_backfill.json', 'w') as f:
    json.dump({'generated': datetime.utcnow().isoformat() + 'Z', 'dry_run': DRY,
               'written': written, 'failed_dates': failed, 'rows': report}, f, indent=2)
sys.exit(1 if failed else 0)
