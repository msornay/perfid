"""Tests for message_router.py — encrypted message routing."""

import pytest
from pathlib import Path

from message_router import (
    archive_phase,
    clear_inboxes,
    init_message_dirs,
    list_inbox,
    list_outbox,
    message_count,
    message_filename,
    next_seq,
    parse_message_filename,
    route_messages,
    send_message,
)

POWERS = ["Austria", "England", "France", "Germany",
          "Italy", "Russia", "Turkey"]


@pytest.fixture
def game_dir(tmp_path):
    gd = tmp_path / "test-game"
    gd.mkdir()
    init_message_dirs(gd, POWERS)
    return gd


class TestInitMessageDirs:
    def test_creates_inbox_dirs(self, game_dir):
        for power in POWERS:
            assert (game_dir / "messages" / "inbox" / power).is_dir()

    def test_creates_outbox_dirs(self, game_dir):
        for power in POWERS:
            assert (game_dir / "messages" / "outbox" / power).is_dir()

    def test_creates_archive_dir(self, game_dir):
        assert (game_dir / "messages" / "archive").is_dir()


class TestMessageFilename:
    def test_basic_format(self):
        name = message_filename(
            "France", "Germany", "Spring Diplomacy", 1, 1
        )
        assert name == "France-to-Germany-Spring_Diplomacy-r1-1.gpg"

    def test_round_and_seq(self):
        name = message_filename(
            "England", "France", "Fall Diplomacy", 3, 5
        )
        assert name == "England-to-France-Fall_Diplomacy-r3-5.gpg"


class TestParseMessageFilename:
    def test_parses_valid_filename(self):
        parsed = parse_message_filename(
            "France-to-Germany-Spring_Diplomacy-r1-1.gpg"
        )
        assert parsed["sender"] == "France"
        assert parsed["recipient"] == "Germany"
        assert parsed["phase"] == "Spring Diplomacy"
        assert parsed["round"] == 1
        assert parsed["seq"] == 1

    def test_parses_higher_numbers(self):
        parsed = parse_message_filename(
            "Russia-to-Turkey-Fall_Diplomacy-r3-12.gpg"
        )
        assert parsed["round"] == 3
        assert parsed["seq"] == 12

    def test_returns_none_for_invalid(self):
        assert parse_message_filename("not-a-message.txt") is None
        assert parse_message_filename("") is None

    def test_round_trip(self):
        name = message_filename(
            "Italy", "Austria", "Spring Diplomacy", 2, 3
        )
        parsed = parse_message_filename(name)
        assert parsed["sender"] == "Italy"
        assert parsed["recipient"] == "Austria"
        assert parsed["phase"] == "Spring Diplomacy"
        assert parsed["round"] == 2
        assert parsed["seq"] == 3


class TestNextSeq:
    def test_first_message_is_1(self, game_dir):
        seq = next_seq(
            game_dir, "France", "Germany", "Spring Diplomacy", 1
        )
        assert seq == 1

    def test_increments_after_send(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg1"
        )
        seq = next_seq(
            game_dir, "France", "Germany", "Spring Diplomacy", 1
        )
        assert seq == 2

    def test_independent_per_recipient(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg1"
        )
        seq = next_seq(
            game_dir, "France", "England", "Spring Diplomacy", 1
        )
        assert seq == 1

    def test_independent_per_round(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg1"
        )
        seq = next_seq(
            game_dir, "France", "Germany", "Spring Diplomacy", 2
        )
        assert seq == 1


class TestSendMessage:
    def test_creates_file_in_outbox(self, game_dir):
        path = send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"encrypted data"
        )
        assert path.exists()
        assert path.parent.name == "France"
        assert "outbox" in str(path)

    def test_file_contains_ciphertext(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"secret message"
        )
        outbox = game_dir / "messages" / "outbox" / "France"
        files = list(outbox.iterdir())
        assert len(files) == 1
        assert files[0].read_bytes() == b"secret message"

    def test_accepts_string_ciphertext(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, "ascii armored gpg"
        )
        outbox = game_dir / "messages" / "outbox" / "France"
        files = list(outbox.iterdir())
        assert files[0].read_text() == "ascii armored gpg"

    def test_auto_increments_seq(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg1"
        )
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg2"
        )

        outbox = game_dir / "messages" / "outbox" / "France"
        files = sorted(f.name for f in outbox.iterdir())
        assert len(files) == 2
        assert "-1.gpg" in files[0]
        assert "-2.gpg" in files[1]


