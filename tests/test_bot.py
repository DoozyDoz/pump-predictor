"""Unit tests for src/bot.py menu-related functions.

Covers all test gaps identified in test-gap-report.md:
  Gap 1 (Critical): button normalization in handle_message() -- lines 240-245
  Gap 2 (High):     build_main_menu() return structure
  Gap 3 (High):     send_message() reply_markup handling
  Gap 4 (Medium):   /menu command handler
  Gap 5 (Medium):   help/start handler keyboard
  Gap 6 (Medium):   startup welcome message keyboard
  Gap 7 (Low):      MENU_BUTTONS dict structure

All tests use mocking to avoid real network calls or database access.
"""

import os
# Set dummy env vars BEFORE importing bot so the module can load.
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from unittest.mock import ANY, patch

import pytest

# Now safe to import the bot module.
from src.bot import (
    MENU_BUTTONS,
    build_main_menu,
    handle_message,
    run_bot,
    send_message,
)

# ---------------------------------------------------------------------------
# Gap 2 (High): build_main_menu() return structure
# ---------------------------------------------------------------------------

class TestBuildMainMenu:
    """Verify the structure of the ReplyKeyboardMarkup dict."""

    def test_returns_dict(self):
        menu = build_main_menu()
        assert isinstance(menu, dict)

    def test_has_keyboard_key(self):
        menu = build_main_menu()
        assert "keyboard" in menu

    def test_keyboard_has_two_rows(self):
        menu = build_main_menu()
        assert len(menu["keyboard"]) == 2

    def test_first_row_scan_and_positions(self):
        menu = build_main_menu()
        assert menu["keyboard"][0] == ["🔍 Scan", "📊 Positions"]

    def test_second_row_help(self):
        menu = build_main_menu()
        assert menu["keyboard"][1] == ["❓ Help"]

    def test_resize_keyboard_is_true(self):
        menu = build_main_menu()
        assert menu["resize_keyboard"] is True

    def test_persistent_is_true(self):
        menu = build_main_menu()
        assert menu["persistent"] is True


# ---------------------------------------------------------------------------
# Gap 3 (High): send_message() reply_markup handling
# ---------------------------------------------------------------------------

class TestSendMessage:
    """Verify send_message() payload construction with/without reply_markup."""

    @patch("src.bot.requests.post")
    def test_reply_markup_included_in_payload(self, mock_post):
        """When reply_markup is passed, it should appear in the JSON payload."""
        mock_post.return_value.status_code = 200
        markup = {"keyboard": [["A", "B"]], "resize_keyboard": True}
        send_message("123", "hello", reply_markup=markup)
        payload = mock_post.call_args[1]["json"]
        assert payload["reply_markup"] == markup

    @patch("src.bot.requests.post")
    def test_reply_markup_omitted_when_not_provided(self, mock_post):
        """When reply_markup is not passed, the payload must not contain the key."""
        mock_post.return_value.status_code = 200
        send_message("123", "hello")
        payload = mock_post.call_args[1]["json"]
        assert "reply_markup" not in payload

    @patch("src.bot.requests.post")
    def test_reply_markup_none_same_as_omitted(self, mock_post):
        """Passing reply_markup=None must behave the same as omitting it."""
        mock_post.return_value.status_code = 200
        send_message("123", "hello", reply_markup=None)
        payload = mock_post.call_args[1]["json"]
        assert "reply_markup" not in payload

    @patch("src.bot.requests.post")
    def test_empty_dict_reply_markup_sent_through(self, mock_post):
        """Even an empty dict {} as reply_markup should be sent through."""
        mock_post.return_value.status_code = 200
        send_message("123", "hello", reply_markup={})
        payload = mock_post.call_args[1]["json"]
        assert payload["reply_markup"] == {}

    @patch("src.bot.requests.post")
    def test_returns_true_on_http_200(self, mock_post):
        """HTTP 200 from Telegram should cause send_message to return True."""
        mock_post.return_value.status_code = 200
        assert send_message("123", "hello") is True

    @patch("src.bot.requests.post")
    def test_returns_false_on_http_non_200(self, mock_post):
        """Non-200 status codes should return False."""
        mock_post.return_value.status_code = 403
        assert send_message("123", "hello") is False

    @patch("src.bot.requests.post")
    def test_returns_false_on_exception(self, mock_post):
        """Network exceptions should be caught and return False."""
        mock_post.side_effect = Exception("network error")
        assert send_message("123", "hello") is False


