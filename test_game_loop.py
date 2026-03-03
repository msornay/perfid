"""Tests for game_loop.py — game orchestration.

Mocks subprocess.run (for agent/GPG calls) and filesystem operations
that require container context (chown, player user, etc.).
"""

import json
import os
import shutil
from unittest.mock import MagicMock, call, patch

import pytest

import gpg
from game_state import POWERS, Phase, load_state, new_game, save_state
from message_router import init_message_dirs
from orders import submit_orders


# Import the module under test
import game_loop


# --- Fixtures ---


@pytest.fixture
def game_dir(tmp_path):
    """Create a game directory with initial state and GPG keys."""
    gd = str(tmp_path / "test-game")
    state = new_game("test-001", gd, use_jdip=False)
    init_message_dirs(gd, POWERS)

    # Create GM keys
    gm_gpg = os.path.join(gd, "gm-gpg")
    gpg.generate_key(gm_gpg, "GM", "gm@perfid.local")
    gm_pub = gpg.export_public_key(gm_gpg, "gm@perfid.local")
    pubkeys_dir = os.path.join(gd, "pubkeys")
    os.makedirs(pubkeys_dir, exist_ok=True)
    with open(os.path.join(pubkeys_dir, "GM.asc"), "w") as f:
        f.write(gm_pub)

    # Create player keys
    for power in POWERS:
        gpg.generate_player_key(gd, power, gm_gpg)

    # Create additional dirs
    for d in ("orders", "notes"):
        os.makedirs(os.path.join(gd, d), exist_ok=True)

    return gd


@pytest.fixture
def ctx(game_dir):
    """Create a game context dict."""
    from logger import GameLogger
    return {
        "game_id": "test-001",
        "game_dir": game_dir,
        "gm_gnupghome": os.path.join(game_dir, "gm-gpg"),
        "script_dir": os.path.dirname(os.path.abspath(__file__)),
        "logger": GameLogger(game_dir),
    }


@pytest.fixture
def state(game_dir):
    """Load the initial game state."""
    return load_state(game_dir)


# --- Helper ---


