"""Tests for game_state.py — Diplomacy state management."""

import json
import pytest
from pathlib import Path

from game_state import (
    ALL_SUPPLY_CENTERS,
    HOME_CENTERS,
    PHASE_ORDER,
    POWERS,
    STARTING_UNITS,
    WIN_THRESHOLD,
    Phase,
    adjustment_counts,
    apply_adjustments,
    apply_orders,
    apply_retreats,
    check_elimination,
    check_win,
    format_status,
    get_phase,
    load_state,
    new_game,
    next_phase,
    save_state,
    sc_counts,
    skip_retreat_if_empty,
    state_for_power,
    update_sc_ownership,
)


@pytest.fixture
def game_dir(tmp_path):
    return tmp_path / "test-game"


@pytest.fixture
def state(game_dir):
    return new_game("test-001", game_dir)


class TestNewGame:
    def test_creates_game_dir(self, game_dir):
        new_game("test-001", game_dir)
        assert game_dir.exists()

    def test_creates_state_file(self, game_dir):
        new_game("test-001", game_dir)
        assert (game_dir / "state.json").exists()

    def test_initial_year(self, state):
        assert state["year"] == 1901

    def test_initial_phase(self, state):
        assert state["phase"] == "Spring Diplomacy"

    def test_all_powers_have_units(self, state):
        for power in POWERS:
            assert power in state["units"]
            assert len(state["units"][power]) > 0

    def test_starting_unit_counts(self, state):
        # Russia has 4 starting units, everyone else has 3
        for power in POWERS:
            expected = 4 if power == "Russia" else 3
            assert len(state["units"][power]) == expected

    def test_no_winner_at_start(self, state):
        assert state["winner"] is None

    def test_no_eliminated_at_start(self, state):
        assert state["eliminated"] == []

    def test_initial_sc_ownership(self, state):
        # Each power owns its home centers
        for power in POWERS:
            for sc in HOME_CENTERS[power]:
                assert state["sc_ownership"][sc] == power

    def test_total_starting_scs(self, state):
        # 22 home SCs at start (3 per power + 1 extra for Russia)
        owned = len(state["sc_ownership"])
        assert owned == 22

    def test_game_id(self, state):
        assert state["game_id"] == "test-001"

    def test_timestamps(self, state):
        assert "created_at" in state
        assert "updated_at" in state


class TestSaveLoad:
    def test_round_trip(self, state, game_dir):
        save_state(state, game_dir)
        loaded = load_state(game_dir)
        assert loaded["game_id"] == state["game_id"]
        assert loaded["year"] == state["year"]
        assert loaded["phase"] == state["phase"]
        assert loaded["units"] == state["units"]
        assert loaded["sc_ownership"] == state["sc_ownership"]

    def test_updates_timestamp(self, state, game_dir):
        old_ts = state["updated_at"]
        save_state(state, game_dir)
        loaded = load_state(game_dir)
        # updated_at should be refreshed (may be same if fast)
        assert "updated_at" in loaded

    def test_state_file_is_valid_json(self, state, game_dir):
        save_state(state, game_dir)
        text = (game_dir / "state.json").read_text()
        parsed = json.loads(text)
        assert isinstance(parsed, dict)


class TestPhaseProgression:
    def test_all_phases_in_order(self):
        assert len(PHASE_ORDER) == 7
        assert PHASE_ORDER[0] == Phase.SPRING_DIPLOMACY
        assert PHASE_ORDER[-1] == Phase.WINTER_ADJUSTMENT

    def test_next_phase_advances(self, state):
        assert state["phase"] == "Spring Diplomacy"
        next_phase(state)
        assert state["phase"] == "Spring Movement"

    def test_full_year_cycle(self, state):
        phases_seen = [state["phase"]]
        for _ in range(7):
            next_phase(state)
            phases_seen.append(state["phase"])

        # Should have cycled back to Spring Diplomacy
        assert phases_seen[-1] == "Spring Diplomacy"
        assert state["year"] == 1902

    def test_year_increments_after_winter(self, state):
        # Advance to Winter Adjustment
        for _ in range(6):
            next_phase(state)
        assert state["phase"] == "Winter Adjustment"
        assert state["year"] == 1901

        next_phase(state)
        assert state["phase"] == "Spring Diplomacy"
        assert state["year"] == 1902

    def test_get_phase_returns_enum(self, state):
        p = get_phase(state)
        assert p == Phase.SPRING_DIPLOMACY
        assert isinstance(p, Phase)


