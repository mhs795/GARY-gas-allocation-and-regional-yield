"""
Extract AEMO 2026 GSOO demand data, for ALL modelled baseline scenarios, into
tidy intermediate CSVs.

Source workbooks (downloaded into data/other data/2026 GSOO/2026-gsoo-supply-data/):
  - 2026-gsoo-report-figures-and-data.xlsx   annual sector consumption + GPG seasonal max
  - 2026-gsoo-daily-maximum-demand-summary.xlsx  regional summer/winter peak demand

The GSOO GPG/RC&I/Industrial/LNG forecasts are built on the Draft 2026 ISP optimal
development path (gas-regionalised, calendar year, weather-averaged). The 2026 GSOO
publishes three demand scenarios -- Step Change (central), Accelerated Transition,
and Slower Growth -- which the model now exposes as selectable *baselines*. The
model's own scenario levers (Winter, LNG, gas reservation) then layer on top of whichever
baseline is chosen.

Scenario coverage in the source workbooks:
  * ResComm / Industrial / LNG annual (Figures 17/18/19): all three scenarios are
    published as separate columns (Accelerated Transition, Step Change, Slower Growth).
  * Regional summer/winter peaks (daily-max workbook): all three scenarios appear as
    separate banner blocks.
  * GPG annual (Figure 2 & 22): AEMO publishes only a SINGLE 2026 GSOO GPG trajectory
    (not split by scenario). We emit it once tagged "StepChange"; build_gpg_demand_gsoo
    then derives per-scenario GPG levels by scaling that trajectory on each scenario's
    total regional GPG peak relative to Step Change.

Outputs (data/gsoo/):
  - annual_sector.csv     Scenario, Year, Sector, PJ_per_year   (NEM/ECGM national totals)
  - regional_peak.csv     Scenario, Year, Region, Season, GPG_TJd, RCI_TJd
  - gpg_seasonal_max.csv  Year, SummerMax_TJd, WinterMax_TJd   (single 2026 GSOO GPG)
"""
import os
import pandas as pd

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
SRC = os.path.join(DATA, "other data", "2026 GSOO", "2026-gsoo-supply-data")
FIG = os.path.join(SRC, "2026-gsoo-report-figures-and-data.xlsx")
DMX = os.path.join(SRC, "2026-gsoo-daily-maximum-demand-summary.xlsx")
OUT = os.path.join(DATA, "gsoo")

REGIONS = ["NSW", "NT", "QLD", "SA", "TAS", "VIC"]

# Baseline scenarios the model exposes -> workbook label. The dict key is the slug
# used in output filenames downstream (gpg_demand_profile_<slug>.csv, etc.).
SCENARIOS = {
    "StepChange": "Step Change",
    "Accelerated": "Accelerated Transition",
    "SlowerGrowth": "Slower Growth",
}

