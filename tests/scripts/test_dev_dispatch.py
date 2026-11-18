"""Tests for ``scripts/dev_dispatch.sh`` — the forced command of the dev loop's key on a target.

It decides from ``SSH_ORIGINAL_COMMAND`` alone (#2844 D9), so each case runs
the real script with ``sh`` under a home of its own, a request in that variable
and a ``PATH`` of stand-ins (``tests/_remote_target.py``): ``rsync`` records
what it was started with, and nothing else on the ``PATH`` reaches a real
remote tool. A request it lets through is checked by what it ran; a refusal by
its message, its status, and nothing having run.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from _remote_target import RSYNC_RECORD_VARIABLE, fake_path

_DISPATCHER = Path(__file__).resolve().parents[2] / "scripts" / "dev_dispatch.sh"

_REFUSED = 3
_INCOMING = ".local/lib/romm-tender-dev/incoming/"
# What rsync 3.2's client asks the receiving server for under `-a --delete`.
_RECEIVE = f"rsync --server -logDtpre.iLsfxCIvu --delete-during . {_INCOMING}"


def _run(home: Path, tools: Path, request: str | None) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(home),
        "PATH": fake_path(tools),
        RSYNC_RECORD_VARIABLE: str(tools / "rsync-calls"),
        "TENDER_PYTHON": sys.executable,
    }
    if request is not None:
        env["SSH_ORIGINAL_COMMAND"] = request
    return subprocess.run(["sh", str(_DISPATCHER)], cwd="/", capture_output=True, text=True, check=False, env=env)


def _rsync_calls(tools: Path) -> list[list[str]]:
    record = tools / "rsync-calls"
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]


def _dev_target(home: Path) -> Path:
    """An incoming copy's ``scripts/dev_target.sh`` that says what it was given, and where it ran."""
    script = home / ".local/lib/romm-tender-dev/incoming/scripts/dev_target.sh"
    script.parent.mkdir(parents=True)
    script.write_text('printf "ran in %s with:%s\\n" "$PWD" "$*"\n', encoding="utf-8")
    return script


@pytest.mark.parametrize("destination", [_INCOMING, _INCOMING.rstrip("/")])
def test_the_copy_into_the_incoming_tree_reaches_rsync_as_asked(home: Path, tmp_path: Path, destination: str) -> None:
    request = f"rsync --server -logDtpre.iLsfxCIvu --delete-during . {destination}"

    result = _run(home, tmp_path, request)

    assert result.returncode == 0, result.stderr
    assert _rsync_calls(tmp_path) == [request.split()[1:]]


def test_dev_target_sh_runs_from_the_incoming_copy_with_its_words(home: Path, tmp_path: Path) -> None:
    _dev_target(home)

    result = _run(home, tmp_path, "scripts/dev_target.sh restart bpm dp-2 --surface")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"ran in {home} with:restart bpm dp-2 --surface\n"


def test_dev_target_sh_before_any_copy_says_none_has_arrived(home: Path, tmp_path: Path) -> None:
    result = _run(home, tmp_path, "scripts/dev_target.sh restart")

    assert result.returncode == 1
    assert "no copy has arrived yet" in result.stderr


def test_read_log_prints_the_backend_log(home: Path, tmp_path: Path) -> None:
    log = home / ".local/state/romm-tender/backend.log"
    log.parent.mkdir(parents=True)
    log.write_text("one line\nanother\n", encoding="utf-8")

    result = _run(home, tmp_path, "read log")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "one line\nanother\n"


def test_read_database_dumps_it_as_sql_and_leaves_it_as_it_was(home: Path, tmp_path: Path) -> None:
    database = home / ".local/share/romm-tender/romm-tender.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE roms (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO roms (name) VALUES ('Tetris')")
    connection.close()
    before = database.read_bytes()

    result = _run(home, tmp_path, "read database")

    assert result.returncode == 0, result.stderr
    copy = sqlite3.connect(":memory:")
    copy.executescript(result.stdout)
    assert copy.execute("SELECT name FROM roms").fetchall() == [("Tetris",)]
    assert database.read_bytes() == before


