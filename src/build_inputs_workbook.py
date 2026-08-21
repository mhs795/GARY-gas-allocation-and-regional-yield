"""
Create data/gary_inputs.xlsx, the single inputs workbook every parameter is read
from (see params.py).

This is a ONE-OFF SCAFFOLD, not part of regenerate_data.py. The workbook is a
committed source input that is meant to be edited by hand; regenerating it from
this script would silently discard those edits, so:

  * it REFUSES to overwrite an existing workbook unless given --force
  * it is not in the regenerate_all pipeline and no button calls it

Use it to create the workbook on a fresh clone, to inspect what the defaults are,
or with --check to list parameters the code asks for that the workbook does not
have (which is how a typo in a parameter name gets caught -- params.py falls back
to the in-code default rather than raising).

The ACIL Allen sheets seed from the CSVs the netback work shipped with, so the
workbook stays the authority and the CSVs remain a readable plain-text mirror.
"""
import argparse
import os

import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "data")
WORKBOOK = os.path.join(DATA, "gary_inputs.xlsx")

# (Group, Parameter, Value, Unit, Source/Note)
PARAMETERS = [
    ("Horizon", "horizon_start", 2025, "year", "First modelled year"),
    ("Horizon", "horizon_end", 2050, "year", "Last modelled year"),

    ("Value of lost load", "voll_per_gj", 300.0, "A$/GJ",
     "Price on unserved must-serve gas. NOTE the National Gas Rules set VoLL at "
     "$800/GJ in the Victorian DWGM and the STTM cap at $400/GJ (AEMO, Gas Market "
     "Parameters Review 2022, Feb 2023). $300 predates that check and is "
     "conservative; changing it moves every historical result."),

    ("Curtailment strikes", "strike_gpg_default", 22.0, "A$/GJ",
     "Fallback GPG strike if curtailment_params.csv is absent"),
    ("Curtailment strikes", "strike_ind_default", 120.0, "A$/GJ",
     "Fallback industrial strike if curtailment_params.csv is absent"),

    ("Seasonality", "winter_day_start", 150, "day of year",
     "Southern winter window used by the Winter lever and the per-block WinterScale"),
    ("Seasonality", "winter_day_end", 250, "day of year", "Inclusive"),

    ("SA dunkelflaute", "dunkelflaute_year", 2027, "year",
     "Event year for the SA wind/solar drought lever"),
    ("SA dunkelflaute", "dunkelflaute_node", "Adelaide", "node", "SA demand node"),
    ("SA dunkelflaute", "dunkelflaute_day_start", 152, "day of year", "1 June"),
    ("SA dunkelflaute", "dunkelflaute_day_end", 181, "day of year", "30 June, inclusive"),
    ("SA dunkelflaute", "dunkelflaute_mult", 2.75, "multiple",
     "Adelaide GPG gas call multiplier: ~73 TJ/d normal early-June -> ~200 TJ/d, "
     "between the node winter peak and the ~309 TJ/d SA regional GPG peak"),

    ("Gas reservation", "reservation_levels", "0.05,0.10,0.20,0.30", "fractions",
     "Shares of LNG export volume offered by the dashboard slider"),

    ("Network roles", "lng_nodes", "APLNG,GLNG,QCLNG", "nodes",
     "East coast LNG export trains"),
    ("Network roles", "import_nodes", "Port_Kembla", "nodes",
     "Regasification terminals: potential supply priced on an international price, "
     "not a field development cost"),

    ("Data centre demand", "datacentre_state_node", "NSW:Sydney,VIC:Melbourne",
     "state:node", "Where each state's data centre volume lands"),

    ("Capacity model", "discount_rate_default", 0.07, "fraction",
     "NPV discount rate for the perfect-foresight capacity MIP"),
    ("Capacity model", "peak_day_weight", 5.0, "days",
     "Days represented by the annual peak representative day (adequacy)"),
    ("Capacity model", "terminal_earliest", 2028, "year",
     "Earliest build year for an LNG import terminal"),
    ("Capacity model", "capacity_base_year", 2025, "year", "Discounting base year"),

    # --- ACIL Allen gas pricing methodology ------------------------------------
    ("ACIL Allen LNG price", "oil_link_fixed", 0.40, "US$/mmbtu",
     "ACIL Allen (14 Nov 2025) B.9: FC in P_LNG=(FC+S*Pb)/(FX*C)"),
    ("ACIL Allen LNG price", "oil_link_slope", 0.12, "fraction",
     "ACIL Allen (14 Nov 2025) B.9: S, slope to Brent"),
    ("ACIL Allen LNG price", "fx_usd_per_aud", 0.66, "US$/A$",
     "ACIL Allen (14 Nov 2025) B.9: FX"),
    ("ACIL Allen LNG price", "gj_per_mmbtu", 1.055, "GJ/mmbtu",
     "ACIL Allen (14 Nov 2025) B.9: C"),
    ("ACIL Allen LNG price", "shipping", 0.80, "A$/GJ",
     "ACIL Allen (14 Nov 2025) B.11.1: US$0.56/mmbtu average shipping"),
    ("ACIL Allen LNG price", "regasification", 1.50, "A$/GJ",
     "ACIL Allen (14 Nov 2025) B.11.2: allowance in all cases"),
    ("ACIL Allen LNG price", "export_netback_deduction", 2.87, "A$/GJ",
     "NOT PUBLISHED by ACIL Allen or the ACCC. Avoidable liquefaction (plant SRMC "
     "+ fuel gas) plus shipping, ACCC netback framework; midpoint of the public "
     "US$1.5-2.5/mmbtu range at FX 0.66, C 1.055. A CHOICE, NOT A SOURCE - the "
     "first number to test if a netback result matters."),
    ("ACIL Allen LNG price", "code_price_cap", 12.00, "A$/GJ",
     "Gas Market Code cap, ACIL Allen (14 Nov 2025) 2.3.1; applied as a ceiling on "
     "the LNG netback per ACIL Allen (14 Jul 2023) 4.1. Self-terminating: once the "
     "netback falls below it the ceiling stops binding."),
    ("ACIL Allen LNG price", "export_headroom", 1.0, "multiple",
     "Ceiling on each train's liquefaction as a multiple of its planned export "
     "volume. 1.0 = exports may only be DECLINED, never expanded (default). Above "
     "1.0 lets spare liquefaction absorb cheap gas, which is what makes the netback "
     "ANCHOR domestic prices rather than only cap them in scarcity - but GARY has "
     "no reserves constraint, so a high value lets exports soak up field "
     "deliverability indefinitely."),
]

