"""Connect each (product, region, unit) curve to its EEX data.

The editable mappings/products.csv table has one row per complete curve
identity, matching the input's product, region and unit exactly after trimming:

  product     exact name in the input                     DE_Base load
  region      exact region; blank is a literal value
  unit        exact price unit                            EUR/MWh
  use         fill = complete and write the curve         fill
              helper = only support other products
              off = exclude from calculations
  area        output label                                DE
  profile     output label                                Base
  eex_file    EEX file relative to eex_curves_dir          DE/Base.csv (blank = no EEX)
  hours       delivery hours convention                   Base
              Base (24h), Peak (Mon-Fri 08-20), Peak7 (every day 08-20)
  timezone    delivery timezone                           Europe/Berlin
  comment     free text

`python run.py mapping` drafts rows for all input products, guesses their EEX
files and checks whether the configured files exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from vwaps.config import Config
from vwaps.identity import CurveKey, IDENTITY_COLUMNS, curve_keys, normalize_identity
from vwaps.product_config import MODEL_PARAMETER_FIELDS, effective_config, parse_parameter_cells

COLUMNS = [*IDENTITY_COLUMNS, "use", "area", "profile", "eex_file", "hours", "timezone", "comment"]
PARAMETER_COLUMNS = list(MODEL_PARAMETER_FIELDS)
USES = ("fill", "helper", "off")
HOURS = ("Base", "Peak", "Peak7")


@dataclass
class ProductMap:
    product: str
    use: str
    area: str
    profile: str
    eex_file: str
    hours: str
    timezone: str
    comment: str = ""
    region: str = ""
    unit: str = ""
    parameter_overrides: dict = field(default_factory=dict)

    @property
    def key(self) -> CurveKey:
        return (self.product, self.region, self.unit)

    @property
    def active(self) -> bool:
        """Only explicitly enabled curves with an assigned EEX path run."""
        return self.use in ("fill", "helper") and bool(self.eex_file.strip())

    @property
    def label(self) -> str:
        return f"{self.product} [region={self.region!r}, unit={self.unit!r}]"


def read_mapping_table(path: str | Path) -> pd.DataFrame:
    """Read a CSV or the first sheet of an XLSX mapping without losing literals."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Product mapping does not exist: {path}. Create it with: python run.py mapping")
    if path.suffix.lower() == ".xlsx":
        df = pd.read_excel(path, sheet_name=0, dtype=str, keep_default_na=False, engine="openpyxl")
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    else:
        raise ValueError("The product mapping must be a .csv or .xlsx file")
    df.columns = [str(name).strip() for name in df.columns]
    if df.columns.duplicated().any():
        raise ValueError(f"{path.name}: duplicate mapping column names after trimming")
    return df


def load_mapping(cfg: Config) -> list[ProductMap]:
    """Load exact identities and, in individual mode, their fixed model values."""
    path = cfg.mapping_file
    df = read_mapping_table(path)
    missing = [c for c in COLUMNS if c not in df.columns and c != "comment"]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}. "
                         "Run python run.py mapping to migrate an unambiguous legacy mapping; "
                         "otherwise add one explicit row per product, region and unit.")
    if cfg.configuration_mode not in ("global", "individual"):
        raise ValueError("run.configuration_mode must be global or individual")
    if cfg.configuration_mode == "individual":
        unknown = [name for name in df.columns if name not in COLUMNS and name not in MODEL_PARAMETER_FIELDS]
        if unknown:
            raise ValueError(f"{path.name}: unknown individual configuration columns {unknown}. "
                             "Use Config model parameter names; put notes in comment.")
    out, errors = [], []
    for i, r in df.iterrows():
        use = r["use"].strip().lower()
        hours = r["hours"].strip()
        if use not in USES:
            errors.append(f"row {i + 2} ({r['product']}): use={use!r} (must be {'/'.join(USES)})")
        if hours not in HOURS:
            errors.append(f"row {i + 2} ({r['product']}): hours={hours!r} (must be {'/'.join(HOURS)})")
        out.append(ProductMap(
            r["product"].strip(), use, r["area"].strip(), r["profile"].strip(),
            r["eex_file"].strip(), hours, r["timezone"].strip() or cfg.tz(r["area"].strip()),
            r.get("comment", "").strip(),
            r["region"].strip(), r["unit"].strip(),
        ))
        if not out[-1].product:
            errors.append(f"row {i + 2}: product must not be empty")
        if cfg.configuration_mode == "individual":
            try:
                out[-1].parameter_overrides = parse_parameter_cells(r)
                effective_config(cfg, out[-1])
            except ValueError as exc:
                errors.append(f"row {i + 2} ({out[-1].label}): {exc}")
    if errors:
        raise ValueError("Mapping errors:\n  " + "\n  ".join(errors))
    keys = pd.Series([m.key for m in out], dtype=object)
    dup = keys.duplicated()
    if dup.any():
        raise ValueError(f"Duplicate curve identities in mapping: {sorted(set(keys[dup]))}")
    return out


