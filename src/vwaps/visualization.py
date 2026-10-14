"""Read and compare saved curve outputs without accessing VWAP or EEX inputs."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from vwaps.config import load_config
from vwaps.dates import parse_reference_dates
from vwaps.identity import CurveKey, IDENTITY_COLUMNS
from vwaps.tenors import Period, parse_tenor


REQUIRED_COLUMNS = [
    "reference_date", *IDENTITY_COLUMNS, "tenor", "kind", "delivery_start", "delivery_end",
    "price", "eex_settle", "source",
]
PERIOD_COLUMNS = ["kind", "delivery_start", "delivery_end"]
OBSERVATION_COLUMNS = ["reference_date", *IDENTITY_COLUMNS, *PERIOD_COLUMNS]
NUMERIC_COLUMNS = ["price", "eex_settle", "price_before_shape", "own_vwap", "shape_adjustment"]
KIND_ORDER = ("Day", "Weekend", "BOW", "Week", "BOM", "Month", "Quarter", "Season", "Year")


def find_project_root(start: str | Path | None = None) -> Path:
    """Locate this repository from its root, notebooks directory or a descendant."""
    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for parent in (current, *current.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "vwaps").is_dir():
            return parent
    raise FileNotFoundError("Cannot find the project root; set PROJECT_ROOT to the copied repository")


def resolve_output_path(
    root: str | Path | None = None, *, config_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> Path:
    """Prefer an explicit CSV/directory, otherwise use the configured output directory.

    Relative explicit paths are relative to the project root. The configuration
    loader resolves paths relative to its own TOML file; no input data is opened.
    """
    project = Path(root).expanduser().resolve() if root is not None else find_project_root()
    if output_path is not None:
        target = Path(output_path).expanduser()
        target = target if target.is_absolute() else project / target
    else:
        config = Path(config_path).expanduser() if config_path is not None else project / "config.toml"
        config = config if config.is_absolute() else project / config
        if not config.exists() and config_path is None:
            target = project / "output"
        else:
            target = load_config(config).output_dir
    if target.is_dir() or target.suffix.lower() != ".csv":
        target = target / "filled_history.csv"
    return target.resolve()


def _canonical_tenor(value: str) -> str:
    parsed = parse_tenor(value)
    if parsed is None:
        return value.strip()
    base, offset = parsed
    return base if base in ("BOM", "BOW") else f"{base}+{offset}"


def _dates(values: pd.Series, name: str, *, optional: bool = False) -> pd.Series:
    parsed = parse_reference_dates(values).dt.normalize()
    absent = values.astype(str).str.strip().isin(("", "nan", "NaT", "None"))
    invalid = parsed.isna() & (~absent if optional else True)
    if invalid.any():
        raise ValueError(f"Output column {name!r} contains invalid or missing dates at rows {list(values.index[invalid][:5])}")
    return parsed


def _deduplicate(frame: pd.DataFrame) -> pd.DataFrame:
    """Retain one observation per absolute contract and reject conflicting aliases."""
    if frame.empty:
        return frame.assign(tenor_aliases=pd.Series(dtype=str))
    duplicates = frame.duplicated(OBSERVATION_COLUMNS, keep=False)
    distinct = frame.loc[~duplicates].copy()
    distinct["tenor_aliases"] = distinct["tenor"]
    distinct["tenor"] = distinct["tenor"].map(_canonical_tenor)
    if not duplicates.any():
        return distinct.reset_index(drop=True)
    rows = []
    checks = [name for name in NUMERIC_COLUMNS if name in frame]
    checks += ["source", "eex_asof"]
    checks += [name for name in ("data_origin", "shape_status") if name in frame]
    for key, aliases in frame.loc[duplicates].groupby(OBSERVATION_COLUMNS, sort=False, dropna=False):
        conflicts = [name for name in checks if aliases[name].nunique(dropna=False) > 1]
        if conflicts:
            raise ValueError(f"Conflicting output aliases for {key}: {', '.join(conflicts)}")
        row = aliases.iloc[0].copy()
        row["tenor_aliases"] = ", ".join(sorted(set(aliases["tenor"])))
        row["tenor"] = sorted(set(aliases["tenor"].map(_canonical_tenor)))[0]
        rows.append(row)
    return pd.concat([distinct, pd.DataFrame(rows)], ignore_index=True)


def load_output(path: str | Path) -> pd.DataFrame:
    """Load a filled CSV, normalize dates/prices and deduplicate contract aliases.

    Shape columns are optional. Identity columns are required even for older
    outputs, since inventing them could silently combine different curves.
    Nonfinite numeric values are missing; zero and negative prices are valid.
    """
    path = Path(path)
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        frame = pd.DataFrame(columns=REQUIRED_COLUMNS)
    missing = set(REQUIRED_COLUMNS) - set(frame)
    if missing:
        raise ValueError(f"Expected a filled output CSV; missing columns: {sorted(missing)}. "
                         "Choose a current filled CSV with complete product/region/unit identity; "
                         "regenerate legacy outputs instead of guessing missing dimensions.")
    for name in (*IDENTITY_COLUMNS, "kind", "tenor", "source"):
        frame[name] = frame[name].str.strip()
    if frame["product"].eq("").any():
        raise ValueError("Output contains a blank product identity")
    for name in ("reference_date", "delivery_start", "delivery_end"):
        frame[name] = _dates(frame[name], name)
    if (frame["delivery_end"] <= frame["delivery_start"]).any():
        raise ValueError("Output delivery_end must be after delivery_start")
    if "eex_asof" not in frame:
        frame["eex_asof"] = ""
    frame["eex_asof"] = _dates(frame["eex_asof"], "eex_asof", optional=True)
    for name in NUMERIC_COLUMNS:
        if name not in frame:
            continue
        text = frame[name].astype(str).str.strip()
        number = pd.to_numeric(text, errors="coerce")
        absent = text.str.lower().isin(("", "nan", "none", "na", "n/a"))
        if (number.isna() & ~absent).any():
            raise ValueError(f"Output column {name!r} contains nonnumeric prices")
        frame[name] = number.where(np.isfinite(number)).astype(float)
    # A missing-source row is never a price observation, even in a malformed legacy CSV.
    frame.loc[frame["source"].eq("missing"), "price"] = np.nan
    input_rows = len(frame)
    frame = _deduplicate(frame)
    frame["eex_age_days"] = (frame["reference_date"] - frame["eex_asof"]).dt.days
    frame = frame.sort_values([*IDENTITY_COLUMNS, "reference_date", *PERIOD_COLUMNS]).reset_index(drop=True)
    frame.attrs.update(input_rows=input_rows, alias_rows_removed=input_rows - len(frame))
    return frame


def available_curves(frame: pd.DataFrame) -> list[CurveKey]:
    """List exact product/region/unit identities, preserving literal blank dimensions."""
    return sorted(set(frame[IDENTITY_COLUMNS].itertuples(index=False, name=None)))


def select_curve(
    frame: pd.DataFrame, identity: CurveKey, *, kind: str | None = None,
    reference_date=None, start=None, end=None,
) -> pd.DataFrame:
    """Select one exact identity from a normalized output frame; dates are inclusive."""
    if len(identity) != 3:
        raise ValueError("Select a complete (product, region, unit) identity")
    selected = pd.Series(True, index=frame.index)
    for name, value in zip(IDENTITY_COLUMNS, identity):
        selected &= frame[name].eq(value)
    if kind is not None:
        selected &= frame["kind"].eq(kind)
    if start is not None and end is not None and pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError("The start date must not be after the end date")
    if reference_date is not None:
        selected &= frame["reference_date"].eq(pd.Timestamp(reference_date))
    if start is not None:
        selected &= frame["reference_date"].ge(pd.Timestamp(start))
    if end is not None:
        selected &= frame["reference_date"].le(pd.Timestamp(end))
    return frame.loc[selected].copy()


def curve_plot_points(frame: pd.DataFrame, *, alignment: str = "delivery") -> pd.DataFrame:
    """Prepare distinct categorical positions for every saved curve contract.

    Use one reference date or already aggregated paired means for one identity.
    Delivery alignment orders absolute periods within each contract family;
    relative alignment orders numeric tenor offsets, then their base labels.
    Known families follow KIND_ORDER; unknown families follow alphabetically.
    ``plot_x`` identifies the full contract rather than just its start date, so
    a month, quarter and year starting together remain distinct. ``plot_label``
    uses a supplied tenor, or an absolute period name for delivery means.

    Prices and missing observations are preserved. No contracts are generated
    and no dates are aggregated here: duplicate contract keys are rejected.
    """
    if alignment not in ("delivery", "tenor"):
        raise ValueError("alignment must be delivery or tenor")
    result = frame.copy()
    if result.empty:
        result["plot_x"] = pd.Series(index=result.index, dtype=str)
        result["plot_label"] = pd.Series(index=result.index, dtype=str)
        return result

    keys = PERIOD_COLUMNS if alignment == "delivery" else ["kind", "tenor"]
    missing = set(keys) - set(result)
    if missing:
        raise ValueError(f"Missing curve plot columns: {sorted(missing)}")
    if result[keys].isna().any().any():
        raise ValueError("Curve plot contract keys must not be missing")
    if result.duplicated(keys, keep=False).any():
        raise ValueError("Duplicate curve plot contracts; select one identity and date or aggregate dates first")

    kind_rank = {kind: index for index, kind in enumerate(KIND_ORDER)}

    def order_key(position: int) -> tuple:
        row = result.iloc[position]
        kind = str(row["kind"])
        family = (kind_rank.get(kind, len(KIND_ORDER)), kind)
        if alignment == "delivery":
            return (*family, pd.Timestamp(row["delivery_start"]), pd.Timestamp(row["delivery_end"]))
        tenor = str(row["tenor"])
        parsed = parse_tenor(tenor)
        return (*family, parsed[1] if parsed else float("inf"), parsed[0] if parsed else tenor, tenor)

    result = result.iloc[sorted(range(len(result)), key=order_key)].reset_index(drop=True)
    plot_keys, labels = [], []
    for row in result.to_dict("records"):
        kind = str(row["kind"])
        if alignment == "tenor":
            label = str(row["tenor"])
            key = [kind, label]
        else:
            first, last = pd.Timestamp(row["delivery_start"]), pd.Timestamp(row["delivery_end"])
            key = [kind, first.isoformat(), last.isoformat()]
            tenor = row.get("tenor")
            label = (
                str(tenor) if pd.notna(tenor) and str(tenor).strip()
                else Period(kind, first.date(), last.date()).name
            )
        plot_keys.append(json.dumps(key, ensure_ascii=False, separators=(",", ":")))
        labels.append(label)
    result["plot_x"] = plot_keys
    result["plot_label"] = labels
    return result


def coverage_metrics(frame: pd.DataFrame) -> dict:
    """Count saved observations, not absent rows or unknown expected targets."""
    price = np.isfinite(frame["price"])
    eex = np.isfinite(frame["eex_settle"])
    original = frame["source"].eq("own")
    if "data_origin" in frame:
        original |= frame["data_origin"].eq("original")
    changed_original = frame["source"].eq("own+shape")
    original &= ~changed_original
    return {
        "n_observations": len(frame), "n_dates": frame["reference_date"].nunique(),
        "n_price": int(price.sum()), "n_eex": int(eex.sum()),
        "n_paired": int((price & eex).sum()), "n_missing_price": int((~price).sum()),
        "n_original": int((price & original).sum()),
        "n_estimated": int((price & ~original).sum()),
        "n_adjusted_original": int((price & changed_original).sum()),
        "price_coverage": float(price.mean()) if len(frame) else np.nan,
    }


def paired_means(
    frame: pd.DataFrame, identity: CurveKey, *, start=None, end=None,
    kind: str | None = "Month", alignment: str = "delivery",
) -> pd.DataFrame:
    """Compare equal-date-weight means on paired finite price/EEX observations only.

    Delivery alignment follows a fixed absolute period through label rolls.
    Tenor alignment intentionally combines different deliveries and exposes
    their count. Aliases must already have been normalized by load_output.
    """
    if alignment not in ("delivery", "tenor"):
        raise ValueError("alignment must be delivery or tenor")
    selected = select_curve(frame, identity, kind=kind, start=start, end=end)
    group_columns = PERIOD_COLUMNS if alignment == "delivery" else ["kind", "tenor"]
    rows = []
    for key, group in selected.groupby(group_columns, sort=True, dropna=False):
        if group["reference_date"].duplicated().any():
            raise ValueError("One aligned point contains multiple observations for the same reference date")
        paired = group[np.isfinite(group["price"]) & np.isfinite(group["eex_settle"])]
        row = dict(zip(group_columns, key))
        row.update(coverage_metrics(group))
        row.update(
            alignment=alignment,
            tenor_aliases=", ".join(sorted(set(group["tenor"]))),
            n_delivery_periods=len(group[PERIOD_COLUMNS].drop_duplicates()),
            first_date=group["reference_date"].min(), last_date=group["reference_date"].max(),
            price_mean=paired["price"].mean(), eex_mean=paired["eex_settle"].mean(),
            mean_spread=(paired["price"] - paired["eex_settle"]).mean(),
        )
        rows.append(row)
    result = pd.DataFrame(rows)
    if alignment == "tenor" and not result.empty:
        order = result["tenor"].map(lambda value: (parse_tenor(value) or ("", 0))[1])
        result = result.assign(_offset=order).sort_values(["kind", "_offset", "tenor"])
        result = result.drop(columns="_offset").reset_index(drop=True)
    return result


def contract_evolution(
    frame: pd.DataFrame, identity: CurveKey, kind: str, delivery_start, delivery_end,
) -> pd.DataFrame:
    """Follow one absolute contract across reference dates and relative label changes."""
    selected = select_curve(frame, identity, kind=kind)
    return selected[
        selected["delivery_start"].eq(pd.Timestamp(delivery_start))
        & selected["delivery_end"].eq(pd.Timestamp(delivery_end))
    ].sort_values("reference_date")
