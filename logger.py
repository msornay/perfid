"""Append-only JSONL game logger for perfid.

Writes structured events to log.jsonl in the game directory. Each line
is a self-contained JSON object with a timestamp, event type, and
event-specific data.
"""

import json
import os
from datetime import datetime, timezone


# Event types
PHASE_START = "phase_start"
ORDERS_SUBMITTED = "orders_submitted"
ADJUDICATION_RESULT = "adjudication_result"
MESSAGE_SENT = "message_sent"
KEY_GENERATED = "key_generated"
KEY_IMPORTED = "key_imported"
GAME_CREATED = "game_created"
GAME_ENDED = "game_ended"
ERROR = "error"


class GameLogger:
    """Append-only JSONL logger for a single game."""

    def __init__(self, game_dir):
        self.game_dir = game_dir
        self.log_path = os.path.join(game_dir, "log.jsonl")

    def _write(self, event):
        """Append a single event dict as a JSON line."""
        os.makedirs(self.game_dir, exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")

    def _event(self, event_type, **kwargs):
        """Build and write an event with timestamp."""
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
        }
        event.update(kwargs)
        self._write(event)

    def phase_start(self, year, season, phase_type):
        """Log the start of a game phase.

        Args:
            year: Game year (e.g. 1901).
            season: "Spring" or "Fall".
            phase_type: "Negotiation", "Orders", "Retreat", or "Adjustment".
        """
        self._event(
            PHASE_START,
            year=year,
            season=season,
            phase=phase_type,
        )

    def orders_submitted(self, power, orders, phase):
        """Log orders submitted by a power.

        Args:
            power: Diplomacy power name.
            orders: List of order strings.
            phase: Phase label (e.g. "S1901M").
        """
        self._event(
            ORDERS_SUBMITTED,
            power=power,
            orders=orders,
            phase=phase,
        )

    def adjudication_result(self, phase, results):
        """Log adjudication results.

        Args:
            phase: Phase label.
            results: Dict with adjudication outcome (resolved orders,
                     dislodged units, etc.).
        """
        self._event(
            ADJUDICATION_RESULT,
            phase=phase,
            results=results,
        )

    def message_sent(self, sender, recipient, phase, round_num):
        """Log that an encrypted message was sent.

        The message content is NOT logged (it's encrypted and private).

        Args:
            sender: Sending power name.
            recipient: Receiving power name.
            phase: Phase label.
            round_num: Negotiation round number.
        """
        self._event(
            MESSAGE_SENT,
            sender=sender,
            recipient=recipient,
            phase=phase,
            round=round_num,
        )

    def key_generated(self, power, fingerprint):
        """Log that a GPG key was generated."""
        self._event(KEY_GENERATED, power=power, fingerprint=fingerprint)

    def key_imported(self, power, fingerprint, imported_by):
        """Log that a public key was imported."""
        self._event(
            KEY_IMPORTED,
            power=power,
            fingerprint=fingerprint,
            imported_by=imported_by,
        )

    def game_created(self, game_id, powers):
        """Log game creation."""
        self._event(GAME_CREATED, game_id=game_id, powers=powers)

    def game_ended(self, winner=None, reason=""):
        """Log game end.

        Args:
            winner: Winning power name, or None for draw.
            reason: Why the game ended (e.g. "solo victory", "draw").
        """
        self._event(GAME_ENDED, winner=winner, reason=reason)

    def error(self, power, message, phase=None):
        """Log an error event.

        Args:
            power: Power involved (or "GM").
            message: Error description.
            phase: Optional phase label.
        """
        kwargs = {"power": power, "message": message}
        if phase is not None:
            kwargs["phase"] = phase
        self._event(ERROR, **kwargs)

    def read_events(self, event_type=None):
        """Read all events from the log, optionally filtered by type.

        Returns:
            List of event dicts.
        """
        if not os.path.exists(self.log_path):
            return []
        events = []
        with open(self.log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                if event_type is None or event.get("event") == event_type:
                    events.append(event)
        return events
