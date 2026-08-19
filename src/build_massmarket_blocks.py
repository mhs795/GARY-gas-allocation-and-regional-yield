"""Derive the mass-market demand curve blocks from a published price elasticity.

GARY represents mass-market (distribution-level residential/commercial) gas as a
step demand curve: the load is split into blocks, each with a strike price, and a
block is shed rather than supplied once the nodal price exceeds its strike. This
script derives those blocks from evidence instead of assertion and writes them
into data/curtailment_params.csv.

METHOD
    Constant-elasticity demand  Q(P) = Q0 * (P/P0)^e  discretised onto a
    log-spaced price grid. The share shed once the price reaches P is
    1 - (P/P0)^e, and each block's share is the increment between grid points.

SOURCES
    P0  = $13.56/GJ -- average price offered by gas producers to retailers for
          2026 supply, ACCC Gas Inquiry 2017-2030 (December 2025 interim report).
          The east coast contract range has been steady at $13-15/GJ since 2023.
          https://www.accc.gov.au/inquiries-and-consultations/gas-inquiry-2017-30
    e   = -0.180 -- short-run own-price elasticity of natural gas demand,
          Labandeira, Labeaga & Lopez-Otero (2017), "A meta-analysis on the price
          elasticity of energy demand", Energy Policy 102, 549-568, Table 6
          (significant at 1%; long-run counterpart -0.684, and the 230-estimate
          sample mean is -0.184, so the short-run figure is robust).

    The residual (inelastic) share is NOT written here. It stays in the model as
    the existing `shortage` variable priced at the value of lost load, so the
    blocks below only describe the part of the load that has a price response.

CAVEATS -- both matter when reading results
    1. EXTRAPOLATION. -0.18 is estimated on the modest price variation in the
       historical record, not on prices 8x the contract price. The upper blocks
       are therefore an extrapolation of the fitted curve, not a measurement.
       The inelastic residual caps how far that extrapolation can run.
    2. FREQUENCY MISMATCH. The literature elasticity is estimated on monthly or
       annual data; GARY applies it to a daily nodal price. Kanellakis et al.
       find that at DAILY frequency European gas demand shows little or no price
       response through the heating season, with the response concentrated in the
       shoulder months ("The daily price and income elasticity of natural gas
       demand in Europe", Energy Reports 8, 2022). WINTER_SCALE below exists to
       represent that; it defaults to 1.0 (no seasonal adjustment) so the shipped
       calibration is exactly what the elasticity implies and nothing more.

Run this again if the ACCC reference price moves materially. It rewrites only the
MassMarket_* rows; the GPG and Industrial strikes are left untouched.

It runs as the last step of regenerate_data.py because build_curtailable_demand.py
rewrites curtailment_params.csv from scratch with only the GPG/Industrial strikes,
which would otherwise drop the blocks on every regeneration.
"""
import os

import pandas as pd

# --- calibration inputs (see SOURCES above) --------------------------------
REFERENCE_PRICE = 13.56       # $/GJ, ACCC producer offers for 2026 supply
ELASTICITY = -0.180           # short-run own-price, Labandeira et al. (2017)
MULTIPLES = (2, 4, 8)         # log-spaced grid: strikes at 2x, 4x, 8x P0

# Scales every block share during the southern winter peak (see CAVEAT 2).
# 1.0 = no seasonal adjustment. Set ~0.3-0.5 to reflect the daily-frequency
# evidence that heating-season demand barely responds to price.
WINTER_SCALE = 1.0

PARAMS_FILE = os.path.join(os.path.dirname(__file__), "data", "curtailment_params.csv")


def blocks(reference_price=REFERENCE_PRICE, elasticity=ELASTICITY,
           multiples=MULTIPLES):
    """Return [(tier name, strike $/GJ, share of mass-market demand), ...].

    Shares are the increments of the constant-elasticity curve across the price
    grid, so they sum to the total elastic share and the remainder is inelastic.
    """
    out, cumulative = [], 0.0
    for i, mult in enumerate(multiples, start=2):
        shed = 1.0 - mult ** elasticity          # (P/P0)^e with P/P0 = mult
        out.append((f"MassMarket_B{i}",
                    round(reference_price * mult, 2),
                    round(shed - cumulative, 6)))
        cumulative = shed
    return out


def main():
    rows = blocks()
    # build_curtailable_demand.py writes this file first with only the GPG and
    # Industrial strikes, so read whatever is there and append; on a fresh clone
    # where neither has run yet, start from an empty table.
    try:
        df = pd.read_csv(PARAMS_FILE)
    except FileNotFoundError:
        df = pd.DataFrame(columns=['Tier', 'StrikePrice'])
    for col in ("Share", "WinterScale"):
        if col not in df.columns:
            df[col] = pd.NA
    df = df[~df['Tier'].astype(str).str.startswith("MassMarket_")]
    new = pd.DataFrame([{'Tier': t, 'StrikePrice': p, 'Share': s,
                         'WinterScale': WINTER_SCALE} for t, p, s in rows])
    pd.concat([df, new], ignore_index=True).to_csv(PARAMS_FILE, index=False)

    total = sum(s for _, _, s in rows)
    print(f"Reference price ${REFERENCE_PRICE}/GJ, elasticity {ELASTICITY}")
    for t, p, s in rows:
        print(f"  {t:16s} strike ${p:7.2f}/GJ   share {s*100:6.3f}%")
    print(f"  elastic total {total*100:.3f}%, inelastic core "
          f"{(1 - total)*100:.3f}% (served or shed at the value of lost load)")
    print(f"-> {PARAMS_FILE}")


if __name__ == "__main__":
    main()
