import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from scripts import refresh_report

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = json.loads(
    (ROOT / "tests" / "fixtures" / "evci_2026-10" / "scenario.json").read_text(encoding="utf-8")
)


def _column_date(value):
    return pd.Timestamp(value).strftime("%d %B %Y").lstrip("0")


def _write_fixture(workspace, malformed=False):
    baseline = refresh_report.read_release(ROOT / "data" / "raw", "2026-07")
    authorities = baseline.authorities.copy()
    removed_code = authorities.iloc[0]["code"]
    authorities = authorities.loc[authorities["code"] != removed_code].copy().reset_index(drop=True)
    added = {
        "code": SCENARIO["added_authority"]["code"],
        "name": SCENARIO["added_authority"]["name"],
        "country": "England",
    }
    for column in authorities.columns:
        if column.startswith(("count_", "rapid_count_", "rate_", "rapid_rate_")):
            added[column] = 0
    authorities = pd.concat([authorities, pd.DataFrame([added])], ignore_index=True)
    old_latest = baseline.dates[-1]
    new_date = SCENARIO["quarter_date"]
    for prefix, delta in SCENARIO["authority_deltas"].items():
        authorities[f"{prefix}_{new_date}"] = authorities[f"{prefix}_{old_latest}"] + delta
    new_index = authorities.index[authorities["code"] == SCENARIO["added_authority"]["code"]][0]
    authorities.loc[new_index, f"count_{new_date}"] = SCENARIO["added_counts"]["count"]
    authorities.loc[new_index, f"rapid_count_{new_date}"] = SCENARIO["added_counts"]["rapid_count"]
    authorities.loc[new_index, f"rate_{new_date}"] = SCENARIO["added_counts"]["rate"]
    authorities.loc[new_index, f"rapid_rate_{new_date}"] = SCENARIO["added_counts"]["rapid_rate"]

    workspace.mkdir(parents=True, exist_ok=True)
    dates = baseline.dates + [new_date]
    headers = [_column_date(value) for value in dates]
    authority_path = workspace / f"evci0102_{SCENARIO['release']}.ods"
    with pd.ExcelWriter(authority_path, engine="odf") as writer:
        for sheet, prefix in zip(
            refresh_report.AUTHORITY_SHEETS, ("count", "rapid_count", "rate", "rapid_rate")
        ):
            frame = pd.DataFrame({
                "Local authority code": authorities["code"],
                "Local authority": authorities["name"],
            })
            for current_date, header in zip(dates, headers):
                frame[header] = authorities[f"{prefix}_{current_date}"]
            if malformed and sheet == refresh_report.AUTHORITY_SHEETS[0]:
                sheet = "Renamed authority sheet"
            frame.to_excel(writer, sheet_name=sheet, index=False, startrow=2)

    regions = baseline.regions.copy()
    latest_regions = regions.loc[regions["date"] == old_latest]
    future_rows = []
    for row in latest_regions.to_dict(orient="records"):
        total = row["total_per_100k"]
        if row["area"] == "United Kingdom":
            total += SCENARIO["uk_rate_delta"]
        if row["area"] == "London":
            total += SCENARIO["sub_precision_london_delta"]
        future_rows.append({
            "date": new_date,
            "area": row["area"],
            "total_per_100k": total,
            "rapid_per_100k": row["rapid_per_100k"],
        })
    regions = pd.concat([regions, pd.DataFrame(future_rows)], ignore_index=True)
    region_path = workspace / f"evci0101_{SCENARIO['release']}.ods"
    with pd.ExcelWriter(region_path, engine="odf") as writer:
        for sheet, value_column in zip(refresh_report.REGION_SHEETS, ("total_per_100k", "rapid_per_100k")):
            frame = regions.pivot(index="date", columns="area", values=value_column).reset_index()
            frame = frame.rename(columns={"date": "Date"})
            frame["Date"] = pd.to_datetime(frame["Date"])
            frame.insert(1, "Notes", "")
            frame.to_excel(writer, sheet_name=sheet, index=False, startrow=2)

    power_rows = []
    for current_date in dates:
        row = {"Date": current_date, "Notes": ""}
        row.update(baseline.power_bands)
        if current_date == new_date:
            row["Standard"] += SCENARIO["power_standard_delta"]
        power_rows.append(row)
    power_path = workspace / f"evci0201_{SCENARIO['release']}.ods"
    power_frame = pd.DataFrame(power_rows)
    power_frame["Date"] = pd.to_datetime(power_frame["Date"])
    power_frame.to_excel(
        power_path, engine="odf", sheet_name=refresh_report.POWER_SHEET,
        index=False, startrow=2,
    )
    return {
        "removed_code": removed_code,
        "files": [
            f"evci0101_{SCENARIO['release']}.ods",
            f"evci0102_{SCENARIO['release']}.ods",
            f"evci0201_{SCENARIO['release']}.ods",
        ],
    }


