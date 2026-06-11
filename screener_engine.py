"""
screener_engine.py
-------------------
Core screening logic: applies the 7-stage NSE momentum funnel to a universe
of tickers and produces a ranked DataFrame of qualifying stocks.

This module is intentionally pure / side-effect-free (no I/O) so it can be
unit-tested independently of data fetching and GitHub integration.
"""

import logging
import numpy as np
import pandas as pd

import indicators as ind

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strategy parameters (per spec)
# ---------------------------------------------------------------------------
MIN_MARKET_CAP_INR = 1_000 * 1e7          # ₹1,000 Crores = ₹1,000 * 10,000,000
MIN_ADV_SHARES = 1_000_000                # 3-month average daily volume (shares)
ADV_WINDOW = 63                           # ~3 months of trading days
MAX_ANNUALIZED_VOL = 0.75                 # 75%
VOL_WINDOW = 252
RSI_WINDOW = 14
RSI_MIN = 50
SMA_TREND_WINDOW = 30                     # SMA30 trend support
FIFTY_TWO_WEEK_WINDOW = 252
PROXIMITY_TO_HIGH = 0.75                  # Close >= 0.75 * 52w high
CMF_WINDOW = 20

SMA_RANK_FAST = 21
SMA_RANK_SLOW = 200

PORTFOLIO_SIZE = 15
RETENTION_BUFFER_RANK = 25                # Top-25 retention buffer

SELL_FRICTION = 0.0023                    # 0.23%
BUY_FRICTION = 0.0013                     # 0.13%


def compute_metrics_for_ticker(ticker: str, df: pd.DataFrame, shares_outstanding: float = None) -> dict:
    """
    Compute all screening metrics for a single ticker's OHLCV history.

    Parameters
    ----------
    ticker : str
    df : pd.DataFrame
        OHLCV data indexed by date, sorted ascending, columns
        [Open, High, Low, Close, Volume].
    shares_outstanding : float, optional
        Latest known shares outstanding, used for market cap.

    Returns
    -------
    dict or None
        Dict of computed metrics, or None if there isn't enough history
        to compute the required indicators.
    """
    required_history = max(VOL_WINDOW, FIFTY_TWO_WEEK_WINDOW, SMA_RANK_SLOW) + 1
    if len(df) < required_history:
        return None

    close = df["Close"]
    volume = df["Volume"]

    last_close = float(close.iloc[-1])

    # --- Liquidity: 3-month ADV (shares) ---
    adv = ind.average_volume(volume, ADV_WINDOW).iloc[-1]

    # --- Volatility: annualized, trailing 252d ---
    ann_vol = ind.annualized_volatility(close, VOL_WINDOW).iloc[-1]

    # --- RSI 14 ---
    rsi_val = ind.rsi(close, RSI_WINDOW).iloc[-1]

    # --- SMA30 (trend support) ---
    sma30 = ind.sma(close, SMA_TREND_WINDOW).iloc[-1]

    # --- 52-week high & proximity ---
    high_52w = ind.fifty_two_week_high(close, FIFTY_TWO_WEEK_WINDOW).iloc[-1]

    # --- CMF 20 ---
    cmf_val = ind.chaikin_money_flow(df, CMF_WINDOW).iloc[-1]

    # --- Ranking SMAs ---
    sma21 = ind.sma(close, SMA_RANK_FAST).iloc[-1]
    sma200 = ind.sma(close, SMA_RANK_SLOW).iloc[-1]

    # --- Market cap ---
    market_cap = (last_close * shares_outstanding) if shares_outstanding else np.nan

    return {
        "Ticker": ticker,
        "Close": last_close,
        "ADV_3M": adv,
        "Annualized_Vol": ann_vol,
        "RSI_14": rsi_val,
        "SMA30": sma30,
        "High_52W": high_52w,
        "CMF_20": cmf_val,
        "SMA21": sma21,
        "SMA200": sma200,
        "Market_Cap": market_cap,
        "Shares_Outstanding": shares_outstanding,
    }


