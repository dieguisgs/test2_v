# Output dictionary

Spanish version: [OUTPUT.es.md](OUTPUT.es.md). Calculation rules: [ALGORITHM.md](ALGORITHM.md). Optional final layer: [SHAPE.md](SHAPE.md).

All curve keys use **reference date + product + region + unit + tenor label**. A blank region or unit is a literal identity value. Prices and absolute errors use the row's `unit`; currencies are not converted. CSV files use UTF-8 with BOM, comma separators and a header. Empty numeric/date cells mean unavailable or not applicable, not zero. Zero and negative prices are valid finite values.

## Files

Paths below are relative to `[paths].output_dir`. Files appear when the corresponding command has results; an empty consistency result need not create a file.

```text
output/
  enriched_history.csv          Original rows plus added points across processed dates
  enriched/YYYY-MM-DD.csv        Same enriched schema for one reference date
  filled_history.csv            Engine target points across processed dates
  filled/YYYY-MM-DD.csv          Same engine schema for one reference date
  consistency_history.csv       Available aggregate-versus-parts comparisons
  backtest_loo.csv               Individual held-out predictions
  backtest_report.csv            Backtest metrics
  tuning_calibration.csv         Every candidate's calibration scores
  tuning_validation.csv          Selected candidate's later-date scores
  tuning_selected.json           Proposed parameters and evaluation metadata
  configurations/<id>.json       Calculation-context documents referenced by curve rows
  _logs/vwaps_YYYY-MM-DD.log      Command execution log
```

`daily` and `refill` preserve original rows, including unmapped/off/helper curves, in enriched output. `catchup` updates only pending active `fill` date/curve groups; already complete groups remain intact. It does not add raw rows of unrelated curves. A processed target with `source=missing` is already processed. Recalculation replaces the selected date/curve groups in histories, rather than appending another version. There is no `latest.csv` file.

Catchup rejects existing filled/enriched target rows with a different `eex_offset_days` in
its requested active date/curve/target scope. A missing or blank legacy offset means zero.
Use explicit refill with the intended offset or another output directory to change that
policy; catchup does not silently relabel or recalculate previously completed prices.

The `make-synthetic` command writes `synthetic_vwaps.csv` and `synthetic_truth.csv` to its selected destination (see below). Logs are text records with timestamp, severity and message, not a tabular price output.

## Enriched output: the original table with provenance

`enriched_history.csv` and daily enriched files begin with **every original input column**, in its original spelling and order. Original row values and duplicate rows are retained. The physical columns for the logical fields `reference_date`, `product`, `region`, `unit`, `tenor`, `vwap`, `volume` follow `[vwap_columns]`; for example `tenor2` and `total_volume` can be the configured input names. A source index column or any custom field is retained too. These arbitrary fields cannot have a fixed universal dictionary: their original meaning remains the supplier's meaning.

| Input field or category | Original row | Newly added row |
|---|---|---|
| Reference date | Original representation retained; ISO and day/month/year parse consistently | Formatted following the first usable input date representation |
| Product, region, unit | Original values retained | Exact normalized curve identity |
| Tenor (`tenor2`, if configured) | Original label retained, even outside targets | Configured target label |
| VWAP (`vwap`, if configured) | Original value retained, including invalid text or an empty value | Same price as `curve_price`, or empty if missing |
| Volume (`total_volume`, if configured), `n_trades`, transaction IDs, index/custom fields | Preserved | Empty; never invented from prices or copied from another trade |
| `country`, `classification`, `currency`, `area`, `profile` | Preserved | Copied only when that field has one distinct nonempty value in the original data for this complete curve identity |
| `weekday`, if supplied | Preserved | English weekday of the reference date |
| `periodicity_2`, if supplied | Preserved | `Daily`, `Weekend`, `BOW`, `Weekly`, `BOM`, `Monthly`, `Quarterly`, `Seasonal` or `Annual`, according to target kind |

Input names `data_origin`, `estimation_method` and names starting with `curve_` are reserved and rejected to prevent silent overwriting.

### Enrichment-specific columns

| Column | Type and values | Meaning |
|---|---|---|
| `data_origin` | Text: `original`, `estimated`, `missing` | Origin of the usable `curve_price`. A finite original remains `original` unless shape explicitly changes its usable price, in which case it becomes `estimated`; its raw VWAP is still retained, including zero/negative values. An invalid original row can have an estimated price in `curve_price`. |
| `estimation_method` | Text; method rules below | `none` on every unchanged valid original and every unavailable enriched price; `shape_adjusted_original` on an original price changed by shape; otherwise the method used to supply that price. |
| `curve_reference_date` | ISO date | Parsed reference date, independent of the original date's formatting. |
| `curve_product`, `curve_region`, `curve_unit` | Text | Normalized curve identity. |
| `curve_tenor` | Text | Original label for an original row; target label for an added row. |
| `curve_price` | Number or empty | Usable original, estimated or missing price. Use this field when the original VWAP column contains invalid text. |
| `curve_source` | Engine source enum below | `own` for an unchanged valid original row, even without an engine calculation; `own+shape` when its curve price was adjusted. Otherwise the engine source, or `missing`. |
| `curve_row_type` | Text: `original`, `original_invalid`, `added` | Whether the physical row already existed, contained an unusable VWAP, or was appended. Independent of price origin. |
| `curve_flags` | Semicolon-separated text or empty | Engine flags plus enrichment flags, listed below. |

