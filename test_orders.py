"""Tests for orders.py — Agent I/O: order submission, validation, notes."""

import json
import pytest

from game_state import Phase, new_game
import gpg as gpg_mod
from orders import (
    PROVINCES,
    collect_orders,
    decrypt_all_orders,
    decrypt_orders,
    default_orders,
    has_submitted,
    list_notes,
    parse_order,
    read_note,
    submit_orders,
    validate_order,
    validate_orders,
    write_note,
)


# --- Fixtures ---

@pytest.fixture
def game_dir(tmp_path):
    return tmp_path / "test-game"


@pytest.fixture
def state(game_dir):
    return new_game("test-001", game_dir)


@pytest.fixture
def spring_movement_state(state):
    """State in Spring Movement phase (orders accepted)."""
    state["phase"] = Phase.SPRING_MOVEMENT.value
    return state


@pytest.fixture
def fall_movement_state(state):
    """State in Fall Movement phase."""
    state["phase"] = Phase.FALL_MOVEMENT.value
    return state


@pytest.fixture
def spring_retreat_state(state):
    """State in Spring Retreat with a dislodged unit."""
    state["phase"] = Phase.SPRING_RETREAT.value
    state["dislodged"] = [
        {
            "power": "France",
            "unit": {"type": "Army", "location": "Paris"},
            "retreats": ["Burgundy", "Picardy"],
        }
    ]
    return state


@pytest.fixture
def winter_state(state):
    """State in Winter Adjustment phase."""
    state["phase"] = Phase.WINTER_ADJUSTMENT.value
    return state


@pytest.fixture
def gpg_setup(tmp_path):
    """Set up GM and one agent (France) GPG keyrings."""
    gm_home = str(tmp_path / "gm-gpg")
    agent_home = str(tmp_path / "france-gpg")

    # Generate keys
    gpg_mod.generate_key(gm_home, "GM", "gm@perfid.local")
    gpg_mod.generate_key(agent_home, "France", "france@perfid.local")

    # Exchange public keys
    gm_pub = gpg_mod.export_public_key(gm_home, "gm@perfid.local")
    agent_pub = gpg_mod.export_public_key(agent_home, "france@perfid.local")
    gpg_mod.import_and_trust(agent_home, gm_pub)
    gpg_mod.import_and_trust(gm_home, agent_pub)

    return {
        "gm_home": gm_home,
        "agent_home": agent_home,
        "gm_email": "gm@perfid.local",
        "agent_email": "france@perfid.local",
        "power": "France",
    }


# --- Order parsing tests ---

