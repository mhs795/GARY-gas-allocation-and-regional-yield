"""
Create data/gary_parameters.xlsx, the single parameters workbook every parameter is read
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
WORKBOOK = os.path.join(DATA, "gary_parameters.xlsx")

# (Group, Parameter, Value, Unit, Source/Note)
PARAMETERS = [
    ("Horizon", "horizon_start", 2025, "year", "First modelled year"),
    ("Horizon", "horizon_end", 2051, "year",
     "Last SOLVED year, one beyond horizon_report_end. Held at 2051 because that is "
     "where the published data ends (demand and ACIL Allen prices 2051, GPG/industrial "
     "profiles 2045). Padding further does NOT buy a cleaner answer: measured 2 Sep 2026, "
     "a 2065 pad put 14 extra years of held-flat demand against the same finite reserves "
     "and drove the Surat reserve dual from $0.00 to $14.42/GJ, manufacturing scarcity "
     "rather than removing an artefact. Fix the terminal value instead -- see TODO item 15."),
    ("Horizon", "horizon_report_end", 2050, "year",
     "Last year PRESENTED. Results beyond this are solved but trimmed off before they "
     "reach the dashboard or a caller -- see solve.solve_scenario. Keep it strictly "
     "below horizon_end."),

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
    ("Network roles", "import_nodes", "Port_Kembla,Geelong,Adelaide", "nodes",
     "Regasification terminals: potential supply priced on an international price, "
     "not a field development cost. Geelong = Viva/Vopak FSRU, Adelaide = AG&P "
     "Outer Harbor (its gas reaches Victoria over SEA_Gas_Rev)"),

    ("Data centre demand", "datacentre_state_node", "NSW:Sydney,VIC:Melbourne",
     "state:node", "Where each state's data centre volume lands"),
    ("Data centre demand", "datacentre_series_path", "none", "file path",
     "Optional: a spreadsheet holding a YEAR-BY-YEAR data centre demand series "
     "(Year column + NSW/VIC columns in PJ/yr), used instead of the flat cells "
     "for the states it covers. \"none\" = the sidebar box starts empty and the "
     "lever behaves as it always has. Set it to a path an analyst keeps a live "
     "pipeline in and the box comes up pointing there. This is a DEFAULT, not a "
     "lock: the box is editable and --dc-file overrides it. See "
     "src/data/datacentre_demand_example.csv for the layout"),

    ("Cost coefficients", "capex_annualisation_rate", 0.08, "fraction",
     "Share of a built project's CapEx charged to each single-year dispatch solve. "
     "The dispatch model has no NPV to charge a lump sum against, so a build shows "
     "up as an annual carrying cost at this rate. GARY's own number, not a source: "
     "it sits above discount_rate_default because it stands in for return OF "
     "capital as well as return ON it. The capacity MIP does NOT use it -- that "
     "layer charges full CapEx once, discounted to the build year."),
    ("Cost coefficients", "storage_cycle_cost", 0.50, "A$/GJ",
     "Round-trip charge on storage, applied to injection AND withdrawal, so "
     "inventory cycles only when the seasonal price spread justifies it. GARY's "
     "own number, not a source. Read by both the dispatch model and the capacity "
     "MIP so the two layers value a store identically."),

    ("Dashboard defaults", "winter_default", "Medium", "level",
     "Winter level the dashboard opens on. MEDIUM is the central GSOO case "
     "(multiplier 1.0, AEMO's weather-averaged series). Low is an unseasonably "
     "warm winter (0.91x) and High a deliberate stress (1.5x) beyond any observed "
     "winter."),
    ("Dashboard defaults", "lng_default", "Medium", "level",
     "Global LNG price level the dashboard opens on. Under netback pricing this lever "
     "selects a PRICE path, not an export volume: Medium is the run's own GSOO "
     "scenario path, so it is the internally consistent choice."),
    ("Dashboard defaults", "mip_gap_default", 0.005, "fraction",
     "Relative MIP gap for the capacity layer. Matches solve_scenario's own default; "
     "the dashboard slider used to open at 0.01, a wider tolerance than any headless "
     "run used. That matters more since field developments carry derived CapEx in the "
     "tens of billions -- 1% of the objective is enough to hide a build decision."),

    ("Capacity model", "discount_rate_default", 0.07, "fraction",
     "NPV discount rate for the perfect-foresight capacity MIP"),
    ("Capacity model", "peak_day_weight", 5.0, "days",
     "Days represented by the annual peak representative day (adequacy)"),
    ("Capacity model", "asset_salvage", "TRUE", "TRUE/FALSE",
     "Credit a built asset with the service life the horizon cuts off, as the present "
     "value at the horizon of its remaining capital charges: (1-(1+r)^-(life-used)) / "
     "(1-(1+r)^-life) of CapEx, where life is the row's AssetLife in "
     "expansion_options.csv and a blank life earns nothing. ON because the objective "
     "already credits leftover GAS at the horizon, and writing off the steel that moves "
     "it while valuing the gas is an asymmetry rather than a conservatism -- it biases "
     "the model against long-lived and late-built infrastructure, which is the class of "
     "candidate a 2050 horizon most needs to judge fairly. Set FALSE to reproduce runs "
     "from before the credit existed."),
    ("Capacity model", "salvage_max_passes", 0, "passes",
     "Extra capacity solves spent chasing the terminal-value fixed point. The salvage "
     "credit scales leftover margin by 1/(1+r.tau) and tau must be measured on the stock "
     "left AT THE HORIZON, which is an output of the solve it feeds. Self-correcting "
     "(more salvage -> less production -> more stock left -> longer tau -> less salvage), "
     "so it settles; 0 disables the iteration and falls back to each row nameplate ratio."),
    ("Capacity model", "salvage_tau_tol", 0.25, "years",
     "Stop iterating once no row moves its reserves-to-production ratio by more than this."),
    ("Capacity model", "terminal_earliest", 2028, "year",
     "Earliest build year for an LNG import terminal"),
    ("Capacity model", "capacity_base_year", 2025, "year", "Discounting base year"),
    ("Capacity model", "allow_import_terminals", "TRUE", "boolean",
     "Whether the capacity layer may build LNG IMPORT terminals -- a Type=Terminal "
     "candidate whose Target is one of import_nodes. FALSE removes them from the "
     "candidate set, so the east coast must meet demand from domestic supply and "
     "pipe. Field developments are also Type=Terminal but target basin nodes, so "
     "they are not affected."),
    ("Capacity model", "gsoo_expansions_only", "FALSE", "boolean",
     "Default for the GSOO-only expansion filter. FALSE = the capacity layer may "
     "choose any candidate in expansion_options.csv; TRUE = only rows with "
     "Source=GSOO, i.e. those AEMO NAMES in its 2026 GSOO material (the G26 Field "
     "Developments sheet for supply, the GSOO/VGPR project set for pipelines) -- "
     "whatever status AEMO gives them, Committed or Undeveloped alike. Source "
     "records where a candidate came from, NOT whether it is committed. Market rows "
     "are ones GARY researched from public announcements or built itself. Overridden "
     "per run by the dashboard toggle or --gsoo-expansions-only"),
    ("Capacity model", "expansion_source_gsoo", "GSOO", "label",
     "Value of the Source column in expansion_options.csv that counts as "
     "AEMO-sourced"),

    # --- ACIL Allen gas pricing methodology ------------------------------------
    ("ACIL Allen LNG price", "netback_pricing_default", "TRUE", "boolean",
     "Default state of the dashboard's LNG netback pricing switch. TRUE = ACIL "
     "Allen price formation is on unless turned off, which is the methodology "
     "behind the 2026 GSOO and the only mode in which an international price "
     "disciplines domestic prices. Overridden per run by the sidebar switch or "
     "--netback-pricing"),
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

    ("LNG trains", "lng_nameplate_tj_day", 3680.0, "TJ/day",
     "Total east coast liquefaction nameplate (100% utilisation). Mirrors "
     "This is the PHYSICAL ceiling on "
     "exports under netback pricing - a train cannot liquefy more than it can "
     "liquefy, however attractive the netback."),
    ("LNG trains", "lng_train_shares", "APLNG:0.357,GLNG:0.31,QCLNG:0.333",
     "node:share", "Each train's share of nameplate. Mirrors the *_factor rows in "
     "the 2026 GSOO LNG Export sheet's Mtpa."),
    ("LNG trains", "lng_foundation_share", 0.93, "fraction",
     "Share of planned export volume committed under long-term foundation SPAs: "
     "take-or-pay, therefore price-insensitive, served whatever the netback. The "
     "remainder is the uncontracted spot tail that bids at the netback and can be "
     "outbid by a domestic buyer or taken by a reservation. DERIVED FROM PUBLIC "
     "DATA: the ACCC publishes Queensland LNG producers' uncontracted gas each "
     "quarter and reported 22 PJ available for Q1 2026 (ACCC, east coast gas "
     "supply outlook, Q1 2026); against ~325-330 PJ of quarterly exports that is "
     "~7% uncontracted, hence 0.93. Structure confirmed by ACIL Allen (14 Nov "
     "2025) 2.3.2: 'the supply under foundation customers is untouched in our "
     "modelling, LNG exporters then supply the domestic market and export further "
     "gas via spot cargoes'. ONE QUARTER's figure, so treat as a KEY SENSITIVITY - "
     "it sets how much export volume is contestable at all."),

    ("Storage", "storage_opening_fraction", 0.5, "fraction of capacity",
     "GARY's own. Inventory each year opens at this share of capacity and must "
     "close at or above it, so a year cannot create gas. Each year is solved "
     "independently, so this is an assumption either way."),
    ("Gas reservation", "reservation_respects_contracts", 1, "1=yes, 0=no",
     "Default for whether a reservation may only take UNCONTRACTED export volume. "
     "1: the reservation bites on the spot tail first and is capped at "
     "(1 - lng_foundation_share) of planned volume, so foundation SPAs are not "
     "broken - which is how the Heads of Agreement with the east coast LNG "
     "exporters actually works. 0: the reservation takes its share of ALL export "
     "volume, foundation contracts included. The dashboard toggle overrides this."),
]

# Winter and LNG demand levers, currently coded in solve.py.
SCENARIO_LEVERS = [
    ("Winter", "Low", 0.91, "", "", "UNSEASONABLY WARM WINTER -- the warmest of the seven full Bulletin Board winters (2019-2025), detrended. Multiplier on Melbourne/Adelaide/Sydney "
     "distribution demand over the winter window"),
    ("Winter", "Medium", 1.0, "", "", "THE CENTRAL GSOO CASE -- AEMO's weather-averaged series, unmodified. The default."),
    ("Winter", "High", 1.5, "", "", "STRESS CASE, deliberately beyond observed weather: the coldest Bulletin Board winter is only 1.08x detrended."),
    ("LNG", "Low", 0.04, 2026, 2030, "Annual decline step applied to LNG export "
     "demand; see get_lng_mult in solve.py for the piecewise path"),
    ("LNG", "Medium", 1.0, "", "", "Flat, no adjustment"),
    ("LNG", "High", 1.6, 2026, 2030, "Multiplier over the high window; 1.1 outside it"),
    # Under netback pricing the LNG lever stops scaling export VOLUME and instead
    # selects which of ACIL Allen's published price paths the netback is struck
    # off. Values are scenario names, or 'baseline' for the run's own GSOO
    # scenario. Using published paths rather than an invented percentage shift
    # keeps every number in the chain sourced.
    ("LNG_Netback", "Low", "Accelerated", "", "",
     "Weak global LNG demand: ACIL Allen's Accelerated Transition price path "
     "(netback $7.33/GJ in 2030, $3.94 by 2050)"),
    ("LNG_Netback", "Medium", "baseline", "", "",
     "The run's own GSOO scenario price path"),
    ("LNG_Netback", "High", "SlowerGrowth", "", "",
     "Strong global LNG demand: ACIL Allen's Slower Growth price path "
     "(netback $10.39/GJ in 2030, at the $12 Code cap from 2040)"),
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
