#!/usr/bin/env python3
"""
run_screener.py
----------------
Main entrypoint for the live NSE Momentum Screening & Rebalancing Engine.

This script is designed to be triggered via GitHub Actions `workflow_dispatch`.
It performs the following steps:

  1. Read current portfolio state from `holdings.csv` (repo root).
  2. Read `Total_Portfolio_Value` and `Unallocated_Cash` from workflow inputs
     (passed as environment variables) or fall back to a `portfolio_config.json`
     file in the repo root.
  3. Fetch the NSE universe + OHLCV data via yfinance (with retries).
  4. Run the 7-stage screening funnel and momentum ranking.
  5. Apply the live rebalancing decision tree (Retention Buffer Rule).
  6. Write the updated `holdings.csv` back to disk (committed by the workflow).
  7. Generate `dashboard/index.html` for GitHub Pages.

Environment Variables (set by the GitHub Actions workflow / workflow_dispatch inputs):
  TOTAL_PORTFOLIO_VALUE : float, total portfolio value (cash + holdings market value)
  UNALLOCATED_CASH      : float, cash not currently invested in holdings
  MAX_UNIVERSE_TICKERS  : int, optional cap on universe size (for testing/dev)

Outputs:
  holdings.csv          : updated in repo root (committed by workflow)
  dashboard/index.html  : generated dashboard for GitHub Pages
  screen_results.csv    : full ranked results for today (for audit/debugging)
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone

import pandas as pd

from nse_universe import get_nse_universe
from data_fetcher import fetch_price_history, fetch_shares_outstanding
import screener_engine as se
import portfolio_engine as pe
from dashboard import generate_dashboard_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_screener")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
HOLDINGS_PATH = os.path.join(REPO_ROOT, "holdings.csv")
CONFIG_PATH = os.path.join(REPO_ROOT, "portfolio_config.json")
DASHBOARD_DIR = os.path.join(REPO_ROOT, "dashboard")
DASHBOARD_PATH = os.path.join(DASHBOARD_DIR, "index.html")
SCREEN_RESULTS_PATH = os.path.join(REPO_ROOT, "screen_results.csv")


def load_holdings() -> pd.DataFrame:
    """
    Load holdings.csv. If it doesn't exist, create an empty one with the
    correct schema (first run scenario).
    """
    if os.path.exists(HOLDINGS_PATH):
        df = pd.read_csv(HOLDINGS_PATH)
        required_cols = {"Ticker", "Shares", "Average_Buy_Price"}
        if not required_cols.issubset(df.columns):
            raise ValueError(
                f"holdings.csv is missing required columns. "
                f"Expected {required_cols}, found {set(df.columns)}"
            )
        return df
    else:
        logger.warning("holdings.csv not found. Assuming empty portfolio (first run).")
        return pd.DataFrame(columns=["Ticker", "Shares", "Average_Buy_Price"])


def load_portfolio_inputs() -> tuple:
    """
    Determine Total_Portfolio_Value and Unallocated_Cash from:
      1. Environment variables (set via workflow_dispatch inputs) - highest priority
      2. portfolio_config.json in repo root - fallback / persisted default

    Returns
    -------
    (total_portfolio_value: float, unallocated_cash: float)
    """
    env_total = os.environ.get("TOTAL_PORTFOLIO_VALUE")
    env_cash = os.environ.get("UNALLOCATED_CASH")

    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)

    total_portfolio_value = float(env_total) if env_total else float(config.get("Total_Portfolio_Value", 0.0))
    unallocated_cash = float(env_cash) if env_cash else float(config.get("Unallocated_Cash", 0.0))

    if total_portfolio_value <= 0:
        raise ValueError(
            "Total_Portfolio_Value must be > 0. Provide it via the workflow_dispatch "
            "input 'total_portfolio_value' or in portfolio_config.json."
        )

    return total_portfolio_value, unallocated_cash


def save_holdings(df: pd.DataFrame):
    df.to_csv(HOLDINGS_PATH, index=False)
    logger.info("Wrote updated holdings.csv with %d positions", len(df))


def save_dashboard(html_content: str):
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info("Wrote dashboard to %s", DASHBOARD_PATH)


def save_screen_results(ranked_df: pd.DataFrame):
    ranked_df.to_csv(SCREEN_RESULTS_PATH, index=False)
    logger.info("Wrote full screen results to %s", SCREEN_RESULTS_PATH)


def main():
    logger.info("=== NSE Momentum Screener: Live Run Starting ===")
    run_ts = datetime.now(timezone.utc)

    # 1. Load state
    holdings_df = load_holdings()
    total_portfolio_value, unallocated_cash = load_portfolio_inputs()
    logger.info(
        "Loaded portfolio state: %d holdings, Total_Portfolio_Value=%.2f, Unallocated_Cash=%.2f",
        len(holdings_df), total_portfolio_value, unallocated_cash,
    )

    # 2. Build universe
    max_universe = os.environ.get("MAX_UNIVERSE_TICKERS")
    max_universe = int(max_universe) if max_universe else None
    universe = get_nse_universe(max_tickers=max_universe)

    # Ensure all currently-held tickers are included in the universe even if
    # they've fallen out of the standard list (so we can evaluate SELL decisions)
    held_tickers = set(holdings_df["Ticker"].tolist()) if not holdings_df.empty else set()
    for t in held_tickers:
        if t not in universe:
            universe.append(t)

    logger.info("Universe size (incl. current holdings): %d tickers", len(universe))

    # 3. Fetch data
    logger.info("Fetching price history (this may take a while for large universes)...")
    price_data = fetch_price_history(universe)

    logger.info("Fetching shares outstanding for market-cap calculation...")
    shares_map = fetch_shares_outstanding(list(price_data.keys()))

    # 4. Run screening funnel + ranking
    logger.info("Running 7-stage screening funnel and momentum ranking...")
    screen_result = se.run_full_screen(price_data, shares_map)
    funnel_df = screen_result["funnel"]
    ranked_df = screen_result["ranked"]
    universe_count = screen_result["universe_count"]
    passing_count = screen_result["passing_count"]

    logger.info(
        "Screening complete: %d/%d stocks passed all 7 filters.",
        passing_count, universe_count,
    )

    if ranked_df.empty:
        logger.warning("No stocks passed the screening funnel today!")

    # 5. Run rebalancing logic
    logger.info("Running rebalancing decision tree...")
    rebalance_result = pe.run_rebalance(
        holdings_df=holdings_df,
        ranked_df=ranked_df,
        funnel_df=funnel_df,
        total_portfolio_value=total_portfolio_value,
        unallocated_cash=unallocated_cash,
    )

    n_buys = len(rebalance_result["buy_orders"])
    n_sells = len(rebalance_result["sell_orders"])
    n_holds = len(rebalance_result["hold_orders"])
    logger.info("Rebalance decisions: %d BUY, %d SELL, %d HOLD", n_buys, n_sells, n_holds)
    logger.info(
        "Cash: freed=%.2f, available=%.2f, deployed=%.2f, hoarding=%.2f",
        rebalance_result["cash_freed"],
        rebalance_result["cash_available"],
        rebalance_result["cash_deployed"],
        rebalance_result["cash_hoarding"],
    )

    # 6. Persist updated holdings
    save_holdings(rebalance_result["updated_holdings"])

    # 7. Persist full screen results for audit
    if not ranked_df.empty:
        save_screen_results(ranked_df)

    # 8. Generate dashboard
    dashboard_html = generate_dashboard_html(
        rebalance_result=rebalance_result,
        universe_count=universe_count,
        passing_count=passing_count,
        run_timestamp=run_ts,
    )
    save_dashboard(dashboard_html)

    # 9. Emit a GitHub Actions step summary (nice-to-have)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"## NSE Momentum Screener Run — {run_ts.isoformat()}\n\n")
            f.write(f"- Universe evaluated: **{universe_count}**\n")
            f.write(f"- Passed 7-stage funnel: **{passing_count}**\n")
            f.write(f"- BUY signals: **{n_buys}**\n")
            f.write(f"- SELL signals: **{n_sells}**\n")
            f.write(f"- HOLD signals: **{n_holds}**\n")
            f.write(f"- Cash hoarding: **₹{rebalance_result['cash_hoarding']:,.2f}**\n")

    logger.info("=== Run Complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Fatal error during screener run")
        sys.exit(1)