class TestParseOrder:
    def test_move(self):
        p = parse_order("A Paris - Burgundy")
        assert p["type"] == "move"
        assert p["unit_type"] == "A"
        assert p["location"] == "Paris"
        assert p["destination"] == "Burgundy"

    def test_fleet_move(self):
        p = parse_order("F Brest - Mid-Atlantic Ocean")
        assert p["type"] == "move"
        assert p["unit_type"] == "F"
        assert p["location"] == "Brest"
        assert p["destination"] == "Mid-Atlantic Ocean"

    def test_move_with_coast(self):
        p = parse_order("F St Petersburg (South Coast) - Gulf of Bothnia")
        assert p["type"] == "move"
        assert p["location"] == "St Petersburg"
        assert p["coast"] == "South Coast"
        assert p["destination"] == "Gulf of Bothnia"

    def test_move_to_coast(self):
        p = parse_order("F Mid-Atlantic Ocean - Spain (North Coast)")
        assert p["type"] == "move"
        assert p["destination"] == "Spain"
        assert p["dest_coast"] == "North Coast"

    def test_hold(self):
        p = parse_order("A Paris H")
        assert p["type"] == "hold"
        assert p["unit_type"] == "A"
        assert p["location"] == "Paris"

    def test_hold_long(self):
        p = parse_order("A Paris Hold")
        assert p["type"] == "hold"
        assert p["location"] == "Paris"

    def test_support_move(self):
        p = parse_order("A Munich S A Berlin - Silesia")
        assert p["type"] == "support_move"
        assert p["unit_type"] == "A"
        assert p["location"] == "Munich"
        assert p["supported_type"] == "A"
        assert p["supported_loc"] == "Berlin"
        assert p["destination"] == "Silesia"

    def test_support_move_with_keyword(self):
        p = parse_order("A Munich Support A Berlin - Silesia")
        assert p["type"] == "support_move"
        assert p["location"] == "Munich"

    def test_support_hold(self):
        p = parse_order("A Burgundy S A Paris H")
        assert p["type"] == "support_hold"
        assert p["location"] == "Burgundy"
        assert p["supported_type"] == "A"
        assert p["supported_loc"] == "Paris"

    def test_support_hold_implicit(self):
        # "S A Paris" without "H" should still parse as support hold
        p = parse_order("A Burgundy S A Paris")
        assert p["type"] == "support_hold"
        assert p["supported_loc"] == "Paris"

    def test_convoy(self):
        p = parse_order("F North Sea C A London - Belgium")
        assert p["type"] == "convoy"
        assert p["unit_type"] == "F"
        assert p["location"] == "North Sea"
        assert p["convoyed_type"] == "A"
        assert p["convoyed_loc"] == "London"
        assert p["destination"] == "Belgium"

    def test_convoy_full_keyword(self):
        p = parse_order("F North Sea Convoy A London - Belgium")
        assert p["type"] == "convoy"

    def test_retreat_disband(self):
        p = parse_order("A Munich Disband")
        assert p["type"] == "retreat_disband"
        assert p["location"] == "Munich"

    def test_retreat_disband_short(self):
        p = parse_order("A Munich D")
        assert p["type"] == "retreat_disband"

    def test_build_army(self):
        p = parse_order("Build A Paris")
        assert p["type"] == "build"
        assert p["unit_type"] == "A"
        assert p["location"] == "Paris"

    def test_build_fleet(self):
        p = parse_order("Build F Brest")
        assert p["type"] == "build"
        assert p["unit_type"] == "F"
        assert p["location"] == "Brest"

    def test_build_with_coast(self):
        p = parse_order("Build F St Petersburg (North Coast)")
        assert p["type"] == "build"
        assert p["location"] == "St Petersburg"
        assert p["coast"] == "North Coast"

    def test_winter_disband(self):
        p = parse_order("Disband A Paris")
        assert p["type"] == "disband"
        assert p["location"] == "Paris"

    def test_winter_remove(self):
        p = parse_order("Remove F Brest")
        assert p["type"] == "disband"
        assert p["location"] == "Brest"

    def test_waive(self):
        p = parse_order("Waive")
        assert p["type"] == "waive"

    def test_invalid_order(self):
        assert parse_order("nonsense gibberish") is None

    def test_empty_string(self):
        assert parse_order("") is None

    def test_em_dash_move(self):
        p = parse_order("A Paris — Burgundy")
        assert p["type"] == "move"
        assert p["destination"] == "Burgundy"

    def test_en_dash_move(self):
        p = parse_order("A Paris – Burgundy")
        assert p["type"] == "move"


# --- Validation tests ---

