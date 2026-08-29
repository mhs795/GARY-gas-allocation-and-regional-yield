"""Derive GARY's demand curves -- every tier, both directions -- from evidence.

GARY's three demand tiers are not fixed volumes. Each is a step curve around its
baseline: blocks that SHED when the nodal price rises above their willingness to
pay, and blocks that RAISE demand when the price falls below it. This module
derives those blocks and writes them to data/curtailment_params.csv, alongside
data/gpg_capacity.csv (the physical ceiling on gas-powered generation).

Run it again whenever a calibration input below moves:

    python src/build_demand_curves.py

------------------------------------------------------------------------------
THE REFERENCE PRICE -- and why it is the model's own, not a market price
------------------------------------------------------------------------------
An elasticity measures response to a price DEVIATION, so it needs the price the
consumer is deviating from. GARY's nodal prices are LP duals: short-run marginal
cost plus transport, which come out at $4-8/GJ against field costs of $4-14/GJ.
They are NOT east coast contract prices ($13-15/GJ), which are set by LNG export
parity and carry capital recovery and resource rent on top of SRMC.

Calibrating against a contract price was the original mistake here: it put 93.8%
of node-days below the reference, so the shed blocks almost never fired and an
upward response would have inflated demand ~16% everywhere, permanently, as a
pure artefact of comparing two different price series.

REFERENCE_PRICE below is therefore the demand-weighted mean nodal dual of the
inelastic baseline run (StepChange, Winter Medium, LNG Medium, 2025-2050). That
also fixes a second problem: the AEMO GSOO demand baselines already embed AEMO's
own assumed price path, so the elasticity must apply to deviations from the
baseline rather than to the absolute price level, or the price response is
counted twice.

``compute_reference_price()`` below recomputes it from the cached baseline run,
and main() prints a warning if REFERENCE_PRICE has drifted from it -- so a change
that moves baseline prices (new supply costs, a new GSOO vintage) shows up rather
than quietly leaving the curves calibrated against a stale price.

------------------------------------------------------------------------------
SOURCES
------------------------------------------------------------------------------
e (shed)     -0.180 -- short-run own-price elasticity of natural gas demand,
             Labandeira, Labeaga & Lopez-Otero (2017), "A meta-analysis on the
             price elasticity of energy demand", Energy Policy 102, 549-568,
             Table 6, significant at 1%.

             The full set of natural gas figures from that paper:
               short run, meta-regression   -0.180 (1%)   <- used here
               long run,  meta-regression   -0.684 (10%)  <- deliberately NOT used
               short run, mean of 230 est.  -0.184        <- robustness check
               long run,  mean of 230 est.  -0.568
             For scale, its short-run figures for other fuels are electricity
             -0.126, gasoline -0.293, diesel -0.153, heating oil -0.017 (n.s.).

             The LONG-RUN elasticity is not used because GARY's shed decision is
             a daily one -- a cold Tuesday in Melbourne, not a decade of appliance
             turnover -- and -0.684 would attribute a capital-stock response to a
             single day's price. The long-run figure belongs to a question GARY
             does not ask: how the baseline demand TRAJECTORY responds to a
             sustained price level. That trajectory comes from the AEMO GSOO,
             which is the same reason the elasticity here applies to deviations
             from the baseline rather than to the absolute price level.

e (raise)    -0.090 = ASYMMETRY x e (shed). See ASYMMETRY below.

ASYMMETRY    Demand responds LESS to price falls than to price rises: efficiency
             investments and plant closures triggered by high prices do not
             reverse when prices come back down. Gately & Huntington (2002),
             "The asymmetric effects of changes in price and income on energy
             and oil demand", The Energy Journal 23(1), 19-55, established this
             by splitting price into maximum-historical, cut and recovery series
             and finding much weaker response to the latter two.
             The DIRECTION is well evidenced; the RATIO here is a judgement, set
             deliberately at a conservative 0.5. It is a parameter, not a
             measurement -- vary it before leaning on any upward result.

Heat rates   CCGT ~7 GJ/MWh, OCGT ~13 GJ/MWh, NEM capacity-weighted average
             10.64 GJ/MWh (AEMO 2021, via Griffith University, "The role of gas
             price in wholesale electricity price", 2022-08).

DISPLACED_SRMC
             What extra gas generation actually pushes out at the margin, $/MWh,
             BY JURISDICTION. A single national coal figure was wrong: it priced
             SA and NT gas off a coal fleet that does not exist, and put 27.9% of
             the GPG response at nodes with nothing to displace.
               QLD, NSW  black coal. ~$26-28/MWh in 2020, sharply higher after
                         2022 with coal costs and the end of the price cap.
                         $45/MWh is a mid-range post-2022 figure and remains the
                         single most influential assumption here -- vary it.
               VIC       BROWN coal, mine-mouth and far cheaper than black:
                         Hazelwood's private SRMC was put at ~$3/MWh (Environment
                         Victoria, Hazelwood Mine Fire Inquiry). Gas essentially
                         cannot displace brown coal on running cost, and the
                         model should say so rather than pretend otherwise.
               SA        No coal since Northern (784 MW, Port Augusta) ceased
                         generation on 9 May 2016. Gas competes against imports
                         over Heywood and Project EnergyConnect, so it is priced
                         off the exporting region's cheap brown coal.
               NT        Nothing. Darwin-Katherine is a small isolated system
                         outside the NEM with no interconnection and >80% gas
                         already, so extra gas generation displaces nothing and
                         has nowhere to sell. GARY does not model that system;
                         zero is an honest "cannot say", not an estimate.
------------------------------------------------------------------------------
"""
import os

