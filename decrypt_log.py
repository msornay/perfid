"""Decrypt a perfid JSONL log using the GM key.

Usage: python3 decrypt_log.py --gm-key gm-gpg/ game.jsonl > decrypted.jsonl

Decrypts 'encrypted_output' fields in agent_turn events (GM-encrypted)
and 'encrypted' fields in message_routed events (recipient-encrypted),
replacing them with plaintext. Non-encrypted events pass through unchanged.

Agent output is encrypted to the GM key directly. Messages between agents
are encrypted to the recipient's key, so we decrypt each recipient's
private key (stored in keys/*.key.gpg, GM-encrypted) to read them.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

import gpg as gpg_mod


def _get_recipient_gnupghome(gm_gnupghome, keys_dir, recipient, cache):
    """Get a temp GNUPGHOME with the recipient's private key.

    Caches keyrings so we only decrypt each key once.

    Args:
        gm_gnupghome: Path to the GM's GPG home directory.
        keys_dir: Path to the keys/ directory in the game dir.
        recipient: Recipient power name.
        cache: Dict mapping recipient → tmpdir path.

    Returns:
        Path to the temp GNUPGHOME, or None if the key is unavailable.
    """
    if recipient in cache:
        return cache[recipient]

    key_file = os.path.join(keys_dir, f"{recipient}.key.gpg")
    if not os.path.exists(key_file):
        cache[recipient] = None
        return None

    tmpdir = tempfile.mkdtemp(prefix=f"perfid-dec-{recipient}-")
    try:
        os.chmod(tmpdir, 0o700)
        private_key = gpg_mod.decrypt_file(gm_gnupghome, key_file)
        gpg_mod.import_key(tmpdir, private_key)
        cache[recipient] = tmpdir
        return tmpdir
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        cache[recipient] = None
        return None


def decrypt_event(event, gm_gnupghome, keys_dir, key_cache):
    """Decrypt encrypted fields in a single event dict.

    Modifies the event in place:
    - agent_turn: decrypts 'encrypted_output' → 'output' (GM key)
    - message_routed: decrypts 'encrypted' → 'plaintext' (recipient key)

    Returns the modified event.
    """
    event_type = event.get("event", "")

    if event_type == "agent_turn" and "encrypted_output" in event:
        ciphertext = event["encrypted_output"]
        if ciphertext.startswith("-----BEGIN PGP MESSAGE-----"):
            try:
                plaintext = gpg_mod.decrypt(gm_gnupghome, ciphertext)
                event["output"] = plaintext
                del event["encrypted_output"]
            except Exception as e:
                event["decrypt_error"] = str(e)

    elif event_type == "simulation_run" and "encrypted_data" in event:
        ciphertext = event["encrypted_data"]
        if ciphertext.startswith("-----BEGIN PGP MESSAGE-----"):
            try:
                plaintext = gpg_mod.decrypt(gm_gnupghome, ciphertext)
                event["simulation"] = json.loads(plaintext)
                del event["encrypted_data"]
            except Exception as e:
                event["decrypt_error"] = str(e)

    elif event_type == "message_routed" and "encrypted" in event:
        ciphertext = event["encrypted"]
        if ciphertext.startswith("-----BEGIN PGP MESSAGE-----"):
            recipient = event.get("recipient", "")
            gnupghome = _get_recipient_gnupghome(
                gm_gnupghome, keys_dir, recipient, key_cache,
            )
            if gnupghome:
                try:
                    plaintext = gpg_mod.decrypt(gnupghome, ciphertext)
                    event["plaintext"] = plaintext
                    del event["encrypted"]
                except Exception as e:
                    event["decrypt_error"] = str(e)
            else:
                event["decrypt_error"] = (
                    f"no key for recipient {recipient}"
                )

    return event


def decrypt_log(log_path, gm_gnupghome, keys_dir, output=None):
    """Decrypt all encrypted events in a JSONL log file.

    Args:
        log_path: Path to the JSONL log file.
        gm_gnupghome: Path to the GM's GPG home directory.
        keys_dir: Path to the keys/ directory containing
            GM-encrypted recipient private keys.
        output: Writable file object. Defaults to stdout.

    Returns:
        Number of events decrypted.
    """
    if output is None:
        output = sys.stdout

    decrypted_count = 0
    key_cache = {}

    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    output.write(line + "\n")
                    continue

                had_encrypted = (
                    "encrypted_output" in event
                    or "encrypted" in event
                    or "encrypted_data" in event
                )
                event = decrypt_event(
                    event, gm_gnupghome, keys_dir, key_cache,
                )
                still_encrypted = (
                    "encrypted_output" in event
                    or "encrypted" in event
                    or "encrypted_data" in event
                )
                if had_encrypted and not still_encrypted:
                    decrypted_count += 1

                output.write(
                    json.dumps(event, separators=(",", ":")) + "\n"
                )
    finally:
        for tmpdir in key_cache.values():
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

    return decrypted_count


def main():
    parser = argparse.ArgumentParser(
        description="Decrypt a perfid JSONL log using the GM key.",
    )
    parser.add_argument(
        "--gm-key",
        required=True,
        help="Path to GM GPG home directory (e.g. gm-gpg/)",
    )
    parser.add_argument(
        "--keys-dir",
        help=(
            "Path to keys/ directory with GM-encrypted recipient "
            "private keys. Defaults to keys/ next to the log file."
        ),
    )
    parser.add_argument(
        "log_file",
        help="Path to the JSONL log file",
    )

    args = parser.parse_args()
    keys_dir = args.keys_dir
    if keys_dir is None:
        keys_dir = os.path.join(
            os.path.dirname(os.path.abspath(args.log_file)), "keys",
        )
    count = decrypt_log(args.log_file, args.gm_key, keys_dir)
    print(f"Decrypted {count} events.", file=sys.stderr)


if __name__ == "__main__":
    main()
