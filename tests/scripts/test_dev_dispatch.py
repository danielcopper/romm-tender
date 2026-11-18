"""Tests for ``scripts/dev_dispatch.sh`` — the forced command of the dev loop's key on a target.

It decides from ``SSH_ORIGINAL_COMMAND`` alone (#2844 D9), so each case runs
the real script with ``sh`` under a home of its own — copied to where
setup:remote-target installs it, since it finds its directories from its own
location — with a request in that variable
and a ``PATH`` of stand-ins (``tests/_remote_target.py``): ``rsync`` records
what it was started with, and nothing else on the ``PATH`` reaches a real
remote tool. A request it lets through is checked by what it ran; a refusal by
its message, its status, and nothing having run.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from _remote_target import RSYNC_RECORD_VARIABLE, fake_path

_DISPATCHER = Path(__file__).resolve().parents[2] / "scripts" / "dev_dispatch.sh"

_REFUSED = 3
_INCOMING = ".local/lib/romm-tender-dev/incoming/"
# What rsync 3.2's client asks the receiving server for under `-a --delete`.
_RECEIVE = f"rsync --server -logDtpre.iLsfxCIvu --delete-during . {_INCOMING}"


def _run(
    home: Path, tools: Path, request: str | None, stdin: str = "", **variables: str
) -> subprocess.CompletedProcess[str]:
    installed = home / ".local/lib/romm-tender-dev/dispatch.sh"
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_DISPATCHER, installed)
    env = {
        "HOME": str(home),
        "PATH": fake_path(tools),
        RSYNC_RECORD_VARIABLE: str(tools / "rsync-calls"),
        "TENDER_PYTHON": sys.executable,
        **variables,
    }
    if request is not None:
        env["SSH_ORIGINAL_COMMAND"] = request
    return subprocess.run(
        ["sh", str(installed)], cwd="/", input=stdin, capture_output=True, text=True, check=False, env=env
    )


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


@pytest.mark.parametrize(
    "request_text",
    [
        f"rsync --server -logDtpre.iLsfxCIvu --delete --partial --numeric-ids . {_INCOMING}",
        f"rsync --server -rlptgoDcz --delete-after --force --safe-links . {_INCOMING}",
    ],
    ids=["delete-partial-numeric-ids", "no-capabilities-delete-after-force-safe-links"],
)
def test_the_long_options_a_copy_may_carry_reach_rsync(home: Path, tmp_path: Path, request_text: str) -> None:
    result = _run(home, tmp_path, request_text)

    assert result.returncode == 0, result.stderr
    assert _rsync_calls(tmp_path) == [request_text.split()[1:]]


@pytest.mark.parametrize("destination", [_INCOMING, _INCOMING.rstrip("/")])
def test_the_copy_into_the_incoming_tree_reaches_rsync_as_asked(home: Path, tmp_path: Path, destination: str) -> None:
    request = f"rsync --server -logDtpre.iLsfxCIvu --delete-during . {destination}"

    result = _run(home, tmp_path, request)

    assert result.returncode == 0, result.stderr
    assert _rsync_calls(tmp_path) == [request.split()[1:]]


def test_the_setup_request_runs_the_script_on_its_stdin_in_the_home(home: Path, tmp_path: Path) -> None:
    """#2844 D11: setup:remote-target run again repairs the target through the key."""
    result = _run(home, tmp_path, "sh -s", stdin='printf "%s|%s" "$PWD" "$#"; printf installed > repaired\n')

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{home}|0"
    assert (home / "repaired").read_text(encoding="utf-8") == "installed"


def test_installed_elsewhere_it_takes_its_root_from_where_it_lies(home: Path, tmp_path: Path) -> None:
    elsewhere = home / "other" / "dispatch.sh"
    elsewhere.parent.mkdir()
    shutil.copyfile(_DISPATCHER, elsewhere)
    env = {"HOME": str(home), "PATH": fake_path(tmp_path), RSYNC_RECORD_VARIABLE: str(tmp_path / "rsync-calls")}

    def dispatch(request: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(elsewhere)],
            cwd="/",
            capture_output=True,
            text=True,
            check=False,
            env={**env, "SSH_ORIGINAL_COMMAND": request},
        )

    taken = dispatch("rsync --server -logDtpre.iLsfxCIvu . other/incoming/")
    refused = dispatch(f"rsync --server -logDtpre.iLsfxCIvu . {_INCOMING}")

    assert taken.returncode == 0, taken.stderr
    assert refused.returncode == _REFUSED
    assert _rsync_calls(tmp_path) == [["--server", "-logDtpre.iLsfxCIvu", ".", "other/incoming/"]]


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


