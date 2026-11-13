import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
ASSETS = ROOT / "assets"
CURRENT = "1 July 2026"

INK, TEAL, GOLD, MUTED, GRID = "#20343a", "#247367", "#d68d38", "#65767a", "#e2e9e4"


def read_sheet(book, sheet):
    frame = pd.read_excel(book, sheet_name=sheet, header=2, engine="odf")
    frame.columns = [str(column).split("\n")[0] for column in frame.columns]
    return frame


def build_data():
    authority_book = RAW / "evci0102_2026-07.ods"
    counts = read_sheet(authority_book, "EVCI0102a")
    rapid_counts = read_sheet(authority_book, "EVCI0102b")
    rates = read_sheet(authority_book, "EVCI0102c")
    rapid_rates = read_sheet(authority_book, "EVCI0102d")
    data = counts[["Local authority code", "Local authority", "1 January 2026", "1 April 2026", CURRENT]].copy()
    data.columns = ["code", "name", "count_jan", "count_apr", "count_jul"]
    data = data.merge(rapid_counts[["Local authority code", CURRENT]].rename(columns={"Local authority code":"code", CURRENT:"rapid_count_jul"}), on="code", validate="one_to_one")
    for source, prefix in [(rates, "rate"), (rapid_rates, "rapid_rate")]:
        temp = source[["Local authority code", "1 January 2026", "1 April 2026", CURRENT]].copy()
        temp.columns = ["code", f"{prefix}_jan", f"{prefix}_apr", f"{prefix}_jul"]
        data = data.merge(temp, on="code", validate="one_to_one")
    if len(data) != 361 or data["code"].nunique() != 361:
        raise ValueError(f"Expected 361 unique local authorities, found {len(data)} rows / {data['code'].nunique()} codes")
    data["country"] = data.code.str[0].map({"E":"England", "W":"Wales", "S":"Scotland", "N":"Northern Ireland"})
    if data.country.isna().any():
        raise ValueError("Some authority codes could not be mapped to a UK nation.")
    if int(data.count_jul.sum()) != 121171 or int(data.rapid_count_jul.sum()) != 28887:
        raise ValueError("Authority totals do not match the DfT national headline figures; review the source tables.")
    for col in data.columns:
        if col.endswith("count_jul") or "_count_" in col:
            data[col] = pd.to_numeric(data[col], errors="coerce")
        if "rate_" in col:
            data[col] = pd.to_numeric(data[col], errors="coerce")
    data["rapid_share_pct"] = data.rapid_count_jul / data.count_jul * 100

    region_book = RAW / "evci0101_2026-07.ods"
    region = read_sheet(region_book, "EVCI0101c")
    rapid_region = read_sheet(region_book, "EVCI0101d")
    date_col = region.columns[0]
    date_values = pd.to_datetime(region[date_col], errors="coerce")
    region = region.loc[date_values.notna()].copy()
    rapid_region = rapid_region.loc[pd.to_datetime(rapid_region.iloc[:, 0], errors="coerce").notna()].copy()
    region.insert(0, "date", pd.to_datetime(region.iloc[:, 0]).dt.strftime("%Y-%m-%d"))
    regions = []
    for _, row in region.iterrows():
        date = row["date"]
        rapid_row = rapid_region.loc[pd.to_datetime(rapid_region.iloc[:, 0]).dt.strftime("%Y-%m-%d") == date].iloc[0]
        for name in region.columns[3:]:
            if name in {"Great Britain"}:
                continue
            regions.append({"date": date, "area": name, "total_per_100k": float(row[name]), "rapid_per_100k": float(rapid_row[name])})
    region_frame = pd.DataFrame(regions)
    region_frame = region_frame.loc[region_frame.date >= "2026-01-01"].copy()

    power_book = RAW / "evci0201_2026-07.ods"
    power = read_sheet(power_book, "EVCI0201a")
    power = power.loc[pd.to_datetime(power.iloc[:, 0], errors="coerce").notna()].copy()
    power_row = power.iloc[-1]
    power_values = {str(key): int(power_row[key]) for key in ["Standard", "Standard plus", "Rapid", "Ultra-rapid"]}

    areas = []
    for _, row in data.iterrows():
        areas.append({
            "code": row.code,
            "name": row["name"],
            "country": row.country,
            "counts": {"2026-01-01": int(row.count_jan), "2026-04-01": int(row.count_apr), "2026-07-01": int(row.count_jul), "rapid_2026-07-01": int(row.rapid_count_jul)},
            "rates": {"2026-01-01": float(row.rate_jan), "2026-04-01": float(row.rate_apr), "2026-07-01": float(row.rate_jul)},
            "rapid_rates": {"2026-01-01": float(row.rapid_rate_jan), "2026-04-01": float(row.rapid_rate_apr), "2026-07-01": float(row.rapid_rate_jul)},
        })
    payload = {"as_of": "2026-07-01", "areas": areas, "regions": region_frame.to_dict(orient="records"), "power_bands": power_values}
    (DERIVED / "charger_data.json").write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    data.to_csv(DERIVED / "local_authority_chargers.csv", index=False)
    return data, region_frame, power_values


