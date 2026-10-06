"""
core/nse_index.py — NSE's official end-of-day INDEX closes (Nifty 50, Nifty 500).

Why: Nifty 500 is no longer reliably available on Yahoo. The earlier workaround
(NETF.NS, an ETF) trades thinly and can go days without a fresh candle, which
left the Nifty 500 benchmark line flat/stale. NSE publishes the real index
close for every index in one daily file on the same archive host the
bhavcopy already comes from.

Fail-safe design (same spirit as nse_bhavcopy):
  * Returns None if the file can't be fetched/parsed — callers fall back.
  * Only returns values whose 'Index Date' matches the requested date.
"""

import csv
import io
import urllib.request
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124 Safari/537.36',
    'Accept': '*/*',
    'Referer': 'https://www.nseindia.com/',
}
URLS = [
    'https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv',
    'https://archives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv',
]
WANTED = {'nifty 50': 'nifty50', 'nifty 500': 'nifty500'}
DATE_FORMATS = ('%d-%m-%Y', '%Y-%m-%d', '%d-%b-%Y', '%d/%m/%Y')


def _num(v):
    try:
        return float(str(v).strip().replace(',', ''))
    except (TypeError, ValueError):
        return None


def _parse_date(s):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def parse_index_close_csv(text):
    """Parse ind_close_all CSV text → {'nifty50': x, 'nifty500': y, 'date': date|None}.
    Column matching is tolerant of small header changes."""
    out = {}
    idx_date = None
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    for raw in reader:
        r = {(k or '').strip().lower(): (v or '').strip() for k, v in raw.items()}
        name = next((v for k, v in r.items() if k.startswith('index name')), '')
        key = WANTED.get(name.strip().lower())
        if not key:
            continue
        close = None
        for k, v in r.items():
            if k.startswith('closing index value') or k in ('close', 'closing'):
                close = _num(v)
                break
        if close is None or close <= 1000:     # sanity: both indices are in the thousands
            continue
        out[key] = close
        if idx_date is None:
            d = next((v for k, v in r.items() if k.startswith('index date')), '')
            idx_date = _parse_date(d)
    out['date'] = idx_date
    return out


def fetch_index_close(d, timeout=30):
    """Fetch official index closes for date `d` (datetime.date).
    Returns {'nifty50': x, 'nifty500': y} (either may be missing) or None."""
    ddmmyyyy = d.strftime('%d%m%Y')
    for tmpl in URLS:
        url = tmpl.format(ddmmyyyy=ddmmyyyy)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode('utf-8', errors='replace')
        except Exception:
            continue
        parsed = parse_index_close_csv(text)
        if parsed.get('date') is not None and parsed['date'] != d:
            continue
        res = {k: v for k, v in parsed.items() if k in ('nifty50', 'nifty500')}
        if res:
            return res
    return None
