"""Tests du module contracts/rollover.py (RolloverChain)."""

from __future__ import annotations

from datetime import date

import polars as pl

from myquantstore.contracts.rollover import RolloverChain


class TestRolloverChainConstruction:
    """Tests de la construction de la RolloverChain."""

    def test_build_chain(self, sample_contracts_df):
        """La chaîne est construite avec 3 segments pour 3 contrats."""
        chain = RolloverChain("ES", sample_contracts_df, days_before_expiry=7)

        assert len(chain) == 3
        assert chain.segments[0].ticker == "ESH5"
        assert chain.segments[1].ticker == "ESM5"
        assert chain.segments[2].ticker == "ESU5"

    def test_rollover_date_calculation(self, sample_chain):
        """rollover_date = last_trade_date - days_before_expiry."""
        # ESH5 : last_trade_date = 2025-03-14, rollover = 2025-03-14 - 7 = 2025-03-07
        seg = sample_chain.segment_for_ticker("ESH5")
        assert seg.rollover_date == date(2025, 3, 7)

        # ESM5 : last_trade_date = 2025-06-13, rollover = 2025-06-13 - 7 = 2025-06-06
        seg = sample_chain.segment_for_ticker("ESM5")
        assert seg.rollover_date == date(2025, 6, 6)

    def test_active_from_chaining(self, sample_chain):
        """active_from du segment N+1 = rollover_date du segment N."""
        # Premier segment : active_from = first_trade_date
        assert sample_chain.segments[0].active_from == date(2024, 12, 16)

        # Deuxième segment : active_from = rollover_date du premier (2025-03-07)
        assert sample_chain.segments[1].active_from == date(2025, 3, 7)

        # Troisième segment : active_from = rollover_date du deuxième (2025-06-06)
        assert sample_chain.segments[2].active_from == date(2025, 6, 6)

    def test_empty_contracts(self):
        """Une chaîne avec des contrats vides a 0 segment."""
        chain = RolloverChain("ES", pl.DataFrame(), days_before_expiry=7)
        assert len(chain) == 0

    def test_filter_combo_contracts(self):
        """Les contrats de type 'combo' sont ignorés."""
        df = pl.DataFrame(
            {
                "ticker": ["ESH5", "ES_SPREAD"],
                "first_trade_date": [date(2024, 12, 16), date(2024, 12, 16)],
                "last_trade_date": [date(2025, 3, 14), date(2025, 3, 14)],
                "settlement_date": [date(2025, 3, 14), date(2025, 3, 14)],
                "trade_tick_size": [0.25, 0.25],
                "type": ["single", "combo"],
                "product_code": ["ES", "ES"],
            }
        )
        chain = RolloverChain("ES", df, days_before_expiry=7)
        assert len(chain) == 1  # seulement le contrat "single"
        assert chain.segments[0].ticker == "ESH5"


class TestActiveContract:
    """Tests de active_contract()."""

    def test_active_contract_in_first_segment(self, sample_chain):
        """Date dans le premier segment → ESH5."""
        assert sample_chain.active_contract(date(2025, 1, 15)) == "ESH5"

    def test_active_contract_in_second_segment(self, sample_chain):
        """Date dans le deuxième segment → ESM5."""
        assert sample_chain.active_contract(date(2025, 4, 15)) == "ESM5"

    def test_active_contract_at_rollover_boundary(self, sample_chain):
        """À la date de rollover, on bascule au contrat suivant.

        Rollover de ESM5 = 2025-06-06. À cette date, c'est ESU5 qui est actif
        (active_until de ESM5 = rollover_date = 2025-06-06, exclusive).
        """
        assert sample_chain.active_contract(date(2025, 6, 6)) == "ESU5"

    def test_active_contract_before_first_segment(self, sample_chain):
        """Date avant le premier segment → premier contrat."""
        assert sample_chain.active_contract(date(2024, 1, 1)) == "ESH5"

    def test_active_contract_after_last_segment(self, sample_chain):
        """Date après le dernier segment → dernier contrat."""
        assert sample_chain.active_contract(date(2026, 1, 1)) == "ESU5"

    def test_empty_chain_returns_none(self):
        """Une chaîne vide retourne None."""
        chain = RolloverChain("ES", pl.DataFrame(), days_before_expiry=7)
        assert chain.active_contract(date(2025, 1, 1)) is None


