"""Message routing between Diplomacy agents.

Routes GPG-encrypted messages between agent outboxes and inboxes.
Messages are named with a structured convention for traceability:
    <sender>-to-<recipient>-<phase>-r<round>-<seq>.gpg

Directory layout inside a game dir:
    messages/
        outbox/<power>/          — agent writes encrypted messages here
        inbox/<power>/           — router delivers messages here
        archive/<year>/<phase>/  — archived after phase completes
"""

import re
import shutil
from pathlib import Path


# Message filename pattern
MSG_PATTERN = re.compile(
    r"^(?P<sender>[A-Za-z]+)-to-(?P<recipient>[A-Za-z]+)"
    r"-(?P<phase>[A-Za-z_]+)"
    r"-r(?P<round>\d+)"
    r"-(?P<seq>\d+)\.gpg$"
)


def init_message_dirs(game_dir, powers):
    """Create outbox/ and inbox/ directories for each power.

    Args:
        game_dir: Path to game directory.
        powers: List of power names.
    """
    game_dir = Path(game_dir)
    msg_dir = game_dir / "messages"
    for subdir in ("outbox", "inbox"):
        for power in powers:
            (msg_dir / subdir / power).mkdir(parents=True, exist_ok=True)
    (msg_dir / "archive").mkdir(parents=True, exist_ok=True)


def message_filename(sender, recipient, phase, round_num, seq):
    """Build a structured message filename.

    Args:
        sender: Sending power name.
        recipient: Receiving power name.
        phase: Phase label (e.g. "Spring_Diplomacy").
        round_num: Negotiation round number (1-indexed).
        seq: Sequence number within this round (1-indexed).

    Returns:
        Filename string like "France-to-Germany-Spring_Diplomacy-r1-1.gpg"
    """
    phase_label = phase.replace(" ", "_")
    return (
        f"{sender}-to-{recipient}-{phase_label}"
        f"-r{round_num}-{seq}.gpg"
    )


def parse_message_filename(filename):
    """Parse a structured message filename.

    Returns a dict with sender, recipient, phase, round, seq,
    or None if the filename doesn't match the convention.
    """
    m = MSG_PATTERN.match(filename)
    if not m:
        return None
    return {
        "sender": m.group("sender"),
        "recipient": m.group("recipient"),
        "phase": m.group("phase").replace("_", " "),
        "round": int(m.group("round")),
        "seq": int(m.group("seq")),
    }


def next_seq(game_dir, sender, recipient, phase, round_num):
    """Determine the next sequence number for a message.

    Scans the sender's outbox for existing messages to this
    recipient in this phase/round and returns the next seq.
    """
    game_dir = Path(game_dir)
    outbox = game_dir / "messages" / "outbox" / sender
    if not outbox.exists():
        return 1

    phase_label = phase.replace(" ", "_")
    prefix = f"{sender}-to-{recipient}-{phase_label}-r{round_num}-"
    max_seq = 0

    for f in outbox.iterdir():
        if f.name.startswith(prefix) and f.name.endswith(".gpg"):
            parsed = parse_message_filename(f.name)
            if parsed and parsed["seq"] > max_seq:
                max_seq = parsed["seq"]

    return max_seq + 1


def send_message(game_dir, sender, recipient, phase, round_num,
                 ciphertext):
    """Write an encrypted message to the sender's outbox.

    The message is written as a .gpg file with a structured name.
    Call route_messages() to deliver it to the recipient's inbox.

    Args:
        game_dir: Path to game directory.
        sender: Sending power name.
        recipient: Receiving power name.
        phase: Current phase label.
        round_num: Negotiation round number.
        ciphertext: Encrypted message bytes.

    Returns:
        Path to the written outbox file.
    """
    game_dir = Path(game_dir)
    outbox = game_dir / "messages" / "outbox" / sender
    outbox.mkdir(parents=True, exist_ok=True)

    seq = next_seq(game_dir, sender, recipient, phase, round_num)
    fname = message_filename(sender, recipient, phase, round_num, seq)
    msg_path = outbox / fname

    if isinstance(ciphertext, str):
        msg_path.write_text(ciphertext)
    else:
        msg_path.write_bytes(ciphertext)

    return msg_path


