"""Compare VWAPS (basis method) and VWAPS2 (spread method) on identical inputs.

    python compare_methods.py --vwap "D:/data/vwaps.xlsx" --mapping mappings/products.csv
    python compare_methods.py --synthetic            # generate synthetic data first

Each repository runs with its own code and its own config.toml, copied to a
temporary config whose paths and eex.offset_days are replaced, so neither
repository is modified. For every offset it runs `refill` and `backtest` in
both and writes, under --out (default: comparison/):

    <case>/vwaps/...  <case>/vwaps2/...   outputs of each repository
    summary_backtest.csv   leave-one-out MAE on the SAME hidden observations
    summary_truth.csv      error against synthetic truth (only with --truth)
    summary_coverage.csv   cells priced by each method, by source
    SUMMARY.md             readable comparison
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
KEYS = ["reference_date", "product", "region", "unit", "tenor"]


def toml_path(p: Path) -> str:
    return str(Path(p).resolve()).replace("\\", "/")


def make_config(repo: Path, out: Path, vwap: Path, mapping: Path, eex: Path, offset: int) -> Path:
    text = (repo / "config.toml").read_text(encoding="utf-8")
    for key, value in (("vwap_input", vwap), ("mapping", mapping), ("eex_curves_dir", eex), ("output_dir", out)):
        text, n = re.subn(rf'(?m)^{key}\s*=.*$', f'{key} = "{toml_path(value)}"', text)
        assert n == 1, (repo, key)
    text, n = re.subn(r'(?m)^offset_days\s*=.*$', f"offset_days = {offset}", text)
    assert n == 1, (repo, "offset_days")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def run(repo: Path, config: Path, *args: str) -> None:
    cmd = ["uv", "run", "--project", str(repo), "python", str(repo / "run.py"), "--config", str(config), *args]
    print(">", repo.name, *args, flush=True)
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (config.parent / f"{args[0]}.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    if done.returncode != 0:
        sys.exit(f"{repo.name} {' '.join(args)} failed; see {config.parent / (args[0] + '.log')}")


def read(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False, na_values=[""])
    for column in ("region", "unit"):
        if column in frame:
            frame[column] = frame[column].fillna("").astype(str)
    return frame


def markdown(frame: pd.DataFrame) -> str:
    """Plain Markdown table (no optional dependency)."""
    def cell(v):
        return "" if isinstance(v, float) and v != v else (f"{v:.4g}" if isinstance(v, float) else str(v))
    head = "| " + " | ".join(map(str, frame.columns)) + " |"
    rule = "|" + "---|" * len(frame.columns)
    body = ["| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([head, rule, *body])


def group_of(kind: str) -> str:
    return {"Day": "short", "Weekend": "short", "BOW": "short", "Week": "short", "BOM": "month",
            "Month": "month", "Quarter": "quarter", "Season": "long", "Year": "long"}.get(kind, "?")


def backtest_summary(case: str, a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """MAE of each repository's deployed pipeline and of raw EEX, on hidden
    observations that BOTH pipelines predicted."""
    pick = lambda f, m, name: f.loc[f["method"] == m, KEYS + ["group", "own", "pred"]].rename(columns={"pred": name})
    pa = pick(a, "pipeline_configured", "vwaps")
    pb = pick(b, "pipeline_configured", "vwaps2")
    pe = pick(a, "eex", "eex")
    paired = pa.merge(pb[KEYS + ["vwaps2"]], on=KEYS).merge(pe[KEYS + ["eex"]], on=KEYS)
    rows = []
    for group, g in [("ALL", paired), *paired.groupby("group")]:
        err = lambda col: (g[col] - g["own"]).abs().mean()
        rows.append({"case": case, "group": group, "n": len(g), "mae_eex_raw": err("eex"),
                     "mae_vwaps": err("vwaps"), "mae_vwaps2": err("vwaps2"),
                     "vwaps2_better_share": ((g["vwaps2"] - g["own"]).abs() < (g["vwaps"] - g["own"]).abs()).mean()})
    rows.append({"case": case, "group": "coverage", "n": len(pa), "mae_eex_raw": float("nan"),
                 "mae_vwaps": float(len(pa)), "mae_vwaps2": float(len(pb)), "vwaps2_better_share": float("nan")})
    return pd.DataFrame(rows)


def truth_summary(case: str, fa: pd.DataFrame, fb: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    t = truth.rename(columns={"date": "reference_date"})
    cols = KEYS + ["kind", "source", "price"]
    m = (fa[cols].rename(columns={"source": "src_a", "price": "vwaps"})
         .merge(fb[KEYS + ["source", "price"]].rename(columns={"source": "src_b", "price": "vwaps2"}), on=KEYS)
         .merge(t[KEYS + ["truth"]], on=KEYS))
    m = m[(m["src_a"] != "own") & m["vwaps"].notna() & m["vwaps2"].notna()]
    m["group"] = m["kind"].map(group_of)
    rows = []
    for group, g in [("ALL", m), *m.groupby("group")]:
        rows.append({"case": case, "group": group, "n_cells": len(g),
                     "mae_vwaps": (g["vwaps"] - g["truth"]).abs().mean(),
                     "mae_vwaps2": (g["vwaps2"] - g["truth"]).abs().mean(),
                     "mean_abs_diff_between_methods": (g["vwaps"] - g["vwaps2"]).abs().mean()})
    return pd.DataFrame(rows)


def coverage(case: str, fa: pd.DataFrame, fb: pd.DataFrame) -> pd.DataFrame:
    a = fa["source"].value_counts().rename("vwaps")
    b = fb["source"].value_counts().rename("vwaps2")
    out = pd.concat([a, b], axis=1).fillna(0).astype(int).reset_index(names="source")
    out.insert(0, "case", case)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vwaps-repo", type=Path, default=HERE.parent / "VWAPS")
    ap.add_argument("--vwap", type=Path, help="own VWAP file (same for both methods)")
    ap.add_argument("--mapping", type=Path, default=HERE / "mappings" / "products.csv")
    ap.add_argument("--eex", type=Path, default=HERE.parent / "eex_scraper" / "output" / "curves" / "POWER")
    ap.add_argument("--truth", type=Path, help="synthetic truth (make-synthetic) to score filled cells")
    ap.add_argument("--synthetic", action="store_true",
                    help="generate synthetic VWAPs with both basis models (ratio and additive) and compare on each")
    ap.add_argument("--offsets", default="0,-1", help="eex.offset_days values to compare")
    ap.add_argument("--out", type=Path, default=HERE / "comparison")
    a = ap.parse_args()

    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    mapping = out / ("mapping" + a.mapping.suffix)
    shutil.copyfile(a.mapping, mapping)  # both repositories read the same copy
    datasets = []
    if a.synthetic:
        for mode in ("ratio", "additive"):
            folder = out / f"data_{mode}"
            cfg = make_config(HERE, folder / "_gen", folder / "unused.csv", mapping, a.eex, 0)
            run(HERE, cfg, "make-synthetic", "--mode", mode, "--out", str(folder))
            datasets.append((f"synthetic_{mode}", folder / "synthetic_vwaps.csv", folder / "synthetic_truth.csv"))
    else:
        if a.vwap is None:
            sys.exit("Use --vwap <file> or --synthetic")
        datasets.append(("input", a.vwap.resolve(), a.truth.resolve() if a.truth else None))

    backtests, truths, coverages = [], [], []
    for name, vwap, truth in datasets:
        for offset in (int(x) for x in a.offsets.split(",")):
            case = f"{name}_offset{offset}"
            outputs = {}
            for label, repo in (("vwaps", a.vwaps_repo.resolve()), ("vwaps2", HERE)):
                cfg = make_config(repo, out / case / label, vwap, mapping, a.eex, offset)
                run(repo, cfg, "refill")
                run(repo, cfg, "backtest")
                outputs[label] = cfg.parent
            fa = read(outputs["vwaps"] / "filled_history.csv")
            fb = read(outputs["vwaps2"] / "filled_history.csv")
            coverages.append(coverage(case, fa, fb))
            backtests.append(backtest_summary(case, read(outputs["vwaps"] / "backtest_loo.csv"),
                                              read(outputs["vwaps2"] / "backtest_loo.csv")))
            if truth is not None:
                truths.append(truth_summary(case, fa, fb, read(truth)))

    bt = pd.concat(backtests, ignore_index=True)
    bt.to_csv(out / "summary_backtest.csv", index=False)
    cov = pd.concat(coverages, ignore_index=True)
    cov.to_csv(out / "summary_coverage.csv", index=False)
    lines = ["# VWAPS vs VWAPS2", "",
             "## Leave-one-out on identical hidden observations (MAE)", "",
             markdown(bt.round(4)), "",
             "## Cells by source", "", markdown(cov), ""]
    if truths:
        tr = pd.concat(truths, ignore_index=True)
        tr.to_csv(out / "summary_truth.csv", index=False)
        lines += ["## Filled cells against synthetic truth (MAE)", "", markdown(tr.round(4)), ""]
    (out / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