**Every other engine column in the next section is copied with `curve_` prefixed**, except `data_origin` and `estimation_method`, which use the enrichment semantics above. Thus the full normal trace also includes `curve_area`, `curve_profile`, `curve_kind`, `curve_period`, `curve_delivery_start`, `curve_delivery_end`, `curve_hours`, `curve_confidence`, `curve_basis_mode`, `curve_configured_basis_mode`, `curve_configuration_mode`, `curve_configuration_id`, `curve_configuration_parameters`, `curve_own_vwap`, `curve_own_volume`, `curve_eex_settle`, `curve_eex_method`, `curve_eex_asof`, `curve_eex_offset_days`, `curve_eex_cutoff_date`, `curve_eex_fallback_trace`, `curve_basis`, `curve_basis_local`, `curve_basis_hist`, `curve_cross_adj`, `curve_local_weight`, `curve_anchors`, `curve_cross_from` and **`curve_flag`**. If no engine rows were produced, only the fixed enrichment columns are guaranteed; optional trace columns depend on the engine table supplied.

`curve_flag` is the engine's singular `flag` copied unchanged. `curve_flags` is the combined enrichment field. Do not confuse them. On valid original rows, `curve_price`, `curve_source`, and, when present, `curve_own_vwap`/`curve_own_volume` describe that exact original row. An enabled original shape adjustment changes only the usable curve price and its provenance; raw observations and own diagnostics remain available. Other trace fields describe the matching engine point and can be empty, or refer to an aggregate of repeated original delivery periods. The presence of an original row does not imply that all diagnostics were computed.

Illustrative rows, not actual market observations; all three use the same EUR/MWh curve. Here `tenor2` is the configured tenor column:

| `tenor2` | Original/output `vwap` | `curve_price` | `data_origin` | `curve_row_type` | `curve_source` | `estimation_method` |
|---|---:|---:|---|---|---|---|
| M+1 | 100 | 100 | original | original | own | none |
| M+2 | 105 | 105 | estimated | added | eex+local | ratio_local |
| M+3 | invalid | empty | missing | original_invalid | missing | none |

The third row also receives `original_vwap_missing_or_invalid`. If a price were available for it, its original `vwap` would still read `invalid`; `curve_price` would hold the estimate and `data_origin` would become `estimated`.

## Filled output: 39 base engine columns

These fields are written to `filled_history.csv` and daily filled files. There is one row per resolvable configured target label and active `fill` curve/date; active shape also includes observed Month/Quarter/Year labels needed as context. An unresolvable target produces no row. Different labels can resolve to the same delivery period.

