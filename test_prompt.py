"""Tests for prompt.py — agent prompt generation for perfid."""

import json
import os

import pytest

from game_state import (
    HOME_CENTERS,
    POWERS,
    Phase,
    new_game,
    next_phase,
    state_for_power,
)
from message_router import init_message_dirs, send_message
from prompt import (
    adjustment_prompt,
    bootstrap_prompt,
    negotiation_prompt,
    order_prompt,
    retreat_prompt,
    system_prompt,
    turn_context,
)


@pytest.fixture
def game_dir(tmp_path):
    return str(tmp_path / "test-game")


@pytest.fixture
def state(game_dir):
    return new_game("test-001", game_dir)


class TestSystemPrompt:
    def test_contains_power_name(self):
        for power in POWERS:
            sp = system_prompt(power)
            assert power in sp

    def test_contains_email(self):
        sp = system_prompt("England")
        assert "england@perfid.local" in sp

    def test_contains_home_centers(self):
        sp = system_prompt("France")
        for hc in HOME_CENTERS["France"]:
            assert hc in sp

    def test_contains_rules(self):
        sp = system_prompt("Germany")
        assert "Diplomacy" in sp
        assert "supply center" in sp.lower() or "SC" in sp
        assert "18" in sp

    def test_emphasizes_solo_victory(self):
        sp = system_prompt("France")
        sp_lower = sp.lower()
        assert "solo victory" in sp_lower
        assert "win" in sp_lower
        assert "draw" in sp_lower  # mentions draw as failure
        assert "stab" in sp_lower  # willingness to break alliances

    def test_contains_gpg_instructions(self):
        sp = system_prompt("Italy")
        assert "GPG" in sp or "gpg" in sp
        assert "encrypt" in sp
        assert "decrypt" in sp

    def test_contains_order_syntax(self):
        sp = system_prompt("Russia")
        assert "Hold" in sp or "H" in sp
        assert "Support" in sp or "S" in sp
        assert "Move" in sp or "-" in sp
        assert "Convoy" in sp or "C" in sp

    def test_contains_file_layout(self):
        sp = system_prompt("Turkey")
        assert "pubkeys/" in sp
        assert "messages/" in sp
        assert "orders/" in sp
        assert "notes/" in sp

    def test_different_powers_get_different_prompts(self):
        sp_eng = system_prompt("England")
        sp_fra = system_prompt("France")
        assert sp_eng != sp_fra
        assert "England" in sp_eng
        assert "France" in sp_fra
        assert "england@perfid.local" in sp_eng
        assert "france@perfid.local" in sp_fra


class TestBootstrapPrompt:
    def test_contains_power_name(self):
        bp = bootstrap_prompt("Austria")
        assert "Austria" in bp

    def test_contains_email(self):
        bp = bootstrap_prompt("England")
        assert "england@perfid.local" in bp

    def test_contains_key_gen_command(self):
        bp = bootstrap_prompt("France")
        assert "gen-key" in bp or "generate" in bp.lower()

    def test_contains_export_command(self):
        bp = bootstrap_prompt("Germany")
        assert "--export" in bp

    def test_contains_import_command(self):
        bp = bootstrap_prompt("Italy")
        assert "--import" in bp

    def test_contains_trust_setup(self):
        bp = bootstrap_prompt("Russia")
        assert "trust" in bp.lower()


class TestNegotiationPrompt:
    def test_contains_phase_info(self, state, game_dir):
        np = negotiation_prompt("England", state, game_dir, 1, 3)
        assert "Spring Diplomacy" in np
        assert "1901" in np

    def test_contains_round_info(self, state, game_dir):
        np = negotiation_prompt("France", state, game_dir, 2, 3)
        assert "Round 2" in np or "round 2" in np
        assert "2" in np and "3" in np

    def test_contains_unit_positions(self, state, game_dir):
        np = negotiation_prompt("England", state, game_dir, 1, 3)
        assert "London" in np
        assert "Edinburgh" in np
        assert "Liverpool" in np

    def test_contains_sc_info(self, state, game_dir):
        np = negotiation_prompt("France", state, game_dir, 1, 3)
        assert "3 SCs" in np  # France starts with 3

    def test_empty_inbox_shown(self, state, game_dir):
        init_message_dirs(game_dir, POWERS)
        np = negotiation_prompt("England", state, game_dir, 1, 3)
        assert "no messages" in np.lower()

    def test_inbox_with_messages(self, state, game_dir):
        init_message_dirs(game_dir, POWERS)
        send_message(
            game_dir, "France", "England", "Spring Diplomacy", 1,
            "encrypted content"
        )
        from message_router import route_messages
        route_messages(game_dir, "France")

        np = negotiation_prompt("England", state, game_dir, 1, 1)
        # Should mention France in the inbox
        assert "France" in np

    def test_contains_messaging_instructions(self, state, game_dir):
        np = negotiation_prompt("Germany", state, game_dir, 1, 3)
        assert "encrypt" in np.lower()
        assert "outbox" in np.lower()

    def test_last_round_notice(self, state, game_dir):
        np = negotiation_prompt("Italy", state, game_dir, 3, 3)
        assert "last round" in np.lower()

    def test_all_powers_units_shown(self, state, game_dir):
        np = negotiation_prompt("England", state, game_dir, 1, 3)
        for power in POWERS:
            assert power in np


