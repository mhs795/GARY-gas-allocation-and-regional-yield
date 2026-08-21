"""
LNG price and netback series, on ACIL Allen's published methodology.

ACIL Allen produces the wholesale gas price projections that sit behind AEMO's
GSOO. Their model, GasMark, is a partial spatial equilibrium LP over supply
nodes, demand nodes, liquefaction and receiving facilities connected by pipeline
and shipping arcs, solved to maximise producer plus consumer surplus. GARY is the
same class of model, so most of that methodology is already here. What GARY did
NOT have is the piece ACIL Allen identify as the thing that actually sets east
coast prices: **the LNG netback**.

    "Price formation from 2026 is then based off the LNG netback pricing
     mechanism, which was the price setting mechanism until the price cap was
     introduced."
        -- ACIL Allen, Natural gas price forecasts for the Final 2023 IASR and
           for the 2024 GSOO (14 July 2023), s4.1

This module builds the price series that mechanism needs. It does not change any
behaviour on its own; model.py consumes the output when the netback lever is on.

WHAT IS DERIVED HERE, AND FROM WHAT
-----------------------------------
Everything below comes from ACIL Allen, *Wholesale natural gas prices for AEMO*,
Final Report, 14 November 2025 -- the report behind the **2026 GSOO**, which is
the same vintage as GARY's demand baselines. Its three scenarios carry the same
names as GARY's three baselines, so they map one to one with no interpretation:
Slower Growth, Step Change, Accelerated Transition.

1. **Brent oil price** (Table B.2), anchored at 2025/2030/2040/2050 and linearly
   interpolated between anchors.

2. **Oil-linked contract LNG price** (s B.9), exactly their formula:

       P_LNG = (FC + S * Pb) / (FX * C)

   with FC = US$0.40/mmbtu, S = 0.12, FX = 0.66 US$/A$, C = 1.055 GJ/mmbtu.

3. **Spot share of LNG sales** (Table B.3), anchored and interpolated.

4. **Blended Asian LNG price** (Table B.4), anchored and interpolated. This is
   the published headline series and it is treated as primary.

5. **Implied spot LNG price**, backed out of 2-4 so the three are consistent:

       P_spot = (P_blend - (1 - spot_share) * P_contract) / spot_share

   ACIL Allen give the blend and the contract formula but not the spot series;
   solving for it is the only way to keep the published blend exact while still
   having the two components the contract/spot weighting needs. Where the back-out
   would be degenerate (spot share at or below zero) the spot price falls back to
   the blend.

6. **Import injection price** (Table 2.1) = Asian LNG + shipping + regasification,
   with shipping A$0.80/GJ and regasification A$1.50/GJ. This reproduces their
   published table exactly, which is asserted as a test in ``main()``.

7. **Export netback** = Asian LNG - export netback deduction, then capped at the
   Gas Market Code price if the cap is applied (see below).

THE ONE NUMBER THAT IS NOT PUBLISHED
------------------------------------
The export netback deduction -- avoidable liquefaction (plant short-run marginal
cost plus fuel gas) and shipping -- is commercial-in-confidence. ACIL Allen do not
publish it, and neither does the ACCC, whose netback series is built on the same
avoidable-cost framework and which obtains the numbers directly from the
Queensland LNG producers. The default here, A$2.87/GJ, is the midpoint of the
publicly discussed US$1.5-2.5/mmbtu range converted at the same FX and heat
content ACIL Allen use. **It is a choice, not a source**, it lives on the
Parameters sheet of data/gary_inputs.xlsx precisely so it can be moved, and it is
the first number to test if a netback result matters.

Note that GARY does NOT need the Wallumbilla-to-Gladstone pipeline leg the ACCC
deducts: the netback here is struck at the LNG train node, which already sits
downstream of the APLNG/GLNG/WGP feed pipes and their tariffs in arcs.csv.
Deducting it again would double-count.

THE GAS MARKET CODE PRICE CAP
-----------------------------
The Commonwealth's $12/GJ cap is applied the way ACIL Allen apply it: as a
ceiling on the netback, not as a cap bolted onto domestic prices.

    "The price cap is operationalised in our model by setting the LNG netback
     price (measured at Wallumbilla) to not move above $12/GJ."
        -- ACIL Allen (14 July 2023), s4.1

That rule is self-terminating, which is why no end year is needed: once long-run
LNG prices pull the netback below $12 the ceiling stops binding on its own, which
is exactly ACIL Allen's stated assumption about how the Code lapses. Worth
knowing that their 2025 report is sceptical the cap binds at all in practice --
"the price cap has not necessarily acted as a price cap, but more like a price
floor" (s2.3.1) -- so the capped series is the conservative reading, not a
consensus one. A ``Netback_Uncapped_AUD_GJ`` column is emitted alongside so the
difference is always visible.

Output: data/lng_prices.csv, one row per (Scenario, Year). Generated, gitignored,
rebuilt by regenerate_data.py.
"""
import os

import pandas as pd

import params as P

DATA = os.path.join(os.path.dirname(__file__), "data")

ANCHORS_FILE = "acil_lng_anchors.csv"
PARAMS_FILE = "acil_lng_params.csv"
OUT_FILE = "lng_prices.csv"

YEARS = range(2025, 2051)

