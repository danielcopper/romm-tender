"""Tests for ``install.sh`` — the one command that puts Tender on a machine.

Every case runs the real script, and none of them touches the machine running
them: ``HOME`` is a ``tmp_path``, the six ``TENDER_*`` directory variables put
every root under it, the environment is built from a fixed set rather than
inherited — ``PATH`` is the one value taken from this process, and a stub
directory is prepended to it so that ``systemctl`` and ``curl`` resolve to
stand-ins. The
stubs record what they were asked for, which is what lets a test assert the URL
that was built and the unit that was enabled rather than only the files left
behind.

The tarball under test is built by ``scripts/package.sh`` from a synthetic
checkout, so what ``--from`` installs is the layout the packager actually
produces rather than one this file invented.

**One tier runs the script on a real terminal** (``Install.on_a_terminal``), because the
rest of the file cannot: every other case starts the script in a session of its
own, so ``/dev/tty`` cannot be opened and the acknowledgement's prompt is never
reached. Everything on the far side of that check — the prompt, the answer, and
whether this script still has a stderr afterwards — is invisible without one.
"""

from __future__ import annotations

import hashlib
import os
import pty
import re
import selectors
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_INSTALL = _REPO / "install.sh"
_PACKAGE = _REPO / "scripts" / "package.sh"

_VERSION = "1.2.3"
_TAG = f"tender-v{_VERSION}"
_ARCHIVE = f"romm-tender-{_VERSION}.tar.gz"
_DOWNLOAD_BASE = "https://example.invalid/releases/download"
_RELEASE_API = "https://example.invalid/repos/releases/latest"
_DEBUGGER_PROBE = "http://127.0.0.1:8080/json/version"
_MIGRATION_NOTES = "https://danielcopper.github.io/romm-tender/user-guide/getting-started/#coming-from-the-decky-plugin"

# What the packager is handed. Only the three entries install.sh requires need
# real content; the rest have to exist because the packager refuses a tree that
# is missing any shipped entry.
_CHECKOUT_FILES = (
    "backend/main.py",
    "dist/index.js",
    "dist/globals.js",
    "dist/index-coexistence.js",
    "bin/tender-rom-launcher",
    "defaults/config.json",
    "LICENSE",
    "THIRD-PARTY-NOTICES.md",
)

_SYSTEMCTL_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_SYSTEMCTL_LOG"
case "$*" in
    *"show-environment"*) exit "${STUB_USER_MANAGER_EXIT:-0}" ;;
    *"list-unit-files plugin_loader.service"*)
        [ -z "${STUB_DECKY_UNIT:-}" ] || printf '%s\\n' "$STUB_DECKY_UNIT"
        exit 0
        ;;
    *"is-active"*) exit "${STUB_UNIT_ACTIVE:-3}" ;;
    *"enable"*) [ -z "${STUB_ENABLE_DELAY:-}" ] || sleep "$STUB_ENABLE_DELAY" ;;
esac
exit 0
"""

_CURL_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_CURL_ARGV_LOG"
out=""
url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --max-time) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
printf '%s\\n' "$url" >> "$STUB_CURL_LOG"
if [ "$url" = "$STUB_DEBUGGER_URL" ]; then
    [ "${STUB_DEBUGGER:-silent}" = "answer" ] || exit 22
    echo '{"Browser":"stub"}'
    exit 0
fi
source="$STUB_SERVE/${url##*/}"
[ -f "$source" ] || exit 22
if [ -n "$out" ]; then cp "$source" "$out"; else cat "$source"; fi
"""

_PGREP_STUB = """#!/usr/bin/env bash
# Answers for `steam` only, and only when the test says it is up.
[ "${STUB_STEAM_RUNNING:-no}" = "yes" ] || exit 1
printf '4242\\n'
"""

_PYTHON_STUB = """#!/usr/bin/env bash
printf 'python %s\\n' "${STUB_PYTHON_VERSION:-3.13}"
exit "${STUB_PYTHON_EXIT:-0}"
"""


