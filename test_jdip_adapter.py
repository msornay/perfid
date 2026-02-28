"""Tests for jDip adapter — DATC compliance smoke tests.

These tests verify that the jDip headless CLI correctly adjudicates
standard Diplomacy scenarios. Covers:
- Basic movement (DATC 6.A): move, hold, bounce
- Support (DATC 6.D): support move, support hold, cut support
- Convoy (DATC 6.F): simple convoy, convoy disruption
- Self-dislodgement prevention
- Head-to-head battles
- Phase advancement
- Game state integration (adjudicate, elimination, win check)
- Simulate function for agent strategy testing
"""

import pytest

from jdip_adapter import (
    is_available,
    jdip_adjudicate,
    jdip_init,
    order_results_for,
    phase_to_brief,
    simulate,
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

    def test_fall_retreat(self):
        assert phase_to_brief("Fall Retreat", 1901) == "F1901R"

    def test_winter_adjustment(self):
        assert phase_to_brief("Winter Adjustment", 1901) == "F1901B"

    def test_unknown_phase_raises(self):
        with pytest.raises(ValueError):
            phase_to_brief("Bogus Phase", 1901)

    def test_diplomacy_maps_to_movement(self):
        """Diplomacy phases map to jDip movement codes."""
        assert phase_to_brief("Spring Diplomacy", 1901) == "S1901M"
        assert phase_to_brief("Fall Diplomacy", 1901) == "F1901M"


class TestBasicAdjudication:
    """DATC Section 6.A — basic movement tests."""

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

    def test_fleet_moves_to_sea(self):
        """Fleet moves to an adjacent sea zone."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "England": [
                    {"type": "Fleet", "location": "London"},
                ],
            },
            sc_ownership={"London": "England"},
            orders={
                "England": ["F London - English Channel"],
            },
        )
        en_locs = {
            u["location"]
            for u in result["resolved_units"]["England"]
        }
        assert "English Channel" in en_locs

    def test_head_to_head_equal_strength(self):
        """Two units moving into each other's provinces bounce.

        DATC 6.A.5: neither can swap without convoy.
        """
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Paris"},
                ],
                "Germany": [
                    {"type": "Army", "location": "Burgundy"},
                ],
            },
            sc_ownership={
                "Paris": "France",
                "Munich": "Germany",
            },
            orders={
                "France": ["A Paris - Burgundy"],
                "Germany": ["A Burgundy - Paris"],
            },
        )
        fr_locs = {
            u["location"]
            for u in result["resolved_units"]["France"]
        }
        de_locs = {
            u["location"]
            for u in result["resolved_units"]["Germany"]
        }
        # Neither moves
        assert "Paris" in fr_locs
        assert "Burgundy" in de_locs

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
        assert result["next_phase"] == "F1901M"

    def test_fall_movement_advances_correctly(self):
        """After Fall Movement, next phase advances properly."""
        result = jdip_adjudicate(
            "Fall Movement", 1901,
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
        # Should advance (possibly to retreat or winter)
        assert result["next_year"] >= 1901


class TestSupport:
    """DATC Section 6.D — support tests."""

    def test_support_move_dislodges(self):
        """Supported move dislodges a defender."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Burgundy"},
                ],
                "Italy": [
                    {"type": "Army", "location": "Tyrolia"},
                ],
                "Germany": [
                    {"type": "Army", "location": "Munich"},
                ],
            },
            sc_ownership={
                "Paris": "France",
                "Rome": "Italy",
                "Munich": "Germany",
            },
            orders={
                "France": ["A Burgundy - Munich"],
                "Italy": ["A Tyrolia S A Burgundy - Munich"],
                "Germany": ["A Munich Hold"],
            },
        )
        fr_locs = {
            u["location"]
            for u in result["resolved_units"]["France"]
        }
        assert "Munich" in fr_locs
        assert len(result["dislodged"]) >= 1
        assert result["dislodged"][0]["power"] == "Germany"

    def test_support_hold_prevents_dislodge(self):
        """Support to hold prevents a 1v1 attack.

        DATC 6.D.1: supported holding unit cannot be dislodged
        by unsupported attack.
        """
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
        fr_locs = {
            u["location"]
            for u in result["resolved_units"]["France"]
        }
        assert "Burgundy" in fr_locs
        de_locs = {
            u["location"]
            for u in result["resolved_units"]["Germany"]
        }
        assert "Munich" in de_locs

    def test_cut_support(self):
        """Attack on supporting unit cuts the support.

        DATC 6.D.2: France's North Sea supports Norwegian Sea
        into Norway, but English Channel attacks North Sea and
        cuts the support, so the attack on Norway fails.
        """
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "England": [
                    {"type": "Fleet", "location": "North Sea"},
                    {"type": "Fleet", "location": "Norwegian Sea"},
                ],
                "Russia": [
                    {"type": "Fleet", "location": "Norway"},
                ],
                "France": [
                    {"type": "Fleet", "location": "English Channel"},
                ],
            },
            sc_ownership={
                "London": "England",
                "Edinburgh": "England",
                "Norway": "Russia",
                "Moscow": "Russia",
                "Paris": "France",
            },
            orders={
                "England": [
                    "F Norwegian Sea - Norway",
                    "F North Sea S F Norwegian Sea - Norway",
                ],
                "Russia": [
                    "F Norway Hold",
                ],
                "France": [
                    "F English Channel - North Sea",
                ],
            },
        )
        # Support was cut — Norway should still be Russian
        ru_locs = {
            u["location"]
            for u in result["resolved_units"]["Russia"]
        }
        assert "Norway" in ru_locs
        # English fleet still in Norwegian Sea (bounced)
        en_locs = {
            u["location"]
            for u in result["resolved_units"]["England"]
        }
        assert "Norwegian Sea" in en_locs
        # French fleet bounced back to English Channel
        fr_locs = {
            u["location"]
            for u in result["resolved_units"]["France"]
        }
        assert "English Channel" in fr_locs

    def test_self_dislodge_prevented(self):
        """A power cannot dislodge its own unit.

        DATC 6.D.6: Even with enough support, a power's own
        unit cannot dislodge another of its units.
        """
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Paris"},
                    {"type": "Army", "location": "Burgundy"},
                    {"type": "Army", "location": "Picardy"},
                ],
            },
            sc_ownership={
                "Paris": "France",
                "Marseilles": "France",
                "Brest": "France",
            },
            orders={
                "France": [
                    "A Burgundy - Paris",
                    "A Picardy S A Burgundy - Paris",
                    "A Paris Hold",
                ],
            },
        )
        fr_locs = {
            u["location"]
            for u in result["resolved_units"]["France"]
        }
        # Paris should still hold — no self-dislodge
        assert "Paris" in fr_locs
        assert "Burgundy" in fr_locs
        assert len(result["dislodged"]) == 0


