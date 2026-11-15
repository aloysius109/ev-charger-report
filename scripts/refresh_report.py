"""Read and summarize an EVCI release without touching the published report."""
import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urljoin, urlsplit

import pandas as pd

try:
    from . import check_sources
except ImportError:
    import check_sources

ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT / "sources" / "watch.json"
COUNTRIES = {"E": "England", "W": "Wales", "S": "Scotland", "N": "Northern Ireland"}
AUTHORITY_SHEETS = ("EVCI0102a", "EVCI0102b", "EVCI0102c", "EVCI0102d")
REGION_SHEETS = ("EVCI0101c", "EVCI0101d")
POWER_SHEET = "EVCI0201a"
POWER_BANDS = ("Standard", "Standard plus", "Rapid", "Ultra-rapid")
RATE_BANDS = ((0, 100), (100, 150), (150, 200), (200, math.inf))
FIGURE_LABELS = {
    "total_chargers": ("UK public electric-vehicle chargers", 0),
    "rapid_chargers": ("Rapid or ultra-rapid chargers", 0),
    "uk_total_per_100k": ("UK chargers per 100,000 residents", 1),
    "uk_rapid_per_100k": ("UK rapid-or-above chargers per 100,000 residents", 1),
    "london_total_per_100k": ("London chargers per 100,000 people", 1),
    "london_rapid_per_100k": ("London rapid-or-above chargers per 100,000 people", 1),
    "scotland_total_per_100k": ("Scotland chargers per 100,000 people", 1),
    "scotland_rapid_per_100k": ("Scotland rapid-or-above chargers per 100,000 people", 1),
    "power_standard": ("Standard chargers", 0),
    "power_standard_plus": ("Standard-plus chargers", 0),
    "power_rapid": ("Rapid chargers by power band", 0),
    "power_ultra_rapid": ("Ultra-rapid chargers by power band", 0),
    "highest_authority": ("Authority with highest charger rate", 0),
    "lowest_authority": ("Authority with lowest charger rate", 0),
    "rate_under_100": ("Authorities under 100 chargers per 100,000", 0),
    "rate_100_to_150": ("Authorities at 100 to under 150 per 100,000", 0),
    "rate_150_to_200": ("Authorities at 150 to under 200 per 100,000", 0),
    "rate_200_or_more": ("Authorities at 200 or more per 100,000", 0),
    "authority_count": ("Local authorities", 0),
    "quarter_count": ("Quarters in the series", 0),
}


@dataclass(frozen=True)
class Figure:
    key: str
    label: str
    precision: int
    accepted: int | float | str
    candidate: int | float | str

    @property
    def changed(self) -> bool:
        return self.accepted != self.candidate


class SourceUnavailable(RuntimeError):
    def __init__(self, message, downloads=None):
        super().__init__(message)
        self.downloads = downloads or []


class UnrecognisedRelease(ValueError):
    pass


@dataclass(frozen=True)
class Release:
    tag: str
    dates: list[str]
    authorities: pd.DataFrame
    regions: pd.DataFrame
    power_bands: dict[str, int]


def _normalize_columns(frame):
    frame = frame.copy()
    frame.columns = [str(column).split("\n", 1)[0].strip() for column in frame.columns]
    return frame


def _date_value(value):
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _date_columns(frame):
    found = {}
    for column in frame.columns:
        parsed = _date_value(column)
        if parsed:
            if parsed in found:
                raise ValueError(f"Duplicate quarter column for {parsed}.")
            found[parsed] = column
    if not found:
        raise ValueError("No date-bearing quarter columns were found in the authority table.")
    return dict(sorted(found.items()))


def _read_book(path, required_sheets):
    if not path.is_file():
        raise ValueError(f"Required EVCI workbook is missing: {path.name}")
    with pd.ExcelFile(path, engine="odf") as workbook:
        missing = sorted(set(required_sheets) - set(workbook.sheet_names))
        if missing:
            raise ValueError(f"{path.name} is missing required sheet(s): {', '.join(missing)}")
        return {
            sheet: _normalize_columns(pd.read_excel(workbook, sheet_name=sheet, header=2))
            for sheet in required_sheets
        }


def _numeric(frame, column, label, integer=False):
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any() or not all(math.isfinite(float(value)) for value in values):
        raise ValueError(f"{label} contains missing or non-numeric values in {column}.")
    if integer and (values % 1 != 0).any():
        raise ValueError(f"{label} contains non-integer counts in {column}.")
    return values.astype("int64") if integer else values.astype("float64")


