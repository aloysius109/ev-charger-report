# Where the UK plugs in

A data-led report on public electric-vehicle charger coverage across the United Kingdom, based on Department for Transport statistics at 1 July 2026.

[Read the report](https://aloysius109.github.io/ev-charger-report/)

## Rebuild

Install the packages in `requirements.txt`, then run:

```powershell
python scripts/analyze.py
quarto render
```

The analysis reads the original DfT ODS tables in `data/raw/`, regenerates charts in `assets/`, and writes local-authority selector data to `data/derived/`.

## Checks

Run `python scripts/check_report.py` after rebuilding to verify the report's headline figures, regional rates, power bands, explorer data and linked assets. The **Report checks** workflow rebuilds the derived data and runs this check on pushes and pull requests.