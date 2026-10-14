# Code review and remediation

Review date: **2026-10-07**. [Versión en español](CODE_REVIEW.es.md).

This review found implementation defects that could change prices, bypass an exclusion,
misstate evaluation results or damage saved outputs. The corrections below have regression
tests. This is an engineering review with synthetic reproductions and source inspection;
it does **not** certify the accuracy of estimated proprietary prices. The real own-price
input is not available in this workspace.

## 1. Scope and interpretation

The review covered the engine, duplicate observations and aliases, EEX matching and contract
reconstruction, configuration, CSV/XLSX date handling, command scheduling, publication,
backtest/tuning, and the inspection notebook. Existing tests were supplemented with
reproductions designed to fail before the fixes.

Severity labels describe impact: **P1** means possible loss or replacement of valid saved
results; **P2** means incorrect calculation, filtering or reporting in a supported case;
**P3** concerns less common numerical/reporting edge cases. They are not a numerical risk
assessment or a claim that every defect has been found.

The changes preserve the existing LOCAL model. Proposed family restrictions, horizon
filters and correlation-based anchor selection remain research topics in [ANCHORS.md](ANCHORS.md).
The optional shape layer remains a separate, bounded adjustment with soft constraints.

## 2. Engine findings and corrections

| Finding | Reproduction and consequence | Implemented correction |
|---|---|---|
| P2: duplicate aggregation depended on row order | Three observations 90,100,110 with missing/zero volumes could average to 97.5,100 or 102.5 instead of 100. Mixed positive/missing volumes had the same problem. | Aggregate the complete observation set with separate effective weights. Positive finite volume supplies its weight; otherwise the averaging weight is 1. Keep reported volume separate and retain unknown volume when all volumes are unknown. |
| P2: the first original alias chose the contract family | On a date where D+1 and BOM describe the same delivery, reversing input rows could change the anchor kind, its history and another target's estimate:106.083542 versus 104.824008 in the reproduction. | Choose a deterministic representative kind, sort/deduplicate alias labels, and make LOO use a label matching that representative kind. The raw input is preserved. |
| P2: arbitrage could reuse an excluded original | A low-volume M+2 original at 900 was excluded when only Q+1 was requested, but could re-enter through the completed target rows when months were also requested. Q+1 became 360.751471. | Apply the rejected-period mask to both routes into reconstruction. Keep the original value in output, but do not use it as reconstruction evidence. |
| P2: minimum-volume filtering depended on EEX availability | An original without a matching EEX quote could bypass the minimum-volume check and later enter arbitrage. | Check known volume before looking for EEX. Known low volume is excluded; unknown volume retains its documented eligibility. |
| P2: a zero-hour Peak head blocked a valid residual | August 2026 Peak had 252 delivery hours. Its initial weekend had zero Peak-month hours; the remaining interval had all 252 hours. The parent price 100 should therefore also price the remainder. | Recognize a zero-hour head without requiring its price. A head with positive delivery hours still needs evidence; incompatible Peak Day/Weekend rules remain protected. |

`CurveFiller` now also validates the complete configuration before calculation, including
when called directly rather than through the CLI. See [fill.py](src/vwaps/fill.py),
[pricer.py](src/vwaps/pricer.py) and [engine regressions](tests/test_engine_audit.py).

**Important boundary:** deterministic aggregation of observed aliases does not make every
unobserved alias use the same estimator. D+1 and BOM can still receive different estimates
for the same dates because their target kinds route differently through LOCAL/HIST/fallback.
That behavior is reproduced in the engine tests. The synthetic comparison now rejects
conflicting predictions for one physical period instead of silently choosing or counting
them twice. Redesigning target-kind routing is an explicit remaining modeling task.

## 3. Input, configuration and publication findings

