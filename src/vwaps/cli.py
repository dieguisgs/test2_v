"""Command-line interface. See run.py for entry-point examples."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from vwaps.backtest import compare_truth, summarize_loo
from vwaps.config import Config, load_config, validate_config
from vwaps.dates import parse_reference_dates
from vwaps.enrich import enrich_input
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, load_eex_books
from vwaps.io_vwap import load_input
from vwaps.identity import CurveKey, IDENTITY_COLUMNS, curve_keys, normalize_identity
from vwaps.log import get_logger, setup_logging
from vwaps.mapping import (COLUMNS, ProductMap, check_mapping, eex_path, guess_row,
                           load_mapping, migrate_legacy_mapping, read_mapping_table)
from vwaps.product_config import (
    MODEL_PARAMETER_FIELDS, configuration_context_records, configuration_record, effective_config,
)
from vwaps.publication import CsvBatch, publication_lock
from vwaps.synthetic import make_synthetic
from vwaps.tenors import parse_tenor, resolve_tenor

SOURCES = ["own", "eex+local", "eex+cross", "eex+hist", "eex+smooth", "eex", "arbitrage", "missing"]
KEYS = ["reference_date", *IDENTITY_COLUMNS]
ENRICHED_KEYS = [f"curve_{name}" for name in KEYS]


def main(argv: list[str], config_path: Path) -> int:
    ap = argparse.ArgumentParser(prog="run.py", description="Power curve filling: own VWAPs + EEX")
    ap.add_argument("--config", type=Path, default=config_path)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("mapping", help="create/update and validate the configured product mapping")
    p.add_argument("--vwap", default=None, help="VWAP file(s); override config.toml")
    p.add_argument("--with-parameters", action="store_true",
                   help="add editable model-parameter columns; blank cells inherit global values")
    p.add_argument("--export", type=Path, default=None,
                   help="export the mapping to a new CSV/XLSX path, without changing paths.mapping")

    p = sub.add_parser("daily", help="fill one reference date (default: today)")
    p.add_argument("--date", type=date.fromisoformat, default=None)
    p.add_argument("--vwap", default=None)

    p = sub.add_parser("refill", help="fill a historical date range")
    p.add_argument("--from", dest="start", type=date.fromisoformat, default=None)
    p.add_argument("--to", dest="end", type=date.fromisoformat, default=None)
    p.add_argument("--vwap", default=None)

    p = sub.add_parser("catchup", help="process pending date/curve groups through today")
    p.add_argument("--from", dest="start", type=date.fromisoformat, default=None)
    p.add_argument("--to", dest="end", type=date.fromisoformat, default=None)
    p.add_argument("--vwap", default=None)

    p = sub.add_parser("status", help="summarize the filled curve history")
    p.add_argument("--last", type=int, default=10, help="number of dates to display")

    p = sub.add_parser("backtest", help="leave-one-out prediction errors by method")
    p.add_argument("--from", dest="start", type=date.fromisoformat, default=None)
    p.add_argument("--to", dest="end", type=date.fromisoformat, default=None)
    p.add_argument("--vwap", default=None)
    p.add_argument("--truth", default=None, help="synthetic ground truth from make-synthetic")

    p = sub.add_parser("detect-conventions", help="compare D+n / WE+n conventions against EEX")
    p.add_argument("--vwap", default=None)

    p = sub.add_parser("tune", help="compare parameter combinations and validate the selected configuration on later dates")
    p.add_argument("--from", dest="start", type=date.fromisoformat, default=None)
    p.add_argument("--to", dest="end", type=date.fromisoformat, default=None)
    p.add_argument("--vwap", default=None)
    p.add_argument("--basis-modes", default="auto,ratio,additive", help="comma-separated calculation modes")
    p.add_argument("--tau-log", default=None, help="comma-separated positive anchor-distance parameters; default: configured value")
    p.add_argument("--shrink-k", default=None, help="comma-separated positive shrinkage parameters; default: configured value")
    p.add_argument("--hist-modes", default=None, help="comma-separated on/off/auto; default: configured value")
    p.add_argument("--correlation", default=None, help="comma-separated off/on; default: configured value")
    p.add_argument("--cross", default=None, help="comma-separated off/on; default: configured value")
    p.add_argument("--validation-days", type=int, default=20,
                   help="number of latest distinct observation dates reserved for validation")
    p.add_argument("--max-trials", type=int, default=50, help="maximum number of parameter combinations")

    p = sub.add_parser("make-synthetic", help="generate synthetic VWAPs from EEX for testing")
    p.add_argument("--areas", default="DE,FR,NL,AT", help="comma-separated areas")
    p.add_argument("--profiles", default="Base,Peak")
    p.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2000, 1, 1))
    p.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2100, 1, 1))
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--mode", choices=["ratio", "additive"], default="ratio",
                   help="how to generate the VWAP-EEX difference (controls synthetic model bias)")
    p.add_argument("--out", default="data")

    for command in ("mapping", "daily", "refill", "catchup", "backtest"):
        sub.choices[command].add_argument(
            "--configuration-mode", choices=("global", "individual"), default=None,
            help="global ignores table model settings; individual applies each curve's parameter cells")

    for command in ("daily", "refill", "catchup", "backtest", "tune"):
        parser = sub.choices[command]
        parser.add_argument("--eex-offset-days", type=int, default=None,
                            help="latest allowed EEX publication is reference date plus this calendar-day "
                                 "offset (0 or negative); staleness remains measured from the reference date")
        group = parser.add_argument_group("EEX fallback transformation (override config for this run)")
        group.add_argument("--eex-price-method", dest="fallback_price_method", choices=("simple", "ewma"),
                           default=None, help="finite-window price average: equal or exponential weights")
        group.add_argument("--eex-price-window", dest="fallback_price_window", type=int, default=None,
                           help="complete price window in EEX publication dates, at least 2")
        group.add_argument("--eex-ewma-halflife", dest="fallback_ewma_halflife", type=float, default=None,
                           help="positive exponential weight half-life in observations within the price window")
        group.add_argument("--eex-spread-window", dest="fallback_spread_window", type=int, default=None,
                           help="complete simple-average monthly spread window, at least 2 dates")
        group.add_argument("--eex-anchor-months", dest="fallback_anchor_months", type=int, default=None,
                           help="directly averaged months from the current calendar month, at least 1")
        group = parser.add_argument_group("Optional curve shape adjustment (override config for this run)")
        group.add_argument("--shape-mode", choices=("off", "audit", "adjust"), default=None,
                           help="disable, propose without applying, or apply shape adjustments")
        group.add_argument("--shape-adjust-originals", choices=("on", "off"), default=None,
                           help="allow or forbid changes to final prices derived from original observations")
        group.add_argument("--shape-smoothness-weight", type=float, default=None,
                           help="nonnegative penalty on irregular adjustment to raw EEX")
        group.add_argument("--shape-coherence-weight", type=float, default=None,
                           help="nonnegative soft aggregation penalty; does not guarantee exact equality")
        group.add_argument("--shape-max-abs-adjustment", type=float, default=None,
                           help="positive maximum price change in each curve's price unit")
        group.add_argument("--shape-original-weight", type=float, default=None,
                           help="fidelity weight for original prices when adjustment is allowed, at least 1")
        group.add_argument("--shape-coherence-tolerance", type=float, default=None,
                           help="nonnegative diagnostic tolerance for aggregate discrepancies in price units")

    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        setup_logging(cfg.output_dir / "_logs", " ".join(["run.py", *argv]))
        overrides = {name: getattr(a, name) for name in (
            "fallback_price_method", "fallback_price_window", "fallback_ewma_halflife",
            "fallback_spread_window", "fallback_anchor_months",
            "shape_mode", "shape_adjust_originals", "shape_smoothness_weight",
            "shape_coherence_weight", "shape_max_abs_adjustment", "shape_original_weight",
            "shape_coherence_tolerance", "eex_offset_days", "configuration_mode",
        ) if getattr(a, name, None) is not None}
        if "shape_adjust_originals" in overrides:
            overrides["shape_adjust_originals"] = overrides["shape_adjust_originals"] == "on"
        cfg = replace(cfg, **overrides, command_overrides={
            name: value for name, value in overrides.items() if name in MODEL_PARAMETER_FIELDS})
        validate_config(cfg)
        return {
            "mapping": cmd_mapping, "daily": cmd_daily, "refill": cmd_refill,
            "catchup": cmd_catchup, "status": cmd_status,
            "backtest": cmd_backtest, "detect-conventions": cmd_detect,
            "make-synthetic": cmd_synthetic,
            "tune": cmd_tune,
        }[a.cmd](cfg, a)
    except (OSError, ValueError) as exc:
        get_logger().error(str(exc))
        return 1


# ---------------------------------------------------------------- helpers
def _out(*parts) -> None:
    get_logger().info(" ".join(str(p) for p in parts))


def _table(df: pd.DataFrame, index: bool = False) -> str:
    with pd.option_context("display.max_rows", 500, "display.width", 250,
                           "display.max_colwidth", 60):
        return df.to_string(index=index)


def _source_order(sources) -> list[str]:
    """Keep known sources first without dropping additional transformation sources."""
    present = set(sources)
    return [name for name in SOURCES if name in present] + sorted(present - set(SOURCES))


def _books(cfg: Config, maps: list[ProductMap]) -> dict[CurveKey, EexBook | None]:
    return load_eex_books(cfg, maps)


def _load(cfg: Config, override: str | None):
    """Load VWAPs, original rows, mapping and EEX; report unmatched products."""
    vw, raw = load_input(override or cfg.vwap_input, cfg)
    maps = load_mapping(cfg)
    known = {m.key for m in maps}
    new = sorted(set(curve_keys(raw, cfg)) - known)
    if new:
        get_logger().warning(f"{len(new)} input curves are not mapped (preserved in enriched output; "
                             f"add them with `python run.py mapping`): {new[:10]}"
                             + (" ..." if len(new) > 10 else ""))
    fill = [m.key for m in maps if m.active and m.use == "fill"]
    helpers = [m.key for m in maps if m.active and m.use == "helper"]
    _out(f"Curves to fill: {len(fill)}" + (f" | helpers: {len(helpers)}" if helpers else ""))
    active = {m.key for m in maps if m.active}
    vw = vw[curve_keys(vw).isin(active)].reset_index(drop=True)
    return vw, raw, maps, _books(cfg, maps)


def _upsert(path: Path, new: pd.DataFrame, replaced_groups: pd.DataFrame | None = None,
            *, batch: CsvBatch | None = None) -> None:
    if new.empty and (replaced_groups is None or replaced_groups.empty):
        return
    if new.empty and not path.exists():
        return  # No prior rows or column schema to preserve.
    replaced = new[KEYS] if replaced_groups is None else replaced_groups[KEYS]
    if path.exists():
        _validate_output_schema(path, KEYS)
        old = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        if "product" in old.columns:
            old["reference_date"] = pd.to_datetime(old["reference_date"]).dt.date
            done = set(map(tuple, replaced.drop_duplicates().to_numpy()))
            old = old.loc[[tuple(r) not in done for r in old[KEYS].to_numpy()]]
            new = pd.concat([old, new], ignore_index=True) if not new.empty else old
    new = new.sort_values(KEYS + ["delivery_start"] if "delivery_start" in new else KEYS)
    if batch is None:
        with CsvBatch() as standalone:
            standalone.stage(path, new)
    else:
        batch.stage(path, new)


def _require_complete(res) -> None:
    if res.errors:
        products = ", ".join(sorted({error["product"] for error in res.errors}))
        raise ValueError(f"Incomplete run: {len(res.errors)} errors in {products}. "
                         "No results written; check the log and run again.")


def _merge_enriched(path: Path, new: pd.DataFrame, *, batch: CsvBatch | None = None) -> None:
    """Replace date/curve groups without aggregating or sorting original rows."""
    keys = ENRICHED_KEYS
    if path.exists():
        _validate_output_schema(path, keys)
        # Read text to preserve identifiers such as 001 when rewriting
        # earlier dates that are outside the current run.
        old = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        old["curve_reference_date"] = pd.to_datetime(old["curve_reference_date"]).dt.date
        done = set(map(tuple, new[keys].drop_duplicates().to_numpy()))
        old = old[[tuple(row) not in done for row in old[keys].to_numpy()]]
        new = pd.concat([old, new], ignore_index=True)
    if batch is None:
        with CsvBatch() as standalone:
            standalone.stage(path, new)
    else:
        batch.stage(path, new)


def _validate_output_schema(path: Path, keys: list[str]) -> None:
    """Reject ambiguous legacy histories before any result files are written."""
    if not path.exists():
        return
    columns = pd.read_csv(path, encoding="utf-8-sig", nrows=0).columns
    missing = [key for key in keys if key not in columns]
    if missing:
        raise ValueError(f"Legacy output {path} lacks curve identity columns {missing}. "
                         "Set paths.output_dir to a new directory and run refill to rebuild "
                         "with product, region and unit. No result files written.")


def _write(cfg: Config, res, enriched: pd.DataFrame) -> None:
    _require_complete(res)
    with publication_lock(cfg.output_dir), CsvBatch() as batch:
        _stage_outputs(cfg, res, enriched, batch)


def _stage_outputs(cfg: Config, res, enriched: pd.DataFrame, batch: CsvBatch) -> None:
    """Read and merge every affected output while holding the writer lock."""
    out = cfg.output_dir
    for context_id, document in getattr(res, "configuration_contexts", {}).items():
        if len(context_id) != 64 or any(character not in "0123456789abcdef" for character in context_id):
            raise ValueError("Invalid configuration context identifier; no results written")
        destination = out / "configurations" / f"{context_id}.json"
        content = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if destination.exists():
            if json.loads(destination.read_text(encoding="utf-8")) != document:
                raise ValueError(f"Configuration context snapshot conflicts with its identifier: {destination}")
        else:
            batch.stage_text(destination, content)
    for name in ("filled_history.csv", "consistency_history.csv"):
        _validate_output_schema(out / name, KEYS)
    _validate_output_schema(out / "enriched_history.csv", ENRICHED_KEYS)
    if not res.filled.empty:
        for day in res.filled["reference_date"].unique():
            _validate_output_schema(out / "filled" / f"{day}.csv", KEYS)
    if not enriched.empty:
        for day in enriched["curve_reference_date"].unique():
            _validate_output_schema(out / "enriched" / f"{day}.csv", ENRICHED_KEYS)
    (out / "filled").mkdir(parents=True, exist_ok=True)
    groups = res.filled.groupby("reference_date") if not res.filled.empty else []
    for day, g in groups:
        f = out / "filled" / f"{day}.csv"
        if f.exists():  # Preserve curves that were not recalculated on this date.
            old = pd.read_csv(f, encoding="utf-8-sig", dtype=str, keep_default_na=False)
            if "product" in old.columns:
                old = old[~curve_keys(old).isin(set(curve_keys(g)))]
                g = pd.concat([old, g], ignore_index=True)
        batch.stage(f, g)
    _upsert(out / "filled_history.csv", res.filled, batch=batch)
    recalculated = res.filled[KEYS] if not res.filled.empty else None
    _upsert(out / "consistency_history.csv", res.consistency, recalculated, batch=batch)
    if not enriched.empty:
        for day, g in enriched.groupby("curve_reference_date", sort=False):
            _merge_enriched(out / "enriched" / f"{day}.csv", g, batch=batch)
        _merge_enriched(out / "enriched_history.csv", enriched, batch=batch)


def _print_summary(filled: pd.DataFrame) -> None:
    if filled.empty:
        _out("No curve points to fill in this range.")
        return
    n_prod = len(set(curve_keys(filled)))
    n_days = filled["reference_date"].nunique()
    # Summarize by product for large runs; daily details remain in filled_history.csv / status.
    index = IDENTITY_COLUMNS if n_prod > 1 and n_days > 1 else KEYS
    t = filled.pivot_table(index=index, columns="source", values="tenor", aggfunc="count", fill_value=0)
    t = t.reindex(columns=_source_order(t.columns))
    if "missing" in t.columns:
        t["% missing"] = (100 * t["missing"] / t.sum(axis=1)).round(1)
    _out(_table(t, index=True))
    miss = filled[filled["source"] == "missing"]
    if not miss.empty:
        by = miss.groupby("tenor").size().sort_values(ascending=False)
        _out("\nUnfilled tenors (cells): " + ", ".join(f"{k}={v}" for k, v in by.items()))
    n = len(filled)
    _out(f"\n{n} cells | " + " | ".join(
        f"{s} {100 * (filled['source'] == s).sum() / n:.1f}%"
        for s in _source_order(filled["source"])))


def _layers(cfg: Config) -> str:
    on = [n for n, v in [("local", cfg.layer_local), ("correlation", cfg.layer_correlation),
                         ("cross", cfg.layer_cross), ("arbitrage", cfg.layer_arbitrage)] if v]
    return (f"Configuration: {cfg.configuration_mode}; global defaults: "
            f"layers={', '.join(on)} | hist={cfg.layer_hist} | mode={cfg.basis_mode} "
            f"| shape={cfg.shape_mode} | shape_adjust_originals={cfg.shape_adjust_originals} "
            f"| eex_offset_days={cfg.eex_offset_days}")


# --------------------------------------------------------------- commands
def cmd_mapping(cfg: Config, a) -> int:
    """Append exact curve identities while preserving manual mapping choices."""
    vw, raw = load_input(a.vwap or cfg.vwap_input, cfg)
    identities = normalize_identity(raw, cfg).drop_duplicates()
    products = sorted(set(curve_keys(identities)))
    path = cfg.mapping_file
    migrated = False
    if path.exists():
        cur = read_mapping_table(path)
        migrated = any(name not in cur for name in IDENTITY_COLUMNS)
        cur = migrate_legacy_mapping(cur, identities)
    else:
        cur = pd.DataFrame(columns=COLUMNS)
    if "comment" not in cur:
        cur["comment"] = ""
    new = [p for p in products if p not in set(curve_keys(cur))]
    if new:
        cur = pd.concat([cur, pd.DataFrame([
            guess_row(p, cfg, region=r, unit=u) for p, r, u in new])], ignore_index=True)
    parameter_columns_added = False
    if getattr(a, "with_parameters", False):
        for name in MODEL_PARAMETER_FIELDS:
            if name not in cur:
                cur[name] = ""
                parameter_columns_added = True
    export = getattr(a, "export", None)
    if export is not None:
        export = cfg.resolve(export).resolve()
        if export.exists():
            raise ValueError(f"Mapping export already exists: {export}; choose a new filename")
        if export.suffix.lower() not in (".csv", ".xlsx"):
            raise ValueError("Mapping export must have a .csv or .xlsx extension")
    if new or migrated or parameter_columns_added or export is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with CsvBatch() as batch:
            columns = [*COLUMNS, *[name for name in cur if name not in COLUMNS]]
            destinations = ([path] if new or migrated or parameter_columns_added else [])
            if export is not None:
                destinations.append(export)
            for destination in destinations:
                if destination.suffix.lower() == ".xlsx":
                    batch.stage_excel(destination, cur[columns])
                else:
                    batch.stage(destination, cur[columns])
        _out(f"{len(new)} new curves added to {path} (draft: review these entries)"
             + ("; legacy mapping migrated to product/region/unit" if migrated else ""))
        if export is not None:
            _out(f"Mapping exported to {export}. Set paths.mapping to that file to use it.")
    else:
        _out(f"{path}: no new curves")
    maps = load_mapping(cfg)
    chk = check_mapping(maps, cfg, vw)
    _out("\n" + _table(chk))
    gone = [m.label for m in maps if m.key not in set(products)]
    if gone:
        get_logger().warning(f"Mapped but absent from the VWAP input: {', '.join(gone)}")
    bad = chk[(chk["use"] != "off") & (chk["eex"] == "MISSING")]
    if not bad.empty:
        get_logger().error(f"{len(bad)} products refer to a missing EEX file")
    _out("\nReview the mapping (use = fill / helper / off, eex_file, hours, timezone), then run "
         "`python run.py mapping` again to validate it.")
    return 0


def cmd_refill(cfg: Config, a) -> int:
    vw, raw, maps, books = _load(cfg, a.vwap)
    eex_dates = [d for b in books.values() if b for d in b.trade_dates]
    date_col = cfg.vwap_columns.get("reference_date", "reference_date")
    dates = list(parse_reference_dates(raw[date_col]).dt.date) + eex_dates
    if not dates and (a.start is None or a.end is None):
        raise ValueError("No input or EEX dates available; specify --from and --to")
    start = a.start or min(dates)
    end = a.end or max(dates)
    first_eex = min(eex_dates) if eex_dates else None
    _out(f"Refill {start} -> {end} | VWAPs: {len(vw)} rows | EEX starts on {first_eex}")
    _out(_layers(cfg))
    if first_eex and start < first_eex:
        get_logger().warning(f"no EEX before {first_eex}: those dates use own VWAPs "
                             f"(contract reconstruction enabled: {cfg.layer_arbitrage})")
    days = _reference_days(cfg, raw, maps, books, start, end)
    output_keys = {(day, m.key) for day in days for m in maps if m.active and m.use == "fill"}
    res = CurveFiller(cfg, vw, maps, books).run(start, end, output_keys=output_keys)
    _require_complete(res)
    enriched = enrich_input(raw, res.filled, cfg, maps, start, end)
    _write(cfg, res, enriched)
    _print_summary(res.filled)
    _out(f"\nWritten to {cfg.output_dir}")
    return 0


def cmd_daily(cfg: Config, a) -> int:
    day = a.date or date.today()
    vw, raw, maps, books = _load(cfg, a.vwap)
    _out(_layers(cfg))
    if not (vw["date"] == day).any():
        get_logger().warning(f"no own VWAPs on {day}: using EEX and available historical adjustments")
    output_keys = {(day, m.key) for m in maps if m.active and m.use == "fill"}
    res = CurveFiller(cfg, vw, maps, books).run(day, day, output_keys=output_keys)
    _require_complete(res)
    enriched = enrich_input(raw, res.filled, cfg, maps, day, day)
    _write(cfg, res, enriched)
    _print_summary(res.filled)
    _out(f"\nWritten to {cfg.output_dir} (curve and enriched original rows)")
    return 0


def _history_point_keys(path: Path, enriched: bool = False, *,
                        eex_offset_days: int | None = None,
                        required_points: set[tuple] | None = None,
                        configurations: dict[CurveKey, dict] | None = None) -> set[tuple]:
    """Read processed keys and reject reuse with another EEX availability policy.

    Presence includes missing prices. Legacy rows without a policy column or
    with an empty policy cell were calculated with offset zero. Policy checks
    apply only to requested targets, excluding unrelated dates and raw rows
    whose contracts were not calculated by the engine.
    """
    if not path.exists():
        return set()
    prefix = "curve_" if enriched else ""
    columns = [f"{prefix}{name}" for name in [*KEYS, "tenor"]]
    _validate_output_schema(path, columns)
    policy_column = f"{prefix}eex_offset_days"
    configuration_column = f"{prefix}configuration_id"
    parameters_column = f"{prefix}configuration_parameters"
    context_column = f"{prefix}configuration_context_id"
    header = pd.read_csv(path, encoding="utf-8-sig", nrows=0).columns
    read_columns = [*columns, *[name for name in (policy_column, configuration_column, parameters_column, context_column)
                               if name in header]]
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False,
                        usecols=read_columns)
    frame = frame.rename(columns={column: column.removeprefix(prefix) for column in columns}) if prefix else frame
    dates = pd.to_datetime(frame["reference_date"], format="mixed", errors="coerce").dt.date
    if dates.isna().any():
        raise ValueError(f"Output history {path} contains empty or invalid reference dates")
    points = [(day, *curve, str(tenor).strip())
              for day, curve, tenor in zip(dates, curve_keys(frame), frame["tenor"])]
    if eex_offset_days is not None:
        policies = frame[policy_column] if policy_column in frame else [""] * len(frame)
        for point, value in zip(points, policies):
            if required_points is not None and point not in required_points:
                continue
            text = str(value).strip()
            try:
                saved = float(text) if text else 0.0
            except ValueError:
                saved = math.nan
            if not math.isfinite(saved) or saved > 0 or not saved.is_integer() or saved != eex_offset_days:
                raise ValueError(
                    f"Catchup cannot reuse {path} row {point}: saved EEX offset "
                    f"{text or '0 (legacy)'} differs from or is invalid for requested offset {eex_offset_days}. "
                    f"Run refill for this range with --eex-offset-days {eex_offset_days} "
                    "to recalculate explicitly, or select another paths.output_dir. No result files written."
                )
    if configurations is not None:
        saved_ids = frame[configuration_column] if configuration_column in frame else [""] * len(frame)
        saved_parameters = frame[parameters_column] if parameters_column in frame else [""] * len(frame)
        for point, saved_id, parameters in zip(points, saved_ids, saved_parameters):
            if required_points is not None and point not in required_points:
                continue
            expected = configurations.get(tuple(point[1:4]))
            if expected is None:
                continue
            text = str(saved_id).strip()
            # Legacy global outputs lack a fingerprint. Preserve their established
            # catchup behavior; an individual run cannot infer their settings.
            if not text and expected["configuration_mode"] == "global":
                continue
            if text != expected["configuration_id"] and _target_expansion_only(
                    parameters, expected["configuration_parameters"]):
                continue  # Missing additional targets trigger a full date/curve recalculation below.
            if text != expected["configuration_id"]:
                raise ValueError(
                    f"Catchup cannot reuse {path} row {point}: saved model configuration "
                    f"{text or '(unrecorded legacy settings)'} differs from the requested configuration. "
                    "Run refill for this range to recalculate explicitly, or select another "
                    "paths.output_dir. No result files written.")
        saved_contexts = frame[context_column] if context_column in frame else [""] * len(frame)
        for point, saved_context in zip(points, saved_contexts):
            if required_points is not None and point not in required_points:
                continue
            expected = configurations.get(tuple(point[1:4]))
            if expected is None or "configuration_context_id" not in expected:
                continue
            text = str(saved_context).strip()
            if not text and expected["configuration_mode"] == "global":
                continue
            if text != expected["configuration_context_id"]:
                raise ValueError(
                    f"Catchup cannot reuse {path} row {point}: its mapping, operational settings "
                    "or CROSS helper configuration context has changed. Run refill for this range "
                    "to recalculate explicitly, or select another paths.output_dir. No result files written.")
    return set(points)


def _target_expansion_only(previous: str, current: str) -> bool:
    """Allow catchup's established target-expansion workflow without mixing model settings."""
    try:
        old, new = json.loads(previous), json.loads(current)
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        old_targets, new_targets = old.pop("tenors"), new.pop("tenors")
        if (not isinstance(old_targets, list) or not isinstance(new_targets, list)
                or any(not isinstance(label, str) for label in [*old_targets, *new_targets])):
            return False
        return old == new and set(old_targets) < set(new_targets)
    except (ValueError, TypeError, KeyError):
        return False


