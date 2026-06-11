"""
nse_universe.py
----------------
Provides the list of NSE-listed equity tickers (yfinance format, e.g. 'RELIANCE.NS')
to be used as the screening universe.

Approach:
  - Primary: Pull the official NSE equity list CSV (EQUITY_L.csv) which contains
    all listed equity symbols on the NSE. This is fetched from the NSE archives.
  - Fallback: If the live fetch fails (NSE often blocks non-browser requests),
    fall back to a bundled static list (universe_fallback.csv) that the user
    can periodically refresh.

NOTE: NSE's website aggressively blocks scripted requests without proper headers.
We set browser-like headers and a session warm-up (hitting the homepage first to
get cookies) to improve reliability. If this still fails in GitHub Actions,
the fallback CSV is used so the pipeline never hard-fails.
"""

import io
import os
import logging
import pandas as pd
import requests

logger = logging.getLogger(__name__)

NSE_EQUITY_LIST_URL = "https://archives.nseindia.com/content/equity/EQUITY_L.csv"
FALLBACK_CSV_PATH = os.path.join(os.path.dirname(__file__), "universe_fallback.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def _fetch_live_universe() -> pd.DataFrame:
    """Attempt to fetch the live NSE equity list."""
    session = requests.Session()
    session.headers.update(HEADERS)

    # Warm up session (NSE requires cookies set from homepage visit)
    session.get("https://www.nseindia.com", timeout=10)
    resp = session.get(NSE_EQUITY_LIST_URL, timeout=15)
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = [c.strip() for c in df.columns]
    return df


def get_nse_universe(max_tickers: int = None) -> list:
    """
    Returns a list of yfinance-compatible tickers for the NSE equity universe.

    Parameters
    ----------
    max_tickers : int, optional
        If provided, truncate the universe to this many tickers (useful for
        testing / staying within rate limits during development).

    Returns
    -------
    list[str]
    """
    df = None
    try:
        df = _fetch_live_universe()
        logger.info("Fetched live NSE universe: %d symbols", len(df))
    except Exception as e:
        logger.warning("Live NSE universe fetch failed (%s). Using fallback list.", e)
        if os.path.exists(FALLBACK_CSV_PATH):
            df = pd.read_csv(FALLBACK_CSV_PATH)
            df.columns = [c.strip() for c in df.columns]
            logger.info("Loaded fallback universe: %d symbols", len(df))
        else:
            raise RuntimeError(
                "Could not fetch live NSE universe and no fallback CSV present "
                f"at {FALLBACK_CSV_PATH}."
            )

    # Filter to "EQ" series only (regular equity, excludes ETFs/preference shares etc.)
    if "SERIES" in df.columns:
        df = df[df["SERIES"].str.strip() == "EQ"]

    symbol_col = "SYMBOL" if "SYMBOL" in df.columns else df.columns[0]
    symbols = df[symbol_col].astype(str).str.strip().tolist()

    # Convert to yfinance format
    tickers = [f"{s}.NS" for s in symbols if s and s.upper() != "SYMBOL"]

    # De-duplicate while preserving order
    seen = set()
    unique_tickers = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique_tickers.append(t)

    if max_tickers:
        unique_tickers = unique_tickers[:max_tickers]

    return unique_tickers


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    u = get_nse_universe()
    print(f"Universe size: {len(u)}")
    print(u[:20])