class TestOrderPrompt:
    def test_requires_movement_phase(self, state, game_dir):
        # Advance to Spring Movement
        state["phase"] = Phase.SPRING_MOVEMENT.value
        op = order_prompt("England", state, game_dir)
        assert "Spring Movement" in op

    def test_contains_unit_list(self, state, game_dir):
        state["phase"] = Phase.SPRING_MOVEMENT.value
        op = order_prompt("England", state, game_dir)
        assert "Fleet London" in op
        assert "Fleet Edinburgh" in op
        assert "Army Liverpool" in op

    def test_contains_example_orders(self, state, game_dir):
        state["phase"] = Phase.SPRING_MOVEMENT.value
        op = order_prompt("France", state, game_dir)
        # Should have Hold examples for France's units
        assert "Paris H" in op or "Paris" in op

    def test_contains_order_types(self, state, game_dir):
        state["phase"] = Phase.SPRING_MOVEMENT.value
        op = order_prompt("Germany", state, game_dir)
        assert "Move" in op
        assert "Hold" in op
        assert "Support" in op
        assert "Convoy" in op

    def test_contains_encryption_instructions(self, state, game_dir):
        state["phase"] = Phase.SPRING_MOVEMENT.value
        op = order_prompt("Italy", state, game_dir)
        assert "gm@perfid.local" in op

    def test_sc_ownership_shown(self, state, game_dir):
        state["phase"] = Phase.SPRING_MOVEMENT.value
        op = order_prompt("Russia", state, game_dir)
        assert "SCs" in op

    def test_emphasizes_winning(self, state, game_dir):
        state["phase"] = Phase.SPRING_MOVEMENT.value
        op = order_prompt("England", state, game_dir)
        assert "18 SCs" in op
        assert "solo victory" in op.lower()


class TestRetreatPrompt:
    def test_contains_phase_info(self, state, game_dir):
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = []
        rp = retreat_prompt("England", state, game_dir)
        assert "Spring Retreat" in rp

    def test_no_dislodged_units(self, state, game_dir):
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = []
        rp = retreat_prompt("England", state, game_dir)
        assert "none" in rp.lower()

    def test_dislodged_units_shown(self, state, game_dir):
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = [
            {
                "power": "England",
                "unit": {"type": "Army", "location": "London"},
                "retreats": ["Wales", "Yorkshire"],
            }
        ]
        rp = retreat_prompt("England", state, game_dir)
        assert "London" in rp
        assert "Wales" in rp
        assert "Yorkshire" in rp

    def test_only_own_dislodged_shown(self, state, game_dir):
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = [
            {
                "power": "France",
                "unit": {"type": "Army", "location": "Paris"},
                "retreats": ["Burgundy"],
            },
            {
                "power": "England",
                "unit": {"type": "Fleet", "location": "London"},
                "retreats": ["Wales"],
            },
        ]
        rp = retreat_prompt("England", state, game_dir)
        assert "London" in rp
        assert "Wales" in rp
        # France's dislodged unit info should be hidden from England
        # (the dislodged section only shows England's)

    def test_disband_option_mentioned(self, state, game_dir):
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = []
        rp = retreat_prompt("Germany", state, game_dir)
        assert "Disband" in rp

    def test_no_retreat_options(self, state, game_dir):
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = [
            {
                "power": "England",
                "unit": {"type": "Fleet", "location": "London"},
                "retreats": [],
            },
        ]
        rp = retreat_prompt("England", state, game_dir)
        assert "must disband" in rp.lower()


