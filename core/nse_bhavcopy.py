"""
core/nse_bhavcopy.py — NSE's official end-of-day file (bhavcopy) as the source
of truth for the LATEST trading day. Yahoo remains the source for history.

Why: Yahoo publishes NSE daily bars late and sometimes leaves them blank
(especially BE-series stocks). The screener's forward-fill then silently used
the previous day's close. The bhavcopy has the official close for every stock
in every series.

Design principle — fail safe:
  * If the bhavcopy can't be fetched, the run behaves exactly as before.
  * If NSE's previous close disagrees sharply with Yahoo's history for a stock
    (a suspected corporate action, e.g. split/bonus Yahoo hasn't adjusted yet),
    that stock is NOT patched — it keeps pre-existing behaviour — and is flagged.
    Nothing is rescaled; the system self-heals once Yahoo adjusts its history.
"""

import io
import csv
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

import pandas as pd

VALID_SERIES   = {'EQ', 'BE', 'BZ', 'ST'}
SERIES_PRIORITY = {'EQ': 0, 'BE': 1, 'BZ': 2, 'ST': 3}
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124 Safari/537.36',
    'Accept': '*/*',
    'Referer': 'https://www.nseindia.com/',
}
URL = 'https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv'

# Corporate-action check thresholds (NSE prev close vs Yahoo close)
STRICT_TOL = 0.05   # Yahoo's last bar IS the previous trading day: should match almost exactly
LOOSE_TOL  = 0.25   # Yahoo's last bar is older (itself stale): only catch split/bonus-sized gaps
FIELDS = {'Open': 'OPEN_PRICE', 'High': 'HIGH_PRICE', 'Low': 'LOW_PRICE',
          'Close': 'CLOSE_PRICE', 'Volume': 'TTL_TRD_QNTY'}


def _num(v):
    try:
        return float(str(v).strip().replace(',', ''))
    except (TypeError, ValueError):
        return None


def parse_bhavcopy(text):
    """Parse sec_bhavdata_full CSV text → {symbol: record} for valid series."""
    out = {}
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        r = {(k or '').strip(): (v or '').strip() for k, v in raw.items()}
        series = r.get('SERIES', '')
        if series not in VALID_SERIES:
            continue
        rec = {'series': series, 'prev_close': _num(r.get('PREV_CLOSE')),
               'date': r.get('DATE1', '')}
        for f, col in FIELDS.items():
            rec[f] = _num(r.get(col))
        if rec['Close'] is None or rec['Close'] <= 0:
            continue
        sym = r.get('SYMBOL', '')
        # If a symbol appears in more than one valid series, prefer EQ > BE > BZ > ST
        if sym in out and SERIES_PRIORITY[out[sym]['series']] <= SERIES_PRIORITY[series]:
            continue
        out[sym] = rec
    return out


def fetch_latest_bhavcopy(max_days_back=7, timeout=30):
    """Return (trade_date: pd.Timestamp, records: dict) for the most recent
    published bhavcopy, or (None, {}) if none could be fetched."""
    today_ist = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()
    for back in range(max_days_back + 1):
        d = today_ist - timedelta(days=back)
        if d.weekday() >= 5:            # skip weekends
            continue
        url = URL.format(ddmmyyyy=d.strftime('%d%m%Y'))
        try:
            req  = urllib.request.Request(url, headers=HEADERS)
            text = urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')
            recs = parse_bhavcopy(text)
            if len(recs) > 500:         # sanity: a real file has thousands of rows
                # Trust the trade date INSIDE the file, never the URL. On holidays NSE
                # serves the previous session's file under the holiday's name
                # (e.g. 02-Oct-2026 returned 01-Oct-2026 data).
                dates = Counter(r['date'] for r in recs.values() if r['date'])
                file_date = pd.to_datetime(dates.most_common(1)[0][0], format='%d-%b-%Y')
                if file_date.date() != d:
                    print(f'   bhavcopy requested for {d} contains {file_date.date()} '
                          f'(holiday/non-trading day) — using {file_date.date()}')
                return file_date, recs
        except Exception:
            continue                    # 404 = not published / holiday → try earlier day
    return None, {}