class Install:
    """One machine under test: its home, its stubs, and a way to run the script."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.home = tmp_path / "home"
        self.stubs = tmp_path / "stubs"
        self.serve = tmp_path / "serve"
        self.code = tmp_path / "code"
        self.config = tmp_path / "config"
        self.data = tmp_path / "data"
        self.cache = tmp_path / "cache"
        self.state = tmp_path / "state"
        self.bin = tmp_path / "bin"
        self.runtime = tmp_path / "run"
        self.systemctl_log = tmp_path / "systemctl.log"
        self.curl_log = tmp_path / "curl.log"
        self.curl_argv_log = tmp_path / "curl-argv.log"
        self.python = self.stubs / "stub-python3"

        for directory in (self.home, self.stubs, self.serve, self.runtime):
            directory.mkdir(parents=True, exist_ok=True)
        self.systemctl_log.touch()
        self.curl_log.touch()
        self.curl_argv_log.touch()
        _write_executable(self.stubs / "systemctl", _SYSTEMCTL_STUB)
        _write_executable(self.stubs / "curl", _CURL_STUB)
        _write_executable(self.python, _PYTHON_STUB)
        _write_executable(self.stubs / "pgrep", _PGREP_STUB)

        self.steam_root = self.home / ".local" / "share" / "Steam"
        self.steam_root.mkdir(parents=True)

    @property
    def unit(self) -> Path:
        return self.home / ".config" / "systemd" / "user" / "romm-tender.service"

    @property
    def marker(self) -> Path:
        return self.steam_root / ".cef-enable-remote-debugging"

    @property
    def note(self) -> Path:
        return self.state / "debugger-marker"

    def env(self, **extra: str) -> dict[str, str]:
        base = {
            "PATH": f"{self.stubs}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(self.home),
            # A real terminal is in a UTF-8 locale, and that is what decides
            # whether the mark and the row marks are drawn as glyphs. The cases
            # that test the other answer override it.
            "LANG": "C.UTF-8",
            "XDG_RUNTIME_DIR": str(self.runtime),
            "TENDER_PYTHON": str(self.python),
            "TENDER_CODE_DIR": str(self.code),
            "TENDER_CONFIG_DIR": str(self.config),
            "TENDER_DATA_DIR": str(self.data),
            "TENDER_CACHE_DIR": str(self.cache),
            "TENDER_STATE_DIR": str(self.state),
            "TENDER_BIN_DIR": str(self.bin),
            "TENDER_RELEASE_API": _RELEASE_API,
            "TENDER_DOWNLOAD_BASE": _DOWNLOAD_BASE,
            "STUB_SYSTEMCTL_LOG": str(self.systemctl_log),
            "STUB_CURL_LOG": str(self.curl_log),
            "STUB_CURL_ARGV_LOG": str(self.curl_argv_log),
            "STUB_SERVE": str(self.serve),
            "STUB_DEBUGGER_URL": _DEBUGGER_PROBE,
        }
        base.update(extra)
        return base

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run the installer with no controlling terminal, the way ``curl | bash`` does."""
        return subprocess.run(
            ["bash", str(_INSTALL), *args],
            capture_output=True,
            text=True,
            check=False,
            env=self.env(**extra),
            start_new_session=True,
        )

    def run_raw(self, *args: str, **extra: str) -> subprocess.CompletedProcess[bytes]:
        """The same run, captured as BYTES.

        ``text=True`` decodes with universal newlines, which rewrites every
        ``\r`` to ``\n`` before a test can look — so an assertion about carriage
        returns made on a decoded run cannot fail, whatever the script wrote.
        Anything asking what bytes reached the pipe has to ask here.
        """
        return subprocess.run(
            ["bash", str(_INSTALL), *args],
            capture_output=True,
            check=False,
            env=self.env(**extra),
            start_new_session=True,
        )

    def on_a_terminal(self, *args: str, answer: str = "yes", **extra: str) -> tuple[int, str]:
        """Run the installer with a controlling terminal, and type *answer* at its prompt.

        ``pty.fork`` rather than a pipe: the acknowledgement opens ``/dev/tty``,
        which resolves only for a process that HAS a controlling terminal, and a
        pipe on stdin does not give it one. The child becomes a session leader
        with the pty attached, which is what a user running this in a shell has.

        Answers with the exit status and everything that reached the terminal —
        one stream, because a terminal is one stream.
        """
        env = self.env(**extra)
        pid, master = pty.fork()
        if pid == 0:  # pragma: no cover - the child execs before it can be measured
            try:
                os.execve("/bin/bash", ["bash", str(_INSTALL), *args], env)
            finally:
                os._exit(127)
        os.write(master, f"{answer}\n".encode())
        return _drain(pid, master)

    def systemctl_calls(self) -> list[str]:
        return self.systemctl_log.read_text(encoding="utf-8").split("\n")[:-1]

    def curl_calls(self) -> list[str]:
        return self.curl_log.read_text(encoding="utf-8").split("\n")[:-1]

    def curl_argv(self) -> list[str]:
        """Every curl invocation's whole argument line, for the flags the URL does not show."""
        return self.curl_argv_log.read_text(encoding="utf-8").split("\n")[:-1]

    def publish_release(self, *, tag: str = _TAG, archive: str | None = None, checksum: bool = True) -> Path:
        """Put a real tarball where the stubbed curl will serve it from."""
        name = archive or f"romm-tender-{tag.removeprefix('tender-v')}.tar.gz"
        built = _build_tarball(self.tmp_path)
        (self.serve / name).write_bytes(built.read_bytes())
        if checksum:
            digest = hashlib.sha256(built.read_bytes()).hexdigest()
            (self.serve / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
        (self.serve / "latest").write_text(f'{{"tag_name": "{tag}"}}\n', encoding="utf-8")
        return self.serve / name


def _drain(pid: int, master: int, timeout: float = 30.0) -> tuple[int, str]:
    """Read the terminal until the child is gone, then reap it.

    A pty's master reports ``EIO`` rather than end-of-file once the last slave
    is closed, so that is the read this loop ends on. *timeout* is an IDLE
    timeout rather than a deadline — it bounds one silent wait, not the run — and
    it is a backstop: a script that sat waiting for an answer would otherwise
    hang the suite instead of failing it.
    """
    selector = selectors.DefaultSelector()
    selector.register(master, selectors.EVENT_READ)
    chunks: list[bytes] = []
    try:
        while True:
            if not selector.select(timeout):
                break
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
    finally:
        selector.close()
        os.close(master)
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    return code, b"".join(chunks).decode(errors="replace")


_ANSI = re.compile(r"\x1b\[([\d;]*)([A-Za-z])")


def _screen(transcript: str) -> str:
    """What the terminal SHOWS after replaying *transcript*, colour removed.

    The installer rewrites its four rows in place, so the bytes that reached the
    pty are not what a reader sees: a row appears once per frame the spinner
    drew and the marks sit inside colour escapes, which is why a plain
    ``in output`` test cannot ask what the run ended up saying. Replaying the
    carriage returns, the line clears and the cursor moves answers that, and it
    is the only way to assert that something was drawn OVER — the evidence for
    which is the absence of what used to be there.

    Handles exactly what this script writes: ``\r``, ``\n``, ``\033[K``,
    ``\033[<n>A`` and SGR colour, which is dropped.
    """
    rows: list[str] = [""]
    row = column = 0
    position = 0
    while position < len(transcript):
        match = _ANSI.match(transcript, position)
        if match:
            kind = match.group(2)
            count = int(match.group(1) or 1) if match.group(1).isdigit() else 1
            if kind == "A":
                row = max(0, row - count)
            elif kind == "B":
                row += count
            elif kind == "K":
                while len(rows) <= row:
                    rows.append("")
                rows[row] = rows[row][:column]
            position = match.end()
            continue
        character = transcript[position]
        position += 1
        if character == "\r":
            column = 0
        elif character == "\n":
            row += 1
            column = 0
        else:
            while len(rows) <= row:
                rows.append("")
            line = rows[row].ljust(column)
            rows[row] = line[:column] + character + line[column + 1 :]
            column += 1
    return "\n".join(line.rstrip() for line in rows)


def _icon_escapes(transcript: str) -> str:
    """Everything the run wrote before the warning, which is where the icon is.

    The rows after it carry escapes of their own — the marks, the success line —
    and those are coloured whatever the icon is, so a test that asked the whole
    transcript would be answering about them.
    """
    return transcript.split("Coming from the Decky plugin?", 1)[0]


def _block_rows(name: str) -> list[str]:
    """One generated array's rows, as install.sh carries them.

    Read out of the script rather than written here, because the whole point of
    generating the art is that no copy of it is maintained by hand — and a test
    holding a second copy would be exactly that.
    """
    text = _INSTALL.read_text(encoding="utf-8")
    block = text[text.index(f"{name}=(") :]
    return [line.strip() for line in block[: block.index(")\n")].splitlines()[1:] if line.strip()]


def _icon_rows() -> list[str]:
    """The half-block icon's rows as a reader sees them: glyphs, no colour."""
    rows = []
    for line in _block_rows("LOGO_ICON_TRUECOLOR"):
        body = line.removeprefix("$'").removesuffix("'")
        rows.append(re.sub(r"\\033\[[0-9;]*m", "", body).rstrip())
    return rows


def _stacked(transcript: str) -> bool:
    """Whether the run put the text block UNDER the drawing rather than beside it.

    Beside the drawing the block hangs from partway DOWN it, so a title below
    the icon's first row is what both layouts look like — only a title below
    its LAST row tells them apart.
    """
    screen = _screen(transcript).splitlines()
    drawn = _icon_rows()
    icon_at = next(index for index, line in enumerate(screen) if line.startswith(drawn[0]))
    title_at = next(index for index, line in enumerate(screen) if "TENDER" in line)
    return title_at > icon_at + len(drawn) - 1


def _ascii_rows() -> list[str]:
    """The ASCII drawing's rows, colour runs removed."""
    rows = []
    for line in _block_rows("LOGO_ASCII"):
        runs = line.strip("'").split("|")
        rows.append("".join(run.split(":", 1)[1] for run in runs).rstrip())
    return rows


def _refusals(stderr: str) -> list[str]:
    """Every line on which this script refused — one per abort, by its prefix.

    A run that refuses TWICE is the shape a `$(...)`-swallowed `exit` leaves
    behind: the first refusal ends a subshell, the caller carries on with an
    empty answer, and refuses again further down about the emptiness. Counting
    the lines is what tells that apart from refusing once, because both exit 1.
    """
    return [line for line in stderr.splitlines() if line.startswith("install.sh: ")]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _build_tarball(tmp_path: Path) -> Path:
    """A real release tarball, produced by the real packager from a synthetic checkout."""
    source = tmp_path / "checkout"
    if not source.exists():
        for relative in _CHECKOUT_FILES:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{relative}\n", encoding="utf-8")
        (source / "version.txt").write_text(f"{_VERSION}\n", encoding="utf-8")
        (source / "bin" / "tender-rom-launcher").chmod(0o755)
        subprocess.run(
            ["bash", str(_PACKAGE), "--source", str(source), "--out", str(tmp_path / "built")],
            capture_output=True,
            text=True,
            check=True,
        )
    return tmp_path / "built" / _ARCHIVE


@pytest.fixture
def machine(tmp_path) -> Install:
    return Install(tmp_path)


class TestTheArguments:
    """What the script does with an option it cannot read: refuses it by name, once."""

    @pytest.mark.parametrize("flag", ["--version", "--from"])
    def test_an_option_with_no_value_is_refused_once_by_name(self, machine, flag):
        result = machine.run(flag)

        assert result.returncode == 1
        assert _refusals(result.stderr) == [f"install.sh: {flag} needs a value"]
        assert not machine.code.exists()

    @pytest.mark.parametrize("flag", ["--version", "--from"])
    def test_an_empty_value_is_not_a_value(self, machine, flag):
        """``--version ""`` used to read as no version at all and install the newest release."""
        result = machine.run(flag, "", "--yes")

        assert result.returncode == 1
        assert _refusals(result.stderr) == [f"install.sh: {flag} needs a value"]
        assert machine.curl_calls() == []
        assert not machine.code.exists()

    def test_a_value_that_looks_like_a_flag_still_reaches_the_option(self, machine):
        """Edge: ``echo`` would have swallowed ``-n`` as a flag of its own.

        The cost is the same as an empty value's — a version silently unasked
        for — so the test is that the newest release is never looked up.
        """
        result = machine.run("--version", "-n", "--yes")

        assert result.returncode == 1
        assert machine.curl_calls() == [f"{_DOWNLOAD_BASE}/tender-v-n/romm-tender--n.tar.gz"]
        assert _RELEASE_API not in machine.curl_calls()

    def test_an_unknown_argument_is_refused(self, machine):
        result = machine.run("--sideways")

        assert result.returncode == 1
        assert _refusals(result.stderr) == ["install.sh: unknown argument: --sideways"]

    def test_help_asks_for_nothing_and_writes_nothing(self, machine):
        result = machine.run("--help")

        assert result.returncode == 0
        assert "Usage: install.sh" in result.stdout
        assert machine.systemctl_calls() == []
        assert not machine.code.exists()


class TestAFreshInstall:
    def test_the_program_lands_at_the_code_root(self, machine):
        tarball = _build_tarball(machine.tmp_path)

        result = machine.run("--from", str(tarball), "--yes")

        assert result.returncode == 0, result.stderr
        assert (machine.code / "backend" / "main.py").is_file()
        assert (machine.code / "dist" / "index.js").is_file()
        assert (machine.code / "bin" / "tender-rom-launcher").is_file()
        assert not (machine.code / "romm-tender").exists()

    def test_the_four_rows_report_the_whole_run_in_order(self, machine):
        """The run is four rows, and each says what it did rather than only that it ran."""
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 0, result.stderr
        rows = [
            "[ok] Checking     python 3.13",
            f"[ok] Installing   {_ARCHIVE}",
            "[ok] Service      romm-tender.service",
            "[--] Steam        not running",
        ]
        at = [result.stdout.index(row) for row in rows]
        assert at == sorted(at), "the rows are reported in the order they run"

    def test_the_service_row_names_the_port_once_the_backend_has_bound_one(self, machine):
        """The backend's own note of the port it bound, which is the proof it came up.

        Usually absent: this run has only just asked systemd to start the unit,
        and the note is written once the backend is up. The row says what it can
        see either way rather than claiming the stronger answer.
        """
        port_dir = machine.runtime / "romm-tender"
        port_dir.mkdir(parents=True, exist_ok=True)
        (port_dir / "port").write_text("27737\n", encoding="utf-8")

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 0, result.stderr
        assert "[ok] Service      romm-tender.service running on 127.0.0.1:27737" in result.stdout

    def test_the_service_row_says_what_it_can_see_with_no_port_file(self, machine):
        """The ordinary case on a fresh install, and not a fault."""
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert "[ok] Service      romm-tender.service enabled and started" in result.stdout

    def test_the_checking_row_names_each_answer_as_it_gets_it(self, machine):
        """A row that only said "checking" would be four seconds of nothing."""
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert "python 3.13 - systemd - Steam - no Tender plugin in Decky" in result.stdout

    def test_a_piped_run_carries_no_carriage_returns(self, machine):
        """No terminal, no spinner: every row is written once, forwards.

        Asked of the raw bytes. Universal newlines turn every ``\r`` into
        ``\n`` while decoding, so the same assertion over ``run`` holds no
        matter what the script wrote.
        """
        result = machine.run_raw("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 0, result.stderr
        assert b"\r" not in result.stdout
        assert b"\x1b" not in result.stdout
        assert f"[ok] Installing   {_ARCHIVE}".encode() in result.stdout

    def test_the_closing_summary_is_one_aligned_block(self, machine):
        """A blank line, then two labelled rows on one column, then what to do next.

        The log's path is NOT shortened here and that is correct: these tests
        put the state root outside the fake home, so there is no `$HOME` to
        replace. `~` is exercised where a path really is under it — the
        uninstall list.
        """
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert "Next: start Steam, then open the Quick Access menu." in result.stdout
        assert "  Status  systemctl --user status romm-tender" in result.stdout
        assert f"  Log     {machine.state}/backend.log" in result.stdout
        assert result.stdout.rstrip().endswith(f"{machine.state}/backend.log")
        assert "\033[" not in result.stdout, "a piped run writes the summary plain"

    def test_the_unit_names_every_root_as_an_absolute_path(self, machine):
        """The installer and the service share no environment, so nothing may be derived at runtime."""
        machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert machine.unit.read_text(encoding="utf-8").splitlines() == [
            "[Unit]",
            "Description=Tender — RomM library in Steam",
            "",
            "[Service]",
            f"ExecStart={machine.python} {machine.code}/backend/main.py",
            f"Environment=TENDER_CODE_DIR={machine.code}",
            f"Environment=TENDER_CONFIG_DIR={machine.config}",
            f"Environment=TENDER_DATA_DIR={machine.data}",
            f"Environment=TENDER_CACHE_DIR={machine.cache}",
            f"Environment=TENDER_STATE_DIR={machine.state}",
            f"Environment=TENDER_BIN_DIR={machine.bin}",
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
        ]

    def test_the_interpreter_it_checked_is_the_one_the_unit_runs(self, machine):
        """One value, two uses: a unit naming another Python would run something nobody verified."""
        machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert f"ExecStart={machine.python} " in machine.unit.read_text(encoding="utf-8")

    def test_the_unit_is_reloaded_enabled_and_started(self, machine):
        machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        calls = machine.systemctl_calls()
        assert "--user daemon-reload" in calls
        # --quiet, so `enable` does not announce the symlink it made.
        assert "--user enable --now --quiet romm-tender" in calls
        assert calls.index("--user daemon-reload") < calls.index("--user enable --now --quiet romm-tender")

    def test_steams_debugger_marker_and_our_note_are_written(self, machine):
        machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert machine.marker.is_file()
        first, second = machine.note.read_text(encoding="utf-8").splitlines()
        assert first == str(machine.marker)
        assert second.startswith("created by install.sh ")

    def test_the_note_names_the_marker_that_was_actually_created(self, machine):
        """The uninstaller acts on that line, so it has to name the file this run wrote.

        The other Steam spelling is the case that tells the two apart: a note
        recording a name rather than a path would send the uninstaller looking,
        and looking is what it must not do.
        """
        machine.steam_root.rmdir()
        other = machine.home / ".steam" / "steam"
        other.mkdir(parents=True)

        machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert machine.note.read_text(encoding="utf-8").splitlines()[0] == str(other / ".cef-enable-remote-debugging")

    def test_a_marker_that_was_already_there_leaves_no_note(self, machine):
        """The note is the claim that the marker is ours, so it may not be written over someone else's."""
        machine.marker.write_text("", encoding="utf-8")

        machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert not machine.note.exists()


class TestThePreflight:
    def test_a_python_older_than_3_11_is_refused(self, machine):
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes", STUB_PYTHON_EXIT="1")

        assert result.returncode == 1
        assert "older than 3.11" in result.stderr
        assert not machine.code.exists()

    def test_a_python_that_is_not_there_at_all_is_refused(self, machine):
        result = machine.run(
            "--from", str(_build_tarball(machine.tmp_path)), "--yes", TENDER_PYTHON=str(machine.tmp_path / "nowhere")
        )

        assert result.returncode == 1
        assert "no Python at" in result.stderr

    def test_an_unreachable_user_manager_is_refused(self, machine):
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes", STUB_USER_MANAGER_EXIT="1")

        assert result.returncode == 1
        assert "no systemd user manager" in result.stderr
        assert not machine.code.exists()

    def test_flatpak_steam_is_refused(self, machine):
        (machine.home / ".var" / "app" / "com.valvesoftware.Steam").mkdir(parents=True)

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 1
        assert "Flatpak Steam is not supported" in result.stderr

    def test_a_machine_with_no_native_steam_is_refused(self, machine):
        """Refused once: ``check_native_steam`` answers, and the pre-flight is what aborts."""
        machine.steam_root.rmdir()

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 1
        assert _refusals(result.stderr) == ["install.sh: no native Steam installation found"]

    def test_the_other_spelling_of_the_steam_root_is_accepted(self, machine):
        machine.steam_root.rmdir()
        other = machine.home / ".steam" / "steam"
        other.mkdir(parents=True)

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 0, result.stderr
        assert (other / ".cef-enable-remote-debugging").is_file()

    @pytest.mark.parametrize("name", ["Tender", "RomM Sync"])
    def test_tender_still_installed_as_a_decky_plugin_is_refused(self, machine, name):
        """Two backends on one database. The message names the folder to remove."""
        folder = machine.home / "homebrew" / "plugins" / "some-folder"
        folder.mkdir(parents=True)
        (folder / "plugin.json").write_text(f'{{\n  "name": "{name}",\n  "version": "0.33.0"\n}}\n', encoding="utf-8")

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 1
        assert str(folder) in result.stderr
        assert "remove it in Decky first" in result.stderr

    def test_another_decky_plugin_is_not_mistaken_for_ours(self, machine):
        folder = machine.home / "homebrew" / "plugins" / "someone-else"
        folder.mkdir(parents=True)
        (folder / "plugin.json").write_text('{"name": "PowerTools"}\n', encoding="utf-8")

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 0, result.stderr


class TestTheAcknowledgement:
    def test_no_terminal_and_no_yes_refuses_before_anything_is_written(self, machine):
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)))

        assert result.returncode == 1
        assert "run with --yes" in result.stderr
        assert "Coming from the Decky plugin?" in result.stdout
        assert not machine.code.exists()

    def test_yes_passes_it(self, machine):
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 0, result.stderr

    def test_the_warning_is_bold_yellow_on_a_terminal(self, machine):
        """It is the one warning this script prints, and the only colour it uses."""
        _code, output = machine.on_a_terminal("--from", str(machine.tmp_path / "gone.tar.gz"), answer="y")

        assert "\033[1;33m" in output
        assert "\033[0m" in output

    def test_no_color_is_honoured(self, machine):
        """https://no-color.org: present and not empty means no, whatever the value."""
        _code, output = machine.on_a_terminal("--from", str(machine.tmp_path / "gone.tar.gz"), answer="y", NO_COLOR="1")

        assert "\033[" not in output
        assert "Coming from the Decky plugin?" in _screen(output)

    def test_an_empty_no_color_says_nothing(self, machine):
        """The other half of the convention, and the one a `-n` test gets wrong."""
        _code, output = machine.on_a_terminal("--from", str(machine.tmp_path / "gone.tar.gz"), answer="y", NO_COLOR="")

        assert "\033[1;33m" in output

    def test_a_piped_run_is_never_coloured(self, machine):
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert "\033[" not in result.stdout

    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES"])
    def test_a_yes_in_any_of_its_spellings_is_accepted(self, machine, answer):
        code, output = machine.on_a_terminal("--from", str(machine.tmp_path / "not-here.tar.gz"), answer=answer)

        assert code == 1
        assert "no such file" in output
        assert "stopped" not in output

    @pytest.mark.parametrize("answer", ["", "n", "no", "maybe"])
    def test_everything_else_including_a_bare_enter_refuses(self, machine, answer):
        """The capital N in `[y/N]` is the answer Enter gives, and it is the safe one."""
        code, output = machine.on_a_terminal("--from", str(_build_tarball(machine.tmp_path)), answer=answer)

        assert code == 1
        assert "install.sh: stopped — nothing was changed." in output
        assert "--yes" not in output, "a no is an answer, not a question to route around"
        assert not machine.code.exists()

    def test_a_refused_acknowledgement_says_so_on_the_terminal(self, machine):
        """The case every other test in this file is structurally blind to.

        It needs a real terminal twice over: to reach the prompt at all, and to
        show that this script still HAS a stderr on the far side of the check
        that opened one. An `exec` redirection applied to the shell rather than
        to a group silences every later message, and it silences them only where
        a terminal exists — so a suite without this tier reports a green run for
        an installer whose every abort has gone missing.
        """
        code, output = machine.on_a_terminal("--from", str(_build_tarball(machine.tmp_path)), answer="no")

        assert code == 1
        assert "install.sh: stopped — nothing was changed." in output
        assert not machine.code.exists()

    def test_an_answered_acknowledgement_gets_on_with_it_and_keeps_its_stderr(self, machine):
        """ "yes" passes the check, and the next failure still reaches the terminal."""
        code, output = machine.on_a_terminal("--from", str(machine.tmp_path / "not-here.tar.gz"), answer="yes")

        assert code == 1
        assert "Continue? [y/N]" in output
        assert "no such file" in output
        assert "not confirmed" not in output

    def test_the_download_draws_a_progress_bar_only_on_a_terminal(self, machine):
        """Tens of megabytes over an unknown connection is the one wait worth showing.

        The checksum beside it is a hundred bytes and stays silent either way,
        which is what keeps the two apart in the recorded arguments.
        """
        machine.publish_release()

        code, _output = machine.on_a_terminal("--version", _VERSION, answer="y")

        assert code == 0
        archive = [line for line in machine.curl_argv() if line.endswith(_ARCHIVE)]
        sidecar = [line for line in machine.curl_argv() if ".sha256" in line]
        assert archive, "the tarball was never fetched"
        assert all("--progress-bar" in line for line in archive)
        assert sidecar, "the checksum was never fetched"
        assert not any("--progress-bar" in line for line in sidecar)

    def test_a_piped_download_stays_silent(self, machine):
        machine.publish_release()

        machine.run("--version", _VERSION, "--yes")

        assert not any("--progress-bar" in line for line in machine.curl_argv())

    def test_a_phase_that_aborts_closes_its_line_before_the_message(self, machine):
        """The reason belongs ON the row it is about, and before the record of it."""
        corrupt = machine.tmp_path / "corrupt.tar.gz"
        corrupt.write_bytes(b"this is not a gzip stream")

        code, output = machine.on_a_terminal("--from", str(corrupt), answer="y")

        assert code == 1
        assert "✗ Installing   the tarball could not be unpacked" in _screen(output)
        assert output.index("Installing   the tarball could not be unpacked") < output.index(
            "install.sh: the tarball could not be unpacked"
        )

    def test_each_row_is_left_on_screen_once_in_its_finished_form(self, machine):
        """The block is rewritten in place, so what matters is what is LEFT.

        A spinner draws the whole block — it moves the cursor up over the four
        rows and writes them again — so a run that lost track of how many lines
        are on screen does not leave a stray frame somewhere: it writes the
        block a second time further down, or over the summary that follows it.
        Both are absences, which is why this reads the replayed screen rather
        than the bytes.

        Seeing any of it needs a phase long enough for a second frame, which is
        what the delay buys: a run with none is over faster than one frame.

        **What this cannot see is a spinner left running at a row's end.** The
        EXIT trap stops the last one, and an extra one drawing mid-run draws the
        same block, so the damage is a race on the bookkeeping rather than
        anything a transcript shows. That one is carried by `row_end` and by
        nothing else.
        """
        code, output = machine.on_a_terminal(
            "--from",
            str(_build_tarball(machine.tmp_path)),
            answer="y",
            STUB_ENABLE_DELAY="0.4",
        )

        assert code == 0, output
        screen = _screen(output).splitlines()
        # By the row rather than over the whole screen: the mark IS Braille, so
        # every spinner frame is also a cell of the drawing above it.
        for mark, label in (("✓", "Checking"), ("✓", "Installing"), ("✓", "Service"), ("✗", "Steam")):
            drawn = [line for line in screen if line.startswith(f"{mark} {label}")]
            assert len(drawn) == 1, f"{label} is not on screen exactly once in its finished form"
        assert "\n".join(screen).rstrip().endswith(f"{machine.state}/backend.log")

    def test_it_is_not_shown_once_its_expiry_has_passed(self, machine):
        """The warning carries an expiry in the code so it does not outlive its reason."""
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), TENDER_ACK_UNTIL="2000-01-01")

        assert result.returncode == 0, result.stderr
        assert "migration notes" not in result.stdout