# Column index of each scenario in the 2026-group of Figures 17/18/19.
# (header row: col3=Accelerated Transition, col4=Step Change, col5=Slower Growth.)
FIG_SECTOR_COL = {
    "Accelerated Transition": 3,
    "Step Change": 4,
    "Slower Growth": 5,
}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def extract_annual_sectors():
    """Annual national consumption (PJ/y), for all scenarios, by sector.

    Returns (annual_df[Scenario, Year, Sector, PJ_per_year], gpg_seasonal_df).
    GPG annual is single-scenario in the workbook, emitted under 'StepChange'.
    """
    rows = []

    # Figure 2 & 22 -- GPG. col1=year, col7=2026 GSOO NEM PJ/y, col8=NT PJ/y,
    # col9=summer max TJ/d, col10=winter max TJ/d. Single 2026 GSOO trajectory.
    f2 = pd.read_excel(FIG, sheet_name="Figure 2 & 22", header=None)
    seasonal = []
    for i in range(len(f2)):
        yr = _num(f2.iat[i, 1])
        if yr is None or not (2019 <= yr <= 2050):
            continue
        nem = _num(f2.iat[i, 7])
        nt = _num(f2.iat[i, 8])
        if nem is not None:
            rows.append(("StepChange", int(yr), "GPG_NEM", nem))
        if nt is not None:
            rows.append(("StepChange", int(yr), "GPG_NT", nt))
        smax, wmax = _num(f2.iat[i, 9]), _num(f2.iat[i, 10])
        if smax is not None or wmax is not None:
            seasonal.append((int(yr), smax, wmax))

    # Figures 17 (res/comm), 18 (industrial), 19 (LNG): col1=year, one column per
    # scenario. The header row differs by sheet (Figure 17 -> row 21, others -> 23).
    for sheet, hdr, sector in [("Figure 17", 21, "ResComm"),
                               ("Figure 18", 23, "Industrial"),
                               ("Figure 19", 23, "LNG")]:
        df = pd.read_excel(FIG, sheet_name=sheet, header=None)
        for canon, label in SCENARIOS.items():
            col = FIG_SECTOR_COL[label]
            assert label in str(df.iat[hdr, col]), \
                f"{sheet}: col{col} expected '{label}', got '{df.iat[hdr, col]}'"
            for i in range(hdr + 1, len(df)):
                yr = _num(df.iat[i, 1])
                val = _num(df.iat[i, col])
                if yr is not None and 2019 <= yr <= 2050 and val is not None:
                    rows.append((canon, int(yr), sector, val))

    annual = pd.DataFrame(rows, columns=["Scenario", "Year", "Sector", "PJ_per_year"])
    seasonal_df = pd.DataFrame(seasonal, columns=["Year", "SummerMax_TJd", "WinterMax_TJd"])
    return annual, seasonal_df


def extract_regional_peaks():
    """Regional summer/winter peak GPG and RC&I (TJ/d), all scenario blocks."""
    raw = pd.read_excel(DMX, sheet_name="Regional GPG Max", header=None)
    recs = []
    for canon, label in SCENARIOS.items():
        # Find this scenario's banner in col0, then the GPG/RC&I sub-header below it.
        sc_row = next((i for i in range(len(raw))
                       if str(raw.iat[i, 0]).strip() == label), None)
        if sc_row is None:
            raise ValueError(f"Regional GPG Max: scenario banner '{label}' not found")
        sub = next(i for i in range(sc_row, sc_row + 8)
                   if str(raw.iat[i, 2]).strip() == "GPG")
        for i in range(sub + 1, sub + 1 + 20):  # 2026..2045
            yr = _num(raw.iat[i, 1])
            if yr is None or not (2026 <= yr <= 2045):
                break
            # Summer block: regions at cols 2,4,6,8,10,12 (GPG); winter at 14,16,...,24
            for season, base in [("Summer", 2), ("Winter", 14)]:
                for r, reg in enumerate(REGIONS):
                    gpg = _num(raw.iat[i, base + 2 * r])
                    rci = _num(raw.iat[i, base + 2 * r + 1])
                    recs.append((canon, int(yr), reg, season, gpg, rci))
    return pd.DataFrame(recs, columns=["Scenario", "Year", "Region", "Season", "GPG_TJd", "RCI_TJd"])


def main():
    os.makedirs(OUT, exist_ok=True)
    annual, seasonal = extract_annual_sectors()
    regional = extract_regional_peaks()

    annual.to_csv(os.path.join(OUT, "annual_sector.csv"), index=False)
    seasonal.to_csv(os.path.join(OUT, "gpg_seasonal_max.csv"), index=False)
    regional.to_csv(os.path.join(OUT, "regional_peak.csv"), index=False)

    for canon in SCENARIOS:
        print(f"\n=== Annual national consumption (PJ/y), {canon} ===")
        sub = annual[annual.Scenario == canon]
        piv = sub.pivot_table(index="Year", columns="Sector", values="PJ_per_year")
        keep = [y for y in (2026, 2030, 2035, 2040, 2045) if y in piv.index]
        print(piv.loc[keep].round(1).to_string())
    print("\n=== GPG NEM+NT seasonal max (TJ/d) ===")
    print(seasonal.set_index("Year").round(0).to_string())
    print("\nWrote 3 CSVs to", OUT)


if __name__ == "__main__":
    main()
