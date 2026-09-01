"""Domestic volume-weighted delivered price for every cached scenario, as a workbook.

Three tabs, the shape the price comparisons have always been read in:

  levels          $/GJ, one row per year, one column per scenario
  percent change  the same grid as % against the central case
  charts          one percent-change panel per scenario, read off that grid

The price is dashboard.headline_price -- the volume-weighted price at the DEMAND
nodes, weighted by what physically arrived at each node each day. Importing it
rather than recomputing it here is the point: a second implementation is a second
number to reconcile, and this workbook is quoted next to the dashboard's own KPI.
That is also why "domestic" is not a filter applied afterwards -- the LNG trains
are simply not in the node set being averaged.

The central case is Step Change / Winter Medium / LNG Medium with netback pricing,
so its own percent-change column is zero by construction and every other column
reads as a deviation from the run the commentary treats as the base case.

Charts are written with xlsxwriter, not openpyxl. openpyxl emits a chart Excel
will open but a stricter reader will not: it gives the category axis
``axPos="l"`` -- both axes claiming the left -- and omits ``<c:delete>`` and
``<c:crosses>`` altogether. Excel infers the missing pieces; OnlyOffice draws
nothing. xlsxwriter writes the axes out in full, so the same file renders in
Excel, OnlyOffice and LibreOffice alike. openpyxl is still the right tool for
READING workbooks elsewhere in this repo; it is chart output specifically that
this module avoids it for.

    python src/export_price_summary.py                  # write output/
    python src/export_price_summary.py --out some.xlsx  # somewhere else

run_standard_set.py calls write_summary() once the solves land, so the workbook is
never staler than the cache it came from.
"""
import argparse
import math
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import results_io
from dashboard import headline_price, short_key

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'data', 'precalculated_results.pkl')
OUT = os.path.join(ROOT, 'output', 'domestic_price_summary.xlsx')

# The run everything else is measured against.
CENTRAL = 'Base_StepChange_Winter_Medium_LNG_Medium_Netback'

LEVELS_SHEET, PCT_SHEET, PANEL_SHEET = 'levels', 'percent change', 'charts'


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


def half_grid(lo, hi):
    """`lo`/`hi` snapped out onto a 0.5 grid, so the axis reads in halves.

    Outward, not to the nearest: rounding a max of 21.03 to 21.0 would put the
    peak of the series on the frame or just past it. The bound moves away from
    the data, never into it, so nothing is ever clipped to tidy the axis.

    Degenerate ranges (a flat series) still get half a unit of air either side --
    an axis whose min equals its max has nothing to draw against.
    """
    lo, hi = math.floor(lo * 2) / 2, math.ceil(hi * 2) / 2
    return (lo - 0.5, hi + 0.5) if lo == hi else (lo, hi)


def _line_chart(book, sheet, df, cols, title, y_title, size, span_zero=False):
    """A line chart over `cols` of `df`, sourced from `sheet`.

    Row 1 holds the headers and column A the years, so a DataFrame column at
    position `i` is spreadsheet column `i + 1` and its values run rows 2..n+1.

    The y-axis is bounded explicitly on a 0.5 grid rather than left to autoscale,
    which is what keeps the tick labels round numbers instead of whatever the
    renderer picks off the data. `span_zero` additionally forces the axis to
    include zero -- wanted on the percent-change charts, where "above or below
    central" must not depend on where the axis starts, and not on the levels
    chart, where a $/GJ series in the teens would be squashed into the top of a
    frame that starts at nothing.
    """
    chart = book.add_chart({'type': 'line'})
    n = len(df)
    for i in cols:
        chart.add_series({
            'name':       [sheet, 0, i + 1],
            'categories': [sheet, 1, 0, n, 0],
            'values':     [sheet, 1, i + 1, n, i + 1],
            'marker':     {'type': 'none'},
            'smooth':     False,
        })
    plotted = df.iloc[:, list(cols)]
    lo, hi = float(plotted.min().min()), float(plotted.max().max())
    if span_zero:
        lo, hi = min(0.0, lo), max(0.0, hi)
    lo, hi = half_grid(lo, hi)
    chart.set_title({'name': title})
    chart.set_x_axis({'name': 'Year'})
    chart.set_y_axis({'name': y_title, 'min': lo, 'max': hi,
                      # Halves, so one decimal is exactly enough: it shows the
                      # .5 ticks and invents no precision beyond them.
                      'num_format': '0.0',
                      'major_gridlines': {'visible': True}})
    chart.set_size(size)
    if len(cols) == 1:
        chart.set_legend({'none': True})     # the title already names the series
    return chart


def write_summary(cache=CACHE, out=OUT, log=True):
    """Read the results cache and write the three-tab workbook. Returns its path."""
    scenarios = results_io.load(cache).get('all_scenarios', {})
    if not scenarios:
        raise SystemExit(f"no scenarios in {cache}")

    levels = price_frame(scenarios)
    pct = pct_frame(levels)
    # Long keys are unreadable as headers and worse in a legend; short_key is the
    # dashboard's own shorthand, so the columns are named the way the app names them.
    labels = {k: short_key(k) for k in levels.columns}
    levels_out, pct_out = levels.rename(columns=labels), pct.rename(columns=labels)
    central_label = short_key(CENTRAL)
    n = len(levels_out)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with pd.ExcelWriter(out, engine='xlsxwriter') as xl:
        levels_out.to_excel(xl, sheet_name=LEVELS_SHEET)
        pct_out.to_excel(xl, sheet_name=PCT_SHEET)
        book = xl.book
        wide = {'width': 960, 'height': 460}

        all_cols = range(len(levels_out.columns))
        xl.sheets[LEVELS_SHEET].insert_chart(
            f'A{n + 4}',                       # clear of the data it plots
            _line_chart(book, LEVELS_SHEET, levels_out, all_cols,
                        'Domestic volume-weighted delivered price', '$/GJ', wide))
        xl.sheets[PCT_SHEET].insert_chart(
            f'A{n + 4}',
            _line_chart(book, PCT_SHEET, pct_out, all_cols,
                        f'Change against {central_label}', '% vs central', wide,
                        span_zero=True))

        # One panel per scenario, off the same grid rather than a copy of it. The
        # combined chart above answers "which scenarios move together"; these
        # answer "what does this one do", which sixteen overlaid series cannot.
        # Central is skipped -- its column is zero by construction, so its panel
        # would be a flat line on the axis.
        panels = book.add_worksheet(PANEL_SHEET)
        slot = 0
        for i, name in enumerate(pct_out.columns):
            if name == central_label:
                continue
            # Each panel scales to its own series -- a common scale would
            # flatten the small movers against the reservation runs -- but the
            # axis is forced to span zero, so "above or below central" is never
            # an artefact of where the axis happens to start.
            chart = _line_chart(book, PCT_SHEET, pct_out, [i], name,
                                '% vs central', {'width': 560, 'height': 300},
                                span_zero=True)
            # Two per row, spaced to clear a 560x300 chart at default row
            # heights and column widths with a margin either side.
            row, side = divmod(slot, 2)
            panels.insert_chart(row * 17 + 1, side * 10, chart)
            slot += 1

    if log:
        print(f"  wrote {os.path.relpath(out, ROOT)}  "
              f"({len(levels_out.columns)} scenarios x {n} years)")
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