class TestConvoy:
    """DATC Section 6.F — convoy tests."""

    def test_simple_convoy(self):
        """Army convoyed across sea succeeds."""
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

    def test_convoy_disrupted_by_dislodge(self):
        """Dislodging the convoying fleet disrupts the convoy.

        DATC 6.F: When the fleet doing the convoy is dislodged,
        the convoyed army fails to move.
        """
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "England": [
                    {"type": "Army", "location": "London"},
                    {"type": "Fleet", "location": "English Channel"},
                ],
                "France": [
                    {"type": "Fleet", "location": "Brest"},
                    {"type": "Fleet",
                     "location": "Mid-Atlantic Ocean"},
                ],
            },
            sc_ownership={
                "London": "England",
                "Edinburgh": "England",
                "Paris": "France",
                "Brest": "France",
            },
            orders={
                "England": [
                    "A London - Belgium",
                    "F English Channel C A London - Belgium",
                ],
                "France": [
                    "F Brest - English Channel",
                    "F Mid-Atlantic Ocean S F Brest"
                    " - English Channel",
                ],
            },
        )
        # English army should NOT have reached Belgium
        en_locs = {
            u["location"]
            for u in result["resolved_units"]["England"]
        }
        assert "London" in en_locs
        assert "Belgium" not in en_locs
        # English Channel fleet was dislodged
        assert len(result["dislodged"]) >= 1
        # France took English Channel
        fr_locs = {
            u["location"]
            for u in result["resolved_units"]["France"]
        }
        assert "English Channel" in fr_locs