# ACIL Allen Table 2.1 / B.7, "Price of LNG injected into ECGM". Reproducing this
# is the check that the shipping and regasification adders are being applied the
# way they were published, so it is asserted rather than commented.
PUBLISHED_INJECTION = {
    ('SlowerGrowth', 2025): 16.29, ('SlowerGrowth', 2030): 15.56,
    ('SlowerGrowth', 2040): 17.24, ('SlowerGrowth', 2050): 17.39,
    ('StepChange', 2025): 16.29, ('StepChange', 2030): 13.57,
    ('StepChange', 2040): 12.80, ('StepChange', 2050): 12.29,
    ('Accelerated', 2025): 16.29, ('Accelerated', 2030): 12.50,
    ('Accelerated', 2040): 9.69, ('Accelerated', 2050): 9.11,
}


def load_params(data_dir=DATA):
    """Scalar assumptions, from the Parameters sheet of the inputs workbook.

    Falls back to acil_lng_params.csv -- the plain-text mirror of the same
    numbers -- if the workbook is absent, so a clone without it still builds.
    """
    from model import load_params as workbook_params
    if P.available():
        return workbook_params()
    df = pd.read_csv(os.path.join(data_dir, PARAMS_FILE))
    return {str(r['Parameter']): float(r['Value']) for _, r in df.iterrows()}


def contract_price(brent_usd_bbl, p):
    """ACIL Allen s B.9: oil-linked Asian LNG contract price in A$/GJ.

        P_LNG = (FC + S * Pb) / (FX * C)
    """
    return ((p['oil_link_fixed'] + p['oil_link_slope'] * brent_usd_bbl)
            / (p['fx_usd_per_aud'] * p['gj_per_mmbtu']))


def _interpolate(anchors, column):
    """Linear interpolation of one anchored column onto every year in YEARS.

    Anchors outside the horizon are honoured (they pull the interpolation);
    years beyond the last anchor hold flat rather than extrapolating a trend,
    because ACIL Allen's own long-run values are level assumptions, not slopes.
    """
    s = anchors.set_index('Year')[column].astype(float).sort_index()
    idx = sorted(set(s.index) | set(YEARS))
    return s.reindex(idx).interpolate(method='index').ffill().bfill().reindex(YEARS)


def build_scenario(anchors, p):
    """One scenario's full annual price series as a DataFrame indexed by year."""
    brent = _interpolate(anchors, 'Brent_USD_bbl')
    blend = _interpolate(anchors, 'LNG_Asia_AUD_GJ')
    share = _interpolate(anchors, 'SpotShare')

    contract = brent.map(lambda b: contract_price(b, p))
    # Back out the spot leg so contract/spot/blend stay mutually consistent at
    # every year, not just at the published anchors.
    spot = pd.Series(
        [(bl - (1 - sh) * ct) / sh if sh > 1e-9 else bl
         for bl, sh, ct in zip(blend, share, contract)],
        index=blend.index)

    netback_uncapped = blend - p['export_netback_deduction']
    netback_capped = netback_uncapped.clip(upper=p['code_price_cap'])
    injection = blend + p['shipping'] + p['regasification']

    return pd.DataFrame({
        'Year': list(YEARS),
        'Brent_USD_bbl': brent.round(4).values,
        'LNG_Contract_AUD_GJ': contract.round(4).values,
        'LNG_Spot_AUD_GJ': spot.round(4).values,
        'SpotShare': share.round(4).values,
        'LNG_Asia_AUD_GJ': blend.round(4).values,
        'Netback_Uncapped_AUD_GJ': netback_uncapped.round(4).values,
        'Netback_Capped_AUD_GJ': netback_capped.round(4).values,
        'Import_Injection_AUD_GJ': injection.round(4).values,
    })


def main():
    p = load_params()
    # Anchors come from the workbook's LNG_Anchors sheet when it is there, so a
    # scenario assumption is edited in one place; the CSV is the fallback mirror.
    anchors_all = P.sheet('LNG_Anchors')
    if anchors_all.empty:
        anchors_all = pd.read_csv(os.path.join(DATA, ANCHORS_FILE))

    frames = []
    for scenario, anchors in anchors_all.groupby('Scenario', sort=False):
        df = build_scenario(anchors, p)
        df.insert(0, 'Scenario', scenario)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)

    # Reproduce ACIL Allen's published injection-cost table. A silent drift here
    # would mean the adders or the anchors have been edited apart from the source.
    ix = out.set_index(['Scenario', 'Year'])['Import_Injection_AUD_GJ']
    for key, published in PUBLISHED_INJECTION.items():
        got = float(ix.loc[key])
        assert abs(got - published) < 0.01, (
            f"injection cost for {key} is {got:.2f}, ACIL Allen Table 2.1 "
            f"publishes {published:.2f} -- check acil_lng_anchors.csv and the "
            f"shipping/regasification adders in acil_lng_params.csv")

    path = os.path.join(DATA, OUT_FILE)
    out.to_csv(path, index=False)
    print(f"Wrote {path}: {len(out)} rows, {out['Scenario'].nunique()} scenarios "
          f"x {len(YEARS)} years")
    for scenario in out['Scenario'].unique():
        s = out[out['Scenario'] == scenario].set_index('Year')
        print(f"  {scenario:14s} netback  2025 ${s.loc[2025, 'Netback_Capped_AUD_GJ']:5.2f}"
              f"  2030 ${s.loc[2030, 'Netback_Capped_AUD_GJ']:5.2f}"
              f"  2040 ${s.loc[2040, 'Netback_Capped_AUD_GJ']:5.2f}"
              f"  2050 ${s.loc[2050, 'Netback_Capped_AUD_GJ']:5.2f}/GJ")
    return out


if __name__ == "__main__":
    main()