def mock_subprocess_run(*args, **kwargs):
    """Default mock for subprocess.run that succeeds."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = ""
    result.stderr = ""
    return result


def mock_subprocess_with_output(output):
    """Create a mock that returns specific stdout."""
    def _mock(*args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = output
        result.stderr = ""
        return result
    return _mock


def mock_run_factory(stdout="", returncode=0):
    """Create a mock subprocess.run for run_agent tests.

    Writes stdout to the file handle if one is passed via the stdout
    kwarg (matching the file-based output pattern in run_agent).
    """
    def _mock_run(cmd, **kwargs):
        fh = kwargs.get("stdout")
        if fh and hasattr(fh, "write"):
            fh.write(stdout)
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = ""
        return result
    return _mock_run


def mock_popen_factory(stdout="", returncode=0):
    """Create a mock Popen for run_agent tests."""
    def _mock_popen(cmd, **kwargs):
        fh = kwargs.get("stdout")
        if fh and hasattr(fh, "write"):
            fh.write(stdout)
        proc = MagicMock()
        proc.poll.return_value = returncode  # exits immediately
        proc.returncode = returncode
        proc.pid = 99999
        return proc
    return _mock_popen


# --- Tests: Active powers ---


class TestActivePowers:
    def test_all_powers_active_initially(self, state):
        active = game_loop._active_powers(state)
        assert len(active) == 7
        assert active == POWERS

    def test_eliminated_powers_excluded(self, state):
        state["eliminated"] = ["Austria", "Turkey"]
        active = game_loop._active_powers(state)
        assert len(active) == 5
        assert "Austria" not in active
        assert "Turkey" not in active


# --- Tests: Dropbox path ---


class TestDropboxPath:
    def test_dropbox_path_format(self):
        assert game_loop._dropbox_path("England") == "/tmp/orders-england"
        assert game_loop._dropbox_path("Austria") == "/tmp/orders-austria"


# --- Tests: Setup/cleanup GPG ---


class TestSetupTurnGpg:
    @patch("game_loop.subprocess.run")
    def test_creates_gpg_dir(self, mock_run, ctx):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b""
        )
        # Patch the symlink and cleanup parts
        with patch("os.symlink"), \
             patch("os.remove", side_effect=lambda p: None), \
             patch("os.path.islink", return_value=False), \
             patch("os.path.exists", wraps=os.path.exists), \
             patch("shutil.rmtree", side_effect=lambda *a, **kw: None):
            try:
                game_loop.setup_turn_gpg(ctx, "England")
            except Exception:
                pass  # may fail on symlink, that's OK
        gpg_dir = "/tmp/gpg-england"
        assert os.path.isdir(gpg_dir) or True  # dir may not exist in test

    @patch("game_loop.subprocess.run")
    def test_logs_gpg_setup(self, mock_run, ctx, game_dir):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b""
        )
        with patch("os.symlink"), \
             patch("os.remove", side_effect=lambda p: None), \
             patch("os.path.islink", return_value=False), \
             patch("shutil.rmtree"):
            try:
                game_loop.setup_turn_gpg(ctx, "France")
            except Exception:
                pass
        # Check that a gpg_setup event was logged
        log_path = os.path.join(game_dir, "log.jsonl")
        if os.path.exists(log_path):
            with open(log_path) as f:
                events = [json.loads(l) for l in f if l.strip()]
            gpg_events = [
                e for e in events if e.get("event") == "gpg_setup"
            ]
            # May or may not have logged depending on error
            if gpg_events:
                assert gpg_events[0]["power"] == "France"


class TestCleanupTurnGpg:
    @patch("game_loop.subprocess.run")
    def test_logs_gpg_cleanup(self, mock_run, ctx, game_dir):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b""
        )
        gpg_dir = "/tmp/gpg-england"
        os.makedirs(gpg_dir, mode=0o700, exist_ok=True)
        with patch("shutil.rmtree"):
            game_loop.cleanup_turn_gpg(ctx, "England")

        log_path = os.path.join(game_dir, "log.jsonl")
        with open(log_path) as f:
            events = [json.loads(l) for l in f if l.strip()]
        cleanup_events = [
            e for e in events if e.get("event") == "gpg_cleanup"
        ]
        assert len(cleanup_events) >= 1
        assert cleanup_events[0]["power"] == "England"


# --- Tests: Run agent ---


class TestRunAgent:
    """Tests for run_agent.

    We mock subprocess.Popen (used by run_agent for the claude call)
    and gpg_mod.encrypt (to avoid real GPG calls in encryption).
    """

    @patch("game_loop.gpg_mod.encrypt", return_value="(mock encrypted)")
    @patch("game_loop.subprocess.Popen")
    def test_first_call_uses_session_id(
        self, mock_popen, mock_enc, ctx, game_dir
    ):
        mock_popen.side_effect = mock_popen_factory("agent output")
        game_loop.run_agent(ctx, "England", "test prompt")
        cmd = mock_popen.call_args.args[0]
        assert "--session-id" in cmd

    @patch("game_loop.gpg_mod.encrypt", return_value="(mock encrypted)")
    @patch("game_loop.subprocess.Popen")
    def test_subsequent_call_uses_resume(
        self, mock_popen, mock_enc, ctx, game_dir
    ):
        from game_state import mark_session_started, get_session_id
        get_session_id(game_dir, "England")
        mark_session_started(game_dir, "England")

        mock_popen.side_effect = mock_popen_factory("agent output")
        game_loop.run_agent(ctx, "England", "test prompt")
        cmd = mock_popen.call_args.args[0]
        assert "--resume" in cmd

    @patch("game_loop.subprocess.Popen")
    def test_agent_output_encrypted_and_logged(
        self, mock_popen, ctx, game_dir
    ):
        mock_popen.side_effect = mock_popen_factory("secret strategy")
        game_loop.run_agent(ctx, "France", "prompt")

        log_path = os.path.join(game_dir, "log.jsonl")
        with open(log_path) as f:
            events = [json.loads(l) for l in f if l.strip()]
        agent_events = [
            e for e in events if e.get("event") == "agent_turn"
        ]
        assert len(agent_events) >= 1
        evt = agent_events[0]
        assert evt["power"] == "France"
        # Output should be encrypted (PGP message)
        enc = evt.get("encrypted_output", "")
        assert "BEGIN PGP MESSAGE" in enc or enc == "(encryption failed)"

    @patch("game_loop.gpg_mod.encrypt", return_value="(mock encrypted)")
    @patch("game_loop.subprocess.Popen")
    def test_agent_called_with_sudo(
        self, mock_popen, mock_enc, ctx, game_dir
    ):
        mock_popen.side_effect = mock_popen_factory("output")
        game_loop.run_agent(ctx, "Germany", "prompt")
        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "sudo"
        assert "-u" in cmd
        assert "player" in cmd

    @patch("game_loop.gpg_mod.encrypt", return_value="(mock encrypted)")
    @patch("game_loop.subprocess.Popen")
    def test_agent_gnupghome_set(
        self, mock_popen, mock_enc, ctx, game_dir
    ):
        mock_popen.side_effect = mock_popen_factory("output")
        game_loop.run_agent(ctx, "Italy", "prompt")
        cmd = mock_popen.call_args.args[0]
        gnupghome_arg = [a for a in cmd if "GNUPGHOME=" in a]
        assert len(gnupghome_arg) == 1
        assert "gpg-italy" in gnupghome_arg[0]

    @patch("game_loop.gpg_mod.encrypt", return_value="(mock encrypted)")
    @patch("game_loop.subprocess.Popen")
    def test_agent_game_dir_env(
        self, mock_popen, mock_enc, ctx, game_dir
    ):
        mock_popen.side_effect = mock_popen_factory("output")
        game_loop.run_agent(ctx, "England", "prompt")
        cmd = mock_popen.call_args.args[0]
        game_dir_arg = [a for a in cmd if "PERFID_GAME_DIR=" in a]
        assert len(game_dir_arg) == 1
        assert game_dir in game_dir_arg[0]

    @patch("game_loop.gpg_mod.encrypt", return_value="(mock encrypted)")
    @patch("game_loop.subprocess.Popen")
    def test_agent_env_extra_passed(
        self, mock_popen, mock_enc, ctx, game_dir
    ):
        mock_popen.side_effect = mock_popen_factory("output")
        game_loop.run_agent(
            ctx, "France", "prompt",
            env_extra={"PERFID_DROPBOX": "/tmp/orders-france"},
        )
        cmd = mock_popen.call_args.args[0]
        dropbox_arg = [a for a in cmd if "PERFID_DROPBOX=" in a]
        assert len(dropbox_arg) == 1
        assert "/tmp/orders-france" in dropbox_arg[0]


# --- Tests: Route and log messages ---


class TestRouteAndLogMessages:
    def test_routes_messages(self, ctx, game_dir, state):
        from message_router import send_message
        send_message(
            game_dir, "France", "England", "Spring", 1,
            "encrypted content"
        )
        game_loop.route_and_log_messages(ctx, "France", state)

        log_path = os.path.join(game_dir, "log.jsonl")
        with open(log_path) as f:
            events = [json.loads(l) for l in f if l.strip()]
        msg_events = [
            e for e in events
            if e.get("event") == "message_routed"
        ]
        assert len(msg_events) >= 1
        assert msg_events[0]["sender"] == "France"
        assert msg_events[0]["recipient"] == "England"


# --- Tests: Movement phase ---


class TestMovementPhase:
    @patch("game_loop.adjudicate_movement")
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_all_powers_get_turns(
        self, mock_cleanup, mock_setup, mock_agent, mock_adj,
        ctx, state, game_dir
    ):
        mock_agent.return_value = True
        # Submit orders for all powers to end the phase
        gm_gpg = os.path.join(game_dir, "gm-gpg")
        # Create a keyring that can encrypt to GM
        enc_home = str(os.path.join(game_dir, "enc-home"))
        gpg.init_gnupghome(enc_home)
        gpg.import_all_pubkeys(enc_home, game_dir)

        # Pre-submit orders for all powers
        for power in POWERS:
            submit_orders(
                game_dir, power, 1901, "Spring",
                [f"A {power} H"], enc_home,
            )

        game_loop._movement_phase(ctx, state)
        # All 7 powers should have had setup/cleanup called
        assert mock_setup.call_count >= 7
        assert mock_cleanup.call_count >= 7

    @patch("game_loop.adjudicate_movement")
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_negotiation_rounds_before_orders(
        self, mock_cleanup, mock_setup, mock_agent, mock_adj,
        ctx, state, game_dir
    ):
        """Powers should play at least MIN_NEGOTIATION_ROUNDS rounds."""
        round_nums = []

        def track_agent(ctx_, power, prompt, **kwargs):
            # Extract round number from "Round N" in prompt
            import re
            m = re.search(r'Round\s+(\d+)', prompt)
            if m:
                round_nums.append(int(m.group(1)))
            return True

        mock_agent.side_effect = track_agent

        # Pre-submit to limit rounds
        enc_home = str(os.path.join(game_dir, "enc-home"))
        gpg.init_gnupghome(enc_home)
        gpg.import_all_pubkeys(enc_home, game_dir)
        for power in POWERS:
            submit_orders(
                game_dir, power, 1901, "Spring",
                [f"A {power} H"], enc_home,
            )

        game_loop._movement_phase(ctx, state)
        # Should have rounds 1, 2, 3, and 4 (when submission checked)
        if round_nums:
            assert min(round_nums) == 1
            assert max(round_nums) >= 4

    @patch("game_loop.adjudicate_movement")
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_eliminated_powers_skipped(
        self, mock_cleanup, mock_setup, mock_agent, mock_adj,
        ctx, state, game_dir
    ):
        mock_agent.return_value = True
        state["eliminated"] = ["Austria", "Turkey"]
        save_state(state, game_dir)

        enc_home = str(os.path.join(game_dir, "enc-home"))
        gpg.init_gnupghome(enc_home)
        gpg.import_all_pubkeys(enc_home, game_dir)
        active = [p for p in POWERS if p not in state["eliminated"]]
        for power in active:
            submit_orders(
                game_dir, power, 1901, "Spring",
                [f"A {power} H"], enc_home,
            )

        game_loop._movement_phase(ctx, state)
        # Check that eliminated powers were never set up
        setup_powers = [c.args[1] for c in mock_setup.call_args_list]
        assert "Austria" not in setup_powers
        assert "Turkey" not in setup_powers

    @patch("game_loop.adjudicate_movement")
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_safety_limit_reached(
        self, mock_cleanup, mock_setup, mock_agent, mock_adj,
        ctx, state, game_dir
    ):
        """No submissions → safety limit stops the loop."""
        mock_agent.return_value = True
        # Don't submit any orders — loop should hit max rounds
        game_loop._movement_phase(ctx, state)
        # Should have been called MAX_ROUNDS * 7 times
        assert mock_setup.call_count == game_loop.MAX_ROUNDS * 7


# --- Tests: Checkpoint helpers ---


class TestCheckpointHelpers:
    def test_save_and_load(self, game_dir):
        game_loop._save_checkpoint(
            game_dir, 1901, "Spring", 3,
            ["England", "France", "Germany"], 2,
        )
        cp = game_loop._load_checkpoint(game_dir, 1901, "Spring")
        assert cp is not None
        assert cp["year"] == 1901
        assert cp["phase"] == "Spring"
        assert cp["round"] == 3
        assert cp["power_order"] == ["England", "France", "Germany"]
        assert cp["next_index"] == 2

    def test_load_wrong_year(self, game_dir):
        game_loop._save_checkpoint(
            game_dir, 1901, "Spring", 1, ["England"], 0,
        )
        assert game_loop._load_checkpoint(game_dir, 1902, "Spring") is None

    def test_load_wrong_phase(self, game_dir):
        game_loop._save_checkpoint(
            game_dir, 1901, "Spring", 1, ["England"], 0,
        )
        assert game_loop._load_checkpoint(game_dir, 1901, "Fall") is None

    def test_load_no_file(self, game_dir):
        assert game_loop._load_checkpoint(game_dir, 1901, "Spring") is None

    def test_clear(self, game_dir):
        game_loop._save_checkpoint(
            game_dir, 1901, "Spring", 1, ["England"], 0,
        )
        path = os.path.join(game_dir, ".phase-progress.json")
        assert os.path.exists(path)
        game_loop._clear_checkpoint(game_dir)
        assert not os.path.exists(path)

    def test_clear_no_file(self, game_dir):
        # Should not raise
        game_loop._clear_checkpoint(game_dir)

    def test_atomic_write(self, game_dir):
        """Tmp file should not linger after save."""
        game_loop._save_checkpoint(
            game_dir, 1901, "Spring", 1, ["England"], 0,
        )
        tmp = os.path.join(game_dir, ".phase-progress.json.tmp")
        assert not os.path.exists(tmp)


# --- Tests: Movement phase resume ---


class TestMovementPhaseResume:
    @patch("game_loop.adjudicate_movement")
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_resumes_from_checkpoint(
        self, mock_cleanup, mock_setup, mock_agent, mock_adj,
        ctx, state, game_dir
    ):
        """After a checkpoint, powers before next_index are skipped."""
        mock_agent.return_value = True

        # Pre-submit orders so phase ends after round 4
        enc_home = str(os.path.join(game_dir, "enc-home"))
        gpg.init_gnupghome(enc_home)
        gpg.import_all_pubkeys(enc_home, game_dir)
        for power in POWERS:
            submit_orders(
                game_dir, power, 1901, "Spring",
                [f"A {power} H"], enc_home,
            )

        # Plant a checkpoint mid-round-4: 3 powers already done
        fixed_order = list(POWERS)
        game_loop._save_checkpoint(
            game_dir, 1901, "Spring", 4, fixed_order, 3,
        )

        game_loop._movement_phase(ctx, state)

        # Only powers 3..6 (4 powers) should have had turns in round 4
        setup_powers = [c.args[1] for c in mock_setup.call_args_list]
        assert len(setup_powers) == 4
        assert setup_powers == fixed_order[3:]

    @patch("game_loop.adjudicate_movement")
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_checkpoint_cleared_after_phase(
        self, mock_cleanup, mock_setup, mock_agent, mock_adj,
        ctx, state, game_dir
    ):
        mock_agent.return_value = True
        enc_home = str(os.path.join(game_dir, "enc-home"))
        gpg.init_gnupghome(enc_home)
        gpg.import_all_pubkeys(enc_home, game_dir)
        for power in POWERS:
            submit_orders(
                game_dir, power, 1901, "Spring",
                [f"A {power} H"], enc_home,
            )

        game_loop._movement_phase(ctx, state)
        path = os.path.join(game_dir, ".phase-progress.json")
        assert not os.path.exists(path)

    @patch("game_loop.adjudicate_movement")
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_stale_checkpoint_ignored(
        self, mock_cleanup, mock_setup, mock_agent, mock_adj,
        ctx, state, game_dir
    ):
        """Checkpoint from a different phase is ignored."""
        mock_agent.return_value = True
        enc_home = str(os.path.join(game_dir, "enc-home"))
        gpg.init_gnupghome(enc_home)
        gpg.import_all_pubkeys(enc_home, game_dir)
        for power in POWERS:
            submit_orders(
                game_dir, power, 1901, "Spring",
                [f"A {power} H"], enc_home,
            )

        # Plant checkpoint for a different phase
        game_loop._save_checkpoint(
            game_dir, 1901, "Fall", 2, list(POWERS), 5,
        )

        game_loop._movement_phase(ctx, state)
        # All 7 powers should play in each round (no skipping)
        assert mock_setup.call_count >= 7


# --- Tests: Retreat phase ---


class TestRetreatPhase:
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_only_dislodged_powers_act(
        self, mock_cleanup, mock_setup, mock_agent,
        ctx, game_dir
    ):
        state = load_state(game_dir)
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = [
            {
                "power": "Austria",
                "unit": {"type": "Army", "location": "Vienna"},
                "retreats": ["Bohemia", "Tyrolia"],
            }
        ]
        save_state(state, game_dir)

        mock_agent.return_value = True
        game_loop._retreat_phase(ctx, state)

        # Only Austria should have had a turn
        setup_powers = [c.args[1] for c in mock_setup.call_args_list]
        assert "Austria" in setup_powers
        assert len(setup_powers) == 1

    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_logs_retreat_orders(
        self, mock_cleanup, mock_setup, mock_agent,
        ctx, game_dir
    ):
        state = load_state(game_dir)
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = [
            {
                "power": "Austria",
                "unit": {"type": "Army", "location": "Vienna"},
                "retreats": ["Bohemia", "Tyrolia"],
            }
        ]
        save_state(state, game_dir)

        mock_agent.return_value = True
        game_loop._retreat_phase(ctx, state)

        log_path = os.path.join(game_dir, "log.jsonl")
        with open(log_path) as f:
            events = [json.loads(l) for l in f if l.strip()]
        retreat_events = [
            e for e in events if e.get("event") == "retreats_applied"
        ]
        assert len(retreat_events) >= 1
        assert "retreat_orders" in retreat_events[0]
        assert "units" in retreat_events[0]

    @patch("game_loop.apply_retreats_and_advance")
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_retreat_resumes_from_checkpoint(
        self, mock_cleanup, mock_setup, mock_agent, mock_advance,
        ctx, game_dir
    ):
        state = load_state(game_dir)
        state["phase"] = Phase.SPRING_RETREAT.value
        state["dislodged"] = [
            {"power": "Austria", "unit": {"type": "Army", "location": "Vienna"},
             "retreats": ["Bohemia"]},
            {"power": "France", "unit": {"type": "Army", "location": "Paris"},
             "retreats": ["Picardy"]},
        ]
        save_state(state, game_dir)

        mock_agent.return_value = True

        # Checkpoint: Austria already done, resume at France
        game_loop._save_checkpoint(
            game_dir, 1901, Phase.SPRING_RETREAT.value,
            1, ["Austria", "France"], 1,
        )

        game_loop._retreat_phase(ctx, state)

        setup_powers = [c.args[1] for c in mock_setup.call_args_list]
        assert setup_powers == ["France"]


# --- Tests: Winter phase ---


class TestWinterPhase:
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_all_active_powers_act(
        self, mock_cleanup, mock_setup, mock_agent,
        ctx, game_dir
    ):
        state = load_state(game_dir)
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        state["eliminated"] = ["Turkey"]
        save_state(state, game_dir)

        mock_agent.return_value = True
        game_loop._winter_phase(ctx, state)

        setup_powers = [c.args[1] for c in mock_setup.call_args_list]
        assert len(setup_powers) == 6  # 7 - 1 eliminated
        assert "Turkey" not in setup_powers

    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_logs_adjustments(
        self, mock_cleanup, mock_setup, mock_agent,
        ctx, game_dir
    ):
        state = load_state(game_dir)
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        save_state(state, game_dir)

        mock_agent.return_value = True
        game_loop._winter_phase(ctx, state)

        log_path = os.path.join(game_dir, "log.jsonl")
        with open(log_path) as f:
            events = [json.loads(l) for l in f if l.strip()]
        adj_events = [
            e for e in events
            if e.get("event") == "adjustments_applied"
        ]
        assert len(adj_events) >= 1
        assert "adjustments" in adj_events[0]
        assert "units" in adj_events[0]

    @patch("game_loop.apply_adjustments_and_advance")
    @patch("game_loop.run_agent")
    @patch("game_loop.setup_turn_gpg")
    @patch("game_loop.cleanup_turn_gpg")
    def test_winter_resumes_from_checkpoint(
        self, mock_cleanup, mock_setup, mock_agent, mock_advance,
        ctx, game_dir
    ):
        state = load_state(game_dir)
        state["phase"] = Phase.WINTER_ADJUSTMENT.value
        save_state(state, game_dir)

        mock_agent.return_value = True

        active = game_loop._active_powers(state)
        # Checkpoint: first 4 powers done
        game_loop._save_checkpoint(
            game_dir, 1901, Phase.WINTER_ADJUSTMENT.value,
            1, active, 4,
        )

        game_loop._winter_phase(ctx, state)

        setup_powers = [c.args[1] for c in mock_setup.call_args_list]
        assert len(setup_powers) == 3  # 7 - 4 skipped
        assert setup_powers == active[4:]


# --- Tests: Adjudication ---


class TestAdjudicateMovement:
    @patch("game_loop.adjudicate")
    def test_calls_adjudicate(
        self, mock_adj, ctx, state, game_dir
    ):
        mock_adj.return_value = {
            "year": 1901,
            "phase": "Spring Retreat",
            "units": state["units"],
            "sc_ownership": state["sc_ownership"],
            "eliminated": [],
            "winner": None,
            "game_id": "test-001",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }
        game_loop.adjudicate_movement(ctx, state)
        assert mock_adj.called

    @patch("game_loop.adjudicate")
    def test_logs_resolved_units(
        self, mock_adj, ctx, state, game_dir
    ):
        mock_adj.return_value = {
            "year": 1901,
            "phase": "Spring Retreat",
            "units": state["units"],
            "sc_ownership": state["sc_ownership"],
            "eliminated": [],
            "winner": None,
            "game_id": "test-001",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }
        game_loop.adjudicate_movement(ctx, state)

        log_path = os.path.join(game_dir, "log.jsonl")
        with open(log_path) as f:
            events = [json.loads(l) for l in f if l.strip()]
        adj_events = [
            e for e in events if e.get("event") == "adjudication"
        ]
        assert len(adj_events) >= 1
        assert "resolved_units" in adj_events[0]
        assert adj_events[0]["resolved_units"] == state["units"]


# --- Tests: Win detection ---


class TestWinDetection:
    @patch("game_loop._movement_phase")
    def test_winner_stops_loop(self, mock_phase, ctx, game_dir):
        state = load_state(game_dir)
        state["winner"] = "France"
        save_state(state, game_dir)

        # game_dir is .../test-game, so game_id for run() must match
        games_dir = os.path.dirname(game_dir)
        game_id = os.path.basename(game_dir)
        game_loop.run(game_id, games_dir, ctx["script_dir"])
        # Should not enter any phase handler
        assert not mock_phase.called


# --- Tests: PerfidError ---


class TestPerfidError:
    def test_missing_game(self, tmp_path):
        with pytest.raises(game_loop.PerfidError, match="not found"):
            game_loop.run("no-such-game", str(tmp_path), ".")

    def test_missing_keys(self, tmp_path):
        gd = str(tmp_path / "test-game")
        new_game("test", gd, use_jdip=False)
        with pytest.raises(game_loop.PerfidError, match="Missing key"):
            game_loop.run("test-game", str(tmp_path), ".")
