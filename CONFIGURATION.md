# Global or individual model configuration

[Español](CONFIGURATION.es.md) · [Algorithm](ALGORITHM.md) · [Backtest](BACKTEST.md) · [MLflow](MLFLOW.md)

There are two separate choices. **Production configuration** determines the fixed settings
used to calculate each curve. **Experiment scope** determines whether a parameter search
selects one common candidate or a separate candidate for each product. The Excel mapping
stores fixed values; it does not store lists of candidates or automatically receive a winner.

## 1. The production switch

Edit the existing section of `config.toml`:

```toml
[run]
configuration_mode = "global"
warmup_days = 0
```

| Value | What the engine does |
|---|---|
| `global` (default) | Uses the TOML model settings for every product. Ignores optional model-parameter cells in the mapping, even if they contain invalid values. |
| `individual` | Starts from the TOML values and applies the nonempty model cells in each mapping row. Blank cells inherit the global value. |

The selector can be overridden for one execution without editing the TOML:

```powershell
python run.py daily --configuration-mode individual
python run.py refill --from 2026-09-01 --to 2026-10-06 --configuration-mode individual
python run.py catchup --configuration-mode individual
python run.py backtest --configuration-mode individual
```

Use `--configuration-mode global` to ignore individual cells for that execution. This does
not disable the mapping itself: identity, `use`, EEX file, delivery hours and timezone still
apply. Separate historical states are maintained for each curve in both modes; using common
parameters does not mix all products' prices into one historical mean.

## 2. One Excel/CSV row is one fixed product configuration

A curve identity is the exact tuple **`product + region + unit`**. Two rows with the same
product but different regions or units may have different settings. A blank region is a
literal value, not a wildcard. The existing `profile` column still means Base/Peak or another
delivery-profile label; it is not an algorithm configuration profile.

The mapping accepts `.csv` and `.xlsx`. Excel reads the **first worksheet**. Its existing
metadata columns remain unchanged; optional columns use the Python parameter names listed
in section 4, such as `tau_log`, `fallback_price_window` and `shape_mode`.

```powershell
python run.py mapping --with-parameters
```

This adds all 34 supported parameter columns to the configured mapping, preserving existing
manual values and metadata. New parameter cells are blank. Updating an Excel mapping keeps
its other worksheets. To create a new Excel copy instead of replacing the configured file:

```powershell
python run.py mapping --with-parameters --export mappings/products.individual.xlsx
```

Then edit the existing `[paths]` section:

```toml
[paths]
mapping = "mappings/products.individual.xlsx"
```

Exporting a copy does not switch `paths.mapping` automatically. The command reports the
path to use. [The fictional example](mappings/products.individual.example.csv) contains all
34 columns and disabled rows. Replace its identities and EEX assignments with your own and
review `use` before running; it is not a real mapping or a calibrated recommendation.

Suppose the global values are `tau_log=0.5`, `layer_hist="auto"` and
`fallback_price_window=5`:

| Product | `tau_log` cell | `layer_hist` cell | `fallback_price_window` cell | Effective values in individual mode |
|---|---|---|---|---|
| A | blank | blank | blank | 0.5, auto, 5 |
| B | 0.8 | off | blank | 0.8, off, 5 |
| C | blank | blank | 9 | 0.5, auto, 9 |

In global mode all three use 0.5, auto, 5. These example values illustrate inheritance;
they do not demonstrate that one configuration predicts prices better.

### Cell syntax and validation

- Leave a cell empty to inherit. Zero and `false` are explicit values, not empty cells.
- Boolean controls use `true` or `false` (case-insensitive); native Excel booleans work.
  Do not use `0`, `1`, `yes` or `off` for a boolean. `layer_hist` is a string control and
  specifically accepts `on`, `off` or `auto`.
- Decimal values use a dot, for example `0.5`. Integers use `5`, not `5.0` in CSV text.
- Text choices use the listed words, without extra literal quote characters in the cell.
- `tenors` uses one JSON array cell, for example `["M+1", "M+2", "Q+1"]`. In CSV, normal
  CSV quoting escapes the embedded double quotes; spreadsheet software handles this.
