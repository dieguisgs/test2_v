# Backtesting: what is measured, and what still needs testing

[Español](BACKTEST.es.md) · [Algorithm](ALGORITHM.md) · [Output dictionary](OUTPUT.md)

This guide distinguishes **implemented evaluation** from **proposed experiments**. The proposals
below are not pricing features. The optional shape layer in the final section is implemented and off by default. A reasonable-looking curve is a hypothesis to test, not
evidence that its missing prices are accurate.

The [MLflow notebook and guide](MLFLOW.md) provide editable candidate lists, local experiment
tracking, a synthetic demo and an expanded model grid. In this guide, `tune` refers to the
command-line interface with its original six grid fields unless stated otherwise. Both use
the same coverage-first objective and chronological winner-only validation. The notebook
example reserves five dates; the CLI default remains twenty.

For a product-manager reading path, start with [product settings](#backtest-product-settings),
[what gets hidden](#backtest-masking), [worked error metrics](#backtest-metrics),
[the exact selection rule](#backtest-selection) and [what the result cannot establish](#backtest-limits).

## 1. A curve can look plausible and still need explanation

In the synthetic M+1/Q+2 example, EEX January, February and March all equal **150**. Additive
estimates are **132.239522, 128.569541 and 130.903094**. The trough in February comes from
local weighting: Q+2's delivery midpoint coincides with February's midpoint, strengthening
its negative basis and local evidence there.

A quarter is a **delivery-period average**, not a physical price observation at its midpoint.
Representing its influence through that midpoint is a modeling choice. Own Q+2 remains **120**,
while its estimated months average **130.637302 by delivery hours**. Neither visual smoothness
nor preservation of the quarterly original proves accurate monthly allocation.

Plot months along their delivery timeline and show quarters as separate interval averages.
Do not connect mixed M/Q labels as one continuous sequence. Power curves need not be monotonic:
seasonality and shocks can justify rises and falls.

## 2. The local model is one weighted basis estimator

Each eligible anchor contributes its basis with weight:

```text
L(t) = ln(1 + max(days to delivery midpoint, 0.5))
weight = volume_factor × kind_factor × exp(−abs(L_target−L_anchor)/tau_log)
local_basis = sum(weight×anchor_basis)/sum(weight)
w = W/(W+shrink_k); final_basis = w×local_basis + (1−w)×permitted_prior
```

Positive volume uses `ln(1+volume)`; otherwise its factor is 1. Correlation changes the kind
factor within this estimator; it is not another interpolation method. Ratio and additive
change basis coordinates and price conversion, not this distance geometry, when eligibility
matches. Without history/cross, the prior adjustment is zero.

## 3. What backtest and tuning actually do

The question is: **if one of our observed prices had been missing, how well would this
configuration have estimated it from the information allowed at that time?** The answer
being predicted is our hidden own VWAP. EEX provides a reference and a benchmark; matching
EEX is not the objective.

### 3.1 Three tools, with different jobs

| Tool | Question it answers | What it does |
|---|---|---|
| `backtest` | How does the currently configured procedure behave on known prices that we temporarily hide? | Evaluates the configured pipeline and component diagnostics over the requested dates. It does not select parameters. |
| `tune` | Which candidate in my supplied grid wins on earlier dates, and how does that winner perform later? | Compares configurations on calibration dates, selects one, then evaluates only that winner on validation dates. |
| MLflow notebook | Can I define more model controls, retain the evidence and inspect each tested model? | Uses the same tuning rules, adds an expanded grid, saves reports and optional price artifacts, and provides experiment and curve viewers. |

Use `pipeline_configured` to assess the deployed refill procedure, including the enabled
fallback, reconstruction and shape steps. Rows named `local_*`, `hist_*`, `blend_*`,
`local_corr_*` and similar are diagnostic calculations. Their settings and fallback
behavior need not match the full production pipeline. They do not vote independently
in tuning.

```powershell
python run.py backtest
python run.py tune --basis-modes auto,ratio,additive --hist-modes off,auto --validation-days 20 --max-trials 50
```

With other grid flags omitted, that tuning command tests six configurations:
three basis modes times two history modes. The CLI permits six search fields:
`basis_mode`, `tau_log`, `shrink_k`, `layer_hist`, `layer_correlation` and `layer_cross`.
Unspecified fields retain the configured values. `--max-trials` limits the Cartesian
product of the supplied lists; its CLI default is 50.

The [MLflow guide](MLFLOW.md) lists the additional notebook search fields, including
model memory, fallback averaging and shape settings. Both interfaces use the same
selection objective. A completed search produces a proposal; it does not change the
production configuration or apply the winning parameters automatically.

<a id="backtest-product-settings"></a>

### 3.2 Global production settings and individual product settings

A curve is the exact **`(product, region, unit)`** identity. Product alone is not enough
to distinguish regions or units; Month and Quarter remain families inside one curve.

Production now supports `[run].configuration_mode="global"` or `"individual"`. Global
ignores optional model cells in the mapping. Individual applies nonempty cells over the
global TOML values; blank cells inherit. All 33 scalar model controls and the `tenors`
list are supported, with complete effective-value provenance in every calculated row.
The mapping may be CSV or the first Excel sheet. It holds **one fixed value per parameter
and curve**, not a list of candidates or a named preset. The existing `profile` column
keeps its delivery-profile meaning. See [CONFIGURATION.md](CONFIGURATION.md) for the
full registry, commands, inheritance and examples.

Historical basis and skill remain separately learned for each curve and basis mode.
Using the same history half-life does not pool all own prices. Optional CROSS can still
connect curves through their raw-own basis surprises and history, not filled output prices.

The experiment scope is a separate notebook/JSON setting:

| Choice | Fixed starting settings | What gets selected |
|---|---|---|
| Configured `backtest` | Actual effective production configuration for each curve | Nothing: it evaluates the current settings. |
| Global MLflow search | Global model settings, ignoring spreadsheet model overrides | One common candidate across the selected `fill` curves. |
| Individual MLflow search | Each curve's effective base under the production selector | One candidate per selected full identity, keeping other curves' settings fixed as helpers. |

In the notebook set `SEARCH_SCOPE`, `SELECTED_CURVES`, `PARAMETER_GRID` and, optionally,
`PRODUCT_GRIDS`. `SEARCH_PLAN_PATH` can load the corresponding JSON plan. A product-specific
grid **replaces** the common grid for that product; it does not merge additional axes.
Unsearched fields retain their fixed base values. The 33 scalar model controls can be
searched; targets, identities, operational availability and calendar conventions cannot.
The production spreadsheet and plan remain unchanged by the search. CLI `tune` retains its
six grid controls; use the notebook for per-product campaigns and expanded grids.

Example: four candidates, two selected products. Global search evaluates four shared
configurations and selects one. Individual search evaluates four candidates for A and four
for B, selecting one for each. `MAX_TRIALS` applies to each search, not to the campaign's
total. This is not a search over every possible combination of A and B's settings.

Individual searches share one chronological split based on the selected curves' union of
observation dates. Each scored product needs enough calibration and later-date evidence;
an insufficient product is reported as an error rather than receiving an invented winner.
Each search must retain predictions on at least two common calibration dates and usable
holdout evidence. Unselected active curves remain available as helpers, not scored products.

The completed individual campaign also replays all frozen winners together and logs a
combined check, including their actual CROSS interactions. It reuses the **same reserved
dates** and does not retune parameters. This is a joint configuration check, not a second
independent final test. Repeatedly changing the plan after inspecting those dates uses up
their status as untouched validation. The [MLflow guide](MLFLOW.md) explains the run tree,
saved curves and plan format. A result remains a proposal; nothing is deployed automatically.

<a id="backtest-masking"></a>

### 3.3 What exactly gets hidden? A four-contract example

Suppose that on September 30 we observe these contracts for one curve:

| Contract label | Absolute delivery period | Own price |
|---|---|---:|
| `M+1` | October | 100 |
| `M+2` | November | 120 |
| `M+3` | December | 80 |
| `Q+1` | October through December | 105 |

These are invented observed prices for explaining masking, not a coherent-price benchmark
or outputs promised by the estimator. Assume each contract passes the eligibility checks
below and has a usable EEX reference.

The backtest performs **four separate prediction exercises**:

| Exercise | Temporarily hidden own contract | Other own contracts still visible that day | Truth used to score the prediction |
|---|---|---|---:|
| 1 | `M+1` | `M+2`, `M+3`, `Q+1` | 100 |
| 2 | `M+2` | `M+1`, `M+3`, `Q+1` | 120 |
| 3 | `M+3` | `M+1`, `M+2`, `Q+1` | 80 |
| 4 | `Q+1` | `M+1`, `M+2`, `M+3` | 105 |

For each exercise, the engine removes the hidden physical delivery period from the own
prices and from both ratio and additive anchor lists. It reruns the configured target
curve with that period hidden, then reads the estimate for the hidden contract. This
allows reconstruction or shape to interact with the same target set used by the refill.
The target's EEX reference remains available under the configured publication policy.

**The hidden observation is restored before the next exercise.** Exercise 2 is not
missing both M+1 and M+2. The four exercises represent four isolated gaps, not a single
curve with four simultaneous gaps.

Different input labels can describe the same physical delivery interval. Such aliases
are aggregated into one own-period observation, and **all aliases of that interval are
hidden together**. Leaving an equivalent label visible would reveal the answer through
another name. A month and an overlapping quarter are different intervals, so hiding the
month does not also hide the quarter. This distinction is one reason isolated masking
can be easier than a real block of missing prices.

Where multiple original rows represent the same interval, the held-out truth is their
aggregated own price: positive finite volumes supply weights; zero or unknown volumes
use weight 1. This is the engine's own-period observation, not a random transaction row.
Original input rows remain unchanged.

There is no configurable random percentage of observations to hide in this procedure.
There is also no current option to hide three consecutive months or every own price on a
day as one test case. Those are different experiments, described as proposals in
[section 5](#5-proposed-protocol-complete-truth-realistic-missingness).

### 3.4 Which observations can become backtest cases?

An observation must pass all the following steps to enter the common EEX-evaluable
test population:

The table describes a configured `backtest` and the six-field CLI `tune`. MLflow campaigns
freeze the scoring population without the last two filters, as explained below the table.

| Check | What it means |
|---|---|
| Active `fill` identity | Its mapping is enabled, assigned to an EEX file and marked `use=fill`. Helpers can supply evidence but are not scored as output curves. |
| Finite own price and resolvable tenor | Zero and negative own prices are allowed. The tenor must resolve on its own reference date. An originally missing own price cannot be scored because its truth is unknown. |
| Positive delivery hours | The resolved period must have delivery hours under that curve's profile/calendar. A zero-hour Peak interval is not an eligible observation. |
| Usable finite EEX reference | The period has a finite price available under the cutoff and age rules. It may be quoted directly or constructed from supported contracts; a direct contract row is not mandatory. |
| Minimum volume | If the aggregated period volume is known and below `min_volume`, the observation is excluded. The aggregate sums known nonnegative volumes. All unknown volumes remain unknown and do not fail this check; known zero plus unknown volume aggregates to zero. |
| Optional own/EEX deviation filter | If `max_anchor_dev > 0`, the relative difference must not exceed that limit. With `max_anchor_dev=0`, this filter is disabled. |

The deviation calculation is:

```text
abs(own_price - eex_reference) / max(abs(eex_reference), ratio_eex_floor)
```

These common filters define the evaluation population before the method-specific ratio
guards. A zero or near-zero EEX price, an incompatible sign, or a ratio outside
`max_ratio_deviation` can make a price unusable **as a ratio anchor** without removing its
otherwise eligible observation from evaluation. The pipeline must still try to predict
that hidden case, and an abstention counts against coverage. This prevents a ratio
candidate from improving its reported results simply by removing inconvenient cases
from its denominator.

The configured target-tenor list determines the regular output curve. It does **not**
restrict which observed tenors can be tested. An eligible observed contract outside
that list is added when the engine predicts its hidden period. No filter currently says
"evaluate only monthly targets" merely because the output list contains months.

Filtering changes the question being answered. Raising `min_volume` or rejecting large
own/EEX differences can remove the very observations on which a method struggles.
For a direct `backtest`, the configured volume/deviation filters define eligibility.
The MLflow campaign freezes its scoring population with those two filters disabled, while
each candidate's filters still control which visible observations can become model
anchors and train its history. A candidate cannot remove a difficult answer from the
exam by raising `min_volume` or tightening `max_anchor_dev`. Ratio guard parameters
likewise affect the estimator rather than deleting scoring truths. Identities,
calendars, target lists and EEX availability stay fixed across the grid. This distinction
can give the campaign more cases than a direct filtered backtest of the same settings.
The six-field CLI `tune` retains its fixed configured filters. Direct expanded API searches
also freeze scoring eligibility when their grid varies those filters or the ratio floor.

### 3.5 Dates, history and the number of prediction exercises

Start and end dates are inclusive. Tuning first builds a chronological list of distinct
dates with a finite own price, a resolvable tenor and an active `fill` identity. This is
the union of dates across the included curves, not an independent split per product.
Weekends can appear if observations exist on those dates.

**Date selection happens before the full eligibility checks in section 3.4.** A date can
enter this list and later contribute no EEX-evaluable case because of missing EEX,
insufficient known volume or another common filter. Therefore `validation_days=5`
does not guarantee five dates with successfully scored predictions.

Suppose the requested interval contains **20 dates in that initial observation list**,
and we reserve the last five for validation:

| Part | Dates | Use |
|---|---|---|
| Calibration | The first 15 observation dates | Evaluate every candidate and select one configuration. |
| Validation, also called holdout | The last 5 observation dates | Evaluate only the selected configuration. These errors do not select the winner. |

Now assume four different delivery periods pass every eligibility check on each date:

| Work | Calculation | Hidden prediction cases |
|---|---|---:|
| Calibration of one candidate | 15 dates x 4 periods | 60 |
| Calibration of six candidates | 6 candidates x 60 cases | 360 |
| Validation of the selected candidate | 5 dates x 4 periods | 20 |
| Total pipeline prediction exercises | 360 + 20 | 380 |

This is not 6 x 20 validation cases: nonwinning candidates are not evaluated on the
holdout. These counts assume sufficient evidence and full eligibility; the number of
available model predictions can be smaller. Raw diagnostic reports can contain several
method rows for each case, so their row count is not the number of independent hidden
observations.

The CLI default reserves **20** observation dates, whereas the notebook example reserves
**5**. At least two calibration dates must remain after splitting, and at least two
distinct dates must have predictions common to all calibration candidates. In global search this minimum
is across the scored population. In individual search each product is its own scored population.
The CLI example in section 3.1 therefore needs at least 22 dates in the initial list,
plus enough common predictions and usable holdout EEX. A one-date demonstration cannot
support tuning.

Earlier data are still useful. `warmup_days=0` means **replay all supplied earlier own
history**, not "use no history"; tuning requires this setting. Choosing a later evaluation
start does not discard earlier observations that can initialize historical adjustments.

Within calibration or validation, the engine predicts a day's hidden cases before that
day's own originals are learned. It then treats those originals as observed for later
dates, subject to EEX availability. With a negative EEX offset, an historical pair is
learned only after its same-date EEX becomes allowed; [section 3.9](#39-eex-availability-is-a-fixed-evaluation-scenario)
details that release rule. Every candidate starts a fresh chronological replay, and the
winner keeps fixed parameters during validation.

This is an **isolated-gap prediction exercise followed by normal historical learning**.
It does not simulate a price that remains unavailable in every future historical update.
A persistent gap needs a different masking protocol.

<a id="backtest-metrics"></a>

### 3.6 What do the errors and coverage columns mean?

For one curve and one price unit, imagine three hidden observations:

| Case | Hidden own price | Model prediction | Raw EEX benchmark | Model error: prediction minus own | Absolute model error | Absolute EEX error |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 100 | 103 | 110 | +3 | 3 | 10 |
| 2 | 120 | 116 | 110 | -4 | 4 | 10 |
| 3 | 80 | 85 | 90 | +5 | 5 | 10 |

The resulting metrics are:

```text
Model MAE  = (3 + 4 + 5) / 3                    = 4
Model RMSE = sqrt((3^2 + (-4)^2 + 5^2) / 3)     = 4.08248
Model bias = (3 - 4 + 5) / 3                    = 1.33333
EEX MAE    = (10 + 10 + 10) / 3                 = 10
Score      = Model MAE / EEX MAE                = 0.4
Skill      = 1 - Score                         = 0.6
```

MAE says the average error magnitude is 4 price units. RMSE gives larger errors more
influence through squaring. Positive bias means the predictions are higher on average;
negative bias means lower. Positive and negative errors can cancel in bias, which is why
a bias near zero does not imply small errors.

The score of 0.4 here means 40% of the benchmark's MAE, or 60% lower MAE than EEX.
It does **not** mean the model is 40% away from the own price. This direct reduction
interpretation applies to this one-curve, same-sample illustration; the global score
across several curves is calculated differently, as explained next.

The raw EEX benchmark is evaluated even though the production pipeline does not use an
unsmoothed EEX-only fallback. A benchmark is a comparison calculation, not an instruction
to publish that benchmark price.

Accuracy must be read together with coverage:

| Field in tuning reports | Meaning |
|---|---|
| `n_baseline` | Eligible hidden own observations with a usable EEX reference: the coverage denominator. |
| `n_available` | Those baseline observations for which this candidate produced a finite pipeline prediction. |
| `n_missing` | `n_baseline - n_available`. |
| `coverage` | `n_available / n_baseline`. It counts predictions, not volume, delivery hours or every possible production gap. |
| `n_paired` | Cases actually used for error metrics: the intersection across all candidates during calibration; available winner predictions during validation. |
| `n_paired_dates` | Distinct dates represented by those scored cases. |
| `n_curves` | Full product/region/unit identities represented by those scored cases. |

For example, `n_baseline=100`, `n_available=95` and `n_missing=5` mean 95% coverage.
If other grid candidates reduce the shared calibration sample to 80 cases, this candidate's
`n_paired` is 80, even though it predicted 95 prices. It is essential to inspect both counts.

These fields describe tuning reports and the corresponding MLflow metrics. The CLI
`backtest` method summaries also report method-level error diagnostics and paired
comparisons; their row counts must not be mistaken for the tuning grid's common sample.

Within a curve's MAE, each scored observation has equal weight. A large trade does not
get more error weight than a small trade, and a longer delivery period does not get more
error weight than a shorter one. Volumes and hours can affect the estimate or eligibility;
they do not weight the final error observations.

Absolute MAE, RMSE and bias are reported **by price unit**. Tuning's overall absolute-error
fields are blank, rather than adding EUR/MWh and GBP/MWh errors together. The overall
score is dimensionless. The per-unit MAE averages cases within that unit; it is not the
same calculation as averaging equally weighted curve-level error ratios.

If the winner produces no validation predictions but EEX-evaluable cases exist, validation
coverage is zero and its error score is unavailable. That is not zero error or perfect
performance. MLflow omits nonfinite numeric metrics and retains their state in audit
JSON; unavailable and infinite scores must not be read as ordinary zero values.

<a id="backtest-selection"></a>

### 3.7 Exactly how is the winner selected?

The current rule has two priorities, in this order:

1. **Maximize calibration coverage.** Only candidates with the largest `n_available`
   can win. All candidates must have the same baseline observation keys, own truths,
   EEX references and available EEX policy metadata.
2. **Compare accuracy on a common sample.** Take the observations predicted by
   **every candidate in the supplied grid**, including candidates that cannot win
   on coverage. For each curve, compute the model MAE and EEX MAE on those same cases.
   Divide the first by the second, then average those curve ratios with equal curve
   weights. The smallest score among the coverage-eligible candidates wins.

An exact tie selects the first candidate in grid order. The selection does not use
validation errors, P95 errors, smoothness, month/quarter residuals or the number of
parameters. `normalized_skill` is `1 - score` and therefore gives the same ranking as
score on the same sample; it is not a second optimization objective.

**Why use a ratio to EEX?** A 5-unit error can be small for one curve and large for another.
Comparing each curve with its own EEX benchmark gives a dimensionless measure. Giving
each curve the same weight also prevents a curve with many observations from automatically
dominating a sparse one. The tradeoffs are equal voting power for sparse curves and
sensitivity to a very accurate EEX benchmark.

For two equally sampled curves expressed in the same unit:

| Curve | EEX MAE | Model MAE | Curve error ratio |
|---|---:|---:|---:|
| A | 10 | 6 | 0.6 |
| B | 1 | 1 | 1.0 |

```text
Global score = (0.6 + 1.0) / 2 = 0.8
Pooled EEX MAE, in this equally sampled same-unit example = 5.5
Pooled model MAE                                           = 3.5
Pooled MAE reduction                                       = 36.36%
```

A global score of 0.8 is therefore **not generally a 20% reduction in pooled MAE**.
Different units must not be pooled at all. With unequal case counts, the distinction
between case-weighted absolute errors and equal-curve scoring becomes even more important.

**What if EEX's error is zero or almost zero?** For a curve with EEX MAE exactly zero:

- Model MAE zero gives a ratio of 1: both are perfect.
- Any positive model MAE gives infinity: the model is worse than an exact benchmark.

A very small nonzero denominator is used as it stands. EEX MAE 0.01 and model MAE 1
produce a ratio of 100. The `ratio_eex_floor` parameter guards ratio-based price
adjustments; it does **not** put a floor under the scoring denominator. Inspect
absolute errors and sample sizes when a nearly exact benchmark dominates the score.
If all eligible candidates have the same infinite score, the usual first-candidate
tie rule still applies; a selected proposal is not necessarily a good model.

**What is the consequence of coverage taking priority?** A candidate with 100 predictions
out of 100 outranks a candidate with 99 out of 100, even if the latter has dramatically
lower common-case MAE. The rule discourages winning by refusing difficult cases, but
one extra filled price can outweigh a large accuracy improvement. This is an explicit
tradeoff in the implemented policy, not a guarantee of a better curve.

**Can adding another grid candidate change the result even if it cannot win? Yes.**
The common sample includes the predictions shared by *all* candidates. Consider one
curve with one test case on each of three distinct calibration dates. EEX's absolute
error is 10 in every case:

| Case/date | Candidate A absolute error | Candidate B absolute error | Candidate C absolute error |
|---|---:|---:|---:|
| 1 | 0 | 10 | 0 |
| 2 | 100 | 0 | Missing prediction |
| 3 | 0 | 0 | 0 |

With only A and B, all three cases are common. A's MAE is 33.3333 and B's is 3.3333:
**B wins**. Add C, and the common sample becomes cases 1 and 3. A's common-case MAE
becomes 0 and B's becomes 5: **A wins**. C has only 2/3 coverage and is ineligible,
but its abstention removed A's large error from the accuracy comparison. Cases 1 and 3
span two dates, so the implemented minimum common-date requirement is still satisfied.

The error of 100 has not disappeared from the saved prediction evidence. It simply
falls outside the sample used for that grid's ranking. For this reason, record the
whole candidate grid, coverage, `n_paired` and common-case membership. A score belongs
to a specific grid and evaluation sample; scores from different grids are not
automatically comparable. More candidates do not necessarily make the selection
criterion more informative.

### 3.8 Saved full curves and masked predictions answer different questions

The [MLflow notebook](MLFLOW.md) displays saved full curves for every calibration trial
and for the winner's validation stage. They retain the day's available own observations;
**they are not LOO curves with the scored own observation removed**. Use those charts for
shape, month/quarter differences, rollover behavior and temporal changes. Use the masked
predictions and their reports to assess accuracy. Seeing an original price reproduced
on a full-curve chart is not a successful prediction test.

With `LOG_PREDICTIONS=True`, each child trial stores:

| Artifact path | Contents |
|---|---|
| `curves/calibration_filled.csv` | Full calibration-stage refill for that candidate, with own observations visible. |
| `curves/validation_filled.csv` | Full validation-stage refill; present only for the selected candidate. |
| `predictions/calibration_paired_predictions.csv` | Hidden own truths, raw EEX benchmarks and pipeline predictions, including common-sample membership after scoring. |
| `predictions/validation_paired_predictions.csv` | The winner's hidden-case validation evidence. |

The paired files retain abstentions from the EEX baseline population; the model prediction
is missing for those cases. These are the files to inspect when a candidate's coverage
and common-case accuracy tell different stories.

`LOG_PREDICTIONS=False` omits both individual paired predictions and full curves.
Aggregate reports and metadata are still recorded. Saved full curves come from the
existing tuning engine runs; opening them does not rerun the model.

The viewer reads persisted artifacts, including previously saved experiments, without
loading today's production inputs or EEX files. If an older run did not save full curves,
create a new experiment to obtain them. A missing artifact is never reconstructed and
presented as that original run's output.

### 3.9 EEX availability is a fixed evaluation scenario

`[eex].offset_days=0` permits publications through the reference date T; `-1` restricts
them to T-1 calendar day or earlier, and `-2` to T-2 or earlier. Choose it in the TOML,
use `--eex-offset-days -1` in `backtest` or `tune`, or set `EEX_OFFSET_DAYS=-1` in the
experiment notebook. It is not a candidate field in either grid. The same cutoff applies
to the raw EEX benchmark, pipeline references and fallback price/spread windows.
Delivery targets still resolve from T.

Historical learning with a negative offset releases original Own_h/EEX_h pairs only
when `h<T` and `h<=T+offset_days`, preserving the observation date h for historical
timestamps and expiry. It requires same-date EEX_h; it never learns own_h against an
older EEX snapshot. Historical observations are released once, not learned again on
every forecast date.

LOCAL and HIST remain usable. Current CROSS surprises require same-day EEX and therefore
contribute no current cross adjustment with negative offsets, even if `layer_cross=True`.
Cross covariances can still learn exact historical pairs; local correlation weights
remain usable.

For Monday with offset -1, the cutoff is Sunday. A Friday publication is **three days
old relative to Monday**; a `max_stale_days=2` limit rejects it. Setting
`[eex].max_stale_days=0` in the TOML means unlimited age. Audit `eex_offset_days`,
`eex_cutoff_date` and `eex_asof` in LOO/paired prediction files and the availability
policy recorded in tuning metadata.

Compare offset 0 and -1 as separate, labeled runs with the same intended dates and masking.
A lower score alone does not establish a better model: the policy can change the EEX
benchmark and which own observations are EEX-evaluable. Report coverage and common
observation keys; use an explicitly matched comparison before attributing differences
to forecast quality. CSV publication dates provide a date-level convention, not historical
intraday availability or revision visibility.

<a id="backtest-limits"></a>

### 3.10 What can we conclude, and what remains untested?

| Question | What the current procedure can say |
|---|---|
| Why hide known prices if the real objective is unknown ones? | Known prices supply answers against which predictions can be checked. This gives evidence about the estimator on those cases; the truly unknown prices cannot provide their own test answers. |
| Does a good score prove the actual missing prices are correct? | No. Observed prices can differ systematically from missing ones in liquidity, horizon or market regime. The masking experiment is evidence about its tested population. |
| Is the own VWAP an unquestionable fair value? | No. It is the implemented target and can contain noise, outliers or inconsistent aggregates. Common filters and source quality matter. |
| Are all curves being filled independently? | They have separate own histories, but optional helpers/CROSS can connect them. Global search chooses one shared candidate; individual search chooses one per identity and then checks the winners together. |
| Does a good one-gap result establish performance on several missing months? | No. Other same-day own observations remain visible in each test. Block gaps, no-own-price days and persistent missingness require different masks. |
| Does the best score identify the best-looking or most coherent shape? | No. Shape is not a direct scoring term. It can affect hidden predictions, but smoothness and aggregation residuals are not separately rewarded. |
| Can the grid find the universally best parameters? | No. It compares the supplied combinations under one fixed data and availability scenario. Untried configurations, future regimes and different missingness remain untested. |
| Can I repeatedly change the grid after seeing validation? | You can run another experiment, but those dates have then influenced your choices. They no longer provide an untouched final check. |
| Do synthetic results or passing software tests establish real-market accuracy? | No. They validate mechanics and performance within the invented assumptions, not unknown real prices. |

For a product manager, the proposed configuration means: **among these candidates, on
these observed-price masking cases, this one satisfied our coverage priority and had
the best common-case score; this is how it then performed on later dates**. It does not
mean that every missing price is known, every product improved, or every aggregation
relationship is consistent.

Before adopting a proposal, read its later-date validation, coverage, sample counts,
absolute errors by unit and the underlying paired predictions. Inspect the saved full
curves separately for shape, unusual contract relationships and rollover changes.
A smooth chart and a low error score answer different questions.

[Section 5](#5-proposed-protocol-complete-truth-realistic-missingness) describes proposed
block masking, persistent gaps and repeated-origin validation. Those are not hidden
features of the current command or notebook. The later sections retain the shape and
anchor investigations and distinguish implemented controls from suggested extensions.

## 4. What hiding one period can establish

Current leave-one-out evaluation hides one own delivery period and **all equivalent aliases**
on that date, recomputes the configured targets, and scores the hidden period. Prediction
precedes that day's historical update; future publications are excluded. No direct future-price
leakage was identified in the reviewed path.

With only observed M+1 and Q+2, this can test predicting either from remaining evidence.
It cannot establish the accuracy of an unobserved M+3. Hiding one point differs from losing
a block of months or every own observation that day.

Evaluated originals need EEX. Direct `backtest` and the six-field CLI `tune` also apply their
fixed volume/deviation filters. The expanded notebook supports varying `min_volume` and
`max_anchor_dev` using a separately frozen scoring population with those filters disabled;
their candidate values still affect visible anchors and historical learning. Ratio guard
parameters are searchable too. Coverage still concerns **EEX-evaluable own observations**,
not every requested unknown gap. Candidate abstentions continue to affect coverage and the
common prediction intersection; freezing eligible truths does not remove those scoring limits.

## 5. Proposed protocol: complete truth, realistic missingness

This protocol is **not implemented by the current leave-one-out command**:

1. Generate complete synthetic truth on a delivery grid. For coherent valuation scenarios,
   derive quarters and years from its hours-weighted pieces; test inconsistent observed-VWAP
   aggregates as a separate, explicitly defined scenario. Include additive, proportional and
   mixed worlds, seasonality, shocks, zero and negative prices.
2. Apply identical masks to every candidate: isolated periods, consecutive month blocks, only
   M+1/Q+2 visible, and days without own observations. Follow fixed absolute delivery periods
   through tenor/calendar rolls instead of confusing successive M+1 labels.
3. For persistent missingness, remove hidden observations before preparation **and every later
   history update**, including helper inputs. Do not let a hidden label re-enter training later.
   Ordinary current leave-one-out instead treats the real original as observed after scoring.
4. Separate chronological calibration, validation and a final untouched test interval. Never
   use later labels to select earlier parameters. Repeat across forecast origin dates to test
   changing regimes; this repeated-origin protocol is a proposed extension, not today's tune.

The existing synthetic generator uses group-level basis and random volumes without an explicit
volume-to-noise relationship. It is not a neutral experiment for learning whether volume
weighting or maturity structure helps. New generators must state and vary those hypotheses.

## 6. Accuracy, coverage and shape are different measurements

Current reports provide **MAE, RMSE, bias and coverage**; consistency reports compare aggregates
with available pieces. Absolute price errors must remain separated by unit. Read coverage
alongside accuracy, including missing predictions and the paired-sample size.
Zero holdout predictions mean zero coverage and unassessable errors, not perfect accuracy.

Recommended additions are P95 absolute error, adjacent-delivery spread error, quarter/month
discrepancies, and changes in the estimated **basis curvature**. Do not reward smooth or
monotonic absolute prices regardless of market structure. Track fixed-delivery price drift
through calendar rolls separately from genuine new-information moves. These added metrics
are proposals, not fields already provided by the standard reports.

## 7. Parameter families: what to compare deliberately

The CLI grid varies the six fields listed above. The [experiment notebook](MLFLOW.md)
supports all 33 scalar model fields, including memory, fallback, shape, anchor filters and
ratio guards, using the flat Python names listed in that guide. Its scoring population is
frozen independently of candidate filters as explained in section 3.4. Omitted model fields
stay fixed; targets and operational/data-meaning settings remain outside the grid. This table
is an experiment map, not an instruction to optimize every setting.

| Family | Relevant controls |
|---|---|
| Local reach and evidence | `tau_log`, `other_kind_weight`, `shrink_k`, `min_volume` |
| Coordinates and guards | `basis_mode`, `ratio_eex_floor`, `max_ratio_deviation`, `max_anchor_dev` |
| Own history | `ewma_halflife_days`, `hist_max_age_days`, `hist_auto_min_obs`, `layers.hist` |
| EEX fallback | `eex_fallback.price_method`, `price_window`, `ewma_halflife`, `spread_window`, `anchor_months` |
| Relationships | `layers.correlation`, `correlation.halflife_days`, `correlation.prior_obs`; `layers.cross`, `cross.min_corr`, `cross.min_obs`, `cross.halflife_days` |
| Availability and execution | `eex.offset_days`, `eex.max_stale_days`, `layers.local`, `layers.arbitrage`, `run.warmup_days` |
| Meaning and scope | Targets, identity mapping, units, schema aliases and calendar conventions: establish these correctly, rather than fitting them to reduce errors |

Increasing history, reach or complexity is not automatically an improvement. Freeze data,
scope and evaluation cases while comparing a small, interpretable family of changes.

## 8. Alternatives to test, not existing features

Potential alternatives are calendar-distance kernels, delivery-hours-overlap weights,
same-kind-first with fallback, and linear or PCHIP interpolation of monthly basis. None is
implemented as an alternative local estimator. Strict same-kind weighting is already possible
with `other_kind_weight=0` and correlation disabled, but is not same-kind-first-then-fallback.

Volume experiments could compare equal weights, current log weights, and capped/normalized
weights only when volume units are known. Start with the current model versus an overlap/calendar
candidate and equal volume weights; do not launch a search over every setting or add machine
learning before this comparison is informative.

Enforcing aggregate equalities is a separate model version, not a cosmetic smoothing change.
If fixed originals contradict each other, report the residual. The optional shape layer
uses soft penalties and permits original movement only through an explicit configuration option.
Arbitrage on pending targets does not reconcile already resolved prices.

<a id="shape-investigation"></a>

## 9. Shape investigation: evidence and conclusion

**Primary research.** Callegaro et al. (2022), section 5.2 and Appendix A, smooth the
adjustment to preserve seasonality and match delivery-period averages. This supports
investigating interval-based fitting rather than locating an entire quarter at its midpoint.
[Full article](https://onlinelibrary.wiley.com/doi/full/10.1002/asmb.2645).

Fleten and Lemming (2003) combine market observations, seasonal shape and a smoothness
objective with price constraints. Their construction is more than visual smoothing.
[Original publication](https://www.sciencedirect.com/science/article/pii/S0140988303000392).

**Implemented optional application:** the shape layer regularizes monthly adjustments around
EEX and adds soft Q/Cal hours-weighted coherence penalties; see [its actual scope](#optional-shape-layer). Using EEX as the shape reference is our adaptation, not
proof from these papers that it improves this input. Do not require flat, monotonic or
universally positive prices.

**Executed sensitivity experiment.** The same two synthetic anchors were run in additive
mode, without history, cross, correlation or arbitrage, across all 27 combinations of:

- `tau_log`: 0.25, 0.5, 1.
- `shrink_k`: 0.5, 1, 2.
- `other_kind_weight`: 0, 0.2, 0.6.

Own M+1=110 and Q+2=120 were preserved. A strict interior trough means
`February < min(January, March)`. Being below the neighbors' mean alone is insufficient:
that can occur along a curved monotonic sequence.

| Finding | Interpretation |
|---|---|
| All 18 combinations with other-kind weight 0.2/0.6 have a February interior minimum | The effect persists across this grid, beyond the default parameters. |
| None of the 9 combinations with other-kind weight 0 has that minimum | Removing quarterly influence removes this trough, but also discards that information. |
| The monthly average exceeds own Q+2 by 6.979617–35.411089 | These parameters do not enforce quarterly equality. These are not errors against known monthly truth. |

At tau=0.5 and shrink=1, changing other-kind weight from 0.6 to 0 moves January–March from
**132.239522, 128.569541, 130.903094** to **151.054476, 150.681660, 150.474199**.
The latter has no interior trough but its average, **150.738738**, moves farther from own
Q+2=120. A more regular-looking shape alone does not identify the better predictor.

There is also an algebraic explanation in this scenario. Let a and b be the nonnegative month
and quarter weights, with a+b>0, k=shrink_k>0 and no history/cross:

```text
basis = (10*a - 30*b)/(a+b+k)
basis + 30 = (40*a + 30*k)/(a+b+k) > 0
```

With monthly EEX=150, each estimate exceeds 120. Even a=0 cannot reach 120 while k>0 and
local evidence exists. Weight tuning cannot impose an equality absent from the estimator.
This argument applies to these anchors and assumptions, not every possible market input.

**Controls to compare before selecting a different refill:** errors on hidden gaps, delivery
spreads, abrupt basis changes, hours-weighted aggregation, sensitivity to one anchor, and
fixed-delivery stability across reference dates. Distinguish original from estimated changes.
Thresholds need calibration by curve/horizon; own evidence can justify a jump. Only part of
this monitoring currently exists in consistency output.

For preserved historical VWAPs, aggregation differences do not establish an executable
arbitrage: trades can come from different times or samples. EEX publishes settlements among
its end-of-day data. [EEX End-of-Day](https://www.eex.com/en/market-data/eex-group-datasource/end-of-day-prices).
Whether exact equalities are needed depends on the purpose: coherent valuation or completing
observations while retaining their differences. It is a use-case decision.

Local artifacts are `output/anchor_influence_test/run_shape_sensitivity.py`,
`shape_sensitivity.csv` and `shape_sensitivity.json` in that same directory. Output is
Git-ignored and these artifacts are not included in the published repository. This study
measures sensitivity and structure, **not real predictive accuracy or optimal parameters**.
Production behavior was not changed.

<a id="rollover-audit"></a>

## 10. Fixed-contract rollover audit

A second synthetic audit runs the actual engine on September 30 and October 1, 2026,
following **the same absolute delivery periods**. There are no own observations, so local
has no anchors; history, correlation, cross and output arbitrage are off. Nine EEX
publications end on September 30, with no new October 1 publication. The accepted snapshot
is therefore identical on both dates. Price smoothing uses five observations, EWMA
half-life 2, nine simple spread observations and `anchor_months=2`.

| Fixed delivery period | September 30 estimate | October 1 estimate | Change |
|---|---|---|---:|
| November 2026 | M+2, monthly cascade: 114.000000 | M+1, direct EWMA: 116.659473 | +2.659473 |
| January–March 2027 | Q+2, direct EWMA: 156.659473 | Q+1, direct EWMA: 156.659473 | 0 |

October's prices are constantly 100. The nine same-publication November-minus-October
spreads are 10 through 18, so the September estimate is `100 + mean(10,...,18) = 114`.
After rollover, November becomes an anchor month: its last five prices are 114, 115, 116,
117 and 118. Normalized EWMA weights, oldest first, are approximately 0.088947, 0.125790,
0.177894, 0.251580 and 0.355788, producing **116.659473**. November's raw settlement remains
118 and `eex_asof` remains September 30 throughout.

This change follows the implemented rules: M0 and M1 are counted from the **reference
month**, so November switches from a cascade to direct averaging. It is not a new price
observation or the wrong delivery contract. Moving the last anchor can also affect more
distant months that stay in the cascade. The fixed January–March quarter has no monthly
cascade: the same direct EWMA of 154 through 158 produces 156.659473 on both dates.

The benefit of this design is explicit, auditable level and spread smoothing with complete
historical windows. Its limitation is that these two calculations need not meet continuously
at calendar rollover. Compare `delivery_start` and `delivery_end`: the label M+1 itself
changes delivery month, and Q+1 changes from October–December to January–March in this test.

Local weights also depend on time to delivery, and history pools observations by kind,
group and overall curve rather than maintaining only one fixed contract's history. These
are separate considerations for future rollover studies; neither explains this isolated
no-own, history-off result. Investigating fixed-contract continuity is a proposed next
test, **not an implemented fix or a reason to force a flat curve**.

The local script is `output/rollover_audit/run_audit.py`; run it from the repository root
with `.venv/Scripts/python.exe -B output/rollover_audit/run_audit.py`. Its `summary.json` and
`README.md` record the calculation and settings. These output artifacts are Git-ignored and
not distributed in the repository. All eight engine rows have `source=eex+smooth`, manual
calculations match, and no engine errors occurred. This documentation records behavior;
production configuration and code were unchanged, and the audit demonstrates no accuracy
or performance improvement.

<a id="optional-shape-layer"></a>

## 11. Implemented optional shape layer and how to validate it

The layer is now implemented and **off by default**. It runs after refill, jointly balancing
small changes, second differences of the monthly additive adjustment `price-eex_settle`,
and hours-weighted Quarter/Year aggregation residuals. It uses current accepted raw EEX,
not the fallback's EWMA template. Monthly smoothness needs three consecutive months with a
shared `eex_asof`; aggregate terms need all three/twelve months but do not require EEX.
No missing prices are filled and no Day/Week/Season/BOM/BOW prices are adjusted.

Modes are `off`, `audit` (proposals only), and `adjust` (apply a validated bounded solution).
Original curve prices stay fixed by default. The explicit `adjust_originals=true` option
permits them to move with a larger fidelity penalty; raw input columns always remain intact.
Changed originals are labelled `estimated`/`own+shape`/`shape_adjusted_original`, not passed
off as observations. Adjusted estimates retain their pre-shape provenance and gain `+shape`.

The implemented controls and initial values are `shape.mode="off"`,
`shape.adjust_originals=false`, `shape.smoothness_weight=1.0`,
`shape.coherence_weight=10.0`, `shape.coherence_tolerance=0.01`, `shape.max_abs_adjustment=10.0` and
`shape.original_weight=10.0`. These are starter values, not a calibrated optimum.
[SHAPE.md](SHAPE.md) explains the exact objective, limits, CLI overrides and examples.
A NumPy bounded convex solver validates KKT conditions; failed solutions preserve pre-shape
prices. Aggregate agreement is soft, so conflicting originals or tight bounds can leave a
residual even in a successful solve. Existing heuristic confidence is not used as a variance.

**Aggregation and shape are different.** Subtracting 10.637302 from each month in the earlier
example makes their hours-weighted average 120 but preserves all monthly spreads, including
the February trough. It also exceeds the initial movement bound of 10. This arithmetic is
not an expected output of the new solver. Smoothness and coherence must be assessed jointly;
aliases and multiple estimates from shared anchors must not count as independent evidence.

The same-date solve needs no extra history; the initial refill's memory and EEX-window
requirements remain. Historical data are needed to choose penalties and limits: test missing
blocks, later dates, seasons, horizons and rollovers using the same evaluation universe.
A number of years alone does not establish sufficient overlap or coverage.

`pipeline_configured` now evaluates shape after hiding the observed period and all its aliases;
the hidden truth cannot act as an original shape constraint. History and cross still learn
from actual originals only. The CLI keeps its six search dimensions and fixed shape settings;
the [experiment notebook](MLFLOW.md) can explicitly include shape settings in its grid.
Compare shape settings on matched observations, including errors, large errors, coverage, aggregate
residuals, modification sizes and original movements. Block masking and repeated-origin
validation remain proposed protocols, not new features of this layer.

The research motivation for smoothing a correction to preserve seasonality is section 5.2,
Remark 10 of [Callegaro et al.](https://onlinelibrary.wiley.com/doi/full/10.1002/asmb.2645).
Our EEX-reference adaptation is not proof of predictive improvement. It does not impose a
flat, monotonic or positive curve. A jointly smoothed EEX template and temporal regularization
are not implemented. Cross-maturity agreement cannot by itself cure the same-contract
[rollover jump](#rollover-audit); that needs a separate temporal test and must not be hidden
by a visually smoother curve.

**Coherence tolerance is a diagnostic, not a hard constraint.** `shape.coherence_tolerance=0.01`
compares the absolute monthly-average-minus-parent residual with 0.01 in the curve's price
unit. Increasing it relaxes the reported test; decreasing it tightens it. It changes neither
the optimizer nor the accepted price. Soft penalties, protected originals and movement
bounds can leave a result outside tolerance. Audit flags the proposal; adjust flags the
published result. The trace reports each aggregate test and the overall result, or null
when there is no applicable aggregate. The same numeric setting is interpreted in each
curve's own unit; it is not a percentage or a currency conversion.

See the [power-specific scope and local EEX sample](SHAPE.md#why-this-is-power-specific-and-what-the-eex-sample-showed): aggregate agreement does not require equal month prices and is not a universal rule for every asset.

## 12. Day-ahead influence: current behavior and validation limits

An isolated synthetic audit uses September 30, 2026, an own D+1 price of 100 versus EEX 80,
volume 100, additive mode, no history and the existing distance/type/shrink settings. With
target EEX 120, this +20 anchor produces **M+1=121.069779, M+4=120.028826 and
Q+2=120.017899**. Influence becomes tiny but remains nonzero. Available LOCAL adjustment
therefore prevents the smoothed-EEX fallback: small price influence can still change the
calculation route. This is existing behavior, not a demonstrated accuracy improvement.

Current correlation learns own-minus-EEX **basis surprises between contract groups**;
it is neither an EEX-only model nor a separate learned M+1-versus-M+4 relationship.
An EEX-only check used 42 publications, August 10–October 6, 2026: April 2027 daily price
changes correlated about 0.81–0.85 with fixed November–February contracts over 41 pairs.
Without own historical truth, this cannot rank refill methods or validate a single winning
anchor. Family eligibility rules and an EEX-derived prior with own-price validation remain
proposals. Local evidence is in Git-ignored `output/local_anchor_review/`; no production
logic was changed for this audit.