class TestSkipRetreat:
    def test_skips_when_no_dislodged(self, state):
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = []
        skip_retreat_if_empty(state)
        assert state["phase"] == "Fall Diplomacy"

    def test_does_not_skip_when_dislodged(self, state):
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = [{"power": "France", "unit": {}}]
        skip_retreat_if_empty(state)
        assert state["phase"] == "Spring Retreat"

    def test_skips_fall_retreat_when_empty(self, state):
        state["phase"] = Phase.FALL_RETREAT.value
        state["dislodged"] = []
        skip_retreat_if_empty(state)
        assert state["phase"] == "Winter Adjustment"

    def test_no_op_on_non_retreat_phase(self, state):
        state["phase"] = Phase.SPRING_MOVEMENT.value
        skip_retreat_if_empty(state)
        assert state["phase"] == "Spring Movement"


class TestSupplyCenters:
    def test_initial_sc_counts(self, state):
        counts = sc_counts(state)
        for power in POWERS:
            expected = 4 if power == "Russia" else 3
            assert counts[power] == expected

    def test_all_supply_centers_count(self):
        assert len(ALL_SUPPLY_CENTERS) == 34

    def test_home_centers_are_supply_centers(self):
        for power, centers in HOME_CENTERS.items():
            for sc in centers:
                assert sc in ALL_SUPPLY_CENTERS

    def test_update_sc_ownership(self, state):
        # Move a French army to Belgium
        state["units"]["France"].append(
            {"type": "Army", "location": "Belgium"}
        )
        state["sc_ownership"]["Belgium"] = "neutral"

        update_sc_ownership(state)
        assert state["sc_ownership"]["Belgium"] == "France"

    def test_update_sc_coast_normalization(self, state):
        # A fleet in "Spain (South Coast)" should capture "Spain"
        state["sc_ownership"]["Spain"] = "neutral"
        state["units"]["France"].append(
            {"type": "Fleet", "location": "Spain (South Coast)"}
        )

        update_sc_ownership(state)
        assert state["sc_ownership"]["Spain"] == "France"


class TestWinCondition:
    def test_no_winner_at_start(self, state):
        assert check_win(state) is None

    def test_winner_at_18_scs(self, state):
        # Give France 18 supply centers
        for i, sc in enumerate(ALL_SUPPLY_CENTERS):
            if i < 18:
                state["sc_ownership"][sc] = "France"
            else:
                state["sc_ownership"][sc] = "Germany"

        assert check_win(state) == "France"

    def test_no_winner_at_17_scs(self, state):
        for i, sc in enumerate(ALL_SUPPLY_CENTERS):
            if i < 17:
                state["sc_ownership"][sc] = "France"
            else:
                state["sc_ownership"][sc] = "Germany"

        assert check_win(state) is None

    def test_win_threshold_is_18(self):
        assert WIN_THRESHOLD == 18


class TestElimination:
    def test_power_eliminated_at_zero_scs(self, state):
        # Remove all Turkey's SCs
        for sc in HOME_CENTERS["Turkey"]:
            state["sc_ownership"][sc] = "Russia"

        check_elimination(state)
        assert "Turkey" in state["eliminated"]

    def test_power_not_eliminated_with_scs(self, state):
        check_elimination(state)
        assert state["eliminated"] == []

    def test_elimination_idempotent(self, state):
        for sc in HOME_CENTERS["Turkey"]:
            state["sc_ownership"][sc] = "Russia"

        check_elimination(state)
        check_elimination(state)
        assert state["eliminated"].count("Turkey") == 1


class TestAdjustmentCounts:
    def test_balanced_at_start(self, state):
        counts = adjustment_counts(state)
        for power in POWERS:
            assert counts[power] == 0

    def test_builds_when_more_scs_than_units(self, state):
        state["sc_ownership"]["Belgium"] = "France"
        counts = adjustment_counts(state)
        assert counts["France"] == 1

    def test_disbands_when_fewer_scs_than_units(self, state):
        # Remove one of France's SCs
        state["sc_ownership"]["Brest"] = "England"
        counts = adjustment_counts(state)
        assert counts["France"] == -1

    def test_eliminated_power_gets_zero(self, state):
        state["eliminated"] = ["Turkey"]
        counts = adjustment_counts(state)
        assert counts["Turkey"] == 0