def plot_style(ax):
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="both", colors=MUTED, labelsize=9, length=0, pad=7)
    ax.grid(axis="x", color=GRID, linewidth=.8)
    ax.set_axisbelow(True)


def charts(data, regions, power):
    fig, ax = plt.subplots(figsize=(10, 5.2), layout="constrained")
    chosen = ["United Kingdom", "Northern Ireland", "North West", "Yorkshire and The Humber", "East Midlands", "North East", "East of England", "South West", "West Midlands", "South East", "Wales", "Scotland", "London"]
    latest = regions.loc[regions.date == "2026-07-01"].set_index("area").reindex(chosen).dropna().sort_values("total_per_100k")
    colors = [GOLD if area == "London" else TEAL if area in {"United Kingdom", "Wales", "Scotland", "Northern Ireland"} else "#8fb6a8" for area in latest.index]
    ax.barh(latest.index, latest.total_per_100k, color=colors, height=.68)
    ax.axvline(174.9, color="#7f8e89", lw=1.3, ls=(0,(4,4)))
    ax.text(178, -.7, "UK average 174.9", color=MUTED, fontsize=8)
    ax.set_xlabel("Public EV chargers per 100,000 residents", color=MUTED, fontsize=9)
    plot_style(ax)
    fig.savefig(ASSETS / "regional-coverage.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.6, 4.6), layout="constrained")
    labels = list(power.keys())
    values = list(power.values())
    colors = ["#9bbeb1", "#4d8b7d", "#e5b96d", "#be664d"]
    bars = ax.bar(labels, values, color=colors, width=.62)
    ax.bar_label(bars, labels=[f"{v:,}" for v in values], padding=4, fontsize=9, color=INK)
    ax.set_ylim(0, max(values) * 1.2)
    ax.set_ylabel("Public chargers", color=MUTED, fontsize=9)
    ax.tick_params(axis="x", labelsize=9, colors=INK)
    ax.yaxis.grid(True, color=GRID, linewidth=.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left", "bottom"]].set_visible(False)
    fig.savefig(ASSETS / "power-bands.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.8, 4.7), layout="constrained")
    dates = sorted(regions.date.unique())
    uk = regions.loc[regions.area == "United Kingdom"].sort_values("date")
    ax.plot(pd.to_datetime(uk.date), uk.total_per_100k, color=TEAL, lw=3, marker="o", ms=4, label="All public chargers")
    ax.plot(pd.to_datetime(uk.date), uk.rapid_per_100k, color=GOLD, lw=2.5, marker="o", ms=4, label="Rapid or ultra-rapid (50kW+)")
    ax.set_ylabel("Chargers per 100,000 residents", color=MUTED, fontsize=9)
    ax.tick_params(axis="both", colors=MUTED, labelsize=8, length=0, pad=7)
    ax.yaxis.grid(True, color=GRID, linewidth=.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left", "bottom"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left", ncol=2, fontsize=8)
    fig.savefig(ASSETS / "uk-network-trend.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def normalize_svgs():
    for path in ASSETS.glob("*.svg"):
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(line.rstrip() for line in lines) + "\n", encoding="utf-8")

def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    DERIVED.mkdir(parents=True, exist_ok=True)
    data, regions, power = build_data()
    charts(data, regions, power)
    normalize_svgs()
    latest = regions.loc[(regions.area == "United Kingdom") & (regions.date == "2026-07-01")].iloc[0]
    high, low = data.loc[data.rate_jul.idxmax()], data.loc[data.rate_jul.idxmin()]
    print(f"Verified {len(data)} local authorities; July totals {int(data.count_jul.sum()):,} EV chargers, including {int(data.rapid_count_jul.sum()):,} rated 50kW+.")
    print(f"UK coverage: {latest.total_per_100k:.1f} per 100k; {latest.rapid_per_100k:.1f} rapid or above per 100k.")
    print(f"Highest local rate: {high['name']} ({high.rate_jul:.1f}/100k); lowest: {low['name']} ({low.rate_jul:.1f}/100k).")
    print(f"January-July: {int((data.count_jul > data.count_jan).sum())} areas had a higher recorded count; note DfT's June data-feed correction.")


if __name__ == "__main__":
    main()