def _tracked_tree_digest():
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
    digest = hashlib.sha256()
    for name in sorted(name for name in names if name):
        path = ROOT / Path(name.decode("utf-8"))
        digest.update(name)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _fixture_reader(fixtures):
    def read(url):
        name = url.rsplit("/", 1)[-1]
        return 200, (fixtures / name).read_bytes()
    return read


class RefreshReportReaderTests(unittest.TestCase):
    def test_july_release_matches_every_accepted_figure(self):
        payload = json.loads((ROOT / "data" / "derived" / "charger_data.json").read_text(encoding="utf-8"))
        tag = payload["as_of"][:7]
        release = refresh_report.read_release(ROOT / "data" / "raw", tag)

        self.assertEqual(refresh_report.figures_from_release(release), refresh_report.figures_from_derived(ROOT))
        self.assertEqual(release.dates, sorted(payload["areas"][0]["rates"]))

    def test_reader_does_not_pin_a_calendar_date(self):
        source = (ROOT / "scripts" / "refresh_report.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)", source))

    def test_discovery_uses_the_watcher_pattern_and_resolves_required_tables(self):
        watch = json.loads((ROOT / "sources" / "watch.json").read_text(encoding="utf-8"))
        source = watch["sources"][0]
        names = [
            f"evci0101_{SCENARIO['release']}_EV_chargers_by_region_UK.ods",
            f"evci0102_{SCENARIO['release']}_EV_chargers_by_authority_UK.ods",
            f"evci0201_{SCENARIO['release']}_EV_chargers_by_power_UK.ods",
        ]
        links = [
            f"https://assets.publishing.service.gov.uk/media/test/{name}" for name in names
        ]
        body = "\n".join(f'"contentUrl": "{link}"' for link in links).encode("utf-8")
        with patch.object(refresh_report.check_sources, "read", return_value=(200, body)):
            tag, urls = refresh_report.discover(watch)

        self.assertEqual(tag, SCENARIO["release"])
        self.assertEqual(set(urls), set(names))
        self.assertEqual(set(urls.values()), set(links))

    def test_no_new_release_discovery_fetches_the_listing_once(self):
        watch = json.loads((ROOT / "sources" / "watch.json").read_text(encoding="utf-8"))
        body = b"evci0101_2026-07_EV.ods evci0102_2026-07_EV.ods evci0201_2026-07_EV.ods"
        with patch.object(refresh_report.check_sources, "read", return_value=(200, body)) as read:
            tag, urls = refresh_report.discover(watch)

        self.assertIsNone(tag)
        self.assertEqual(urls, {})
        read.assert_called_once()

    def test_release_tag_changes_issue_state_and_new_release_output(self):
        source = json.loads((ROOT / "sources" / "watch.json").read_text(encoding="utf-8"))["sources"][0]
        baseline = [{
            "id": source["id"], "state": "changed", "observed": {"latest_release": "2026-10"}
        }]
        later = [{
            "id": source["id"], "state": "changed", "observed": {"latest_release": "2027-01"}
        }]
        self.assertTrue(refresh_report.check_sources.has_new_release(baseline, [source]))
        self.assertNotEqual(
            refresh_report.check_sources.fingerprint(baseline),
            refresh_report.check_sources.fingerprint(later),
        )
        self.assertEqual(
            refresh_report.check_sources.fingerprint(baseline),
            refresh_report.check_sources.fingerprint(baseline),
        )
        same_as_baseline = [{
            "id": source["id"], "state": "changed", "observed": {"latest_release": source["latest_release"]}
        }]
        self.assertFalse(refresh_report.check_sources.has_new_release(same_as_baseline, [source]))

    def test_no_new_release_writes_audit_without_downloading(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "candidate"
            report_path = Path(temporary) / "refresh.md"
            with patch.object(refresh_report, "discover", return_value=(None, {})):
                audit, report = refresh_report.run_refresh(output, report_path)

            self.assertEqual(audit["status"], "NO NEW RELEASE")
            self.assertFalse(audit["drift"])
            self.assertEqual(audit["downloads"], [])
            self.assertTrue((output / "audit.json").is_file())
            self.assertIn("source-watch:", report)
            self.assertEqual(report_path.read_text(encoding="utf-8"), report)

    def test_synthetic_release_diff_reports_structure_and_precision(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            fixture_dir = workspace / "fixtures"
            written = _write_fixture(fixture_dir)
            output = workspace / "candidate"
            urls = {name: f"https://example.test/{name}" for name in written["files"]}
            original_tree = _tracked_tree_digest()
            with (
                patch.object(refresh_report, "discover", return_value=(SCENARIO["release"], urls)),
                patch.object(refresh_report.check_sources, "read", side_effect=_fixture_reader(fixture_dir)),
            ):
                audit, report = refresh_report.run_refresh(output, workspace / "refresh.md")

            figures = {figure["key"]: figure for figure in audit["figures"]}
            self.assertEqual(audit["status"], f"NEW RELEASE {SCENARIO['release']}", audit["detail"])
            self.assertTrue(audit["drift"])
            self.assertTrue(figures["total_chargers"]["changed"])
            self.assertTrue(figures["power_standard"]["changed"])
            self.assertFalse(figures["london_total_per_100k"]["changed"])
            self.assertAlmostEqual(figures["london_total_per_100k"]["accepted"], figures["london_total_per_100k"]["candidate"])
            self.assertIn(written["removed_code"], {item["code"] for item in audit["structure"]["authorities_removed"]})
            self.assertIn(SCENARIO["added_authority"]["code"], {item["code"] for item in audit["structure"]["authorities_added"]})
            self.assertFalse(audit["structure"]["series_start_changed"])
            self.assertEqual(audit["structure"]["quarters"], 4)
            self.assertEqual(audit["changed"] + audit["unchanged"], len(refresh_report.FIGURE_LABELS))
            self.assertEqual(len(audit["downloads"]), 3)
            for item in audit["downloads"]:
                self.assertEqual(item["bytes"], (output / item["name"]).stat().st_size)
            self.assertIn("Nothing has been adopted", report)
            self.assertIn("source-watch:", report)
            self.assertTrue((output / "candidate_local_authority_chargers.csv").is_file())
            self.assertTrue((output / "candidate_regions.csv").is_file())
            self.assertEqual(original_tree, _tracked_tree_digest())

    def test_malformed_sheet_writes_a_readable_failure_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            fixture_dir = workspace / "fixtures"
            written = _write_fixture(fixture_dir, malformed=True)
            output = workspace / "candidate"
            urls = {name: f"https://example.test/{name}" for name in written["files"]}
            with (
                patch.object(refresh_report, "discover", return_value=(SCENARIO["release"], urls)),
                patch.object(refresh_report.check_sources, "read", side_effect=_fixture_reader(fixture_dir)),
            ):
                audit, report = refresh_report.run_refresh(output, workspace / "refresh.md")

            self.assertEqual(audit["status"], "COULD NOT READ RELEASE")
            self.assertTrue(audit["drift"])
            self.assertTrue(audit["structure"]["schema_warnings"])
            self.assertTrue((output / "audit.json").is_file())
            saved = json.loads((output / "audit.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "COULD NOT READ RELEASE")
            self.assertIn("missing required sheet", report.lower())
            self.assertIn("source-watch:", report)


if __name__ == "__main__":
    unittest.main()