class TestTheCoversMoveOnce:
    def _seed(self, machine, name: str, files: dict[str, str]) -> None:
        folder = machine.data / name
        folder.mkdir(parents=True, exist_ok=True)
        for filename, body in files.items():
            (folder / filename).write_text(body, encoding="utf-8")

    def test_covers_and_artwork_move_to_the_cache_root(self, machine):
        self._seed(machine, "covers", {"a.png": "a"})
        self._seed(machine, "artwork", {"b.png": "b"})

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert (machine.cache / "covers" / "a.png").read_text(encoding="utf-8") == "a"
        assert (machine.cache / "artwork" / "b.png").read_text(encoding="utf-8") == "b"
        assert not (machine.data / "covers").exists()
        assert "1 cover and 1 artwork file moved to the cache" in result.stdout

    def test_a_file_the_cache_already_holds_is_never_overwritten(self, machine):
        """And says nothing about it: after the first run that is what EVERY file is."""
        self._seed(machine, "covers", {"a.png": "old"})
        (machine.cache / "covers").mkdir(parents=True)
        (machine.cache / "covers" / "a.png").write_text("new", encoding="utf-8")

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert (machine.cache / "covers" / "a.png").read_text(encoding="utf-8") == "new"
        assert (machine.data / "covers" / "a.png").read_text(encoding="utf-8") == "old"
        assert "moved to the cache" not in result.stdout

    def test_a_move_that_fails_is_reported_as_a_failure_not_as_a_duplicate(self, machine):
        """The two reasons a file stays are not the same news.

        "the cache already holds it" is the rule working; a write that failed is
        the user's covers still sitting where nothing reads them. Reading the
        exit status of ``mv -n`` cannot tell them apart, because it answers 0
        for both.
        """
        self._seed(machine, "covers", {"a.png": "a"})
        target = machine.cache / "covers"
        target.mkdir(parents=True)
        target.chmod(0o500)
        try:
            result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")
        finally:
            target.chmod(0o700)

        assert result.returncode == 0, result.stderr
        assert "could not move 1 cover;" in result.stderr
        assert "already there" not in result.stdout
        assert "could not move 1 cover; see above" in result.stdout
        assert (machine.data / "covers" / "a.png").is_file()

    def test_the_sentences_are_plural_above_one(self, machine):
        """The counts decide the nouns, and only what MOVED is counted."""
        self._seed(machine, "covers", {f"c{n}.png": "x" for n in range(5)})
        self._seed(machine, "artwork", {f"a{n}.png": "x" for n in range(3)})
        (machine.cache / "covers").mkdir(parents=True)
        (machine.cache / "covers" / "c0.png").write_text("held", encoding="utf-8")
        (machine.cache / "covers" / "c1.png").write_text("held", encoding="utf-8")

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert "3 covers and 3 artwork files moved to the cache" in result.stdout

    def test_a_folder_holding_only_a_dotfile_costs_no_line(self, machine):
        """The count and the move have to see the same files.

        ``"$source"/*`` never yields a dotfile, so counting with ``find``
        announced a phase that then moved nothing and had nothing to report —
        a line saying work was done over a folder still sitting where it was.
        """
        self._seed(machine, "covers", {".keep": "x"})

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 0, result.stderr
        assert "moved to the cache" not in result.stdout
        assert (machine.data / "covers" / ".keep").is_file()

    def test_nothing_else_under_the_data_root_is_touched(self, machine):
        (machine.data).mkdir(parents=True, exist_ok=True)
        (machine.data / "library.db").write_text("the database", encoding="utf-8")

        machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert (machine.data / "library.db").read_text(encoding="utf-8") == "the database"


