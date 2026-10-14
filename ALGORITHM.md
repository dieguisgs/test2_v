# VWAPS2 — Spread-based curve filling

VWAPS2 completes each day's power curve for every product with the **spread methodology**: your
VWAPs are the fixed points, and EEX only contributes **the differences between maturities**,
smoothed with an exponential average of recent publications.

It is a sibling of `VWAPS` with the same structure: same commands, same `config.toml` apart from the
method section, same Excel/CSV mapping with `use = fill/off`, and the same outputs. **Only the gap
filling changes**, so both methods can be compared on identical data (`compare_methods.py`, section 8).
Spanish version: [ALGORITMO.md](ALGORITMO.md).

---

## 1. Idea

The working rule is: *“the spread between contracts is what we look at, not the outright value”*.

| Colour | Meaning | Calculation |
|---|---|---|
| **Black** | Your VWAP of the day | As is |
| **Blue** | Gap with an own VWAP nearby (the *anchor*) | `anchor + EWMA₅(EEX_gap − EEX_anchor)` |
| **Orange** | Gap without any anchor | `EWMA₅(EEX_gap)` |

Blue can also be read as **smoothed EEX + local adjustment**:

```
blue = anchor + EWMA(EEX_gap − EEX_anchor)
     = EWMA(EEX_gap) + [anchor − EWMA(EEX_anchor)]
```

The identity is exact because a weighted mean is linear. Orange is the same without the local
adjustment.

---

## 2. Step by step

For each reference date `T`, product and target tenor:

1. **Tenors to delivery dates.** Same code as VWAPS. Matching is by delivery period, so your `Q+1`
   and EEX's quarter coincide whatever EEX calls it (for example after cascading).
