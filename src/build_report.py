"""Which builds to REPORT, and from which year -- as against which the model switched on.

A field development carries no CapEx: its capital is inside the 2C gas cost (see
expansion_options.csv). So the capacity MIP can switch one on at zero cost whether or
not its gas is ever used, and it often does. Measured 24 Sep 2026 on the central case,
14 of the 17 developments the MIP "built" produced nothing over 2025-50 (TODO #32).
That changes no price, flow or cost -- an idle development does nothing -- but a build
list that names them overstates what gets developed.

So a ZERO-CAPEX FIELD DEVELOPMENT is reported as built from its FIRST YEAR OF GAS, and
not at all if it never produces. Everything with CapEx -- pipelines, import terminals --
is reported from the year it is switched on, as before, because paying for it is the
decision. So is anything AEMO lists as Committed (the Carpentaria pilot): that is
happening whatever the model makes of its gas, so it is not the model's choice to report.

Several developments can front one tranche (the three Surat projects share Surat's 2C
row), and the results carry production per tranche, not per project. The attribution is
therefore the minimal one that explains the output: each year, the tranche's peak daily
production is covered by the developments already reported, and if it exceeds them the
next ones in build order (ties by larger capacity first) are reported until it does not.
A development that the gas never needed is never reported.

Reporting only. The model's own build decisions (``res['builds']``) are untouched --
the supply-curve reconstruction and the myopic solve still read them as the physical
state -- which is also why this needs no re-solve.
"""
import pandas as pd

TOL_TJD = 0.5          # production below this is solver noise, not first gas


def _free_developments(expansion):
    """{project: (target node, NewCapacity)} for zero-CapEx, uncommitted field developments."""
    out = {}
    for _, r in expansion.iterrows():
        capex = pd.to_numeric(r.get('CapEx'), errors='coerce')
        committed = str(r.get('Status', '')).strip() == 'Committed'
        if (str(r.get('Type')) == 'Terminal' and (pd.isna(capex) or capex == 0)
                and not committed):
            out[r['Name']] = (str(r['Target']), float(r['NewCapacity']))
    return out


def _peak_2c(production):
    """{node: peak daily TJ from its undeveloped (2C) tranche} for one solved year."""
    if production is None or len(production) == 0:
        return {}
    p = production[['Node', 'Potential', 'Value']].astype({'Node': str, 'Potential': str})
    p = p[p['Potential'] == 'True']
    return p.groupby('Node')['Value'].max().to_dict()


def reported_build_years(results, expansion):
    """{project: year it is reported as built}, over a scenario's per-year results.

    ``results`` is the list a solve returns (sorted or not); ``expansion`` the
    expansion_options frame. Projects the model built but that are never reported
    -- idle free developments -- are absent.
    """
    free = _free_developments(expansion)
    out, switched_on, counted_cap = {}, {}, {}
    for res in sorted(results, key=lambda r: r['Year']):
        year = res['Year']
        for b in res.get('builds') or []:
            switched_on.setdefault(b, year)
            if b not in free:
                out.setdefault(b, year)
        peak = _peak_2c(res.get('production'))
        for node, need in peak.items():
            if need <= counted_cap.get(node, 0.0) + TOL_TJD:
                continue
            waiting = sorted((e for e, (n, _) in free.items()
                              if n == node and e in switched_on and e not in out),
                             key=lambda e: (switched_on[e], -free[e][1], e))
            for e in waiting:
                out[e] = year
                counted_cap[node] = counted_cap.get(node, 0.0) + free[e][1]
                if need <= counted_cap[node] + TOL_TJD:
                    break
    return out
