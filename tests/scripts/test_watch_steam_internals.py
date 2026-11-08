"""The daily watch over Steam's published client finds, judges and reports the listed Steam internals.

The script is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``. No case touches the network: the UI package is built from the
hand-written fixtures beside this file, and GitHub is a recording stand-in for
``gh``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from domain.steam_internals import STEAM_INTERNALS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO / "scripts" / "watch_steam_internals.py"
_FIXTURES = Path(__file__).resolve().parent / "steam_internals_watch"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("watch_steam_internals", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watch = _load_module()


def _package() -> bytes:
    """A UI package holding the fixtures under ``steamui/``.

    The fixtures are stored as ``.txt`` so that no tool reads them as this
    repository's own JavaScript or CSS.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in ("sp.js", "library.js", "sp.css"):
            archive.writestr(f"steamui/{name}", (_FIXTURES / f"{name}.txt").read_text(encoding="utf-8"))
    return buffer.getvalue()


@pytest.fixture(scope="module")
def spellings() -> Any:
    return watch.spellings_of(_package())


def _internal(path: str, *losses: str, **fields: Any) -> Any:
    return watch.Internal(path=path, losses=losses or ("a loss",), **fields)


def _result(
    channel: str = "Stable", version: str = "1", misses: Sequence[Any] = (), spelled: frozenset[str] = frozenset()
) -> Any:
    return watch.ChannelResult(channel, version, tuple(misses), spelled, ())


def _miss(
    *paths: str, not_found: Sequence[str] = ("x",), losses: Sequence[str] = ("a loss",), dev: bool = False
) -> Any:
    return watch.Miss(tuple(paths), tuple(not_found), tuple(losses), dev)


class TestHowANameIsFound:
    def test_an_identifier_path_is_found_when_every_name_it_spells_is_an_identifier(self, spellings: Any) -> None:
        # `(callback)` and `(details)` name a call's parameter and the argument
        # Steam hands that callback, in Tender's own words; the fixture spells neither.
        assert watch.not_found("SteamClient.Apps.AddShortcut(callback)(details).strAppName", spellings) == ()
        assert watch.not_found("appStore.GetAppOverviewByAppID().m_mapApps.has", spellings) == ()

    def test_a_name_not_spelled_is_answered_by_name(self, spellings: Any) -> None:
        assert watch.not_found("SteamClient.Apps.RemoveShortcut", spellings) == ("RemoveShortcut",)
        assert watch.not_found("SteamClient.Browser.RestartJSContext", spellings) == ("Browser", "RestartJSContext")

    def test_a_name_inside_a_longer_identifier_is_not_found(self, spellings: Any) -> None:
        assert watch.not_found("appStore.GetAppOverviewByAppID().controller_support", spellings) == (
            "controller_support",
        )
        assert watch.not_found("SteamClient.Apps.AddShortcut(callback)(details).LaunchOptions", spellings) == (
            "LaunchOptions",
        )

    def test_a_route_is_found_only_as_a_bounded_literal(self, spellings: Any) -> None:
        assert watch.not_found("/apprunning", spellings) == ()
        assert watch.not_found("https://steamloopback.host", spellings) == ()

        only_inside_a_url = watch.Spellings(identifiers=frozenset(), text='("steam://open/apprunningcontrols")')
        assert watch.not_found("/apprunning", only_inside_a_url) == ("/apprunning",)

    def test_a_route_built_in_a_template_is_not_found(self, spellings: Any) -> None:
        assert watch.not_found("/library/app/", spellings) == ("/library/app/",)

    def test_an_address_is_found_as_its_host_and_its_route(self, spellings: Any) -> None:
        assert watch.not_found("steamloopback.host/customimages", spellings) == ()
        assert watch.not_found("steamloopback.host/nowhere", spellings) == ("/nowhere",)

    def test_every_javascript_file_is_read_and_nothing_else(self, spellings: Any) -> None:
        assert watch.not_found("__mobxGlobals.allowStateChanges", spellings) == ()
        assert watch.not_found("RemoveShortcut", spellings) == ("RemoveShortcut",)