def _reference_days(cfg: Config, raw: pd.DataFrame, maps: list[ProductMap], books: dict,
                    start: date, end: date) -> set[date]:
    """Include weekdays and observed active dates even when own prices are invalid."""
    date_col = cfg.vwap_columns.get("reference_date", "reference_date")
    raw_dates = parse_reference_dates(raw[date_col]).dt.date
    active = {m.key for m in maps if m.active}
    observed = set(raw_dates[curve_keys(raw, cfg).isin(active)])
    observed.update(day for book in books.values() if book is not None for day in book.trade_dates)
    days = {day for day in observed if start <= day <= end}
    days.update(start + timedelta(days=offset) for offset in range((end - start).days + 1)
                if (start + timedelta(days=offset)).weekday() < 5)
    return days


def cmd_catchup(cfg: Config, a) -> int:
    """Fill pending date/curve groups while replaying all original history."""
    end = a.end or date.today()
    if end > date.today():
        raise ValueError("catchup --to cannot be later than today")
    if a.start is not None and a.start > end:
        raise ValueError("The start date cannot be later than the end date")
    _validate_output_schema(cfg.output_dir / "consistency_history.csv", KEYS)
    vw, raw, maps, books = _load(cfg, a.vwap)
    outputs = [m for m in maps if m.active and m.use == "fill"]
    if not outputs:
        _out("Catchup: no active fill curves with an assigned EEX path; nothing to process.")
        return 0
    date_col = cfg.vwap_columns.get("reference_date", "reference_date")
    raw_dates = parse_reference_dates(raw[date_col]).dt.date
    eex_dates = {day for book in books.values() if book is not None for day in book.trade_dates}
    available_dates = set(raw_dates) | eex_dates
    if a.start is None and not available_dates:
        raise ValueError("No input or EEX dates are available; specify catchup --from")
    start = a.start if a.start is not None else min(available_dates)
    if start > end:
        raise ValueError("The start date cannot be later than the end date")

    days = _reference_days(cfg, raw, maps, books, start, end)
    settings = {mapping.key: effective_config(cfg, mapping) for mapping in outputs}
    contexts = configuration_context_records(cfg, maps)
    configurations = {mapping.key: {
        **configuration_record(cfg, mapping),
        "configuration_context_id": contexts[mapping.key]["configuration_context_id"],
    } for mapping in outputs}
    required_points = {
        (day, *mapping.key, tenor)
        for day in days for mapping in outputs for tenor in dict.fromkeys(settings[mapping.key].tenors)
        if resolve_tenor(tenor, day, cfg.day_convention, cfg.weekend_offset) is not None
    }
    completed_filled = _history_point_keys(
        cfg.output_dir / "filled_history.csv", eex_offset_days=cfg.eex_offset_days,
        required_points=required_points, configurations=configurations,
    )
    completed_enriched = _history_point_keys(
        cfg.output_dir / "enriched_history.csv", enriched=True,
        eex_offset_days=cfg.eex_offset_days, required_points=required_points, configurations=configurations,
    )
    pending: set[tuple[date, CurveKey]] = set()
    for day in sorted(days):
        for m in outputs:
            targets = [label for label in dict.fromkeys(settings[m.key].tenors)
                       if resolve_tenor(label, day, cfg.day_convention, cfg.weekend_offset) is not None]
            if any((day, *m.key, tenor) not in completed_filled
                   or (day, *m.key, tenor) not in completed_enriched for tenor in targets):
                pending.add((day, m.key))
    if not pending:
        _out(f"Catchup {start} -> {end}: all resolvable targets are already present in both histories.")
        return 0

    for day in {day for day, _ in pending}:
        _validate_output_schema(cfg.output_dir / "filled" / f"{day}.csv", [*KEYS, "tenor"])
        _validate_output_schema(cfg.output_dir / "enriched" / f"{day}.csv", [*ENRICHED_KEYS, "curve_tenor"])
    _out(f"Catchup {start} -> {end}: {len(pending)} pending date/curve groups; completed groups are preserved.")
    _out(_layers(cfg))
    res = CurveFiller(cfg, vw, maps, books).run(start, end, output_keys=pending)
    _require_complete(res)
    # Retain all originals when deriving metadata; write only selected groups.
    enriched = enrich_input(raw, res.filled, cfg, maps, start, end)
    selected = {(day, *curve) for day, curve in pending}
    enriched = enriched.loc[[tuple(row) in selected
                             for row in enriched[ENRICHED_KEYS].itertuples(index=False, name=None)]].reset_index(drop=True)
    _write(cfg, res, enriched)
    _print_summary(res.filled)
    _out(f"\nWritten pending groups to {cfg.output_dir}")
    return 0