def _authority_table(frames, dates):
    base = frames[0]
    required = {"Local authority code", "Local authority"}
    for sheet, frame in zip(AUTHORITY_SHEETS, frames):
        if not required.issubset(frame.columns):
            raise ValueError(f"{sheet} is missing the local-authority code or name column.")
        if _date_columns(frame) != dates:
            raise ValueError(f"{sheet} has a different set of quarter columns.")

    codes = base["Local authority code"].astype("string").str.strip()
    names = base["Local authority"].astype("string").str.strip()
    if codes.isna().any() or names.isna().any() or codes.duplicated().any():
        raise ValueError("EVCI0102a has missing or duplicate local-authority identifiers.")
    authority = pd.DataFrame({"code": codes, "name": names})

    for sheet, prefix, frame in zip(AUTHORITY_SHEETS, ("count", "rapid_count", "rate", "rapid_rate"), frames):
        source_codes = frame["Local authority code"].astype("string").str.strip()
        if source_codes.isna().any() or source_codes.duplicated().any():
            raise ValueError(f"{sheet} has missing or duplicate local-authority codes.")
        if set(source_codes) != set(codes):
            raise ValueError(f"{sheet} does not contain the same authority set as EVCI0102a.")
        source_names = frame.set_index(source_codes)["Local authority"].astype("string").str.strip()
        expected_names = pd.Series(names.to_list(), index=codes.to_list(), dtype="string")
        if source_names.reindex(expected_names.index).to_list() != expected_names.to_list():
            raise ValueError(f"{sheet} local-authority names do not align with EVCI0102a.")

        indexed = frame.set_index(source_codes)
        for current_date, column in dates.items():
            values = _numeric(
                indexed.reindex(codes.to_list()), column, sheet,
                integer=prefix in {"count", "rapid_count"},
            )
            authority[f"{prefix}_{current_date}"] = values.to_list()

    authority["country"] = authority["code"].str[0].map(COUNTRIES)
    if authority["country"].isna().any():
        unknown = sorted(authority.loc[authority["country"].isna(), "code"].unique())
        raise ValueError(f"Unrecognized local-authority nation prefix for codes: {unknown}")
    return authority


def _dated_rows(frame, label):
    if "Date" not in frame.columns:
        raise ValueError(f"{label} is missing its Date column.")
    parsed = pd.to_datetime(frame["Date"], errors="coerce", dayfirst=True)
    result = frame.loc[parsed.notna()].copy()
    result["_date"] = parsed.loc[parsed.notna()].dt.strftime("%Y-%m-%d")
    if result["_date"].duplicated().any():
        raise ValueError(f"{label} contains duplicate date rows.")
    return result.set_index("_date")


def _region_table(total_frame, rapid_frame, dates):
    total = _dated_rows(total_frame, REGION_SHEETS[0])
    rapid = _dated_rows(rapid_frame, REGION_SHEETS[1])
    missing_total = sorted(set(dates) - set(total.index))
    missing_rapid = sorted(set(dates) - set(rapid.index))
    if missing_total or missing_rapid:
        raise ValueError(
            "Regional tables do not contain every authority-table quarter "
            f"(total missing {missing_total}; rapid missing {missing_rapid})."
        )
    areas = [column for column in total.columns if column not in {"Date", "Notes", "_date", "Great Britain"}]
    rapid_areas = [column for column in rapid.columns if column not in {"Date", "Notes", "_date", "Great Britain"}]
    if not areas or set(areas) != set(rapid_areas):
        raise ValueError("The total and rapid regional tables have different area columns.")

    records = []
    for current_date in dates:
        for area in areas:
            total_value = pd.to_numeric(pd.Series([total.at[current_date, area]]), errors="coerce").iloc[0]
            rapid_value = pd.to_numeric(pd.Series([rapid.at[current_date, area]]), errors="coerce").iloc[0]
            if pd.isna(total_value) or pd.isna(rapid_value):
                raise ValueError(f"Regional tables have a missing rate for {area} on {current_date}.")
            records.append({
                "date": current_date,
                "area": area,
                "total_per_100k": float(total_value),
                "rapid_per_100k": float(rapid_value),
            })
    return pd.DataFrame.from_records(records)


