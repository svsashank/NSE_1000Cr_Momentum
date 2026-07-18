"""
rank_velocity.py — Independent rank-momentum metric for the NSE universe.

Reads the last WINDOW authoritative `screen_runs`, reconstructs each ticker's
rank trajectory, and computes how fast it is climbing (or falling) *relative to
the rest of the universe*. This does NOT re-rank anything — the universe stays
ordered by `rank_score`. Velocity is a separate signal, upserted per-ticker into
the `rank_momentum` table for the frontend to display alongside rank_score.

Design decisions (see also the discussion in-repo):
  • Percentile, not raw rank, is the internal metric. Universe size N drifts
    run-to-run (data availability, universe refresh), so rank 60 in a 2300-name
    universe ≠ rank 60 in a 2380-name universe. percentile = rank / N is
    comparable across runs. Lower percentile = better (rank 1 → ~0.0).
  • Velocity = slope of percentile vs *calendar date* over the window, via
    Theil-Sen (median of pairwise slopes) — robust to the spiky single-run
    jumps that data drop-outs cause. velocity = -slope*100, so a POSITIVE
    number means "climbing toward the top", in units of percent-of-universe/day.
  • Absence is treated as MISSING (NaN), never as "rank = worst". A ticker
    dropping out of a run is almost always yfinance lag, not a rank collapse.
    A ticker must appear in >= MIN_OBS runs before we trust a slope.
  • consistency = fraction of observed steps that moved in the rising direction
    — a clean monotone climber scores high; a whipsaw scores low.

Run as a final step of run_screen.yml (after the screen has pushed its row), so
the window includes the just-completed run.
"""

import os
import math
from datetime import datetime, date
from collections import defaultdict

from supabase import create_client

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

# ── Tuning ────────────────────────────────────────────────────────────────────
WINDOW        = int(os.environ.get('RANK_VELOCITY_WINDOW', '20'))  # authoritative runs
MIN_OBS_FRAC  = 0.40   # must appear in >= this fraction of the window...
MIN_OBS_ABS   = 4      # ...and in at least this many runs, to get a velocity
# ──────────────────────────────────────────────────────────────────────────────