class TestJudge:
    def test_an_internal_not_found_is_a_miss_with_its_losses(self, spellings: Any) -> None:
        internals = [
            _internal("SteamClient.Apps.AddShortcut"),
            _internal("SteamClient.Apps.RemoveShortcut", "changes to Steam shortcuts", dev_build_only=True),
        ]

        result = watch.judge(internals, spellings, channel="Stable", version="7")

        assert result.misses == (
            _miss(
                "SteamClient.Apps.RemoveShortcut",
                not_found=("RemoveShortcut",),
                losses=("changes to Steam shortcuts",),
                dev=True,
            ),
        )
        assert (result.channel, result.version) == ("Stable", "7")

    def test_an_exempted_internal_is_not_a_miss(self, spellings: Any) -> None:
        internals = [_internal("SteamClient.Browser.RestartJSContext", not_spelled_by_steam="native only")]

        result = watch.judge(internals, spellings, channel="Stable", version="7")

        assert result.misses == ()
        assert result.exempted_spelled == frozenset()

    def test_an_exempted_name_the_channel_spells_is_named(self, spellings: Any) -> None:
        internals = [_internal("SteamClient.Apps.AddShortcut", not_spelled_by_steam="thought native")]

        result = watch.judge(internals, spellings, channel="Stable", version="7")

        assert result.misses == ()
        assert result.exempted_spelled == frozenset({"SteamClient.Apps.AddShortcut"})

    def test_a_group_is_a_miss_only_when_no_member_is_found(self, spellings: Any) -> None:
        one_found = [
            _internal("App.BFinishedInitStageOne", one_of="init"),
            _internal("/apprunning", one_of="init"),
        ]
        none_found = [
            _internal("SteamUIStore.NavigateToRunningApp", "Resume", one_of="resume"),
            _internal("/apprunningnow", "Resume", one_of="resume"),
        ]

        held = watch.judge(one_found, spellings, channel="Stable", version="7")
        missed = watch.judge(none_found, spellings, channel="Stable", version="7")

        assert held.misses == ()
        assert held.lone_members_missing == ("App.BFinishedInitStageOne",)
        assert missed.misses == (
            _miss(
                "SteamUIStore.NavigateToRunningApp",
                "/apprunningnow",
                not_found=("SteamUIStore", "NavigateToRunningApp", "/apprunningnow"),
                losses=("Resume",),
            ),
        )
        assert missed.lone_members_missing == ()

    def test_an_exempted_member_holds_its_group(self, spellings: Any) -> None:
        internals = [
            _internal("SteamUIStore.NavigateToRunningApp", one_of="resume"),
            _internal("/apprunningnow", one_of="resume", not_spelled_by_steam="built in a template"),
        ]

        assert watch.judge(internals, spellings, channel="Stable", version="7").misses == ()


