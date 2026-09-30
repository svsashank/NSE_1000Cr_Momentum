"""Diagnostic: what does Yahoo return for the latest bars? Writes JSON to repo."""
import json, os
from datetime import datetime, timedelta, timezone
import pandas as pd, yfinance as yf

T = ['CUPID.NS','SIGMAADV.NS','STLTECH.NS','SHILPAMED.NS','WELCORP.NS','E2E.NS',
     'RELIANCE.NS','TCS.NS','^NSEI']
out = {'run_utc': datetime.now(timezone.utc).isoformat(), 'yfinance': yf.__version__}

end = (datetime.today().date() + timedelta(days=2)).strftime('%Y-%m-%d')
start = (datetime.today().date() - timedelta(days=10)).strftime('%Y-%m-%d')

# 1. Exactly as the screener does it (batch download, auto_adjust)
d = yf.download(T, start=start, end=end, auto_adjust=True, progress=False, threads=True)
out['batch_download'] = {t: {str(i.date()): (None if pd.isna(v) else round(float(v),2))
                             for i, v in d['Close'][t].tail(4).items()} for t in T}

# 2. Per-ticker history (5d) with raw timestamps, plus live quote
out['ticker_history'] = {}; out['live_quote'] = {}
for t in T:
    try:
        h = yf.Ticker(t).history(period='5d', auto_adjust=True)
        out['ticker_history'][t] = {str(i): round(float(v),2) for i, v in h['Close'].tail(4).items()}
    except Exception as e:
        out['ticker_history'][t] = f'ERR {e}'
    try:
        fi = yf.Ticker(t).fast_info
        out['live_quote'][t] = {'last_price': round(float(fi['last_price']),2),
                                'previous_close': round(float(fi['previous_close']),2)}
    except Exception as e:
        out['live_quote'][t] = f'ERR {e}'

os.makedirs('diagnostic_output', exist_ok=True)
json.dump(out, open('diagnostic_output/price_check.json','w'), indent=2)
print(json.dumps(out, indent=2))