import pandas as pd

# --- reference price ---------------------------------------------------------
REFERENCE_PRICE = 5.49        # $/GJ, demand-weighted mean dual, inelastic baseline

# --- shed side (all tiers) ---------------------------------------------------
ELASTICITY = -0.180           # Labandeira et al. (2017)
SHED_MULTIPLES = (2, 4, 8)    # strikes at 2x, 4x, 8x the reference price

# Damps the shed response over the southern winter window. Kanellakis et al.
# ("The daily price and income elasticity of natural gas demand in Europe",
# Energy Reports 8, 2022) find European gas demand shows little or no DAILY price
# response through the heating season, with the response concentrated in the
# shoulder months. GARY sheds almost entirely in winter, so this matters.
# Defaults to 1.0 -- no adjustment -- so the shipped calibration is exactly what
# the elasticity implies. Set ~0.3-0.5 to test the daily-frequency evidence.
WINTER_SCALE = 1.0

# --- raise side --------------------------------------------------------------
ASYMMETRY = 0.5               # upward elasticity = ASYMMETRY x ELASTICITY
RAISE_FRACTIONS = (0.85, 0.70, 0.55)   # blocks priced at these fractions of P0

# Mass-market demand does NOT rise when gas gets cheap. Victoria banned gas
# connections in new homes from 1 January 2024 (~80% of Victorian homes were on
# gas) and the sector is in policy-driven structural decline; a lower commodity
# price does not reverse a connection ban. Households therefore shed but never
# expand. Set True only to test that assumption.
MASSMARKET_RAISES = False

# --- gas-powered generation --------------------------------------------------
# GPG has NO elasticity. A generator's willingness to pay per GJ of gas is an
# engineering substitution threshold: the cost of the generation it displaces
# divided by its heat rate, WTP = DISPLACED_SRMC[state] / heat_rate. Efficient
# plant can pay the most for gas, peakers the least. This is the largest demand response in the
# model and none of it comes from the elasticity literature -- do not read the
# GPG blocks as an elasticity estimate.
# $/MWh of generation displaced, and what it is, by jurisdiction. See the
# DISPLACED_SRMC note above -- a single national figure priced SA and NT gas off
# a coal fleet neither has.
DISPLACED_SRMC = {
    'QLD': (45.0, 'Black coal'),
    'NSW': (45.0, 'Black coal'),
    'VIC': (10.0, 'Brown coal, mine-mouth'),
    'SA':  (10.0, 'Imports from VIC/NSW (Heywood, Project EnergyConnect)'),
    'NT':  (0.0,  'Nothing - isolated gas-dominated system, no coal'),
}
HEAT_RATES = (7.0, 10.64, 13.0)         # GJ/MWh: CCGT, NEM average, OCGT

