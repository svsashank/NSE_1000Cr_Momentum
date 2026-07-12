"""
Full-scale reproduction of screener.py's exact pipeline (fetch_ohlcv on the
FULL 2384-ticker universe, then compute_indicators) to see whether STLTECH
ends up in `valid` or `no_data_tickers` for real, at production scale —
rather than in an isolated 50-ticker batch test.
"""
import json
import numpy as np
from core.data_fetcher import fetch_ohlcv
from core.indicators import compute_indicators

with open('nse_universe.json') as f:
    tickers = json.load(f)

with open('config.json') as f:
    CONFIG = json.load(f)

with open('shares_outstanding.json') as f:
    shares_data = json.load(f)['shares']

lines = [f'Full universe: {len(tickers)} tickers']

raw, available = fetch_ohlcv(tickers, lookback_days=CONFIG['lookback_days'],
                              batch_size=50, recover_time_budget=600)
screen_tickers = [t for t in tickers if t in available]
lines.append(f'available after fetch: {len(available)}/{len(tickers)}')
lines.append(f'STLTECH.NS in available: {"STLTECH.NS" in available}')

# Build mcap matrix same way screener.py does
import pandas as pd
close_for_mcap = raw['Close'][[t for t in screen_tickers if t in raw['Close'].columns]].astype(float).ffill(limit=3)
shares_arr = np.array([float(shares_data.get(t, 0)) for t in close_for_mcap.columns], dtype=float)
mcap_matrix = close_for_mcap.mul(shares_arr / 1e7, axis=1)  # approx, crore

ind = compute_indicators(raw, mcap_matrix, screen_tickers, CONFIG)

close_row = ind['close'].iloc[-1]
sma_s_row = ind['sma_short'].iloc[-1]
sma_l_row = ind['sma_long'].iloc[-1]

if 'STLTECH.NS' in close_row.index:
    lines.append(f'\nSTLTECH.NS final row:')
    lines.append(f'  close: {close_row["STLTECH.NS"]}')
    lines.append(f'  sma21: {sma_s_row["STLTECH.NS"]}')
    lines.append(f'  sma200: {sma_l_row["STLTECH.NS"]}')
    valid = pd.notna(close_row["STLTECH.NS"]) and pd.notna(sma_s_row["STLTECH.NS"]) and pd.notna(sma_l_row["STLTECH.NS"])
    lines.append(f'  WOULD BE VALID: {valid}')
else:
    lines.append(f'\nSTLTECH.NS NOT in ind[\"close\"] columns at all')

with open('diagnostic_output.txt', 'w') as f:
    f.write('\n'.join(lines))
print('\n'.join(lines))
