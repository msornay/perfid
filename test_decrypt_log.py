"""Tests for decrypt_log.py — JSONL log decryption helper."""

import io
import json
import os

import pytest

import gpg
from decrypt_log import decrypt_event, decrypt_log


@pytest.fixture
def gm_home(tmp_path):
    """GM keyring with generated key."""
    home = str(tmp_path / "gm-gpg")
    gpg.generate_key(home, "GM", "gm@perfid.local")
    return home


@pytest.fixture
def keys_dir(tmp_path, gm_home):
    """Keys directory with GM-encrypted recipient private keys."""
    kdir = str(tmp_path / "keys")
    os.makedirs(kdir)
    return kdir


def _make_recipient_key(tmp_path, gm_home, keys_dir, power):
    """Generate a recipient key and store GM-encrypted private key."""
    email = f"{power.lower()}@perfid.local"
    tmp_keyring = str(tmp_path / f"tmp-{power.lower()}")
    gpg.generate_key(tmp_keyring, power, email)

    # Export public key for encrypting test messages
    pub = gpg.export_public_key(tmp_keyring, email)

    # Export private key and encrypt with GM key
    from subprocess import run
    result = run(
        ["gpg", "--batch", "--yes", "--homedir", tmp_keyring,
         "--armor", "--export-secret-keys", email],
        capture_output=True, check=True,
    )
    private_key = result.stdout

    enc_path = os.path.join(keys_dir, f"{power}.key.gpg")
    gpg.gpg_mod = gpg  # self-reference sanity
    run(
        ["gpg", "--batch", "--yes", "--homedir", gm_home,
         "--armor", "--encrypt", "--trust-model", "always",
         "--recipient", "gm@perfid.local",
         "--output", enc_path],
        input=private_key, check=True, capture_output=True,
    )

    return tmp_keyring, pub


class TestDecryptEvent:
    def test_decrypt_agent_turn(self, gm_home, keys_dir):
        plaintext = "Strategy: attack from the south"
        ct = gpg.encrypt(gm_home, plaintext, "gm@perfid.local")
        event = {
            "event": "agent_turn",
            "power": "France",
            "encrypted_output": ct,
        }
        result = decrypt_event(event, gm_home, keys_dir, {})
        assert result["output"] == plaintext
        assert "encrypted_output" not in result

    def test_decrypt_message_routed(self, tmp_path, gm_home, keys_dir):
        """Messages encrypted to recipient are decrypted via keys dir."""
        eng_keyring, eng_pub = _make_recipient_key(
            tmp_path, gm_home, keys_dir, "England",
        )
        # Import England's public key into a sender keyring to encrypt
        sender_keyring = str(tmp_path / "sender")
        gpg.generate_key(sender_keyring, "France", "france@perfid.local")
        gpg.import_and_trust(sender_keyring, eng_pub)

        plaintext = "Let's ally against Germany"
        ct = gpg.encrypt(
            sender_keyring, plaintext, "england@perfid.local",
        )
        event = {
            "event": "message_routed",
            "sender": "France",
            "recipient": "England",
            "encrypted": ct,
        }
        cache = {}
        result = decrypt_event(event, gm_home, keys_dir, cache)
        assert result["plaintext"] == plaintext
        assert "encrypted" not in result
        assert "England" in cache  # key was cached

    def test_passthrough_non_encrypted(self, gm_home, keys_dir):
        event = {
            "event": "phase_start",
            "year": 1901,
            "phase": "Spring",
        }
        result = decrypt_event(event, gm_home, keys_dir, {})
        assert result == event

    def test_passthrough_unknown_event(self, gm_home, keys_dir):
        event = {"event": "custom", "data": "hello"}
        result = decrypt_event(event, gm_home, keys_dir, {})
        assert result == event

    def test_decrypt_error_preserved(self, tmp_path, gm_home, keys_dir):
        """Wrong key produces a decrypt_error field."""
        other_home = str(tmp_path / "other")
        gpg.generate_key(other_home, "Other", "other@test.local")
        ct = gpg.encrypt(other_home, "secret", "other@test.local")
        event = {
            "event": "agent_turn",
            "power": "X",
            "encrypted_output": ct,
        }
        result = decrypt_event(event, gm_home, keys_dir, {})
        assert "decrypt_error" in result
        assert "encrypted_output" in result  # kept on failure

    def test_message_no_key_available(self, gm_home, keys_dir):
        """Missing recipient key produces a decrypt_error."""
        event = {
            "event": "message_routed",
            "sender": "France",
            "recipient": "NoSuchPower",
            "encrypted": "-----BEGIN PGP MESSAGE-----\nfoo",
        }
        result = decrypt_event(event, gm_home, keys_dir, {})
        assert "decrypt_error" in result
        assert "encrypted" in result  # kept on failure


    def test_decrypt_simulation_run(self, gm_home, keys_dir):
        """simulation_run events: encrypted_data → simulation."""
        sim_data = json.dumps({
            "orders": {"France": ["A Paris - Burgundy"]},
            "order_results": [{"order": "France: A Paris - Burgundy",
                               "result": "success"}],
            "summary": {"France": {"units_before": 3, "units_after": 3}},
        })
        ct = gpg.encrypt(gm_home, sim_data, "gm@perfid.local")
        event = {
            "event": "simulation_run",
            "power": "France",
            "phase": "Spring",
            "year": 1901,
            "encrypted_data": ct,
        }
        result = decrypt_event(event, gm_home, keys_dir, {})
        assert "simulation" in result
        assert "encrypted_data" not in result
        assert result["simulation"]["orders"]["France"] == [
            "A Paris - Burgundy"
        ]

    def test_simulation_run_bad_key(self, tmp_path, gm_home, keys_dir):
        """Wrong key on simulation_run produces decrypt_error."""
        other = str(tmp_path / "other")
        gpg.generate_key(other, "Other", "other@test.local")
        ct = gpg.encrypt(other, '{"orders":{}}', "other@test.local")
        event = {
            "event": "simulation_run",
            "power": "X",
            "encrypted_data": ct,
        }
        result = decrypt_event(event, gm_home, keys_dir, {})
        assert "decrypt_error" in result
        assert "encrypted_data" in result


