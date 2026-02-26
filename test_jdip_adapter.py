"""Tests for jDip adapter — DATC compliance smoke tests.

These tests verify that the jDip headless CLI correctly adjudicates
standard Diplomacy scenarios, covering basic movement, support,
bouncing, convoy, and dislodgement.
"""

import json
import os
import subprocess
import sys

import pytest

import jdip_adapter
from jdip_adapter import (
    is_available,
    jdip_adjudicate,
    jdip_init,
    phase_to_brief,
)

pytestmark = pytest.mark.skipif(
    not is_available(),
    reason="jDip not available (missing JAR or Java)",
)


class TestJDipInit:
    def test_init_returns_standard_setup(self):
        state = jdip_init()
        assert state["phase"] == "S1901M"
        assert state["year"] == 1901
        assert len(state["powers"]) == 7
        # Standard Diplomacy has 22 starting units
        total = sum(len(u) for u in state["units"].values())
        assert total == 22
        # 22 supply centers owned at start
        assert len(state["sc_ownership"]) == 22

    def test_init_has_all_powers(self):
        state = jdip_init()
        expected = {
            "Austria", "England", "France",
            "Germany", "Italy", "Russia", "Turkey",
        }
        assert set(state["powers"]) == expected

    def test_init_france_has_three_units(self):
        state = jdip_init()
        france = state["units"]["France"]
        assert len(france) == 3
        locs = {u["location"] for u in france}
        assert "Paris" in locs
        assert "Brest" in locs
        assert "Marseilles" in locs


class TestPhaseToBrief:
    def test_spring_movement(self):
        assert phase_to_brief("Spring Movement", 1901) == "S1901M"

    def test_fall_movement(self):
        assert phase_to_brief("Fall Movement", 1901) == "F1901M"

    def test_spring_retreat(self):
        assert phase_to_brief("Spring Retreat", 1902) == "S1902R"

    def test_winter_adjustment(self):
        assert phase_to_brief("Winter Adjustment", 1901) == "F1901B"

    def test_unknown_phase_raises(self):
        with pytest.raises(ValueError):
            phase_to_brief("Bogus Phase", 1901)