def test_follow_log_follows_the_backend_log_by_name(home: Path, tmp_path: Path) -> None:
    """#2846 D2: the second read request, for dev:log --follow — the system's own tail, following by name."""
    log = home / ".local/state/romm-tender/backend.log"
    log.parent.mkdir(parents=True)
    log.write_text("".join(f"line {number}\n" for number in range(15)), encoding="utf-8")
    installed = home / ".local/lib/romm-tender-dev/dispatch.sh"
    installed.parent.mkdir(parents=True)
    shutil.copyfile(_DISPATCHER, installed)
    real_tail = tmp_path / "real"
    real_tail.mkdir()
    found = shutil.which("tail", path="/usr/bin:/bin")
    assert found is not None
    (real_tail / "tail").symlink_to(found)
    env = {"HOME": str(home), "PATH": f"{real_tail}:{fake_path(tmp_path)}", "SSH_ORIGINAL_COMMAND": "follow log"}
    process = subprocess.Popen(
        ["sh", str(installed)], cwd="/", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    # A tail that stops following ends the test rather than leaving it waiting.
    watchdog = threading.Timer(15, process.kill)
    watchdog.start()
    try:
        assert process.stdout is not None
        shown = [process.stdout.readline() for _ in range(10)]
        log.rename(log.with_name("backend.log.1"))
        log.write_text("after the rotation\n", encoding="utf-8")
        followed = process.stdout.readline()
    finally:
        watchdog.cancel()
        process.kill()
        process.communicate()

    assert shown == [f"line {number}\n" for number in range(5, 15)]
    assert followed == "after the rotation\n"


@pytest.mark.parametrize(
    ("variables", "folder"),
    [
        ({"TENDER_STATE_DIR": "{home}/tender-state"}, "tender-state"),
        ({"XDG_STATE_HOME": "{home}/xdg-state"}, "xdg-state/romm-tender"),
        ({"TENDER_STATE_DIR": "{home}/tender-state", "XDG_STATE_HOME": "{home}/xdg-state"}, "tender-state"),
    ],
    ids=["tender", "xdg", "tender-before-xdg"],
)
def test_read_log_follows_install_sh_s_ladder(
    home: Path, tmp_path: Path, variables: dict[str, str], folder: str
) -> None:
    log = home / folder / "backend.log"
    log.parent.mkdir(parents=True)
    log.write_text("from the ladder\n", encoding="utf-8")

    result = _run(home, tmp_path, "read log", **{name: value.format(home=home) for name, value in variables.items()})

    assert result.returncode == 0, result.stderr
    assert result.stdout == "from the ladder\n"


def test_read_database_takes_tender_data_dir_first(home: Path, tmp_path: Path) -> None:
    database = home / "tender-data" / "romm-tender.db"
    database.parent.mkdir(parents=True)
    sqlite3.connect(database).close()

    result = _run(
        home, tmp_path, "read database", TENDER_DATA_DIR=str(database.parent), XDG_DATA_HOME=str(home / "xdg")
    )

    assert result.returncode == 0, result.stderr
    assert "BEGIN TRANSACTION;" in result.stdout


class TestTheNamesAreTheBackends:
    """Each name the dispatcher reads by, held to the backend's own."""

    def test_the_log_the_database_and_their_folder(self) -> None:
        from bootstrap.adapters import DB_FILENAME
        from domain.user_data_location import APP_DIR_NAME
        from host.logging_setup import LOG_FILENAME

        script = _DISPATCHER.read_text(encoding="utf-8")

        assert f'APP_DIR="{APP_DIR_NAME}"' in script
        assert f'LOG_NAME="{LOG_FILENAME}"' in script
        assert f'DATABASE_NAME="{DB_FILENAME}"' in script


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
        pytest.param("sh", id="a shell"),
        pytest.param("sh -c touch pwned", id="a shell with a command"),
        pytest.param("sh -s pwned", id="the setup request with an argument"),
        pytest.param("bash -s", id="another shell on stdin"),
        pytest.param("setup", id="setup by name"),
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
        pytest.param(f"rsync --server -logDtprKe.iLsfxCIvu . {_INCOMING}", id="rsync keeping dirlinks"),
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
        pytest.param("follow", id="follow nothing"),
        pytest.param("follow database", id="follow the database"),
        pytest.param("follow log -n 1000000", id="follow with options"),
        pytest.param("follow log; touch pwned", id="follow with a command after it"),
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