@pytest.mark.parametrize(("request_name", "missing"), [("log", "no backend log"), ("database", "no database")])
def test_a_read_of_something_not_there_says_so(home: Path, tmp_path: Path, request_name: str, missing: str) -> None:
    result = _run(home, tmp_path, f"read {request_name}")

    assert result.returncode == 1
    assert missing in result.stderr


@pytest.mark.parametrize(
    "request_text",
    [
        pytest.param(None, id="a login"),
        pytest.param("", id="an empty request"),
        pytest.param("sh -s", id="a shell"),
        pytest.param("cat .ssh/authorized_keys", id="another command"),
        pytest.param("rsync --server --sender -logDtpre.iLsfxCIvu . .local/share/romm-tender/", id="rsync sending"),
        pytest.param("rsync -logDtpre.iLsfxCIvu . " + _INCOMING, id="rsync not as a server"),
        pytest.param("rsync --server -logDtpre.iLsfxCIvu --delete-during . .ssh/", id="rsync into another dir"),
        pytest.param(
            "rsync --server -logDtpre.iLsfxCIvu . .local/lib/romm-tender-dev/", id="rsync over the dispatcher"
        ),
        pytest.param(f"rsync --server -logDtpre.iLsfxCIvu . {_INCOMING}../current", id="rsync up out of incoming"),
        pytest.param(f"rsync --server -logDtpre.iLsfxCIvu . {_INCOMING} .ssh/", id="rsync with two destinations"),
        pytest.param(f"rsync --server -logDtpre.iLsfxCIvu {_INCOMING}", id="rsync without its dot"),
        pytest.param(f"rsync --server -logDtprse.iLsfxCIvu . {_INCOMING}", id="rsync with protected arguments"),
        pytest.param(f"rsync --server -logDtprRe.iLsfxCIvu . {_INCOMING}", id="rsync with relative paths"),
        pytest.param(f"rsync --server -logDtpre.iLs/fxCIvu . {_INCOMING}", id="rsync with a stray character"),
        pytest.param(f"rsync --server -logDtpre.iLsfxCIvu --log-file=/tmp/x . {_INCOMING}", id="rsync logging"),
        pytest.param(f"rsync --server -logDtpre.iLsfxCIvu --backup-dir=/tmp . {_INCOMING}", id="rsync backing up"),
        pytest.param(f"{_RECEIVE}; touch pwned", id="rsync with a command after it"),
        pytest.param("scripts/dev_target.sh restart; touch pwned", id="dev_target.sh with a command after it"),
        pytest.param("scripts/dev_target.sh $(touch pwned)", id="dev_target.sh with a substitution"),
        pytest.param("scripts/dev_target.sh ../../../../pwned", id="dev_target.sh with a path"),
        pytest.param("scripts/dev_target.sh *", id="dev_target.sh with a glob"),
        pytest.param(".local/lib/romm-tender-dev/incoming/scripts/dev_target.sh", id="dev_target.sh by another path"),
        pytest.param("scripts/other.sh", id="another script of the copy"),
        pytest.param("read", id="read nothing"),
        pytest.param("read settings", id="read something else"),
        pytest.param("read log database", id="read two things"),
        pytest.param("read log; touch pwned", id="read with a command after it"),
    ],
)
def test_anything_else_is_refused_and_runs_nothing(home: Path, tmp_path: Path, request_text: str | None) -> None:
    _dev_target(home)
    log = home / ".local/state/romm-tender/backend.log"
    log.parent.mkdir(parents=True)
    log.write_text("secret\n", encoding="utf-8")

    result = _run(home, tmp_path, request_text)

    assert result.returncode == _REFUSED
    assert result.stdout == ""
    assert result.stderr.startswith(f"tender-dev: refused: {request_text or '(a login)'}\n")
    assert _rsync_calls(tmp_path) == []
    assert not (home / "pwned").exists()
    assert not Path("/pwned").exists()