class TestTheList:
    def test_a_path_both_halves_list_is_one_entry_with_both_losses(self) -> None:
        panel = [_internal("webpackChunksteamui", "Tender's section on a game page")]
        backend = [_internal("webpackChunksteamui", "loading the panel"), _internal("SharedJSContext", "finding Steam")]

        assert watch.merge(panel, backend) == [
            _internal("webpackChunksteamui", "Tender's section on a game page", "loading the panel"),
            _internal("SharedJSContext", "finding Steam"),
        ]

    def test_a_path_both_halves_list_is_the_dev_builds_only_when_both_say_so(self) -> None:
        panel = [_internal("g_PopupManager", dev_build_only=True), _internal("App", dev_build_only=True)]
        backend = [_internal("g_PopupManager"), _internal("App", dev_build_only=True)]

        assert [entry.dev_build_only for entry in watch.merge(panel, backend)] == [False, True]

    def test_a_path_both_halves_list_keeps_the_exemption_and_the_group_either_half_states(self) -> None:
        panel = [_internal("a.b"), _internal("c.d", one_of="pair", not_spelled_by_steam="the panel's reason")]
        backend = [_internal("a.b", one_of="pair", not_spelled_by_steam="native only"), _internal("c.d")]

        merged = watch.merge(panel, backend)

        assert [(entry.one_of, entry.not_spelled_by_steam) for entry in merged] == [
            ("pair", "native only"),
            ("pair", "the panel's reason"),
        ]

    def test_reads_the_panel_dump(self) -> None:
        dump = json.dumps(
            [
                {
                    "path": "/library/app/",
                    "oneOf": None,
                    "notSpelledBySteam": "built in a template",
                    "devBuildOnly": True,
                    "losses": ["the desktop surface (dev build)"],
                }
            ]
        )

        assert watch.read_panel_half(dump) == [
            _internal(
                "/library/app/",
                "the desktop surface (dev build)",
                not_spelled_by_steam="built in a template",
                dev_build_only=True,
            )
        ]

    def test_reads_the_backend_half_with_its_exemptions(self) -> None:
        read = watch.read_backend_half()

        assert [(entry.path, entry.losses, entry.not_spelled_by_steam) for entry in read] == [
            (entry.path, (entry.feature,), entry.not_spelled_by_steam) for entry in STEAM_INTERNALS
        ]
        assert any(entry.not_spelled_by_steam for entry in read)


class TestTheManifest:
    def test_reads_the_version_and_the_ui_package(self) -> None:
        manifest = watch.parse_manifest((_FIXTURES / "client-manifest.vdf").read_text(encoding="utf-8"))

        assert manifest == watch.Manifest(version="1792618285", file="steamui_websrc_all.zip.4567", sha256="sha256")

    def test_refuses_a_manifest_without_the_ui_package(self) -> None:
        text = '"steamrt64"\n{\n\t"version"\t\t"1"\n}\n'

        with pytest.raises(watch.Refused, match="steamui_websrc_all"):
            watch.parse_manifest(text)

    def test_refuses_a_package_whose_sha256_is_not_the_manifests(self) -> None:
        package = _package()
        right = watch.Manifest("1", "f", hashlib.sha256(package).hexdigest())
        wrong = watch.Manifest("1", "f", hashlib.sha256(b"other").hexdigest())

        watch.verify(package, right)
        with pytest.raises(watch.Refused, match="the manifest says"):
            watch.verify(package, wrong)


class _Response:
    """A download that hands out ``chunks``, advancing ``clock`` by ``step`` seconds per chunk.

    ``length`` is the Content-Length it states, none when ``None``.
    """

    def __init__(self, chunks: list[bytes], clock: list[float], step: float, length: int | None = None) -> None:
        self._chunks, self._clock, self._step = chunks, clock, step
        self.headers = {} if length is None else {"Content-Length": str(length)}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read1(self, _size: int) -> bytes:
        self._clock[0] += self._step
        return self._chunks.pop(0) if self._chunks else b""


class TestFetch:
    def test_a_download_past_its_deadline_is_tried_again(self) -> None:
        clock = [0.0]
        stalled, quick = _Response([b"a"] * 10, clock, 50.0), _Response([b"a", b"b"], clock, 0.1)
        responses = [stalled, quick]

        data = watch.fetch("u", opener=lambda *_a, **_k: responses.pop(0), clock=lambda: clock[0], deadline=120.0)

        assert data == b"ab"
        assert responses == []

    def test_a_download_cut_short_of_its_content_length_is_tried_again(self) -> None:
        clock = [0.0]
        cut_short, whole = _Response([b"a"], clock, 0.1, length=2), _Response([b"a", b"b"], clock, 0.1, length=2)
        responses = [cut_short, whole]

        data = watch.fetch("u", opener=lambda *_a, **_k: responses.pop(0), clock=lambda: clock[0])

        assert data == b"ab"
        assert responses == []

    def test_a_download_cut_short_every_try_is_refused(self) -> None:
        clock = [0.0]

        def cut_short(*_a: object, **_k: object) -> _Response:
            return _Response([b"a"], clock, 0.1, length=2)

        with pytest.raises(watch.Refused, match="1 more expected"):
            watch.fetch("u", opener=cut_short, clock=lambda: clock[0])

    def test_a_download_that_fails_every_try_is_refused(self) -> None:
        def unreachable(*_a: object, **_k: object) -> _Response:
            raise OSError("no route")

        with pytest.raises(watch.Refused, match="no route; no route; no route"):
            watch.fetch("u", opener=unreachable, tries=3)


