"""
One-off diagnostic: why do STLTECH.NS and related tickers land in
'Insufficient Data' (valid=False, i.e. NaN close/SMA21/SMA200)?

Checks: row count, date range, gap size, whether the current SMA200
window is fully populated, given the demerger break in April 2025.
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

TICKERS = ['STLTECH.NS', 'STLNETWORK.NS', 'SIGMAADV.NS', 'UFBL.NS']

end_date = datetime.today().date() + timedelta(days=2)
start_date = end_date - timedelta(days=550)

lines = []
lines.append(f'Fetch window: {start_date} -> {end_date}\n')

for t in TICKERS:
    lines.append(f'\n=== {t} ===')
    try:
        df = yf.download(t, start=start_date.isoformat(), end=end_date.isoformat(),
                          progress=False, auto_adjust=False)
        if df.empty:
            lines.append('  EMPTY dataframe returned')
            continue
        close = df['Close'].dropna()
        lines.append(f'  total rows: {len(df)}')
        lines.append(f'  non-null Close rows: {len(close)}')
        lines.append(f'  first date: {close.index.min().date() if len(close) else None}')
        lines.append(f'  last date : {close.index.max().date() if len(close) else None}')
        # check for a gap (missing >5 consecutive trading days)
        idx = close.index.to_series()
        gaps = idx.diff().dt.days.dropna()
        max_gap = gaps.max() if len(gaps) else None
        lines.append(f'  max gap between consecutive rows (days): {max_gap}')
        # SMA200 feasibility at the last available date
        sma200_valid = len(close) >= 200 and not close.tail(200).isna().any()
        lines.append(f'  enough clean data for SMA200 as of last date: {sma200_valid}')
        sma21_valid = len(close) >= 21 and not close.tail(21).isna().any()
        lines.append(f'  enough clean data for SMA21: {sma21_valid}')
    except Exception as e:
        lines.append(f'  ERROR: {e}')

with open('diagnostic_output.txt', 'w') as f:
    f.write('\n'.join(lines))

print('\n'.join(lines))