2. **Allowed EEX window.** Cutoff = `T + offset_days` (0: `T`'s publication or the latest earlier
   one; −1: never `T`'s own publication). The window holds the latest `window` publications (5)
   up to the cutoff. Nothing after the cutoff is ever used.
3. **EEX price of each contract on each publication.** Exact, or built by hours from other
   contracts (months → quarter, quarters → season, remaining days → BOM), as in VWAPS. A
   publication without a price is skipped.
4. **Weights.** `2^(−age / halflife)`, where age 0 is the latest publication, normalised over
   publications with data. With `window = 5` and `halflife = 2` the weights are
   9 / 13 / 18 / 25 / 36 %. `weighting = "simple"` uses equal weights. With fewer than `min_obs`
   publications there is no estimate.
5. **Blue.**
   - Eligible anchors are today's VWAPs with volume ≥ `min_volume`. With `anchor_scope = "same_kind"`
     months anchor months, quarters anchor quarters and so on; `"any"` allows any kind.
   - The smoothed spread is computed **absolute contract against absolute contract** (for example
     Nov-26 − Oct-26), never by rolling label, so a month roll inside the window cannot mix contracts.
   - `anchor_rule = "nearest"` picks the closest anchor in log days to delivery, falling back to the
     next one if its spread is unavailable. `"interpolate"` takes the closest anchor on each side and
     blends both estimates by inverse distance, which avoids jumps between anchors.
6. **Orange.** `no_anchor = "eex_ewma"` uses the EWMA of the EEX price (as described in the
   meeting); `"eex_level"` uses the latest allowed EEX price without smoothing; `"missing"` leaves
   the gap empty.
7. **Shared options.** `layers.arbitrage` (rebuild remaining gaps from filled contracts) and `shape`
   behave exactly as in VWAPS.

---

## 3. Worked example

Reference date 30 Sep, DE Base, own **M+1 (Oct) = 105.00**. EEX on the last five publications
(example numbers):

| Publication | Oct | Nov | Nov − Oct | Weight |
|---|---|---|---|---|
| 24 Sep | 100 | 110 | 10 | 8.9 % |
| 25 Sep | 101 | 112 | 11 | 12.6 % |
| 28 Sep | 102 | 114 | 12 | 17.8 % |
| 29 Sep | 103 | 116 | 13 | 25.2 % |
| 30 Sep | 104 | 118 | 14 | 35.6 % |

- Blue: `EWMA(Nov − Oct) = 12.66`, so **M+2 = 117.66**.
- Orange: `EWMA(Nov) = 115.32`, which lags the latest price of 118 (the cost of smoothing the level).
  With `no_anchor = "eex_level"` the result is 118.
- With `offset_days = -1`, the window is 24–29 Sep (weights 13.8 / 19.5 / 27.6 / 39.1 %), the spread
  is 11.92 and M+2 = 116.92.

---

## 4. Cases

| # | Case | Result | `source` |
|---|---|---|---|
| 1 | Own VWAP for the contract | Your VWAP | `own` |
| 2 | A usable anchor | Blue | `eex_spread` |
| 3 | `interpolate` with anchors on both sides | Distance blend | `eex_spread` |
| 4 | No same-kind anchor (with `same_kind`) | Orange | `eex_ewma` / `eex_level` |
| 5 | No own VWAP that day | Orange on the whole curve | `eex_ewma` / `eex_level` |
| 6 | EEX does not quote the exact contract | Built by hours, then as above | `eex_method = strip / residual` |
| 7 | No EEX publication today, or `offset_days = -1` | Window ends at the latest allowed publication; logged | — |
| 8 | Fewer than `min_obs` publications | No estimate (or arbitrage if enabled) | `missing` |
| 9 | VWAP below `min_volume` | Published, but does not anchor others | `own` + `anchor_excluded` |
| 10 | Product without EEX in the mapping | Own VWAPs only (optional arbitrage) | `own` / `missing` |

**Unlike VWAPS**, this method:
- learns nothing from previous days (no historical factor, no correlations, no help from other
  products);
- uses spreads in price units only, never ratios;
- uses a single anchor, or two neighbours with `interpolate`.

A `daily` run and the same day inside a `refill` are therefore identical, without warmup.

---

## 5. Parameters (`config.toml`)

| Section | Parameter | Default | Meaning |
|---|---|---|---|
| `spread` | `window` | 5 | EEX publications per window |
| | `weighting` | ewma | `ewma` or `simple` |
| | `halflife` | 2.0 | EWMA half-life in publications |
| | `min_obs` | 1 | Minimum publications with data |
| | `anchor_rule` | nearest | `nearest` or `interpolate` |
| | `anchor_scope` | same_kind | `same_kind` or `any` |
| | `no_anchor` | eex_ewma | `eex_ewma`, `eex_level` or `missing` |
| `method` | `min_volume` | 0 | Minimum volume for a VWAP to anchor |
| | `max_anchor_dev` | 0 | If > 0, reject anchors further than this fraction from EEX |
| `layers` | `arbitrage` | false | Rebuild remaining gaps from filled contracts |
| `eex` | `offset_days` | 0 | 0 = same-day publication; −1 = up to yesterday… |
| | `max_stale_days`, `warn_stale_days` | 0, 3 | Maximum age / warning |
| `paths`, `targets`, `conventions`, `timezones`, `shape`, `run`, `vwap_columns` | | | **Same as VWAPS** |

**Removed** relative to VWAPS:
- `[layers] local/correlation/cross/hist`
- `basis_mode`, `tau_log`, `other_kind_weight`, `shrink_k`, `ewma_halflife_days`, `hist_*`,
  `ratio_eex_floor`, `max_ratio_deviation`
- the `[eex_fallback]`, `[correlation]` and `[cross]` sections

**Per product**: `python run.py mapping --with-parameters` adds one column per model parameter
(`spread_window`, `spread_halflife`, `spread_weighting`, `spread_min_obs`, `anchor_rule`,
`anchor_scope`, `no_anchor`, `min_volume`, `max_anchor_dev`, `layer_arbitrage`, `shape_*`,
`tenors`). Each row uses its cells in `configuration_mode = "individual"`; blank cells inherit from
the TOML.

**Per run**: `--spread-window`, `--spread-weighting`, `--spread-halflife`, `--spread-min-obs`,
`--anchor-rule`, `--anchor-scope`, `--no-anchor`, `--eex-offset-days`.

---

## 6. Commands

Same as VWAPS: `mapping`, `refill`, `daily`, `catchup`, `status`, `backtest`, `tune`,
`detect-conventions`, `make-synthetic`.

`backtest` hides each VWAP and predicts it with:
- `eex`: the day's EEX, the baseline shared with VWAPS;
- `eex_ewma` and `eex_level`;
- `spread_nearest` and `spread_interpolate`;
- `pipeline_configured`: the deployed configuration, replaying the whole curve.

`tune` searches `spread_window`, `spread_halflife`, `spread_weighting`, `anchor_rule`,
`anchor_scope` and `no_anchor`.

---

## 7. Output

The columns for identity, dates, price, source, confidence, origin, method, EEX provenance and
flags are the same as in VWAPS. Method-specific columns:

| Column | Meaning |
|---|---|
| `anchors` | Anchor tenor(s) |
| `anchor_price` | Anchor price, when there is a single anchor |
| `spread_smoothed` | Smoothed EEX spread, gap − anchor |
| `window_obs` | Publications used |
| `spread_trace` | JSON with every publication, value and weight |

`source` is one of `own`, `eex_spread`, `eex_ewma`, `eex_level`, `arbitrage`, `missing`.

---

## 8. Comparing with VWAPS

```
python compare_methods.py --vwap "path/to/your_vwaps.xlsx"
python compare_methods.py --synthetic
```

Each repository runs with its own code and a copy of its own `config.toml`, with the same paths and
the same `offset_days` (0 and −1); neither repository is modified. `comparison/SUMMARY.md` contains:
- a paired leave-one-out backtest on identical hidden observations, with raw EEX as reference;
- cell counts by source for each method;
- with synthetic data, the error against the truth on cells both methods filled.