_RUN = "https://github.com/o/r/actions/runs/9"


class TestTheIssue:
    def test_the_issue_names_each_internal_the_channel_and_the_manifest_version(self) -> None:
        results = [
            _result(
                "Public beta",
                "1794079253",
                [
                    _miss(
                        "appStore.GetAppOverviewByAppID().controller_support",
                        not_found=("controller_support",),
                        losses=("game details in Steam's library",),
                    ),
                    _miss(
                        "SteamUIStore.NavigateToRunningApp",
                        "/apprunning",
                        losses=("Resume for a running game",),
                    ),
                ],
            ),
            _result("Stable", "1792618285"),
        ]
        finding = watch.finding_id(results)

        assert watch.issue_body(results, finding, _RUN) == (
            "The daily check against Valve's client CDN (`.github/workflows/steam-internals.yml`) found Steam "
            "internals Tender reaches that Steam's own JavaScript no longer spells.\n"
            "\n"
            "### Public beta — manifest 1794079253\n"
            "\n"
            "| Internal | Not found | What it costs |\n"
            "|---|---|---|\n"
            "| `appStore.GetAppOverviewByAppID().controller_support` | `controller_support` | game details in "
            "Steam's library |\n"
            "| `SteamUIStore.NavigateToRunningApp` or `/apprunning` | both | Resume for a running game |\n"
            "\n"
            "### Stable — manifest 1792618285\n"
            "\n"
            "Every listed internal was found.\n"
            "\n"
            "This is an early warning, not a proof: a name is looked for as text anywhere in the client's UI "
            "JavaScript, not on the object Tender reaches it on: a refactor that stops spelling it can raise this "
            "with nothing removed, a name spelled elsewhere can hide one that was, and a name Steam keeps can still "
            'have changed what it does. How a name is looked for: docs/architecture/frontend-bundles.md, "Every '
            'Steam internal Tender reaches".\n'
            "\n"
            f"Run: {_RUN}\n"
            "\n"
            f"<!-- steam-internals-watch: {finding} -->\n"
        )

    def test_a_comment_carries_the_finding_without_the_opening(self) -> None:
        results = [_result(misses=[_miss("g_PopupManager", not_found=("g_PopupManager",), losses=(), dev=True)])]
        finding = watch.finding_id(results)

        assert watch.comment_body(results, finding, None) == (
            "A later check found:\n"
            "\n"
            "### Stable — manifest 1\n"
            "\n"
            "| Internal | Not found | What it costs |\n"
            "|---|---|---|\n"
            "| `g_PopupManager` (dev build only) | `g_PopupManager` | a log line only |\n"
            "\n"
            f"{watch._CAVEAT}\n"
            "\n"
            f"<!-- steam-internals-watch: {finding} -->\n"
        )

    def test_an_exemption_both_channels_spell_is_named(self) -> None:
        results = [
            _result("Public beta", spelled=frozenset({"SP Desktop", "/library/app/"})),
            _result("Stable", spelled=frozenset({"SP Desktop"})),
        ]
        finding = watch.finding_id(results)
        body = watch.issue_body(results, finding, None)

        assert body.startswith(
            "The daily check against Valve's client CDN (`.github/workflows/steam-internals.yml`) found names "
            "exempted from it that Steam's own JavaScript now spells.\n"
        )
        assert (
            "### Exemptions no longer needed\n\n"
            "Steam's JavaScript now spells these exempted names in both channels: `SP Desktop`.\n"
        ) in body

    def test_a_run_with_nothing_missing_finds_nothing(self) -> None:
        results = [_result("Public beta", spelled=frozenset({"SP Desktop"})), _result("Stable")]

        assert watch.finding_id(results) is None

    def test_a_new_manifest_version_with_the_same_misses_is_the_same_finding(self) -> None:
        def run(version: str) -> str | None:
            return watch.finding_id([_result(version=version, misses=[_miss("a.b")])])

        assert run("1") == run("2")
        assert run("1") != watch.finding_id([_result(misses=[_miss("a.c")])])

    def test_results_that_differ_only_in_a_stale_exemption_are_different_findings(self) -> None:
        def run(*stale: str) -> str | None:
            spelled = frozenset(stale)
            return watch.finding_id(
                [_result("Public beta", spelled=spelled, misses=[_miss("a.b")]), _result(spelled=spelled)]
            )

        assert run() != run("SP Desktop")
        assert run("SP Desktop") != run("/library/app/")