def cmd_status(cfg: Config, a) -> int:
    path = cfg.output_dir / "filled_history.csv"
    if not path.exists():
        _out(f"No filled history yet ({path}). Run refill first.")
        return 1
    _validate_output_schema(path, KEYS)
    h = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    days = sorted(h["reference_date"].unique())
    _out(f"{path}\n{len(days)} dates: {days[0]} -> {days[-1]} | {len(set(curve_keys(h)))} curves\n")
    last = h[h["reference_date"].isin(days[-a.last:])]
    t = last.pivot_table(index="reference_date", columns="source", values="tenor",
                         aggfunc="count", fill_value=0)
    _out(_table(t.reindex(columns=_source_order(t.columns)), index=True))
    d0, d1 = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    have = set(days)
    gaps = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    gaps = [d for d in gaps if d.weekday() < 5 and d.isoformat() not in have]
    if gaps:
        _out(f"\nUnfilled weekdays: {', '.join(map(str, gaps))}")
    return 0


def cmd_backtest(cfg: Config, a) -> int:
    vw, _, maps, books = _load(cfg, a.vwap)
    if vw.empty:
        raise ValueError("No valid VWAPs from active products are available for backtesting")
    start = a.start or min(vw["date"])
    end = a.end or max(vw["date"])
    res = CurveFiller(cfg, vw, maps, books).run(start, end, loo=True)
    _require_complete(res)
    if res.loo.empty:
        _out("No VWAPs with matching EEX in this range: nothing to evaluate.")
        return 1
    rep = summarize_loo(res.loo)
    comparison = None
    if a.truth:
        truth = pd.read_csv(cfg.resolve(a.truth), encoding="utf-8-sig", keep_default_na=False,
                            dtype={name: str for name in IDENTITY_COLUMNS})
        required = ["date", *IDENTITY_COLUMNS, "tenor", "truth"]
        missing = [name for name in required if name not in truth]
        if missing:
            raise ValueError(f"Synthetic truth lacks required columns: {missing}")
        truth["date"] = parse_reference_dates(truth["date"]).dt.date
        if truth["date"].isna().any():
            raise ValueError("Synthetic truth contains invalid reference dates")
        truth["truth"] = pd.to_numeric(truth["truth"], errors="coerce")
        if not truth["truth"].map(math.isfinite).all():
            raise ValueError("Synthetic truth prices must be finite numbers")
        comparison = compare_truth(res.filled, truth)
    with publication_lock(cfg.output_dir), CsvBatch() as batch:
        batch.stage(cfg.output_dir / "backtest_loo.csv", res.loo)
        batch.stage(cfg.output_dir / "backtest_report.csv", rep)
    _out("Leave-one-out: hide each own VWAP and predict it from the remaining observations (error = pred - own)\n")
    _out(_table(rep))
    if comparison is not None:
        _out("\nFilled curve vs synthetic ground truth (cells without own VWAPs):\n")
        _out(_table(comparison))
    return 0