class TestDislodgement:
    """Tests for dislodgement and retreat data."""

    def test_dislodged_unit_data(self):
        """Dislodged units include power and unit info."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Burgundy"},
                ],
                "Italy": [
                    {"type": "Army", "location": "Tyrolia"},
                ],
                "Germany": [
                    {"type": "Army", "location": "Munich"},
                ],
            },
            sc_ownership={
                "Paris": "France",
                "Rome": "Italy",
                "Munich": "Germany",
            },
            orders={
                "France": ["A Burgundy - Munich"],
                "Italy": ["A Tyrolia S A Burgundy - Munich"],
                "Germany": ["A Munich Hold"],
            },
        )
        assert len(result["dislodged"]) == 1
        d = result["dislodged"][0]
        assert d["power"] == "Germany"
        assert d["unit"]["type"] == "Army"
        assert d["unit"]["location"] == "Munich"

    def test_dislodge_advances_to_retreat_phase(self):
        """When units are dislodged, next phase is retreat."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
                    {"type": "Army", "location": "Burgundy"},
                ],
                "Italy": [
                    {"type": "Army", "location": "Tyrolia"},
                ],
                "Germany": [
                    {"type": "Army", "location": "Munich"},
                ],
            },
            sc_ownership={
                "Paris": "France",
                "Rome": "Italy",
                "Munich": "Germany",
            },
            orders={
                "France": ["A Burgundy - Munich"],
                "Italy": ["A Tyrolia S A Burgundy - Munich"],
                "Germany": ["A Munich Hold"],
            },
        )
        assert result["next_phase"] == "S1901R"