class _FakeGh:
    """``gh`` as a recording stand-in: an open issue or none, its texts, and whether the label exists."""

    def __init__(self, *, issue: int | None = None, texts: Sequence[str] = (), label: bool = True) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self._issue, self._texts, self._label = issue, list(texts), label

    def __call__(self, arguments: Sequence[str], stdin: str | None) -> str:
        self.calls.append((list(arguments), stdin))
        verb = tuple(arguments[:2])
        if verb == ("label", "list"):
            return json.dumps([{"name": "steam-internals"}] if self._label else [{"name": "steam-internals-old"}])
        if verb == ("issue", "list"):
            return json.dumps([] if self._issue is None else [{"number": self._issue}])
        if verb == ("issue", "view"):
            body, *comments = self._texts
            return json.dumps({"body": body, "comments": [{"body": text} for text in comments]})
        return ""

    def verbs(self) -> list[tuple[str, str]]:
        return [(arguments[0], arguments[1]) for arguments, _ in self.calls]


def _missing(path: str = "a.b") -> list[Any]:
    return [_result(misses=[_miss(path)])]


def _marker(results: list[Any]) -> str:
    return f"<!-- steam-internals-watch: {watch.finding_id(results)} -->"


class TestReporting:
    def test_nothing_missing_calls_nothing(self) -> None:
        gh = _FakeGh()

        assert watch.report([_result()], watch.GitHub("o/r", gh), None) == "nothing to report"
        assert gh.calls == []

    def test_with_no_issue_open_it_opens_one_with_the_label_creating_the_label_first(self) -> None:
        gh = _FakeGh(label=False)
        results = _missing()

        assert watch.report(results, watch.GitHub("o/r", gh), _RUN) == "opened an issue"

        assert gh.verbs() == [("label", "list"), ("label", "create"), ("issue", "list"), ("issue", "create")]
        create, body = gh.calls[-1]
        assert create == [
            "issue",
            "create",
            "--repo",
            "o/r",
            "--title",
            "Steam internals missing from Steam's published client",
            "--label",
            "steam-internals",
            "--body-file",
            "-",
        ]
        assert body == watch.issue_body(results, watch.finding_id(results), _RUN)
        assert gh.calls[1][0][:3] == ["label", "create", "steam-internals"]

    def test_an_existing_label_is_not_created_again(self) -> None:
        gh = _FakeGh()

        watch.report(_missing(), watch.GitHub("o/r", gh), None)

        assert ("label", "create") not in gh.verbs()

    def test_an_open_issue_gets_a_comment_for_a_new_finding(self) -> None:
        gh = _FakeGh(issue=41, texts=[f"opened\n{_marker(_missing('a.c'))}"])
        results = _missing()

        assert watch.report(results, watch.GitHub("o/r", gh), None) == "commented on #41"

        assert gh.calls[-1] == (
            ["issue", "comment", "41", "--repo", "o/r", "--body-file", "-"],
            watch.comment_body(results, watch.finding_id(results), None),
        )

    def test_the_finding_the_issue_last_records_gets_no_comment(self) -> None:
        gh = _FakeGh(issue=41, texts=[f"opened\n{_marker(_missing('a.c'))}", f"later\n{_marker(_missing())}"])

        assert watch.report(_missing(), watch.GitHub("o/r", gh), None) == "#41 already records this finding"
        assert ("issue", "comment") not in gh.verbs()

    def test_a_finding_recorded_earlier_but_not_last_gets_a_comment(self) -> None:
        gh = _FakeGh(issue=41, texts=[f"opened\n{_marker(_missing())}", f"later\n{_marker(_missing('a.c'))}"])

        assert watch.report(_missing(), watch.GitHub("o/r", gh), None) == "commented on #41"


