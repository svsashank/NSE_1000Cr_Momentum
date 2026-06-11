"""
data_fetcher.py
----------------
Handles bulk fetching of OHLCV data and shares-outstanding / market-cap data
for the NSE universe via yfinance, with retry logic for resilience against
transient network/API failures (common with yfinance at scale).
"""

import time
import logging
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# How many trading days of history we need:
#   - 252 for annualized volatility & 52-week high
#   - +30 buffer for SMA200 warm-up and weekends/holidays
LOOKBACK_DAYS = 320

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
BATCH_SIZE = 50  # yfinance download batch size to avoid rate-limit issues


def fetch_price_history(tickers: list, period_days: int = LOOKBACK_DAYS) -> dict:
    """
    Fetch daily OHLCV history for a list of tickers using yfinance, in batches,
    with retry logic.

    Returns
    -------
    dict[str, pd.DataFrame]
        Mapping of ticker -> DataFrame with columns [Open, High, Low, Close, Volume],
        indexed by date. Tickers with no usable data are omitted.
    """
    period_str = f"{period_days}d"
    all_data = {}

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        logger.info(
            "Fetching batch %d/%d (%d tickers)...",
            (i // BATCH_SIZE) + 1,
            (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE,
            len(batch),
        )

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                data = yf.download(
                    tickers=batch,
                    period=period_str,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
                break
            except Exception as e:
                logger.warning(
                    "Batch download attempt %d/%d failed: %s", attempt, MAX_RETRIES, e
                )
                if attempt == MAX_RETRIES:
                    logger.error("Giving up on batch starting at index %d", i)
                    data = None
                else:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        if data is None or data.empty:
            continue

        for ticker in batch:
            try:
                if len(batch) == 1:
                    df = data.copy()
                else:
                    if ticker not in data.columns.get_level_values(0):
                        continue
                    df = data[ticker].copy()

                df = df.dropna(how="all")
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(
                    subset=["Close"]
                )

                if df.empty or len(df) < 60:
                    # Not enough history to be useful (e.g. recently listed)
                    continue

                all_data[ticker] = df
            except Exception as e:
                logger.debug("Skipping %s due to parse error: %s", ticker, e)
                continue

    logger.info("Successfully fetched data for %d/%d tickers", len(all_data), len(tickers))
    return all_data


def fetch_shares_outstanding(tickers: list) -> dict:
    """
    Fetch shares outstanding for each ticker via yfinance .info / .fast_info,
    with retry logic. Used to compute market capitalization.

    Returns
    -------
    dict[str, float]
        Mapping of ticker -> shares outstanding. Tickers where this could not
        be determined are omitted (market cap filter will then exclude them
        conservatively).
    """
    shares_map = {}

    for ticker in tickers:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                tk = yf.Ticker(ticker)
                shares = None

                # fast_info is cheaper/more reliable when available
                try:
                    fi = tk.fast_info
                    shares = fi.get("shares_outstanding") or fi.get("shares")
                except Exception:
                    shares = None

                if not shares:
                    info = tk.info
                    shares = info.get("sharesOutstanding")

                if shares:
                    shares_map[ticker] = float(shares)
                break
            except Exception as e:
                logger.debug(
                    "Shares-outstanding fetch attempt %d/%d failed for %s: %s",
                    attempt, MAX_RETRIES, ticker, e,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(1)

    logger.info(
        "Fetched shares-outstanding for %d/%d tickers", len(shares_map), len(tickers)
    )
    return shares_map


def fetch_current_prices(tickers: list) -> dict:
    """
    Fetch the latest available close price for a list of tickers.
    Used for execution-price estimates in the rebalancing logic.

    Returns
    -------
    dict[str, float]
    """
    prices = {}
    history = fetch_price_history(tickers, period_days=10)
    for ticker, df in history.items():
        if not df.empty:
            prices[ticker] = float(df["Close"].iloc[-1])
    return prices