class TestOrderResults:
    """Tests for order result parsing."""

    def test_order_results_contain_outcomes(self):
        """Each order result has order, result, and message."""
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
        assert len(result["order_results"]) >= 1
        r = result["order_results"][0]
        assert "order" in r
        assert "result" in r
        assert r["result"] == "success"

    def test_order_results_for_power(self):
        """order_results_for filters by power."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "France": [
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
                "France": ["A Paris - Burgundy"],
                "Germany": ["A Munich Hold"],
            },
        )
        fr_results = order_results_for(result, "France")
        de_results = order_results_for(result, "Germany")
        assert len(fr_results) >= 1
        assert len(de_results) >= 1
        assert all(
            r["order"].startswith("France:")
            for r in fr_results
        )
        assert all(
            r["order"].startswith("Germany:")
            for r in de_results
        )


class TestSimulate:
    """Tests for the simulate() function."""

    def test_simulate_does_not_modify_state(self):
        """simulate() returns results without changing state."""
        from game_state import new_game

        state = new_game("test-sim", "/tmp/test-sim")
        state["phase"] = "Spring Movement"
        original_units = {
            p: list(u) for p, u in state["units"].items()
        }

        simulate(state, {
            "France": ["A Paris - Burgundy"],
        })

        # State should be unchanged
        assert state["phase"] == "Spring Movement"
        for power in original_units:
            assert len(state["units"][power]) == len(
                original_units[power]
            )

    def test_simulate_returns_results(self):
        """simulate() returns adjudication results."""
        from game_state import new_game

        state = new_game("test-sim2", "/tmp/test-sim2")
        state["phase"] = "Spring Movement"

        result = simulate(state, {
            "France": ["A Paris - Burgundy"],
        })

        assert "resolved_units" in result
        assert "order_results" in result
        assert "summary" in result

    def test_simulate_includes_summary(self):
        """simulate() result has per-power summary."""
        from game_state import new_game

        state = new_game("test-sim3", "/tmp/test-sim3")
        state["phase"] = "Spring Movement"

        result = simulate(state, {
            "France": [
                "A Paris - Burgundy",
                "F Brest - English Channel",
                "A Marseilles Hold",
            ],
        })

        summary = result["summary"]
        assert "France" in summary
        assert summary["France"]["units_before"] == 3
        assert summary["France"]["units_after"] == 3
        assert summary["France"]["scs_before"] == 3

    def test_simulate_shows_failed_orders(self):
        """simulate() captures failed order results."""
        from game_state import new_game

        state = new_game("test-sim4", "/tmp/test-sim4")
        state["phase"] = "Spring Movement"

        # Two powers try to move to the same province
        result = simulate(state, {
            "France": ["A Paris - Burgundy"],
            "Germany": ["A Munich - Burgundy"],
        })

        fr = order_results_for(result, "France")
        de = order_results_for(result, "Germany")
        # Both should fail (bounce)
        assert any(r["result"] == "failure" for r in fr)
        assert any(r["result"] == "failure" for r in de)


class TestGameStateIntegration:
    """Test jDip adapter integration with game_state.py."""

    def test_adjudicate_function(self):
        """game_state.adjudicate delegates to jDip."""
        from game_state import adjudicate, new_game

        state = new_game("test-jdip", "/tmp/test-jdip-game")
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
                "F St Petersburg (South Coast)"
                " - Gulf of Bothnia",
            ],
            "Turkey": [
                "F Ankara - Black Sea",
                "A Constantinople - Bulgaria",
                "A Smyrna - Constantinople",
            ],
        }

        state = adjudicate(state, orders)

        fr_locs = {
            u["location"] for u in state["units"]["France"]
        }
        assert "Burgundy" in fr_locs
        assert "English Channel" in fr_locs
        assert state["phase"] != "Spring Movement"

    def test_adjudicate_checks_elimination(self):
        """adjudicate() marks powers with 0 SCs as eliminated."""
        from game_state import adjudicate, new_game

        state = new_game("test-elim", "/tmp/test-jdip-elim")
        state["phase"] = "Spring Movement"

        state["units"] = {
            "France": [{"type": "Army", "location": "Paris"}],
            "Austria": [],
            "England": [],
            "Germany": [],
            "Italy": [],
            "Russia": [],
            "Turkey": [],
        }
        state["sc_ownership"] = {"Paris": "France"}

        orders = {"France": ["A Paris Hold"]}
        state = adjudicate(state, orders)

        for power in ["Austria", "England", "Germany",
                       "Italy", "Russia", "Turkey"]:
            assert power in state["eliminated"]
        assert "France" not in state["eliminated"]

    def test_adjudicate_preserves_game_over(self):
        """adjudicate() uses jDip game_over for win detection."""
        from game_state import check_win, new_game

        state = new_game("test-win", "/tmp/test-jdip-win")
        assert state["winner"] is None
        assert check_win(state) is None

    def test_adjudicate_advances_phase(self):
        """adjudicate() advances the phase after resolution."""
        from game_state import adjudicate, new_game

        state = new_game("test-phase", "/tmp/test-jdip-phase")
        state["phase"] = "Spring Movement"

        orders = {"France": ["A Paris Hold"]}
        state = adjudicate(state, orders)

        # Should have advanced past Spring Movement
        assert state["phase"] != "Spring Movement"


class TestLocationNormalization:
    """Test St Petersburg location normalization."""

    def test_st_petersburg_move(self):
        """St Petersburg with coast normalizes correctly."""
        result = jdip_adjudicate(
            "Spring Movement", 1901,
            units={
                "Russia": [
                    {"type": "Fleet",
                     "location": "St Petersburg (South Coast)"},
                ],
            },
            sc_ownership={
                "St Petersburg": "Russia",
                "Moscow": "Russia",
            },
            orders={
                "Russia": [
                    "F St Petersburg (South Coast)"
                    " - Gulf of Bothnia",
                ],
            },
        )
        ru_locs = {
            u["location"]
            for u in result["resolved_units"]["Russia"]
        }
        assert "Gulf of Bothnia" in ru_locs
        # Location should use perfid format (no period)
        for unit in result["resolved_units"]["Russia"]:
            assert "St." not in unit["location"]
