import json, os, io, csv, urllib.request
from datetime import datetime, timedelta
import yfinance as yf
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
     'Accept': '*/*', 'Referer': 'https://www.nseindia.com/'}
WANT = ['AASTHA','BAJAJ-AUTO','DELTACORP','ELIN','ESSARSHPNG','RELIANCE']
out = {'bhavcopy': {}, 'yahoo': {}}
for d in ['29092026', '30092026', '01102026', '02102026']:
    url = f'https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv'
    try:
        txt = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30).read().decode('utf-8','ignore')
        rows = [{(k or '').strip(): (v or '').strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(txt))]
        out['bhavcopy'][d] = {'status': 'OK', 'n_rows': len(rows),
            'DATE1_values': sorted({r.get('DATE1') for r in rows}),
            'sample': {r['SYMBOL']: {k: r.get(k) for k in ('SERIES','PREV_CLOSE','CLOSE_PRICE')}
                       for r in rows if r.get('SYMBOL') in WANT}}
    except Exception as e:
        out['bhavcopy'][d] = {'status': f'FAIL {type(e).__name__}: {str(e)[:80]}'}
d = yf.download([w + '.NS' for w in WANT], start='2026-09-25', end='2026-10-05', auto_adjust=True, progress=False)
for w in WANT:
    s = d['Close'][w + '.NS']
    out['yahoo'][w] = {str(i.date()): (None if v != v else round(float(v), 2)) for i, v in s.items()}
os.makedirs('diagnostic_output', exist_ok=True)
json.dump(out, open('diagnostic_output/price_check.json', 'w'), indent=2)
