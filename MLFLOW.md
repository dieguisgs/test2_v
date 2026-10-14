# Compare backtests in a notebook with MLflow

[Español](MLFLOW.es.md) · [Notebook](notebooks/backtest_mlflow.ipynb) · [Backtest method](BACKTEST.md) · [README](README.md)

Before interpreting results, read the worked examples of [which prices are hidden](BACKTEST.md#backtest-masking),
[how metrics are calculated](BACKTEST.md#backtest-metrics), and [how the winner is selected](BACKTEST.md#backtest-selection).
The [per-product configuration guide](CONFIGURATION.md) distinguishes fixed production values
from the candidate grids compared in this notebook.

This notebook runs the existing parameter tuner and records experiments in a local MLflow
service. Start with the synthetic example, then use the same notebook on the computer that
has your own VWAP and EEX files. MLflow organizes the evidence; the tuner decides which
combination wins. It does not add a new refill method or change the selection objective.

The separate [curve viewer](NOTEBOOK.md) displays saved curves. This experiment notebook
loads data, executes backtests and writes experiment reports under `output/mlflow`.
Both notebooks use English throughout: explanations, code, comments, labels and messages.
The standalone Markdown guides remain available in English and Spanish.

## 1. Install and open

From the repository root, with Python 3.11 or later:

```powershell
uv sync --group notebook --group experiment
uv run --group notebook --group experiment jupyter lab notebooks/backtest_mlflow.ipynb
```

In VS Code, open the repository folder, open the notebook and select the local `.venv`
kernel. On Windows its interpreter is `.venv\Scripts\python.exe`. If it is not listed:

```powershell
uv run --group notebook --group experiment python -m ipykernel install --user --name vwaps --display-name "Python (VWAPS .venv)"
```

Restart the kernel after installing dependencies. Without uv, install the project dependencies
and the optional tools into the same environment as the notebook kernel:

```powershell
python -m pip install "numpy>=1.26" "pandas>=2.2" "openpyxl>=3.1" "jupyterlab>=4" "ipywidgets>=8" "plotly>=5" "mlflow>=3.5,<4" "psutil>=6"
python -m jupyter lab notebooks/backtest_mlflow.ipynb
```

Run the cells in order. The default demo generates its own observations, mapping and EEX
history in memory. It needs `config.toml` for the base configuration, but does not read its
real input, mapping or EEX files. Its overrides apply to a separate configuration object.
The demo invents two curves: one with an additive own/EEX relationship, another with a
proportional relationship, both with noise and changing adjustments. That construction
deliberately favors those model families; it is not a neutral market benchmark.

## 2. The controls you edit

| Notebook control | Meaning |
| --- | --- |
| `PROJECT_ROOT` | `None` finds the project from the current directory or its parents. Set an absolute repository path if needed. |
| `CONFIG_PATH` | `None` uses `config.toml`; a relative path is relative to the project root. |
| `DATA_MODE` | `"synthetic"` by default; change to `"real"` on the computer with your files. |
| `VWAP_PATH` | Optional real input override. `None` uses the input configured in the TOML. A relative override is relative to the project root. |
| `START_DATE`, `END_DATE` | ISO dates such as `"2026-09-01"`, inclusive. `None` uses the dataset's date bounds. |
| `EEX_OFFSET_DAYS` | `None` retains `[eex].offset_days` in both synthetic and real mode. `0` permits a publication on the reference date; `-1` permits only T-1 calendar day or earlier. Any override must be a nonpositive integer. |
| `VALIDATION_DAYS` | Number of latest eligible observation dates reserved for validation; the notebook example uses **5**. These are observation dates, not a five-calendar-day duration. |
| `MAX_TRIALS` | Maximum combinations per search; the example uses **24**. Individual mode applies it to each product, not the campaign total. Oversized grids fail before trials start. |
| `DEMO_SEED`, `DEMO_PERIODS` | Reproducible synthetic generation; default seed 7 and 30 periods. They do not affect real mode. |
| `MLFLOW_PORT` | Local service port, initially **5000**. |
| `MLFLOW_STORAGE` | Local experiment storage, initially `output/mlflow`, relative to the project root. |
| `EXPERIMENT_NAME` | Groups related MLflow runs. Keep the same name to compare reruns. |
| `LOG_PREDICTIONS` | Save individual paired backtest predictions **and full curve snapshots per trial**. `False` omits both. Real-data artifacts can contain observed own prices. |
| `PARAMETER_GRID` | Explicit lists of candidate parameter values, described below. |
| `SEARCH_SCOPE` | `"global"` searches for a shared combination; `"individual"` searches per complete identity. Independent of the production configuration mode. |
| `SELECTED_CURVES` | `None` includes all active `fill` curves; otherwise a list of exact `product`, `region`, `unit` identities. Other active curves remain available as helpers. |
| `PRODUCT_GRIDS` | Optional list of grids by identity. Each replaces the common grid for that product in full; axes are not silently merged. Individual searches only. |
| `SEARCH_PLAN_PATH` | `None` uses notebook settings. A JSON path loads its grid and supplied search controls; omitted optional controls retain their notebook values. |
| `STOP_SERVER` | Final cell: `False` leaves the UI running. Set `True` and execute that cell to stop the managed service. |

`run.warmup_days` must be **0** for tuning, so the replay can use all supplied earlier own
history. In real mode the notebook preserves your configuration and rejects a nonzero value;
it does not silently rewrite it. Earlier history can precede `START_DATE`: the date range
controls evaluation dates, not an instruction to discard the available historical input.

### Compare a same-date or previous-date EEX policy

Set `EEX_OFFSET_DAYS=0` for a run that may use EEX published on T, or `-1` to exclude T even
when the file contains it. The cutoff is `T + offset`, in **calendar days**. On Monday, -1
means Sunday; the latest available publication may therefore be Friday. Its stale age is
three days relative to Monday, not two relative to Sunday. The configured age limit still
applies (`max_stale_days=0` means unlimited). Target tenors continue to resolve from T.

This is a fixed scenario **outside `PARAMETER_GRID`**. Run the
notebook twice, for example with `EXPERIMENT_NAME="vwaps-offset-0"` and `"vwaps-offset-minus-1"`,
keeping the same intended dates and grid. The setup cell prints the effective availability
policy; the config snapshot and tuning metadata record it, and paired prediction files carry
`eex_offset_days`, `eex_cutoff_date`, `eex_asof` when prediction logging is enabled. The raw
EEX benchmark, refill and complete averaging windows all obey the selected cutoff.

Historical Own_h/EEX_h pairs are released with delay only when `h<T` and `h<=cutoff`; their
original dates still govern expiry/decay. LOCAL can use current own anchors against the older
permitted reference. **Current CROSS adjustment is unavailable with negative offsets**, since
its surprise requires same-day EEX; cross relationships can still learn exact historical pairs.
HIST and learned local correlation weights remain usable when sufficient evidence exists.

Changing availability can change the evaluable sample and coverage as well as the reference
price. A lower score in another scenario is not automatically better: inspect common held-out
keys and coverage before comparing. This is a date-level convention over supplied CSVs,
not an intraday publication clock or reconstruction of historical revision vintages.

## 3. Which parameters can be compared?

### Production configuration and search scope are separate choices

Each production Excel/CSV cell contains **one fixed value** for one product. Notebook/JSON
grid entries contain **lists of candidates**. Searching never rewrites the mapping or TOML.

| Production configuration | `SEARCH_SCOPE` | What is compared |
|---|---|---|
| `global` or `individual` | `global` | One shared combination across selected curves. Table overrides are ignored, including helper overrides. |
| `global` | `individual` | One search per product, starting from global settings. Helpers retain that base. |
| `individual` | `individual` | One search per product, starting from its effective mapping settings. Only its grid axes change; helpers retain their initial effective settings. |

To reuse the same grid separately for every product:

```python
SEARCH_SCOPE = "individual"
SELECTED_CURVES = None
PRODUCT_GRIDS = []
PARAMETER_GRID = {
    "basis_mode": ["auto", "ratio", "additive"],
    "layer_hist": ["off", "auto"],
}
```

Two products mean six candidates per product: twelve calibration trials. Each may select a
different winner. This does not search all 36 joint assignments. After selection, **all winners
are checked together**, with fixed parameters, in one additional single-candidate run. This
can expose CROSS interactions: changing a product's memory can change its signal to another.

Searches share a chronological cutoff. The combined check reuses the same reserved dates
and does not select parameters again; **it is not a second independent test set**. Each product
needs at least two calibration observation dates and holdout observations with usable EEX;
the common-case requirements still apply. Insufficient evidence produces an explanatory
error, not an invented winning configuration.

### Replace one product's grid or load a JSON plan

`PRODUCT_GRIDS` contains full identities and replacement grids. For the synthetic demo:

```python
PRODUCT_GRIDS = [{
    "product": "SYNTHETIC_POWER",
    "region": "DE",
    "unit": "EUR/MWh",
    "parameter_grid": {"tau_log": [0.35, 0.75], "shrink_k": [0.5, 2.0]},
}]
```

That product tests four distance/shrinkage combinations. It no longer explores `basis_mode`:
the base value stays fixed. Products without an entry use `PARAMETER_GRID`. Unknown, duplicate
or unselected identities fail validation.

Alternatively set `SEARCH_PLAN_PATH = "notebooks/search_plan.example.json"`. The included
[JSON example](notebooks/search_plan.example.json) works with both synthetic curves and supplies
`search_scope`, `selected_curves`, `parameter_grid` and `product_grids`. Replace identities with
your actual mapping for real data. The plan contains no input paths or executable instructions.
`parameter_grid` is required; omitted optional controls retain their notebook values. Setup
displays the scope and candidate counts before execution.

Each search retains its MLflow parent/candidate runs, linked by `vwaps.campaign_id`. Parent
names identify the product or combined check. The viewer selects those parents, then a
candidate and date, without refitting anything.

The `campaign_<id>` directory contains `campaign.json`, `summary.csv` and
`proposed_product_parameters.json`. The last file contains full effective individual winner
settings, **without applying them to production**. These files are also logged under `campaign/`
in completed searches. Each trial logs `effective_curve_configurations.json`, including helpers,
to preserve its effective settings context.
`configuration_contexts.json` links row context identifiers to the mapping, availability
policy and helper settings actually used by the engine.

### Supported hyperparameters

The default is six combinations:

```python
PARAMETER_GRID = {
    "basis_mode": ["auto", "ratio", "additive"],
    "layer_hist": ["off", "auto"],
}
```

The Cartesian product tries each basis mode with each history setting: **3 × 2 = 6**.
It does not mean six unrelated grids. Omitted parameters retain their dataset configuration
values. The notebook prints the size before running.

The experiment notebook accepts the following **flat Python field names**. They differ from
some TOML names: for example `fallback_price_window` corresponds to
`[eex_fallback].price_window`. The default command-line `tune` interface continues to expose
its original six fields; this expanded grid belongs to the experiment API/notebook.

| Grid field | Accepted values | What changes |
| --- | --- | --- |
| `basis_mode` | `"auto"`, `"ratio"`, `"additive"` | How own prices are expressed relative to EEX. |
| `tau_log` | Positive finite numbers | How quickly an anchor's weight decays with logarithmic delivery distance. |
| `shrink_k` | Positive finite numbers | How strongly local evidence is pulled toward its permitted prior when evidence is weak. |
| `min_volume`, `max_anchor_dev` | Finite numbers >= 0 | Candidate anchor filters; changing them does not shrink the campaign's evaluation population. Zero disables the deviation filter. |
| `ratio_eex_floor`, `max_ratio_deviation` | Positive finite numbers | EEX stability floor and the permitted proportional-adjustment limit. |
| `layer_hist` | `"off"`, `"auto"`, `"on"` | Whether/how the existing history layer is enabled; usable prior observations are still required. |
| `layer_correlation` | `False`, `True` | The existing learned own-basis correlation weighting. It does not search for a best EEX-only anchor. |
| `layer_cross` | `False`, `True` | The existing cross-curve prior, subject to its eligibility and evidence requirements. |
| `layer_local`, `layer_arbitrage` | `False`, `True` | Enable the existing local or arbitrage layers. |
| `other_kind_weight` | Finite numbers >= 0 | Prior weight for an anchor of another contract family. Zero is a weight choice, not a general family-eligibility policy. |
| `ewma_halflife_days` | Positive finite numbers | Historical own-basis half-life (`method.ewma_halflife_days`). |
| `hist_max_age_days` | Integers >= 1 | Expiry since the latest usable own historical observation. |
| `hist_auto_min_obs` | Finite numbers >= 0 | Effective evidence threshold for historical auto selection. |
| `corr_halflife_days`, `corr_prior_obs` | Positive half-life; nonnegative prior, both finite | Correlation decay and prior strength (`correlation.halflife_days`, `correlation.prior_obs`). |
| `cross_min_corr` | Finite numbers from -1 to 1 | Minimum usable cross-curve correlation. |
| `cross_min_obs`, `cross_halflife_days` | Nonnegative evidence; positive half-life, both finite | Cross-curve evidence threshold and decay. |
| `fallback_price_method` | `"simple"`, `"ewma"` | Equal or exponential weights within the fallback price window. |
| `fallback_price_window`, `fallback_spread_window` | Integers >= 2 | Number of EEX publication dates for price averaging and spread averaging. |
| `fallback_ewma_halflife` | Positive finite numbers | Exponential half-life inside the fallback price window. |
| `fallback_anchor_months` | Integers >= 1 | Initial monthly maturities averaged directly before propagating spreads. |
| `shape_mode` | `"off"`, `"audit"`, `"adjust"` | Disable, propose, or apply the existing optional shape layer. |
| `shape_adjust_originals` | `False`, `True` | Explicitly allow the shape layer to move final prices derived from own observations. |
| `shape_smoothness_weight`, `shape_coherence_weight` | Finite numbers >= 0 | Soft penalties for irregular basis and month/aggregate inconsistency. |
| `shape_max_abs_adjustment` | Positive finite numbers | Maximum shape change in price units. |
| `shape_original_weight` | Finite numbers >= 1 | Fidelity to original values when modifying them is permitted. |
| `shape_coherence_tolerance` | Finite numbers >= 0 | Diagnostic tolerance; changing it alone does not alter the objective or prices. |

`GRID_EXAMPLES` offers small named alternatives for basis only, basis plus history, distance
plus shrinkage, local reach, historical memory, fallback averaging, shape and optional learned
layers. To select one, replace the `PARAMETER_GRID`
assignment with `deepcopy(GRID_EXAMPLES["distance_and_shrinkage"])`, for example. The
example values are candidates to investigate, not recommendations established with real data.
`fallback_isolated` additionally disables local/history/cross to exercise fallback explicitly;
it is a controlled branch comparison, not a comparison of the full production setup.

The grid accepts all **33 scalar model hyperparameters**. The `tenors` list is configurable
per product in production but remains fixed during a search. Paths, identity, delivery
conventions, EEX availability/staleness and evaluation dates are also excluded from grid axes.
Unsupported keys raise an error.

Notebook campaigns fix an evaluation population of valid own observations with EEX, disabling
`min_volume` and `max_anchor_dev` only when deciding which truths enter the exam. Candidate
filters still control usable anchors. A candidate therefore cannot remove difficult questions
by raising its filter. Cases without EEX remain outside the exam. A standalone configured
`backtest` retains its configured eligibility; its case counts need not match this expanded
campaign population. Coverage and the common prediction intersection still determine the
ranking described in section 4.

Small focused grids are easier to interpret than multiplying every list together. For
example, fallback settings may tie because leave-one-out still has other own anchors and
never activates fallback. Such a tie is **no evidence about which fallback is better**.
Similarly, `shape_mode="audit"` does not change predictions, and the coherence tolerance
is diagnostic. To assess actual fallback holes or missing blocks, add a suitable masking
experiment; the notebook does not manufacture that evidence automatically.

## 4. How the winner is chosen

Think of a teacher covering a known own price, asking the refill to recover it, and then
comparing its answer with the covered price. Other own prices available at that date remain
visible. Equivalent labels for the same physical delivery period are hidden together, so
an alias cannot reveal the answer. This is leave-one-period-out evaluation, not a simulation
of every missing block that could occur in production.

The tuner splits dates chronologically. Earlier dates are **calibration**; the latest
`VALIDATION_DAYS` eligible own-observation dates are **validation**. It tries all combinations
on calibration, selects one, and evaluates **only that winner** on validation. Parameters
stay fixed during validation, while own observations from earlier validation dates can
become available history for later dates, as in a daily chronological replay.

Selection follows this order:

1. Prefer combinations with maximum calibration prediction coverage over the common EEX
   benchmark universe. A method does not win by abstaining on difficult cases.
2. Among those combinations, prefer the lowest accuracy score on cases predicted by **all**
   calibration candidates. For each curve `(product, region, unit)`, calculate
   `MAE_model / MAE_EEX`; then average those ratios with equal weight per curve.
3. Break an exact tie by the order of combinations in the grid.

Lower `score` is better. A score below 1 improves on the EEX benchmark under this equal-curve
measure; a score above 1 is worse. This is **not** a percentage price error. If EEX's MAE is
zero for a curve, the ratio is 1 when the model is also exact, otherwise infinity.
`normalized_skill = 1 - score`. A synthetic example can contain ties and does not establish
the best real parameters.

The hidden **own VWAP is the truth**, not EEX. EEX is a reference and benchmark. Absolute
MAE, RMSE and bias are shown separately by unit; the overall row leaves these absolute-error
fields blank to avoid mixing currencies or units. Always inspect coverage, sample size,
dates and per-unit results alongside the score. The paired accuracy sample can be smaller
than a candidate's available predictions. Data lacking enough paired dates cannot support
this comparison and produces an error instead of a fabricated ranking.

Repeatedly changing the grid after reading the same validation result turns that validation
set into part of your research. Reserve later untouched dates, or run a separate rolling
evaluation, before claiming real generalization. See [BACKTEST.md](BACKTEST.md) for the
limits of masking observed prices when actual missing prices may have different liquidity.

<a id="selection-explained"></a>

### What “best” means, with numbers

The question being answered is: **among this grid, which configuration fills the most
EEX-evaluable hidden own observations, then best improves on EEX on a common test sample?**
It is not “which curve looks smoothest?” or “which configuration is proven best for every
unknown real price?”.

For each hidden case, compare **both** the model and EEX against the same hidden own price:

```text
model absolute error = abs(model prediction - hidden own price)
EEX absolute error   = abs(EEX reference - hidden own price)
curve ratio = mean(model absolute errors) / mean(EEX absolute errors)
score = mean(curve ratios), with one equal vote per (product, region, unit)
```

The means use the shared prediction cases from **all calibration candidates**. Average
errors within each curve first, then divide; this is not an average of individual percentage
price errors. If the hidden own price is 100, EEX is 110 and the model predicts 106, the
absolute errors are 10 and 6. In a one-case, one-curve illustration, the score is **0.6**:
the error is 60% of EEX's error, or a 40% reduction. The answer being recovered is 100, not 110.

For several curves, a score of **0.8** does **not** necessarily mean a 20% reduction in pooled
absolute MAE. Suppose two EUR/MWh curves have equal numbers of paired cases:

| Curve | EEX MAE | Model MAE | Curve ratio |
|---|---:|---:|---:|
| A | 10 | 6 | 0.6 |
| B | 1 | 1 | 1.0 |

The score is `(0.6+1.0)/2 = 0.8`. Pooled MAE is 5.5 for EEX and 3.5 for the model, a **36.4%**
reduction, not 20%. With different units, pooled absolute MAE is not meaningful at all.
The implemented special case is: if a curve's EEX MAE is zero, model MAE zero gives ratio
**1**; nonzero model MAE gives **infinity**. A nearly zero EEX error can make the ratio very
sensitive even when the model's absolute error is small.

Coverage is a **first decision**, not a small bonus inside the accuracy score. With a common
100-case benchmark universe, a candidate predicting 100 can beat one predicting 99 even
if the 99-case method has lower error on their common cases. This prevents winning merely
by skipping hard cases, but it means one extra filled point can outweigh a large accuracy
improvement. Coverage concerns eligible hidden **observations with an EEX reference**;
it does not measure coverage of every genuinely unknown production gap.

Equal weighting per curve prevents a curve with many observations from dominating by count
and allows unitless comparison across currencies. It also gives a sparsely observed curve
the same vote as a well-observed curve, and small EEX baseline errors can dominate ratios.
Check sample sizes and absolute per-unit errors alongside the ranking. Exact ties select
the first candidate in grid order; they do not establish a unique optimum.

The later holdout is evaluated **only for the chosen candidate** and does not choose that
candidate. Shape quality, month/quarter coherence, and temporal jumps are **not direct terms
of the selection objective**. A shape setting can affect predictions and thereby accuracy,
but a smaller coherence residual does not earn a separate reward. Inspect the holdout,
contract families/horizons, and saved curve charts before accepting a proposal. These checks
help assess the winner; they do not alter the implemented selection rule.

## 5. What appears in MLflow and the notebook

The notebook starts the managed service on **`127.0.0.1`** and displays its clickable URL.
It runs the tuner, links to the resulting MLflow run, displays the sorted calibration table,
shows the proposed parameter configuration, and then shows the winner's validation table.
Use MLflow to compare parameters, metrics and saved artifacts across runs. Use the notebook's
`eligible_for_selection` and `selected` fields to understand selection; sorting only by an
accuracy metric in the MLflow UI does not reproduce coverage-first selection.

Tracking storage and run reports are under `MLFLOW_STORAGE`. Each notebook run gets a fresh
report directory. Saved reports and configuration metadata let you examine what was tried;
the selected parameters are a **proposal**, never automatically applied to `config.toml`.
The notebook does not regenerate or overwrite production curve outputs.

The parent MLflow run groups candidate child runs. Calibration metrics are recorded per
candidate; validation metrics are recorded only for the selected candidate and the parent.
Artifacts include calibration/validation CSV reports, grid and configuration snapshots,
data fingerprints and code/environment provenance. Optional prediction files help audit
individual errors. Fingerprints identify supplied data; they are not a copy of the input
dataset or a substitute for preserving the actual data needed to reproduce a real run.
`LOG_PREDICTIONS=False` omits individual paired price/prediction artifacts **and full curve
snapshots**; aggregate reports and configuration/provenance metadata are still saved.

This workflow uses a local service and local artifacts; it does not require an MLflow cloud
account. Real-data artifacts can contain own prices and identifying labels. They remain in
the chosen storage directory unless you explicitly copy/share it or change the workflow.
No ZIP is generated or uploaded. Stopping the service keeps its stored experiment history.
For the underlying tool, see the official [tracking server documentation](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/)
and [MLflow Tracking guide](https://mlflow.org/docs/latest/ml/tracking/).

### Read one experiment in the UI

1. Open the notebook's MLflow link, then the experiment's run list. Find the parent run and
   show its child runs, named `calibration-001`, `calibration-002`, and so on. Compare those
   children with each other; the parent summarizes the selected candidate, not another trial.
2. Add the parameters you varied and the metrics **`calibration.overall.coverage`** and
   **`calibration.overall.score`** to the run table. First retain the highest coverage; among
   those rows, the lowest score wins. The child tag **`vwaps.selected` = `true`** identifies
   the actual winner. For an exact tie, check `trial_id` and the grid order in the report.
   The fixed **`eex_offset_days`** parameter is recorded on the parent and every child;
   display or filter it to distinguish offset 0 and -1 scenarios before comparing runs.
3. Select child runs to compare their parameter and metric values. Inspect
   `calibration.overall.n_paired` and `calibration.overall.n_missing` as well as the score.
   **`validation.*` metrics appear only for the selected child and the parent**: absent
   validation metrics on other children mean they were not evaluated on the holdout.
   A parent's validation result must not be counted as a second independent trial.
4. Open the run's artifacts. **`reports/`** contains CSV summaries; the parent's
   `reports/calibration_report.csv` contains the full grid. **`audit/`** holds JSON records
   such as configuration, grid, split, selected parameters, fingerprints and provenance,
   depending on whether you opened the parent or a child. **`predictions/`** on child runs
   holds individual paired observations/predictions when enabled. **`curves/`** holds full
   saved curves for visual inspection. With `LOG_PREDICTIONS=False`, neither individual
   prediction files nor full curve snapshots are generated; reports and audit
   metadata remain. The child tag `vwaps.predictions` records `enabled` or `disabled`.
5. Read the selected child's validation coverage and score, then its per-unit errors. Unit
   metric names use a sanitized label plus a hash; `audit/*_metric_states.json` maps them
   back to the original unit. That file also records nonfinite metrics omitted from MLflow's
   numeric charts. An absent/infinite score is not a score of zero; inspect the CSV/JSON.

The notebook's sorted table remains the direct presentation of the selection policy. MLflow
is useful for inspecting and comparing runs, but a UI sort does not itself select parameters.

<a id="saved-curves"></a>

### Inspect a saved candidate's curves

The experiment notebook includes the same chart views as [the CSV viewer](NOTEBOOK.md),
with selectors for a saved experiment, parent run, candidate and stage:

**To reopen old runs without running a new backtest:** execute only the editable settings
cell in **section 1**, then the saved-curve viewer cell in **section 8**. That viewer cell
resolves the project/storage paths, starts or reuses the local MLflow service, and imports
its own dependencies. Skip dataset preparation and the tuning cell: no original VWAP/EEX
files, dataset object or previous `result` variable are needed. Set `PROJECT_ROOT`,
`MLFLOW_STORAGE`, `MLFLOW_PORT` and `EXPERIMENT_NAME` for the saved experiment location.
The optional stop cell in **section 9** also works after this independent browsing flow.

1. In **Experiment:** enter the saved experiment name and click **Refresh runs**.
2. Choose **Run:** and **Trial:**. Choose **Calibration (all trials)**, or
   **Validation (selected trial only)** for the winning trial.
3. Click **Load saved curves**. Use **Curve:**, **View:** and the tabs **Single date**,
   **Range means**, **Fixed delivery evolution**. The full-curve view includes every saved
   contract kind and tenor for that curve/date; it does not invent missing CSV rows.

The browser reads the persisted run artifacts, using temporary downloads under
`<MLFLOW_STORAGE>/viewer_cache` (default `output/mlflow/viewer_cache`). Downloaded files are
removed after loading; this is not a persistent cache reused in place of the saved artifacts.
It does not recalculate old results using today's configuration or files.
The underlying artifacts are attached to each candidate's child run:

| MLflow artifact | Availability | Meaning |
|---|---|---|
| `curves/calibration_filled.csv` | Every trial whose calibration engine stage completed, when logging is enabled | Full curves generated in that calibration date interval. |
| `curves/validation_filled.csv` | Selected trial only, when its validation engine stage completed and logging is enabled | Full curves generated in the reserved validation interval. |
| `predictions/calibration_paired_predictions.csv` | When individual prediction logging is enabled | Held-out prediction cases used to audit errors and coverage. |
| `predictions/validation_paired_predictions.csv` | Selected trial only, when enabled | The winner's held-out validation cases. |

**Full curve snapshots retain visible own observations. They are not the curves with each
LOO observation hidden.** They show how that candidate fills a normal curve with its available
originals. Use the held-out metrics/prediction files to assess accuracy; use the full curves
to inspect shape, aggregate differences and changes across dates. Matching an original shown
on a chart is not evidence of successful hidden-price prediction.

Snapshots come from the existing stage engine calls, exclude warmup output, and cover the
stage's first-to-last evaluation date interval. They can include generated dates/targets
outside the individual LOO case list. A stage artifact can survive if a later step fails,
so inspect run status and reports before treating a run as complete.

Old runs without curve artifacts, runs with `LOG_PREDICTIONS=False`, and a losing candidate's
validation stage cannot display full curves. The browser explains what is missing. To obtain
them, run a **new** experiment with logging enabled; it never silently reconstructs a past
experiment using current data. The artifact files can be downloaded from MLflow as CSVs too.

## 6. Move to the computer with real data

Copy or clone the source repository and install the two optional groups there. Configure
your real input, reviewed mapping and EEX folder in `config.toml`, as explained in the
[README](README.md). Then edit the notebook:

```python
DATA_MODE = "real"
VWAP_PATH = r"C:\Data\VWAPS\observations.xlsx"  # Or None to use config.toml.
START_DATE = "2026-08-10"
END_DATE = "2026-10-06"
EEX_OFFSET_DAYS = -1  # Example policy: exclude same-date EEX; None retains the TOML.
VALIDATION_DAYS = 5
EXPERIMENT_NAME = "vwaps-real-first-review"
```

Those paths and dates are examples, not supplied files or a required historical period.
Real mode loads normalized own observations plus the configured mapping/EEX books. Only
mapped active fill curves participate. Choose dates with enough eligible own observations
and EEX reference coverage; merely having calendar dates in the range is insufficient.
Keep enough earlier history for the methods you want to compare.

## 7. Troubleshooting

- **Import error:** install both groups in the notebook's actual kernel, then restart it.
  The terminal's Python and the notebook's Python may differ; the setup cell prints the latter.
- **Port already in use:** reexecuting the startup cell in the same kernel can reuse its managed service.
  An unrelated process on the port is rejected. Choose another `MLFLOW_PORT`, such as 5001,
  instead of terminating a process you did not start.
- **Startup timeout or inaccessible UI:** inspect the `log_path` displayed by the service
  handle and the startup error; check the selected port and local environment. The helper
  waits for service readiness before continuing.
- **Kernel restarted:** run setup/startup again. Ownership is local to a kernel; the new kernel
  does not adopt or stop a service left by another process. Normal cleanup stops the owned
  service; if a previous service still occupies the port, choose another port or stop it from
  the session that owns it. Stored runs remain on disk.
- **Grid too large:** shorten the value lists or deliberately raise `MAX_TRIALS`. The notebook
  displays the product, not just the number of fields.
- **Insufficient history/paired observations:** inspect the requested dates, mapping, tenors,
  finite own prices and EEX publications. Five validation dates still require earlier usable
  calibration dates. A history/correlation/cross toggle cannot create missing evidence.
- **Stop the UI:** set `STOP_SERVER=True` and run the final cell, or call `server.stop()`.
  Leave it `False` for normal Run All use so the UI remains available after the run.

## 8. Verified execution

The latest complete suite passed **857 tests** in 165.89 seconds on Windows with Python
**3.14.2** and MLflow **3.17.0**, with one upstream MLflow/SQLAlchemy deprecation warning.
The current notebook also completed end to end through the real Jupyter kernel in both
search scopes: **global** with six candidates and EEX offset -1; **individual** with the
example JSON's four German candidates, six French candidates, and one combined verification
candidate at offset 0. All tracked runs finished. Each product winner saved 30 validation
curve rows, and the combined check saved 60. Saved curves carried all four configuration
provenance fields, and their context IDs resolved to the stored context documents. The
saved-curve browser loaded without calling the engine again; the local UI returned HTTP
200 and both managed services shut down. Jupyter emitted socket warnings during kernel
shutdown after the checks completed; these did not fail the executions.

Earlier availability-policy and viewer checks remain relevant: the notebook was
executed through the actual `.venv` Jupyter kernel with both the default offset **0** and an
explicit offset **-1**. The -1 run completed all six candidate children under one parent,
with **25 calibration dates / 290 paired cases** and **5 validation dates / 58 cases** for
the selected candidate only. Saved paired-prediction CSVs were checked to confirm every
accepted publication satisfied **`eex_asof <= eex_cutoff_date = T - 1 calendar day`**.

The offset -1 execution also verified an HTTP 200 response from the local UI and clean
service shutdown. Earlier default-offset checks verified report artifact upload/download
and retention of experiment records after stopping/restarting the service.

The saved-curve viewer was then exercised through the actual notebook/kernel and local
service at offset -1. Each of six trials saved **300 full calibration rows**; only the
winner saved **60 full validation rows**. These full curves are separate from the held-out
case counts above. Checks switched between a nonwinner's calibration and the winner's
validation, full-curve/Month/Quarter views, and the three chart tabs.

The independent viewer was rerun after removing `dataset` and `result` and pointing input
and config paths at missing files. The engine's `run` method was patched to fail if called:
the saved curves still loaded with **zero engine reruns**. The UI returned HTTP 200 and the
managed service stopped cleanly. This confirms inspection of persisted artifacts without
loading the original data or silently recreating an experiment.

These checks establish the tested local workflow, not predictive accuracy on unavailable
real own data, the optimality of synthetic winning parameters, or runtime verification on
every supported Python version and operating system. The notebook saved in Git has no outputs.
