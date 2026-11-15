import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MatplotlibPath
from matplotlib.ticker import FuncFormatter
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
ASSETS = ROOT / "assets"
MEDIUM_IMAGES = ROOT / "medium-edition" / "images"
BOUNDARIES = ROOT / "data" / "geo" / "local_authorities.geojson"
CURRENT = "1 July 2026"
MAP_CHART = "charger-coverage-map"
MEDIUM_CHARTS = ("regional-coverage", "power-bands", "uk-network-trend", MAP_CHART)
CHARGER_RATE_BANDS = (
    ("Under 100", 0, 100, "#b8d7c6"),
    ("100 to under 150", 100, 150, "#4d8b7d"),
    ("150 to under 200", 150, 200, "#e5b96d"),
    ("200 or more", 200, float("inf"), "#be664d"),
)

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


def save_chart(fig, stem: str, medium: bool = False):
    fig.savefig(ASSETS / f"{stem}.svg", bbox_inches="tight", facecolor="white")
    if medium:
        MEDIUM_IMAGES.mkdir(parents=True, exist_ok=True)
        fig.savefig(MEDIUM_IMAGES / f"{stem}.png", dpi=200, bbox_inches="tight", facecolor="white")


def polygon_path(rings):
    vertices = []
    codes = []
    for ring in rings:
        points = [tuple(point[:2]) for point in ring]
        if len(points) < 4:
            continue
        vertices.extend(points)
        codes.extend([MatplotlibPath.MOVETO] + [MatplotlibPath.LINETO] * (len(points) - 2) + [MatplotlibPath.CLOSEPOLY])
    return MatplotlibPath(vertices, codes) if vertices else None


def geometry_paths(geometry):
    if geometry["type"] == "Polygon":
        path = polygon_path(geometry["coordinates"])
        return [path] if path else []
    if geometry["type"] == "MultiPolygon":
        return [path for polygon in geometry["coordinates"] if (path := polygon_path(polygon))]
    raise ValueError(f"Unsupported boundary geometry: {geometry['type']}")


def build_map(data, medium=False):
    payload = json.loads(BOUNDARIES.read_text(encoding="utf-8"))
    boundaries = payload["features"]
    by_code = {str(row.code): row.rate_jul for row in data.itertuples()}
    boundary_codes = {feature["properties"]["code"] for feature in boundaries}
    missing_codes = set(by_code) - boundary_codes
    if missing_codes:
        raise ValueError(f"No boundary geometry for local-authority codes: {sorted(missing_codes)}")
    extra_codes = boundary_codes - set(by_code)
    if extra_codes:
        raise ValueError(f"Boundary data has no charger rates for codes: {sorted(extra_codes)}")

    fig, ax = plt.subplots(figsize=(7.4, 12.2), layout="constrained")
    london_ax = ax.inset_axes([.66, .64, .30, .24])
    london_ax.set_facecolor("white")
    band_counts = [0] * len(CHARGER_RATE_BANDS)
    x_values, y_values = [], []
    london_features = []
    for feature in boundaries:
        code = feature["properties"]["code"]
        value = by_code[code]
        band_index = next(
            index for index, (_, lower, upper, _) in enumerate(CHARGER_RATE_BANDS)
            if lower <= value < upper
        )
        color = CHARGER_RATE_BANDS[band_index][3]
        band_counts[band_index] += 1
        for path in geometry_paths(feature["geometry"]):
            ax.add_patch(PathPatch(path, facecolor=color, edgecolor="white", linewidth=.35, zorder=2))
            x_values.extend(path.vertices[:, 0])
            y_values.extend(path.vertices[:, 1])
            if code.startswith("E090000"):
                london_features.append((path, color))

    x_margin = (max(x_values) - min(x_values)) * .035
    y_margin = (max(y_values) - min(y_values)) * .025
    ax.set_xlim(min(x_values) - x_margin, max(x_values) + x_margin)
    ax.set_ylim(min(y_values) - y_margin, max(y_values) + y_margin)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Public EV chargers per 100,000 residents, 1 July 2026", loc="left", color=INK, fontsize=14, fontweight="bold", pad=15)
    for path, color in london_features:
        london_ax.add_patch(PathPatch(path, facecolor=color, edgecolor="white", linewidth=.65))
    london_ax.set_xlim(500_000, 570_000)
    london_ax.set_ylim(150_000, 200_000)
    london_ax.set_aspect("equal")
    london_ax.set_title("London", color=INK, fontsize=9, pad=4)
    london_ax.set_xticks([])
    london_ax.set_yticks([])
    for spine in london_ax.spines.values():
        spine.set_color("#9caaa4")
    legend = [
        Patch(facecolor=color, edgecolor="white", label=f"{label} (n={count})")
        for (label, _, _, color), count in zip(CHARGER_RATE_BANDS, band_counts)
    ]
    ax.legend(handles=legend, frameon=False, loc="lower center", bbox_to_anchor=(.5, -.015), ncol=2, fontsize=9)
    save_chart(fig, MAP_CHART, medium)
    plt.close(fig)
    return band_counts


