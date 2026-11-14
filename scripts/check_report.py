"""Check that the EV charger report agrees with its derived data."""
import csv
import json
import math
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DATES = ["2026-01-01", "2026-04-01", "2026-07-01"]
COUNTRIES = {"E": "England", "W": "Wales", "S": "Scotland", "N": "Northern Ireland"}


def require(condition, message: str):
    if not condition:
        raise AssertionError(message)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def close(left, right):
    return math.isclose(float(left), float(right), rel_tol=0, abs_tol=1e-9)


def main():
    report_path = ROOT / "index.qmd"
    report = report_path.read_text(encoding="utf-8")
    derived = ROOT / "data" / "derived"
    payload = json.loads((derived / "charger_data.json").read_text(encoding="utf-8"))
    areas = payload.get("areas", [])
    rows = read_csv(derived / "local_authority_chargers.csv")

    require(payload.get("as_of") == DATES[-1], "Explorer JSON has an unexpected latest data date.")
    require(len(areas) == 361 and len(rows) == 361, "Expected 361 local authorities in both derived files.")
    area_codes = [area.get("code") for area in areas]
    csv_codes = [row.get("code") for row in rows]
    require(len(set(area_codes)) == 361, "Explorer JSON has missing or duplicate authority codes.")
    require(len(set(csv_codes)) == 361 and set(csv_codes) == set(area_codes),
            "Local-authority CSV codes do not match the explorer JSON.")

    csv_by_code = {row["code"]: row for row in rows}
    for area in areas:
        code = area["code"]
        row = csv_by_code[code]
        require(area.get("country") == COUNTRIES.get(code[:1]), f"Nation mapping is missing or incorrect for {code}.")
        require(set(area.get("counts", {})) == set(DATES) | {"rapid_2026-07-01"},
                f"Unexpected count dates for {code}.")
        require(set(area.get("rates", {})) == set(DATES), f"Unexpected rate dates for {code}.")
        require(set(area.get("rapid_rates", {})) == set(DATES), f"Unexpected rapid-rate dates for {code}.")
        csv_counts = {
            "2026-01-01": int(row["count_jan"]),
            "2026-04-01": int(row["count_apr"]),
            "2026-07-01": int(row["count_jul"]),
            "rapid_2026-07-01": int(row["rapid_count_jul"]),
        }
        csv_rates = {
            "2026-01-01": float(row["rate_jan"]),
            "2026-04-01": float(row["rate_apr"]),
            "2026-07-01": float(row["rate_jul"]),
        }
        csv_rapid_rates = {
            "2026-01-01": float(row["rapid_rate_jan"]),
            "2026-04-01": float(row["rapid_rate_apr"]),
            "2026-07-01": float(row["rapid_rate_jul"]),
        }
        require(area.get("name") == row["name"] and area.get("country") == row["country"],
                f"Explorer labels differ from the local-authority CSV for {code}.")
        require(area["counts"] == csv_counts, f"Explorer counts differ from the local-authority CSV for {code}.")
        require(all(close(area["rates"][date], csv_rates[date]) for date in DATES),
                f"Explorer rates differ from the local-authority CSV for {code}.")
        require(all(close(area["rapid_rates"][date], csv_rapid_rates[date]) for date in DATES),
                f"Explorer rapid rates differ from the local-authority CSV for {code}.")

    total = sum(int(row["count_jul"]) for row in rows)
    rapid_total = sum(int(row["rapid_count_jul"]) for row in rows)
    require(total == 121171 and total == sum(area["counts"]["2026-07-01"] for area in areas),
            "The 121,171 July charger headline no longer matches the authority data.")
    require(rapid_total == 28887 and rapid_total == sum(area["counts"]["rapid_2026-07-01"] for area in areas),
            "The 28,887 rapid-or-above headline no longer matches the authority data.")

    latest_regions = {
        item["area"]: item
        for item in payload.get("regions", [])
        if item.get("date") == DATES[-1]
    }
    require(len([item for item in payload.get("regions", []) if item.get("date") == DATES[-1]]) == 14,
            "Expected the UK, four nations and nine English regions for July 2026.")
    uk = latest_regions.get("United Kingdom")
    london = latest_regions.get("London")
    scotland = latest_regions.get("Scotland")
    require(uk is not None and round(uk["total_per_100k"], 1) == 174.9
            and round(uk["rapid_per_100k"], 1) == 41.7,
            "UK per-capita headline rates no longer match the July regional data.")
    require(london is not None and round(london["total_per_100k"], 1) == 345.1
            and round(london["rapid_per_100k"], 1) == 27.9,
            "London regional rates no longer match the report claims.")
    require(scotland is not None and round(scotland["total_per_100k"], 1) == 231.6
            and round(scotland["rapid_per_100k"], 1) == 59.1,
            "Scotland regional rates no longer match the report claims.")

    power = payload.get("power_bands", {})
    expected_power = {"Standard": 59587, "Standard plus": 32697, "Rapid": 14891, "Ultra-rapid": 13996}
    require(power == expected_power, "Power-band counts no longer match the report claims.")
    highest = max(rows, key=lambda row: float(row["rate_jul"]))
    lowest = min(rows, key=lambda row: float(row["rate_jul"]))
    require(highest["name"] == "Hammersmith and Fulham" and lowest["name"] == "North East Derbyshire",
            "The highest or lowest local-authority rate no longer matches the report.")

    required_claims = [
        f"**{total:,} public electric-vehicle chargers**",
        f"**{round(uk['total_per_100k'], 1):.1f} per 100,000 residents**",
        f"{rapid_total:,}",
        f"**{round(london['total_per_100k'], 1):.1f} chargers per 100,000 people**",
        f"**{round(london['rapid_per_100k'], 1):.1f} per 100,000**",
        f"{round(scotland['total_per_100k'], 1):.1f} per 100,000",
        f"{round(scotland['rapid_per_100k'], 1):.1f} per 100,000",
        f"{expected_power['Standard']:,} standard chargers, {expected_power['Standard plus']:,} standard plus, "
        f"{expected_power['Rapid']:,} rapid, and {expected_power['Ultra-rapid']:,} ultra-rapid chargers",
        highest["name"],
        lowest["name"],
        "361 local authorities",
        "the three quarters in the new series",
    ]
    for claim in required_claims:
        require(claim in report, f"Report claim is missing or no longer matches derived data: {claim}")

    for asset in [
        "assets/regional-coverage.svg",
        "assets/power-bands.svg",
        "assets/uk-network-trend.svg",
        "assets/charger-explorer.js",
    ]:
        require((ROOT / asset).is_file(), f"Report asset is missing: {asset}")

    links = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", report)
    for link in links:
        url = urlsplit(link)
        if url.scheme or url.netloc or not url.path:
            continue
        target = ROOT / unquote(url.path)
        require(target.is_file(), f"Report link target does not exist: {link}")

    print("Report QA passed: charger totals, regional rates, power bands, authority schema, and report assets/links are consistent.")


if __name__ == "__main__":
    main()