# GARY models gas, not the NEM. Nameplate headroom is ~3,300 TJ/d against 321
# TJ/d of baseline GPG demand (8.9% utilisation), so an unbounded response would
# let an unmodelled electricity market set gas demand. Expansion is therefore
# capped at this multiple of each node's baseline GPG demand as well as by
# physical headroom. 1.0 = GPG may at most double. This is a modelling guardrail,
# not a finding -- raise it only alongside an argument about NEM dispatch.
GPG_EXPANSION_CAP = 1.0

DATA = os.path.join(os.path.dirname(__file__), "data")
PARAMS_FILE = os.path.join(DATA, "curtailment_params.csv")
GPG_CAPACITY_FILE = os.path.join(DATA, "gpg_capacity.csv")
GPG_RAISE_FILE = os.path.join(DATA, "gpg_raise_blocks.csv")


def shed_blocks(prefix, reference_price=REFERENCE_PRICE, elasticity=ELASTICITY,
                multiples=SHED_MULTIPLES, winter_scale=WINTER_SCALE):
    """Blocks that shed as price rises. Shares are increments of 1 - (P/P0)^e."""
    out, cumulative = [], 0.0
    for i, mult in enumerate(multiples, start=2):
        shed = 1.0 - mult ** elasticity
        out.append({'Tier': f"{prefix}_B{i}", 'StrikePrice': round(reference_price * mult, 2),
                    'Share': round(shed - cumulative, 6), 'WinterScale': winter_scale,
                    'Direction': 'shed'})
        cumulative = shed
    return out


def raise_blocks(prefix, reference_price=REFERENCE_PRICE, elasticity=ELASTICITY,
                 asymmetry=ASYMMETRY, fractions=RAISE_FRACTIONS):
    """Blocks that add demand as price falls. Shares are increments of
    (P/P0)^e_up - 1, with e_up = asymmetry x e (see ASYMMETRY in the docstring)."""
    e_up = elasticity * asymmetry
    out, cumulative = [], 0.0
    for i, frac in enumerate(fractions, start=1):
        extra = frac ** e_up - 1.0
        out.append({'Tier': f"{prefix}_U{i}", 'StrikePrice': round(reference_price * frac, 2),
                    'Share': round(extra - cumulative, 6), 'WinterScale': 1.0,
                    'Direction': 'raise'})
        cumulative = extra
    return out


def gpg_raise_blocks(heat_rates=HEAT_RATES, displaced=None):
    """Per-node GPG expansion ladder.

    Value is engineering, not econometrics: what a generator can pay per GJ to
    displace the marginal generation IN ITS OWN JURISDICTION, at its own heat
    rate -- ``DISPLACED_SRMC[state] / heat_rate``. A node whose jurisdiction has
    nothing cheaper to displace gets no expansion blocks at all.

    Share is the fraction of that node's nameplate headroom each rung may call
    on, split evenly because GARY carries no CCGT/OCGT split for the fleet. That
    even split is the weakest assumption in this file.
    """
    displaced = DISPLACED_SRMC if displaced is None else displaced
    fac = pd.read_csv(os.path.join(DATA, "gpg_facilities.csv"))
    state_of = (fac.groupby('Node')['State']
                   .agg(lambda x: x.mode().iat[0]).to_dict())
    n = len(heat_rates)
    rows = []
    for node, state in sorted(state_of.items()):
        srmc, tech = displaced.get(state, (0.0, 'Unknown jurisdiction'))
        for i, hr in enumerate(sorted(heat_rates), start=1):
            value = srmc / hr
            if value <= 0:
                continue
            rows.append({'Node': node, 'State': state, 'Block': f"GPG_U{i}",
                         'HeatRate': hr, 'DisplacedSRMC': srmc, 'DisplacedTech': tech,
                         'ValuePerGJ': round(value, 3), 'Share': round(1.0 / n, 6)})
    return pd.DataFrame(rows)