class TestAnUpdateOverAnExistingInstall:
    def test_the_previous_tree_is_gone_and_nothing_is_staged_beside_it(self, machine):
        tarball = _build_tarball(machine.tmp_path)
        machine.run("--from", str(tarball), "--yes")
        (machine.code / "backend" / "leftover.py").write_text("from the old install\n", encoding="utf-8")

        result = machine.run("--from", str(tarball), "--yes")

        assert result.returncode == 0, result.stderr
        assert not (machine.code / "backend" / "leftover.py").exists()
        assert not Path(f"{machine.code}.new").exists()
        assert not Path(f"{machine.code}.old").exists()

    def test_it_restarts_the_unit_that_was_already_running(self, machine):
        """`enable --now` leaves a running unit on the tree that was just replaced under it."""
        tarball = _build_tarball(machine.tmp_path)
        machine.run("--from", str(tarball), "--yes")
        machine.systemctl_log.write_text("", encoding="utf-8")

        machine.run("--from", str(tarball), "--yes")

        assert "--user restart romm-tender" in machine.systemctl_calls()

    def test_the_users_own_directories_survive_it(self, machine):
        tarball = _build_tarball(machine.tmp_path)
        machine.run("--from", str(tarball), "--yes")
        machine.config.mkdir(parents=True, exist_ok=True)
        (machine.config / "settings.json").write_text("{}", encoding="utf-8")

        machine.run("--from", str(tarball), "--yes")

        assert (machine.config / "settings.json").read_text(encoding="utf-8") == "{}"


