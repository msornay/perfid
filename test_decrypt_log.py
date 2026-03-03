"""Tests for decrypt_log.py — JSONL log decryption helper."""

import io
import json

import pytest

import gpg
from decrypt_log import decrypt_event, decrypt_log


@pytest.fixture
def gm_home(tmp_path):
    """GM keyring with generated key."""
    home = str(tmp_path / "gm-gpg")
    gpg.generate_key(home, "GM", "gm@perfid.local")
    return home


class TestDecryptEvent:
    def test_decrypt_agent_turn(self, gm_home):
        plaintext = "Strategy: attack from the south"
        ct = gpg.encrypt(gm_home, plaintext, "gm@perfid.local")
        event = {
            "event": "agent_turn",
            "power": "France",
            "encrypted_output": ct,
        }
        result = decrypt_event(event, gm_home)
        assert result["output"] == plaintext
        assert "encrypted_output" not in result

    def test_decrypt_message_routed(self, gm_home):
        plaintext = "Let's ally against Germany"
        ct = gpg.encrypt(gm_home, plaintext, "gm@perfid.local")
        event = {
            "event": "message_routed",
            "sender": "France",
            "recipient": "England",
            "encrypted": ct,
        }
        result = decrypt_event(event, gm_home)
        assert result["plaintext"] == plaintext
        assert "encrypted" not in result

    def test_passthrough_non_encrypted(self, gm_home):
        event = {
            "event": "phase_start",
            "year": 1901,
            "phase": "Spring",
        }
        result = decrypt_event(event, gm_home)
        assert result == event

    def test_passthrough_unknown_event(self, gm_home):
        event = {"event": "custom", "data": "hello"}
        result = decrypt_event(event, gm_home)
        assert result == event

    def test_decrypt_error_preserved(self, tmp_path, gm_home):
        """Wrong key produces a decrypt_error field."""
        other_home = str(tmp_path / "other")
        gpg.generate_key(other_home, "Other", "other@test.local")
        ct = gpg.encrypt(other_home, "secret", "other@test.local")
        event = {
            "event": "agent_turn",
            "power": "X",
            "encrypted_output": ct,
        }
        result = decrypt_event(event, gm_home)
        assert "decrypt_error" in result
        assert "encrypted_output" in result  # kept on failure


class TestDecryptLog:
    def test_full_roundtrip(self, gm_home, tmp_path):
        plaintext1 = "Austria strategy"
        ct1 = gpg.encrypt(gm_home, plaintext1, "gm@perfid.local")
        plaintext2 = "Propose alliance"
        ct2 = gpg.encrypt(gm_home, plaintext2, "gm@perfid.local")

        log_path = str(tmp_path / "game.jsonl")
        with open(log_path, "w") as f:
            f.write(json.dumps({
                "event": "phase_start",
                "year": 1901,
            }) + "\n")
            f.write(json.dumps({
                "event": "agent_turn",
                "power": "Austria",
                "encrypted_output": ct1,
            }) + "\n")
            f.write(json.dumps({
                "event": "message_routed",
                "sender": "France",
                "recipient": "England",
                "encrypted": ct2,
            }) + "\n")

        output = io.StringIO()
        count = decrypt_log(log_path, gm_home, output=output)

        assert count == 2

        lines = output.getvalue().strip().split("\n")
        assert len(lines) == 3

        e0 = json.loads(lines[0])
        assert e0["event"] == "phase_start"

        e1 = json.loads(lines[1])
        assert e1["event"] == "agent_turn"
        assert e1["output"] == plaintext1
        assert "encrypted_output" not in e1

        e2 = json.loads(lines[2])
        assert e2["event"] == "message_routed"
        assert e2["plaintext"] == plaintext2
        assert "encrypted" not in e2

    def test_handles_missing_gm_key(self, tmp_path):
        log_path = str(tmp_path / "game.jsonl")
        with open(log_path, "w") as f:
            f.write(json.dumps({
                "event": "agent_turn",
                "power": "X",
                "encrypted_output": "-----BEGIN PGP MESSAGE-----\nfoo",
            }) + "\n")

        gm_home = str(tmp_path / "no-such-dir")
        output = io.StringIO()
        # Should not raise — errors are captured per-event
        count = decrypt_log(log_path, gm_home, output=output)
        assert count == 0

        lines = output.getvalue().strip().split("\n")
        event = json.loads(lines[0])
        assert "decrypt_error" in event
