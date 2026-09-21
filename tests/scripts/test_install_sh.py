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
import selectors
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
esac
exit 0
"""

_CURL_STUB = """#!/usr/bin/env bash
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

_PYTHON_STUB = """#!/usr/bin/env bash
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
        self.python = self.stubs / "stub-python3"

        for directory in (self.home, self.stubs, self.serve, self.runtime):
            directory.mkdir(parents=True, exist_ok=True)
        self.systemctl_log.touch()
        self.curl_log.touch()
        _write_executable(self.stubs / "systemctl", _SYSTEMCTL_STUB)
        _write_executable(self.stubs / "curl", _CURL_STUB)
        _write_executable(self.python, _PYTHON_STUB)

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
    """What the script does with an option it cannot read.

    Every refusal here is counted rather than merely found, because a refusal
    that ends a subshell instead of the run prints one message and then another
    about the emptiness it left behind — and both spellings exit 1.
    """

    @pytest.mark.parametrize("flag", ["--version", "--from"])
    def test_an_option_with_no_value_is_refused_once_by_name(self, machine, flag):
        result = machine.run(flag)

        assert result.returncode == 1
        assert _refusals(result.stderr) == [f"install.sh: {flag} needs a value"]
        assert not machine.code.exists()

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
        assert "--user enable --now romm-tender" in calls
        assert calls.index("--user daemon-reload") < calls.index("--user enable --now romm-tender")

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

    def test_a_silent_debugger_asks_for_a_steam_restart(self, machine):
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert "Restart Steam once" in result.stdout
        assert _DEBUGGER_PROBE in machine.curl_calls()

    def test_a_debugger_that_answers_does_not(self, machine):
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes", STUB_DEBUGGER="answer")

        assert "Restart Steam once" not in result.stdout
        assert "Quick Access menu" in result.stdout


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
        assert "migration notes" in result.stdout
        assert not machine.code.exists()

    def test_yes_passes_it(self, machine):
        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert result.returncode == 0, result.stderr

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
        assert "not confirmed" in output
        assert "run with --yes" in output
        assert not machine.code.exists()

    def test_an_answered_acknowledgement_gets_on_with_it_and_keeps_its_stderr(self, machine):
        """ "yes" passes the check, and the next failure still reaches the terminal."""
        code, output = machine.on_a_terminal("--from", str(machine.tmp_path / "not-here.tar.gz"), answer="yes")

        assert code == 1
        assert 'Type "yes" to continue' in output
        assert "no such file" in output
        assert "not confirmed" not in output

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
        assert "moved 1 covers file(s)" in result.stdout

    def test_a_file_the_cache_already_holds_is_never_overwritten(self, machine):
        self._seed(machine, "covers", {"a.png": "old"})
        (machine.cache / "covers").mkdir(parents=True)
        (machine.cache / "covers" / "a.png").write_text("new", encoding="utf-8")

        result = machine.run("--from", str(_build_tarball(machine.tmp_path)), "--yes")

        assert (machine.cache / "covers" / "a.png").read_text(encoding="utf-8") == "new"
        assert (machine.data / "covers" / "a.png").read_text(encoding="utf-8") == "old"
        assert "left 1 covers file(s)" in result.stdout

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
        assert "could not move 1 covers file(s)" in result.stderr
        assert "the cache already holds a file of that name" not in result.stdout
        assert (machine.data / "covers" / "a.png").is_file()

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
        """The repository may publish another program's tag; it is not a thing to install.

        Refused once, and nothing is fetched afterwards. A refusal that ended a
        subshell would leave the caller holding an empty tag: a second refusal
        about the emptiness, and a download asked for at a URL with no tag in
        it.
        """
        machine.publish_release()
        (machine.serve / "latest").write_text('{"tag_name": "gavel-v2.0.0"}\n', encoding="utf-8")

        result = machine.run("--yes")

        assert result.returncode == 1
        assert _refusals(result.stderr) == ["install.sh: the newest release is not a Tender release (gavel-v2.0.0)"]
        assert machine.curl_calls() == [_RELEASE_API]
        assert not machine.code.exists()

    def test_a_release_body_with_no_tag_at_all_is_refused_by_name_and_only_once(self, machine):
        """A body naming no tag is refused where it is read, and nothing is fetched after it.

        The refusal is a value ``resolve_tag`` answers with, which is what lets
        the caller act on it. The same run under a refusal that ended a subshell
        printed two messages and then asked curl for
        ``.../download//romm-tender-.tar.gz``.
        """
        machine.publish_release()
        (machine.serve / "latest").write_text('{"message": "Not Found"}\n', encoding="utf-8")

        result = machine.run("--yes")

        assert result.returncode == 1
        assert _refusals(result.stderr) == ["install.sh: the newest release is not a Tender release ()"]
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
        assert f"verified {tarball} against its checksum" in result.stderr

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
        assert "local file, not verified" in result.stderr

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
        # so it gets a line of its own rather than riding along with them.
        assert f"{machine.home}/romm-tender-recovery" in result.stdout

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