# Winter and LNG demand levers, currently coded in solve.py.
SCENARIO_LEVERS = [
    ("Winter", "Low", 1.0, "", "", "Multiplier on Melbourne/Adelaide/Sydney "
     "distribution demand over the winter window"),
    ("Winter", "Medium", 1.5, "", "", ""),
    ("Winter", "High", 2.2, "", "", ""),
    ("LNG", "Low", 0.04, 2026, 2030, "Annual decline step applied to LNG export "
     "demand; see get_lng_mult in solve.py for the piecewise path"),
    ("LNG", "Medium", 1.0, "", "", "Flat, no adjustment"),
    ("LNG", "High", 1.6, 2026, 2030, "Multiplier over the high window; 1.1 outside it"),
]


def _seed_from_csv(name, columns=None):
    """Read one of the plain-text ACIL Allen sources, if present."""
    path = os.path.join(DATA, name)
    try:
        df = pd.read_csv(path)
    except FileNotFoundError:
        return pd.DataFrame(columns=columns or [])
    return df


def build(force=False):
    if os.path.exists(WORKBOOK) and not force:
        raise SystemExit(
            f"{WORKBOOK} already exists. It is a committed source input meant to be "
            f"edited by hand -- rebuilding would discard those edits. Pass --force "
            f"only if you intend to reset it to the defaults in this script.")

    params = pd.DataFrame(PARAMETERS,
                          columns=['Group', 'Parameter', 'Value', 'Unit', 'Source'])
    levers = pd.DataFrame(SCENARIO_LEVERS,
                          columns=['Lever', 'Level', 'Value', 'FromYear', 'ToYear', 'Note'])
    anchors = _seed_from_csv("acil_lng_anchors.csv")
    weights = _seed_from_csv("acil_segment_weights.csv")

    with pd.ExcelWriter(WORKBOOK, engine='openpyxl') as xl:
        params.to_excel(xl, sheet_name='Parameters', index=False)
        levers.to_excel(xl, sheet_name='Scenario_Levers', index=False)
        anchors.to_excel(xl, sheet_name='LNG_Anchors', index=False)
        weights.to_excel(xl, sheet_name='Segment_Weights', index=False)
        # Readable column widths -- this workbook is meant to be opened, not just
        # parsed, and a 300-character Source column at default width is unusable.
        widths = {'Parameters': [22, 30, 16, 14, 90],
                  'Scenario_Levers': [10, 10, 10, 12, 12, 70],
                  'LNG_Anchors': [16, 8, 16, 20, 12, 46],
                  'Segment_Weights': [24, 16, 12, 14, 90]}
        for name, ws in xl.sheets.items():
            for i, w in enumerate(widths.get(name, []), start=1):
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
            ws.freeze_panes = 'A2'

    print(f"Wrote {WORKBOOK}")
    print(f"  Parameters       {len(params):3d} rows, {params['Group'].nunique()} groups")
    print(f"  Scenario_Levers  {len(levers):3d} rows")
    print(f"  LNG_Anchors      {len(anchors):3d} rows")
    print(f"  Segment_Weights  {len(weights):3d} rows")


def check():
    """Import the model and report parameters it wanted that the workbook lacks."""
    import params as P
    if not P.available():
        raise SystemExit(f"No workbook at {WORKBOOK}. Run this script without --check.")
    import model            # noqa: F401  -- importing is what triggers the lookups
    import capacity_model   # noqa: F401
    import solve            # noqa: F401
    miss = P.missing()
    if miss:
        print("Parameters requested but NOT found in the workbook (in-code defaults "
              "were used):")
        for name in miss:
            print(f"  - {name}")
        raise SystemExit(1)
    print(f"All parameters resolved from {WORKBOOK}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing workbook, discarding hand edits")
    ap.add_argument("--check", action="store_true",
                    help="list parameters the code asks for that the workbook lacks")
    a = ap.parse_args()
    check() if a.check else build(force=a.force)