class TestTheRun:
    def test_a_channel_that_cannot_be_fetched_fails_the_run_and_reports_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = tmp_path / "panel.json"
        panel.write_text("[]", encoding="utf-8")
        reported: list[object] = []

        def unreachable(url: str, **_: object) -> bytes:
            raise watch.Refused(f"{url}: no route")

        monkeypatch.setattr(watch, "fetch", unreachable)
        monkeypatch.setattr(watch, "report", lambda *arguments: reported.append(arguments))
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

        assert watch.main(["--panel", str(panel)]) == 1
        assert reported == []
        assert "no route" in capsys.readouterr().err

    def test_a_package_that_does_not_match_its_manifest_fails_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = tmp_path / "panel.json"
        panel.write_text("[]", encoding="utf-8")
        manifest = (_FIXTURES / "client-manifest.vdf").read_text(encoding="utf-8").replace("SHA256", "00" * 32)
        reported: list[object] = []

        monkeypatch.setattr(watch, "fetch", lambda url, **_: manifest.encode() if "steam_client" in url else _package())
        monkeypatch.setattr(watch, "report", lambda *arguments: reported.append(arguments))
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

        assert watch.main(["--panel", str(panel)]) == 1
        assert reported == []

    def test_each_channel_is_judged_from_its_verified_package_and_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = tmp_path / "panel.json"
        entry = {"oneOf": None, "notSpelledBySteam": None, "devBuildOnly": False, "losses": ["a loss"]}
        panel.write_text(json.dumps([{**entry, "path": "SteamClient.Apps.AddShortcut"}, {**entry, "path": "x.Gone"}]))
        package = _package()
        manifest = (_FIXTURES / "client-manifest.vdf").read_text(encoding="utf-8")
        manifest = manifest.replace("SHA256", hashlib.sha256(package).hexdigest())
        fetched: list[str] = []
        reported: list[Any] = []

        def fetch(url: str, **_: object) -> bytes:
            fetched.append(url)
            return manifest.encode() if "steam_client" in url else package

        monkeypatch.setattr(watch, "fetch", fetch)
        monkeypatch.setattr(watch, "read_backend_half", list)
        monkeypatch.setattr(watch, "report", lambda results, *_: reported.append(results) or "reported")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

        assert watch.main(["--panel", str(panel)]) == 0

        cdn = "https://client-update.fastly.steamstatic.com/"
        assert fetched == [
            f"{cdn}steam_client_steamdeck_publicbeta_steamrt64",
            f"{cdn}steamui_websrc_all.zip.4567",
            f"{cdn}steam_client_steamdeck_stable_steamrt64",
            f"{cdn}steamui_websrc_all.zip.4567",
        ]
        [results] = reported
        assert [(result.channel, result.version) for result in results] == [
            ("Public beta", "1792618285"),
            ("Stable", "1792618285"),
        ]
        assert [miss.paths for result in results for miss in result.misses] == [("x.Gone",), ("x.Gone",)]