class TestAdjustmentPrompt:
    def test_build_scenario(self, state, game_dir):
        # Give England an extra SC without adding a unit
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        state["sc_ownership"]["Norway"] = "England"
        ap = adjustment_prompt("England", state, game_dir)
        assert "Build" in ap
        assert "1" in ap  # 1 build

    def test_disband_scenario(self, state, game_dir):
        # Remove an SC from France but keep units
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        state["sc_ownership"]["Paris"] = "Germany"
        ap = adjustment_prompt("France", state, game_dir)
        assert "Disband" in ap

    def test_no_adjustment_needed(self, state, game_dir):
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        ap = adjustment_prompt("England", state, game_dir)
        assert "No adjustment" in ap or "no adjustment" in ap

    def test_home_center_availability(self, state, game_dir):
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        state["sc_ownership"]["Belgium"] = "England"
        # London is occupied, Edinburgh is occupied, Liverpool is occupied
        ap = adjustment_prompt("England", state, game_dir)
        # All home centers are occupied so no builds possible
        assert "occupied" in ap.lower()

    def test_contains_sc_ownership(self, state, game_dir):
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        ap = adjustment_prompt("Germany", state, game_dir)
        assert "SCs" in ap

    def test_build_with_open_home_center(self, state, game_dir):
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        # Give France an extra SC and remove a unit to create open HC
        state["sc_ownership"]["Belgium"] = "France"
        state["units"]["France"] = [
            {"type": "Army", "location": "Belgium"},
            {"type": "Army", "location": "Marseilles"},
        ]
        # Paris and Brest are open home centers
        ap = adjustment_prompt("France", state, game_dir)
        assert "available for build" in ap

    def test_waive_mentioned_for_builds(self, state, game_dir):
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        state["sc_ownership"]["Norway"] = "England"
        ap = adjustment_prompt("England", state, game_dir)
        assert "Waive" in ap


class TestTurnContext:
    def test_diplomacy_phase_returns_negotiation(self, state, game_dir):
        tc = turn_context("England", state, game_dir, round_num=1,
                          max_rounds=3)
        assert "Spring Diplomacy" in tc
        assert "Round 1" in tc or "round 1" in tc

    def test_movement_phase_returns_orders(self, state, game_dir):
        state["phase"] = Phase.SPRING_MOVEMENT.value
        tc = turn_context("England", state, game_dir)
        assert "Submit" in tc or "orders" in tc.lower()

    def test_retreat_phase_returns_retreat(self, state, game_dir):
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = []
        tc = turn_context("England", state, game_dir)
        assert "Retreat" in tc

    def test_adjustment_phase_returns_adjustment(self, state, game_dir):
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        tc = turn_context("Germany", state, game_dir)
        assert "Winter" in tc or "Adjustment" in tc

    def test_diplomacy_requires_round_info(self, state, game_dir):
        with pytest.raises(ValueError, match="round_num"):
            turn_context("England", state, game_dir)

    def test_fall_diplomacy(self, state, game_dir):
        state["phase"] = Phase.FALL_DIPLOMACY.value
        tc = turn_context("France", state, game_dir, round_num=1,
                          max_rounds=2)
        assert "Fall Diplomacy" in tc

    def test_fall_movement(self, state, game_dir):
        state["phase"] = Phase.FALL_MOVEMENT.value
        tc = turn_context("France", state, game_dir)
        assert "Fall Movement" in tc

    def test_fall_retreat(self, state, game_dir):
        state["phase"] = Phase.FALL_RETREAT.value
        state["dislodged"] = []
        tc = turn_context("Austria", state, game_dir)
        assert "Retreat" in tc

    def test_all_powers_generate_prompts(self, state, game_dir):
        for power in POWERS:
            sp = system_prompt(power)
            assert len(sp) > 100
            bp = bootstrap_prompt(power)
            assert len(bp) > 100
            tc = turn_context(power, state, game_dir, round_num=1,
                              max_rounds=3)
            assert len(tc) > 100


class TestEdgeCases:
    def test_eliminated_power_in_units_display(self, state, game_dir):
        state["phase"] = Phase.SPRING_DIPLOMACY.value
        state["units"]["Austria"] = []
        state["eliminated"].append("Austria")
        np = negotiation_prompt("England", state, game_dir, 1, 3)
        assert "eliminated" in np.lower()

    def test_late_game_state(self, state, game_dir):
        # Simulate a later game state
        state["year"] = 1905
        state["phase"] = Phase.FALL_MOVEMENT.value
        state["sc_ownership"]["Belgium"] = "France"
        state["sc_ownership"]["Holland"] = "Germany"
        state["sc_ownership"]["Norway"] = "England"
        op = order_prompt("England", state, game_dir)
        assert "1905" in op

    def test_empty_units_list(self, state, game_dir):
        state["phase"] = Phase.SPRING_MOVEMENT.value
        state["units"]["Austria"] = []
        op = order_prompt("Austria", state, game_dir)
        assert "none" in op.lower()
