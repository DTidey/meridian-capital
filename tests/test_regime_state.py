"""regime_state blend columns and the scoring run's regime step (packet 64, AC7, AC10)."""

import pytest
import sqlalchemy as sa
import yaml

import run_scoring
from data.db import get_engine, initialise_schema
from factors.db import regime_state

_STATE = {
    "vix_close": 19.92,
    "regime": "NORMAL",
    "vix_smoothed": 22.236,
    "high_vol_share": 0.2236,
    "low_vol_share": 0.0,
}


def test_regime_state_columns(tmp_path):
    """An existing regime_state without the new columns gains them; old rows stay NULL."""
    engine = get_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE regime_state (score_date VARCHAR PRIMARY KEY, vix_close FLOAT, "
                "regime VARCHAR, computed_at VARCHAR)"
            )
        )
        conn.execute(
            sa.text("INSERT INTO regime_state VALUES ('2026-11-06', 25.64, 'HIGH_VOL', 'x')")
        )
    initialise_schema(engine)
    with engine.connect() as conn:
        cols = {c["name"] for c in sa.inspect(conn).get_columns("regime_state")}
        assert {"vix_smoothed", "high_vol_share", "low_vol_share"} <= cols
        old = conn.execute(sa.select(regime_state)).mappings().one()
        assert old["regime"] == "HIGH_VOL" and old["vix_smoothed"] is None
        run_scoring._write_regime_state(conn, "2026-11-09", _STATE)
        new = (
            conn.execute(sa.select(regime_state).where(regime_state.c.score_date == "2026-11-09"))
            .mappings()
            .one()
        )
    for key, value in _STATE.items():
        assert new[key] == pytest.approx(value) if isinstance(value, float) else new[key] == value
    engine.dispose()


def test_bad_band_stops_scoring_before_db(tmp_path, monkeypatch):
    """AC10: run_scoring raises on a bad band before it opens the database."""
    config = yaml.safe_load((run_scoring._ROOT / "config.yaml").read_text())
    config["scoring"]["regime_weights"]["low_vol"]["vix_none"] = 25  # overlaps high_vol
    monkeypatch.setattr(run_scoring, "_load_config", lambda: config)
    monkeypatch.setattr(run_scoring, "_setup_logging", lambda config: None)
    monkeypatch.setattr(run_scoring, "database_url", lambda: pytest.fail("DB opened"))
    monkeypatch.setattr("sys.argv", ["run_scoring.py", "--date", "2026-11-10"])
    with pytest.raises(ValueError, match="band edges"):
        run_scoring.main()