class TestBasicAdjudication:
    """DATC Section 6.A — basic movement tests."""

    def _make_state(self, units, sc_ownership=None):
        """Helper to build a minimal state for adjudication."""
        if sc_ownership is None:
            sc_ownership = {}
        return {
            "units": units,
            "sc_ownership": sc_ownership,
        }

    def test_simple_move_succeeds(self):
        """A single army moves to an empty province."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Paris"},
                ],
            },
            sc_ownership={"Paris": "France"},
            orders={
                "France": ["A Paris - Burgundy"],
            },
        )
        france_units = result["resolved_units"]["France"]
        locs = {u["location"] for u in france_units}
        assert "Burgundy" in locs
        assert "Paris" not in locs

    def test_hold_succeeds(self):
        """A unit holding stays in place."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Paris"},
                ],
            },
            sc_ownership={"Paris": "France"},
            orders={
                "France": ["A Paris Hold"],
            },
        )
        france_units = result["resolved_units"]["France"]
        locs = {u["location"] for u in france_units}
        assert "Paris" in locs

    def test_bounce_equal_strength(self):
        """Two units moving to the same province bounce."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Paris"},
                ],
                "Germany": [
                    {"type": "Army", "location": "Ruhr"},
                ],
            },
            sc_ownership={
                "Paris": "France",
                "Munich": "Germany",
            },
            orders={
                "France": ["A Paris - Burgundy"],
                "Germany": ["A Ruhr - Burgundy"],
            },
        )
        # Both should stay where they were
        fr_locs = {
            u["location"]
            for u in result["resolved_units"]["France"]
        }
        de_locs = {
            u["location"]
            for u in result["resolved_units"]["Germany"]
        }
        assert "Paris" in fr_locs
        assert "Ruhr" in de_locs

    def test_support_move_dislodges(self):
        """Supported move dislodges defender."""
        result = jdip_adjudicate(
            "Fall Movement", 1901,
            units={
                "Russia": [
                    {"type": "Fleet", "location": "Constantinople"},
                    {"type": "Fleet", "location": "Black Sea"},
                ],
                "Turkey": [
                    {"type": "Fleet", "location": "Ankara"},
                ],
            },
            sc_ownership={
                "Constantinople": "Russia",
                "Sevastopol": "Russia",
                "Ankara": "Turkey",
            },
            orders={
                "Russia": [
                    "F Constantinople S F Black Sea - Ankara",
                    "F Black Sea - Ankara",
                ],
                "Turkey": [
                    "F Ankara - Constantinople",
                ],
            },
        )
        # Russia should have a fleet in Ankara now
        ru_locs = {
            u["location"]
            for u in result["resolved_units"]["Russia"]
        }
        assert "Ankara" in ru_locs
        # Turkey's fleet should be dislodged
        assert len(result["dislodged"]) >= 1

    def test_support_hold_prevents_dislodge(self):
        """Support to hold prevents a 1v1 attack."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Burgundy"},
                    {"type": "Army", "location": "Paris"},
                ],
                "Germany": [
                    {"type": "Army", "location": "Munich"},
                ],
            },
            sc_ownership={
                "Paris": "France",
                "Munich": "Germany",
            },
            orders={
                "France": [
                    "A Paris S A Burgundy",
                    "A Burgundy Hold",
                ],
                "Germany": [
                    "A Munich - Burgundy",
                ],
            },
        )
        # Burgundy should still be French
        fr_locs = {
            u["location"]
            for u in result["resolved_units"]["France"]
        }
        assert "Burgundy" in fr_locs
        # Germany should bounce back to Munich
        de_locs = {
            u["location"]
            for u in result["resolved_units"]["Germany"]
        }
        assert "Munich" in de_locs

    def test_next_phase_advances(self):
        """After Spring Movement, next phase should be Fall."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Paris"},
                ],
            },
            sc_ownership={"Paris": "France"},
            orders={
                "France": ["A Paris Hold"],
            },
        )
        # Next phase should be Fall 1901
        assert result["next_phase"] == "F1901M"


class TestConvoy:
    """DATC Section 6.F — convoy tests."""

    def test_simple_convoy(self):
        """Army convoyed across sea."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "England": [
                    {"type": "Army", "location": "London"},
                    {"type": "Fleet", "location": "English Channel"},
                ],
            },
            sc_ownership={
                "London": "England",
                "Edinburgh": "England",
            },
            orders={
                "England": [
                    "A London - Belgium",
                    "F English Channel C A London - Belgium",
                ],
            },
        )
        en_locs = {
            u["location"]
            for u in result["resolved_units"]["England"]
        }
        assert "Belgium" in en_locs


class TestGameStateIntegration:
    """Test jDip adapter integration with game_state.py."""

    def test_adjudicate_function(self):
        """game_state.adjudicate delegates to jDip."""
        from game_state import adjudicate, new_game

        state = new_game("test-jdip", "/tmp/test-jdip-game")
        # Advance to Spring Movement
        state["phase"] = "Spring Movement"

        orders = {
            "France": [
                "A Paris - Burgundy",
                "F Brest - English Channel",
                "A Marseilles Hold",
            ],
            "England": [
                "F London - North Sea",
                "F Edinburgh - Norwegian Sea",
                "A Liverpool - Yorkshire",
            ],
            "Germany": [
                "A Berlin - Kiel",
                "F Kiel - Denmark",
                "A Munich - Ruhr",
            ],
            "Austria": [
                "A Vienna - Galicia",
                "A Budapest - Serbia",
                "F Trieste - Albania",
            ],
            "Italy": [
                "A Venice - Tyrolia",
                "A Rome - Venice",
                "F Naples - Ionian Sea",
            ],
            "Russia": [
                "A Moscow - Ukraine",
                "A Warsaw - Galicia",
                "F Sevastopol - Black Sea",
                "F St Petersburg (South Coast) - Gulf of Bothnia",
            ],
            "Turkey": [
                "F Ankara - Black Sea",
                "A Constantinople - Bulgaria",
                "A Smyrna - Constantinople",
            ],
        }

        state = adjudicate(state, orders)

        # France should have moved
        fr_locs = {u["location"] for u in state["units"]["France"]}
        assert "Burgundy" in fr_locs
        assert "English Channel" in fr_locs
        # Phase should have advanced past Spring Movement
        assert state["phase"] != "Spring Movement"