def _power_bands(frame, dates):
    dated = _dated_rows(frame, POWER_SHEET)
    if not set(dates).issubset(dated.index):
        raise ValueError("The power-band table does not contain every authority-table quarter.")
    if not set(POWER_BANDS).issubset(dated.columns):
        raise ValueError("The power-band table is missing one or more expected charger categories.")
    latest = dates[-1]
    row = dated.loc[latest]
    result = {}
    for band in POWER_BANDS:
        value = pd.to_numeric(pd.Series([row[band]]), errors="coerce").iloc[0]
        if pd.isna(value) or not math.isfinite(float(value)) or float(value) % 1:
            raise ValueError(f"The power-band table has an invalid count for {band}.")
        result[band] = int(value)
    return result


def read_release(workspace: Path, tag: str) -> Release:
    """Read three EVCI books, discovering quarter dates from their headers."""
    if not tag or Path(tag).name != tag:
        raise ValueError(f"Invalid EVCI release tag: {tag!r}")
    workspace = Path(workspace)
    authority_books = _read_book(workspace / f"evci0102_{tag}.ods", AUTHORITY_SHEETS)
    region_books = _read_book(workspace / f"evci0101_{tag}.ods", REGION_SHEETS)
    power_book = _read_book(workspace / f"evci0201_{tag}.ods", (POWER_SHEET,))
    dates = _date_columns(authority_books[AUTHORITY_SHEETS[0]])
    authorities = _authority_table([authority_books[sheet] for sheet in AUTHORITY_SHEETS], dates)
    regions = _region_table(
        region_books[REGION_SHEETS[0]], region_books[REGION_SHEETS[1]], list(dates)
    )
    power_bands = _power_bands(power_book[POWER_SHEET], list(dates))
    return Release(tag, list(dates), authorities, regions, power_bands)


def _claim_figures(authorities, regions, power_bands, dates):
    latest = dates[-1]
    count_column = f"count_{latest}"
    rapid_count_column = f"rapid_count_{latest}"
    rate_column = f"rate_{latest}"
    if not {count_column, rapid_count_column, rate_column}.issubset(authorities.columns):
        raise ValueError(f"Authority data is missing columns for the latest quarter {latest}.")
    latest_regions = regions.loc[regions["date"] == latest].set_index("area")
    for area in ("United Kingdom", "London", "Scotland"):
        if area not in latest_regions.index:
            raise ValueError(f"Regional data is missing {area} for {latest}.")
    highest = authorities.loc[authorities[rate_column].idxmax()]
    lowest = authorities.loc[authorities[rate_column].idxmin()]
    rate_values = authorities[rate_column].astype(float)
    band_counts = [int(((rate_values >= lower) & (rate_values < upper)).sum()) for lower, upper in RATE_BANDS]
    if sum(band_counts) != len(authorities):
        raise ValueError("Charger-rate bands do not account for every local authority.")

    return {
        "total_chargers": int(authorities[count_column].sum()),
        "rapid_chargers": int(authorities[rapid_count_column].sum()),
        "uk_total_per_100k": round(float(latest_regions.at["United Kingdom", "total_per_100k"]), 1),
        "uk_rapid_per_100k": round(float(latest_regions.at["United Kingdom", "rapid_per_100k"]), 1),
        "london_total_per_100k": round(float(latest_regions.at["London", "total_per_100k"]), 1),
        "london_rapid_per_100k": round(float(latest_regions.at["London", "rapid_per_100k"]), 1),
        "scotland_total_per_100k": round(float(latest_regions.at["Scotland", "total_per_100k"]), 1),
        "scotland_rapid_per_100k": round(float(latest_regions.at["Scotland", "rapid_per_100k"]), 1),
        "power_standard": int(power_bands["Standard"]),
        "power_standard_plus": int(power_bands["Standard plus"]),
        "power_rapid": int(power_bands["Rapid"]),
        "power_ultra_rapid": int(power_bands["Ultra-rapid"]),
        "highest_authority": str(highest["name"]),
        "lowest_authority": str(lowest["name"]),
        "rate_under_100": band_counts[0],
        "rate_100_to_150": band_counts[1],
        "rate_150_to_200": band_counts[2],
        "rate_200_or_more": band_counts[3],
        "authority_count": len(authorities),
        "quarter_count": len(dates),
    }


def figures_from_release(release: Release) -> dict[str, object]:
    return _claim_figures(release.authorities, release.regions, release.power_bands, release.dates)