class TestRouteMessages:
    def test_copies_to_inbox(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"hello Germany"
        )

        routed = route_messages(game_dir, sender="France")
        assert len(routed) == 1

        inbox = game_dir / "messages" / "inbox" / "Germany"
        files = list(inbox.iterdir())
        assert len(files) == 1
        assert files[0].read_bytes() == b"hello Germany"

    def test_message_stays_in_outbox(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"hello"
        )
        route_messages(game_dir, sender="France")

        outbox = game_dir / "messages" / "outbox" / "France"
        assert len(list(outbox.iterdir())) == 1

    def test_routes_all_senders(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"from France"
        )
        send_message(
            game_dir, "England", "Germany",
            "Spring Diplomacy", 1, b"from England"
        )

        routed = route_messages(game_dir)
        assert len(routed) == 2

        inbox = game_dir / "messages" / "inbox" / "Germany"
        assert len(list(inbox.iterdir())) == 2

    def test_does_not_duplicate(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"hello"
        )

        route_messages(game_dir)
        route_messages(game_dir)  # second call should be no-op

        inbox = game_dir / "messages" / "inbox" / "Germany"
        assert len(list(inbox.iterdir())) == 1

    def test_routes_to_correct_recipient(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"for Germany"
        )
        send_message(
            game_dir, "France", "England",
            "Spring Diplomacy", 1, b"for England"
        )

        route_messages(game_dir, sender="France")

        de_inbox = game_dir / "messages" / "inbox" / "Germany"
        en_inbox = game_dir / "messages" / "inbox" / "England"
        fr_inbox = game_dir / "messages" / "inbox" / "France"

        assert len(list(de_inbox.iterdir())) == 1
        assert len(list(en_inbox.iterdir())) == 1
        assert len(list(fr_inbox.iterdir())) == 0


class TestListInbox:
    def test_lists_messages(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg1"
        )
        send_message(
            game_dir, "England", "Germany",
            "Spring Diplomacy", 1, b"msg2"
        )
        route_messages(game_dir)

        msgs = list_inbox(game_dir, "Germany")
        assert len(msgs) == 2
        senders = {m["sender"] for m in msgs}
        assert senders == {"France", "England"}

    def test_filters_by_phase(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"spring"
        )
        send_message(
            game_dir, "France", "Germany",
            "Fall Diplomacy", 1, b"fall"
        )
        route_messages(game_dir)

        msgs = list_inbox(
            game_dir, "Germany", phase="Spring Diplomacy"
        )
        assert len(msgs) == 1
        assert msgs[0]["phase"] == "Spring Diplomacy"

    def test_filters_by_round(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"r1"
        )
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 2, b"r2"
        )
        route_messages(game_dir)

        msgs = list_inbox(
            game_dir, "Germany", round_num=2
        )
        assert len(msgs) == 1
        assert msgs[0]["round"] == 2

    def test_empty_inbox(self, game_dir):
        msgs = list_inbox(game_dir, "Germany")
        assert msgs == []

    def test_message_has_path(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg"
        )
        route_messages(game_dir)

        msgs = list_inbox(game_dir, "Germany")
        assert len(msgs) == 1
        assert "path" in msgs[0]
        assert Path(msgs[0]["path"]).exists()


class TestListOutbox:
    def test_lists_sent_messages(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg1"
        )
        send_message(
            game_dir, "France", "England",
            "Spring Diplomacy", 1, b"msg2"
        )

        msgs = list_outbox(game_dir, "France")
        assert len(msgs) == 2

    def test_filters_by_phase(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"spring"
        )
        send_message(
            game_dir, "France", "Germany",
            "Fall Diplomacy", 1, b"fall"
        )

        msgs = list_outbox(
            game_dir, "France", phase="Fall Diplomacy"
        )
        assert len(msgs) == 1