- A fixed scalar cell must not contain a search list such as `[0.3, 0.8]`.
- Individual mode rejects unknown columns and invalid values, with row/parameter context.
  Put notes in `comment`; a misspelling such as `tau_logs` must not silently do nothing.
  Validation also checks disabled rows, so the table can be activated deliberately later.

The exact precedence is:

```text
TOML defaults
  -> nonempty mapping cells, only in individual mode
  -> explicitly supplied command-line model flags
  -> explicit candidate values in an experiment
```

For example, a mapping row can set `shape_adjust_originals=true`, but an explicit
`--shape-adjust-originals off` overrides it for that command. An omitted CLI option does
not erase a mapping value. CLI and experiment overrides do not edit the spreadsheet.

## 3. Daily, historical refill and catchup use the same settings

The engine resolves each curve's complete configuration before learning its historical
state or filling its dates. The same rule applies to daily runs, historical refill,
catchup and a configured-pipeline backtest. Helpers receive their own effective settings
as well, which can affect the evidence they provide through CROSS.

Changing a cell affects future calculations; it does not rewrite stored history silently.
Catchup checks model and calculation-context IDs of existing requested curve/date/target
rows. If stored settings are incompatible, it rejects the mixed configuration and asks
for an explicit refill. Expanding only the target list is an allowed exception: missing
new targets trigger recalculation of the affected date/curve groups. Legacy global rows
without IDs retain their earlier catchup behavior; individual mode cannot infer their
unrecorded settings and requires a refill. Catchup does not declare old results current merely because
the requested date is already present. Preserve a separate output directory when comparing
alternative production policies. Rows already recorded as `missing` are still processed
rows; recalculating them with newly arrived data requires `daily` or `refill`.

Every calculated row contains:

| Column | Meaning |
|---|---|
| `configuration_mode` | `global` or `individual`: how production settings were resolved. |
| `configuration_id` | SHA-256 fingerprint of the complete effective model-parameter values. |
| `configuration_parameters` | JSON containing all 34 effective fields, including inherited values. |
| `configuration_context_id` | Fingerprint of the curve's calculation context, including relevant helper settings when CROSS is enabled. |

The enriched input uses the `curve_` prefix for these columns. Equal model values share the
same ID even across products, modes or filesystem locations. Changing a model value changes
the ID; changing a local path does not. This is a configuration fingerprint, not a hash of
code, input files or EEX publication policy. Preserve those separately; EEX availability is
also recorded in its existing offset/cutoff/as-of columns. The separate context ID includes
mapping/calendar/availability settings and, when CROSS is enabled, other active curves'
effective settings. This detects a changed helper even when the receiving curve's own
34 model values stay unchanged. Its full context document is saved under
`configurations/<configuration_context_id>.json` within your configured output directory.
The context is not a snapshot of input-file contents. See [OUTPUT.md](OUTPUT.md).

## 4. Every individually configurable model field

