"""
dashboard.py
------------
Generates the static `index.html` dashboard for GitHub Pages, summarizing:
  - Action checklist (BUY / SELL / HOLD tables)
  - Current portfolio snapshot (post-rebalance holdings, weights, valuation)
  - Market health indicator (passing_count / universe_count)

The output is a single self-contained HTML file (inline CSS, no external
dependencies) so it renders correctly on GitHub Pages with zero build step.
"""

import html
from datetime import datetime, timezone
import pandas as pd


def _fmt_money(x: float) -> str:
    try:
        return f"₹{x:,.2f}"
    except Exception:
        return str(x)


def _fmt_pct(x: float) -> str:
    try:
        return f"{x * 100:.2f}%"
    except Exception:
        return str(x)


def _df_to_table(df: pd.DataFrame, columns: dict) -> str:
    """
    Render a DataFrame as an HTML table.

    Parameters
    ----------
    df : pd.DataFrame
    columns : dict
        Mapping of {df_column_name: (display_label, formatter_fn or None)}
        Order of dict keys determines column order.
    """
    if df is None or df.empty:
        return "<p class='empty-msg'>None</p>"

    headers = "".join(f"<th>{html.escape(label)}</th>" for label, _ in columns.values())

    rows_html = []
    for _, row in df.iterrows():
        cells = []
        for col, (_, fmt) in columns.items():
            val = row.get(col, "")
            if fmt:
                val = fmt(val)
            cells.append(f"<td>{html.escape(str(val))}</td>")
        rows_html.append(f"<tr>{''.join(cells)}</tr>")

    return f"""
    <table>
      <thead><tr>{headers}</tr></thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
    """


def generate_dashboard_html(
    rebalance_result: dict,
    universe_count: int,
    passing_count: int,
    run_timestamp: datetime = None,
) -> str:
    """
    Build the full dashboard HTML string.

    Parameters
    ----------
    rebalance_result : dict
        Output of portfolio_engine.run_rebalance()
    universe_count : int
        Total number of stocks evaluated today (with sufficient data).
    passing_count : int
        Number of stocks that passed all 7 filters today.
    run_timestamp : datetime, optional
        Defaults to current UTC time.

    Returns
    -------
    str : full HTML document
    """
    if run_timestamp is None:
        run_timestamp = datetime.now(timezone.utc)

    buy_orders = rebalance_result["buy_orders"]
    sell_orders = rebalance_result["sell_orders"]
    hold_orders = rebalance_result["hold_orders"]
    updated_holdings = rebalance_result["updated_holdings"]
    cash_hoarding = rebalance_result["cash_hoarding"]
    cash_freed = rebalance_result["cash_freed"]
    cash_available = rebalance_result["cash_available"]
    cash_deployed = rebalance_result["cash_deployed"]
    total_portfolio_value = rebalance_result["total_portfolio_value"]

    # --- Market health ---
    pct_passing = (passing_count / universe_count * 100) if universe_count else 0
    if pct_passing >= 15:
        regime = "RISK-ON"
        regime_class = "risk-on"
    elif pct_passing >= 5:
        regime = "NEUTRAL"
        regime_class = "neutral"
    else:
        regime = "RISK-OFF"
        regime_class = "risk-off"

    # --- BUY table ---
    buy_table = _df_to_table(
        buy_orders,
        {
            "Ticker": ("Ticker", None),
            "Target_Shares": ("Target Shares", lambda x: f"{int(x):,}"),
            "Current_Price": ("Est. Price", _fmt_money),
            "Allocated_Cash": ("Allocated Cash", _fmt_money),
            "Estimated_Cost": ("Est. Cost", _fmt_money),
            "Rank": ("New Rank", lambda x: f"#{int(x)}"),
        },
    )

    # --- SELL table ---
    sell_table = _df_to_table(
        sell_orders,
        {
            "Ticker": ("Ticker", None),
            "Shares": ("Shares to Liquidate", lambda x: f"{x:,.2f}"),
            "Current_Price": ("Current Price", _fmt_money),
            "Sell_Proceeds": ("Est. Proceeds", _fmt_money),
            "Passes_Funnel": ("Still Passes Funnel?", lambda x: "Yes" if x else "No"),
            "Rank": ("Today's Rank", lambda x: f"#{int(x)}" if pd.notna(x) else "N/A (failed funnel)"),
        },
    )

    # --- HOLD table ---
    hold_table = _df_to_table(
        hold_orders,
        {
            "Ticker": ("Ticker", None),
            "Shares": ("Shares", lambda x: f"{x:,.2f}"),
            "Average_Buy_Price": ("Avg. Buy Price", _fmt_money),
            "Current_Price": ("Current Price", _fmt_money),
            "Rank": ("Today's Rank", lambda x: f"#{int(x)}" if pd.notna(x) else "N/A"),
        },
    )

    # --- Current Portfolio Snapshot (post-rebalance) ---
    snapshot_df = updated_holdings.copy()
    if not snapshot_df.empty:
        # We need current price for valuation; pull from hold_orders/buy_orders
        price_map = {}
        for _, r in hold_orders.iterrows():
            price_map[r["Ticker"]] = r["Current_Price"]
        for _, r in buy_orders.iterrows():
            price_map[r["Ticker"]] = r["Current_Price"]

        snapshot_df["Current_Price"] = snapshot_df["Ticker"].map(price_map)
        snapshot_df["Market_Value"] = snapshot_df["Shares"] * snapshot_df["Current_Price"]

        total_invested = snapshot_df["Market_Value"].sum()
        denom = total_portfolio_value if total_portfolio_value else total_invested
        snapshot_df["Weight"] = snapshot_df["Market_Value"] / denom if denom else 0

        snapshot_df = snapshot_df.sort_values("Market_Value", ascending=False)
    else:
        total_invested = 0.0

    snapshot_table = _df_to_table(
        snapshot_df,
        {
            "Ticker": ("Ticker", None),
            "Shares": ("Shares", lambda x: f"{x:,.2f}"),
            "Average_Buy_Price": ("Avg. Buy Price", _fmt_money),
            "Current_Price": ("Current Price", _fmt_money),
            "Market_Value": ("Market Value", _fmt_money),
            "Weight": ("Weight", _fmt_pct),
        },
    )

    n_holdings = len(updated_holdings)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NSE Momentum Strategy Dashboard</title>