class TestWhatItDownloads:
    def test_a_named_version_builds_the_release_url_and_asks_github_nothing(self, machine):
        machine.publish_release()

        result = machine.run("--version", _VERSION, "--yes")

        assert result.returncode == 0, result.stderr
        assert machine.curl_calls()[0] == f"{_DOWNLOAD_BASE}/{_TAG}/{_ARCHIVE}"
        assert machine.curl_calls()[1] == f"{_DOWNLOAD_BASE}/{_TAG}/{_ARCHIVE}.sha256"
        assert _RELEASE_API not in machine.curl_calls()

    def test_with_no_version_it_asks_for_the_newest_release_first(self, machine):
        machine.publish_release()

        result = machine.run("--yes")

        assert result.returncode == 0, result.stderr
        assert machine.curl_calls()[0] == _RELEASE_API
        assert machine.curl_calls()[1] == f"{_DOWNLOAD_BASE}/{_TAG}/{_ARCHIVE}"

    def test_a_newest_release_that_is_not_a_tender_release_is_refused(self, machine):
        """A release that exists and is another program's: named in the message, refused once."""
        machine.publish_release()
        (machine.serve / "latest").write_text('{"tag_name": "gavel-v2.0.0"}\n', encoding="utf-8")

        result = machine.run("--yes")

        assert result.returncode == 1
        assert _refusals(result.stderr) == ["install.sh: the newest release is not a Tender release (gavel-v2.0.0)"]
        assert machine.curl_calls() == [_RELEASE_API]
        assert not machine.code.exists()

    def test_an_answer_that_names_no_release_is_told_apart_from_a_foreign_one(self, machine):
        """An answer carrying no tag gets its own message, and never the foreign-tag one.

        Why the two are told apart is at ``resolve_tag``. What this pins is the
        message, its fix line, and that no empty parentheses reach the reader.
        """
        machine.publish_release()
        (machine.serve / "latest").write_text('{"message": "Not Found"}\n', encoding="utf-8")

        result = machine.run("--yes")

        assert result.returncode == 1
        assert _refusals(result.stderr) == ["install.sh: GitHub's answer named no release"]
        assert "()" not in result.stderr
        assert "check the network, or name one with --version" in result.stderr
        assert machine.curl_calls() == [_RELEASE_API]
        assert not machine.code.exists()

    def test_a_tag_key_with_an_empty_value_is_the_same_answer(self, machine):
        """Edge: the key is there and says nothing, which is the other route to the same state."""
        machine.publish_release()
        (machine.serve / "latest").write_text('{"tag_name": ""}\n', encoding="utf-8")

        result = machine.run("--yes")

        assert result.returncode == 1
        assert _refusals(result.stderr) == ["install.sh: GitHub's answer named no release"]

    def test_a_release_that_cannot_be_asked_for_at_all_says_so(self, machine):
        """Nothing is published, so the stubbed curl fails the API call itself."""
        result = machine.run("--yes")

        assert result.returncode == 1
        assert _refusals(result.stderr) == ["install.sh: could not ask GitHub for the newest release"]
        assert "check the network" in result.stderr
        assert machine.curl_calls() == [_RELEASE_API]
        assert not machine.code.exists()

    def test_a_release_with_no_tarball_says_so(self, machine):
        machine.publish_release()
        (machine.serve / _ARCHIVE).unlink()

        result = machine.run("--version", _VERSION, "--yes")

        assert result.returncode == 1
        assert _refusals(result.stderr) == [f"install.sh: release {_TAG} carries no tarball"]

    def test_a_release_with_no_checksum_is_refused_rather_than_trusted(self, machine):
        machine.publish_release(checksum=False)

        result = machine.run("--version", _VERSION, "--yes")

        assert result.returncode == 1
        assert "carries no checksum" in result.stderr
        assert not machine.code.exists()

    def test_a_tarball_that_does_not_match_its_checksum_leaves_the_install_alone(self, machine):
        machine.publish_release()
        machine.run("--version", _VERSION, "--yes")
        before = (machine.code / "backend" / "main.py").read_bytes()
        (machine.serve / _ARCHIVE).write_bytes(b"not the tarball you checksummed")

        result = machine.run("--version", _VERSION, "--yes")

        assert result.returncode == 1
        assert "does not match its checksum" in result.stderr
        assert (machine.code / "backend" / "main.py").read_bytes() == before

    def test_a_local_file_beside_its_checksum_is_verified(self, machine):
        """The packager writes that sidecar, so a local build is checked like a release."""
        tarball = _build_tarball(machine.tmp_path)

        result = machine.run("--from", str(tarball), "--yes")

        assert result.returncode == 0, result.stderr
        assert "(not verified)" not in result.stdout

    def test_a_local_file_that_does_not_match_its_checksum_is_refused(self, machine):
        tarball = _build_tarball(machine.tmp_path)
        Path(f"{tarball}.sha256").write_text(f"{'0' * 64}  {tarball.name}\n", encoding="utf-8")

        result = machine.run("--from", str(tarball), "--yes")

        assert result.returncode == 1
        assert "does not match" in result.stderr
        assert not machine.code.exists()

    def test_a_local_file_with_no_checksum_says_it_was_not_verified(self, machine):
        """A hand-built tarball has no release to be checked against; the absence is stated."""
        tarball = _build_tarball(machine.tmp_path)
        Path(f"{tarball}.sha256").unlink()

        result = machine.run("--from", str(tarball), "--yes")

        assert result.returncode == 0, result.stderr
        assert f"[ok] Installing   {_ARCHIVE} (not verified)" in result.stdout

    def test_a_tarball_missing_what_a_release_must_carry_is_refused(self, machine):
        """Nothing is renamed into place until the staged tree has been looked at."""
        broken = machine.tmp_path / "broken.tar.gz"
        with tarfile.open(broken, "w:gz") as tar:
            tar.add(str(_build_tarball(machine.tmp_path)), arcname="romm-tender/backend/main.py")

        result = machine.run("--from", str(broken), "--yes")

        assert result.returncode == 1
        assert "has no dist/index.js" in result.stderr
        assert not machine.code.exists()
        assert not Path(f"{machine.code}.new").exists()

    def test_a_tarball_that_cannot_be_unpacked_leaves_nothing_staged(self, machine):
        """The staging directory is this run's, so a run that failed takes it with it."""
        corrupt = machine.tmp_path / "corrupt.tar.gz"
        corrupt.write_bytes(b"this is not a gzip stream")

        result = machine.run("--from", str(corrupt), "--yes")

        assert result.returncode == 1
        assert "could not be unpacked" in result.stderr
        assert not Path(f"{machine.code}.new").exists()


