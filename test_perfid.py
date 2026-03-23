#!/usr/bin/env python3
"""Tests for the perfid CLI script.

Tests argument parsing, game-id validation, new/status commands, and
directory structure. Does NOT require Docker — tests the Python CLI
directly.
"""

import json
import os
import subprocess
import sys

import pytest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PERFID = os.path.join(SCRIPT_DIR, "perfid")

# Ensure module path
sys.path.insert(0, SCRIPT_DIR)


def run_perfid(*args, env_override=None):
    """Run perfid and return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        [sys.executable, PERFID] + list(args),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


# --- Argument parsing ---


class TestNoArgs:
    def test_exits_nonzero(self):
        rc, _, _ = run_perfid()
        assert rc != 0

    def test_shows_usage(self):
        _, out, err = run_perfid()
        assert "usage" in (out + err).lower()


class TestUnknownCommand:
    def test_exits_nonzero(self):
        rc, _, _ = run_perfid("badcmd")
        assert rc != 0


class TestMissingGameId:
    @pytest.mark.parametrize("cmd", ["new", "play", "status"])
    def test_exits_nonzero(self, cmd):
        rc, _, _ = run_perfid(cmd)
        assert rc != 0

    @pytest.mark.parametrize("cmd", ["new", "play", "status"])
    def test_shows_error(self, cmd):
        _, out, err = run_perfid(cmd)
        combined = out + err
        assert "error" in combined.lower() or "required" in combined.lower()


class TestInvalidGameId:
    def test_spaces_rejected(self, tmp_path):
        rc, _, err = run_perfid(
            "new", "game with spaces",
            env_override={"PERFID_GAMES_DIR": str(tmp_path / "g")},
        )
        assert rc != 0
        assert "invalid game-id" in err.lower()

    def test_slash_rejected(self, tmp_path):
        rc, _, err = run_perfid(
            "new", "game/bad",
            env_override={"PERFID_GAMES_DIR": str(tmp_path / "g")},
        )
        assert rc != 0

    def test_leading_dot_rejected(self, tmp_path):
        rc, _, err = run_perfid(
            "new", ".hidden",
            env_override={"PERFID_GAMES_DIR": str(tmp_path / "g")},
        )
        assert rc != 0

    def test_valid_ids_accepted(self):
        """Valid game-ids should not be rejected by validation."""
        import re
        pattern = r'^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$'
        for gid in ["test-001", "game.v2", "my_game", "ABC123"]:
            assert re.match(pattern, gid), f"{gid} should be valid"


class TestUsageListsCommands:
    def test_lists_new(self):
        _, out, err = run_perfid()
        assert "new" in out + err

    def test_lists_play(self):
        _, out, err = run_perfid()
        assert "play" in out + err

    def test_lists_status(self):
        _, out, err = run_perfid()
        assert "status" in out + err


# --- New command ---


class TestNewCommand:
    def test_creates_state_json(self, tmp_path):
        games_dir = str(tmp_path / "games")
        rc, out, err = run_perfid(
            "new", "test-001",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        assert rc == 0, f"stderr: {err}"
        assert os.path.isfile(
            os.path.join(games_dir, "test-001", "state.json")
        )

    def test_creates_message_dirs(self, tmp_path):
        games_dir = str(tmp_path / "games")
        run_perfid(
            "new", "test-001",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        gd = os.path.join(games_dir, "test-001")
        assert os.path.isdir(os.path.join(gd, "messages", "inbox"))
        assert os.path.isdir(os.path.join(gd, "messages", "outbox"))

    def test_creates_pubkeys_dir(self, tmp_path):
        games_dir = str(tmp_path / "games")
        run_perfid(
            "new", "test-001",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        gd = os.path.join(games_dir, "test-001")
        assert os.path.isdir(os.path.join(gd, "pubkeys"))

    def test_creates_orders_dir(self, tmp_path):
        games_dir = str(tmp_path / "games")
        run_perfid(
            "new", "test-001",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        gd = os.path.join(games_dir, "test-001")
        assert os.path.isdir(os.path.join(gd, "orders"))

    def test_creates_notes_dir(self, tmp_path):
        games_dir = str(tmp_path / "games")
        run_perfid(
            "new", "test-001",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        gd = os.path.join(games_dir, "test-001")
        assert os.path.isdir(os.path.join(gd, "notes"))

    def test_generates_gm_key(self, tmp_path):
        games_dir = str(tmp_path / "games")
        run_perfid(
            "new", "test-001",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        gd = os.path.join(games_dir, "test-001")
        assert os.path.isdir(os.path.join(gd, "gm-gpg"))
        assert os.path.isfile(os.path.join(gd, "pubkeys", "GM.asc"))

    def test_generates_player_keys(self, tmp_path):
        games_dir = str(tmp_path / "games")
        run_perfid(
            "new", "test-001",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        gd = os.path.join(games_dir, "test-001")
        for power in [
            "Austria", "England", "France", "Germany",
            "Italy", "Russia", "Turkey",
        ]:
            assert os.path.isfile(
                os.path.join(gd, "keys", f"{power}.key.gpg")
            ), f"Missing key for {power}"
            assert os.path.isfile(
                os.path.join(gd, "pubkeys", f"{power}.asc")
            ), f"Missing pubkey for {power}"

    def test_rejects_duplicate_game(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        run_perfid("new", "test-001", env_override=env)
        rc, _, err = run_perfid("new", "test-001", env_override=env)
        assert rc != 0
        assert "already exists" in err

    def test_initial_state_correct(self, tmp_path):
        games_dir = str(tmp_path / "games")
        run_perfid(
            "new", "test-001",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        state_path = os.path.join(
            games_dir, "test-001", "state.json"
        )
        with open(state_path) as f:
            state = json.load(f)
        assert state["year"] == 1901
        assert state["phase"] == "Spring"
        assert state["winner"] is None
        assert state["eliminated"] == []
        assert len(state["units"]) == 7

    def test_new_default_profile_is_minimal(self, tmp_path):
        """profiles.json defaults to minimal for all powers."""
        games_dir = str(tmp_path / "g")
        rc, out, err = run_perfid(
            "new", "t1",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        assert rc == 0, f"stderr: {err}"
        prof_path = os.path.join(
            games_dir, "t1", "profiles.json"
        )
        with open(prof_path) as f:
            profiles = json.load(f)
        for power in [
            "Austria", "England", "France", "Germany",
            "Italy", "Russia", "Turkey",
        ]:
            assert profiles[power] == "minimal"

    def test_new_with_profile_flag(self, tmp_path):
        """--profile informed sets all powers to informed."""
        games_dir = str(tmp_path / "g")
        rc, out, err = run_perfid(
            "new", "t2", "--profile", "informed",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        assert rc == 0, f"stderr: {err}"
        prof_path = os.path.join(
            games_dir, "t2", "profiles.json"
        )
        with open(prof_path) as f:
            profiles = json.load(f)
        for power in [
            "Austria", "England", "France", "Germany",
            "Italy", "Russia", "Turkey",
        ]:
            assert profiles[power] == "informed"

    def test_emits_game_created_to_stdout(self, tmp_path):
        games_dir = str(tmp_path / "games")
        rc, out, err = run_perfid(
            "new", "test-001",
            env_override={"PERFID_GAMES_DIR": games_dir},
        )
        assert rc == 0, f"stderr: {err}"
        # game_created event should be in stdout JSONL
        found = False
        for line in out.splitlines():
            try:
                e = json.loads(line)
                if e.get("event") == "game_created":
                    found = True
                    break
            except json.JSONDecodeError:
                pass
        assert found, "game_created event not found in stdout"


# --- Status command ---


class TestStatusCommand:
    def test_shows_game_id(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        run_perfid("new", "test-001", env_override=env)
        rc, out, _ = run_perfid("status", "test-001", env_override=env)
        assert rc == 0
        assert "test-001" in out

    def test_shows_year(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        run_perfid("new", "test-001", env_override=env)
        _, out, _ = run_perfid("status", "test-001", env_override=env)
        assert "1901" in out

    def test_shows_phase(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        run_perfid("new", "test-001", env_override=env)
        _, out, _ = run_perfid("status", "test-001", env_override=env)
        assert "Spring" in out

    @pytest.mark.parametrize("power", [
        "Austria", "England", "France", "Germany",
        "Italy", "Russia", "Turkey",
    ])
    def test_shows_power(self, power, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        run_perfid("new", "test-001", env_override=env)
        _, out, _ = run_perfid("status", "test-001", env_override=env)
        assert power in out

    def test_shows_total_scs(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        run_perfid("new", "test-001", env_override=env)
        _, out, _ = run_perfid("status", "test-001", env_override=env)
        assert "34" in out

    def test_nonexistent_game(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        rc, _, err = run_perfid(
            "status", "no-such-game", env_override=env,
        )
        assert rc != 0
        assert "not found" in err


# --- Script structure checks ---


class TestScriptStructure:
    def test_is_python(self):
        with open(PERFID) as f:
            first_line = f.readline()
        assert "python" in first_line

    def test_uses_game_state(self):
        with open(PERFID) as f:
            content = f.read()
        assert "game_state" in content

    def test_uses_game_loop(self):
        with open(PERFID) as f:
            content = f.read()
        assert "game_loop" in content

    def test_uses_gpg(self):
        with open(PERFID) as f:
            content = f.read()
        assert "gpg" in content

    def test_uses_argparse(self):
        with open(PERFID) as f:
            content = f.read()
        assert "argparse" in content

    def test_defines_perfid_error(self):
        with open(PERFID) as f:
            content = f.read()
        assert "PerfidError" in content


# --- Game-id validation ---


class TestGameIdValidation:
    def test_alphanumeric(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        rc, _, _ = run_perfid("new", "abc123", env_override=env)
        assert rc == 0

    def test_hyphens(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        rc, _, _ = run_perfid("new", "my-game", env_override=env)
        assert rc == 0

    def test_underscores(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        rc, _, _ = run_perfid("new", "my_game", env_override=env)
        assert rc == 0

    def test_dots(self, tmp_path):
        games_dir = str(tmp_path / "games")
        env = {"PERFID_GAMES_DIR": games_dir}
        rc, _, _ = run_perfid("new", "game.v2", env_override=env)
        assert rc == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