def figures_from_derived(root: Path) -> dict[str, object]:
    """Read accepted figures from the committed JSON and authority CSV."""
    derived = Path(root) / "data" / "derived"
    payload = json.loads((derived / "charger_data.json").read_text(encoding="utf-8"))
    authorities = pd.read_csv(derived / "local_authority_chargers.csv")
    latest = date.fromisoformat(payload["as_of"]).isoformat()
    areas = payload.get("areas", [])
    if not areas:
        raise ValueError("Accepted charger_data.json has no local-authority records.")
    json_codes = {area.get("code") for area in areas}
    csv_codes = set(authorities["code"].astype(str))
    if json_codes != csv_codes:
        raise ValueError("Accepted JSON and CSV do not contain the same local-authority codes.")
    dates = sorted(areas[0].get("rates", {}).keys())
    month = pd.Timestamp(latest).strftime("%b").lower()
    authorities = authorities.rename(columns={
        f"count_{month}": f"count_{latest}",
        f"rapid_count_{month}": f"rapid_count_{latest}",
        f"rate_{month}": f"rate_{latest}",
    })
    regions = pd.DataFrame(payload["regions"])
    return _claim_figures(authorities, regions, payload["power_bands"], dates)


def _inside_repository(path):
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def discover(watch: dict) -> tuple[str | None, dict[str, str]]:
    """Find the newest listed release and the three workbook URLs it needs."""
    sources = [source for source in watch.get("sources", []) if source.get("check") == "links"]
    if len(sources) != 1:
        raise SourceUnavailable("Expected exactly one DfT EVCI listing source in sources/watch.json.")
    source = sources[0]
    try:
        status, body = check_sources.read(source["url"])
    except Exception as error:
        raise SourceUnavailable(f"Could not fetch the DfT listing page: {error}") from error
    state, detail, observed = check_sources.check_links(source, status, body)
    if state == "unreachable":
        raise SourceUnavailable(f"Could not read the DfT listing page: {detail}")
    latest = observed.get("latest_release")
    if not latest:
        raise SourceUnavailable("The DfT page did not expose a recognizable EVCI release tag.")

    files = observed.get("files", [])
    for filename in files:
        match = re.match(r"evci\d+_(\d{4}-\d{2})([^_]*)_", filename)
        if match and match.group(1) == latest and match.group(2):
            raise UnrecognisedRelease(
                f"Unrecognised release suffix {match.group(2)!r} in {filename}; refusing to guess its tag."
            )
    if latest == source.get("latest_release"):
        return None, {}

    text = body.decode("utf-8", "replace")
    urls_by_name = {}
    for raw_url in re.findall(r'"contentUrl"\s*:\s*"([^"]+\.ods)"', text):
        url = urljoin(source["url"], raw_url.replace("\\/", "/"))
        filename = unquote(Path(urlsplit(url).path).name)
        if filename in files:
            urls_by_name[filename] = url

    selected = {}
    missing = []
    for code in ("0101", "0102", "0201"):
        matches = [
            filename for filename in files
            if filename.startswith(f"evci{code}_{latest}") and filename.lower().endswith(".ods")
        ]
        if len(matches) != 1:
            missing.append(f"evci{code} ({len(matches)} matching files)")
        elif matches[0] not in urls_by_name:
            missing.append(matches[0] + " (download URL missing from listing)")
        else:
            selected[matches[0]] = urls_by_name[matches[0]]
    if missing:
        raise SourceUnavailable("The DfT listing is missing required release tables: " + ", ".join(missing))
    return latest, selected


