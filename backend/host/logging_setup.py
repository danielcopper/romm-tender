"""Configuring the root logger — done once, from the entry point, never on import.

Contract: the logging this process does. The plugin loader used to own the root
logger, and three modules in this backend reach for a logger of their own rather
than being handed one; without a configured root those three fall back to
Python's last resort — stderr, WARNING and above — and their INFO lines simply
stop existing, the schema migration's among them.

**Configuration happens in the host's entry point and nowhere else.** Doing it as
an import side effect would reconfigure whatever imported the module, and the
test suite is the first thing that would: clearing the root's handlers under a
running suite is invisible until some other test's assertion about log output
stops holding.

**Rotation is by size, not by run.** The loader wrote one file per start; a
process that may run for weeks would make that one enormous file.

**The redaction belongs to the file handler's FORMATTER, not to a filter.** The
address the panel loads from carries the admission token, and it is printed once
at start-up on stderr so the development loop is usable at all — the terminal for
a hand start, the journal for a service. That line is the deliberate exception;
the file is where a token would outlive the process, so the file is what is
redacted.

A ``logging.Filter`` cannot express that, and the way it fails is silent. A
filter sees the record every handler shares, so redacting there rewrites the
message for stderr as well — and whether it does depends on the order the
handlers were added, which nothing in the code reads as an ordering decision.
A formatter is per handler by construction.

**Nothing here may ever write to stdout.** The vendored resolver's core probe
runs a child interpreter and this process reads that child's stdout as JSON. A
handler on stdout would put log lines into the middle of it and the answer would
stop parsing — a failure that looks like a broken probe rather than like
logging.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# Verbatim what the loader used, because saved diagnostic walk-throughs grep
# against this shape.
LOG_FORMAT = "[%(asctime)s][%(levelname)s]: %(message)s"

LOG_FILENAME = "backend.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

_REDACTED = "***"


class RedactingFormatter(logging.Formatter):
    """Formats a record, then removes the admission token from the RESULT.

    The redaction happens on this handler's own output string and touches
    nothing else. That is the whole point: a ``logging.Filter`` sees the record
    every handler shares, so redacting there rewrites the message for the stderr
    handler too — and the one line this program deliberately prints in full, the
    address the panel is loaded from, came out of the terminal already starred.
    A formatter cannot do that: each handler formats for itself.

    Working on the formatted string also catches the token wherever it ended up
    — inside a ``%s`` argument, inside an exception's text — rather than only in
    the parts a filter thought to look at.
    """

    def __init__(self, fmt: str, token: str) -> None:
        super().__init__(fmt)
        self._token = token

    def format(self, record: logging.LogRecord) -> str:
        """Render *record* for this handler, with the token taken out."""
        rendered = super().format(record)
        return rendered.replace(self._token, _REDACTED) if self._token else rendered


def configure_logging(log_dir: str, token: str, level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger to write to *log_dir* and to stderr; return it.

    Call once, from the entry point. Existing root handlers are removed first so
    a second call replaces rather than doubles — a doubled handler writes every
    line twice and rotates at half the size it should.

    *level* is the root's for the life of the process: the entry point passes
    none and no user-facing setting reaches this call, so a DEBUG record judged
    against the root's level is dropped rather than written.

    Both handlers sit at ``NOTSET``, which is a separate axis from the root's
    level: a record some logger's own level has already admitted is handled
    whatever that level was, so a child levelled below the root is written here
    rather than filtered a second time.
    """
    os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, LOG_FILENAME),
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(RedactingFormatter(LOG_FORMAT, token))

    # stderr, never stdout — see the module docstring. Under systemd this is the
    # journal with nothing further to configure. Its formatter is the plain one:
    # this is the handler the start-up address is printed FOR.
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.setLevel(level)
    return root
