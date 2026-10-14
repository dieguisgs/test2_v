# Which observations should anchor a missing power price?

[Español](ANCHORS.es.md) · [Algorithm](ALGORITHM.md) · [Backtest guide](BACKTEST.md) · [Shape layer](SHAPE.md)

**Research note, not a new production model.** An observed price can be useful for some
missing contracts and misleading for others. Neither “use every nearby observation” nor
“never mix contract families” wins universally. This note separates current software,
controlled counterexamples and proposed evaluation. No new eligibility settings are implemented.

## 1. The information we are trying to transfer

LOCAL transfers the observed **adjustment to EEX**, not the own price itself. In additive
mode, an own month at 110 against EEX 100 supplies a +10 adjustment. A target with EEX 150
could use part of that +10, depending on distance, volume, type and shrinkage. It does not
simply copy 110. Ratio mode instead transfers a proportional adjustment.

The important question is therefore: **does this observation's own-minus-EEX adjustment
help predict the target's own-minus-EEX adjustment?** Correlated EEX prices answer a different
question. In `Own = EEX + basis`, strong correlation between two EEX series does not determine
the correlation between their basis series. Both market prices can move together while the
own trading samples have independent deviations.

EEX already supplies each target's market reference. An EEX-only correlation might become a
tested prior about relevance, but it does not establish the best own anchor or validate a
refill model.

## 2. What the current LOCAL calculation does

Within one date and `(product, region, unit)`, valid own/EEX pairs receive weights from log
volume, contract kind and distance in log time to delivery. Same kind has factor 1; another
kind normally has `other_kind_weight=0.6`. Distance measures period midpoints, not delivery
overlap. The total weight also controls shrinkage, `w=W/(W+shrink_k)`.

Consequently, filtering an anchor changes **three things**: which adjustments are averaged,
their total strength, and possibly the entire pricing route. A tiny valid LOCAL contribution
can prevent entry into the historical-price EEX fallback.

With correlation enabled, the software learns relationships between **basis surprises by
contract group**, not separate EEX-only relationships between every M+1/M+4 pair. A surprise
is today's observed adjustment minus its earlier historical mean. Correlation changes a
weight factor; it does not introduce a new price estimator or learn a target-specific family ban.

## 3. Controlled experiments: useful counterexamples, not a winner

The local artifact `output/anchor_family_review/run_experiment.py` ran the actual engine
through an experimental subclass: **four scenarios × four policies = 16 runs, 208 assertions**.
All inputs and hidden truths were synthetic. Reference date was 2026-09-30, anchor volumes
100, additive mode forced, `tau_log=0.5`, `other_kind_weight=0.6`, `shrink_k=1`.
History, correlation, cross, arbitrage and shape were off. Nine complete EEX publication
dates supported the normal five-price EWMA and nine-spread fallback windows.

| Experimental policy | What it changes |
|---|---|
| `current` | Existing weights. |
| `block_short_to_month_plus` | Zero weight from short-group anchors into month/quarter/long targets. |
| `same_kind_only` | Only identical kinds contribute: Month can inform Month, not Quarter. |
| `containment_cross_kind` | Blocks short-to-long and allows different Month/Quarter/Year kinds only when one delivery interval contains the other; same-kind neighbors retain existing weights. |

These labels are experiment names, not configuration keys. The prototype masks weights; it
does not turn quarters into aggregate equations. Strict same-kind LOCAL weighting can already
be obtained using the existing `other_kind_weight=0` with correlation off, but that is not a
new end-to-end family-isolation guarantee. Observed anchors here are Day and Month; the
experiment does not empirically test Quarter/Year as source families.

| Synthetic assumption | Actual result | Interpretation |
|---|---|---|
| Every contract has basis +12; D+1 and M+1 observed | Q+1 truth 112; current 103.2764; same-kind 100 | A blanket family ban can discard useful shared information. |
| Day basis +40, Month +10, Quarter −20, Year −25 | Q+1 truth 80; current 102.8942; same-kind 100 | Transferring monthly basis can point the wrong way. Blocking it still does not reveal the missing −20. |
| Only October–November carry premium +30; October observed | January M+4 truth 100; current and same-kind both 103.1634 | A family filter does not stop an inappropriate near-to-far transfer within Month. |
| Same localized premium, with hours-consistent quarterly/yearly truth | Cal+1 truth 100: current 100.3291, containment 100. Q+1 truth 119.8959: current 108.0713, same-kind 100 | Delivery relationship can matter; discarding every Month→Quarter link can also hurt. |

The separate-families scenario deliberately represents different observed VWAP relationships,
not one coherent valuation surface. The localized-premium scenario derives aggregate truth
from monthly prices and actual delivery hours. Every tested missing target received a finite
estimate because fallback windows were complete; this does not establish unchanged coverage
with sparse real data. The assertions checked mechanics, not real predictive accuracy.

## 4. A weak day anchor can change the EEX reference route

In the fourth scenario, EEX rises through nine publications: 80, 82, …, 96. The sole observed
D+1 is 98, supplying basis +2. Current output is **M+4=96.002883** and
**Cal+1=96.000453**. The distant local addition is tiny, yet the calculation starts from
current EEX 96.

Blocking that day anchor selects smoothed EEX **93.318945**, the normalized half-life-2 EWMA
of the last five prices 88, 90, 92, 94, 96. Most of the roughly 2.68 change comes from changing
the temporal reference, not removing the small own correction.

The same visible observations support opposite conclusions under two possible hidden worlds:

| Hidden prices of the four withheld longer contracts | Current MAE | Block-short MAE |
|---|---:|---:|
| They remain at 90 | 6.031306 | 3.318945 |
| They follow latest EEX +2, reaching 98 | 1.968694 | 4.681055 |

The observed D+1 remains 98 in both worlds; only the unobserved targets differ. The engine
never receives those truths. The counterexample demonstrates why an EEX-only sample cannot
decide which route should win. Reviewing weak-evidence routing is a proposal, not a fix
implemented by this experiment.

## 5. Proposed eligibility should consider direction and horizon

A possible design would first decide whether an anchor is relevant to a target, then weight
eligible anchors. This is **not implemented as a new policy**.

- D+1→nearby short periods and D+1→next calendar year are different questions; a temporary day
  disturbance need not represent a persistent curve-wide adjustment.
- Month→near Month and Month→distant Month also differ, even though their kinds match.
- An October observation covers part of Q4, whereas a Q4 observation constrains an average
  over three months. Neither direction identifies every missing monthly price.
- A month within a year can inform that aggregate without determining its other eleven months.
  Lack of overlap does not prove the absence of a common market-wide own basis.

These are hypotheses for testing, not proven bans. A directional family/horizon rule should
be explicit and auditable, rather than assuming one universal cutoff or symmetric relevance.

**Auto-selection pitfall.** Current `auto` examines mode-specific anchor lists before the
target's local weights are applied. Suppose target M+2 has EEX 100. M+1 own 0/EEX 100 is a
valid additive anchor but not a ratio anchor. A Day own 110/EEX 100 is ratio-valid, but a
proposed family rule would exclude it for M+2. Merely zeroing the Day's weight afterwards can
still make auto choose ratio, leaving no usable local ratio contribution. A true eligibility
implementation would need to filter **both ratio and additive lists before selecting the
mode**, not only multiply final weights by zero. This interaction was not tested by the
16 additive-only runs.

## 6. Family isolation must include history and cross

A LOCAL-only mask does not isolate the entire pipeline. Historical basis is stored by kind,
then falls back to group and `all` within the curve. A Month historical bucket also pools
different monthly horizons; it is not solely the same absolute contract's history.

Cross uses group surprises and may fall back to `all`. Disabling the historical price
contribution does not erase its internal historical state or automatically disable cross.
Therefore an intended “short observations must never inform long prices” policy would
need a consistent treatment of LOCAL, mode selection, historical pooling and cross.
This is an implementation consideration, not a request to replace them with multiple models.

## 7. Regions: separate evidence does not require separate launches

The current identity `(product, region, unit)` already separates original observations,
mapping, LOCAL and history. With cross off by default, one refill command does not merge
regional anchors. Separate runs become useful if regions require different configurations;
their usefulness must be validated, not inferred from country names alone. Cross, if explicitly
enabled, transfers learned basis surprises subject to its rules, not another region's raw
price as an automatic substitute.

## 8. What the primary literature supports

- [Lucia and Schwartz, author version of *Electricity prices and power derivatives*](https://escholarship.org/content/qt12w8v7jj/qt12w8v7jj_noSplash_7b7e7e1b016e6500b1e82cd9219021fa.pdf), §§3–4: seasonal patterns, temporary spot shocks and distinct short/long factors motivate checking horizon relevance. Their one-factor prices are perfectly correlated; introducing another factor permits different maturity responses. Their historical Nordic sample is not validation of our own VWAPs.
- [Kemper, Schmeck and Balci, *The Market Price of Risk for Delivery Periods*](https://arxiv.org/html/2002.07561v3), §§2 and 3.2: delivery averaging and Samuelson volatility depend on delivery length and proximity. Different volatility alone does not prove low correlation or useless anchors. Its geometric pricing construction is not our arithmetic-hours refill.
- [Kiesel, Schindlmayr and Börger, *A two-factor model for the electricity forward market*](https://www.tandfonline.com/doi/abs/10.1080/14697680802126530): the publisher abstract describes delivery periods, volatility term structure and option calibration. It supports considering those dimensions, not a production eligibility rule.

The available EEX-only study has 42 publications, August 10–October 6, 2026. It compares
changes of fixed absolute contracts, not a rolling M+1 series silently changing delivery.
The correlations reported in [BACKTEST.md](BACKTEST.md) do not measure own-basis transfer;
41 price-change pairs cannot validate a universal family ranking.

## 9. A limited, useful validation plan

Keep the existing method as a baseline and compare a small set of explicit eligibility
hypotheses. Use the same hidden own targets and report results by identity, source family,
target family and horizon. Include isolated gaps, blocks, only-short-anchor days and days
with no own anchors. Group all aliases and remove every alias of a hidden observation before
any learning that could otherwise reveal it; permanent block masking remains proposed tooling.

Separate earlier calibration from later evaluation. Measure own-price error, large errors,
coverage, output-route changes and fixed-contract rollover behavior. Report history and
cross effects separately before claiming whole-pipeline isolation. Changing filters must not
improve a score merely by removing difficult targets from its denominator.

With no real own history available here, **no policy or anchor family is validated as the
winner**. More rows are useful only if they supply relevant dates, horizons, regimes and
paired own/EEX observations.

Local evidence files are `output/anchor_family_review/metadata.json`, `predictions.csv`,
`errors_by_target.csv`, `anchor_weights.csv` and `scenario_summary.csv`, plus synthetic inputs.
These artifacts and the experiment script are Git-ignored, not distributed with the repository.
In a workspace containing them, reproduce with
`.venv/Scripts/python.exe -B output/anchor_family_review/run_experiment.py`.