# ---------------------------------------------------------------------------
# Gap 1 (Critical): Button normalization in handle_message()
# ---------------------------------------------------------------------------

class TestButtonNormalization:
    """Verify that emoji-prefixed button texts are normalized to commands
    and that free-text commands are NOT affected."""

    @patch("src.pipeline.run_daily", return_value=[])
    @patch("src.bot.send_message")
    def test_scan_button_dispatches_scan(self, mock_send, mock_run_daily):
        """Tapping "🔍 Scan" should normalise to "scan" and run the scan pipeline."""
        handle_message({"text": "🔍 Scan", "chat": {"id": "123"}})
        assert mock_run_daily.called, (
            "run_daily should have been called when '🔍 Scan' normalizes to 'scan'"
        )

    @patch("src.bot.get_active_positions", return_value=[])
    @patch("src.bot.send_message")
    def test_positions_button_dispatches_positions(self, mock_send, mock_positions):
        """Tapping "📊 Positions" should normalise to "positions"."""
        handle_message({"text": "📊 Positions", "chat": {"id": "123"}})
        assert mock_positions.called, (
            "get_active_positions should have been called for positions button"
        )
        sent_text = mock_send.call_args[0][1]
        assert "No active positions" in sent_text

    @patch("src.bot.send_message")
    def test_help_button_dispatches_help(self, mock_send):
        """Tapping "❓ Help" should normalise to "help" and show the help text."""
        handle_message({"text": "❓ Help", "chat": {"id": "123"}})
        sent_text = mock_send.call_args[0][1]
        assert "Alpha Bot" in sent_text

    @patch("src.pipeline.run_daily", return_value=[])
    @patch("src.bot.send_message")
    def test_typing_scan_manually_still_works(self, mock_send, mock_run_daily):
        """Typing 'scan' (without an emoji prefix) must NOT be normalised and must
        still dispatch to the scan handler."""
        handle_message({"text": "scan", "chat": {"id": "123"}})
        assert mock_run_daily.called, (
            "run_daily should still be called for plain 'scan'"
        )

    @patch("src.bot.get_active_positions", return_value=[])
    @patch("src.bot.send_message")
    def test_close_command_unaffected_by_normalization(self, mock_send, mock_positions):
        """'close COS' must NOT be caught by the button-normalization block and must
        reach the close handler."""
        handle_message({"text": "close COS", "chat": {"id": "123"}})
        assert mock_positions.called, (
            "get_active_positions should be called for 'close COS'"
        )

    @patch("src.bot.open_position", return_value=1)
    @patch("src.bot.send_message")
    def test_buy_command_unaffected_by_normalization(self, mock_send, mock_open):
        """'buy COS at 0.00123' must NOT be caught by normalization."""
        handle_message({"text": "buy COS at 0.00123", "chat": {"id": "123"}})
        assert mock_open.called, (
            "open_position should be called for buy command"
        )

    @patch("src.bot.MENU_BUTTONS", {})
    @patch("src.bot.send_message")
    def test_empty_menu_buttons_no_crash(self, mock_send):
        """When MENU_BUTTONS is empty, no normalization should occur and no error
        should be raised for any input."""
        # With an empty dict the generator in the normalization block yields
        # nothing, so the 'if lower in (...)' check is always False.
        handle_message({"text": "🔍 Scan", "chat": {"id": "123"}})
        # The function should return cleanly without dispatching to any
        # matching handler (it falls through the bottom of handle_message).
        # No crash == pass.


# ---------------------------------------------------------------------------
# Gap 4 (Medium): /menu command handler
# ---------------------------------------------------------------------------