def theil_sen_slope(xs, ys):
    """Median of pairwise slopes. xs, ys are equal-length lists of floats."""
    slopes = []
    n = len(xs)
    for i in range(n):
        for j in range(i + 1, n):
            dx = xs[j] - xs[i]
            if dx != 0:
                slopes.append((ys[j] - ys[i]) / dx)
    if not slopes:
        return None
    slopes.sort()
    m = len(slopes)
    return slopes[m // 2] if m % 2 else 0.5 * (slopes[m // 2 - 1] + slopes[m // 2])


def authoritative_runs(supabase):
    """Return [(id, run_date_str)] for the most recent WINDOW distinct run_dates,
    oldest-first. For each date keep the run with the latest triggered_at
    (the post-close, authoritative run)."""
    resp = (supabase.table('screen_runs')
            .select('id,run_date,triggered_at')
            .order('run_date', desc=True)
            .order('triggered_at', desc=True)
            .limit(WINDOW * 6)      # generous headroom for multiple runs/day
            .execute())
    rows = resp.data or []
    seen = {}
    for r in rows:                   # already sorted date desc, triggered_at desc
        rd = r.get('run_date')
        if rd and rd not in seen:
            seen[rd] = r['id']       # first seen per date == authoritative
    # most recent WINDOW dates, returned oldest-first
    dates_desc = sorted(seen.keys(), reverse=True)[:WINDOW]
    dates_asc  = sorted(dates_desc)
    return [(seen[d], d) for d in dates_asc]


def rank_map_for_run(records):
    """Given a run's all_universe list, return {ticker: (rank, percentile)}.
    Rank is derived from rank_score ordering (positional), which is the true
    rank_score rank regardless of any stored-order drift. N = number of names
    with a usable rank_score."""
    ranked = [r for r in records if r.get('rank_score') is not None]
    ranked.sort(key=lambda r: r['rank_score'], reverse=True)
    n = len(ranked)
    out = {}
    for i, r in enumerate(ranked):
        rank = i + 1
        out[r['ticker']] = (rank, rank / n)
    return out


def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    runs = authoritative_runs(supabase)
    if len(runs) < 2:
        print(f'Not enough history yet ({len(runs)} run(s)) — need >=2. Skipping.')
        return

    print(f'Building rank-velocity panel over {len(runs)} authoritative runs: '
          f'{runs[0][1]} … {runs[-1][1]}')

    # panel[ticker] = list of (date_ordinal, rank, percentile), in run order
    panel = defaultdict(list)
    latest_date_ord = None
    latest_ranks = {}
    for run_id, rd in runs:
        rec_resp = (supabase.table('screen_runs')
                    .select('all_universe')
                    .eq('id', run_id)
                    .execute())
        recs = (rec_resp.data[0]['all_universe'] if rec_resp.data else None) or []
        if not recs:
            print(f'  {rd}: empty all_universe — skipped')
            continue
        d_ord = date.fromisoformat(rd).toordinal()
        rmap = rank_map_for_run(recs)
        for tkr, (rank, pct) in rmap.items():
            panel[tkr].append((d_ord, rank, pct))
        latest_date_ord = d_ord
        latest_ranks = rmap          # last iteration = most recent run
        print(f'  {rd}: {len(rmap)} ranked names')

    n_runs   = len([1 for _ in runs])
    min_obs  = max(MIN_OBS_ABS, math.ceil(MIN_OBS_FRAC * n_runs))
    now_iso  = datetime.utcnow().isoformat()
    latest_run_date = runs[-1][1]

    out_rows = []
    for tkr, pts in panel.items():
        pts.sort(key=lambda p: p[0])
        if len(pts) < min_obs:
            continue
        xs   = [p[0] for p in pts]
        pcts = [p[2] for p in pts]
        slope = theil_sen_slope(xs, pcts)          # percentile per day
        if slope is None:
            continue
        velocity = round(-slope * 100.0, 4)        # %-of-universe/day, + = rising

        # net positions climbed over the observed span (first -> last obs)
        rank_chg    = pts[0][1] - pts[-1][1]        # + = climbed
        window_days = xs[-1] - xs[0]

        # consistency: fraction of adjacent steps that improved (percentile fell)
        improves = sum(1 for a, b in zip(pcts, pcts[1:]) if b < a)
        steps    = len(pcts) - 1
        consistency = round(improves / steps, 4) if steps else None

        # current standing (from the most recent run; null if it dropped out)
        cur = latest_ranks.get(tkr)
        cur_rank = cur[0] if cur else None
        cur_pct  = round(cur[1], 4) if cur else None

        out_rows.append({
            'ticker'            : tkr,
            'current_rank'      : cur_rank,
            'current_percentile': cur_pct,
            'rank_chg'          : int(rank_chg),
            'velocity'          : velocity,
            'consistency'       : consistency,
            'n_obs'             : len(pts),
            'window_days'       : int(window_days),
            'run_date'          : latest_run_date,
            'updated_at'        : now_iso,
        })

    if not out_rows:
        print('No tickers met the minimum-observation bar — nothing to write.')
        return

    # Upsert in batches (latest snapshot per ticker)
    total = 0
    for i in range(0, len(out_rows), 200):
        supabase.table('rank_momentum').upsert(
            out_rows[i:i + 200], on_conflict='ticker').execute()
        total += len(out_rows[i:i + 200])
    print(f'✅ rank_momentum → {total} tickers upserted '
          f'(min_obs={min_obs}, window={n_runs} runs)')

    # Eyeball log: fastest risers currently inside rank 15–120
    approaching = [r for r in out_rows
                   if r['current_rank'] is not None and 15 <= r['current_rank'] <= 120]
    approaching.sort(key=lambda r: r['velocity'], reverse=True)
    print('\nTop 20 climbers currently in rank 15–120:')
    print(f'{"ticker":<16}{"rank":>6}{"Δrank":>7}{"vel/day":>10}{"consist":>9}{"obs":>5}')
    for r in approaching[:20]:
        print(f'{r["ticker"]:<16}{r["current_rank"]:>6}{r["rank_chg"]:>+7}'
              f'{r["velocity"]:>10.3f}{(r["consistency"] or 0):>9.2f}{r["n_obs"]:>5}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        # Never fail the screen workflow because the velocity add-on hiccuped.
        import traceback
        print('rank_velocity FAILED (non-fatal):', e)
        traceback.print_exc()
