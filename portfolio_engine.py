"""
portfolio_engine.py
--------------------
Implements the live operational rebalancing logic:

  1. Evaluate current holdings against today's screen (Retention Buffer Rule:
     HOLD if pass funnel AND rank <= 25, else SELL ALL).
  2. Select new entrants from today's Top 15 to fill vacated slots.
  3. Allocate freed + unallocated cash equally among new entrants.
  4. Any leftover cash (when fewer than 15 qualified names exist) is
     reported as Cash Hoarding.

This module is pure logic (no I/O) operating on:
  - holdings_df: DataFrame with columns [Ticker, Shares, Average_Buy_Price]
  - ranked_df:   output of screener_engine.rank_by_momentum (has Rank, Close, etc.)
  - funnel_df:   full funnel table (used to check if a held stock still passes
                 the funnel even if it falls outside the ranked Top-N, e.g.
                 if it passes but ranks > 25 only because of the cutoff)
  - total_portfolio_value: float (Cash + market value of holdings), as input
    by the user
  - unallocated_cash: float, cash NOT currently invested in holdings

SELL_FRICTION and BUY_FRICTION are imported from screener_engine to keep a
single source of truth for strategy constants.
"""

import math
import logging
import pandas as pd

from screener_engine import (
    PORTFOLIO_SIZE,
    RETENTION_BUFFER_RANK,
    SELL_FRICTION,
    BUY_FRICTION,
)

logger = logging.getLogger(__name__)


def _get_rank_and_pass(ticker: str, ranked_df: pd.DataFrame, funnel_df: pd.DataFrame):
    """
    Returns (passes_funnel: bool, rank: int or None, current_price: float or None)
    for a given ticker, looking it up in the ranked/funnel tables.

    - If the ticker passes the funnel, rank is its position in ranked_df
      (1 = best). If it doesn't pass, rank is None.
    - current_price comes from funnel_df['Close'] if the ticker was found
      in today's universe at all; otherwise None (ticker may have been
      delisted or had insufficient data today).
    """
    passes = False
    rank = None
    price = None

    if not funnel_df.empty:
        row = funnel_df[funnel_df["Ticker"] == ticker]
        if not row.empty:
            passes = bool(row.iloc[0]["Passes_Funnel"])
            price = float(row.iloc[0]["Close"])

    if passes and not ranked_df.empty:
        rank_row = ranked_df[ranked_df["Ticker"] == ticker]
        if not rank_row.empty:
            rank = int(rank_row.iloc[0]["Rank"])

    return passes, rank, price