def _grid_values(value: str | None, default, *, allowed=None, numeric=False) -> list:
    """Parse explicit trial values without accepting empty or invalid options."""
    if value is None:
        return [default]
    values = [part.strip() for part in value.split(",")]
    if not values or any(not part for part in values):
        raise ValueError("Parameter grids require nonempty comma-separated values")
    if numeric:
        values = [float(part) for part in values]
        if any(not math.isfinite(number) or number <= 0 for number in values):
            raise ValueError("tau-log and shrink-k grid values must be positive and finite")
    elif allowed is not None and any(part not in allowed for part in values):
        raise ValueError(f"Grid values must be selected from {', '.join(allowed)}")
    return list(dict.fromkeys(values))


def _json_finite(value):
    """Keep audit JSON standards-compliant when a diagnostic is unavailable."""
    if isinstance(value, dict):
        return {key: _json_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_finite(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def cmd_tune(cfg: Config, a) -> int:
    """Write evaluation reports without changing the production configuration."""
    from vwaps.tuning import tune_parameters

    if cfg.configuration_mode != "global":
        _out("CLI tune searches one global configuration and ignores individual table settings. "
             "Use the MLflow notebook for per-product searches.")
    cfg = replace(cfg, configuration_mode="global")

    grid = {
        "basis_mode": _grid_values(a.basis_modes, cfg.basis_mode, allowed=("auto", "ratio", "additive")),
        "tau_log": _grid_values(a.tau_log, cfg.tau_log, numeric=True),
        "shrink_k": _grid_values(a.shrink_k, cfg.shrink_k, numeric=True),
        "layer_hist": _grid_values(a.hist_modes, cfg.layer_hist, allowed=("auto", "on", "off")),
        "layer_correlation": [item == "on" for item in _grid_values(
            a.correlation, "on" if cfg.layer_correlation else "off", allowed=("off", "on"))],
        "layer_cross": [item == "on" for item in _grid_values(
            a.cross, "on" if cfg.layer_cross else "off", allowed=("off", "on"))],
    }
    vw, _, maps, books = _load(cfg, a.vwap)
    if vw.empty:
        raise ValueError("No valid original observations are available for parameter evaluation")
    result = tune_parameters(cfg, vw, maps, books, a.start or min(vw["date"]),
                             a.end or max(vw["date"]), grid, a.validation_days, a.max_trials)
    selected = result.selected_config
    patch = {
        "method": {name: selected[name] for name in ("basis_mode", "tau_log", "shrink_k")},
        "layers": {name.removeprefix("layer_"): selected[name]
                   for name in ("layer_hist", "layer_correlation", "layer_cross")},
    }
    report = {"selected_parameters": patch, "metadata": result.metadata,
              "base_configuration": asdict(cfg), "input_pattern": a.vwap or cfg.vwap_input,
              "selection": "Calibration dates only; validation dates are evaluated after selection.",
              "config_changed": False}
    with publication_lock(cfg.output_dir), CsvBatch() as batch:
        batch.stage(cfg.output_dir / "tuning_calibration.csv", result.calibration_report)
        batch.stage(cfg.output_dir / "tuning_validation.csv", result.validation_report)
        batch.stage_text(cfg.output_dir / "tuning_selected.json",
                         json.dumps(_json_finite(report), indent=2, ensure_ascii=False, allow_nan=False, default=str))
    _out("Selected parameters (configuration unchanged):\n" + json.dumps(patch, indent=2))
    _out("Later-date validation:\n" + _table(result.validation_report))
    _out(f"Reports written to {cfg.output_dir}: tuning_calibration.csv, tuning_validation.csv, tuning_selected.json")
    return 0


def cmd_detect(cfg: Config, a) -> int:
    vw, _, maps, books = _load(cfg, a.vwap)
    rows = vw[vw["tenor"].map(lambda t: (parse_tenor(t) or ("",))[0] in ("D", "WE"))]
    _out("Difference |VWAP - EEX| for D+n and WE+n by convention (lower means a closer match)\n")
    for conv in ("calendar", "business"):
        for off in (0, 1):
            diffs: dict[str, dict[str, list[float]]] = {}
            for product, g in rows.groupby(curve_keys(rows)):
                book = books.get(product)
                if book is None:
                    continue
                unit_diffs = diffs.setdefault(product[2], {"D": [], "WE": []})
                for d, t, v in g[["date", "tenor", "vwap"]].itertuples(index=False):
                    per = resolve_tenor(t, d, conv, off)
                    q, asof = book.quotes(d, 0)  # Same-day settlements only.
                    if per is None or asof is None or per.key not in q:
                        continue
                    unit_diffs[parse_tenor(t)[0]].append(abs(v - q[per.key]))
            for unit, terms in sorted(diffs.items()):
                cells = [f"{k}: n={len(x):4d} median={statistics.median(x):7.2f}" if x else f"{k}: n=   0"
                         for k, x in terms.items()]
                _out(f"unit={unit!r} day={conv:8s} weekend_offset={off}  |  " + "  |  ".join(cells))
            if not diffs:
                _out(f"day={conv:8s} weekend_offset={off}  |  no comparable curves")
    return 0


def cmd_synthetic(cfg: Config, a) -> int:
    maps = []
    for area in [x.strip() for x in a.areas.split(",") if x.strip()]:
        for profile in [x.strip() for x in a.profiles.split(",") if x.strip()]:
            r = guess_row(f"{area}_{profile} load", cfg,
                          unit="GBP/MWh" if area == "GB" else "EUR/MWh")
            if r["eex_file"]:
                r["use"] = "fill"  # Temporary synthetic-generation map; persisted drafts stay off.
                maps.append(ProductMap(**r))
    books = _books(cfg, maps)
    maps = [m for m in maps if books.get(m.key) is not None]
    if not maps:
        _out("No EEX curves are available for synthetic generation.")
        return 1
    v, t = make_synthetic(cfg, maps, books, a.start, a.end, seed=a.seed, mode=a.mode)
    out = cfg.resolve(a.out)
    with publication_lock(out), CsvBatch() as batch:
        batch.stage(out / "synthetic_vwaps.csv", v)
        batch.stage(out / "synthetic_truth.csv", t)
    _out(f"{len(v)} synthetic VWAPs across {len(maps)} products -> {out / 'synthetic_vwaps.csv'} "
         f"(+ synthetic_truth.csv)")
    return 0