class TestArchivePhase:
    def test_moves_messages_to_archive(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg"
        )
        route_messages(game_dir)

        archive_phase(game_dir, 1901, "Spring Diplomacy")

        # Inbox should be empty
        inbox = game_dir / "messages" / "inbox" / "Germany"
        assert len(list(inbox.iterdir())) == 0

        # Archive should have the message
        archive = (
            game_dir / "messages" / "archive" / "1901"
            / "Spring_Diplomacy"
        )
        assert archive.exists()

    def test_preserves_other_phase_messages(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"spring"
        )
        send_message(
            game_dir, "France", "Germany",
            "Fall Diplomacy", 1, b"fall"
        )
        route_messages(game_dir)

        archive_phase(game_dir, 1901, "Spring Diplomacy")

        inbox = game_dir / "messages" / "inbox" / "Germany"
        remaining = list(inbox.iterdir())
        assert len(remaining) == 1
        assert "Fall" in remaining[0].name


class TestClearInboxes:
    def test_clears_all(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg"
        )
        send_message(
            game_dir, "England", "France",
            "Spring Diplomacy", 1, b"msg"
        )
        route_messages(game_dir)

        clear_inboxes(game_dir)

        for power in POWERS:
            inbox = game_dir / "messages" / "inbox" / power
            assert len(list(inbox.iterdir())) == 0

    def test_clears_specific_phase(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"spring"
        )
        send_message(
            game_dir, "France", "Germany",
            "Fall Diplomacy", 1, b"fall"
        )
        route_messages(game_dir)

        clear_inboxes(game_dir, phase="Spring Diplomacy")

        inbox = game_dir / "messages" / "inbox" / "Germany"
        remaining = list(inbox.iterdir())
        assert len(remaining) == 1
        assert "Fall" in remaining[0].name


class TestMessageCount:
    def test_inbox_count(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg1"
        )
        send_message(
            game_dir, "England", "Germany",
            "Spring Diplomacy", 1, b"msg2"
        )
        route_messages(game_dir)

        assert message_count(game_dir, "Germany", "inbox") == 2

    def test_outbox_count(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg1"
        )
        send_message(
            game_dir, "France", "England",
            "Spring Diplomacy", 1, b"msg2"
        )

        assert message_count(game_dir, "France", "outbox") == 2

    def test_filtered_count(self, game_dir):
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 1, b"msg1"
        )
        send_message(
            game_dir, "France", "Germany",
            "Spring Diplomacy", 2, b"msg2"
        )
        route_messages(game_dir)

        count = message_count(
            game_dir, "Germany", "inbox", round_num=1
        )
        assert count == 1


class TestFullNegotiationRound:
    """Integration test: simulate a negotiation round."""

    def test_negotiation_round(self, game_dir):
        phase = "Spring Diplomacy"
        round_num = 1

        # France sends to Germany and England
        send_message(
            game_dir, "France", "Germany",
            phase, round_num, b"Alliance proposal"
        )
        send_message(
            game_dir, "France", "England",
            phase, round_num, b"Channel agreement"
        )

        # Germany sends to France and Austria
        send_message(
            game_dir, "Germany", "France",
            phase, round_num, b"Counter-proposal"
        )
        send_message(
            game_dir, "Germany", "Austria",
            phase, round_num, b"Bounce plan"
        )

        # Route all messages
        routed = route_messages(game_dir)
        assert len(routed) == 4

        # Check each power's inbox
        assert message_count(game_dir, "Germany", "inbox") == 1
        assert message_count(game_dir, "England", "inbox") == 1
        assert message_count(game_dir, "France", "inbox") == 1
        assert message_count(game_dir, "Austria", "inbox") == 1

        # Verify message content
        de_msgs = list_inbox(game_dir, "Germany")
        assert de_msgs[0]["sender"] == "France"
        msg_path = Path(de_msgs[0]["path"])
        assert msg_path.read_bytes() == b"Alliance proposal"

        # Archive and verify cleanup
        archive_phase(game_dir, 1901, phase)
        assert message_count(game_dir, "Germany", "inbox") == 0
        assert message_count(game_dir, "France", "inbox") == 0