def route_messages(game_dir, sender=None):
    """Move messages from outbox to recipient inbox.

    Copies each message from the sender's outbox to the recipient's
    inbox (determined from the filename). Messages remain in the
    outbox as a sent record.

    Args:
        game_dir: Path to game directory.
        sender: Optional — route only this power's outbox.
                If None, route all powers' outboxes.

    Returns:
        List of (source_path, dest_path) tuples for routed messages.
    """
    game_dir = Path(game_dir)
    outbox_root = game_dir / "messages" / "outbox"
    inbox_root = game_dir / "messages" / "inbox"
    routed = []

    if sender:
        powers_to_route = [sender]
    else:
        if not outbox_root.exists():
            return routed
        powers_to_route = [
            d.name for d in outbox_root.iterdir() if d.is_dir()
        ]

    for power in powers_to_route:
        outbox = outbox_root / power
        if not outbox.exists():
            continue

        for msg_file in sorted(outbox.iterdir()):
            if not msg_file.name.endswith(".gpg"):
                continue

            parsed = parse_message_filename(msg_file.name)
            if not parsed:
                continue

            recipient = parsed["recipient"]
            inbox = inbox_root / recipient
            inbox.mkdir(parents=True, exist_ok=True)

            dest = inbox / msg_file.name
            if not dest.exists():
                shutil.copy2(msg_file, dest)
                routed.append((msg_file, dest))

    return routed


def list_inbox(game_dir, power, phase=None, round_num=None):
    """List messages in a power's inbox.

    Args:
        game_dir: Path to game directory.
        power: Power whose inbox to list.
        phase: Optional phase filter.
        round_num: Optional round filter.

    Returns:
        List of dicts with parsed message info + path.
    """
    game_dir = Path(game_dir)
    inbox = game_dir / "messages" / "inbox" / power
    if not inbox.exists():
        return []

    messages = []
    for msg_file in sorted(inbox.iterdir()):
        if not msg_file.name.endswith(".gpg"):
            continue

        parsed = parse_message_filename(msg_file.name)
        if not parsed:
            continue

        if phase and parsed["phase"] != phase:
            continue
        if round_num is not None and parsed["round"] != round_num:
            continue

        parsed["path"] = str(msg_file)
        messages.append(parsed)

    return messages


def list_outbox(game_dir, power, phase=None, round_num=None):
    """List messages in a power's outbox.

    Same interface as list_inbox but reads from outbox/.
    """
    game_dir = Path(game_dir)
    outbox = game_dir / "messages" / "outbox" / power
    if not outbox.exists():
        return []

    messages = []
    for msg_file in sorted(outbox.iterdir()):
        if not msg_file.name.endswith(".gpg"):
            continue

        parsed = parse_message_filename(msg_file.name)
        if not parsed:
            continue

        if phase and parsed["phase"] != phase:
            continue
        if round_num is not None and parsed["round"] != round_num:
            continue

        parsed["path"] = str(msg_file)
        messages.append(parsed)

    return messages


def archive_phase(game_dir, year, phase):
    """Archive all messages from a completed phase.

    Moves inbox and outbox contents to archive/<year>/<phase>/.
    """
    game_dir = Path(game_dir)
    phase_label = phase.replace(" ", "_")
    archive = game_dir / "messages" / "archive" / str(year) / phase_label
    archive.mkdir(parents=True, exist_ok=True)

    for subdir in ("inbox", "outbox"):
        src_root = game_dir / "messages" / subdir
        if not src_root.exists():
            continue

        for power_dir in src_root.iterdir():
            if not power_dir.is_dir():
                continue

            for msg_file in list(power_dir.iterdir()):
                parsed = parse_message_filename(msg_file.name)
                if not parsed:
                    continue
                if parsed["phase"] != phase:
                    continue

                dest_dir = archive / subdir / power_dir.name
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(msg_file), str(dest_dir / msg_file.name))


def clear_inboxes(game_dir, phase=None):
    """Clear all inboxes, optionally filtering by phase.

    Used between negotiation rounds or phases to reset inboxes.
    """
    game_dir = Path(game_dir)
    inbox_root = game_dir / "messages" / "inbox"
    if not inbox_root.exists():
        return

    for power_dir in inbox_root.iterdir():
        if not power_dir.is_dir():
            continue

        for msg_file in list(power_dir.iterdir()):
            if phase:
                parsed = parse_message_filename(msg_file.name)
                if parsed and parsed["phase"] != phase:
                    continue
            msg_file.unlink()


def message_count(game_dir, power, direction="inbox",
                  phase=None, round_num=None):
    """Count messages for a power.

    Args:
        game_dir: Path to game directory.
        power: Power name.
        direction: "inbox" or "outbox".
        phase: Optional phase filter.
        round_num: Optional round filter.

    Returns:
        Number of matching messages.
    """
    if direction == "inbox":
        return len(list_inbox(game_dir, power, phase, round_num))
    return len(list_outbox(game_dir, power, phase, round_num))