<style>
  :root {{
    --bg: #0f1117;
    --card-bg: #1a1d27;
    --border: #2a2e3a;
    --text: #e6e8ee;
    --muted: #9aa0ad;
    --green: #3ddc84;
    --red: #ff5d5d;
    --yellow: #ffcc66;
    --blue: #5b9dff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
  }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.8rem; margin-bottom: 4px; }}
  .timestamp {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .stat-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 20px;
  }}
  .stat-card .label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
  .stat-card .value {{ font-size: 1.6rem; font-weight: 700; margin-top: 6px; }}
  .badge {{
    display: inline-block;
    padding: 4px 12px;
    border-radius: 999px;
    font-weight: 700;
    font-size: 0.85rem;
    letter-spacing: 0.05em;
  }}
  .risk-on {{ background: rgba(61, 220, 132, 0.15); color: var(--green); }}
  .neutral {{ background: rgba(255, 204, 102, 0.15); color: var(--yellow); }}
  .risk-off {{ background: rgba(255, 93, 93, 0.15); color: var(--red); }}
  section {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 20px;
  }}
  section h2 {{
    margin-top: 0;
    font-size: 1.2rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 10px;
  }}
  .section-buy h2 {{ color: var(--green); }}
  .section-sell h2 {{ color: var(--red); }}
  .section-hold h2 {{ color: var(--blue); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  th, td {{
    text-align: left;
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }}
  th {{ color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; }}
  tbody tr:hover {{ background: rgba(255,255,255,0.03); }}
  .empty-msg {{ color: var(--muted); font-style: italic; }}
  .table-wrap {{ overflow-x: auto; }}
  footer {{ color: var(--muted); font-size: 0.75rem; text-align: center; margin-top: 32px; }}
</style>
</head>
<body>
<div class="container">
  <h1>📈 NSE Momentum Strategy — Live Dashboard</h1>
  <div class="timestamp">Last run: {run_timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</div>

  <div class="grid">
    <div class="stat-card">
      <div class="label">Market Health</div>
      <div class="value"><span class="badge {regime_class}">{regime}</span></div>
      <div class="label" style="margin-top:8px;">{passing_count} / {universe_count} stocks passed funnel ({pct_passing:.1f}%)</div>
    </div>
    <div class="stat-card">
      <div class="label">Total Portfolio Value</div>
      <div class="value">{_fmt_money(total_portfolio_value)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Holdings Count</div>
      <div class="value">{n_holdings} / 15</div>
    </div>
    <div class="stat-card">
      <div class="label">Cash Hoarding (Liquid Funds)</div>
      <div class="value">{_fmt_money(cash_hoarding)}</div>
    </div>
  </div>

  <div class="grid">
    <div class="stat-card">
      <div class="label">Cash Freed from Sells</div>
      <div class="value">{_fmt_money(cash_freed)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Total Cash Available</div>
      <div class="value">{_fmt_money(cash_available)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Cash Deployed to Buys</div>
      <div class="value">{_fmt_money(cash_deployed)}</div>
    </div>
  </div>

  <section class="section-buy">
    <h2>✅ Action Checklist — BUY (Tomorrow Morning)</h2>
    <div class="table-wrap">{buy_table}</div>
  </section>

  <section class="section-sell">
    <h2>❌ Action Checklist — SELL (Tomorrow Morning)</h2>
    <div class="table-wrap">{sell_table}</div>
  </section>

  <section class="section-hold">
    <h2>🔒 Action Checklist — HOLD (No Action)</h2>
    <div class="table-wrap">{hold_table}</div>
  </section>

  <section>
    <h2>📊 Current Portfolio Snapshot (Post-Rebalance)</h2>
    <div class="table-wrap">{snapshot_table}</div>
  </section>

  <footer>
    Generated automatically by the NSE Momentum Screener pipeline. Not investment advice.
  </footer>
</div>
</body>
</html>
"""
    return html_doc
