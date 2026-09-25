"""The two questions put to Steam before a stranded panel is replaced, run under node.

The injector tier answers these expressions from a table, so what they do in a
page is asserted here: against a stub window, with the shapes Steam's store is
known to take and the ones it must not be mistaken for.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import pytest

from host.inject.recovery import RELOAD_DELAY_MS, RELOAD_EXPRESSION, RUNNING_APPS_EXPRESSION


def run_under_node(tmp_path, prelude: str, body: str) -> dict[str, Any]:
    """Run *prelude*, then *body*, as one module; answer what *body* printed."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - node is a mise-managed tool
        pytest.skip("node is not on PATH")
    script = tmp_path / "recovery.mjs"
    script.write_text(prelude + "\n" + body, encoding="utf-8")
    finished = subprocess.run([node, str(script)], capture_output=True, text=True, check=False)
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def running_apps(tmp_path, prelude: str) -> dict[str, Any]:
    body = (
        "let answer;\n"
        f"try {{ answer = {{ value: {RUNNING_APPS_EXPRESSION} }}; }}\n"
        "catch (error) { answer = { threw: String(error) }; }\n"
        "console.log(JSON.stringify(answer));\n"
    )
    return run_under_node(tmp_path, prelude, body)


class TestWhichAppsAreRunning:
    def test_the_names_the_store_lists(self, tmp_path):
        prelude = (
            "globalThis.SteamUIStore = { RunningApps: ["
            '{ appid: 1, display_name: "Celeste" }, { appid: 2, strDisplayName: "Hades" }, { appid: 3 }'
            "] };"
        )
        assert running_apps(tmp_path, prelude) == {"value": ["Celeste", "Hades", "3"]}

    def test_an_empty_list_is_the_one_answer_that_says_nothing_runs(self, tmp_path):
        assert running_apps(tmp_path, "globalThis.SteamUIStore = { RunningApps: [] };") == {"value": []}

    def test_an_iterable_that_is_not_an_array_is_read_as_one(self, tmp_path):
        prelude = 'globalThis.SteamUIStore = { RunningApps: new Set([{ appid: 1, display_name: "Celeste" }]) };'
        assert running_apps(tmp_path, prelude) == {"value": ["Celeste"]}

    @pytest.mark.parametrize(
        "prelude",
        [
            "",
            "globalThis.SteamUIStore = null;",
            "globalThis.SteamUIStore = {};",
            "globalThis.SteamUIStore = { RunningApps: null };",
            "globalThis.SteamUIStore = { RunningApps: { length: 0 } };",
            "globalThis.SteamUIStore = { RunningApps: 5 };",
        ],
        ids=["no-store", "null-store", "no-list", "null-list", "array-like", "number"],
    )
    def test_anything_it_cannot_read_is_no_answer_rather_than_an_empty_list(self, tmp_path, prelude):
        assert running_apps(tmp_path, prelude) == {"value": None}

    def test_a_store_that_throws_throws(self, tmp_path):
        prelude = "globalThis.SteamUIStore = { get RunningApps() { throw new Error('not yet'); } };"
        assert "not yet" in running_apps(tmp_path, prelude)["threw"]


class TestTheReload:
    def reload(self, tmp_path, prelude: str) -> dict[str, Any]:
        body = (
            f"const value = {RELOAD_EXPRESSION};\n"
            "const before = globalThis.__restarts;\n"
            f"await new Promise((done) => setTimeout(done, {RELOAD_DELAY_MS * 2}));\n"
            "console.log(JSON.stringify({ value, before, after: globalThis.__restarts }));\n"
        )
        return run_under_node(tmp_path, "globalThis.__restarts = 0;\n" + prelude, body)

    def test_it_is_scheduled_so_the_evaluation_returns_before_the_context_goes(self, tmp_path):
        prelude = (
            "globalThis.window = { SteamClient: { Browser: {"
            " RestartJSContext: () => { globalThis.__restarts += 1; } } } };"
        )
        assert self.reload(tmp_path, prelude) == {"value": True, "before": 0, "after": 1}

    @pytest.mark.parametrize(
        "prelude",
        ["globalThis.window = {};", "globalThis.window = { SteamClient: { Browser: {} } };"],
        ids=["no-client", "no-method"],
    )
    def test_a_steam_without_the_call_says_so_and_does_nothing(self, tmp_path, prelude):
        assert self.reload(tmp_path, prelude) == {"value": False, "before": 0, "after": 0}
