"""
indicators.py
--------------
Vectorized technical indicator calculations used by the screener.

All functions operate on a pandas DataFrame of OHLCV data for a SINGLE ticker,
indexed by date, with columns: Open, High, Low, Close, Volume.

Where possible, calculations are vectorized (no per-row Python loops) for speed
across large universes.
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=window, min_periods=window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """
    Relative Strength Index using Wilder's smoothing method.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's smoothing = EMA with alpha = 1/window
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))

    # When avg_loss is 0 and avg_gain > 0 -> RSI = 100
    rsi_val = rsi_val.where(avg_loss != 0, 100)
    # When both avg_gain and avg_loss are 0 -> RSI = 50 (neutral, no movement)
    rsi_val = rsi_val.where(~((avg_gain == 0) & (avg_loss == 0)), 50)

    return rsi_val


def annualized_volatility(close: pd.Series, window: int = 252) -> float:
    """
    Annualized volatility of daily log returns over the trailing `window` days.
    Returns the latest value (scalar).
    """
    log_returns = np.log(close / close.shift(1))
    daily_vol = log_returns.rolling(window=window, min_periods=window).std()
    return daily_vol * np.sqrt(252)


def chaikin_money_flow(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    Chaikin Money Flow (CMF) over a rolling window.

    MFM = ((Close - Low) - (High - Close)) / (High - Low)
    MFV = MFM * Volume
    CMF = Sum(MFV, window) / Sum(Volume, window)

    Handles High == Low (zero range) by setting MFM to 0 for that bar,
    which is the standard convention to avoid division by zero.
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    volume = df["Volume"]

    range_hl = (high - low)
    # Avoid divide-by-zero: where range is 0, MFM contribution is 0
    mfm = np.where(
        range_hl > 0,
        ((close - low) - (high - close)) / range_hl.replace(0, np.nan),
        0.0,
    )
    mfm = pd.Series(mfm, index=df.index).fillna(0.0)

    mfv = mfm * volume

    sum_mfv = mfv.rolling(window=window, min_periods=window).sum()
    sum_vol = volume.rolling(window=window, min_periods=window).sum()

    cmf = sum_mfv / sum_vol.replace(0, np.nan)
    return cmf


def average_dollar_volume(close: pd.Series, volume: pd.Series, window: int = 60) -> pd.Series:
    """
    Average daily traded VALUE (price * volume) over `window` days.
    Note: For this strategy we use share-volume ADV per spec (>1,000,000 shares),
    but this helper is provided for completeness / future use.
    """
    dollar_vol = close * volume
    return dollar_vol.rolling(window=window, min_periods=window).mean()


def average_volume(volume: pd.Series, window: int = 60) -> pd.Series:
    """Average daily share volume over `window` days (used for the 3-month ADV filter)."""
    return volume.rolling(window=window, min_periods=window).mean()


def fifty_two_week_high(close: pd.Series, window: int = 252) -> pd.Series:
    """Rolling 52-week (252 trading day) high of the close price."""
    return close.rolling(window=window, min_periods=window).max()
