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