class TestMenuHandler:
    """Verify that /menu (and 'menu') re-shows the keyboard."""

    @patch("src.bot.build_main_menu")
    @patch("src.bot.send_message")
    def test_slash_menu_shows_keyboard(self, mock_send, mock_build_menu):
        """'/menu' should call send_message with reply_markup from build_main_menu."""
        mock_build_menu.return_value = {"keyboard": [["test"]]}
        handle_message({"text": "/menu", "chat": {"id": "123"}})
        assert "Menu shown" in mock_send.call_args[0][1]
        assert mock_send.call_args[1].get("reply_markup") == {"keyboard": [["test"]]}

    @patch("src.bot.build_main_menu")
    @patch("src.bot.send_message")
    def test_menu_without_slash_shows_keyboard(self, mock_send, mock_build_menu):
        """'menu' (no slash) should also re-show the keyboard."""
        mock_build_menu.return_value = {"keyboard": [["test"]]}
        handle_message({"text": "menu", "chat": {"id": "123"}})
        assert "Menu shown" in mock_send.call_args[0][1]
        assert mock_send.call_args[1].get("reply_markup") == {"keyboard": [["test"]]}

    @patch("src.bot.send_message")
    def test_menu_does_not_fall_through(self, mock_send):
        """'/menu' must return immediately after handling; no other handler runs."""
        handle_message({"text": "/menu", "chat": {"id": "123"}})
        assert mock_send.call_count == 1, (
            "send_message should be called exactly once by the menu handler"
        )


# ---------------------------------------------------------------------------
# Gap 5 (Medium): Help/start handler passes keyboard
# ---------------------------------------------------------------------------

class TestHelpStartHandler:
    """Verify that help/start/hi/hello all show the keyboard."""

    @patch("src.bot.build_main_menu")
    @patch("src.bot.send_message")
    def test_slash_help_shows_keyboard(self, mock_send, mock_build_menu):
        """'/help' should show help text with reply_markup."""
        mock_build_menu.return_value = {"keyboard": [["test"]]}
        handle_message({"text": "/help", "chat": {"id": "123"}})
        sent_text = mock_send.call_args[0][1]
        assert "Alpha Bot" in sent_text
        assert mock_send.call_args[1].get("reply_markup") == {"keyboard": [["test"]]}

    @pytest.mark.parametrize("text", ["help", "/help", "start", "/start", "hi", "hello"])
    def test_all_variants_show_keyboard(self, text):
        """Every help/start variant should show help text with keyboard."""
        with patch("src.bot.build_main_menu") as mock_build_menu:
            with patch("src.bot.send_message") as mock_send:
                mock_build_menu.return_value = {"keyboard": [["test"]]}
                handle_message({"text": text, "chat": {"id": "123"}})
                sent_text = mock_send.call_args[0][1]
                assert "Alpha Bot" in sent_text
                assert mock_send.call_args[1].get("reply_markup") == {
                    "keyboard": [["test"]]
                }


# ---------------------------------------------------------------------------
# Gap 6 (Medium): Startup welcome message includes keyboard
# ---------------------------------------------------------------------------

class TestStartupMessage:
    """Verify that run_bot() sends the startup message with the keyboard."""

    @patch("src.bot.get_updates", return_value=[])
    @patch("src.bot.init_db")
    @patch("src.bot.send_message")
    @patch("src.bot.build_main_menu")
    def test_startup_uses_build_main_menu(
        self, mock_build_menu, mock_send, mock_init_db, mock_get_updates
    ):
        """run_bot() startup send_message call must include reply_markup."""
        mock_build_menu.return_value = {"keyboard": [["startup"]]}

        # run_bot() has an infinite loop; break out via time.sleep.
        with patch("src.bot.time.sleep", side_effect=StopIteration):
            with pytest.raises(StopIteration):
                run_bot()

        mock_send.assert_any_call(
            ANY,
            ANY,
            reply_markup={"keyboard": [["startup"]]},
        )


# ---------------------------------------------------------------------------
# Gap 7 (Low): MENU_BUTTONS dict structure
# ---------------------------------------------------------------------------

class TestMENU_BUTTONS:
    """Verify the MENU_BUTTONS mapping has the expected structure."""

    def test_has_three_buttons(self):
        assert len(MENU_BUTTONS) == 3

    def test_scan_maps_to_scan(self):
        assert MENU_BUTTONS["🔍 Scan"] == "scan"

    def test_positions_maps_to_positions(self):
        assert MENU_BUTTONS["📊 Positions"] == "positions"

    def test_help_maps_to_help(self):
        assert MENU_BUTTONS["❓ Help"] == "help"

    def test_all_keys_start_with_non_ascii(self):
        """Every key should start with a non-ASCII (emoji) character."""
        for key in MENU_BUTTONS:
            assert ord(key[0]) > 127, (
                f"Key {key!r} does not start with an emoji character"
            )
