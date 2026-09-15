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

**The redaction filter sits on the file handler, not on the root.** The address
the panel loads from carries the admission token, and it is printed once at
start-up on stderr so the development loop is usable at all — the terminal for a
hand start, the journal for a service. That line is the deliberate exception;
the file is where a token would outlive the process, so the file is what is
filtered.

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


class TokenRedactionFilter(logging.Filter):
    """Replaces the admission token wherever it appears in a record's message.

    A filter rather than a discipline: an address with a token in it reaches the
    log through whatever formats it, so a rule that every call site must remember
    would be a rule nobody could check. This one is checked by being the only
    path to the file.
    """

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the token in place; never drops a record."""
        if not self._token:
            return True
        if isinstance(record.msg, str) and self._token in record.msg:
            record.msg = record.msg.replace(self._token, _REDACTED)
        if record.args:
            record.args = tuple(
                argument.replace(self._token, _REDACTED) if isinstance(argument, str) else argument
                for argument in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True


def configure_logging(log_dir: str, token: str, level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger to write to *log_dir* and to stderr; return it.

    Call once, from the entry point. Existing root handlers are removed first so
    a second call replaces rather than doubles — a doubled handler writes every
    line twice and rotates at half the size it should.
    """
    os.makedirs(log_dir, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, LOG_FILENAME),
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(TokenRedactionFilter(token))

    # stderr, never stdout — see the module docstring. Under systemd this is the
    # journal with nothing further to configure.
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.setLevel(level)
    return root
