"""
Customer-segment gas prices, on ACIL Allen's two-run weighting method.

GARY's nodal prices are LP duals: the marginal cost of one more TJ at a node, so
short-run marginal cost plus transport. ACIL Allen are explicit that this is NOT
the price a customer pays:

    "Our GasMark model models hypothetical spot prices and does not model gas
     contracts specifically... Contract prices could be expected to be slightly
     higher than this price to take account of contract terms such as take or
     pay, interruptible and other services that are provided."
        -- ACIL Allen, Wholesale natural gas prices for AEMO (14 Nov 2025), s3.1

So they run the model twice and blend the two, per segment:

    "Run 1: Run GasMark model and produce an average annual wholesale price for
     each region in the ECGM which is reflective of a contract price (and includes
     the operation of the price cap)
     Run 2: Run GasMark and produce a monthly price series which is more
     reflective of the spot markets in the ECGM that are not subject to the price
     cap...
     Produce a weighted price that takes into account supply procured through
     contracts and supply procured through the spot market"
        -- ACIL Allen (14 July 2023), s2.6.2

This module is that blending layer, applied to a solved GARY scenario. It adds no
constraint and re-solves nothing: it reads the duals a run already produced and
turns them into the four price series ACIL Allen report to AEMO.

THE TWO RUNS, HERE
------------------
ACIL Allen's Run 1 and Run 2 differ in temporal resolution and in whether the Gas
Market Code cap is applied. GARY dispatches all 365 days in one pass, so both
series come out of the same solve:

  contract leg  annual DEMAND-WEIGHTED mean of the daily nodal duals. Weighting by
                volume rather than taking a flat mean is what makes it a price for
                the gas actually bought; a flat mean over 365 days lets a handful
                of quiet summer days pull an annual contract price down.
  spot leg      annual mean of the daily duals with no smoothing, which is the
                series that carries the winter peaks. Under netback pricing the
                Code cap is applied to the contract leg only, matching ACIL
                Allen's "Run 2 removes the price cap".

WEIGHTS (all from ACIL Allen (14 July 2023), s2.6 and s2.7)
-----------------------------------------------------------
  residential/commercial   100% contract. "We also assume that supply for this
                           market is 100 per cent contracted."
  industrial               90% contract / 10% spot, applied to all regions.
  GPG, CCGT                80% contract / 20% spot -- baseload role.
  GPG, OCGT                20% contract / 80% spot -- "based on their 'peaking'
                           role and their low load factor", plus a long-run
                           premium "to account for the additional costs they
                           typically pay to source high volumes of gas at short
                           notice... reserving pipeline capacity or the costs of
                           storage."

WHAT IS DELIBERATELY NOT REPRODUCED
-----------------------------------
ACIL Allen's Step 2 overlay -- vertical integration, gentailer portfolio effects,
market power, and "inflating" new supply costs toward netback because new entrants
price off the next best alternative -- is judgement applied outside the model, per
generator and per contract. It is not reproducible from published material, and
guessing at it would put a number on this output that looks like ACIL Allen's and
is not. These prices are therefore ACIL Allen's *mechanical* layer only, and will
sit below their published forecasts wherever that overlay adds to them.

The weights live on the Segment_Weights sheet of data/gary_parameters.xlsx (mirrored
in data/acil_segment_weights.csv) so they can be moved without editing this
module.
"""
import os

import pandas as pd

import params as P

DATA = os.path.join(os.path.dirname(__file__), "data")

WEIGHTS_FILE = "acil_segment_weights.csv"

# Fallback weights, used only if the file is missing. Same numbers as the CSV;
# duplicated so the module still runs on a clone that has not regenerated data.
DEFAULT_WEIGHTS = {
    'ResidentialCommercial': {'contract': 1.00, 'spot': 0.00, 'premium': 0.00},
    'Industrial':            {'contract': 0.90, 'spot': 0.10, 'premium': 0.00},
    'GPG_CCGT':              {'contract': 0.80, 'spot': 0.20, 'premium': 0.00},
    'GPG_OCGT':              {'contract': 0.20, 'spot': 0.80, 'premium': 1.00},
}


def load_weights(data_dir=DATA):
    """Segment weights as {segment: {contract, spot, premium}}.

    From the Segment_Weights sheet of the parameters workbook, falling back to the
    plain-text CSV mirror and then to DEFAULT_WEIGHTS.
    """
    df = P.sheet('Segment_Weights')
    if df.empty:
        try:
            df = pd.read_csv(os.path.join(data_dir, WEIGHTS_FILE))
        except FileNotFoundError:
            return dict(DEFAULT_WEIGHTS)
    out = {}
    for _, r in df.iterrows():
        out[str(r['Segment'])] = {
            'contract': float(r['ContractShare']),
            'spot': float(r['SpotShare']),
            'premium': float(r.get('PremiumAUDGJ', 0) or 0),
        }
    return out


def _legs(year_result):
    """Contract and spot price legs for one solved year, as {node: $/GJ} each.

    The contract leg is demand-weighted across days, the spot leg is a plain
    daily mean. Nodes with no throughput are dropped rather than carried at a
    degenerate dual (the Beetaloo problem -- see get_results in model.py).
    """
    prices = year_result['prices']
    if prices is None or len(prices) == 0:
        return {}, {}

    # Volume actually consumed at each node-day, across every tier that has a
    # price to pay: distribution/GPG/industrial all show up in the served columns.
    weights = []
    for series, col in (('gpg', 'Served'), ('industrial', 'Served')):
        df = year_result.get(series)
        if df is not None and len(df):
            weights.append(df[['Day', 'Node', col]].rename(columns={col: 'W'}))
    if weights:
        w = pd.concat(weights, ignore_index=True).groupby(['Day', 'Node'],
                                                          observed=True)['W'].sum()
        w = w.reset_index()
        merged = prices.merge(w, on=['Day', 'Node'], how='left')
        merged['W'] = merged['W'].fillna(0.0)
    else:
        merged = prices.assign(W=0.0)

    contract, spot = {}, {}
    for node, grp in merged.groupby('Node', observed=True):
        tot = float(grp['W'].sum())
        # No metered volume at this node (a pure hub): fall back to the flat mean
        # so the node still gets a price rather than silently vanishing.
        contract[node] = (float((grp['Price'] * grp['W']).sum() / tot) if tot > 0
                          else float(grp['Price'].mean()))
        spot[node] = float(grp['Price'].mean())
    return contract, spot


def segment_prices(results, weights=None, code_price_cap=None):
    """ACIL Allen-style segment prices for a solved scenario.

    ``results`` is the list of per-year result dicts a solve returns. Returns a
    long DataFrame with one row per (Year, Node, Segment) and columns
    ``Contract``, ``Spot``, ``Price``.

    ``code_price_cap``, if given, is applied to the CONTRACT leg only, which is
    ACIL Allen's treatment: the Code anchors contract negotiations, while the spot
    market is explicitly not subject to it.
    """
    weights = weights or load_weights()
    rows = []
    for yr in results:
        contract, spot = _legs(yr)
        year = yr['Year']
        for node in contract:
            c, s = contract[node], spot[node]
            if code_price_cap:
                c = min(c, float(code_price_cap))
            for segment, w in weights.items():
                rows.append({
                    'Year': year, 'Node': node, 'Segment': segment,
                    'Contract': c, 'Spot': s,
                    'Price': w['contract'] * c + w['spot'] * s + w['premium'],
                })
    return pd.DataFrame(rows, columns=['Year', 'Node', 'Segment',
                                       'Contract', 'Spot', 'Price'])