These **33 scalar controls plus the target list** are supported. Defaults below describe
the supplied TOML. Blank cells inherit its current values, which may differ from defaults.
All numeric values must be finite. Bounds validate the input; they do not guarantee useful
prices. [The algorithm reference](ALGORITHM.md#14-complete-configuration-reference-what-controls-each-decision)
explains each calculation in detail.

| Mapping column | Default | Values / purpose |
|---|---|---|
| `tenors` | Supplied 40-label target list | JSON array of supported tenor strings; target periods to complete. Configurable, not a search dimension. |
| `layer_local` | true | Boolean; today's own anchor adjustment. |
| `layer_correlation` | false | Boolean; learned weights between tenor groups. |
| `layer_cross` | false | Boolean; surprises from eligible other products. |
| `layer_hist` | auto | `on`, `off`, `auto`; use of the historical adjustment. |
| `layer_arbitrage` | false | Boolean; final supported contract reconstruction. |
| `basis_mode` | auto | `auto`, `ratio`, `additive`; adjustment coordinates and selection rule. |
| `min_volume` | 0 | Number >=0; minimum known anchor volume. |
| `tau_log` | 0.5 | Number >0; local distance reach. |
| `other_kind_weight` | 0.6 | Number >=0; weight for another contract kind. |
| `shrink_k` | 1 | Number >0; evidence needed to give more weight to today's anchors. |
| `ewma_halflife_days` | 10 | Number >0; historical own-basis memory in observation updates. |
| `max_anchor_dev` | 0 | Number >=0; common anchor deviation filter; zero disables it. |
| `hist_auto_min_obs` | 10 | Number >=0; effective evidence required by history auto. |
| `ratio_eex_floor` | 1 | Number >0; near-zero EEX threshold for ratio safety. |
| `max_ratio_deviation` | 1 | Number >0; maximum absolute ratio basis. |
| `hist_max_age_days` | 60 | Integer >=1; maximum historical adjustment age in calendar days. |
| `corr_halflife_days` | 20 | Number >0; decay of cross-family paired evidence. |
| `corr_prior_obs` | 8 | Number >=0; prior strength for sparse correlation evidence. |
| `cross_min_corr` | 0.5 | Number from -1 to 1; minimum eligible cross-product correlation. |
| `cross_min_obs` | 8 | Number >=0; minimum effective cross-product evidence. |
| `cross_halflife_days` | 20 | Number >0; decay of cross-product learned relationships. |
| `fallback_price_method` | ewma | `simple`, `ewma`; finite-window EEX price average. |
| `fallback_price_window` | 5 | Integer >=2; complete EEX publication window for prices. |
| `fallback_ewma_halflife` | 2 | Number >0; price-weight half-life in publication observations. |
| `fallback_spread_window` | 9 | Integer >=2; simple-average window for monthly spreads. |
| `fallback_anchor_months` | 2 | Integer >=1; independently averaged months, counted from M+0. |
| `shape_mode` | off | `off`, `audit`, `adjust`; post-refill shape behavior. |
| `shape_adjust_originals` | false | Boolean; permit changing final prices derived from originals. |
| `shape_smoothness_weight` | 1 | Number >=0; penalty on irregular corrections to EEX. |
| `shape_coherence_weight` | 10 | Number >=0; soft aggregation penalty. |
| `shape_max_abs_adjustment` | 10 | Number >0; maximum adjustment in the curve's price unit. |
| `shape_original_weight` | 10 | Number >=1; fidelity to originals when movement is allowed. |
| `shape_coherence_tolerance` | 0.01 | Number >=0; reported aggregate tolerance in the price unit. |

Paths, input column names, EEX availability offset/staleness, warmup and calendar conventions
remain global execution/input settings. They are not product model search controls. Delivery
hours and timezone already belong to each mapping identity. Changing units, calendars or
publication availability changes the meaning of the data and must not be used to win a
parameter search.

## 5. Production cells and MLflow candidate lists are separate

In the notebook, a grid such as `{"tau_log": [0.3, 0.8], "layer_hist": ["off", "auto"]}`
describes four trials. In the mapping, `tau_log=0.8` and `layer_hist=auto` describe one fixed
configuration. Use the notebook's global search for a common candidate, or its individual
search for a proposal per exact product identity. The same grid can be reused for every
product; exceptions to the grid belong to the experiment plan, not the production table.

The notebook or associated JSON defines the experiment scope, products and candidate lists.
A global search deliberately uses global model settings and ignores spreadsheet model
overrides. An individual search uses each curve's effective starting settings under the
production selector. Candidate values override the fields being searched; all others stay
at that fixed base. A product-specific grid replaces the common grid, rather than merging
extra dimensions into it. The 33 scalar model controls can be searched; `tenors` stays fixed.
Read [MLFLOW.md](MLFLOW.md) for the executable plan and [BACKTEST.md](BACKTEST.md) for masking,
scoring, coverage and limits. Inspect the proposal before copying fixed winning values into
the mapping; the experiment does not activate them automatically.

Individual searches keep other curves' signal-generating settings fixed during each trial.
The campaign also checks all selected winners together, including their CROSS interactions,
on the same chronological split. This combined check uses the same reserved dates and is
not a second independent final test or another search. Configurability does not establish
predictive quality, and a short product history may support keeping the global settings
rather than fitting an individual exception.

Anchor filters and ratio guards are among the searchable scalar controls. The campaign
chooses scoring truths with `min_volume` and `max_anchor_dev` disabled, independently of
candidate values. Those values still filter each model's visible evidence and learning.
This prevents a candidate from deleting difficult truths by tightening its filters;
abstentions still affect coverage and the common-case accuracy intersection.