class TestDisable:
    def test_it_stops_the_unit_and_says_how_to_get_it_back(self, machine):
        result = machine.run("--disable")

        assert result.returncode == 0, result.stderr
        assert "--user disable --now romm-tender" in machine.systemctl_calls()
        assert "Run install.sh again" in result.stdout


class TestUninstall:
    def _installed(self, machine) -> None:
        machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

    def test_it_removes_the_program_the_unit_and_the_state(self, machine):
        self._installed(machine)
        (machine.runtime / "romm-tender").mkdir(parents=True, exist_ok=True)

        result = machine.run("--uninstall")

        assert result.returncode == 0, result.stderr
        assert not machine.code.exists()
        assert not machine.unit.exists()
        assert not machine.state.exists()
        assert not (machine.runtime / "romm-tender").exists()
        assert "--user disable --now romm-tender" in machine.systemctl_calls()

    def test_it_leaves_the_library_the_settings_and_the_launcher(self, machine):
        self._installed(machine)
        machine.config.mkdir(parents=True, exist_ok=True)
        (machine.config / "settings.json").write_text("{}", encoding="utf-8")
        machine.data.mkdir(parents=True, exist_ok=True)
        (machine.data / "library.db").write_text("the database", encoding="utf-8")
        machine.bin.mkdir(parents=True, exist_ok=True)
        (machine.bin / "tender-rom-launcher").write_text("#!/bin/bash\n", encoding="utf-8")

        result = machine.run("--uninstall")

        assert (machine.config / "settings.json").is_file()
        assert (machine.data / "library.db").is_file()
        assert (machine.bin / "tender-rom-launcher").is_file()
        assert "your settings" in result.stdout
        assert "every Steam shortcut starts through it" in result.stdout
        # The recovery root is the user's home, not one of RetroDECK's folders,
        # so it gets a line of its own rather than riding along with them —
        # and every path in the list is printed with `~` for the home.
        assert "~/romm-tender-recovery" in result.stdout
        assert str(machine.home) not in result.stdout

    def test_it_removes_the_marker_it_created_where_no_decky_loader_is_installed(self, machine):
        self._installed(machine)
        assert machine.marker.is_file()

        machine.run("--uninstall")

        assert not machine.marker.exists()

    def test_it_removes_exactly_the_path_the_note_names_and_nothing_else(self, machine):
        """Authority over one file. A second Steam root is the file it may not touch."""
        self._installed(machine)
        other = machine.home / ".steam" / "steam"
        other.mkdir(parents=True)
        someone_elses = other / ".cef-enable-remote-debugging"
        someone_elses.write_text("", encoding="utf-8")

        machine.run("--uninstall")

        assert not machine.marker.exists()
        assert someone_elses.is_file()

    def test_a_note_that_names_no_marker_removes_nothing(self, machine):
        """Truncated, half-written or hand-edited: the uninstaller unlinks nothing on a hunch."""
        self._installed(machine)
        machine.note.write_text("/etc/passwd\n", encoding="utf-8")

        result = machine.run("--uninstall")

        assert machine.marker.is_file()
        assert "does not name one" in result.stderr

    def test_a_marker_this_install_did_not_create_is_left_alone(self, machine):
        machine.marker.write_text("", encoding="utf-8")
        self._installed(machine)
        assert not machine.note.exists()

        machine.run("--uninstall")

        assert machine.marker.is_file()

    def test_the_marker_stays_where_decky_loader_is_installed_as_a_directory(self, machine):
        """Decky's own installer creates the same file, so taking it away would break Decky."""
        self._installed(machine)
        loader = machine.home / "homebrew" / "services" / "PluginLoader"
        loader.parent.mkdir(parents=True, exist_ok=True)
        loader.write_text("", encoding="utf-8")

        result = machine.run("--uninstall")

        assert machine.marker.is_file()
        assert "Decky Loader is installed" in result.stdout

    def test_the_marker_stays_where_deckys_unit_is_known_to_systemd(self, machine):
        """Installed, not running — an uninstaller may not take away what a stopped program needs."""
        self._installed(machine)

        result = machine.run("--uninstall", STUB_DECKY_UNIT="plugin_loader.service enabled enabled")

        assert machine.marker.is_file()
        assert "Decky Loader is installed" in result.stdout


