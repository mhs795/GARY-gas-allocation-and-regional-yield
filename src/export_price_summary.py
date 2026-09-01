"""Domestic volume-weighted delivered price for every cached scenario, as a workbook.

Two tabs, the shape the price comparisons have always been read in:

  levels          $/GJ, one row per year, one column per scenario
  percent change  the same grid as % against the central case

The price is dashboard.headline_price -- the volume-weighted price at the DEMAND
nodes, weighted by what physically arrived at each node each day. Importing it
rather than recomputing it here is the point: a second implementation is a second
number to reconcile, and this workbook is quoted next to the dashboard's own KPI.
That is also why "domestic" is not a filter applied afterwards -- the LNG trains
are simply not in the node set being averaged.

The central case is Step Change / Winter Medium / LNG Medium with netback pricing,
so its own percent-change column is zero by construction and every other column
reads as a deviation from the run the commentary treats as the base case.

    python src/export_price_summary.py                  # write output/
    python src/export_price_summary.py --out some.xlsx  # somewhere else

run_standard_set.py calls write_summary() once the solves land, so the workbook is
never staler than the cache it came from.
"""
import argparse
import os
import sys

import pandas as pd
from openpyxl.chart import LineChart, Reference

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import results_io
from dashboard import headline_price, short_key

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'data', 'precalculated_results.pkl')
OUT = os.path.join(ROOT, 'output', 'domestic_price_summary.xlsx')

# The run everything else is measured against.
CENTRAL = 'Base_StepChange_Winter_Medium_LNG_Medium_Netback'


def key_order(keys):
    """`keys` ordered as the standard set declares them, extras alphabetically last.

    Imported inside the function because run_standard_set imports this module:
    at module level the two would be a cycle. Falls back to plain sorting if the
    set cannot be built, since column order is presentation, not correctness.
    """
    keys = set(keys)
    try:
        from run_standard_set import build, key_for
        spec = [key_for(kw) for _, kw in build()]
    except Exception:
        spec = []
    seen, out = set(), []
    for k in spec:
        if k in keys and k not in seen:
            seen.add(k)
            out.append(k)
    return out + sorted(keys - seen)


def price_frame(scenarios):
    """Year-indexed $/GJ, one column per scenario, columns in standard-set order."""
    cols = {}
    for key in key_order(scenarios):
        years = scenarios[key]
        cols[key] = pd.Series(
            {int(r['Year']): headline_price(r) for r in years}).sort_index()
    df = pd.DataFrame(cols).sort_index()
    df.index.name = 'Year'
    return df


def pct_frame(levels, central=CENTRAL):
    """`levels` as % change against `central`. Its own column is zero throughout."""
    if central not in levels.columns:
        raise SystemExit(f"central scenario not in the cache: {central}")
    base = levels[central]
    return (levels.div(base, axis=0) - 1.0) * 100.0


def _add_chart(ws, df, title, y_title, anchor):
    """A line chart of every column in `df`, anchored below the data."""
    chart = LineChart()
    chart.title = title
    chart.y_axis.title = y_title
    chart.x_axis.title = 'Year'
    chart.height, chart.width = 12, 30
    chart.style = 2
    n_rows, n_cols = len(df), len(df.columns)
    # Column A is Year, so the series span B..(n_cols+1); min_row=1 pulls the
    # header in as each series name, which is what titles the legend.
    data = Reference(ws, min_col=2, max_col=n_cols + 1, min_row=1, max_row=n_rows + 1)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=n_rows + 1))
    for s in chart.series:
        s.smooth = False
        s.marker.symbol = 'none'
    ws.add_chart(chart, anchor)


def write_summary(cache=CACHE, out=OUT, log=True):
    """Read the results cache and write the two-tab workbook. Returns its path."""
    scenarios = results_io.load(cache).get('all_scenarios', {})
    if not scenarios:
        raise SystemExit(f"no scenarios in {cache}")

    levels = price_frame(scenarios)
    pct = pct_frame(levels)
    # Long keys are unreadable as headers and worse in a legend; short_key is the
    # dashboard's own shorthand, so the columns are named the way the app names them.
    labels = {k: short_key(k) for k in levels.columns}
    levels_out, pct_out = levels.rename(columns=labels), pct.rename(columns=labels)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with pd.ExcelWriter(out, engine='openpyxl') as xl:
        levels_out.to_excel(xl, sheet_name='levels')
        pct_out.to_excel(xl, sheet_name='percent change')
        # Anchored clear of the data so neither chart sits on a cell it plots.
        _add_chart(xl.book['levels'], levels_out,
                   'Domestic volume-weighted delivered price',
                   '$/GJ', f'A{len(levels_out) + 4}')
        _add_chart(xl.book['percent change'], pct_out,
                   f'Change against {short_key(CENTRAL)}',
                   '% vs central', f'A{len(pct_out) + 4}')
    if log:
        print(f"  wrote {os.path.relpath(out, ROOT)}  "
              f"({len(levels_out.columns)} scenarios x {len(levels_out)} years)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cache', default=CACHE)
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()
    write_summary(args.cache, args.out)


if __name__ == '__main__':
    main()
