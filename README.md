# Where the UK plugs in

A data-led report on public electric-vehicle charger coverage across the United Kingdom, based on Department for Transport statistics at 1 July 2026.

[Read the report](https://aloysius109.github.io/ev-charger-report/)

## Rebuild

Install the packages in `requirements.txt`, then run:

```powershell
python scripts/analyze.py
quarto render
```

The analysis reads the original DfT ODS tables in `data/raw/`, regenerates charts in `assets/`, and writes local-authority selector data to `data/derived/`. The UK local-authority boundaries are vendored in `data/geo/local_authorities.geojson`; to refresh them from the ONS Open Geography Portal, run `python scripts/fetch_boundaries.py` before the analysis. The source layer is the [ONS May 2023 UK local-authority districts dataset](https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Local_Authority_Districts_May_2023_UK_BUC_V2/FeatureServer).

## Checks

Run `python scripts/check_report.py` after rebuilding to verify the report's headline figures, regional rates, power bands, all 361 boundary matches, map-band counts, explorer data and linked assets. The **Report checks** workflow rebuilds the derived data and runs this check on pushes and pull requests.

## Source watch

`sources/watch.json` records what the Department for Transport was publishing when this edition was built: the set of EVCI data-table filenames and their release tag, `2026-07`. Run `python scripts/check_sources.py` to compare that baseline with the page today; the script only reads upstream, and never changes the report.

The **Source watch** workflow runs the same check every Monday and opens an issue labelled `source-watch` when a new release appears or the page cannot be read. One open issue is kept at a time, so a release that has not yet been adopted does not reopen weekly. GitHub disables scheduled workflows after 60 days without repository activity; if this report sits untouched for two months, dispatch the workflow manually to re-enable it.

Adopting a new release is a deliberate edit, not an automatic one: download the new tables into `data/raw/`, re-run `scripts/analyze.py`, update every figure quoted in `index.qmd`, re-run `scripts/check_report.py`, then re-baseline the watcher with `python scripts/check_sources.py --update`.

## Analytics

The report uses GoatCounter, configured in `analytics.html`. Update the `data-goatcounter` URL in that partial if the account changes. It is used without cookies, a consent banner or personal-data collection.