| Column | Type / values | Meaning and empty cases |
|---|---|---|
| `reference_date` | ISO date | Valuation/input observation date. |
| `product` | Text | Exact normalized mapped product identifier. |
| `region` | Text, possibly blank | Literal region component of curve identity. |
| `unit` | Text, possibly blank | Literal unit component of curve identity; price/error unit. |
| `area` | Text | Mapping's market area label. |
| `profile` | Text | Mapping's profile label, commonly `Base` or `Peak`. Delivery hours follow the mapping's separate hours convention. |
| `tenor` | Text | Configured relative label, such as `M+1`, `WE`, `BOM`, `Q+1`, `Cal+1`. |
| `kind` | Text | `Day`, `Weekend`, `BOW`, `Week`, `BOM`, `Month`, `Quarter`, `Season`, `Year`. |
| `period` | Text | Absolute delivery label: date, `WE YYYY-Www`, `YYYY-Www`, `YYYY-MM`, `YYYY-Qn`, `Sum-YY`, `Win-YY/YY`, `Cal-YYYY`, or `BOW/BOM start..last-day`. |
| `delivery_start` | ISO date | Inclusive delivery start. |
| `delivery_end` | ISO date | Exclusive delivery end. |
| `hours` | Number, >=0 | Delivery hours under the mapped profile, timezone and target kind; includes applicable calendar/DST rules. |
| `price` | Finite number or empty | Engine price in `unit`; empty with `source=missing`. |
| `source` | Text | `own`, `eex+local`, `eex+hist`, `eex+cross`, `eex+smooth`, `arbitrage`, `missing`; changed shape sources append `+shape` (including `own+shape`); see below. |
| `confidence` | Number, 0..1, or empty after a shape change | Heuristic diagnostic, **not a probability or calibrated accuracy measure**. Own=1, missing=0, arbitrage=0.4. EEX-based values use 0.4 for smoothed EEX or `0.5+0.4*local_weight`, reduced by 0.85 for a non-exact contract and 0.9 for an earlier settlement; rounded to three decimals. |
| `data_origin` | Text | `original` for own, `estimated` for a calculated price (including smoothed EEX), `missing` otherwise. |
| `estimation_method` | Text | `none` for own, `unavailable` for missing, or a calculation method below. |
| `basis_mode` | Text: `ratio`, `additive` | Effective mode selected for this target. On own/missing/eex+smooth rows it does not mean that an adjustment was applied. |
| `configured_basis_mode` | Text: `auto`, `ratio`, `additive` | Requested configuration, before target-specific selection. |
| `configuration_mode` | Text: `global`, `individual` | How model values were resolved; distinct from ratio/additive basis mode. |
| `configuration_id` | Text: SHA-256, 64 hexadecimal characters | Fingerprint of the 34 effective model fields. Equal values share an ID; paths, inputs, code and EEX availability are not part of this hash. |
| `configuration_parameters` | JSON | Complete values of the 33 scalar controls and `tenors`, including inherited settings. Audits this curve's effective configuration; not a candidate grid. |
| `configuration_context_id` | Text: SHA-256 | Fingerprint of the receiver's mapping and operational context and, when CROSS is enabled, active helper configurations. Full document: `configurations/<id>.json` under the configured output directory. |
| `own_vwap` | Number or empty | Own price for this delivery period, potentially an aggregate of duplicate rows/aliases; empty without a usable positive-hour own contract. |
| `own_volume` | Number or empty | Volume associated with that own period; may be aggregated or unavailable. |
| `eex_settle` | Number or empty | EEX price for this delivery period, exact or reconstructed. Not necessarily a directly quoted settlement. |
| `eex_method` | Text or empty | EEX reference method: `exact`, `strip` or `residual`. If smoothing fails and arbitrage succeeds, retains this reference method when present; without a reference it receives reconstruction method. `estimation_method=contract_*` always identifies final arbitrage construction. |
| `eex_asof` | ISO date or empty | Date of the accepted EEX snapshot, no later than `eex_cutoff_date`. Can precede `reference_date`, or exist even if that snapshot cannot price this target. Empty if no snapshot satisfies availability/staleness. |
| `eex_offset_days` | Integer <=0 | Applied calendar-day availability offset. 0 permits a publication on the reference date; -1 excludes it. Does not move target delivery dates. |
| `eex_cutoff_date` | ISO date | Latest permitted publication date: `reference_date + eex_offset_days`. A policy limit, not necessarily a date on which EEX published. Remains meaningful without an available EEX price. |
| `eex_fallback_trace` | JSON or empty | Full evidence for `eex+smooth`; see JSON dictionary below. Empty in other branches. |
| `basis` | Number or empty | Final adjustment applied to EEX, after any ratio limit. Zero is a real adjustment of zero, distinct from empty on own/arbitrage/missing/eex+smooth rows. |
| `basis_local` | Number or empty | Today's local anchor adjustment before blending; empty when unavailable or disabled. |
| `basis_hist` | Number or empty | Historical adjustment actually admitted by the history policy; empty when unavailable, expired, disabled or rejected by the automatic skill gate. |
| `cross_adj` | Number or empty | Cross-curve adjustment from eligible current surprises and learned relationships; empty when absent. |
| `local_weight` | Number, 0..1, or empty | Local weight in the blend; `W/(W+shrink_k)` when local evidence exists, otherwise zero. Empty when no EEX adjustment was calculated. |
| `anchors` | Comma-separated text or empty | Labels of the **top three anchor entries by local weight**, not every contributing anchor. An entry can itself combine alias labels. No weights or complete lineage are exported here. |
| `cross_from` | Comma-separated text or empty | Used helper/other curve labels with correlation rounded to two decimals: `product [region='...', unit='...'](0.85)`. Not a table of beta coefficients or weights. |
| `flag` | Semicolon-separated text or empty | Engine conditions listed below. Anchor exclusion alone does not change own prices; an explicitly enabled shape stage can. |

For `basis`, `basis_local`, `basis_hist` and `cross_adj`, **ratio values are fractions**: `0.05` means +5%, and `price = eex_settle * (1 + basis)`. Additive values use the price unit: `0.05` means +0.05 EUR/MWh on an EUR/MWh curve, and `price = eex_settle + basis`. Never combine adjustments from different modes as if they shared units. `local_weight` is dimensionless in both modes. Own values need not have populated basis diagnostics. With shape active these formulas describe `price_before_shape`; the final price additionally includes `shape_adjustment`.

Example: a Monday 2026-10-05 row with offset -1 has `eex_cutoff_date=2026-10-04`. If the
latest permitted quote is Friday, `eex_asof=2026-10-02` and its age is **three** days relative
to `reference_date`, not two relative to the cutoff. A `max_stale_days=2` limit rejects that
quote. Enriched fields are `curve_eex_offset_days`, `curve_eex_cutoff_date` and `curve_eex_asof`;
rows without matching engine diagnostics may have those fields empty.

### Smoothed EEX fallback audit JSON

`eex_fallback_trace` contains JSON within one CSV cell; enriched output calls it
`curve_eex_fallback_trace`. It is empty outside a completed fallback. Window failure is
identified by `eex_fallback_unavailable`, without inventing a partial calculation.
In Python, use `json.loads(cell)` when the cell is nonempty.

| JSON key | Meaning |
|---|---|
| `reference_date`, `eex_asof` | Calculated date and latest accepted publication |
| `eex_offset_days`, `eex_cutoff_date` | Applied availability policy and latest permitted publication date for both price and spread windows |
| `target` | Absolute target object `kind, delivery_start, delivery_end`; exclusive end |
| `price_method`, `price_window`, `ewma_halflife`, `spread_window`, `anchor_months` | Five effective controls, including overrides |
| `spread_input` | `raw_same_publication_prices`: raw same-publication EEX prices for each difference |
| `price_average.period` | Averaged period: target for direct averaging, last anchor month for a cascade |
| `price_average.value` | Initial weighted average |
| `price_average.observations[]` | One entry per publication, chronologically ordered |
| `observations[].trade_date, price, eex_method, weight` within `price_average` | Date, period price, exact/strip/residual reconstruction and normalized weight; weights sum to 1 |
| `spread_steps[]` | Consecutive monthly cascade links; empty list for direct averaging |
| `spread_steps[].from_period, to_period` | Previous and next absolute periods, each with `kind/delivery_start/delivery_end` |
| `spread_steps[].mean_spread` | Simple mean spread, in price units |
| `spread_steps[].price_before, price_after` | Cumulative price before and after adding this spread |
| `spread_steps[].observations[]` | Publication evidence for this link |
| `trade_date, from_price, to_price` within each spread observation | Shared date and both months' raw prices |
| `from_eex_method, to_eex_method` | How each price was obtained: exact/strip/residual |
| `spread, weight` | Difference `to_price−from_price` and weight `1/spread_window` |