def build_gpg_capacity():
    """Nameplate capacity per node (TJ/d) for the modelled GPG fleet, from the
    AEMO Gas Bulletin Board nameplate register (facilitytype BBGPG)."""
    nb = pd.read_csv(os.path.join(DATA, "GasBBNameplateRatingCurrent.csv"))
    cap = (nb[nb['facilitytype'] == 'BBGPG']
           .groupby(nb['facilityname'].astype(str).str.lower())['capacityquantity'].max())
    fac = pd.read_csv(os.path.join(DATA, "gpg_facilities.csv"))
    fac['Nameplate'] = fac['FacilityName'].astype(str).str.lower().map(cap)
    matched = fac['Nameplate'].notna().sum()
    out = (fac.dropna(subset=['Nameplate']).groupby('Node')
              .agg(Nameplate=('Nameplate', 'sum'), MeanDemand=('MeanDemand', 'sum'))
              .reset_index())
    # Carried in the data rather than read from this module at solve time, so the
    # guardrail travels with the numbers it constrains.
    out['ExpansionCap'] = GPG_EXPANSION_CAP
    out.to_csv(GPG_CAPACITY_FILE, index=False)
    return out, matched, len(fac)


def compute_reference_price(baseline="StepChange", winter="Medium", lng="Medium",
                            netback=None):
    """Demand-weighted mean nodal dual of the inelastic baseline run, from the
    results cache. Returns None if that scenario has not been solved.

    Demand-weighted rather than a flat mean because a price at a node consuming
    nothing is not a price consumers face. Deliberately NOT applied automatically:
    the reference comes from a solve that itself depends on these parameters, so
    it is calibrated once against the inelastic base case and then held fixed.
    """
    import results_io
    import params as P
    cache = os.path.join(DATA, "precalculated_results.pkl")
    if not os.path.exists(cache):
        return None
    # The key must carry the netback segment or this never matches. Netback
    # pricing is ON by default, so every scenario the dashboard writes is keyed
    # `..._Netback` -- and this lookup, which omitted it, therefore returned None
    # on every machine with a perfectly good cache. main() then reported "no
    # cached baseline run" and the drift check silently never ran, through exactly
    # the change (supply-side depletion) that moved the reference price 62%.
    if netback is None:
        netback = str(P.get_str('netback_pricing_default', 'TRUE')
                      ).strip().upper() in ('TRUE', '1', 'YES')
    base = f"Base_{baseline}_Winter_{winter}_LNG_{lng}"
    scen = results_io.load(cache).get('all_scenarios', {})
    years = scen.get(base + '_Netback' if netback else base) or scen.get(base)
    if not years:
        return None
    demand = pd.read_csv(os.path.join(DATA, f"demand_{baseline}.csv"))
    num = den = 0.0
    for yr in years:
        d = demand[demand['Year'] == yr['Year']][['Node', 'Day', 'Demand']]
        merged = yr['prices'].merge(d, on=['Node', 'Day'])
        num += float((merged['Price'] * merged['Demand']).sum())
        den += float(merged['Demand'].sum())
    return num / den if den else None