class TestValidateOrder:
    def test_valid_move(self, spring_movement_state):
        is_valid, parsed, error = validate_order(
            "A Paris - Burgundy", "France", spring_movement_state
        )
        assert is_valid
        assert parsed["type"] == "move"
        assert error is None

    def test_valid_hold(self, spring_movement_state):
        is_valid, _, _ = validate_order(
            "A Paris H", "France", spring_movement_state
        )
        assert is_valid

    def test_valid_fleet_move(self, spring_movement_state):
        is_valid, _, _ = validate_order(
            "F Brest - Mid-Atlantic Ocean", "France",
            spring_movement_state
        )
        assert is_valid

    def test_invalid_unit_not_owned(self, spring_movement_state):
        # France doesn't have an army in Berlin
        is_valid, _, error = validate_order(
            "A Berlin - Silesia", "France", spring_movement_state
        )
        assert not is_valid
        assert "no A at Berlin" in error

    def test_wrong_unit_type(self, spring_movement_state):
        # France has Army in Paris, not Fleet
        is_valid, _, error = validate_order(
            "F Paris - Picardy", "France", spring_movement_state
        )
        assert not is_valid
        assert "no F at Paris" in error

    def test_wrong_phase_build_in_movement(self, spring_movement_state):
        is_valid, _, error = validate_order(
            "Build A Paris", "France", spring_movement_state
        )
        assert not is_valid
        assert "not valid" in error

    def test_diplomacy_phase_rejects_orders(self, state):
        # state starts in Spring Diplomacy
        is_valid, _, error = validate_order(
            "A Paris - Burgundy", "France", state
        )
        assert not is_valid
        assert "Diplomacy" in error

    def test_retreat_phase_accepts_move(self, spring_retreat_state):
        is_valid, _, _ = validate_order(
            "A Paris - Burgundy", "France", spring_retreat_state
        )
        assert is_valid

    def test_retreat_phase_accepts_disband(self, spring_retreat_state):
        is_valid, _, _ = validate_order(
            "A Paris Disband", "France", spring_retreat_state
        )
        assert is_valid

    def test_retreat_must_be_dislodged(self, spring_retreat_state):
        # Army in Marseilles is NOT dislodged
        is_valid, _, error = validate_order(
            "A Marseilles - Spain", "France", spring_retreat_state
        )
        assert not is_valid
        assert "dislodged" in error

    def test_winter_build(self, winter_state):
        is_valid, _, _ = validate_order(
            "Build A Paris", "France", winter_state
        )
        assert is_valid

    def test_winter_disband(self, winter_state):
        is_valid, _, _ = validate_order(
            "Disband A Paris", "France", winter_state
        )
        assert is_valid

    def test_winter_waive(self, winter_state):
        is_valid, _, _ = validate_order(
            "Waive", "France", winter_state
        )
        assert is_valid

    def test_unparseable_order(self, spring_movement_state):
        is_valid, _, error = validate_order(
            "do something weird", "France", spring_movement_state
        )
        assert not is_valid
        assert "Cannot parse" in error


class TestValidateOrders:
    def test_all_valid(self, spring_movement_state):
        orders = [
            "A Paris - Burgundy",
            "A Marseilles - Spain",
            "F Brest - Mid-Atlantic Ocean",
        ]
        valid, errors = validate_orders(
            orders, "France", spring_movement_state
        )
        assert len(valid) == 3
        assert len(errors) == 0

    def test_mixed_valid_and_invalid(self, spring_movement_state):
        orders = [
            "A Paris - Burgundy",
            "A Berlin - Munich",  # France doesn't have A Berlin
            "F Brest - Mid-Atlantic Ocean",
        ]
        valid, errors = validate_orders(
            orders, "France", spring_movement_state
        )
        assert len(valid) == 2
        assert len(errors) == 1
        assert "Berlin" in errors[0][1]

    def test_all_invalid(self, spring_movement_state):
        orders = [
            "gibberish",
            "A Berlin - Munich",  # not France's unit
        ]
        valid, errors = validate_orders(
            orders, "France", spring_movement_state
        )
        assert len(valid) == 0
        assert len(errors) == 2


# --- Default orders tests ---

class TestDefaultOrders:
    def test_movement_defaults_to_hold(self, spring_movement_state):
        defaults = default_orders("France", spring_movement_state)
        assert len(defaults) == 3  # France has 3 units
        for order in defaults:
            assert order.endswith(" H")

    def test_retreat_defaults_to_disband(self, spring_retreat_state):
        defaults = default_orders("France", spring_retreat_state)
        assert len(defaults) == 1
        assert "Disband" in defaults[0]

    def test_winter_defaults_empty(self, winter_state):
        defaults = default_orders("France", winter_state)
        assert defaults == []

    def test_eliminated_power_no_defaults(self, spring_movement_state):
        spring_movement_state["eliminated"] = ["France"]
        spring_movement_state["units"]["France"] = []
        defaults = default_orders("France", spring_movement_state)
        assert defaults == []

    def test_hold_orders_reference_correct_units(self, spring_movement_state):
        defaults = default_orders("France", spring_movement_state)
        # France has: F Brest, A Paris, A Marseilles
        locs = {o.split(" ", 1)[1].replace(" H", "") for o in defaults}
        assert "Paris" in locs
        assert "Marseilles" in locs
        assert "Brest" in locs