Audit: sum `price×weight` to reproduce `price_average.value`; in a cascade, sum each link's
`spread×weight` and check every `price_after`. The last value must equal `price_before_shape` when shape is active, otherwise `price`.
Do not reconstruct it with `eex_settle*(1+basis)`: the settlement retains the unsmoothed
reference and `basis/local_weight` are empty. See section 16 of [ALGORITHM.md](ALGORITHM.md)
for complete windows, M0/M1, worked examples and limits. Numerical equality with the latest
settlement may happen; it does not mean the settlement was copied as a direct fallback.

### Sources and methods

| `source` | Meaning |
|---|---|
| `own` | Own VWAP for the same delivery period, including supported tenor aliases. |
| `eex+local` | EEX with a local-led adjustment; a historical/cross component can also contribute. |
| `eex+hist` | EEX with a history-led adjustment; local evidence can also contribute. |
| `eex+cross` | EEX with a prior-led adjustment that includes cross-curve information. |
| `eex+smooth` | EEX fallback from complete price averages and, for farther months, spreads. Never a direct settlement copy. |
| `arbitrage` | Optional final reconstruction from available own/estimated contracts when no usable EEX reference exists or its unadjusted fallback cannot be smoothed. Disabled by default; enable with `arbitrage = true` in `[layers]`. Does not guarantee an arbitrage-free entire curve. |
| `missing` | No available estimate, including zero delivery hours. |

The sequence follows available information, not an intrinsic quality ranking. Historical
basis is an adjustment relative to EEX, so it requires a usable EEX reference and permitted
history. `arbitrage = false` does not disable `exact`/`strip`/`residual` construction of that
EEX reference; it disables only the later reconstruction from own/already estimated contracts.
For worked examples of the nine cases, see section 0.9 of [ALGORITHM.md](ALGORITHM.md).

`source` is a summary category. For component detail, use `estimation_method` and the diagnostic columns. Engine methods are constructed exactly as follows:

- `none`: own price; `unavailable`: engine price missing.
- `ratio_` or `additive_`, followed by the available component names in this fixed order: `local`, `history`, `cross`, joined with underscores. For example `additive_local_history` or `ratio_history_cross`. This describes available components in the calculation, not a claim that each numerical contribution is nonzero.
- `eex_price_simple`, `eex_price_ewma`: direct period average. `eex_month_cascade_simple`, `eex_month_cascade_ewma`: anchor-month average plus simple spreads. The suffix describes price averaging, not spread averaging.
- `contract_exact`, `contract_strip`, `contract_residual`: final contract reconstruction when `arbitrage` is enabled. `exact` uses the matching period, `strip` combines contiguous periods by delivery hours, and `residual` extracts the tail of a larger contract using the covered head. Components can be own or already estimated prices.
- Enriched output changes any unavailable method to `none`. A supplied price from `source=own` on an added/invalid original row becomes `own_equivalent_period`; it is marked `estimated` because that physical row did not contain a usable original price. This reuses an equivalent delivery period for the same curve identity and reference date, not yesterday's price; it is not arbitrage and remains available when that layer is off. Every unchanged valid original row has method `none`; shape-adjusted originals have `shape_adjusted_original`.

### Flags

Flags are joined with `;`. An empty field means no listed condition. Several can coexist.

| Flag | Location | Meaning |
|---|---|---|
| `anchor_excluded` | Engine and enriched | Own delivery period was rejected as anchor by configured eligibility checks. Does not replace its original price. In auto mode, ratio-only unsuitability is handled per mode and is not necessarily an exclusion flag. |
| `zero_delivery_hours` | Engine and enriched | Target has no delivery hours under its convention; engine returns missing. Raw original rows remain preserved in enriched output. |
| `auto_additive_low_eex` | Engine and enriched | Auto selected additive because target absolute EEX is below `ratio_eex_floor`. |
| `auto_additive_no_ratio_anchors` | Engine and enriched | Auto selected additive because only additive anchors are usable today. |
| `auto_additive_history_only` | Engine and enriched | No usable anchors today; only additive history is currently usable under the history policy. |
| `eex_fallback_unavailable` | Engine and enriched | Complete EEX fallback window unavailable; missing by default, or final reconstruction if `arbitrage` is enabled. May remain after successful arbitrage. |
| `ratio_adjustment_limited` | Engine and enriched | Final ratio basis was clipped to configured `max_ratio_deviation`. |
| `unmapped_product` | Enriched combined flags | No mapping matches the full product/region/unit identity. |
| `mapping_off` | Enriched combined flags | Matching mapping is off. |
| `mapping_unassigned` | Enriched combined flags | Matching non-off mapping has no assigned EEX file. |
| `mapping_helper` | Enriched combined flags | Matching assigned mapping is helper-only. |
| `original_vwap_missing_or_invalid` | Enriched combined flags | Physical original row's VWAP is empty, invalid or nonfinite. |
| `unit_missing` | Enriched combined flags, added rows | Curve identity has an empty unit; nothing was invented. |
| `metadata_ambiguous:<column>` | Enriched combined flags, added rows | More than one nonempty original metadata value exists for this identity; the field was left empty. `<column>` is one of country, classification, currency, area, profile. |

