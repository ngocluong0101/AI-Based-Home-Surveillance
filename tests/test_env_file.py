import os
import stat

import pytest

from src.config.env_file import is_truthy, read_env_values, write_env_values


@pytest.fixture
def env_path(tmp_path):
    return tmp_path / ".env"


def test_read_missing_file_is_empty(env_path):
    assert read_env_values(env_path) == {}


def test_read_skips_comments_and_blank_lines(env_path):
    env_path.write_text("# a comment\n\nRTSP_URL=rtsp://host/x\n  # TELEGRAM_CHAT_ID=1\n", encoding="utf-8")

    assert read_env_values(env_path) == {"RTSP_URL": "rtsp://host/x"}


def test_read_strips_quotes_and_inline_comments(env_path):
    env_path.write_text(
        'TELEGRAM_CHAT_ID="-100123"\nLOG_FILE=logs/app.log   # rotated log\nPLAIN=a#b\n', encoding="utf-8"
    )

    values = read_env_values(env_path)

    assert values == {"TELEGRAM_CHAT_ID": "-100123", "LOG_FILE": "logs/app.log", "PLAIN": "a#b"}


def test_write_replaces_existing_key_in_place_and_keeps_other_lines(env_path):
    env_path.write_text(
        "# Telegram\nTELEGRAM_BOT_TOKEN=old\nENABLE_TELEGRAM=false\n\n# App\nCONFIDENCE_THRESHOLD=0.5\n",
        encoding="utf-8",
    )

    write_env_values(env_path, {"ENABLE_TELEGRAM": "true"})

    assert env_path.read_text(encoding="utf-8") == (
        "# Telegram\nTELEGRAM_BOT_TOKEN=old\nENABLE_TELEGRAM=true\n\n# App\nCONFIDENCE_THRESHOLD=0.5\n"
    )


def test_write_appends_missing_keys(env_path):
    env_path.write_text("RTSP_URL=rtsp://host/x\n", encoding="utf-8")

    write_env_values(env_path, {"TELEGRAM_CHAT_ID": "987654321"})

    assert read_env_values(env_path) == {"RTSP_URL": "rtsp://host/x", "TELEGRAM_CHAT_ID": "987654321"}


def test_write_does_not_touch_commented_out_key(env_path):
    env_path.write_text("# TELEGRAM_CHAT_ID=111\n", encoding="utf-8")

    write_env_values(env_path, {"TELEGRAM_CHAT_ID": "222"})

    assert env_path.read_text(encoding="utf-8") == "# TELEGRAM_CHAT_ID=111\nTELEGRAM_CHAT_ID=222\n"


def test_write_creates_missing_file_with_private_permissions(env_path):
    write_env_values(env_path, {"ENABLE_TELEGRAM": "true", "TELEGRAM_BOT_TOKEN": "123:abc"})

    assert read_env_values(env_path) == {"ENABLE_TELEGRAM": "true", "TELEGRAM_BOT_TOKEN": "123:abc"}
    if os.name == "posix":
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert not env_path.with_name(".env.tmp").exists()


def test_write_quotes_values_with_spaces_and_round_trips(env_path):
    write_env_values(env_path, {"NOTE": "two words"})

    assert env_path.read_text(encoding="utf-8") == 'NOTE="two words"\n'
    assert read_env_values(env_path) == {"NOTE": "two words"}


def test_write_rejects_values_that_would_break_the_file(env_path):
    with pytest.raises(ValueError):
        write_env_values(env_path, {"NOTE": 'say "hi"'})
    with pytest.raises(ValueError):
        write_env_values(env_path, {"NOTE": "line1\nline2"})


@pytest.mark.parametrize(
    "value, expected",
    [("true", True), ("ON", True), ("1", True), (" yes ", True), ("false", False), ("", False), (None, False)],
)
def test_is_truthy(value, expected):
    assert is_truthy(value) is expected