def main():
    rows = shed_blocks("MassMarket")
    if MASSMARKET_RAISES:
        rows += raise_blocks("MassMarket")
    rows += raise_blocks("Industrial")

    try:
        df = pd.read_csv(PARAMS_FILE)
    except FileNotFoundError:
        df = pd.DataFrame(columns=['Tier', 'StrikePrice'])
    for col in ("Share", "WinterScale", "Direction"):
        if col not in df.columns:
            df[col] = pd.NA
    df.loc[df['Direction'].isna() & df['Tier'].isin(['GPG', 'Industrial']), 'Direction'] = 'shed'
    generated = {r['Tier'] for r in rows}
    df = df[~df['Tier'].astype(str).isin(generated)]
    df = df[~df['Tier'].astype(str).str.match(r'^(MassMarket|Industrial|GPG)_[BU]\d+$')]
    pd.concat([df, pd.DataFrame(rows)], ignore_index=True).to_csv(PARAMS_FILE, index=False)

    gpg = gpg_raise_blocks()
    gpg.to_csv(GPG_RAISE_FILE, index=False)
    caps, matched, total = build_gpg_capacity()

    print(f"Reference price ${REFERENCE_PRICE}/GJ (model baseline dual), "
          f"elasticity {ELASTICITY} shed / {ELASTICITY * ASYMMETRY:.3f} raise")
    for r in rows:
        arrow = 'sheds above' if r['Direction'] == 'shed' else 'raises below'
        print(f"  {r['Tier']:16s} {arrow} ${r['StrikePrice']:7.2f}/GJ   "
              f"{r['Share']*100:6.3f}% of {'headroom' if r['Tier'].startswith('GPG') else 'baseline'}")
    shed_total = sum(r['Share'] for r in rows if r['Direction'] == 'shed')
    print(f"  mass-market inelastic core {(1 - shed_total)*100:.3f}% "
          f"(served or shed at the value of lost load)")

    print("\nGPG raise ladder, by jurisdiction (value = displaced $/MWh / heat rate):")
    for state, (srmc, tech) in sorted(DISPLACED_SRMC.items()):
        nodes = sorted(gpg[gpg['State'] == state]['Node'].unique()) if len(gpg) else []
        if srmc <= 0:
            allnodes = sorted(pd.read_csv(os.path.join(DATA, "gpg_facilities.csv"))
                              .query("State == @state")['Node'].unique())
            print(f"  {state:4s} ${srmc:5.1f}/MWh  {tech:52s} NO EXPANSION at {', '.join(allnodes)}")
            continue
        vals = sorted(gpg[gpg['State'] == state]['ValuePerGJ'].unique(), reverse=True)
        print(f"  {state:4s} ${srmc:5.1f}/MWh  {tech:52s} "
              f"${'/$'.join(f'{v:.2f}' for v in vals)}/GJ  at {', '.join(nodes)}")

    print(f"\nGPG capacity: matched {matched}/{total} facilities, "
          f"{caps['Nameplate'].sum():,.0f} TJ/d nameplate vs "
          f"{caps['MeanDemand'].sum():,.0f} TJ/d baseline "
          f"({caps['MeanDemand'].sum()/caps['Nameplate'].sum()*100:.1f}% utilisation); "
          f"expansion capped at {GPG_EXPANSION_CAP:.0%} of baseline")
    implied = compute_reference_price()
    if implied is None:
        print("\nNOTE: no cached baseline run, so REFERENCE_PRICE could not be checked.")
    elif abs(implied - REFERENCE_PRICE) > 0.25:
        print(f"\nWARNING: the cached baseline implies ${implied:.2f}/GJ but "
              f"REFERENCE_PRICE is ${REFERENCE_PRICE:.2f}/GJ. The curves are "
              f"calibrated against a stale price -- update REFERENCE_PRICE and rerun.")
    else:
        print(f"\nReference price check: cached baseline implies ${implied:.2f}/GJ (ok).")
    print(f"-> {PARAMS_FILE}\n-> {GPG_CAPACITY_FILE}\n-> {GPG_RAISE_FILE}")


if __name__ == "__main__":
    main()
