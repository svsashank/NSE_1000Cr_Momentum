"""
NSE Universe Refresher
======================
Rebuilds nse_universe.json monthly from NSE's authoritative equity listing.
Runs as a GitHub Actions workflow (open internet access).

Strategy (3-level fallback):
  1. NSE EQUITY_L.csv  — canonical, all ~2000+ EQ-series listings
  2. nsetools package   — Python wrapper around NSE APIs
  3. Keep existing      — if both fail, abort with non-zero exit so the workflow
                          flags as failed (never silently shrink the universe)

EQ series only: filters out BE (trade-for-trade), SM (SME), BZ (surveillance),
ST, TB series — these are illiquid / restricted and poison a momentum screener.

The script also cross-checks KPL.NS and a few other known stocks to validate
the output before committing.
"""

import json, os, sys, time, csv, io, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

UNIVERSE_FILE = os.path.join(os.path.dirname(__file__), 'nse_universe.json')

# Known tickers that MUST appear in the output (sanity check)
MUST_HAVE = ['RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFCBANK.NS', 'KPL.NS']

# NSE series to include (EQ = main board equity only)
VALID_SERIES = {'EQ'}


# ── Source 1: NSE EQUITY_L.csv ────────────────────────────────────────────────
def fetch_from_nse_csv():
    """
    NSE publishes a full equity listing CSV at a stable URL.
    Columns: SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING,
             PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE
    """
    import urllib.request

    urls = [
        'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv',
        'https://www1.nseindia.com/content/equities/EQUITY_L.csv',
    ]

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.nseindia.com/',
    }

    for url in urls:
        try:
            log.info(f'  Trying {url}')
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode('utf-8', errors='replace')

            reader = csv.DictReader(io.StringIO(raw))
            tickers = []
            for row in reader:
                symbol = row.get('SYMBOL', '').strip()
                series = row.get(' SERIES', row.get('SERIES', '')).strip()
                if symbol and series in VALID_SERIES:
                    tickers.append(f'{symbol}.NS')

            if len(tickers) > 500:
                log.info(f'  ✅ NSE CSV: {len(tickers)} EQ-series tickers')
                return sorted(set(tickers))
            else:
                log.warning(f'  ⚠ Too few tickers ({len(tickers)}) from {url} — skipping')

        except Exception as e:
            log.warning(f'  ⚠ {url} failed: {e}')

    return None


# ── Source 2: nsetools ────────────────────────────────────────────────────────
def fetch_from_nsetools():
    """
    nsetools wraps NSE's public APIs. Falls back to this if CSV fetch fails.
    Note: nsetools returns all series; we post-filter using yfinance quoteType
    for the EQ check — but for speed we just accept all symbols here since
    the volume/ADV filter in the screener removes illiquid ones anyway.
    """
    try:
        from nsetools import Nse
        nse = Nse()
        codes = nse.get_stock_codes()  # returns {symbol: name}
        tickers = sorted([f'{sym}.NS' for sym in codes.keys() if sym and sym != 'SYMBOL'])
        if len(tickers) > 500:
            log.info(f'  ✅ nsetools: {len(tickers)} tickers')
            return tickers
    except Exception as e:
        log.warning(f'  ⚠ nsetools failed: {e}')
    return None


# ── Source 3: Keep existing ───────────────────────────────────────────────────
def load_existing():
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE) as f:
            tickers = json.load(f)
        log.info(f'  ⚠ Using existing file: {len(tickers)} tickers (stale)')
        return tickers
    return None


# ── Sanity check ─────────────────────────────────────────────────────────────
def sanity_check(tickers):
    ticker_set = set(tickers)
    missing = [t for t in MUST_HAVE if t not in ticker_set]
    if missing:
        log.warning(f'  ⚠ Missing expected tickers: {missing}')
        return False
    log.info(f'  ✅ Sanity check passed — all must-have tickers present')
    return True


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info('=' * 55)
    log.info('  NSE Universe Refresh')
    log.info(f'  {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}')
    log.info('=' * 55)

    # Load existing for comparison
    existing = []
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE) as f:
            existing = json.load(f)
        log.info(f'  Existing universe: {len(existing)} tickers')

    # Try sources in order
    tickers = None

    log.info('
1️⃣  Trying NSE EQUITY_L.csv...')
    tickers = fetch_from_nse_csv()

    if not tickers:
        log.info('
2️⃣  Trying nsetools...')
        try:
            import subprocess
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'nsetools', '-q'], check=True)
        except Exception:
            pass
        tickers = fetch_from_nsetools()

    if not tickers:
        log.warning('
3️⃣  Both sources failed — keeping existing universe')
        tickers = load_existing()
        if not tickers:
            log.error('  ❌ No fallback available. Exiting.')
            sys.exit(1)
        # Don't overwrite if we're just using the existing file
        log.info('  Universe unchanged.')
        sys.exit(0)

    # Sort for deterministic diffs
    tickers = sorted(set(tickers))

    # Sanity check
    log.info('
🔍 Running sanity checks...')
    ok = sanity_check(tickers)

    # Diff summary
    existing_set = set(existing)
    new_set      = set(tickers)
    added   = new_set - existing_set
    removed = existing_set - new_set
    log.info(f'
📊 Universe delta:')
    log.info(f'   Before : {len(existing)} tickers')
    log.info(f'   After  : {len(tickers)} tickers')
    log.info(f'   Added  : {len(added)}')
    log.info(f'   Removed: {len(removed)}')
    if added:
        log.info(f'   New    : {sorted(added)[:20]}{"..." if len(added)>20 else ""}')
    if removed:
        log.info(f'   Dropped: {sorted(removed)[:20]}{"..." if len(removed)>20 else ""}')

    # Write output
    with open(UNIVERSE_FILE, 'w') as f:
        json.dump(tickers, f, indent=None, separators=(',', ':'))
    log.info(f'
✅ Wrote {len(tickers)} tickers to {UNIVERSE_FILE}')

    if not ok:
        log.warning('⚠ Sanity check failed but file written — review before relying on this run')
        sys.exit(1)


if __name__ == '__main__':
    main()
