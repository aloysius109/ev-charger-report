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

## Analytics

The report uses GoatCounter, configured in `analytics.html`. Update the `data-goatcounter` URL in that partial if the account changes. It is used without cookies, a consent banner or personal-data collection.