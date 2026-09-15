"""The root logger this process configures, and the token it keeps out of the file."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

import pytest

from host.logging_setup import (
    LOG_BACKUP_COUNT,
    LOG_FILENAME,
    LOG_FORMAT,
    MAX_LOG_BYTES,
    TokenRedactionFilter,
    configure_logging,
)


@pytest.fixture
def restore_root_logger():
    """Put the root logger back — the suite's own logging must survive this file."""
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in saved:
        root.addHandler(handler)
    root.setLevel(level)


class TestConfigureLogging:
    def test_it_writes_to_a_file_in_the_state_directory(self, tmp_path, restore_root_logger):
        configure_logging(str(tmp_path / "state"), "tok").info("a line")

        assert (tmp_path / "state" / LOG_FILENAME).read_text().endswith("a line\n")

    def test_it_creates_the_state_directory(self, tmp_path, restore_root_logger):
        configure_logging(str(tmp_path / "not-yet"), "tok")

        assert (tmp_path / "not-yet").is_dir()

    def test_the_format_is_the_one_saved_walk_throughs_grep_for(self, tmp_path, restore_root_logger):
        configure_logging(str(tmp_path), "tok").warning("careful")

        line = (tmp_path / LOG_FILENAME).read_text().strip()
        assert line.endswith("[WARNING]: careful")
        assert line.startswith("[")

    def test_the_format_string_is_verbatim(self):
        assert LOG_FORMAT == "[%(asctime)s][%(levelname)s]: %(message)s"

    def test_info_lines_are_kept(self, tmp_path, restore_root_logger):
        """Without a configured root the three self-serving loggers lose these."""
        configure_logging(str(tmp_path), "tok")
        logging.getLogger("some.module.that.fetched.its.own").info("schema migration ran")

        assert "schema migration ran" in (tmp_path / LOG_FILENAME).read_text()

    def test_it_also_writes_to_stderr(self, tmp_path, restore_root_logger):
        root = configure_logging(str(tmp_path), "tok")

        streams = [
            h.stream
            for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        ]
        assert streams == [sys.stderr]

    def test_nothing_writes_to_stdout(self, tmp_path, restore_root_logger):
        """A handler on stdout would corrupt the core probe's JSON answer."""
        root = configure_logging(str(tmp_path), "tok")

        assert not any(getattr(h, "stream", None) is sys.stdout for h in root.handlers)

    def test_rotation_is_by_size(self, tmp_path, restore_root_logger):
        """One file per start would become one enormous file for a long-lived service."""
        root = configure_logging(str(tmp_path), "tok")

        rotating = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(rotating) == 1
        assert (rotating[0].maxBytes, rotating[0].backupCount) == (MAX_LOG_BYTES, LOG_BACKUP_COUNT)

    def test_a_second_call_replaces_rather_than_doubles(self, tmp_path, restore_root_logger):
        configure_logging(str(tmp_path), "tok")
        root = configure_logging(str(tmp_path), "tok")
        root.info("once")

        assert (tmp_path / LOG_FILENAME).read_text().count("once") == 1


class TestTokenRedaction:
    def test_the_token_never_reaches_the_file(self, tmp_path, restore_root_logger):
        root = configure_logging(str(tmp_path), "s3cr3t-token")

        root.info("host: load the panel from http://127.0.0.1:27737/index.js?token=s3cr3t-token")

        written = (tmp_path / LOG_FILENAME).read_text()
        assert "s3cr3t-token" not in written
        assert "***" in written

    def test_the_filter_is_on_the_file_handler_not_the_root(self, tmp_path, restore_root_logger):
        """The start-up address must still reach stderr — that is the deliberate exception."""
        root = configure_logging(str(tmp_path), "s3cr3t-token")

        assert not any(isinstance(f, TokenRedactionFilter) for f in root.filters)
        rotating = next(h for h in root.handlers if isinstance(h, RotatingFileHandler))
        assert any(isinstance(f, TokenRedactionFilter) for f in rotating.filters)

    def test_it_redacts_a_token_in_a_formatting_argument(self):
        record = logging.LogRecord("t", logging.INFO, "f", 1, "address %s", ("?token=s3cr3t",), None)

        TokenRedactionFilter("s3cr3t").filter(record)

        assert "s3cr3t" not in record.getMessage()

    def test_it_leaves_a_record_without_the_token_alone(self):
        record = logging.LogRecord("t", logging.INFO, "f", 1, "nothing secret", None, None)

        TokenRedactionFilter("s3cr3t").filter(record)

        assert record.getMessage() == "nothing secret"

    def test_it_never_drops_a_record(self):
        record = logging.LogRecord("t", logging.INFO, "f", 1, "?token=s3cr3t", None, None)

        assert TokenRedactionFilter("s3cr3t").filter(record) is True

    def test_an_empty_token_redacts_nothing(self):
        record = logging.LogRecord("t", logging.INFO, "f", 1, "some message", None, None)

        assert TokenRedactionFilter("").filter(record) is True
        assert record.getMessage() == "some message"

    def test_a_non_string_argument_survives(self):
        record = logging.LogRecord("t", logging.INFO, "f", 1, "count %d", (7,), None)

        TokenRedactionFilter("s3cr3t").filter(record)

        assert record.getMessage() == "count 7"
