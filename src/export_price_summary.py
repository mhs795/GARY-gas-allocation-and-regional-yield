"""Domestic volume-weighted delivered price for every cached scenario, as a workbook.

Three tabs, the shape the price comparisons have always been read in:

  levels          $/GJ, one row per year, one column per scenario
  percent change  the same grid as % against the central case
  charts          EVERY chart in the workbook: the two price grids, the three
                  system-cost charts, then one percent-change panel per scenario
  system cost     total system cost, the central case's breakdown, and mean
                  composition by scenario -- written only when the cache carries
                  the model's recorded cost components

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
COST_SHEET = 'system cost'

# The objective's own terms, in the order they read as a cost stack. lng_revenue is
# negative -- the system is PAID for an export cargo -- so it is charted apart from
# the costs rather than stacked with them.
COST_TERMS = ['production', 'transport', 'storage', 'capex',
              'gpg_curtailment', 'ind_curtailment', 'shortage']
COST_ALL = COST_TERMS + ['lng_revenue']


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


def cost_frames(scenarios, central=CENTRAL):
    """(total by scenario, breakdown for `central`, mean $bn/yr, horizon total $bn).

    All in $bn. Returns ``None`` if the cache predates cost-component recording --
    the components are written by the model at solve time (see
    ``GasMarketModel.get_results``), because reconstructing them from the saved
    frames cannot be made to tie: it has to re-derive the year-varying import cost,
    the per-row scarcity rent, the foundation/spot export split and the $0 reserved
    tranche that sits inside ``production``. A breakdown whose parts do not sum to
    the total is worse than no breakdown, so this reports nothing rather than
    something close.
    """
    have = [k for k in scenarios
            if scenarios[k] and scenarios[k][0].get('cost_components')]
    if not have:
        return None
    keys = [k for k in key_order(scenarios) if k in have]
    total = pd.DataFrame({
        short_key(k): pd.Series({int(r['Year']): r['total_cost'] / 1e9
                                 for r in scenarios[k]}).sort_index()
        for k in keys})
    total.index.name = 'Year'
    if central in scenarios and central in have:
        br = pd.DataFrame({
            t: pd.Series({int(r['Year']): r['cost_components'].get(t, 0.0) / 1e9
                          for r in scenarios[central]}).sort_index()
            for t in COST_ALL})
    else:
        br = pd.DataFrame(columns=COST_ALL)
    br.index.name = 'Year'
    mean = pd.DataFrame({
        t: {short_key(k): sum(r['cost_components'].get(t, 0.0)
                              for r in scenarios[k]) / len(scenarios[k]) / 1e9
            for k in keys}
        for t in COST_ALL})
    mean.index.name = 'Scenario'
    # Aggregate over the whole horizon rather than per year. A single year is the
    # wrong unit to compare an investment decision on -- a build lands in one year
    # and pays back over the rest -- so the aggregate is what the by-component
    # comparison across scenarios is drawn from.
    agg = pd.DataFrame({
        t: {short_key(k): sum(r['cost_components'].get(t, 0.0)
                              for r in scenarios[k]) / 1e9
            for k in keys}
        for t in COST_ALL})
    agg.index.name = 'Scenario'
    return total, br, mean, agg


def _line_chart(book, sheet, df, cols, title, y_title, size, span_zero=False,
                first_row=0):
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
            'name':       [sheet, first_row, i + 1],
            'categories': [sheet, first_row + 1, 0, first_row + n, 0],
            'values':     [sheet, first_row + 1, i + 1, first_row + n, i + 1],
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


def _stacked_chart(book, sheet, df, first_row, title, size, x_title='Year'):
    """Stacked column over every column of `df`, sourced from `sheet`.

    `first_row` is the 0-indexed sheet row holding the headers. lng_revenue is
    negative and stacks below the axis, which is the honest picture: it is the one
    term the system is PAID rather than pays.
    """
    chart = book.add_chart({'type': 'column', 'subtype': 'stacked'})
    n = len(df)
    for i in range(len(df.columns)):
        chart.add_series({
            'name':       [sheet, first_row, i + 1],
            'categories': [sheet, first_row + 1, 0, first_row + n, 0],
            'values':     [sheet, first_row + 1, i + 1, first_row + n, i + 1],
        })
    chart.set_title({'name': title})
    chart.set_x_axis({'name': x_title})
    chart.set_y_axis({'name': '$bn', 'num_format': '0.0',
                      'major_gridlines': {'visible': True}})
    chart.set_size(size)
    return chart


def _write_total_row(ws, df, first_row, label, fmt):
    """A summed row under a year-indexed table.

    Written onto the sheet AFTER the frame rather than concatenated into it, so
    the chart ranges above still cover data rows only -- a total folded into the
    frame would be plotted as another year and double every stack.
    """
    r = first_row + len(df) + 1
    ws.write(r, 0, label, fmt)
    for i, col in enumerate(df.columns):
        ws.write_number(r, i + 1, float(df[col].sum()), fmt)
    return r


def _write_total_col(ws, df, first_row, label, fmt):
    """A summed column beside a scenario-indexed table, for the same reason."""
    c = len(df.columns) + 1
    ws.write(first_row, c, label, fmt)
    for j in range(len(df)):
        ws.write_number(first_row + 1 + j, c, float(df.iloc[j].sum()), fmt)
    return c


def write_cost_tables(xl, scenarios):
    """Write the system-cost TABLES. Charts live on their own tab -- see write_summary.

    Returns the frames for the charts to be drawn from, or None when the cache
    carries no components, so the caller can say so rather than publish an empty
    tab. The frames handed back are the plain ones: every total is written onto
    the sheet beside or beneath its table rather than folded into the frame, so a
    chart range never picks a total up and plots it as another category.
    """
    frames = cost_frames(scenarios)
    if frames is None:
        return None
    total, br, mean, agg = frames
    book, sheet = xl.book, COST_SHEET
    bold = book.add_format({'bold': True, 'top': 1, 'num_format': '0.00'})

    # Each table gets one row of clear air for its total plus a three-row gap, so
    # the totals can never land on the next table's header.
    r_total = 1
    r_break = r_total + len(total) + 5
    r_mean = r_break + len(br) + 5
    r_agg = r_mean + len(mean) + 5
    total.to_excel(xl, sheet_name=sheet, startrow=r_total)
    br.to_excel(xl, sheet_name=sheet, startrow=r_break)
    mean.to_excel(xl, sheet_name=sheet, startrow=r_mean)
    agg.to_excel(xl, sheet_name=sheet, startrow=r_agg)
    ws = xl.sheets[sheet]

    ws.write(r_total - 1, 0, 'Total system cost, $bn')
    ws.write(r_break - 1, 0, f'Cost breakdown, {short_key(CENTRAL)}, $bn')
    ws.write(r_mean - 1, 0, 'Mean $bn/yr by component')
    ws.write(r_agg - 1, 0, 'Total over the horizon by component, $bn')

    # Year-indexed tables sum DOWN to a horizon total; scenario-indexed tables sum
    # ACROSS to a per-scenario total. Both are the same question asked of the axis
    # that varies.
    span = f'{total.index.min()}-{total.index.max()}'
    _write_total_row(ws, total, r_total, f'Total {span}', bold)
    _write_total_row(ws, br, r_break, f'Total {span}', bold)
    _write_total_col(ws, mean, r_mean, 'Total', bold)
    _write_total_col(ws, agg, r_agg, 'Total', bold)

    foot = r_agg + len(agg) + 2
    ws.write(foot, 0,
             'Terms are the dispatch objective\'s own, signed as they enter it, so they '
             'sum to total_cost exactly. lng_revenue is negative: the system is PAID for '
             'an export cargo, which is why a total across components is below the sum '
             'of the costs.')
    ws.write(foot + 1, 0,
             'NOT comparable across reservation levels: the reserved tranche is priced at '
             '$0 in dispatch, so reserving more always lowers the figure -- totals '
             'included. See docs/scenarios.md.')
    ws.set_column(0, 0, 34)
    # The row offsets go back with the frames. They used to be recomputed in
    # write_summary to point the charts at the right block, which is two places
    # holding one layout -- move a table and the charts silently plot the table
    # above it.
    return {'total': (total, r_total), 'break': (br, r_break),
            'mean': (mean, r_mean), 'agg': (agg, r_agg)}


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

        # Tables first, so the data tabs come before the charts tab in the
        # workbook and every chart has its source already on the sheet.
        cost = write_cost_tables(xl, scenarios)

        # EVERY chart lives here. The data tabs stay pure tables: a chart sitting
        # under a grid is in the way of the grid, and a reader comparing two
        # charts should not have to hop between tabs to do it.
        panels = book.add_worksheet(PANEL_SHEET)
        all_cols = range(len(levels_out.columns))
        row = 1
        panels.insert_chart(row, 0,
                            _line_chart(book, LEVELS_SHEET, levels_out, all_cols,
                                        'Domestic volume-weighted delivered price',
                                        '$/GJ', wide))
        row += 24
        panels.insert_chart(row, 0,
                            _line_chart(book, PCT_SHEET, pct_out, all_cols,
                                        f'Change against {central_label}',
                                        '% vs central', wide, span_zero=True))
        row += 24
        if cost:
            (total, r_total), (br, r_break) = cost['total'], cost['break']
            agg, r_agg = cost['agg']
            panels.insert_chart(row, 0,
                                _line_chart(book, COST_SHEET, total,
                                            range(len(total.columns)),
                                            'Total system cost', '$bn', wide,
                                            first_row=r_total))
            row += 24
            panels.insert_chart(row, 0,
                                _stacked_chart(book, COST_SHEET, br, r_break,
                                               f'Cost breakdown, {central_label}',
                                               wide))
            row += 24
            # The one the scenarios are actually compared on: every option's whole
            # horizon, split by where the money went.
            panels.insert_chart(row, 0,
                                _stacked_chart(book, COST_SHEET, agg, r_agg,
                                               'Aggregate system cost by component, '
                                               'whole horizon', wide,
                                               x_title='Scenario'))
            row += 24

        # One panel per scenario, off the percent-change grid rather than a copy
        # of it. The combined chart above answers "which scenarios move together";
        # these answer "what does this one do", which sixteen overlaid series
        # cannot. Central is skipped -- its column is zero by construction, so its
        # panel would be a flat line on the axis.
        slot = 0
        for i, name in enumerate(pct_out.columns):
            if name == central_label:
                continue
            # Each panel scales to its own series -- a common scale would flatten
            # the small movers against the reservation runs -- but the axis is
            # forced to span zero, so "above or below central" is never an
            # artefact of where the axis happens to start.
            chart = _line_chart(book, PCT_SHEET, pct_out, [i], name,
                                '% vs central', {'width': 560, 'height': 300},
                                span_zero=True)
            panel_row, side = divmod(slot, 2)
            panels.insert_chart(row + panel_row * 17, side * 10, chart)
            slot += 1
        cost_ok = cost is not None

    if log:
        note = '' if cost_ok else '  (no system-cost tab: cache predates cost components)'
        print(f"  wrote {os.path.relpath(out, ROOT)}  "
              f"({len(levels_out.columns)} scenarios x {n} years){note}")
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