def build_metrics_table(price_data: dict, shares_map: dict) -> pd.DataFrame:
    """
    Compute metrics for every ticker in price_data.

    Parameters
    ----------
    price_data : dict[str, pd.DataFrame]
    shares_map : dict[str, float]

    Returns
    -------
    pd.DataFrame
        One row per ticker with all computed metrics. Tickers with
        insufficient history are dropped.
    """
    rows = []
    for ticker, df in price_data.items():
        df_sorted = df.sort_index()
        metrics = compute_metrics_for_ticker(
            ticker, df_sorted, shares_outstanding=shares_map.get(ticker)
        )
        if metrics is not None:
            rows.append(metrics)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def apply_screening_funnel(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply the 7-stage screening funnel to the metrics table.

    Adds boolean pass/fail columns for each stage plus an overall
    `Passes_Funnel` column, and returns the full table (not just passers)
    so callers can inspect why a stock failed if needed.

    Note: Market cap filter is only applied if Market_Cap is available
    (non-NaN). If shares outstanding could not be fetched for a ticker,
    it conservatively FAILS the market cap filter (cannot verify >= threshold).
    """
    df = metrics_df.copy()

    df["Pass_MarketCap"] = df["Market_Cap"] >= MIN_MARKET_CAP_INR
    df["Pass_Liquidity"] = df["ADV_3M"] >= MIN_ADV_SHARES
    df["Pass_Volatility"] = df["Annualized_Vol"] < MAX_ANNUALIZED_VOL
    df["Pass_RSI"] = df["RSI_14"] > RSI_MIN
    df["Pass_TrendSupport"] = df["Close"] > df["SMA30"]
    df["Pass_Proximity52W"] = df["Close"] >= (PROXIMITY_TO_HIGH * df["High_52W"])
    df["Pass_CMF"] = df["CMF_20"] > 0

    funnel_cols = [
        "Pass_MarketCap",
        "Pass_Liquidity",
        "Pass_Volatility",
        "Pass_RSI",
        "Pass_TrendSupport",
        "Pass_Proximity52W",
        "Pass_CMF",
    ]

    # Treat NaN comparisons as False (fillna handles cases where an
    # indicator could not be computed)
    for col in funnel_cols:
        df[col] = df[col].fillna(False)

    df["Passes_Funnel"] = df[funnel_cols].all(axis=1)

    return df


def rank_by_momentum(funnel_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the momentum score (SMA21 - SMA200) / SMA200 for stocks that pass
    the funnel, and rank them in descending order.

    Returns
    -------
    pd.DataFrame
        Subset of input where Passes_Funnel is True, with an added
        'Momentum_Score' column and a 'Rank' column (1 = highest score).
        Sorted by Rank ascending.
    """
    passing = funnel_df[funnel_df["Passes_Funnel"]].copy()

    passing["Momentum_Score"] = (passing["SMA21"] - passing["SMA200"]) / passing["SMA200"]

    passing = passing.sort_values("Momentum_Score", ascending=False).reset_index(drop=True)
    passing["Rank"] = passing.index + 1

    return passing


def run_full_screen(price_data: dict, shares_map: dict) -> dict:
    """
    Convenience wrapper: runs the full pipeline (metrics -> funnel -> ranking)
    and returns a dict with all intermediate and final results.

    Returns
    -------
    dict with keys:
        'metrics'   : full metrics table (all tickers with sufficient history)
        'funnel'    : metrics + pass/fail columns for all tickers
        'ranked'    : ranked table of stocks passing the funnel
        'universe_count' : total tickers with sufficient data evaluated
        'passing_count'  : number of tickers passing all 7 filters
    """
    metrics_df = build_metrics_table(price_data, shares_map)

    if metrics_df.empty:
        return {
            "metrics": metrics_df,
            "funnel": metrics_df,
            "ranked": metrics_df,
            "universe_count": 0,
            "passing_count": 0,
        }

    funnel_df = apply_screening_funnel(metrics_df)
    ranked_df = rank_by_momentum(funnel_df)

    return {
        "metrics": metrics_df,
        "funnel": funnel_df,
        "ranked": ranked_df,
        "universe_count": len(metrics_df),
        "passing_count": int(funnel_df["Passes_Funnel"].sum()),
    }