| Finding | Previous behavior | Implemented correction |
|---|---|---|
| P1: a present, malformed/unreadable EEX file was treated as absent | A rerun could return success and replace a previously estimated 125.4489 with `missing`. | A failure to load a present EEX file aborts before result publication. A nonempty input containing no finite prices or no supported contracts is also rejected. Deliberate absent/empty-file behavior remains separate. |
| P1: direct writes could truncate history or mix generations | An injected write failure reduced a745-byte history to 24 bytes; other output files could already contain new values. | Stage complete CSV/text outputs first, serialize writers with an OS-owned lock, back up existing destinations, replace files atomically one at a time, and attempt rollback on publication failure. |
| P2: explicitly requested weekend dates could disappear | `daily` could succeed without producing the requested Saturday; refill could omit a weekend containing only invalid original prices. | Schedule the explicit daily date and active raw observation dates independently of whether they provide valid anchors. |
| P2: contradictory EEX duplicates used the last row | Prices 100 and 999 under the same publication/delivery key selected whichever happened to appear last. | Reject conflicting finite duplicates; identical repeats are harmless. |
| P2: configuration coercion changed user intent | String `"false"` could become true, a tenor string could become a list of characters, and nonfinite/invalid bounds were accepted. | Validate raw types, finite values, allowed modes, ranges and tenor syntax; do not silently coerce these cases. |
| P2: numeric spreadsheet dates became 1970 | Values such as 45931 or 20260928 could be parsed as Unix nanoseconds. | Reject ambiguous numeric dates and timezone-bearing values. Use the same calendar-date parser for own input, EEX and comparison period metadata. |

Optional backtest truth is validated before publication, and tuning JSON is staged with its
CSV reports. The writer lock protects read/merge/write publication, not just the final rename.
See [publication.py](src/vwaps/publication.py), [publication tests](tests/test_io_publication.py),
[configuration tests](tests/test_config_validation.py) and [date tests](tests/test_input_dates.py).

### Publication limits

- Replacement is atomic **per file**. A set of files is not a filesystem transaction: a
  power loss or forced process termination between replacements can leave different generations.
- Ordinary publication failures, `KeyboardInterrupt` and `SystemExit` attempt rollback.
  Interruption after a rename but before its Python call returns is covered by a regression.
  After successful recovery, the original interruption is propagated. Cleanup failures do
  not hide the primary failure. If recovery also fails, recovery copies
  are retained and their locations are reported. Keep those copies for manual recovery.
- Notebook readers do not acquire the writer lock; a reader may observe a publication in progress.
- The Windows runtime has been exercised. The POSIX lock branch exists but has not been
  executed on Linux in this review.
- Absent files and a valid header-only EEX CSV remain permitted as no-data inputs. Recalculating
  with them can still replace previous estimates with `missing`; this review has not introduced
  a blanket rule that preserves old prices whenever current data is absent. A nonempty file
  with no usable finite/supported quote is rejected. Mixed valid/invalid prices retain valid
  rows with a warning. None of these checks assesses the economic plausibility of every quote.

## 4. Backtest and tuning findings

| Finding | Previous result | Implemented correction |
|---|---|---|
| P2: adjusted originals appeared in missing-price accuracy | An original 100 adjusted to 110 was included under `own+shape`, despite the report describing cells without own prices. | Exclude observed contracts using current and pre-shape provenance, including aliases of an observed period. |
| P2/P3: extra aliases changed synthetic error weighting | Errors 2 and 0 gave MAE 1; adding an identical BOM alias for the first contract changed it to 1.3333. | Count each physical delivery period once when either table supplies period metadata. Reject conflicting predictions, truths or sources. |
| P3: finite large errors produced infinite statistics | Directly squaring 1e200 overflowed; display rounding could also overflow near 1e308. | Scale mean/RMSE arithmetic and use safe display rounding. Reject errors whose actual difference is not representable. |

Legacy comparison tables with **no period metadata in either input** retain per-tenor
counting. Present but incomplete/invalid metadata is an error. Matching tenor labels with
different delivery periods are also an error. Public comparison calls now report missing
required columns with context rather than an incidental `KeyError`.

See [backtest.py](src/vwaps/backtest.py) and
[reporting regressions](tests/test_backtest_reporting_regressions.py).

No future-data leak was identified in the inspected LOO/tuning paths:

- LOO removes the whole held-out period, including its aliases, from own observations and
  both basis-mode anchor sets before mode selection. Shape context uses that masked set.
- Production target dependencies are replayed before extracting the held-out prediction.
- Historical learning occurs after that day's predictions and uses original observations.
- Candidate selection uses earlier dates; only the chosen candidate is scored on the later
  holdout. Earlier holdout originals can inform subsequent days, as in an online process.
- Tuning checks that candidates share the same baseline keys, truths and EEX values.

This is supported by regression tests, not a proof covering every possible execution.

## 5. Notebook and demonstration review

Three separate issues were addressed:

1. The old demonstration data did not contain all contract families. The reproducible
   [demo generator](examples/generate_notebook_demo.py) now provides data spanning the
   supported families and horizons. The viewer still never invents absent contracts.