class TestTheNoteIsSpelledOnceOnEachSide:
    def test_marker_note_filename_matches_backend(self):
        """Two literals in two languages; the uninstaller reads whichever of the two wrote it."""
        from host.inject.machine import DEBUGGER_MARKER_NOTE

        script = _INSTALL.read_text(encoding="utf-8")

        assert f'MARKER_NOTE="{DEBUGGER_MARKER_NOTE}"' in script


class TestHowTheRunLooks:
    """The greeter and the four rows, which only a terminal ever sees whole."""

    def test_a_wide_terminal_puts_the_icon_beside_the_text(self, machine):
        """Half-blocks, and the text block on the same lines as the drawing.

        Wide on purpose: the block's longest line carries `$CODE`, and these
        runs put it under a `tmp_path` whose name is longer than any real
        install root. The script measures that line rather than assuming one,
        so the terminal here has to be wider than a real one would need.
        """
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLUMNS="160", COLORTERM="truecolor"
        )

        screen = _screen(output)
        drawn = _icon_rows()
        for row in drawn[1:4]:
            assert row in screen
        beside = [line for line in screen.splitlines() if line.startswith(drawn[3])]
        assert beside, "the icon's fourth row is not on screen"
        assert beside[0].rstrip().endswith("RomM library in Steam"), "the text block is not beside the icon"

    def test_the_text_is_centred_against_the_icon(self, machine):
        """Six lines against thirteen: hung from the top they read as fallen off it."""
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLUMNS="160", COLORTERM="truecolor"
        )

        screen = _screen(output).splitlines()
        first = next(index for index, line in enumerate(screen) if line.startswith(_icon_rows()[0]))
        title = next(index for index, line in enumerate(screen) if "TENDER" in line)

        assert title - first == 3, "the text block does not start on the fourth row of the icon"

    def test_a_narrow_terminal_puts_the_icon_above_the_text(self, machine):
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLUMNS="70", COLORTERM="truecolor"
        )

        assert _stacked(output), "the text block is beside the drawing, not under it"

    def test_the_threshold_is_measured_not_written_down(self, machine):
        """The width the text needs is read off the block, so a longer $CODE moves it.

        One terminal width, two install roots: a threshold carried as a number
        lays the same greeter out the same way twice, whatever the number is,
        so only a run that answers differently at one width says the lines were
        read.
        """
        tarball = str(_build_tarball(machine.tmp_path))
        common = {"answer": "y", "COLUMNS": "160", "COLORTERM": "truecolor"}
        _code, beside = machine.on_a_terminal("--from", tarball, **common)
        _code, under = machine.on_a_terminal(
            "--from", tarball, TENDER_CODE_DIR=str(machine.tmp_path / ("deep" * 50)), **common
        )

        assert not _stacked(beside), "a real install root already pushes the text under the drawing"
        assert _stacked(under), "a deeper install root did not move the text under the drawing"

    def test_a_truecolor_terminal_gets_the_24_bit_icon(self, machine):
        """The two arrays draw the same glyphs, so only the escapes tell them apart."""
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLUMNS="160", COLORTERM="truecolor"
        )

        icon = _icon_escapes(output)
        assert "38;2;" in icon
        assert "38;5;" not in icon

    def test_a_terminal_that_did_not_say_so_gets_the_256_colour_icon(self, machine):
        """`COLORTERM` is the only thing that says a terminal takes 24-bit colour."""
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLUMNS="160"
        )

        icon = _icon_escapes(output)
        assert "38;5;" in icon
        assert "38;2;" not in icon

    def test_a_terminal_with_no_utf8_gets_the_ascii_drawing(self, machine):
        """Half-blocks would be replacement characters, which is worse than no art."""
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLUMNS="160", LANG="C"
        )

        screen = _screen(output)
        for row in _ascii_rows()[1:4]:
            assert row in screen
        assert "▀" not in screen
        assert "[ok] Checking" in screen

    def test_no_colour_leaves_the_icon_out_altogether(self, machine):
        """The half-blocks carry the mark in COLOUR; without it they are a grey slab."""
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLUMNS="160", NO_COLOR="1"
        )

        assert "\033[" not in output
        screen = _screen(output)
        assert "▀" not in screen
        drawn = [line for line in screen.splitlines() if line.strip()]
        assert drawn[1].startswith("TENDER"), "the header is the text block alone"

    def test_a_piped_run_draws_no_art_at_all(self, machine):
        """Art in a log file is something somebody has to scroll past."""
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert "▀" not in result.stdout
        for row in _ascii_rows():
            if row:
                assert row not in result.stdout
        assert result.stdout.lstrip("\n").startswith("TENDER  -  RomM library in Steam\n")

    def test_the_greeter_says_what_the_run_will_do(self, machine):
        """Four keys in one column, and the program's real root rather than a literal."""
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLUMNS="160", COLORTERM="truecolor"
        )

        screen = _screen(output)
        assert "TENDER  ·  RomM library in Steam" in screen
        assert f"Install to   {machine.code}" in screen
        assert "Runs as      a systemd user service, starts with your session" in screen
        assert "Needs        one Steam restart, no sudo" in screen
        assert "Keeps        your settings, library and shortcuts" in screen

    def test_the_four_rows_end_in_one_mark_each(self, machine):
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLORTERM="truecolor"
        )

        screen = _screen(output)
        assert "\x1b[" in output, "a terminal run writes escapes"
        for mark, label in (("✓", "Checking"), ("✓", "Installing"), ("✓", "Service"), ("✗", "Steam")):
            assert len([line for line in screen.splitlines() if line.startswith(f"{mark} {label}")]) == 1

    def test_the_warning_is_four_lines_and_asks_once(self, machine):
        code, output = machine.on_a_terminal("--from", str(machine.tmp_path / "gone.tar.gz"), answer="y")

        assert code == 1
        screen = _screen(output).splitlines()
        first = next(index for index, line in enumerate(screen) if line.startswith("⚠"))
        assert screen[first] == "⚠  Coming from the Decky plugin?"
        assert screen[first + 1] == "   The Steam shortcuts it created are not recognised by this version."
        assert screen[first + 2] == f"   Read first: {_MIGRATION_NOTES}"
        assert screen[first + 3].startswith("   Continue? [y/N]")

    @pytest.mark.parametrize(
        ("reason", "extra"),
        [
            ("Flatpak Steam is not supported", {}),
            ("no native Steam installation found", {}),
        ],
    )
    def test_a_refusal_lands_on_the_row_it_belongs_to(self, machine, reason, extra):
        """The row says what went wrong, rather than only that something did."""
        if reason.startswith("Flatpak"):
            (machine.home / ".var" / "app" / "com.valvesoftware.Steam").mkdir(parents=True)
        else:
            shutil.rmtree(machine.steam_root)

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes", **extra)

        assert result.returncode == 1
        assert f"[!!] Checking     {reason}" in result.stdout
        assert _refusals(result.stderr) == [f"install.sh: {reason}"]