def charts(data, regions, power, medium=False):
    fig, (ax_total, ax_rapid) = plt.subplots(ncols=2, figsize=(12, 6.3), sharey=True, layout="constrained", gridspec_kw={"width_ratios": [1.12, 1]})
    chosen = ["United Kingdom", "Northern Ireland", "North West", "Yorkshire and The Humber", "East Midlands", "North East", "East of England", "South West", "West Midlands", "South East", "Wales", "Scotland", "London"]
    latest = regions.loc[regions.date == "2026-07-01"].set_index("area").reindex(chosen).dropna().sort_values("total_per_100k")
    colors = [GOLD if area == "London" else TEAL if area in {"United Kingdom", "Wales", "Scotland", "Northern Ireland"} else "#8fb6a8" for area in latest.index]
    y = range(len(latest))
    total_bars = ax_total.barh(y, latest.total_per_100k, color=colors, height=.68)
    rapid_bars = ax_rapid.barh(y, latest.rapid_per_100k, color=colors, height=.68)
    ax_total.set_yticks(list(y), labels=latest.index)
    ax_rapid.tick_params(axis="y", left=False, labelleft=False)
    ax_total.set_xlim(0, 380)
    ax_rapid.set_xlim(0, 72)
    ax_total.axvline(174.9, color="#7f8e89", lw=1.2, ls=(0,(4,4)))
    ax_rapid.axvline(41.7, color="#7f8e89", lw=1.2, ls=(0,(4,4)))
    ax_total.text(180, -.75, "UK 174.9", color=MUTED, fontsize=8)
    ax_rapid.text(43, -.75, "UK 41.7", color=MUTED, fontsize=8)
    ax_total.set_xlabel("All chargers per 100,000 residents", color=MUTED, fontsize=9)
    ax_rapid.set_xlabel("Rapid or above per 100,000", color=MUTED, fontsize=9)
    ax_total.tick_params(axis="y", colors=INK, labelsize=8, length=0, pad=6)
    for ax in (ax_total, ax_rapid):
        ax.spines[["top", "right", "left", "bottom"]].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(axis="x", colors=MUTED, labelsize=8, length=0, pad=6)
        ax.grid(axis="x", color=GRID, linewidth=.8)
        ax.set_axisbelow(True)
    save_chart(fig, MEDIUM_CHARTS[0], medium)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(9.6, 4.6), layout="constrained")
    labels = list(power.keys())
    values = list(power.values())
    colors = ["#9bbeb1", "#4d8b7d", "#e5b96d", "#be664d"]
    bars = ax.bar(labels, values, color=colors, width=.62)
    ax.bar_label(bars, labels=[f"{v / 1000:.1f}K" for v in values], padding=4, fontsize=9, color=INK)
    ax.set_ylim(0, max(values) * 1.2)
    ax.set_ylabel("Public chargers", color=MUTED, fontsize=9)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: "0" if value == 0 else f"{value / 1000:.0f}K"))
    ax.tick_params(axis="x", labelsize=9, colors=INK)
    ax.yaxis.grid(True, color=GRID, linewidth=.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left", "bottom"]].set_visible(False)
    save_chart(fig, MEDIUM_CHARTS[1], medium)
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
    save_chart(fig, MEDIUM_CHARTS[2], medium)
    plt.close(fig)
    return build_map(data, medium)


def normalize_svgs():
    for path in ASSETS.glob("*.svg"):
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(line.rstrip() for line in lines) + "\n", encoding="utf-8")

def main(argv=None):
    parser = argparse.ArgumentParser(description="Build EV charger report assets and derived data.")
    parser.add_argument("--medium", action="store_true", help="also export static PNGs for the Medium edition")
    args = parser.parse_args(argv)
    ASSETS.mkdir(parents=True, exist_ok=True)
    DERIVED.mkdir(parents=True, exist_ok=True)
    data, regions, power = build_data()
    band_counts = charts(data, regions, power, medium=args.medium)
    normalize_svgs()
    latest = regions.loc[(regions.area == "United Kingdom") & (regions.date == "2026-07-01")].iloc[0]
    high, low = data.loc[data.rate_jul.idxmax()], data.loc[data.rate_jul.idxmin()]
    print(f"Verified {len(data)} local authorities; July totals {int(data.count_jul.sum()):,} EV chargers, including {int(data.rapid_count_jul.sum()):,} rated 50kW+.")
    print(f"UK coverage: {latest.total_per_100k:.1f} per 100k; {latest.rapid_per_100k:.1f} rapid or above per 100k.")
    print(f"Highest local rate: {high['name']} ({high.rate_jul:.1f}/100k); lowest: {low['name']} ({low.rate_jul:.1f}/100k).")
    print(f"January-July: {int((data.count_jul > data.count_jan).sum())} areas had a higher recorded count; note DfT's June data-feed correction.")
    print("July charger-rate bands per 100,000: " + "; ".join(
        f"{label}: {count}" for (label, _, _, _), count in zip(CHARGER_RATE_BANDS, band_counts)
    ))


if __name__ == "__main__":
    main()