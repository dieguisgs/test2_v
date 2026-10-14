"""The tuning command exports reports without changing production artifacts."""

import json
from datetime import date

import pandas as pd

from vwaps.cli import main
from vwaps.io_eex import REQUIRED
from vwaps.log import get_logger
from vwaps.mapping import COLUMNS


def test_tune_exports_auditable_selection_without_changing_input_config_or_curves(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[paths]\nvwap_input="input.csv"\nmapping="mapping.csv"\neex_curves_dir="."\n'
        'output_dir="output"\n[targets]\ntenors=["M+1","M+2","M+3"]\n'
        '[layers]\nhist="off"\n[method]\nbasis_mode="auto"\n', encoding="utf-8",
    )
    pd.DataFrame([dict(product="P", region="North", unit="EUR/MWh", use="fill", area="DE",
                       profile="Base", eex_file="eex.csv", hours="Base", timezone="Europe/Berlin",
                       comment="reviewed")], columns=COLUMNS).to_csv(tmp_path / "mapping.csv", index=False)
    days = list(pd.bdate_range("2026-09-01", periods=12).date)
    observations, quotes = [], []
    for day in days:
        for n in (1, 2, 3):
            observations.append(dict(reference_date=day, product="P", region="North", unit="EUR/MWh",
                                     tenor=f"M+{n}", vwap=n * 100 + 10, volume=100))
            quotes.append((day, "Month", date(2026, 9 + n, 1), n * 100))
    pd.DataFrame(observations).to_csv(tmp_path / "input.csv", index=False)
    pd.DataFrame(quotes, columns=REQUIRED).to_csv(tmp_path / "eex.csv", index=False)
    output = tmp_path / "output"
    output.mkdir()
    # Existing output must not be read, recalculated or replaced by tuning.
    for name in ("filled_history.csv", "enriched_history.csv"):
        (output / name).write_text("existing production artifact\n", encoding="utf-8")
    protected = [config, tmp_path / "input.csv", tmp_path / "mapping.csv", tmp_path / "eex.csv",
                 output / "filled_history.csv", output / "enriched_history.csv"]
    before = {path: path.read_bytes() for path in protected}
    logger = get_logger()
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
    try:
        assert main([
            "tune", "--from", days[0].isoformat(), "--to", days[-1].isoformat(),
            "--validation-days", "3", "--max-trials", "3", "--basis-modes", "auto,ratio,additive",
            "--tau-log", "10", "--shrink-k", "0.01", "--hist-modes", "off",
            "--correlation", "off", "--cross", "off",
        ], config) == 0
    finally:
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
    assert {path: path.read_bytes() for path in protected} == before
    assert not (output / "filled").exists() and not (output / "enriched").exists()
    calibration = pd.read_csv(output / "tuning_calibration.csv")
    validation = pd.read_csv(output / "tuning_validation.csv")
    selected = json.loads((output / "tuning_selected.json").read_text(encoding="utf-8"))
    assert len(calibration[calibration["scope"] == "overall"]) == 3
    assert {"n_baseline", "n_available", "n_missing", "coverage", "eligible_for_selection"} <= set(calibration)
    assert (calibration["coverage"] == 1.0).all()
    assert (validation["n_missing"] == 0).all()
    assert set(validation["basis_mode"]) == {"additive"}
    assert selected["selected_parameters"]["method"]["basis_mode"] == "additive"
    assert selected["selected_parameters"]["layers"]["hist"] == "off"
    assert selected["metadata"]["validation_paired_days"] == 3
    assert selected["config_changed"] is False
