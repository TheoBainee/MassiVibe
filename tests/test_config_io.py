"""Tests de myquantstore.config_io."""

from __future__ import annotations

from pathlib import Path

from myquantstore.config_io import add_instruments_to_config, resolve_writable_config_path
from myquantstore.instruments import InstrumentType


def _write_config(path: Path) -> None:
    path.write_text(
        """# commentaire top
[instruments]
# futures CME
futures = ["ES"]
forex = []
stocks = ["AAPL"]
indices = []
options = []

[fetch]
timeframe = "1min"
""",
        encoding="utf-8",
    )


def test_add_instruments_dedup_and_preserve_comment(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    _write_config(cfg)

    added = add_instruments_to_config(
        cfg,
        [
            (InstrumentType.STOCKS, "AAPL"),  # déjà présent
            (InstrumentType.STOCKS, "MSFT"),
            (InstrumentType.FOREX, "EURUSD"),
            (InstrumentType.INDICES, "NDX"),
        ],
    )
    assert added["stocks"] == ["MSFT"]
    assert added["forex"] == ["EURUSD"]
    assert added["indices"] == ["NDX"]
    assert added["futures"] == []

    text = cfg.read_text(encoding="utf-8")
    assert "# commentaire top" in text
    assert "# futures CME" in text
    assert "MSFT" in text
    assert "EURUSD" in text
    assert "NDX" in text
    # AAPL toujours là une seule fois dans stocks
    assert text.count("AAPL") == 1


def test_add_dry_run_no_write(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    _write_config(cfg)
    before = cfg.read_text(encoding="utf-8")
    added = add_instruments_to_config(
        cfg,
        [(InstrumentType.STOCKS, "TSLA")],
        dry_run=True,
    )
    assert added["stocks"] == ["TSLA"]
    assert cfg.read_text(encoding="utf-8") == before


def test_add_missing_file(tmp_path: Path):
    try:
        add_instruments_to_config(tmp_path / "nope.toml", [(InstrumentType.STOCKS, "X")])
        raise AssertionError("devait lever")
    except FileNotFoundError:
        pass


def test_resolve_writable_config_path_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("myquantstore.config_io.get_user_config_path", lambda: tmp_path / "u.toml")
    monkeypatch.setattr("myquantstore.config_io.get_repo_config_path", lambda: tmp_path / "r.toml")
    (tmp_path / "r.toml").write_text("[instruments]\nfutures=[]\n", encoding="utf-8")
    assert resolve_writable_config_path() == tmp_path / "r.toml"
