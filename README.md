# VWAP curve completion


[Output dictionary](OUTPUT.md) · [Diccionario en español](OUTPUT.es.md)
[Español](README.es.md) · [Algorithm](ALGORITHM.md) · [Algoritmo en español](ALGORITMO.md)

[GitHub repository](https://github.com/dieguisgs/test1_v)

Complete missing power-curve prices for each **reference date, product, region, unit and target
tenor**, using observed VWAPs and EEX settlements. The enriched output retains original rows
and columns, adds absent curve rows, and identifies each price's origin and estimation method.

For the nine price-origin cases with worked examples, see section 0.9 of [ALGORITHM.md](ALGORITHM.md).

[Anchor research](ANCHORS.md) explains when Day/Month observations can help or mislead other
deliveries, with controlled examples and validation limits. Proposed eligibility policies
are research only; they are not new production configuration options.

The curve identity is **`(product, region, unit)`**. The same product name in another region
or unit has separate mapping, anchors, history and results. Input values are preserved;
internal matching trims surrounding whitespace without changing case or spelling. Empty
region/unit values are literal identities, never wildcards. Product names cannot be empty.

**Optional post-processing:** [shape](SHAPE.md) is implemented and off by default. It can audit or adjust existing finite curve prices. Original curve prices can move only with `shape.adjust_originals=true`; raw input columns remain intact.


**Global or individual configuration is implemented:** `[run].configuration_mode` selects `global` (default, ignores individual cells) or `individual` (applies each curve's nonempty CSV/Excel mapping cells). All 33 scalar model controls and the `tenors` list can be overridden; these are not closed presets. [CONFIGURATION.md](CONFIGURATION.md) documents the 34 columns, inheritance, CLI precedence, daily/refill/catchup behavior and provenance. Global or per-product MLflow search is a separate choice: Excel stores fixed values, while the notebook/JSON stores candidate grids.

## Minimum setup to get started

You do not need to tune all 60 entries. **19 are paths, column names and time zones**, not
statistical parameters. Start by reviewing `[paths]`, generating and reviewing the mapping,
choosing `[targets].tenors`, and keeping `method.basis_mode = "auto"`. Retain the other
initial values, including `correlation = false`, `cross = false`, `arbitrage = false` and `shape.mode = "off"`.

The full reference documents advanced controls for auditing and deliberate changes.
`tune` supports six model controls, but its default comparison tries only the three basis
modes and holds everything else fixed. It does not search across every TOML entry.

For editable parameter lists, automatic local MLflow startup and saved experiment comparisons,
use [the backtest notebook](notebooks/backtest_mlflow.ipynb) and its [MLflow guide](MLFLOW.md).
It starts with a self-contained synthetic demo and six combinations, then supports real data
on your other computer. Its expanded model grid includes local reach, historical memory,
EEX averaging and shape; the guide lists the supported fields and evaluation limits.
Install it with `uv sync --group notebook --group experiment`. It proposes parameters without
applying them to the config or production curves.
Both notebooks, including their explanations, code and interface text, are entirely in
English. The standalone Markdown documentation is available in English and Spanish.

The experiment notebook also [opens saved curves by run, candidate and stage](MLFLOW.md#saved-curves),
using the same full-curve, range-mean and fixed-delivery views as the output viewer. With
`LOG_PREDICTIONS=True`, every calibration trial saves full curves and only the winner saves
validation curves. These retain visible originals; use held-out metrics for accuracy.
[The selection explanation](MLFLOW.md#selection-explained) shows why maximum coverage comes
first, how the equal-curve model/EEX error score works, and why shape is reviewed separately.

## Install

On the machine containing your real data, clone the source repository or copy its source
files and documentation into a working directory. No archive is required:

```powershell
git clone https://github.com/dieguisgs/test1_v.git
cd test1_v
```

Requires Python 3.11 or later. From the project directory, use either:

```powershell
uv sync
```

or a virtual environment with pip:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install "numpy>=1.26" "pandas>=2.2" "openpyxl>=3.1" "pytest>=8.0"
```

The examples below use `python` from that environment. In PowerShell, activate it with `.venv\Scripts\Activate.ps1`, use `.venv\Scripts\python.exe` directly, or prefix commands with `uv run` when using uv.

Create the environment on the destination machine; do not copy `.venv` from another computer.
For both notebooks and local MLflow, install the optional groups with
`uv sync --group notebook --group experiment` and select that environment as the notebook
kernel. Notebook explanations, code and interfaces are in English; the Markdown guides
are available in English and Spanish.

## Configure the real input

There is **one main configuration file, `config.toml`**, in the source repository. Its
initial input path is `data/vwaps.xlsx`: edit that path to the real accumulated input on the
destination machine. All ordinary `python run.py ...` commands use this configuration.
For a separate demonstration run, explicitly pass `--vwap data/synthetic_vwaps.csv` if that
synthetic file has been generated; the default never silently selects demonstration data.

Real inputs, EEX data, the local mapping, virtual environment and generated outputs are not
published. Supply your own input and EEX files on the destination machine, configure their
paths below, and generate/review the mapping there. The repository's fictional mapping example
contains disabled rows; it does not replace your real product assignments.

Edit `[paths]` in the selected configuration:

```toml
[paths]
vwap_input = "data/vwaps.xlsx"
mapping = "mappings/products.csv"
eex_curves_dir = "../eex_scraper/output/curves/POWER"
output_dir = "output"
```

Paths are relative to the configuration file; absolute paths also work. Input can be CSV or Excel, including a file pattern such as `data/vwaps_*.csv`. `--vwap` overrides its path for one command.

The input uses long format, with one observation per row:

| Columns | Meaning |
|---|---|
| `reference_date`, `product`, `region`, `unit`, `tenor2`, `vwap` | Required date, complete curve identity, relative tenor and observed price. Region/unit columns must exist, even if a literal blank value is appropriate. Dates must be valid; missing or invalid prices can be completed. |
| `total_volume` | Optional volume used when combining observations and weighting anchors. |
| `weekday`, `country`, `classification`, `periodicity_2`, `n_trades` | Other original metadata; retained along with every input column. Region/unit are identity fields, not merely descriptive metadata. |

Examples of tenor labels are `D+1`, `WE`, `M+1`, `Q+1` and `Cal+1`. Adjust `[vwap_columns]` if your column names differ, and `[targets]` for the tenors to complete. EEX files require `tradeDate`, `maturityType`, `deliveryStart` and `settlPx`.

`curves/POWER/<area>/Base.csv` and `Peak.csv` are long files containing the history accumulated
locally for each EEX product, not just one date. A repeated date is normal: each date has many
delivery contracts. The scraper rebuilds these views from its contract files in `table_data/`;
it does not append to the curve CSV. This one-file-per-area/product layout remains appropriate
for the current small files and the VWAP reader. Daily partitions are not required; a future
move to monthly partitions or Parquet would need changes to both writing and reading.

The target list is editable directly in the configuration; no Python changes are required:

```toml
[targets]
tenors = ["D+1", "D+2", "WE", "M+1", "M+2", "M+3", "Q+1", "Cal+1"]
```

Edit the existing section to extend or reduce this global list. In global mode it applies to all `fill` products; individual mode permits a `tenors` JSON cell per mapping identity. Original rows outside the list remain preserved; missing rows outside it are not created. A listed tenor is estimated only when the available data support it.

## Choose a calculation mode

Edit the existing `[method]` section in `config.toml`:

```toml
[method]
basis_mode = "auto"
```

| Value | Calculation |
|---|---|
| `auto`, default | Select ratio or additive separately for each target, using the decision tree below |
| `ratio` | Estimate `basis = Own/EEX − 1`, then `price = EEX × (1 + basis)`; never switch automatically |
| `additive` | Estimate `basis = Own − EEX`, then `price = EEX + basis`; never switch automatically |

In auto, the first matching rule wins:

1. If `abs(target EEX) < ratio_eex_floor`, use additive (`auto_additive_low_eex`).
2. Otherwise, if no ratio anchors remain but additive anchors exist, use additive
   (`auto_additive_no_ratio_anchors`).
3. Otherwise, if neither mode has anchors and only additive has current history allowed by
   `[layers].hist`, use additive (`auto_additive_history_only`).
4. Otherwise use ratio.

Raw original VWAPs, including zero and negative values, are always preserved. Final own curve prices also remain fixed unless shape adjustment explicitly permits moving originals. Ratio anchors
require `abs(EEX) >= ratio_eex_floor`, `Own/EEX > 0` and
`abs(Own/EEX − 1) <= max_ratio_deviation`; additive accepts finite pairs after the common
volume/deviation filters. Negative pairs such as −10/−12 can be valid ratio anchors; 2/0 cannot.
An auto target near zero selects additive even when other anchors could support ratio.
With an available ratio adjustment, a zero EEX target gives zero. Without adjustment components, smoothed fallback applies even when ratio was selected.

Ratio anchors can also be rejected by `max_ratio_deviation`, even with nonzero prices and
matching signs. Auto may then use their additive differences if the common filters allow
them. `max_ratio_deviation` protects ratio only; `max_anchor_dev` filters both modes and is
disabled by default. Auto is not an outlier detector or a selector of the lowest-error local model.

The two modes have separate histories, history scores and correlation/cross statistics.
No percentage basis is ever added directly to a price. `[layers].hist = "auto"` is a different
setting: it decides whether past history helps within each mode, rather than selecting the mode.

For example, Own = 2 against EEX = 0 supplies an additive difference of 2. With target EEX = 1,
no history, local weight sum W = 1 and `shrink_k = 1`, auto chooses additive and shrinks that
difference by `w = 0.5`: price = `1 + 0.5 × 2 = 2`. The unshrunk formula would give 3.
Actual W depends on volume and delivery distance. See the algorithm guide for the complete
weighting and learning calculations; auto selection is a rule to validate, not a guarantee
of lower prediction error.

## Smoothed EEX fallback when own adjustments are unavailable

This fallback replaces the former direct settlement return. **It runs only when no local,
permitted historical or usable cross component exists**, and a current EEX reference is
allowed under the freshness policy. Originals and learned adjustments keep their existing
calculations. An available adjustment whose value happens to be zero remains an adjustment:
numerical equality with EEX does not trigger a different branch.

In business terms, when there is no evidence for shifting EEX toward your level, the program
avoids copying one publication. It estimates a level from several publications of the same
contract; for farther months, it carries historical month-to-month differences forward from
a nearby month. Own trades and previous estimates do not train this fallback.

### Complete windows, fixed contracts and averages

Use the latest distinct publication dates in the EEX file through the cutoff
`reference_date + eex.offset_days`, ending at the latest snapshot accepted by `eex.max_stale_days`. These are **publication
observations**, not calendar days: reusing a stale settlement on several nonpublication days
does not increase the sample. Future publications are never used.

Every observation prices the **same absolute delivery period**. On each publication, Pricer
may obtain it by exact contract, strip or residual under the delivery profile's hours.
If that period cannot be priced on any of the file's latest N global publication dates,
the window fails: no skipping gaps for older values, no forward-filling, no partial windows.

- `simple`: arithmetic mean with weights `1/N`.
- Default `ewma`: **finite, normalized** exponential mean of N observations,
  `weight_j = 2^(-age_j/halflife) / sum_weights`; age 0 is the latest publication,
  1 the previous one, and so on. This is not the own-basis recursive EWMA.
- `ewma_halflife=2` gives an observation two publications older half the latest observation's
  unnormalized weight. It does not mean two calendar days.

### Months: a nearby level and differences between consecutive months

With `anchor_months=2`, anchor months are **M0, the reference date's calendar month, and M1**.
Each is averaged independently across `price_window=5` publications when requested.
M2 and later start from the **last anchor month, M1**, adding:

```text
price(M2) = average_price(M1) + simple_mean_9(EEX(M2) − EEX(M1))
price(M3) = price(M2)        + simple_mean_9(EEX(M3) − EEX(M2))
...
```

Each spread uses both prices from the **same publication**, across the latest
`spread_window=9` publications. Spreads use raw EEX prices; the latest day's spread is not
replaced with a previously smoothed spread. This avoids smoothing an already averaged input again and keeps level and spread averages separate.
Spread averages are always simple, including when price averages use EWMA.

M3 needs the complete M1 price window and complete M1→M2 and M2→M3 spread windows.
It does not require the M0 mean, because that anchor is independent. A missing link prevents
an incomplete cascade from being published. Count months from the reference date, not a
stale publication's date or the first configured target. With `anchor_months=1` the cascade
starts at M0; with 3 it starts at M2.

Day, Week, Weekend, Quarter, Season, Year, BOM and BOW average **their own absolute period**
directly across the price window. They are not automatically replaced with smoothed months.
The method retains historical monthly differences but does not force a quarter to equal its
months' average; inconsistencies are reported.

### Reproducible example: 5 price publications and 9 spread publications

Reference date: 2026-09-30; M1=October, M2=November, M3=December. Teaching prices:

| Publication | October | November | December | Nov−Oct | Dec−Nov |
|---|---:|---:|---:|---:|---:|
| 09-18 | 92 | 102 | 103 | 10 | 1 |
| 09-21 | 94 | 105 | 107 | 11 | 2 |
| 09-22 | 96 | 108 | 111 | 12 | 3 |
| 09-23 | 98 | 111 | 115 | 13 | 4 |
| 09-24 | 100 | 114 | 119 | 14 | 5 |
| 09-25 | 102 | 117 | 123 | 15 | 6 |
| 09-28 | 104 | 120 | 127 | 16 | 7 |
| 09-29 | 106 | 123 | 131 | 17 | 8 |
| 09-30 | 108 | 126 | 135 | 18 | 9 |

The latest five October observations are 100,102,104,106,108; their simple mean is 104.
With EWMA half-life 2, normalized weights from oldest to latest are 0.088947075, 0.125790159,
0.177894149, 0.251580318 and 0.355788298: mean **105.318945**.
The nine-publication mean Nov−Oct spread is **14** and Dec−Nov is **5**.
EWMA results: M1 **105.318945**, M2 **119.318945**, M3 **124.318945**.
Simple results: M1 **104**, M2 **118**, M3 **123**.

November is not published as 126 merely because that is its latest settlement. If smoothing
coincidentally equals the settlement — for example, constant prices — that equality is
preserved; no artificial perturbation is added. Longer windows can lag regime changes;
shorter windows can track more noise. Fixed delivery months avoid mixing rotating labels,
but do not guarantee complete seasonality modeling or improved predictions. Validate on
real observations and measure coverage too.

### The five controls and their CLI alternatives

```toml
[eex_fallback]
price_method = "ewma"
price_window = 5
ewma_halflife = 2.0
spread_window = 9
anchor_months = 2
```

| Key | Default | Effect and constraints |
|---|---|---|
| `eex_fallback.price_method` | `"ewma"` | `simple` gives all N observations equal weight; `ewma` favors recent ones. Only these choices. Does not change simple spread averaging |
| `eex_fallback.price_window` | `5` | Integer ≥2. Larger uses more publications and may smooth more, but needs more coverage; smaller has shorter memory. Not calendar days |
| `eex_fallback.ewma_halflife` | `2.0` | Finite >0. Larger distributes more weight to older observations; smaller favors recent ones. Affects prices only under ewma; must remain valid under simple |
| `eex_fallback.spread_window` | `9` | Integer ≥2. Complete window of simultaneous monthly differences. Larger needs more history and coverage. Applies only to monthly cascades |
| `eex_fallback.anchor_months` | `2` | Integer ≥1 counted from M0. Larger averages more months independently and delays cascade onset; not a count of own VWAPs |

These controls do not change originals or prices with local/historical/cross adjustments.
Price and spread windows are independent: neither must be larger than the other.
Maximum staleness governs the latest accepted snapshot; it does not turn the windows into
calendar-day windows. The 60 configuration keys include these five controls.

Override them for one `daily`, `refill`, `catchup`, `backtest` or `tune` execution:

| Option | Replaced control |
|---|---|
| `--eex-price-method simple` or `ewma` | `price_method` |
| `--eex-price-window N` | `price_window` |
| `--eex-ewma-halflife H` | `ewma_halflife` |
| `--eex-spread-window N` | `spread_window` |
| `--eex-anchor-months N` | `anchor_months` |

Example: `python run.py daily --eex-price-method simple --eex-price-window 5`.
Omitted options use TOML; the file is not modified. In tune these values remain fixed across
all candidates: they do not add dimensions to the six-control search grid.

### Result, audit and updating earlier outputs

Source is `eex+smooth`; methods are `eex_price_simple`, `eex_price_ewma`,
`eex_month_cascade_simple` and `eex_month_cascade_ewma`. `eex_settle` retains the unsmoothed
EEX reference for audit. `basis` and `local_weight` are empty: they do not describe this
average. `basis_mode` records the preceding ratio/additive selection, **not a formula applied
to the smoothed fallback**. JSON `eex_fallback_trace` — `curve_eex_fallback_trace` in enriched
output — records periods, dates, prices, weights and each spread for reconstruction.

Without a complete window, flag `eex_fallback_unavailable` is set and the price remains
`missing` by default. If final contract reconstruction is enabled, it is attempted instead.
Without a valid construction the price remains `missing`. The flag may persist
after successful arbitrage. It never reverts to a raw settlement. Unadjusted EEX remains the
`eex` benchmark in backtesting and hist-auto; a benchmark is distinct from production output.

Apply this change to existing results with `refill --from ... --to ...` or
`daily --date ...` on chosen dates. Catchup only recovers pending groups: an existing row,
including `missing` or an estimate from an earlier version, is already processed and is not
recalculated merely because the program was updated.


## Complete configuration reference

The defaults below describe the supplied `config.toml`. Edit its existing sections; do not
add duplicate TOML sections. Changing these settings requires no Python edits. Relative file
paths are resolved against the configuration file, except each mapping `eex_file`, which is
relative to `eex_curves_dir`.

### Paths (`[paths]`)

| Parameter | Supplied value | Effect of changing it |
|---|---|---|
| `vwap_input` | `data/vwaps.xlsx` | Select accumulated original CSV/Excel input or a quoted glob; do not point to enriched output |
| `mapping` | `mappings/products.csv` | Select the (product, region, unit)-to-EEX mapping to generate/read |
| `eex_curves_dir` | `../eex_scraper/output/curves/POWER` | Select the root containing long EEX CSVs such as `DE/Base.csv` |
| `output_dir` | `output` | Choose where daily/cumulative curves, enriched rows, reports and logs are written |

### Layers (`[layers]`)

| Parameter | Default | Effect of changing it |
|---|---|---|
| `local` | `true` | `false` disables today's anchor adjustment, while originals remain preserved |
| `hist` | `"auto"` | `"on"` uses current historical adjustments; `"off"` disables them; `"auto"` compares earlier errors against EEX, separately by mode/group |
| `correlation` | `false` | `true` replaces fixed weights between groups with measured surprise relationships, subject to the prior |
| `cross` | `false` | `true` permits other products' correlated surprises; requires the target's internal history and own EEX, with statistics kept separate by mode |
| `arbitrage` | `false` | `true` enables final reconstruction from available own/estimated contracts when no EEX reference can price the target or the unadjusted-EEX fallback cannot be smoothed |

Layer switches do not remove original rows. `hist = off` does not itself disable cross:
cross may use an internal historical baseline for surprises, without adding that mean to the
price. Selecting additive in auto does not enable a disabled local/history/cross layer.

To enable final contract reconstruction, change `arbitrage = true` in the existing `[layers]`
section. With the default `false`, EEX reference pricing still uses `exact`, `strip` and
`residual`; only the later reconstruction from own/already estimated contracts is disabled.
Historical adjustments are a **basis relative to EEX**, so they require a usable EEX reference
and permitted history. This sequence follows available information, not a ranking of which
method is inherently better.

`own_equivalent_period` (own reused) is separate: it supplies an equivalent tenor label from
an own price for the same curve identity, reference date and delivery period. It neither
reuses yesterday's price nor depends on the `arbitrage` switch.

### EEX availability and freshness (`[eex]`)

| Parameter | Default | Effect of changing it |
|---|---|---|
| `offset_days` | `0` | Nonpositive integer calendar-day offset. Only EEX publications dated at or before `reference_date + offset_days` may be used. `0` permits the reference date; `-1` excludes it. |
| `max_stale_days` | `0` | 0 allows any earlier publication; positive N rejects curves more than N calendar days old; never selects future EEX |
| `warn_stale_days` | `3` | Stale-curve messages become ERROR at this age; this threshold logs a problem but does not itself reject the curve or fail the run |

For a curve dated Wednesday 2026-10-07, `offset_days=0` permits the October 7 publication
if supplied; otherwise it uses the latest earlier one. `offset_days=-1` sets the cutoff to
October 6 even if October 7 is in the file. On Monday October 5, `-1` means Sunday October 4:
with no weekend publication, Friday October 2 is selected. This is a calendar offset, not
"one available publication back". Positive values are rejected.

Age is always `reference_date - eex_asof`, **not cutoff minus publication**. The Friday quote
is three days old on Monday; `max_stale_days=2` rejects it, while `0` remains unlimited.
The same cutoff limits the complete EEX price/spread windows. Targets such as `M+1` still
resolve relative to the original reference date, so October's `M+1` remains November even
when the accepted EEX publication is from September.

History and covariance learn only exact same-date pairs `own_h / EEX_h` or `own_h - EEX_h`.
A negative offset delays when those pairs become available: for a prediction on T, the
pair must have `h<T` and publication date `h<=T+offset_days`. It never learns a basis by
pairing today's own price with an older EEX publication. Logs identify the reference date,
cutoff and actual publication; output exposes `eex_offset_days`, `eex_cutoff_date` and
`eex_asof` (with `curve_` prefixes in enriched output).

With a negative offset, LOCAL can compare today's own anchors with the permitted older EEX
reference, and HIST can use released earlier pairs. **CROSS has no usable current-day surprise**:
that branch still requires same-day EEX to avoid treating a publication delay as an own-market
shock. Its coefficients can learn from delayed exact pairs, but it contributes no current
cross adjustment under a negative offset. Learned local correlation weights can still be used.

Set `offset_days` in the existing `[eex]` section, or override it for one run:

```powershell
python run.py daily --date 2026-10-07 --eex-offset-days -1
python run.py backtest --from 2026-09-01 --to 2026-10-06 --eex-offset-days -1
```

The override is available in `daily`, `refill`, `catchup`, `backtest` and `tune`. The experiment
notebook uses `EEX_OFFSET_DAYS=None` to retain the config, or an explicit `0`/`-1` override.
This is a fixed availability scenario, **not a grid parameter**. Compare scenarios in separate
experiments and inspect coverage: excluding a publication can change the evaluable sample.

`catchup` still completes pending groups. If existing filled/enriched target rows in the
requested active scope have a different offset, it stops: explicitly recalculate with
`refill --eex-offset-days ...` or use another output folder. Legacy missing/blank offset
metadata means `0`; unrelated dates, curves and targets outside that scope do not block it.

Availability uses publication dates in your supplied CSV, not an intraday clock or a database
of historical revisions. It cannot prove when a settlement/revision was visible in real time.

### Mapping time zones (`[timezones]`)

| Parameter | Default | Effect of changing it |
|---|---|---|
| `default` | `"Europe/Berlin"` | Default delivery time zone for newly drafted mapping rows |
| `GB` | `"Europe/London"` | Draft override for GB |
| `IE` | `"Europe/Dublin"` | Draft override for IE |
| `PT` | `"Europe/Lisbon"` | Draft override for PT |
| `GR` | `"Europe/Athens"` | Draft override for GR |
| `RO` | `"Europe/Bucharest"` | Draft override for RO |
| `BG` | `"Europe/Sofia"` | Draft override for BG |
| `FI` | `"Europe/Helsinki"` | Draft override for FI |

Other area keys can be added. Existing mappings use their own `timezone` column; changing
this section does not overwrite reviewed mapping rows. Base delivery hours include daylight
saving changes. Peak Day/Weekend uses 12 hours every day; other Peak kinds use Monday–Friday.

### Targets and conventions (`[targets]`, `[conventions]`)

| Parameter | Default | Effect of changing it |
|---|---|---|
| `targets.tenors` | `D+1..D+3`, `WE..WE+3`, `BOW`, `W+1..W+4`, `BOM`, `M+1..M+10`, `Q+1..Q+8`, `Sum+1..Sum+3`, `Win+1..Win+3`, `Cal+1..Cal+3` | Global target list; individual mode can override it per curve. Original rows outside the effective list remain preserved. |
| `conventions.day` | `"calendar"` | `"business"` interprets D+n as Monday–Friday business-day offsets; no holiday calendar is supplied |
| `conventions.weekend_offset` | `0` | Shifts own WE+n by this many weekends when resolving delivery periods |

Ranges above abbreviate the supplied list; write individual strings in TOML, as shown earlier.
Adding a target does not guarantee coverage. Use `detect-conventions` to compare interpretations.

### Pricing and history (`[method]`)

| Parameter | Default | Effect of changing it |
|---|---|---|
| `basis_mode` | `"auto"` | Use the per-target tree above, or force `"ratio"` / `"additive"` |
| `min_volume` | `0` | Known volume below this threshold cannot anchor adjustments; missing volume is not below a known threshold; this anchor filter does not change original values |
| `tau_log` | `0.5` | Larger positive values let more distant anchors influence a target |
| `other_kind_weight` | `0.6` | Greater weight for anchors of another contract kind, before any configured between-group correlation adjustment |
| `shrink_k` | `1.0` | Larger positive values lower local weight `w = W/(W+k)`, favouring allowed history or zero adjustment if no prior exists |
| `ewma_halflife_days` | `10` | Larger positive values slow ratio/additive history updates; measured in valid observation days, not elapsed days |
| `max_anchor_dev` | `0` | 0 disables this common filter; positive N excludes anchors with `abs(Own−EEX)/max(abs(EEX),ratio_eex_floor) > N`; within positive values, increasing N admits more deviations |
| `ratio_eex_floor` | `1.0` | Positive minimum abs(EEX) for ratio anchors; in auto, targets below it use additive. It is in price units and never edits EEX |
| `max_ratio_deviation` | `1.0` | Positive maximum abs(Own/EEX−1) for ratio anchors and absolute final ratio adjustment; not an additive price cap |
| `hist_max_age_days` | `60` | Positive calendar-day expiry: a kind/group/global mean is dropped after more than this many days since its last valid own observation |
| `hist_auto_min_obs` | `10` | Effective comparison mass before hist-auto decides; before it, current history is allowed. Higher values delay the decision |

Both historical models learn from original anchors with same-day EEX, after that day's
prediction. Filled estimates never train history. When a kind's mean is unavailable, the
same mode can fall back to its current group/global mean. Near-zero ratio exclusions do
not discard an otherwise valid additive observation. Review units and use real backtests
before changing bounds; automatic mode does not establish the predictive accuracy of a large additive adjustment.

### Correlation (`[correlation]`) and cross-product assistance (`[cross]`)

| Parameter | Default | Effect of changing it |
|---|---|---|
| `correlation.halflife_days` | `20` | Larger values retain paired surprise evidence longer; updates discount the calendar gap since the previous pair |
| `correlation.prior_obs` | `8` | Larger values keep weights closer to `other_kind_weight` for longer before measured correlation dominates |
| `cross.min_corr` | `0.5` | Higher correlation required to admit a helper product |
| `cross.min_obs` | `8` | Higher minimum effective joint observation mass before a helper can contribute |
| `cross.halflife_days` | `20` | Memory for helper correlation and β, discounted by calendar gaps between pairs |

These settings affect predictions when the corresponding layer is enabled. Eligible helpers
are mapped `fill` or `helper` products. Surprises, covariances and β remain separate by mode.

### Warmup (`[run]`)

| Parameter | Default | Effect of changing it |
|---|---|---|
| `configuration_mode` | `"global"` | `global` ignores mapping model cells; `individual` applies nonempty per-curve values. See [CONFIGURATION.md](CONFIGURATION.md). |
| `warmup_days` | `0` | 0 rebuilds from all prior original history supplied; positive N restricts warmup to N calendar days and can make a daily run differ from a longer refill |

This setting does not retrieve missing files. Supply accumulated originals and the matching
EEX history on the destination machine.

### Input column assignments (`[vwap_columns]`)

| Parameter | Default input column | Effect of changing it |
|---|---|---|
| `reference_date` | `"reference_date"` | Read observation dates from the named column; mandatory and dates must parse |
| `product` | `"product"` | Read product names, the first identity component; mandatory and cannot be empty |
| `region` | `"region"` | Read the exact region identity component; column mandatory, blank value literal |
| `unit` | `"unit"` | Read the exact unit identity component; column mandatory, no automatic conversion or wildcard |
| `tenor` | `"tenor2"` | Read relative delivery labels; mandatory |
| `vwap` | `"vwap"` | Read observed prices; column mandatory, invalid/empty values retained for enrichment |
| `volume` | `"total_volume"` | Read anchor volumes; the column can be absent |

Assignments change how columns are interpreted; original columns are retained in enriched output.

## Generate and review the mapping

Run once on the destination machine:

```powershell
python run.py mapping
```

Review `mappings/products.csv` before filling. **Every newly drafted row starts with `use = off`,
even when an EEX file is found**. Activate the intended identities explicitly: `fill` calculates
that curve, `helper` supplies assistance without its own calculated output, and `off` excludes
it from calculations. Check each `eex_file`, identity/profile correspondence, `hours` and
`timezone`. Input and EEX currency/units must agree; the program does not convert them.
Unmapped identities are reported and not processed. Their originals remain in enriched output.

`fill`/`helper` also require a nonempty `eex_file`: without an assigned file the identity is
excluded and flagged `mapping_unassigned`, even if `use` is enabled. This differs from an
assigned path whose file is absent or has no quotes for the date: that identity is active,
the missing data is reported, and own VWAPs remain usable. Final contract reconstruction
also remains available if `arbitrage` is explicitly enabled.

**The mapping is editable by hand**, in a text editor or Excel. Its header is
`product,region,unit,use,area,profile,eex_file,hours,timezone,comment`. Use the input's exact
three-component identities and comma-separated UTF-8 CSV format; `eex_file` is relative to
the configured POWER directory. The same product name may have multiple rows when region/unit
differ. Duplicate complete identities are rejected. Running `mapping` again preserves existing
decisions and adds new identities as `off`.

An older mapping missing region/unit columns can be migrated by `mapping` only when every
old row has exactly one compatible identity in the input. Existing EEX choices and other
settings are retained. Zero or multiple candidates cause an error and leave the file unchanged;
add explicit rows manually. A present but blank identity value remains literal, not an
unspecified match. Input files themselves must contain the configured region/unit columns.

Any AT/DE/FR/NL demonstration mapping is a test example, not a complete market list. Generate
and review identities from the destination machine's input. Unmapped and `off` originals remain
in the enriched output, without generating estimated curves for them.

GB input blocks such as `GB_Other_Block_1_2` or `GB_Other_Block_3_4` have no implemented block
calendar/source mapping and stay `off` in the draft. `TB2` does not mean “block 2”; do not map
these rows blindly to TB2, Base or Peak. They require an exact definition of delivery hours and
a matching price source before support can be added.

## Run

After setting the paths in `config.toml` on the machine where the source and real data reside:

```powershell
python run.py mapping
python run.py daily
python run.py catchup
python run.py refill --from 2026-09-01 --to 2026-09-30
python run.py daily --date 2026-09-30
```

`daily` and `refill` share the same engine. Keep `warmup_days = 0` and supply accumulated
originals to rebuild the same historical state. Today's observations alone lose that memory.
Reruns replace results by date/product/region/unit, retaining other identities, including
other regions/units with the same product name.

Older result files without `region`/`unit` (calculated curves) or `curve_region`/`curve_unit`
(enriched output) are rejected before result writes. Set `output_dir` to a new directory and
rebuild with `refill`; old identities are not guessed or silently merged. Logs can still be written.

### Choose among the three uses

| Need | Command | Scope |
|---|---|---|
| Calculate today or revisit a date | `daily` or `daily --date YYYY-MM-DD` | Recalculates the date even if results exist |
| Recover pending days/curves | `catchup` | Only pending date/identity fill groups through today |
| Rebuild an interval or enrich all its input | `refill --from YYYY-MM-DD --to YYYY-MM-DD` | Recalculates the requested range |

Catchup starts at the earliest available input/EEX date unless `--from` is supplied; without
dates it requires an explicit start. `--to` defaults to today; future ends and reversed ranges
are rejected. Candidates include business days and observed active-input/EEX dates, including
weekends. A group is pending if a resolvable target is absent from **either** calculated or
enriched history. A present `missing` record already counts as processed. The full pending
curve is recalculated and complete groups are preserved. Use daily/refill to refresh those
with new quotes. Without active fill curves or pending groups, it does not write results.

Catchup initially exports only originals from pending fill groups, not helper/off/unmapped
rows. Daily/refill enrich all originals in the range. All three rebuild memory from accumulated
originals, never stored estimates. Historical and affected output schemas are validated before
writing results.

### Command-line options

| Option | Commands / default | Effect |
|---|---|---|
| `--config PATH` | Optional global option; default `config.toml` | Advanced override of the single normal config; goes **before** the command |
| `--vwap PATH_OR_PATTERN` | `mapping`, `daily`, `catchup`, `refill`, `backtest`, `tune`, `detect-conventions`; defaults to configured input | Override input for this run; quote patterns with wildcards |
| `--date YYYY-MM-DD` | `daily`; defaults to the machine's local date | Select one reference date |
| `--eex-offset-days N` | `daily`, `refill`, `catchup`, `backtest`, `tune`; defaults to `[eex].offset_days=0` | Nonpositive calendar-day publication cutoff offset; `-1` excludes same-date EEX. Fixed scenario, not a tuning dimension. |
| `--from YYYY-MM-DD`, `--to YYYY-MM-DD` | `refill`, `backtest`; defaults inferred from available data | Restrict the requested date range; earlier data can still warm up history |
| `--from YYYY-MM-DD`, `--to YYYY-MM-DD` | `catchup`; inferred start, end today | Recover pending groups only; future end and reversed ranges rejected |
| `--last N` | `status`; default 10 | Number of dates to display |
| `--truth PATH` | `backtest`; absent by default | Compare calculated prices with a matching synthetic ground-truth file |
| `--areas LIST` | `make-synthetic`; default `DE,FR,NL,AT` | Comma-separated synthetic product areas |
| `--profiles LIST` | `make-synthetic`; default `Base,Peak` | Synthetic profiles |
| `--from`, `--to` | `make-synthetic`; defaults 2000-01-01 to 2100-01-01 | Filter available EEX dates for synthetic generation |
| `--seed N` | `make-synthetic`; default 7 | Synthetic random seed |
| `--mode ratio` or `--mode additive` | `make-synthetic`; default ratio | Choose the synthetic data-generating model; **does not override filling `basis_mode`** |
| `--out PATH` | `make-synthetic`; default `data` | Synthetic output directory |

For example, `python run.py daily --date 2026-09-30 --vwap "C:/data/vwaps_*.csv"` uses the
normal config with another input. An optional custom config is written
`python run.py --config C:/settings/custom.toml daily --date 2026-09-30`; no second config is
required for ordinary use. Use dates actually covered by the destination machine's files.

## Read the output

The main deliverables are `output/enriched_history.csv` and `output/enriched/YYYY-MM-DD.csv`.

| Column | Meaning |
|---|---|
| `curve_price` | Final usable price. Read this column when an original `vwap` was blank or invalid. |
| `data_origin` | `original`, `estimated` or `missing`. |
| `estimation_method` | For example `ratio_local`, `ratio_local_history`, `additive_local`, `eex_price_ewma`, `contract_strip`, `contract_residual` or `none`. |
| `curve_row_type` | `original`, `original_invalid` or `added`. |
| `curve_product`, `curve_region`, `curve_unit` | Complete normalized curve identity used in matching; raw input values remain intact. |
| `curve_basis_mode` | Selected mode; does not imply a basis formula on originals or `eex+smooth` fallback. |
| `curve_configured_basis_mode` | Requested setting: `auto`, `ratio` or `additive`. |
| `curve_flags` | Includes the auto-selection reason when additive was selected for an EEX estimate. |
| Other `curve_*` columns | Reference keys, sources, calculation details and flags. |

Original values, duplicates and tenors outside the configured targets are preserved. An invalid original `vwap` is not overwritten; its estimate appears in `curve_price`. Added rows carry their estimate in both columns. Unresolvable cells stay `missing`, with no fabricated price, volume or trade count.

`filled_history.csv`, `filled/` and `consistency_history.csv` provide the calculated curve and diagnostics. A detected internal calculation failure returns a nonzero exit code without replacing result CSVs; inspect `output/_logs/`.

## Validate

```powershell
python -B -m pytest -p no:cacheprovider
python run.py backtest
```

Tests and synthetic data validate the mechanics; they do not establish accuracy on your real market data. This is a moderately complex baseline using own observations, EEX, local/history adjustments and optional final contract reconstruction. Keep `cross = false`, `correlation = false` and `arbitrage = false` until real-data validation supports enabling those layers. The linked algorithm documents describe the calculations and their limits.

Backtest pairing uses date/product/region/unit/tenor. Error reports and convention checks
separate units, so EUR/MWh and GBP/MWh errors are not pooled. Compare `pipeline_configured`
with EEX on the same observations using `n_paired` and `mae_improvement`.


## Select parameters against real originals: `tune`

The [backtest, curve-shape and local-alternatives guide](BACKTEST.md) separates implemented
evaluation from proposed improvements, explains parameter families and records the investigation
of shapes introduced by filling.

**Prediction errors are measured against your hidden own VWAP, not against EEX.** EEX is an
input and comparison baseline. Example: actual own price 120, EEX 100; candidate A predicts
102 (error 18), candidate B predicts 118 (error 2). B is better even though it moves farther
from EEX. The program does not optimize resemblance to the EEX curve.

For each evaluated observation, it hides that own point, predicts it with the configured
pipeline and compares with the original value that actually existed. It does not hide the
entire daily curve. Originals and EEX must overlap in dates and delivery periods; a similar
scraper without that overlap is insufficient. The command reports or fails on insufficient
paired evidence rather than inventing metrics for dates without comparable data.

```powershell
python run.py tune
python run.py tune --from 2026-08-01 --to 2026-09-30 --validation-days 10 --basis-modes auto,ratio,additive --tau-log 0.3,0.5 --shrink-k 0.5,1,2 --max-trials 50
```

Dates are examples: use the coverage of originals and EEX on the real-data machine.
**Requires `run.warmup_days=0`**; the command does not change it automatically. Reserving
10 dates requires at least 12 dates with eligible own observations, and at least two
calibration dates with actual paired comparisons. The example tests 18 combinations.

| Tune option | Default | Effect |
|---|---|---|
| `--from`, `--to` | First/last input date | Interval split into calibration and validation; earlier history remains available |
| `--vwap PATH_OR_PATTERN` | Configured input | Select accumulated originals for this evaluation |
| `--basis-modes` | `auto,ratio,additive` | Comma-separated candidate formulas |
| `--tau-log` | Configured value only | List of finite positive local reaches, such as `0.3,0.5` |
| `--shrink-k` | Configured value only | List of finite positive shrinkage values, such as `0.5,1,2` |
| `--hist-modes` | Configured value only | List of `auto,on,off` history settings |
| `--correlation` | Configured value only | List `off,on` to compare the weighting extension |
| `--cross` | Configured value only | List `off,on` to compare cross-curve assistance |
| `--validation-days` | `20` | Positive number of latest distinct eligible observation dates reserved; not consecutive calendar days |
| `--max-trials` | `50` | Positive Cartesian-product limit; an oversized grid is rejected rather than truncated |

Only **those six model settings** are explored. Other controls retain their configured values.
Defaults test three modes while keeping the other five fields fixed. Changing settings such
as expiry or volume requires another explicit comparison; tune does not automatically search
the entire configuration inventory.

**How it selects: coverage first, then comparable error.**

1. Sort eligible own-observation dates for active fill curves and reserve the latest N for
   validation. Calibrate every combination on earlier dates. Holdout does not select the winner.
2. Define an identical universe with hidden own VWAP and available EEX benchmark. Cases without
   an EEX benchmark are outside this relative objective. For each candidate report
   `n_baseline`, `n_available` (finite predictions), `n_missing=n_baseline−n_available` and
   `coverage=n_available/n_baseline`. Incomplete-window abstentions remain visible rather
   than silently disappearing from the denominator.
3. Only candidates with **maximum calibration coverage** are eligible. Measure accuracy on
   the **intersection of predictions from every candidate**, including lower-coverage ones:
   exactly the same cases for all. `n_paired` may be smaller than `n_available`. At least
   two distinct dates must remain in that intersection; otherwise abort and review data/grid,
   rather than compare different samples.
4. Among maximum-coverage candidates, minimize the mean per-curve `MAE_model/MAE_EEX`, both
   against the **same hidden own VWAP**. Curves have equal weight. Score 1 matches EEX;
   0.7 means 30% less mean normalized per-curve error, not 30% less pooled currency MAE.
   A perfect EEX baseline gives ratio 1 if the model is also perfect, otherwise infinity.
   Coverage and score ties favor the first grid candidate.
5. Evaluate **only the winner** on later dates with fixed parameters. Memory updates causally
   using originals from earlier dates, including earlier holdout dates. Score uses its
   available pairs while coverage uses the full EEX universe. No predictions means coverage 0,
   `n_paired=0` and unassessable errors/score (empty/NaN in CSV, null in JSON); no invented score
   or hidden coverage failure. Incomplete windows are abstentions; internal errors still abort.

For example, a candidate covering 90 of 100 cases takes precedence over one covering 80,
even if the latter has a lower error. Between two covering 90, compare score on the common
intersection of the entire grid. Read **coverage and accuracy together** rather than assume
the smallest error alone identifies the winner. All five `eex_fallback` settings stay fixed
during search, including CLI overrides; they are not new search dimensions.

Writes `output/tuning_calibration.csv`, `tuning_validation.csv` and `tuning_selected.json`.
The CSVs report score, `normalized_skill=1-score`, paired counts and MAE/RMSE/bias by unit;
absolute EUR/MWh and GBP/MWh errors are not pooled. JSON records proposed changes, the full
base configuration, input pattern and metadata with planned and actually paired dates.
Retain input, EEX and mapping files too: paths/configuration alone do not preserve their contents.

**Does not edit `config.toml` or curve histories.** Review validation and, if accepted for
business use, manually copy selected values into existing configuration sections. Failure
of any candidate aborts evaluation rather than publishing a partial selection.

This is the best candidate **among those tested, at maximum calibration coverage and best common-case score**, not a future guarantee.
Validation hides points that actually had observations: it may favor liquid periods and
does not establish performance when an entire daily curve is missing, or reveal truth for
actual unobserved gaps. Do not repeatedly tune against the same reserved period and then
present it as independent evidence. Reserve fresh dates for later changes.

Missing, nonnumeric or nonfinite EEX settlements are excluded with a warning while valid quotes remain. A present corrupt file or a nonempty EEX source with no usable settlements aborts publication. A nonfinite calculated price also fails the run. Absent files and valid header-only datasets retain the documented no-EEX behavior; rerunning with less evidence can replace earlier estimates.

<a id="shape-layer"></a>

## Optional shape layer: audit or adjust after refill

Implemented and disabled by default (`shape.mode="off"`). It jointly limits changes to the
refill, regularizes second differences of monthly `price - eex_settle`, and reduces
hours-weighted Quarter/Year-versus-month discrepancies. It uses current accepted raw EEX
references with a shared publication date for each consecutive three-month smoothness term;
it does not use fallback EWMA as a template. A complete aggregate can contribute without EEX.

Only finite full Month/Quarter/Year prices participate. Quarters need all three months and
years all twelve; no missing price is invented. Original labels of these kinds can be included
outside the configured targets while shape is active. Aliases are deduplicated. Defaults keep
own prices fixed. `adjust_originals=true` permits bounded movement with greater fidelity
weight, but original input columns remain intact. `curve_price` is the final usable price;
an adjusted original becomes `estimated`, source `own+shape`, method `shape_adjusted_original`.

`audit` records proposals without changing final prices; `adjust` applies a validated bounded
solution. Aggregate penalties are soft, not exact equalities. Conflicting observations or
bounds can leave residuals. Solver failure retains prior prices. Changed prices have empty
confidence; previous basis/anchor diagnostics explain the pre-shape result. There is no
positivity rule, no automatic history training from adjusted prices and no guarantee of
better prediction or continuity at calendar rollover.

### Complete [shape] configuration

These seven entries bring the supplied configuration to 60 keys. Defaults are uncalibrated
starter values. Detailed formulas, examples, solver diagnostics and history needs are in
[SHAPE.md](SHAPE.md).

| Key | Default | Meaning and effect |
|---|---|---|
| `shape.mode` | `"off"` | `off`, `audit` or `adjust`. Other controls have no price effect while off. |
| `shape.adjust_originals` | `false` | Boolean. True permits eligible own curve prices to move; raw input columns still remain untouched. In audit only proposals move. |
| `shape.smoothness_weight` | `1.0` | Finite, ≥0. Higher favors smaller second differences in monthly Own-minus-EEX adjustment. Zero removes this penalty. No effect where a valid three-month reference is unavailable. |
| `shape.coherence_weight` | `10.0` | Finite, ≥0. Higher favors closer hours-weighted aggregate agreement. Zero removes this penalty. No effect on incomplete aggregates. |
| `shape.coherence_tolerance` | `0.01` | Finite, ≥0. Diagnostic absolute aggregate-residual margin in the curve's price unit. Larger relaxes the reported test; it does not change prices or impose a hard constraint. No overall verdict without applicable aggregate terms. |
| `shape.max_abs_adjustment` | `10.0` | Finite, >0. Maximum total absolute price movement per node. Larger allows more changes, not necessarily better estimates. Check the curve's unit; no currency conversion occurs. |
| `shape.original_weight` | `10.0` | Finite, ≥1. Fidelity weight for movable originals versus 1 for estimates. Higher resists moving originals. Has no movement effect while originals are fixed. |


All seven flags work on `daily`, `refill`, `catchup`, `backtest` and `tune`:
`--shape-mode off|audit|adjust`, `--shape-adjust-originals on|off`,
`--shape-smoothness-weight`, `--shape-coherence-weight`, `--shape-coherence-tolerance`,
`--shape-max-abs-adjustment`, `--shape-original-weight`. They override this run only.

```powershell
python run.py daily --date 2026-09-30 --shape-mode audit
python run.py refill --from 2026-09-01 --to 2026-09-30 --shape-mode adjust --shape-adjust-originals off
python run.py daily --date 2026-09-30 --shape-mode adjust --shape-adjust-originals on --shape-original-weight 20
```

Shape needs no extra historical window for a same-date solve; refill's own/EEX requirements
still apply. Calibrate its strengths on real historical evaluation. Backtest
`pipeline_configured` includes the layer after hiding the test period; tune still varies
only its existing six controls and keeps shape fixed in the saved configuration snapshot.
Recalculate existing groups with daily/refill after changing settings; catchup does not
reprocess an already present missing row. See [OUTPUT.md](OUTPUT.md#shape-output) for all
active columns and before/after/proposed consistency diagnostics.

## Interactive output notebook

[NOTEBOOK.md](NOTEBOOK.md) explains the portable notebook for selecting a curve/date and
viewing a date range. It reads `filled_history.csv` from the configured output directory;
the original VWAP input file is not required to inspect existing output. Charts compare
pre-shape/final prices and EEX, and align absolute delivery periods by default; relative
rolling labels are a separate choice and can refer to different contracts across dates.

```powershell
uv sync --group notebook
uv run --group notebook jupyter lab notebooks/inspect_curves.ipynb
```

**Coherence tolerance is a diagnostic, not a hard constraint.** `shape.coherence_tolerance=0.01`
compares the absolute monthly-average-minus-parent residual with 0.01 in the curve's price
unit. Increasing it relaxes the reported test; decreasing it tightens it. It changes neither
the optimizer nor the accepted price. Soft penalties, protected originals and movement
bounds can leave a result outside tolerance. Audit flags the proposal; adjust flags the
published result. The trace reports each aggregate test and the overall result, or null
when there is no applicable aggregate. The same numeric setting is interpreted in each
curve's own unit; it is not a percentage or a currency conversion.

This hours-based layer is specific to the supported power contracts. It must not be transferred to agricultural or other asset curves without validating their delivery definitions; see [scope and the observed EEX sample](SHAPE.md#why-this-is-power-specific-and-what-the-eex-sample-showed).

## Code review and reliable execution

[CODE_REVIEW.md](CODE_REVIEW.md) records reproduced defects, fixes, checks and remaining limits.
Alias aggregation, validation, output protection and scoring have been strengthened.
[Algorithm section 18](ALGORITHM.md#18-determinism-and-operational-integrity) explains the
current operational rules. Passing tests does not demonstrate accuracy on real missing prices.

The notebook starts in **Full curve (all contract types)**, showing every saved contract for the selected curve
and date. To recreate the nine-family example on this or another computer, run
`uv run python examples/generate_notebook_demo.py`, restart the kernel and execute every cell.
The generated CSV is synthetic and is not published in GitHub.