2. A `None` value used for the all-kinds selector could behave like an unselected widget.
   The notebook uses an explicit sentinel and translates it to the data-selection API.
3. Plotly could clip missing contracts at the edge because no finite trace point established
   their categorical position. The axis now receives all contract categories and an explicit
   range; missing values remain visible gaps, with `connectgaps=False`.

Months, quarters and years sharing a start date keep distinct chart positions. Curve,
unit and region identities remain separated. Absolute-period averages and rolling-label
averages are explicitly different views. An attractive plot is not an accuracy test.
See [NOTEBOOK.md](NOTEBOOK.md) and [visualization tests](tests/test_visualization.py).

## 6. Verification status

The final integrated suite passed **561 tests on Windows with Python 3.14.2**, and the same
**561 tests passed with Python 3.11.1**, the documented minimum Python series. The latter
used NumPy 1.26.4, pandas 2.2.0 and openpyxl 3.1.5, and emitted 30 pandas future warnings about
concatenating empty/all-NA columns; there were no failed tests. These are two runs of the same
suite, not 1,122 distinct tests. Intermediate focused counts are not added to this total.

Relevant checks include row-order permutations, mixed/unknown volumes, zero and negative
prices, Peak delivery rules, whole-period masking, alias conflicts, preservation of originals,
finite extreme arithmetic, invalid EEX/configuration, injected write/replace/recovery failures,
writer locking, explicit weekend runs, and plotting missing contracts.

Run the consolidated suite from the repository root:

```powershell
.venv/Scripts/python.exe -B -m pytest -q
```

All notebook cells and interactive callbacks were exercised using the registered project
`.venv` kernel, with synthetic data across seven dates, three curve identities and all nine
kinds. Both date-range alignment modes, M+0 through M+15, Q+1 through Q+8 and the complete
curve were checked. A rendered complete curve was visually inspected in headless Edge.
This is local execution evidence; it does not certify every browser, Jupyter installation,
operating system or external dataset.

## 7. What remains a model choice or validation risk

- LOCAL can transfer an anchor between families and across distant horizons. Distance and
  volume weighting are heuristics, not evidence that every transfer is useful.
- A tiny positive LOCAL weight can choose the current-EEX adjustment branch instead of
  the temporal EEX fallback. A small anchor contribution can therefore cause a larger route change.
- The monthly fallback's anchor changes at calendar boundaries. Following an absolute
  contract avoids label confusion but does not itself remove that formula discontinuity.
- History pools observations by kind/group rather than learning an independent seasonal
  basis for every delivery contract. Its configured memory is not a guarantee of relevance.
- Covariance evidence ages when new paired observations arrive; a stale relationship is
  not independently revalidated merely because the current day advances.
- The shape layer uses current `eex_settle` as its reference and soft aggregate penalties.
  A coherence tolerance diagnoses a remaining gap; it does not impose equality or feasibility.
- Estimated cross-kind aliases may disagree, as described above. Reporting now exposes
  that conflict; the underlying routing has not been redesigned in this review.
- LOO does not reproduce persistent missing blocks or entire families/days without prices.
  Coverage-first selection and the common-candidate intersection remain explicit tuning policies.
- Confidence values are heuristics, not calibrated error bounds. Modified shape prices have
  no calibrated confidence. Synthetic successes do not establish a best real-data configuration.

See [ANCHORS.md](ANCHORS.md), [BACKTEST.md](BACKTEST.md) and [SHAPE.md](SHAPE.md) for these
boundaries and proposed evaluations. Selecting a production refill still requires real own-price
validation, especially on roll dates, sparse curves and changing market regimes.

## 8. Reproducibility and remaining operational work

The project is intentionally run from source with Python 3.11+, not installed as a built
package. `pyproject.toml`, `uv.lock` and the documented environment setup are consistent
with that workflow. Inputs, screenshots and generated output are ignored; no ZIP is required.

Tuning records the base configuration and input pattern, but a stronger run manifest would
also record content hashes, repository commit and dependency versions. Automated CI across
the supported Python/OS combinations is still an improvement opportunity; local passing
tests should not be described as cross-platform certification.

Some original reproductions live under ignored `output/code_audit/`. The tracked regression
tests, source changes and demo generator provide reproducible evidence in a clean checkout;
the ignored local artifacts are supplementary and are not required production inputs.
