"""Diagnostic: real Yahoo fetch + real NSE bhavcopy patch, before production rollout."""
import json, os
from core.data_fetcher import fetch_ohlcv
from core.nse_bhavcopy import patch_latest_with_bhavcopy
T = ['CUPID.NS','SIGMAADV.NS','STLTECH.NS','SHILPAMED.NS','WELCORP.NS','E2E.NS','DIACABS.NS',
     'HFCL.NS','BLISSGVS.NS','KABRAEXTRU.NS','RELIANCE.NS','TCS.NS','^NSEI']
raw, avail = fetch_ohlcv(T, lookback_days=30, batch_size=50, recover_time_budget=60)
before = {t: {str(i.date()): (None if v != v else round(float(v), 2)) for i, v in raw['Close'][t].tail(2).items()} for t in avail}
raw, stats = patch_latest_with_bhavcopy(raw)
after = {t: {str(i.date()): (None if v != v else round(float(v), 2)) for i, v in raw['Close'][t].tail(2).items()} for t in avail}
os.makedirs('diagnostic_output', exist_ok=True)
json.dump({'stats': stats, 'before': before, 'after': after}, open('diagnostic_output/price_check.json', 'w'), indent=2)