Auto rationale flags are emitted when an EEX-based estimate takes that branch, not simply because an own row has effective additive mode. Missing/stale EEX also produces log messages; there is no separate stale-EEX flag in this schema. Compare `eex_asof` with `reference_date`.

## Consistency output

`consistency_history.csv` contains only available comparisons: Quarter against months, Season against quarters, Year against quarters. No row means no such comparison was produced, not proof of consistency. Shape-off prices are reported without reconciliation. Active shape applies soft aggregate penalties and adds stages; equality is not guaranteed.

| Columns | Meaning |
|---|---|
| `reference_date`, `product`, `region`, `unit` | Date and full curve identity. |
| `tenor`, `period` | Aggregate target label and absolute period. |
| `price`, `source` | Aggregate price and its engine source. |
| `from_parts` | Delivery-hour-weighted price reconstructed from finer periods. |
| `deviation` | `price - from_parts`, in the curve's unit. |

## Backtest outputs

`backtest_loo.csv` holds individual leave-one-out predictions. Each eligible own delivery period is hidden before the configured pipeline predicts it; diagnostic alternatives can have different availability. Rows share truth but differ by method.

| Column | Meaning / values |
|---|---|
| `reference_date`, `product`, `region`, `unit`, `tenor`, `kind` | Observation date, full identity and held-out contract. `tenor` can combine own alias labels. |
| `eex_offset_days`, `eex_cutoff_date`, `eex_asof` | Fixed availability offset, computed cutoff and actual accepted EEX snapshot for this held-out case. LOO requires an EEX-evaluable own observation. |
| `group` | `short` (Day/Weekend/BOW/Week), `month` (BOM/Month), `quarter` (Quarter), `long` (Season/Year). |
| `own` | Held-out own price, possibly aggregated by delivery period. |
| `volume` | Associated own volume; can be empty. |
| `n_other_anchors` | Other anchors in the configured summary set, not a count of every original row or a per-method weight. |
| `configured_basis_mode` | Configured `auto`, `ratio` or `additive`. |
| `configuration_mode`, `configuration_id`, `configuration_parameters`, `configuration_context_id` | Effective curve configuration and context, with the same meanings as in filled output. These identify the configured pipeline; diagnostic method rows can deliberately bypass some pipeline switches. |
| `method` | `eex`, `pipeline_configured`, or `local_<mode>`, `hist_<mode>`, `blend_<mode>`, `local_corr_<mode>`, `blend_corr_<mode>`, `hist_cross_<mode>` where `<mode>` is ratio/additive. Methods only appear when their branch/data is available. |
| `basis_mode` | Effective ratio/additive mode; blank for baseline `eex`. |
| `pred` | Predicted price in `unit`. |
| `error` | `pred - own`; positive means overprediction. |

`pipeline_configured` applies the actual configuration, including active shape, after hiding the observation and its aliases. The named alternatives are diagnostic comparisons and are not all constrained by the deployed layer switches/guards in the same way.

