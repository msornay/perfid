"""Tests for logger.py — JSONL game logger for perfid."""

import json
import os

import pytest

from logger import (
    ADJUDICATION_RESULT,
    ERROR,
    GAME_CREATED,
    GAME_ENDED,
    KEY_GENERATED,
    KEY_IMPORTED,
    MESSAGE_SENT,
    ORDERS_SUBMITTED,
    PHASE_START,
    GameLogger,
)


@pytest.fixture
def game_dir(tmp_path):
    return str(tmp_path / "game-001")


@pytest.fixture
def log(game_dir):
    return GameLogger(game_dir)


class TestBasicLogging:
    def test_creates_log_file(self, log, game_dir):
        log.game_created("game-001", ["England", "France"])
        assert os.path.exists(os.path.join(game_dir, "log.jsonl"))

    def test_each_event_is_one_line(self, log):
        log.phase_start(1901, "Spring", "Negotiation")
        log.phase_start(1901, "Spring", "Orders")
        with open(log.log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # each line is valid JSON

    def test_events_have_timestamp(self, log):
        log.game_created("g1", [])
        events = log.read_events()
        assert len(events) == 1
        assert "ts" in events[0]
        assert "T" in events[0]["ts"]  # ISO format

    def test_events_have_type(self, log):
        log.game_created("g1", [])
        events = log.read_events()
        assert events[0]["event"] == GAME_CREATED


class TestEventTypes:
    def test_phase_start(self, log):
        log.phase_start(1901, "Spring", "Negotiation")
        e = log.read_events()[0]
        assert e["event"] == PHASE_START
        assert e["year"] == 1901
        assert e["season"] == "Spring"
        assert e["phase"] == "Negotiation"

    def test_orders_submitted(self, log):
        log.orders_submitted(
            "England",
            ["F Lon - NTH", "A Lvp - Yor"],
            "S1901M",
        )
        e = log.read_events()[0]
        assert e["event"] == ORDERS_SUBMITTED
        assert e["power"] == "England"
        assert e["orders"] == ["F Lon - NTH", "A Lvp - Yor"]
        assert e["phase"] == "S1901M"

    def test_adjudication_result(self, log):
        results = {
            "resolved": ["F Lon - NTH (ok)", "A Lvp - Yor (ok)"],
            "dislodged": [],
        }
        log.adjudication_result("S1901M", results)
        e = log.read_events()[0]
        assert e["event"] == ADJUDICATION_RESULT
        assert e["phase"] == "S1901M"
        assert e["results"]["resolved"] == results["resolved"]

    def test_message_sent(self, log):
        log.message_sent("England", "France", "S1901M", 1)
        e = log.read_events()[0]
        assert e["event"] == MESSAGE_SENT
        assert e["sender"] == "England"
        assert e["recipient"] == "France"
        assert e["round"] == 1

    def test_key_generated(self, log):
        log.key_generated("England", "ABCD1234")
        e = log.read_events()[0]
        assert e["event"] == KEY_GENERATED
        assert e["power"] == "England"
        assert e["fingerprint"] == "ABCD1234"

    def test_key_imported(self, log):
        log.key_imported("France", "EFGH5678", "England")
        e = log.read_events()[0]
        assert e["event"] == KEY_IMPORTED
        assert e["imported_by"] == "England"

    def test_game_ended_winner(self, log):
        log.game_ended(winner="Russia", reason="solo victory")
        e = log.read_events()[0]
        assert e["event"] == GAME_ENDED
        assert e["winner"] == "Russia"
        assert e["reason"] == "solo victory"

    def test_game_ended_draw(self, log):
        log.game_ended(winner=None, reason="draw")
        e = log.read_events()[0]
        assert e["winner"] is None

    def test_error(self, log):
        log.error("England", "Timeout submitting orders", phase="S1901M")
        e = log.read_events()[0]
        assert e["event"] == ERROR
        assert e["power"] == "England"
        assert e["phase"] == "S1901M"

    def test_error_without_phase(self, log):
        log.error("GM", "Failed to start sandbox")
        e = log.read_events()[0]
        assert "phase" not in e


class TestReadEvents:
    def test_read_empty_log(self, log):
        assert log.read_events() == []

    def test_read_nonexistent_log(self, log):
        assert log.read_events() == []

    def test_filter_by_type(self, log):
        log.phase_start(1901, "Spring", "Negotiation")
        log.orders_submitted("England", ["A Lon H"], "S1901M")
        log.phase_start(1901, "Spring", "Orders")

        phases = log.read_events(event_type=PHASE_START)
        assert len(phases) == 2
        orders = log.read_events(event_type=ORDERS_SUBMITTED)
        assert len(orders) == 1

    def test_append_only(self, log):
        """Events accumulate; old events are never overwritten."""
        log.phase_start(1901, "Spring", "Negotiation")
        log.phase_start(1901, "Spring", "Orders")
        log.phase_start(1901, "Fall", "Negotiation")
        events = log.read_events()
        assert len(events) == 3
        assert events[0]["season"] == "Spring"
        assert events[2]["season"] == "Fall"
