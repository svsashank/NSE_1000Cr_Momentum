"""
Reproduce the EXACT production batch fetch (batch_size=50) that contains
STLTECH.NS, to see if it's a batch-fetch casualty rather than a genuine
data-history problem (single-ticker fetch showed 373/373 clean rows).
"""
import json
import pandas as pd
from datetime import datetime, timedelta
from core.data_fetcher import fetch_ohlcv

with open('nse_universe.json') as f:
    universe = json.load(f)

idx = universe.index('STLTECH.NS')
batch_start = (idx // 50) * 50
batch = universe[batch_start:batch_start + 50]

lines = []
lines.append(f'Testing production batch containing STLTECH.NS ({len(batch)} tickers)')
lines.append(f'Batch: {batch}\n')

raw, available = fetch_ohlcv(batch, lookback_days=550, batch_size=50, recover_time_budget=120)

lines.append(f'\navailable set size: {len(available)}/{len(batch)}')
lines.append(f'STLTECH.NS in available: {"STLTECH.NS" in available}')
lines.append(f'STLNETWORK.NS in available: {"STLNETWORK.NS" in available}')
missing = [t for t in batch if t not in available]
lines.append(f'missing from batch: {missing}')

if 'STLTECH.NS' in raw['Close'].columns:
    close = raw['Close']['STLTECH.NS'].dropna()
    lines.append(f'\nSTLTECH.NS in raw Close columns: True')
    lines.append(f'  non-null rows: {len(close)}')
    lines.append(f'  last date: {close.index.max()}')
    sma200 = close.rolling(200).mean()
    lines.append(f'  SMA200 last value: {sma200.iloc[-1]}')
    lines.append(f'  SMA200 non-null count: {sma200.notna().sum()}')
else:
    lines.append(f'\nSTLTECH.NS NOT in raw Close columns at all (dropped during batch/concat)')
    lines.append(f'Actual columns present: {sorted(raw["Close"].columns.tolist())}')

with open('diagnostic_output.txt', 'w') as f:
    f.write('\n'.join(lines))

print('\n'.join(lines))