_RX = re.compile(r"^(?P<area>[A-Za-z0-9]+)_(?P<rest>.+)$")


def guess_row(product: str, cfg: Config, region: str = "", unit: str = "") -> dict:
    """Draft a mapping row from a product name, such as 'DE_Base load'."""
    m = _RX.match(product)
    area = m.group("area") if m else ""
    rest = (m.group("rest") if m else product).lower()
    profile = "Base" if rest.startswith("base") else "Peak" if rest.startswith("peak") else "Other"
    row = {"product": product, "region": region, "unit": unit,
           "use": "off", "area": area, "profile": profile, "eex_file": "",
           "hours": "Peak" if profile == "Peak" else "Base", "timezone": cfg.tz(area), "comment": ""}
    if profile == "Other":
        row["comment"] = "non-Base/Peak product: review manually"
        return row
    folder = cfg.eex_curves_dir / area
    candidates = []
    if folder.exists():
        names = sorted(p.stem for p in folder.glob("*.csv") if not p.stem.endswith("_wide"))
        candidates = [n for n in names if n == profile] or [n for n in names if n.startswith(profile)]
    if candidates:
        f = candidates[0]
        row.update(eex_file=f"{area}/{f}.csv",
                   comment="draft EEX match: review identity, currency and delivery profile, then set use=fill")
        if f != profile:
            row["comment"] = f"EEX only has {f}: check that it represents the same product"
            if "Mo-Su" in f:
                row["hours"] = "Peak7"
    else:
        row.update(comment="no EEX curve found: leave off until explicitly assigned")
    return row


def check_mapping(maps: list[ProductMap], cfg: Config, vwaps: pd.DataFrame | None) -> pd.DataFrame:
    """Report each product's EEX path, file availability and observation count."""
    rows = []
    counts = curve_keys(vwaps).value_counts().to_dict() if vwaps is not None else {}
    for m in maps:
        f = cfg.eex_curves_dir / m.eex_file if m.eex_file else None
        eex = "-" if f is None else ("OK" if f.exists() else "MISSING")
        first = last = ""
        if f is not None and f.exists():
            d = pd.read_csv(f, usecols=["tradeDate"], encoding="utf-8-sig")["tradeDate"]
            first, last = d.min(), d.max()
        n = int(counts.get(m.key, 0))
        rows.append({"product": m.product, "region": m.region, "unit": m.unit,
                     "use": m.use, "eex_file": m.eex_file or "(no EEX)",
                     "eex": eex, "eex_from": first, "eex_to": last, "vwaps": n,
                     "hours": m.hours, "comment": m.comment})
    return pd.DataFrame(rows)


def eex_path(cfg: Config, m: ProductMap) -> Path | None:
    return cfg.eex_curves_dir / m.eex_file if m.eex_file else None


def migrate_legacy_mapping(frame: pd.DataFrame, identities: pd.DataFrame) -> pd.DataFrame:
    """Add missing identity columns only when each legacy row is unambiguous.

    Existing manual EEX choices are preserved. Ambiguous or absent identities
    require manual migration; no wildcard or first-match fallback is allowed.
    """
    missing = [name for name in IDENTITY_COLUMNS if name not in frame]
    if not missing:
        return frame
    if "product" in missing:
        raise ValueError("The mapping requires a product column")
    out = frame.copy()
    identities = normalize_identity(identities).drop_duplicates()
    for index, row in frame.iterrows():
        candidates = identities
        for name in IDENTITY_COLUMNS:
            if name not in missing:
                candidates = candidates[candidates[name] == str(row[name]).strip()]
        if len(candidates) != 1:
            raise ValueError(
                f"Legacy mapping row {index + 2} ({row['product']!r}) matches "
                f"{len(candidates)} input curve identities. Add region/unit columns "
                "manually and use one row per (product, region, unit); file unchanged.")
        for name in missing:
            out.loc[index, name] = candidates.iloc[0][name]
    for name in missing:
        if name not in out:
            out[name] = ""
    return out
