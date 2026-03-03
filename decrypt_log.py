"""Decrypt a perfid JSONL log using the GM key.

Usage: python3 decrypt_log.py --gm-key gm-gpg/ game.jsonl > decrypted.jsonl

Decrypts 'encrypted_output' fields in agent_turn events and 'encrypted'
fields in message_routed events, replacing them with plaintext.
Non-encrypted events pass through unchanged.
"""

import argparse
import json
import sys

import gpg as gpg_mod


def decrypt_event(event, gm_gnupghome):
    """Decrypt encrypted fields in a single event dict.

    Modifies the event in place:
    - agent_turn: decrypts 'encrypted_output' → 'output'
    - message_routed: decrypts 'encrypted' → 'plaintext'

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

    elif event_type == "message_routed" and "encrypted" in event:
        ciphertext = event["encrypted"]
        if ciphertext.startswith("-----BEGIN PGP MESSAGE-----"):
            try:
                plaintext = gpg_mod.decrypt(gm_gnupghome, ciphertext)
                event["plaintext"] = plaintext
                del event["encrypted"]
            except Exception as e:
                event["decrypt_error"] = str(e)

    return event


def decrypt_log(log_path, gm_gnupghome, output=None):
    """Decrypt all encrypted events in a JSONL log file.

    Args:
        log_path: Path to the JSONL log file.
        gm_gnupghome: Path to the GM's GPG home directory.
        output: Writable file object. Defaults to stdout.

    Returns:
        Number of events decrypted.
    """
    if output is None:
        output = sys.stdout

    decrypted_count = 0

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
            )
            event = decrypt_event(event, gm_gnupghome)
            still_encrypted = (
                "encrypted_output" in event
                or "encrypted" in event
            )
            if had_encrypted and not still_encrypted:
                decrypted_count += 1

            output.write(
                json.dumps(event, separators=(",", ":")) + "\n"
            )

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
        "log_file",
        help="Path to the JSONL log file",
    )

    args = parser.parse_args()
    count = decrypt_log(args.log_file, args.gm_key)
    print(f"Decrypted {count} events.", file=sys.stderr)


if __name__ == "__main__":
    main()
