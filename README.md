# VWAPS2 — Spread-based VWAP curve completion

Sibling of `VWAPS` with **the same infrastructure** (commands, `config.toml`, Excel/CSV mapping with
`use = fill/off`, per-product configuration, outputs, backtest, tune, MLflow, shape). Only the
**gap-filling methodology** changes:

- **black**: your VWAP;
- **blue**: `anchor + EWMA₅(EEX_gap − EEX_anchor)`;
- **orange**: no anchor, `EWMA₅(EEX_gap)`.

Details in [ALGORITHM.md](ALGORITHM.md) (Spanish: [ALGORITMO.md](ALGORITMO.md)).

## Quick start

```
uv sync
python run.py mapping                 # create/review mappings/products.csv (or .xlsx)
python run.py refill --from 2026-08-10
python run.py daily
python run.py backtest
```

`[eex] offset_days = 0` uses the same-day publication; `-1` uses publications up to yesterday.
Smoothing windows end at that same publication.

## Compare with VWAPS

```
python compare_methods.py --vwap "path/to/your_vwaps.xlsx"
python compare_methods.py --synthetic
```

Runs both repositories, each with its own code and configuration, on the same input and EEX data,
with `offset_days` 0 and −1. Writes `comparison/SUMMARY.md`; neither repository is modified.

## Inherited documents

`BACKTEST`, `CONFIGURATION`, `OUTPUT`, `MLFLOW`, `NOTEBOOK` and `SHAPE` come from VWAPS and still
describe the infrastructure. A banner at the top of each notes that method parameters live in
`[spread]`.
