"""Diagnostic: can GitHub Actions fetch NSE's official bhavcopy for 29-Sep-2026?"""
import json, os, io, csv, zipfile, urllib.request
from datetime import datetime, timezone
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
     'Accept': '*/*', 'Referer': 'https://www.nseindia.com/'}
URLS = {
  'sec_bhavdata_full': 'https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_29092026.csv',
  'udiff_bhavcopy':    'https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20260929_F_0000.csv.zip',
}
WANT = {'CUPID','SIGMAADV','STLTECH','SHILPAMED','WELCORP','E2E','DIACABS','HFCL'}
out = {'run_utc': datetime.now(timezone.utc).isoformat(), 'results': {}}
for name, url in URLS.items():
    res = {'url': url}
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30).read()
        res['status'] = 'OK'; res['bytes'] = len(raw)
        if url.endswith('.zip'):
            z = zipfile.ZipFile(io.BytesIO(raw)); raw = z.read(z.namelist()[0])
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8', 'ignore'))))
        rows = [{k.strip(): (v or '').strip() for k, v in r.items()} for r in rows]
        res['n_rows'] = len(rows); res['columns'] = list(rows[0].keys()) if rows else []
        symk = 'SYMBOL' if rows and 'SYMBOL' in rows[0] else 'TckrSymb'
        res['sample'] = [r for r in rows if r.get(symk) in WANT]
    except Exception as e:
        res['status'] = f'FAIL: {type(e).__name__}: {str(e)[:120]}'
    out['results'][name] = res
os.makedirs('diagnostic_output', exist_ok=True)
json.dump(out, open('diagnostic_output/price_check.json', 'w'), indent=2)
print(json.dumps(out, indent=2)[:3000])
