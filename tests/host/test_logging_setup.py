"""The root logger this process configures, and the token it keeps out of the file."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from host.logging_setup import (
    LOG_BACKUP_COUNT,
    LOG_FILENAME,
    LOG_FORMAT,
    MAX_LOG_BYTES,
    RedactingFormatter,
    configure_logging,
)


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
    """The file is redacted and stderr is not — asserted on the OUTPUT of each.

    Every case here reads what a handler actually wrote. The earlier version of
    this file asserted where the redaction was *installed*, which stayed green
    while the start-up address came out of the terminal already starred: the
    redaction ran on the shared record, so whichever handler formatted second
    saw the rewritten message.
    """

    def test_the_token_never_reaches_the_file(self, tmp_path, restore_root_logger):
        root = configure_logging(str(tmp_path), "s3cr3t-token")

        root.info("host: load the panel from http://127.0.0.1:27737/index.js?token=s3cr3t-token")

        written = (tmp_path / LOG_FILENAME).read_text()
        assert "s3cr3t-token" not in written
        assert "***" in written

    def test_the_start_up_address_reaches_stderr_in_full(self, tmp_path, restore_root_logger, capsys):
        """The one deliberate exception, and the reason the development loop works at all."""
        root = configure_logging(str(tmp_path), "s3cr3t-token")

        root.info("host: load the panel from http://127.0.0.1:27737/index.js?token=s3cr3t-token")

        assert "?token=s3cr3t-token" in capsys.readouterr().err

    def test_the_token_is_redacted_in_a_formatting_argument(self, tmp_path, restore_root_logger):
        """The file sees the rendered line, so a token in an argument is caught too."""
        root = configure_logging(str(tmp_path), "s3cr3t-token")

        root.warning("address %s", "?token=s3cr3t-token")

        assert "s3cr3t-token" not in (tmp_path / LOG_FILENAME).read_text()

    def test_that_same_argument_reaches_stderr_whole(self, tmp_path, restore_root_logger, capsys):
        root = configure_logging(str(tmp_path), "s3cr3t-token")

        root.warning("address %s", "?token=s3cr3t-token")

        assert "?token=s3cr3t-token" in capsys.readouterr().err

    def test_the_record_itself_is_left_alone(self, tmp_path, restore_root_logger):
        """Nothing shared is mutated — which is exactly what the filter form got wrong."""
        configure_logging(str(tmp_path), "s3cr3t-token")
        record = logging.LogRecord("t", logging.INFO, "f", 1, "?token=s3cr3t-token", None, None)

        for handler in logging.getLogger().handlers:
            handler.format(record)

        assert record.getMessage() == "?token=s3cr3t-token"

    def test_a_line_without_the_token_is_unchanged_in_both(self, tmp_path, restore_root_logger, capsys):
        root = configure_logging(str(tmp_path), "s3cr3t-token")

        root.info("nothing secret here")

        assert "nothing secret here" in (tmp_path / LOG_FILENAME).read_text()
        assert "nothing secret here" in capsys.readouterr().err

    def test_an_empty_token_redacts_nothing(self, tmp_path, restore_root_logger):
        root = configure_logging(str(tmp_path), "")

        root.info("some message")

        assert "some message" in (tmp_path / LOG_FILENAME).read_text()

    def test_the_formatter_keeps_the_repos_own_line_shape(self):
        """Redacting must not change the format saved diagnostic walk-throughs grep for."""
        rendered = RedactingFormatter(LOG_FORMAT, "s3cr3t").format(
            logging.LogRecord("t", logging.WARNING, "f", 1, "careful", None, None)
        )

        assert rendered.endswith("[WARNING]: careful")