def apply_bhavcopy(raw, bhav_date, bhav):
    """Patch the bhav_date bar in `raw` (MultiIndex columns: field, ticker)
    with official NSE values. Returns (raw, stats)."""
    stats = {'bhav_date': str(bhav_date.date()), 'filled': 0, 'corrected': 0,
             'confirmed': 0, 'not_in_file': 0, 'ca_flags': []}

    idx_tz = raw.index.tz
    D = bhav_date.tz_localize(idx_tz) if idx_tz is not None else bhav_date
    if D not in raw.index:
        raw = raw.reindex(raw.index.union([D])).sort_index()

    close = raw['Close']
    before = close.loc[close.index < D]
    # Previous trading day = latest date before D where most of the universe has data
    coverage = before.notna().sum(axis=1)
    covered  = coverage[coverage >= len(close.columns) * 0.5]
    prev_day = covered.index[-1] if len(covered) else None

    patch = {f: {} for f in FIELDS}
    for t in close.columns:
        rec = bhav.get(t[:-3] if t.endswith('.NS') else t)
        if rec is None:
            stats['not_in_file'] += 1
            continue

        # ── Corporate-action safeguard ────────────────────────────────────
        hist = before[t].dropna()
        if len(hist) and rec['prev_close']:
            y_date, y_close = hist.index[-1], float(hist.iloc[-1])
            if y_close > 0:
                dev = abs(rec['prev_close'] / y_close - 1)
                tol = STRICT_TOL if (prev_day is not None and y_date == prev_day) else LOOSE_TOL
                if dev > tol:
                    stats['ca_flags'].append({
                        'ticker': t, 'nse_prev_close': rec['prev_close'],
                        'yahoo_close': round(y_close, 2), 'yahoo_date': str(y_date.date()),
                        'deviation_pct': round(dev * 100, 1)})
                    continue            # leave this stock exactly as Yahoo has it

        existing = raw.at[D, ('Close', t)]
        if pd.isna(existing):
            stats['filled'] += 1
        elif abs(existing / rec['Close'] - 1) > 0.001:
            stats['corrected'] += 1
        else:
            stats['confirmed'] += 1
        for f in FIELDS:
            if rec[f] is not None:
                patch[f][t] = rec[f]

    for f, vals in patch.items():
        if vals:
            cols = [(f, t) for t in vals]
            raw.loc[D, cols] = list(vals.values())

    return raw, stats


def patch_latest_with_bhavcopy(raw):
    """Entry point. Never raises — any failure returns raw unchanged."""
    try:
        bhav_date, bhav = fetch_latest_bhavcopy()
        if bhav_date is None:
            print('⚠ NSE bhavcopy unavailable — using Yahoo prices only')
            return raw, {'status': 'unavailable'}
        print(f'📄 NSE bhavcopy {bhav_date.date()}: {len(bhav)} stocks')
        raw, stats = apply_bhavcopy(raw, bhav_date, bhav)
        print(f"   filled {stats['filled']} blank, corrected {stats['corrected']}, "
              f"confirmed {stats['confirmed']}, not in file {stats['not_in_file']}, "
              f"corporate-action flags {len(stats['ca_flags'])}")
        for c in stats['ca_flags']:
            print(f"   ⚑ {c['ticker']}: NSE prev close {c['nse_prev_close']} vs "
                  f"Yahoo {c['yahoo_close']} ({c['yahoo_date']}) — {c['deviation_pct']}% apart")
        return raw, stats
    except Exception as e:
        print(f'⚠ Bhavcopy step failed ({type(e).__name__}: {e}) — using Yahoo prices only')
        return raw, {'status': 'unavailable', 'error': str(e)[:200]}