# --- GPG-based order submission tests ---

class TestSubmitAndDecrypt:
    def test_submit_creates_gpg_file(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        path = submit_orders(
            game_dir, "France", 1901, "Spring Movement",
            ["A Paris - Burgundy", "F Brest - Mid-Atlantic Ocean"],
            gpg_setup["agent_home"],
        )
        assert path.exists()
        assert path.suffix == ".gpg"

    def test_decrypt_recovers_orders(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        orders = ["A Paris - Burgundy", "A Marseilles - Spain",
                   "F Brest - Mid-Atlantic Ocean"]
        submit_orders(
            game_dir, "France", 1901, "Spring Movement",
            orders, gpg_setup["agent_home"],
        )
        result = decrypt_orders(
            game_dir, "France", 1901, "Spring Movement",
            gpg_setup["gm_home"],
        )
        assert result is not None
        assert result["power"] == "France"
        assert result["orders"] == orders

    def test_decrypt_writes_json(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        submit_orders(
            game_dir, "France", 1901, "Spring Movement",
            ["A Paris H"], gpg_setup["agent_home"],
        )
        decrypt_orders(
            game_dir, "France", 1901, "Spring Movement",
            gpg_setup["gm_home"],
        )
        json_path = (
            game_dir / "orders" / "1901" / "Spring_Movement"
            / "France.json"
        )
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["power"] == "France"

    def test_decrypt_missing_returns_none(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        result = decrypt_orders(
            game_dir, "England", 1901, "Spring Movement",
            gpg_setup["gm_home"],
        )
        assert result is None

    def test_decrypt_all(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        submit_orders(
            game_dir, "France", 1901, "Spring Movement",
            ["A Paris H"], gpg_setup["agent_home"],
        )
        all_orders = decrypt_all_orders(
            game_dir, 1901, "Spring Movement",
            gpg_setup["gm_home"],
        )
        assert all_orders["France"] is not None
        assert all_orders["France"]["orders"] == ["A Paris H"]
        # Others should be None (not submitted)
        assert all_orders["England"] is None

    def test_has_submitted(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        assert not has_submitted(game_dir, "France", 1901, "Spring Movement")
        submit_orders(
            game_dir, "France", 1901, "Spring Movement",
            ["A Paris H"], gpg_setup["agent_home"],
        )
        assert has_submitted(game_dir, "France", 1901, "Spring Movement")


class TestCollectOrders:
    def test_submitted_orders_collected(self, game_dir, gpg_setup):
        state = new_game("test-001", game_dir)
        state["phase"] = Phase.SPRING_MOVEMENT.value
        submit_orders(
            game_dir, "France", 1901, "Spring Movement",
            ["A Paris - Burgundy", "A Marseilles - Spain",
             "F Brest - Mid-Atlantic Ocean"],
            gpg_setup["agent_home"],
        )
        result = collect_orders(
            game_dir, 1901, "Spring Movement",
            gpg_setup["gm_home"], state,
        )
        assert result["orders"]["France"] == [
            "A Paris - Burgundy",
            "A Marseilles - Spain",
            "F Brest - Mid-Atlantic Ocean",
        ]
        assert "France" not in result["defaults"]

    def test_missing_submission_gets_defaults(self, game_dir, gpg_setup):
        state = new_game("test-001", game_dir)
        state["phase"] = Phase.SPRING_MOVEMENT.value
        result = collect_orders(
            game_dir, 1901, "Spring Movement",
            gpg_setup["gm_home"], state,
        )
        # France didn't submit — should get defaults
        assert "France" in result["defaults"]
        france_orders = result["orders"]["France"]
        assert len(france_orders) == 3
        for o in france_orders:
            assert o.endswith(" H")

    def test_eliminated_power_gets_empty(self, game_dir, gpg_setup):
        state = new_game("test-001", game_dir)
        state["phase"] = Phase.SPRING_MOVEMENT.value
        state["eliminated"] = ["Turkey"]
        state["units"]["Turkey"] = []
        result = collect_orders(
            game_dir, 1901, "Spring Movement",
            gpg_setup["gm_home"], state,
        )
        assert result["orders"]["Turkey"] == []

    def test_invalid_orders_produce_errors(self, game_dir, gpg_setup):
        state = new_game("test-001", game_dir)
        state["phase"] = Phase.SPRING_MOVEMENT.value
        # Submit partially invalid orders
        submit_orders(
            game_dir, "France", 1901, "Spring Movement",
            ["A Paris - Burgundy", "A Berlin - Munich"],  # Berlin not French
            gpg_setup["agent_home"],
        )
        result = collect_orders(
            game_dir, 1901, "Spring Movement",
            gpg_setup["gm_home"], state,
        )
        assert "France" in result["errors"]
        # Valid order still used
        assert "A Paris - Burgundy" in result["orders"]["France"]

    def test_all_invalid_falls_to_defaults(self, game_dir, gpg_setup):
        state = new_game("test-001", game_dir)
        state["phase"] = Phase.SPRING_MOVEMENT.value
        submit_orders(
            game_dir, "France", 1901, "Spring Movement",
            ["gibberish", "more nonsense"],
            gpg_setup["agent_home"],
        )
        result = collect_orders(
            game_dir, 1901, "Spring Movement",
            gpg_setup["gm_home"], state,
        )
        assert "France" in result["defaults"]


# --- Private notes tests ---

class TestPrivateNotes:
    def test_write_and_read_note(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        note_text = "I plan to ally with England against Germany."
        path = write_note(
            game_dir, "France", 1901, "Spring Diplomacy",
            note_text, gpg_setup["agent_home"],
        )
        assert path.exists()

        recovered = read_note(
            game_dir, "France", 1901, "Spring Diplomacy",
            gpg_setup["agent_home"],
        )
        assert recovered == note_text

    def test_read_missing_note(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        result = read_note(
            game_dir, "France", 1901, "Spring Diplomacy",
            gpg_setup["agent_home"],
        )
        assert result is None

    def test_list_notes(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        write_note(
            game_dir, "France", 1901, "Spring Diplomacy",
            "Note 1", gpg_setup["agent_home"],
        )
        write_note(
            game_dir, "France", 1901, "Fall Diplomacy",
            "Note 2", gpg_setup["agent_home"],
        )
        notes = list_notes(game_dir, "France")
        assert len(notes) == 2
        assert notes[0]["year"] == 1901
        assert notes[0]["phase"] == "Spring Diplomacy"
        assert notes[1]["phase"] == "Fall Diplomacy"

    def test_note_only_readable_by_author(self, game_dir, gpg_setup, tmp_path):
        """A note encrypted with France's key cannot be read by GM."""
        new_game("test-001", game_dir)
        write_note(
            game_dir, "France", 1901, "Spring Diplomacy",
            "secret thoughts", gpg_setup["agent_home"],
        )
        # Try reading with GM's keyring — should fail
        import subprocess
        with pytest.raises(subprocess.CalledProcessError):
            read_note(
                game_dir, "France", 1901, "Spring Diplomacy",
                gpg_setup["gm_home"],
            )

    def test_multiline_note(self, game_dir, gpg_setup):
        new_game("test-001", game_dir)
        note_text = (
            "Strategic analysis:\n"
            "- England seems aggressive\n"
            "- Germany may be a potential ally\n"
            "- Consider DMZ in Burgundy\n"
        )
        write_note(
            game_dir, "France", 1901, "Spring Diplomacy",
            note_text, gpg_setup["agent_home"],
        )
        recovered = read_note(
            game_dir, "France", 1901, "Spring Diplomacy",
            gpg_setup["agent_home"],
        )
        assert recovered == note_text


# --- Province data tests ---

class TestProvinces:
    def test_major_provinces_present(self):
        for prov in ["Paris", "London", "Berlin", "Vienna", "Rome",
                     "Moscow", "Constantinople"]:
            assert prov in PROVINCES

    def test_sea_provinces_present(self):
        for sea in ["North Sea", "English Channel", "Mediterranean"
                    if False else "Western Mediterranean"]:
            assert sea in PROVINCES

    def test_supply_centers_in_provinces(self):
        from game_state import ALL_SUPPLY_CENTERS
        for sc in ALL_SUPPLY_CENTERS:
            assert sc in PROVINCES, f"SC {sc} not in PROVINCES"