def evaluate_holdings(holdings_df: pd.DataFrame, ranked_df: pd.DataFrame, funnel_df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies the Retention Buffer Rule to each current holding.

    Returns
    -------
    pd.DataFrame with columns:
        Ticker, Shares, Average_Buy_Price, Current_Price, Passes_Funnel,
        Rank, Decision ('HOLD' or 'SELL_ALL'), Sell_Proceeds (0 if HOLD)
    """
    columns = ["Ticker", "Shares", "Average_Buy_Price", "Current_Price",
               "Passes_Funnel", "Rank", "Decision", "Sell_Proceeds"]

    if holdings_df.empty:
        return pd.DataFrame(columns=columns)

    records = []

    for _, row in holdings_df.iterrows():
        ticker = row["Ticker"]
        shares = float(row["Shares"])
        avg_buy_price = float(row["Average_Buy_Price"])

        passes, rank, current_price = _get_rank_and_pass(ticker, ranked_df, funnel_df)

        if current_price is None:
            # No data today (e.g. delisted / fetch failure). Conservatively
            # SELL using the last known average buy price as a proxy so the
            # position doesn't silently disappear from the cash ledger.
            # This is flagged for manual review via a warning.
            logger.warning(
                "No current price data for held ticker %s. Treating as SELL_ALL "
                "using Average_Buy_Price as a fallback price for proceeds calc. "
                "MANUAL REVIEW RECOMMENDED.",
                ticker,
            )
            current_price = avg_buy_price
            decision = "SELL_ALL"
        elif passes and rank is not None and rank <= RETENTION_BUFFER_RANK:
            decision = "HOLD"
        else:
            decision = "SELL_ALL"

        if decision == "SELL_ALL":
            sell_proceeds = shares * current_price * (1 - SELL_FRICTION)
        else:
            sell_proceeds = 0.0

        records.append({
            "Ticker": ticker,
            "Shares": shares,
            "Average_Buy_Price": avg_buy_price,
            "Current_Price": current_price,
            "Passes_Funnel": passes,
            "Rank": rank,
            "Decision": decision,
            "Sell_Proceeds": sell_proceeds,
        })

    return pd.DataFrame(records)


def select_new_entrants(evaluated_holdings: pd.DataFrame, ranked_df: pd.DataFrame) -> pd.DataFrame:
    """
    Selects new entrants from today's Top-15 to fill vacant portfolio slots.

    K = number of HOLD positions.
    Vacant slots = PORTFOLIO_SIZE - K.
    New entrants = highest-ranked Top-15 names not already held (HOLD or SELL),
    taking the top `vacant slots` of them.

    Note: a ticker currently held and SOLD that ALSO appears in today's Top 15
    is still eligible to be a "new entrant" re-buy target (the strategy doesn't
    forbid re-entry; the retention buffer only governs whether the EXISTING
    lot is preserved untouched). This mirrors a realistic momentum rebalance
    where a stock could be sold and immediately re-bought as part of the new
    target list -- though in practice rank <= 25 retention would normally
    prevent this for any stock still in the Top 15.

    Returns
    -------
    pd.DataFrame subset of ranked_df (Top 15) for the selected new entrants,
    with columns including Ticker, Close, Rank, Momentum_Score, etc.
    """
    held_tickers = set(evaluated_holdings.loc[evaluated_holdings["Decision"] == "HOLD", "Ticker"])

    k = len(held_tickers)
    vacant_slots = max(PORTFOLIO_SIZE - k, 0)

    if vacant_slots == 0 or ranked_df.empty:
        return ranked_df.iloc[0:0].copy()

    top15 = ranked_df[ranked_df["Rank"] <= PORTFOLIO_SIZE]
    candidates = top15[~top15["Ticker"].isin(held_tickers)].sort_values("Rank")

    return candidates.head(vacant_slots).copy()


def generate_buy_orders(new_entrants: pd.DataFrame, available_cash: float) -> pd.DataFrame:
    """
    Allocates `available_cash` equally among new_entrants and computes
    integer share quantities to buy after accounting for buy friction.

    Shares = floor((Allocated_Cash * (1 - BUY_FRICTION)) / Current_Price)

    Returns
    -------
    pd.DataFrame with columns:
        Ticker, Rank, Momentum_Score, Current_Price, Allocated_Cash,
        Target_Shares, Estimated_Cost, Leftover_Cash
    """
    if new_entrants.empty:
        return pd.DataFrame(columns=[
            "Ticker", "Rank", "Momentum_Score", "Current_Price",
            "Allocated_Cash", "Target_Shares", "Estimated_Cost", "Leftover_Cash"
        ])

    n = len(new_entrants)
    cash_per_stock = available_cash / n if n > 0 else 0.0

    records = []
    for _, row in new_entrants.iterrows():
        price = float(row["Close"])
        target_shares = math.floor((cash_per_stock * (1 - BUY_FRICTION)) / price) if price > 0 else 0
        estimated_cost = target_shares * price * (1 + BUY_FRICTION)
        leftover = cash_per_stock - estimated_cost

        records.append({
            "Ticker": row["Ticker"],
            "Rank": int(row["Rank"]),
            "Momentum_Score": float(row["Momentum_Score"]),
            "Current_Price": price,
            "Allocated_Cash": cash_per_stock,
            "Target_Shares": target_shares,
            "Estimated_Cost": estimated_cost,
            "Leftover_Cash": leftover,
        })

    return pd.DataFrame(records)


def run_rebalance(
    holdings_df: pd.DataFrame,
    ranked_df: pd.DataFrame,
    funnel_df: pd.DataFrame,
    total_portfolio_value: float,
    unallocated_cash: float,
) -> dict:
    """
    Orchestrates the full rebalance decision process.

    Returns
    -------
    dict with keys:
        'evaluated_holdings' : DataFrame from evaluate_holdings()
        'new_entrants'       : DataFrame from select_new_entrants()
        'buy_orders'         : DataFrame from generate_buy_orders()
        'sell_orders'        : DataFrame (subset of evaluated_holdings where Decision == SELL_ALL)
        'hold_orders'        : DataFrame (subset of evaluated_holdings where Decision == HOLD)
        'cash_freed'         : float, total proceeds from sells
        'cash_available'     : float, cash_freed + unallocated_cash (before buys)
        'cash_deployed'      : float, total estimated cost of buy orders
        'cash_hoarding'      : float, leftover cash after buys (if < 15 qualified names)
        'updated_holdings'   : DataFrame, the new holdings.csv content
                                (HOLD rows + new BUY rows with computed Average_Buy_Price)
    """
    evaluated = evaluate_holdings(holdings_df, ranked_df, funnel_df)

    hold_orders = evaluated[evaluated["Decision"] == "HOLD"].copy()
    sell_orders = evaluated[evaluated["Decision"] == "SELL_ALL"].copy()

    cash_freed = float(sell_orders["Sell_Proceeds"].sum())
    cash_available = cash_freed + unallocated_cash

    new_entrants = select_new_entrants(evaluated, ranked_df)
    buy_orders = generate_buy_orders(new_entrants, cash_available)

    cash_deployed = float(buy_orders["Estimated_Cost"].sum()) if not buy_orders.empty else 0.0
    cash_hoarding = cash_available - cash_deployed

    # Build the updated holdings table
    updated_rows = []
    for _, row in hold_orders.iterrows():
        updated_rows.append({
            "Ticker": row["Ticker"],
            "Shares": row["Shares"],
            "Average_Buy_Price": row["Average_Buy_Price"],
        })
    for _, row in buy_orders.iterrows():
        if row["Target_Shares"] > 0:
            # Effective average buy price includes friction
            effective_price = row["Current_Price"] * (1 + BUY_FRICTION)
            updated_rows.append({
                "Ticker": row["Ticker"],
                "Shares": row["Target_Shares"],
                "Average_Buy_Price": round(effective_price, 4),
            })

    updated_holdings = pd.DataFrame(
        updated_rows, columns=["Ticker", "Shares", "Average_Buy_Price"]
    )

    return {
        "evaluated_holdings": evaluated,
        "new_entrants": new_entrants,
        "buy_orders": buy_orders,
        "sell_orders": sell_orders,
        "hold_orders": hold_orders,
        "cash_freed": cash_freed,
        "cash_available": cash_available,
        "cash_deployed": cash_deployed,
        "cash_hoarding": cash_hoarding,
        "updated_holdings": updated_holdings,
        "total_portfolio_value": total_portfolio_value,
        "unallocated_cash_input": unallocated_cash,
    }