def fetch(urls: dict[str, str], workspace: Path) -> list[dict]:
    """Download the selected workbooks into an isolated review workspace."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    downloads = []
    for name, url in urls.items():
        if Path(name).name != name or not name.lower().endswith(".ods"):
            raise SourceUnavailable(f"Unsafe or unexpected EVCI download name: {name!r}", downloads)
        try:
            status, body = check_sources.read(url)
        except Exception as error:
            raise SourceUnavailable(f"Could not download {name}: {error}", downloads) from error
        if status != 200 or not body:
            raise SourceUnavailable(f"Could not download {name}: HTTP {status} or an empty response.", downloads)
        target = workspace / name
        target.write_bytes(body)
        downloads.append({
            "name": name,
            "url": url,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        })
    return downloads


def _rounded(value, precision):
    if isinstance(value, str):
        return value
    rounded = round(float(value), precision)
    return int(rounded) if precision == 0 else rounded


def compare(accepted: dict, candidate: dict) -> list[Figure]:
    """Compare all pinned prose claims at the precision the prose displays."""
    expected = set(FIGURE_LABELS)
    if set(accepted) != expected or set(candidate) != expected:
        missing_accepted = sorted(expected - set(accepted))
        missing_candidate = sorted(expected - set(candidate))
        extra_accepted = sorted(set(accepted) - expected)
        extra_candidate = sorted(set(candidate) - expected)
        raise ValueError(
            "Figure contract mismatch: "
            f"accepted missing={missing_accepted}, candidate missing={missing_candidate}, "
            f"accepted extra={extra_accepted}, candidate extra={extra_candidate}."
        )
    return [
        Figure(
            key=key,
            label=label,
            precision=precision,
            accepted=_rounded(accepted[key], precision),
            candidate=_rounded(candidate[key], precision),
        )
        for key, (label, precision) in FIGURE_LABELS.items()
    ]


def _structure(root: Path, release: Release) -> dict:
    derived = Path(root) / "data" / "derived"
    payload = json.loads((derived / "charger_data.json").read_text(encoding="utf-8"))
    accepted_dates = sorted(payload["areas"][0]["rates"])
    accepted = pd.read_csv(derived / "local_authority_chargers.csv").set_index("code")
    candidate = release.authorities.set_index("code")
    added = sorted(set(candidate.index) - set(accepted.index))
    removed = sorted(set(accepted.index) - set(candidate.index))
    return {
        "authorities_added": [
            {"code": code, "name": str(candidate.at[code, "name"])} for code in added
        ],
        "authorities_removed": [
            {"code": code, "name": str(accepted.at[code, "name"])} for code in removed
        ],
        "accepted_series_start": accepted_dates[0] if accepted_dates else None,
        "candidate_series_start": release.dates[0] if release.dates else None,
        "series_start_changed": bool(accepted_dates and release.dates and accepted_dates[0] != release.dates[0]),
        "quarters": len(release.dates),
        "schema_warnings": [],
    }


def _write_candidate(output: Path, release: Release, figures: dict) -> None:
    release.authorities.to_csv(output / "candidate_local_authority_chargers.csv", index=False)
    release.regions.to_csv(output / "candidate_regions.csv", index=False)
    (output / "candidate_power_bands.json").write_text(
        json.dumps(release.power_bands, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (output / "candidate_figures.json").write_text(
        json.dumps({"release": release.tag, "dates": release.dates, "figures": figures},
                   indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _display(value, precision):
    if isinstance(value, str):
        return value.replace("|", "\\|").replace("\n", " ")
    if precision == 0:
        return f"{int(value):,}"
    return f"{float(value):,.{precision}f}"


def report_markdown(audit: dict) -> str:
    lines = [
        f"## EV charger refresh review — {audit['retrieved_at_utc'][:10]}", "",
        f"**Status: {audit['status']}**", "",
        f"- Accepted release: `{audit.get('accepted_release') or 'unknown'}`",
        f"- Candidate release: `{audit.get('candidate_release') or 'none'}`",
        "- Nothing has been adopted; the published report and its source data are unchanged.", "",
    ]
    if audit["figures"]:
        lines += [
            "### Figure-by-figure comparison", "",
            "| Reported figure | Accepted | Candidate | Result |",
            "|---|---:|---:|---|",
        ]
        for figure in audit["figures"]:
            lines.append(
                f"| {figure['label']} | {_display(figure['accepted'], figure['precision'])} "
                f"| {_display(figure['candidate'], figure['precision'])} "
                f"| {'Changed' if figure['changed'] else 'Unchanged'} |"
            )
        structure = audit["structure"]
        lines += ["", "### Structural comparison", ""]
        added = structure["authorities_added"]
        removed = structure["authorities_removed"]
        lines.append("- Authorities added: " + (", ".join(f"`{item['code']}` {item['name']}" for item in added) if added else "none"))
        lines.append("- Authorities removed: " + (", ".join(f"`{item['code']}` {item['name']}" for item in removed) if removed else "none"))
        lines.append(
            f"- Series start: `{structure['accepted_series_start']}` → `{structure['candidate_series_start']}` "
            f"({'changed' if structure['series_start_changed'] else 'unchanged'}); "
            f"{structure['quarters']} quarter columns."
        )
        if structure["schema_warnings"]:
            lines.append("- Schema warnings: " + "; ".join(structure["schema_warnings"]))
        lines += [
            "", f"{audit['changed']} figures changed at quoted precision; {audit['unchanged']} are unchanged.", "",
        ]
    elif audit.get("detail"):
        lines += ["### Detail", "", audit["detail"], ""]
        warnings = audit["structure"].get("schema_warnings", [])
        if warnings:
            lines += ["### Schema warnings", "", *[f"- {warning}" for warning in warnings], ""]
    if audit["downloads"]:
        lines += ["### Downloaded workbooks", ""]
        lines += [
            f"- `{item['name']}` — {item['bytes']:,} bytes, sha256 `{item['sha256']}`"
            for item in audit["downloads"]
        ]
        lines.append("")
    if audit["status"] == "NO NEW RELEASE":
        lines += ["No newer release was found, so no workbooks were downloaded.", ""]
    lines += [
        "### Adoption remains a human decision", "",
        "To adopt a release, review the tables and figures, copy the selected source tables into `data/raw/`, "
        "update and run the published analysis, revise every affected report claim, pass the report checks, "
        "render and review the site, then update `sources/watch.json`. This comparison has made no edits or patch.", "",
        f"<!-- source-watch:{audit['state_fingerprint']} -->",
    ]
    return "\n".join(lines) + "\n"


def run_refresh(output: Path, report_path: Path | None = None, watch: dict | None = None) -> tuple[dict, str]:
    output = Path(output).resolve()
    report_path = Path(report_path).resolve() if report_path else output / "refresh.md"
    if _inside_repository(output) or _inside_repository(report_path):
        raise ValueError("Refresh output and report paths must be outside the repository.")
    output.mkdir(parents=True, exist_ok=True)
    watch_data = watch or json.loads(WATCH.read_text(encoding="utf-8"))
    source = next((item for item in watch_data.get("sources", []) if item.get("check") == "links"), {})
    accepted_release = source.get("latest_release")
    audit = {
        "status": "COULD NOT REACH SOURCE",
        "accepted_release": accepted_release,
        "candidate_release": None,
        "retrieved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "downloads": [],
        "structure": {
            "authorities_added": [], "authorities_removed": [],
            "accepted_series_start": None, "candidate_series_start": None,
            "series_start_changed": False, "quarters": 0, "schema_warnings": [],
        },
        "figures": [], "changed": 0, "unchanged": 0, "detail": "",
    }

    try:
        tag, urls = discover(watch_data)
    except UnrecognisedRelease as error:
        audit["status"] = "COULD NOT READ RELEASE"
        audit["detail"] = str(error)
        audit["structure"]["schema_warnings"].append(str(error))
    except SourceUnavailable as error:
        audit["detail"] = str(error)
        audit["downloads"] = error.downloads
    else:
        if tag is None:
            audit["status"] = "NO NEW RELEASE"
        else:
            audit["candidate_release"] = tag
            try:
                audit["downloads"] = fetch(urls, output)
            except SourceUnavailable as error:
                audit["detail"] = str(error)
                audit["downloads"] = error.downloads
            else:
                try:
                    release = read_release(output, tag)
                    candidate_figures = figures_from_release(release)
                    accepted_figures = figures_from_derived(ROOT)
                    figures = compare(accepted_figures, candidate_figures)
                    audit["figures"] = [
                        {**asdict(figure), "changed": figure.changed} for figure in figures
                    ]
                    audit["changed"] = sum(figure.changed for figure in figures)
                    audit["unchanged"] = len(figures) - audit["changed"]
                    audit["structure"] = _structure(ROOT, release)
                    audit["status"] = f"NEW RELEASE {tag}"
                    _write_candidate(output, release, candidate_figures)
                except Exception as error:
                    audit["status"] = "COULD NOT READ RELEASE"
                    audit["detail"] = str(error)
                    audit["structure"]["schema_warnings"].append(str(error))

    audit["drift"] = audit["status"] != "NO NEW RELEASE"
    fingerprint_payload = {
        key: audit[key] for key in ("status", "candidate_release", "downloads", "structure", "figures")
    }
    audit["state_fingerprint"] = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    (output / "audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = report_markdown(audit)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return audit, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path(tempfile.gettempdir()) / "evci-refresh-review",
        help="External workspace for downloaded tables, candidate data and audit.json",
    )
    parser.add_argument("--report", type=Path, help="Write the Markdown comparison to this external path")
    args = parser.parse_args()
    audit, report = run_refresh(args.output, args.report)
    print(report, end="")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"drift={'true' if audit['drift'] else 'false'}\n")
            handle.write(f"new_release={'true' if audit['status'].startswith('NEW RELEASE ') else 'false'}\n")


if __name__ == "__main__":
    main()