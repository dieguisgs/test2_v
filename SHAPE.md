# Optional shape layer

[Español](SHAPE.es.md) · [Algorithm](ALGORITHM.md) · [Outputs](OUTPUT.md) · [Validation](BACKTEST.md#optional-shape-layer)

This implemented layer runs **after the normal refill calculation** and is disabled by default.
It can inspect or adjust an already calculated curve. It seeks a compromise between small
changes, smoother monthly adjustments to EEX and closer agreement between months and their
quarters/years. Smoother output is not evidence of better predictive accuracy.

Raw input columns, including each original `vwap`, are always retained in enriched output.
The usable final price is `curve_price`. By default even this price is protected on original
observations; `shape.adjust_originals=true` explicitly permits changing it. Original
observations used to train history and cross remain the input observations, never the adjusted output.

## Three operating modes

- `off`: the pre-existing price calculation and output schema are unchanged.
- `audit`: calculate proposed adjustments and diagnostics, but keep published prices unchanged.
- `adjust`: apply a validated solution within the configured bounds.

Begin with `audit` to see which observations would move and which discrepancies would remain.
This is a separate step from `basis_mode=auto|ratio|additive` and from EEX price averaging.

## What participates

The layer groups each reference date and full `(product, region, unit)` identity separately,
using its mapped profile, timezone and delivery hours. It considers finite prices for full
Month, Quarter and Year periods. Active shape processing also includes eligible own labels of
these kinds outside `targets.tenors`, so original aggregates can constrain the same day's
curve and receive an auditable adjustment when permitted.

A quarter needs all three full months and a year all twelve, with compatible hours. The layer
does not create missing months, use a partly covered aggregate, or fill missing prices. Days,
weeks, seasons, BOM and BOW are outside this initial scope. Aliases of one absolute period
are deduplicated: several labels do not create independent evidence or duplicate constraints.

For monthly smoothness, three complete consecutive months must have finite EEX references
with the same `eex_asof`. The reference is the current accepted **raw `eex_settle`**, which can
be reconstructed by the EEX Pricer; it is not the fallback EWMA/monthly-cascade price. There
is no new smoothing or fallback route for these references. Missing EEX disables that
smoothness term, but a complete quarter/year coherence term can still be used without EEX.

## One joint objective

Let `p_i` be a finite price before shape, `x_i` its proposed price, `E_m` a monthly EEX
reference, and `H_m` the delivery hours of month `m`. The solver minimizes a sum of squares:

```text
sum_i fidelity_weight_i * (x_i - p_i)^2
+ smoothness_weight * sum_valid_triplets [(x[m]-E[m]) - 2*(x[m+1]-E[m+1]) + (x[m+2]-E[m+2])]^2
+ coherence_weight * sum_complete_aggregates [x_parent - sum_m(H_m*x_m)/sum_m(H_m)]^2
```

Here `x[m+1]` denotes the next month's price.
Smoothness penalizes a second difference along consecutive month indices; it does not penalize
the full price curve, require equal monthly prices, or impose positivity. This additive
difference works with zero and negative prices even if the preceding refill used ratio mode.
Seasonal structure in EEX remains a reference, rather than a prediction target.

Estimated nodes have fidelity weight 1. Movable original nodes have `original_weight`.
Original nodes are fixed when `adjust_originals=false`; otherwise they may move within the
same absolute limit as other eligible nodes. The bound is always measured from the
**pre-shape** price: `abs(x_i-p_i) <= max_abs_adjustment`, in that curve's price unit.

Aggregate agreement is soft. Large `coherence_weight` gives disagreement a larger cost,
but neither an original nor an estimated quarter is guaranteed to equal its months exactly.
Fixed conflicting originals and movement bounds can prevent equality. A remaining residual
is a reported compromise, not automatically a solver failure. Smoothness and coherence are
solved jointly; no alternating smoothing/reconciliation loop is used.

The implementation uses NumPy and a bounded convex solver with KKT validation. On solver
failure or an invalid candidate, it retains pre-shape prices and reports the failure. No
invalid partial solution is accepted. Current heuristic confidence is not treated as variance;
changed prices receive empty confidence because the new uncertainty is uncalibrated.

**Coherence tolerance is a diagnostic, not a hard constraint.** `shape.coherence_tolerance=0.01`
compares the absolute monthly-average-minus-parent residual with 0.01 in the curve's price
unit. Increasing it relaxes the reported test; decreasing it tightens it. It changes neither
the optimizer nor the accepted price. Soft penalties, protected originals and movement
bounds can leave a result outside tolerance. Audit flags the proposal; adjust flags the
published result. The trace reports each aggregate test and the overall result, or null
when there is no applicable aggregate. The same numeric setting is interpreted in each
curve's own unit; it is not a percentage or a currency conversion.

## Configuration and command overrides

These are implemented keys in `config.toml`. Numeric defaults are starter values, **not calibrated recommendations**.

| Key | Default | Meaning and effect |
|---|---|---|
| `shape.mode` | `"off"` | `off`, `audit` or `adjust`. Other controls have no price effect while off. |
| `shape.adjust_originals` | `false` | Boolean. True permits eligible own curve prices to move; raw input columns still remain untouched. In audit only proposals move. |
| `shape.smoothness_weight` | `1.0` | Finite, ≥0. Higher favors smaller second differences in monthly Own-minus-EEX adjustment. Zero removes this penalty. No effect where a valid three-month reference is unavailable. |
| `shape.coherence_weight` | `10.0` | Finite, ≥0. Higher favors closer hours-weighted aggregate agreement. Zero removes this penalty. No effect on incomplete aggregates. |
| `shape.coherence_tolerance` | `0.01` | Finite, ≥0. Diagnostic absolute aggregate-residual margin in the curve's price unit. Larger relaxes the reported test; it does not change prices or impose a hard constraint. No overall verdict without applicable aggregate terms. |
| `shape.max_abs_adjustment` | `10.0` | Finite, >0. Maximum total absolute price movement per node. Larger allows more changes, not necessarily better estimates. Check the curve's unit; no currency conversion occurs. |
| `shape.original_weight` | `10.0` | Finite, ≥1. Fidelity weight for movable originals versus 1 for estimates. Higher resists moving originals. Has no movement effect while originals are fixed. |

```toml
[shape]
mode = "off"
adjust_originals = false
smoothness_weight = 1.0
coherence_weight = 10.0
coherence_tolerance = 0.01
max_abs_adjustment = 10.0
original_weight = 10.0
```

The five pricing/evaluation commands `daily`, `refill`, `catchup`, `backtest` and `tune`
accept all seven overrides: `--shape-mode`, `--shape-adjust-originals on|off`,
`--shape-smoothness-weight`, `--shape-coherence-weight`, `--shape-coherence-tolerance`,
`--shape-max-abs-adjustment` and `--shape-original-weight`. They do not rewrite the TOML.

```powershell
python run.py daily --date 2026-09-30 --shape-mode audit
python run.py refill --from 2026-09-01 --to 2026-09-30 --shape-mode adjust --shape-adjust-originals off
python run.py daily --date 2026-09-30 --shape-mode adjust --shape-adjust-originals on --shape-original-weight 20
```

Use daily/refill to recalculate existing dates after changing shape settings. Catchup treats
an existing missing result as processed and does not automatically replace completed groups.

## Worked comparison: protect or move an original

This calculation was checked directly with the implemented solver. Use January–March 2027
Base delivery hours 744, 672 and 743, three estimated monthly prices of 100, an original
quarter at 130, and each monthly EEX reference at 100 from the same publication. Keep the
initial weights (smoothness 1, coherence 10, original fidelity 10) and maximum movement 10.

| Mode | Allow original movement? | Published monthly prices | Published original quarter | Proposed month/quarter |
|---|---|---|---:|---|
| `adjust` | No | 110, 110, 110 | 130 | 110 / 130 |
| `adjust` | Yes | 110, 110, 110 | 120 | 110 / 120 |
| `audit` | Yes | 100, 100, 100 | 130 | 110 / 120 |

All month changes reach the +10 bound. With original movement allowed, the quarter reaches
−10 and becomes an estimated final curve price, while its raw input remains 130. The
monthly-minus-quarter residual falls from −30 to −20 with protection, or to −10 without it;
it does not become zero. All three monthly adjustments are equal, so the second-difference
penalty is zero. This demonstrates bounds and provenance, not a prediction-quality gain.

## Reading a change without losing the observation

Suppose an original input VWAP is 120. With original adjustment disabled, its final
`curve_price` remains 120 even if estimates around it move. If enabled and the validated
solution changes it to 122, the raw `vwap` stays 120 but `curve_price=122`,
`data_origin=estimated`, `source=own+shape` and
`estimation_method=shape_adjusted_original`. This is an illustrative possible result,
not a promise that the solver will choose 122.

If duplicate physical rows are 118 and 122 and their engine aggregate moves by +2, their
enriched final prices become 120 and 124. Each retains its own raw value and its own
`curve_price_before_shape`; the trace records the aggregate solve. Do not replace every
duplicate with the same aggregate price.

For changed estimates, source and estimation method gain `+shape`. Audit retains the
original final price/source/method and reports only proposed changes. Active outputs add
before-values, actual and proposed deltas, original-modification status and a JSON trace;
the [output dictionary](OUTPUT.md) lists every column. Existing basis, anchors and fallback
traces explain the **pre-shape** calculation, not the adjusted price by themselves.

## History and validation

The structural solve needs no extra historical window beyond the refill data and same-date
references already available. Normal refill history, staleness and complete EEX-window
requirements still apply. Selecting weights and movement limits needs real historical
evaluation across seasons, horizons, missing blocks and calendar rolls; a year count alone
cannot establish adequate coverage.

`pipeline_configured` in backtest includes shape after the held-out period and its aliases
have been hidden. History continues to learn only actual originals after prediction.
Tune still searches the same six existing refill dimensions; shape settings stay fixed
through its grid and are retained in the configuration snapshot. Comparing shape settings
requires separate controlled runs with the same evaluation cases.

This layer does not guarantee continuity of a fixed contract across reference dates.
The [rollover audit](BACKTEST.md#rollover-audit) remains a separate temporal concern.
Using a jointly smoothed EEX template, changing monthly fallback routing, or adding temporal
regularization are different proposals, not features of this layer.


## Why this is power-specific, and what the EEX sample showed

Aggregation is justified only when a contract represents the same delivery as its constituent
periods under compatible specifications. EEX documents cascading across delivery periods;
this software's power profiles and hours do not automatically apply to agricultural futures.
For example, CME corn uses a 5,000-bushel contract specification, not this program's power-hour
weighting. Another asset requires a contract-specific model before reusing the aggregation
rule. [EEX contract details](https://www.eex.com/en/trading-resources/product-specifications/contract-details-product-codes);
[CME corn specification](https://www.cmegroup.com/markets/agriculture/grains/corn/specs).

In the inspected local EEX copy, 13 Base/Peak files across seven areas supplied **1,125 complete
quarter/month comparisons** with publications from 2026-08-10 to 2026-10-06. All quarter
settlements were within 0.004396 of their delivery-hours-weighted monthly average and thus
within 0.01. No comparison had the quarter and all three month prices equal within 0.01.
Near aggregate agreement does not mean a flat curve.

These results describe that sample and available complete cases, not a universal market
guarantee or expected agreement of separately observed own VWAPs. Local audit artifacts are
`output/eex_quarter_audit/summary.csv` and `comparisons.csv`; they are Git-ignored and are
not distributed. They do not calibrate the shape layer or prove improved missing-price accuracy.