class TestTheClosingLine:
    """The one line that says the run worked, in the mark's own blue."""

    def test_it_carries_the_discs_blue_and_bolds_the_action(self, machine):
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLORTERM="truecolor"
        )

        done = next(line for line in output.splitlines() if "Done in" in line)
        assert done.startswith("\033[38;2;146;183;227m"), "the Done line is not in the disc's blue"
        assert "\033[1mNext: " in done, "the action is not bold"

    def test_a_terminal_with_no_truecolor_gets_the_nearest_index(self, machine):
        """`COLORTERM` is the only thing that says a terminal takes 24-bit colour."""
        _code, output = machine.on_a_terminal("--from", str(_build_tarball(machine.tmp_path)), answer="y")

        done = next(line for line in output.splitlines() if "Done in" in line)
        assert done.startswith("\033[38;5;110m")
        assert "38;2;" not in done

    def test_the_rows_under_it_stay_dim(self, machine):
        """They are where to look afterwards, not the news."""
        _code, output = machine.on_a_terminal(
            "--from", str(_build_tarball(machine.tmp_path)), answer="y", COLORTERM="truecolor"
        )

        status = next(line for line in output.splitlines() if "Status" in line)
        assert status.startswith("\033[2m")


def _blank_lines_before(screen: list[str], sentence: str) -> int:
    """How many empty lines sit between *sentence* and whatever precedes it."""
    at = next(index for index, line in enumerate(screen) if line.startswith(sentence))
    count = 0
    while at - 1 - count >= 0 and not screen[at - 1 - count].strip():
        count += 1
    return count


class TestTheOtherTwoModes:
    """Neither installs anything, so neither lists what an install would put where."""

    def test_uninstall_says_what_it_is_about_to_do(self, machine):
        machine.state.mkdir(parents=True, exist_ok=True)
        _code, output = machine.on_a_terminal("--uninstall", COLUMNS="160", COLORTERM="truecolor")

        screen = _screen(output)
        assert _icon_rows()[3] in screen, "the mark is not drawn"
        assert "TENDER" in screen
        assert "Removing the service and the program, and keeping your data." in screen
        assert "Install to" not in screen, "an uninstall lists what an install would do"
        assert _blank_lines_before(screen.splitlines(), "Tender is removed.") == 1

    def test_disable_says_what_it_is_about_to_do(self, machine):
        _code, output = machine.on_a_terminal("--disable", COLUMNS="160", COLORTERM="truecolor")

        screen = _screen(output)
        assert _icon_rows()[3] in screen, "the mark is not drawn"
        assert "TENDER" in screen
        assert "Stopping the service and leaving everything installed." in screen
        assert "Install to" not in screen
        assert _blank_lines_before(screen.splitlines(), "Tender is stopped") == 1


class TestWhatTheUninstallLeavesBehind:
    """The marker is left where something else still reads it, and it says so."""

    def test_a_kept_marker_is_reported_between_one_blank_line_and_another(self, machine):
        """The blank belongs to the message: the greeter already left one, so a

        second printed unconditionally opens a gap on every run with nothing to
        say here — which is what it did.
        """
        machine.state.mkdir(parents=True, exist_ok=True)
        machine.marker.touch()
        machine.note.write_text(f"{machine.marker}\ncreated by install.sh 2026-09-22\n", encoding="utf-8")

        _code, output = machine.on_a_terminal(
            "--uninstall", COLUMNS="160", COLORTERM="truecolor", STUB_DECKY_UNIT="plugin_loader.service enabled"
        )

        screen = _screen(output).splitlines()
        assert _blank_lines_before(screen, "Steam's remote-debugging marker is left in place") == 1
        assert _blank_lines_before(screen, "Tender is removed.") == 1
        assert machine.marker.exists(), "Decky Loader reads it too"

    def test_a_running_steam_is_told_the_entry_outlives_the_uninstall(self, machine):
        """Removing the backend does not take its panel back out of Steam."""
        machine.state.mkdir(parents=True, exist_ok=True)

        result = machine.run("--uninstall", STUB_STEAM_RUNNING="yes")

        assert "Steam still shows Tender's entry until it restarts." in result.stdout

    def test_a_steam_that_is_not_running_is_told_nothing_of_the_sort(self, machine):
        """There is no entry to outlive anything, so the sentence would be noise."""
        machine.state.mkdir(parents=True, exist_ok=True)

        result = machine.run("--uninstall")

        assert "still shows" not in result.stdout


class TestWhatTheRunSaysAboutSteam:
    """Three machines, three answers — and the probe alone cannot tell them apart."""

    def test_a_first_install_into_a_running_steam_needs_no_restart(self, machine):
        """Nothing of Tender's is in Steam yet, so the backend's own panel is the first."""
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes", STUB_DEBUGGER="answer")

        assert "[ok] Steam        debugger answering" in result.stdout
        assert (
            "Next: open the Quick Access menu — Tender's entry appears once the backend has loaded it." in result.stdout
        )

    def test_a_reinstall_into_a_running_steam_asks_for_a_restart(self, machine):
        """The panel the previous backend loaded is still in Steam, holding its token.

        A backend that starts under it cannot replace it: the injection marker
        is already set, so the new one loads nothing, and the panel that IS
        there talks to a backend that has gone. Only Steam restarting clears
        that, so a run which replaced an install may not promise the entry
        appears on its own — which is what it did.
        """
        tarball = str(_build_tarball(machine.tmp_path))
        machine.run("--from", tarball, "--yes")

        result = machine.run("--from", tarball, "--yes", STUB_DEBUGGER="answer")

        assert "[--] Steam        running, an earlier Tender's panel is still loaded" in result.stdout
        assert "Next: restart Steam, then open the Quick Access menu." in result.stdout

    def test_a_running_unit_counts_as_something_to_replace(self, machine):
        """The other way an install is already here: no tree yet, but a unit up.

        Asked before the unit is written, because afterwards every answer is yes.
        """
        result = machine.run(
            "--from",
            str(_build_tarball(machine.tmp_path)),
            "--yes",
            STUB_DEBUGGER="answer",
            STUB_UNIT_ACTIVE="0",
        )

        assert "[--] Steam        running, an earlier Tender's panel is still loaded" in result.stdout
        assert "Next: restart Steam, then open the Quick Access menu." in result.stdout

    def test_a_silent_debugger_with_steam_up_asks_for_a_restart(self, machine):
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes", STUB_STEAM_RUNNING="yes")

        assert "[--] Steam        running, debugger not answering" in result.stdout
        assert "Next: restart Steam, then open the Quick Access menu." in result.stdout

    def test_a_silent_debugger_with_no_steam_asks_for_a_start(self, machine):
        """Telling a user to restart a Steam that is not running sends them hunting."""
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert "[--] Steam        not running" in result.stdout
        assert "Next: start Steam, then open the Quick Access menu." in result.stdout
        assert _DEBUGGER_PROBE in machine.curl_calls()