class TestTickSize:
    """Tests de tick_size_for_ticker()."""

    def test_tick_size_for_known_ticker(self, sample_chain):
        """tick_size_for_ticker retourne la bonne valeur."""
        assert sample_chain.tick_size_for_ticker("ESH5") == 0.25
        assert sample_chain.tick_size_for_ticker("ESM5") == 0.25

    def test_tick_size_for_unknown_ticker(self, sample_chain):
        """tick_size_for_ticker retourne 0.0 pour un ticker inconnu."""
        assert sample_chain.tick_size_for_ticker("UNKNOWN") == 0.0


class TestContinuousSegments:
    """Tests de continuous_segments()."""

    def test_segments_covering_period(self, sample_chain):
        """continuous_segments retourne les segments chevauchant la période."""
        segments = sample_chain.continuous_segments(
            date(2025, 3, 1),  # chevauche ESH5 et ESM5
            date(2025, 4, 1),
        )
        tickers = [s.ticker for s in segments]
        assert "ESH5" in tickers
        assert "ESM5" in tickers

    def test_segments_single_period(self, sample_chain):
        """continuous_segments avec une période courte retourne 1 segment."""
        segments = sample_chain.continuous_segments(
            date(2025, 1, 1),
            date(2025, 1, 15),
        )
        assert len(segments) == 1
        assert segments[0].ticker == "ESH5"


class TestToTable:
    """Tests de to_table()."""

    def test_to_table_returns_dataframe(self, sample_chain):
        """to_table retourne un DataFrame avec les bonnes colonnes."""
        table = sample_chain.to_table()

        assert isinstance(table, pl.DataFrame)
        assert table.height == 3
        assert "ticker" in table.columns
        assert "first_trade_date" in table.columns
        assert "last_trade_date" in table.columns
        assert "rollover_date" in table.columns
        assert "active_from" in table.columns
        assert "active_until" in table.columns
        assert "trade_tick_size" in table.columns

    def test_to_table_empty_chain(self):
        """to_table sur une chaîne vide retourne un DataFrame vide."""
        chain = RolloverChain("ES", pl.DataFrame(), days_before_expiry=7)
        table = chain.to_table()
        assert table.is_empty()


class TestRolloverExample:
    """Test de l'exemple de la documentation : contrat expirant le vendredi 19."""

    def test_friday_19_example(self):
        """Contrat expirant le vendredi 19 → dernier jour conservé = vendredi 12."""
        df = pl.DataFrame(
            {
                "ticker": ["CONTRACT_A", "CONTRACT_B"],
                "first_trade_date": [date(2025, 1, 1), date(2025, 5, 1)],
                "last_trade_date": [date(2025, 6, 19), date(2025, 9, 19)],
                "settlement_date": [date(2025, 6, 19), date(2025, 9, 19)],
                "trade_tick_size": [1.0, 1.0],
                "type": ["single", "single"],
            }
        )

        chain = RolloverChain("TEST", df, days_before_expiry=7)

        # Le rollover_date de CONTRACT_A = 2025-06-19 - 7 = 2025-06-12 (vendredi 12)
        seg_a = chain.segment_for_ticker("CONTRACT_A")
        assert seg_a.rollover_date == date(2025, 6, 12)

        # Le 12 juin (rollover_date), on bascule à CONTRACT_B
        assert chain.active_contract(date(2025, 6, 11)) == "CONTRACT_A"
        assert chain.active_contract(date(2025, 6, 12)) == "CONTRACT_B"