class TestDecryptLog:
    def test_full_roundtrip(self, tmp_path, gm_home, keys_dir):
        plaintext1 = "Austria strategy"
        ct1 = gpg.encrypt(gm_home, plaintext1, "gm@perfid.local")

        # Create England key for message decryption
        eng_keyring, eng_pub = _make_recipient_key(
            tmp_path, gm_home, keys_dir, "England",
        )
        sender_keyring = str(tmp_path / "sender")
        gpg.generate_key(sender_keyring, "France", "france@perfid.local")
        gpg.import_and_trust(sender_keyring, eng_pub)

        plaintext2 = "Propose alliance"
        ct2 = gpg.encrypt(
            sender_keyring, plaintext2, "england@perfid.local",
        )

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
        count = decrypt_log(log_path, gm_home, keys_dir, output=output)

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

    def test_simulation_run_decrypted(self, tmp_path, gm_home, keys_dir):
        """simulation_run events are counted in decrypt_log."""
        sim_data = json.dumps({"orders": {}, "summary": {}})
        ct = gpg.encrypt(gm_home, sim_data, "gm@perfid.local")

        log_path = str(tmp_path / "game.jsonl")
        with open(log_path, "w") as f:
            f.write(json.dumps({
                "event": "simulation_run",
                "power": "France",
                "phase": "Spring",
                "year": 1901,
                "encrypted_data": ct,
            }) + "\n")

        output = io.StringIO()
        count = decrypt_log(log_path, gm_home, keys_dir, output=output)
        assert count == 1

        event = json.loads(output.getvalue().strip())
        assert "simulation" in event
        assert "encrypted_data" not in event

    def test_handles_missing_gm_key(self, tmp_path):
        log_path = str(tmp_path / "game.jsonl")
        with open(log_path, "w") as f:
            f.write(json.dumps({
                "event": "agent_turn",
                "power": "X",
                "encrypted_output": "-----BEGIN PGP MESSAGE-----\nfoo",
            }) + "\n")

        gm_home = str(tmp_path / "no-such-dir")
        keys_dir = str(tmp_path / "no-keys")
        output = io.StringIO()
        # Should not raise — errors are captured per-event
        count = decrypt_log(log_path, gm_home, keys_dir, output=output)
        assert count == 0

        lines = output.getvalue().strip().split("\n")
        event = json.loads(lines[0])
        assert "decrypt_error" in event