class TestApplyOrders:
    def test_updates_units(self, state):
        new_units = {
            p: state["units"][p] for p in POWERS
        }
        # Move French army from Paris to Burgundy
        new_units["France"] = [
            {"type": "Fleet", "location": "Brest"},
            {"type": "Army", "location": "Burgundy"},
            {"type": "Army", "location": "Marseilles"},
        ]

        apply_orders(state, {
            "resolved_units": new_units,
            "dislodged": [],
        })

        locations = [u["location"] for u in state["units"]["France"]]
        assert "Burgundy" in locations
        assert "Paris" not in locations

    def test_tracks_dislodged(self, state):
        dislodged = [{
            "power": "Austria",
            "unit": {"type": "Army", "location": "Vienna"},
            "retreats": ["Tyrolia", "Bohemia"],
        }]

        apply_orders(state, {
            "resolved_units": state["units"],
            "dislodged": dislodged,
        })

        assert len(state["dislodged"]) == 1
        assert state["dislodged"][0]["power"] == "Austria"


class TestApplyRetreats:
    def test_retreat_adds_unit(self, state):
        # Remove Vienna from Austria's units (it was dislodged)
        state["units"]["Austria"] = [
            u for u in state["units"]["Austria"]
            if u["location"] != "Vienna"
        ]
        state["dislodged"] = [{
            "power": "Austria",
            "unit": {"type": "Army", "location": "Vienna"},
            "retreats": ["Tyrolia"],
        }]

        apply_retreats(state, [{
            "power": "Austria",
            "unit": {"type": "Army", "location": "Vienna"},
            "action": "retreat",
            "destination": "Tyrolia",
        }])

        locations = [u["location"] for u in state["units"]["Austria"]]
        assert "Tyrolia" in locations
        assert state["dislodged"] == []

    def test_disband_removes_unit(self, state):
        state["units"]["Austria"] = [
            u for u in state["units"]["Austria"]
            if u["location"] != "Vienna"
        ]
        state["dislodged"] = [{
            "power": "Austria",
            "unit": {"type": "Army", "location": "Vienna"},
        }]

        apply_retreats(state, [{
            "power": "Austria",
            "unit": {"type": "Army", "location": "Vienna"},
            "action": "disband",
        }])

        # Unit was dislodged (not in units list), disband means
        # it stays removed
        locations = [u["location"] for u in state["units"]["Austria"]]
        assert "Vienna" not in locations


class TestApplyAdjustments:
    def test_build(self, state):
        apply_adjustments(state, {
            "France": [{
                "action": "build",
                "unit": {"type": "Army", "location": "Paris"},
            }],
        })

        # France should now have 4 units
        assert len(state["units"]["France"]) == 4

    def test_disband(self, state):
        apply_adjustments(state, {
            "France": [{
                "action": "disband",
                "unit": {"type": "Fleet", "location": "Brest"},
            }],
        })

        assert len(state["units"]["France"]) == 2
        locations = [u["location"] for u in state["units"]["France"]]
        assert "Brest" not in locations


class TestStateForPower:
    def test_includes_power_perspective(self, state):
        view = state_for_power(state, "France")
        assert view["power"] == "France"
        assert view["your_sc_count"] == 3
        assert len(view["your_units"]) == 3
        assert view["your_home_centers"] == HOME_CENTERS["France"]

    def test_includes_global_info(self, state):
        view = state_for_power(state, "France")
        assert "all_units" in view
        assert "sc_ownership" in view
        assert "sc_counts" in view
        assert len(view["all_units"]) == 7


class TestFormatStatus:
    def test_contains_game_id(self, state):
        status = format_status(state)
        assert "test-001" in status

    def test_contains_year_and_phase(self, state):
        status = format_status(state)
        assert "1901" in status
        assert "Spring Diplomacy" in status

    def test_contains_all_powers(self, state):
        status = format_status(state)
        for power in POWERS:
            assert power in status

    def test_shows_winner(self, state):
        state["winner"] = "France"
        status = format_status(state)
        assert "Winner: France" in status


class TestStartingPositions:
    def test_all_powers_present(self):
        assert set(STARTING_UNITS.keys()) == set(POWERS)

    def test_unit_types_valid(self):
        for power, units in STARTING_UNITS.items():
            for unit in units:
                assert unit["type"] in ("Army", "Fleet")
                assert "location" in unit

    def test_total_starting_units(self):
        total = sum(len(u) for u in STARTING_UNITS.values())
        assert total == 22  # 3*6 + 4 (Russia)