Negative offsets delay exact-date historical training; they do not pair today's own with
older EEX for learning. Current CROSS surprises still require same-day EEX, so negative
offsets provide no current cross adjustment. See [algorithm section 20](ALGORITHM.md#20-which-eex-publication-is-available-for-each-reference-date).

`backtest_report.csv` groups by `unit`, `method`, `group`; `group=ALL` combines contract groups within that same unit. Every absolute metric remains in its group's unit.

| Column | Meaning |
|---|---|
| `unit`, `method`, `group` | Metric grouping keys. |
| `n` | Number of predictions for that method/group. |
| `mae`, `bias`, `rmse` | Mean absolute error, mean signed error, root mean squared error. |
| `n_paired` | Predictions matched to the EEX baseline on date/full identity/tenor. |
| `mae_paired`, `mae_eex_paired` | Method and EEX MAE on exactly those matched observations. Empty when no paired metric exists. |
| `mae_improvement` | `mae_eex_paired - mae_paired`; positive means improvement over EEX. |

Metrics are rounded to four decimals. Optional `backtest --truth` prints, without a separate saved report, `unit`, `source`, `n`, `mae`, `bias` against synthetic truth, excluding own-priced cells.

## Tuning outputs

`tuning_calibration.csv` reports all finite-grid candidates on earlier dates. `tuning_validation.csv` reports only the selected candidate on reserved later dates. Parameters are fixed before validation; previous original observations can still update history chronologically. Neither output changes production configuration.

Availability is fixed across each grid. Tuning metadata records `eex_offset_days`,
`eex_availability_policy` and `eex_staleness_origin`; the configuration snapshot also includes
`eex_offset_days`. These describe which publications were permitted and that age is measured
from the reference date. Different offsets belong to separate scenario runs, not grid rows.

Both tables contain:

| Column | Meaning / values |
|---|---|
| `trial_id` | Candidate's 1-based position in the grid. |
| `scope` | `overall` or `unit`. |
| `unit` | Unit for scope=unit; blank for overall. |
| `n_baseline` | Universe of own-truth observations with EEX benchmark; identical across calibration candidates. |
| `n_available`, `n_missing`, `coverage` | Finite predictions, abstentions and this candidate’s `n_available/n_baseline`. |
| `eligible_for_selection` | Calibration only: maximum-coverage candidate eligible for score comparison. |
| `n_paired`, `n_curves`, `n_paired_dates` | Matched observations, distinct complete curve identities, and dates with paired observations. |
| `score` | Mean, giving each curve equal weight, of that curve's model MAE / EEX MAE. Lower is better; 1 matches baseline. Baseline MAE=0 gives ratio 1 if model MAE=0, otherwise infinity. |
| `normalized_skill` | `1 - score`; positive means normalized improvement. |
| `reference_date_from`, `reference_date_to` | First and last observation dates allocated to this calibration/validation block, which can be wider than the dates with paired predictions. |
| `basis_mode` | Candidate setting: `auto`, `ratio`, `additive`; not the per-target effective mode. |
| `tau_log`, `shrink_k` | Positive candidate method parameters. |
| `layer_hist` | Candidate history setting: `auto`, `on`, `off`. |
| `layer_correlation`, `layer_cross` | Candidate Boolean switches. |
| `mae_model`, `rmse_model`, `bias_model` | Configured-pipeline absolute errors in the unit on unit rows; empty on overall rows to avoid mixing currency/price units. |
| `mae_eex`, `rmse_eex`, `bias_eex` | EEX baseline errors under the same rule and on the same paired observations. |
| `selected` | Boolean; true for the calibration winner and all its validation rows. |

Selection prioritizes maximum coverage, then minimizes score on the prediction intersection of ALL candidates. `n_paired` may be smaller than `n_available`; two common dates are required. Validation evaluates only the winner on its available pairs. With zero pairs: coverage 0 and unassessable score/errors (NaN/empty), without invented accuracy.

`tuning_selected.json` has these top-level keys:

| Key | Contents |
|---|---|
| `selected_parameters` | Suggested config patch: `method.{basis_mode,tau_log,shrink_k}` and `layers.{hist,correlation,cross}`. |
| `metadata` | Evaluation record detailed below. |
| `base_configuration` | Complete Config snapshot, including resolved paths, field aliases, targets, method/layer parameters and conventions. |
| `input_pattern` | Actual VWAP input path/glob used. |
| `selection` | Text stating that only calibration dates choose parameters and validation follows selection. |
| `config_changed` | `false`; this command never applies the proposal automatically. |

All `metadata` keys:

- `objective` = `mean_per_curve_mae_model_over_mae_eex`; `evaluated_method` = `pipeline_configured`; `baseline` = `eex`.
- `eex_offset_days`: fixed nonpositive calendar-day offset; `eex_availability_policy` = `latest_publication_on_or_before_reference_date_plus_calendar_day_offset`; `eex_staleness_origin` = `reference_date`.
- `parameter_grid`: requested field-to-list grid; `selection_scope`: maximum calibration coverage, then best common-case score within the supplied finite grid; `lower_score_is_better`: true; `tie_break`: `first_candidate_in_grid_order`.
- `calibration_start`, `calibration_end`, `validation_start`, `validation_end`: allocated observation-date boundaries. `calibration_days`, `validation_days`: distinct allocated dates, not calendar-day duration.
- `n_trials`, `selected_trial_id`, `calibration_score`, `validation_score`: candidate count, winner ID and scores.
- `accuracy_scope` = `intersection_of_predictions_from_all_calibration_candidates`; `coverage_denominator` = `identical_eex_baseline_observations_with_held_out_truth`.
- `calibration_n_baseline`, `calibration_n_available`, `calibration_n_missing`, `calibration_coverage` and matching `validation_*` fields: winner coverage and abstentions.
- `calibration_n_paired`, `validation_n_paired`: matched prediction counts.
- `calibration_paired_days`, `validation_paired_days`, `calibration_paired_start`, `calibration_paired_end`, `validation_paired_start`, `validation_paired_end`: dates actually contributing matched observations.
- `warmup_days` = 0; `validation_protocol` = `fixed_selected_parameters_with_chronological_original_history_updates`.

`base_configuration` contains all fields of `Config`: the 34 model fields listed in [CONFIGURATION.md](CONFIGURATION.md), plus `base_dir`, `vwap_input`, `mapping_file`, `eex_curves_dir`, `output_dir`, `max_stale_days`, `warn_stale_days`, `eex_offset_days`, `timezones`, `day_convention`, `weekend_offset`, `warmup_days`, `vwap_columns`, `configuration_mode` and `command_overrides`. The last field is the explicit command-line model overlay, not another spreadsheet or candidate grid. These are configuration values, not measured outcomes; see [ALGORITHM.md](ALGORITHM.md) and [config.toml](config.toml). Paths become strings. Nonfinite diagnostic numbers are serialized as JSON `null`; CSV diagnostic scores may display `inf`/`-inf` for the zero-baseline case above. This does not permit nonfinite production prices.

## Synthetic files

These files are explicitly simulated data, never real transactions. `synthetic_vwaps.csv` contains `reference_date`, `weekday`, `product`, `country`, `region`, `classification`, `unit`, `periodicity_2`, `tenor2`, `vwap`, `total_volume`, `n_trades`. Their meanings match the input dictionary above, but prices, volumes, trades and observation availability are generated. `reference_date` uses day/month/year, `country` uses mapped area, and classification is `<profile> load`.

`synthetic_truth.csv` contains `date` (reference date), `product`, `region`, `unit`, `tenor`, `period`, `delivery_start`, `delivery_end`, `truth` (simulated complete price) and `eex` (EEX price used to generate it). Aliases for the same delivery period share one truth. Both prices use the stated curve unit.

<a id="shape-output"></a>

## Active shape output: 11 additional engine columns

With `shape.mode=off`, the engine retains the 39-column schema above. Audit/adjust add the
following 11 fields (50 engine columns in total). In enriched output every one is prefixed
with `curve_`, including `curve_data_origin_before_shape` and
`curve_estimation_method_before_shape`. Rows without a matching engine point can have empty
trace fields. Active shape can add observed full Month/Quarter/Year labels outside the target
list as same-day context; it does not invent missing prices.

| Engine column | Meaning |
|---|---|
| `price_before_shape` | Pre-layer engine price; empty for missing. On a valid physical original row, `curve_price_before_shape` is that row's own raw numeric VWAP, not its period aggregate. |
| `source_before_shape` | Source of the pre-layer calculation. Enriched valid original rows use `own`. |
| `estimation_method_before_shape` | Method of the pre-layer calculation. Enriched valid originals use `none`. |
| `data_origin_before_shape` | Pre-layer origin. Enriched valid originals use `original`. |
| `shape_mode` | `audit` or `adjust` for this calculation. |
| `shape_status` | Outcome enum below. A residual does not by itself imply failure. |
| `shape_adjustment` | Actual applied price change, in `unit`. Zero in audit and on unchanged/failed rows. |
| `shape_proposed_price` | Validated proposed price, even in audit. Missing stays empty; failed proposals revert to the pre-layer price. On an original physical row, raw VWAP plus its period's proposed delta. |
| `shape_proposed_adjustment` | Proposed minus pre-layer price; zero where no valid movement is proposed. |
| `shape_original_modified` | True only when an original engine node actually changes in adjust mode. Not an indication that the input cell was overwritten; an added alias can inherit this period-level trace. False in audit. |
| `shape_trace` | JSON with the entire same-date/curve solve. Shared by the group's rows, not separate independent evidence for each row. |

Changed own nodes have `source=own+shape`, `data_origin=estimated` and
`estimation_method=shape_adjusted_original`. Changed estimates append `+shape` to source
and estimation method, for example `eex+local+shape` / `ratio_local+shape`.
Their confidence is empty. Unchanged and audit rows retain final provenance and confidence.
Pre-existing basis, anchor and fallback fields explain `price_before_shape`; after a change
they cannot alone reconstruct `price`.

Raw original `vwap` remains untouched even when original adjustment is enabled. For duplicate
input rows with values 118 and 122, an accepted aggregate delta +2 produces final curve prices
120 and 124; before-values remain 118 and 122. The JSON still describes the common aggregate
node. Proposed changes use the same per-row rule in audit.

### Shape status values

| `shape_status` | Meaning |
|---|---|
| `missing` | No finite price existed; the layer does not supply one. |
| `out_of_scope` | Kind is outside full Month/Quarter/Year. |
| `incomplete_period` | Invalid full period/nonpositive hours, or an aggregate lacks required monthly nodes for an enabled coherence term. |
| `missing_reference` | An uninvolved Month lacks a usable matching EEX reference. This does not prohibit a complete aggregate constraint without EEX. |
| `no_constraints` | Node participates in no enabled applicable term; includes absent consecutive months or incompatible publication dates. |
| `original_preserved` | Original node participates, but is fixed by `adjust_originals=false`. |
| `unchanged` | Valid solve with no material movement for this node. |
| `audit_proposed` | Valid nonzero proposal; final price remains unchanged in audit. |
| `adjusted` | Valid nonzero adjustment was applied. |
| `solver_failed` | Numerical/convergence/validation failure; pre-layer prices retained. |
| `alias_conflict` | Eligible aliases disagree on price or hours; the curve solve is rejected and prices retained. |

These statuses have branch precedence: a fixed original with no applicable term can be
`no_constraints`, not `original_preserved`. Out-of-scope/missing rows can still carry the
group's trace.

### Shape JSON dictionary

Read `shape_trace` / `curve_shape_trace` with `json.loads`. Some failure/no-constraint traces
omit fields that were never calculated; JSON `null` means unavailable.

| JSON key | Meaning |
|---|---|
| `parameters` | Seven effective shape controls, including this command's overrides. |
| `reference`, `reference_description` | `eex_settle`: current accepted EEX, not temporal fallback. |
| `original_policy` | `fixed` or `movable_with_penalty`. |
| `smoothness_grid` | Three consecutive calendar months on a uniform month index. |
| `nodes[]` | One record per eligible deduplicated absolute period. |
| `nodes[].period` | `Kind:delivery_start/delivery_end`, exclusive end. |
| `nodes[].price_before, price_proposed, eex_reference` | Aggregate node prices/reference; can differ from an individual duplicate input row. |
| `nodes[].original, alias_count, bound_hit` | Whether any alias is own; count of engine aliases; whether the proposed movement reaches the box limit. |
| `constraints[]` | Enabled, applicable penalty terms, not claims of exact equalities. |
| `constraints[].type` | `basis_second_difference` or `month_aggregate`. |
| `constraints[].coefficients` | Map of absolute node labels to coefficients: `1,-2,1` for smoothness; positive normalized month hours and parent `-1` for aggregation. |
| `constraints[].weight` | Unscaled configured smoothness/coherence weight. |
| `constraints[].residual_before, residual_after` | Price-unit residual before and after the validated proposal; audit's “after” means proposed. Aggregate sign is monthly average minus parent. |
| `solver.method` | `bounded_coordinate_descent` when a solve runs. |
| `solver.converged, iterations, kkt_residual` | Acceptance/convergence diagnostics in the internally scaled problem. Not forecast confidence. |
| `solver.reason` | Present for no applicable terms or failures, such as conflicting aliases or failed numerical validation. |
| `objective_before, objective_after` | Joint loss values in normalized units, when calculated. |
| `objective_price_scale, objective_weight_scale, objective_units` | Loss is divided by price scale squared and weight scale; use these to interpret/reconstruct it, not as a monetary error metric. |

When shape is active, consistency files additionally contain `shape_stage`: `before`
(pre-layer), `after` (published result), plus `proposed` in audit. Before and after prices
are identical in audit. Existing consistency comparisons remain Quarter/months,
Season/quarters and Year/quarters; shape's internal Year/months penalty is detailed in
`shape_trace` and need not be identical to that report's comparison.

`pipeline_configured` backtest predictions include the configured shape layer after hiding
the held-out period. Diagnostic `local_*`/`hist_*` branches need not include it.
CLI tune does not add shape grid dimensions; it stores the fixed shape configuration in its
snapshot. Full behavior and controls: [SHAPE.md](SHAPE.md).

### Aggregate tolerance diagnostics

`shape.coherence_tolerance` defaults to 0.01 in the row's price unit and changes diagnostics only. Aggregate entries in `shape_trace.constraints` add `within_tolerance_before` and `within_tolerance_after` booleans. The trace adds `coherence_within_tolerance_before` and `coherence_within_tolerance_after`, booleans across applicable aggregates or null when none exist. In audit, “after” refers to the proposal. The flags `shape_coherence_outside_tolerance` (adjust) and `shape_proposal_outside_coherence_tolerance` (audit) identify nodes in a relation outside the margin. An outside-tolerance result can be a valid bounded soft-penalty solution; no guarantee of exact or near equality is implied.

The comparison uses `abs(residual) <= coherence_tolerance + 1e-10` to allow numerical rounding.

## MLflow experiment artifacts

The experiment notebook stores artifacts in the selected MLflow run, separately from the
production files under `[paths].output_dir`. Each candidate has a child run:

| Child artifact | When saved | Schema and purpose |
|---|---|---|
| `curves/calibration_filled.csv` | Every completed calibration engine stage, with logging enabled | The same filled schema: **39 base columns**, plus **11 shape columns** when active (50 total). Full curves with visible own observations for visual inspection. |
| `curves/validation_filled.csv` | Selected candidate's completed validation stage only, with logging enabled | The same filled schema, for the reserved validation interval. Losing candidates have no validation curve artifact. |
| `predictions/calibration_paired_predictions.csv` | When individual prediction logging is enabled | Paired hidden-own truth, model/EEX predictions, errors, case keys and EEX availability metadata; not a full filled-curve CSV. |
| `predictions/validation_paired_predictions.csv` | Selected candidate only, when enabled | The winner's paired held-out validation cases; not its full visible-original curve. |

Full curve artifacts exclude warmup output and cover the stage's date interval. They may
contain dates/targets that are not individual LOO cases. They retain visible originals:
**use paired predictions and metrics for accuracy, and full curves for shape/coherence**.
An existing stage artifact does not imply every later part of its run succeeded.

`LOG_PREDICTIONS=False` writes neither paired-prediction CSVs nor full-curve CSVs. Aggregate
CSV reports and JSON configuration/audit metadata are still saved. Old runs without full
curves are reported as unavailable; the viewer does not reconstruct them using current data.
See the [MLflow artifact and viewer guide](MLFLOW.md#saved-curves) for browsing, download
locations, stage selection and independent access to saved experiments.

### Global and individual configuration provenance

The four configuration columns are generated in both modes, including original and missing rows calculated by the engine. Enrichment copies them with the `curve_` prefix, including `curve_configuration_context_id`; originals without a corresponding engine row may lack those diagnostics. See [CONFIGURATION.md](CONFIGURATION.md). Catchup checks both model and context IDs and rejects incompatible saved settings. Expanding only the target list is allowed and recalculates affected groups with pending targets. Legacy global rows without IDs retain earlier catchup behavior; individual mode requires explicit refill for their unrecorded configuration.

`configuration_id` identifies this curve's own 34 model values. `configuration_context_id` additionally allows detecting relevant helper/calendar/availability changes. For example, altering a helper's historical memory can change a CROSS receiver even though its own model ID stays the same. Normal result publication saves the complete context document at `configurations/<configuration_context_id>.json`; retain it with the CSV, inputs and code version. The IDs do not fingerprint the contents of input files.

| Context JSON key | Content |
|---|---|
| `schema_version` | Integer `1`, identifying this document format. |
| `receiver` | Receiving curve's `product`, `region`, `unit`, `use`, `area`, `profile`, `eex_file`, `hours` and `timezone`. |
| `operations` | Fixed `eex_offset_days`, `max_stale_days`, `day_convention`, `weekend_offset` and `warmup_days`. |
| `cross_helpers` | Empty list when receiver CROSS is off. Otherwise all other active curves, each with its mapping fields and complete `model_parameters`, sorted by identity. Includes potential helpers even when none contributes on a particular date. |

The receiver's own 34 parameters remain in `configuration_parameters`, not duplicated in the context document. This permits target-only expansion to keep the same context ID. The model ID and context ID must be interpreted together.
