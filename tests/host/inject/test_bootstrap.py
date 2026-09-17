"""The source that is evaluated into Steam: what it carries and what it must not."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

import pytest

from host.inject.bootstrap import (
    DISMISS,
    MARKER,
    TITLE,
    build_bootstrap,
    build_facts,
    marker_present_expression,
)

TOKEN = "a-secret-admission-token"
URLS = (f"http://127.0.0.1:27737/globals.js?token={TOKEN}", f"http://127.0.0.1:27737/index.js?token={TOKEN}")


def facts(**overrides):
    values = {
        "kind": "standalone",
        "version": "1.2.3",
        "steam_build": "1788652215",
        "log_path": "/home/deck/.local/state/romm-tender/backend.log",
        "urls": URLS,
        "token": TOKEN,
    }
    values.update(overrides)
    return build_facts(**values)


def folded_facts(source: str) -> dict[str, Any]:
    """Read back the one JSON object the template is built around."""
    found = re.search(r"const T = (\{.*?\});", source, re.DOTALL)
    assert found is not None, "the facts object is not where the template puts it"
    return json.loads(found.group(1))


class TestWhatTheSourceCarries:
    def test_the_facts_arrive_as_one_json_object(self):
        carried = folded_facts(build_bootstrap(facts()))
        assert carried["version"] == "1.2.3"
        assert carried["steam_build"] == "1788652215"
        assert carried["log_path"].endswith("backend.log")
        assert carried["urls"] == list(URLS)
        assert carried["marker"] == MARKER

    def test_a_steam_build_nobody_could_read_is_worded_rather_than_left_blank(self):
        assert folded_facts(build_bootstrap(facts(steam_build="")))["steam_build"] == "unknown"

    def test_the_card_says_what_happened_and_offers_a_way_off_the_screen(self):
        carried = folded_facts(build_bootstrap(facts()))
        assert carried["title"] == TITLE
        assert carried["dismiss"] == DISMISS
        assert "releases" in carried["updates"]

    def test_a_log_path_with_javascript_in_its_name_is_carried_as_data(self):
        awkward = '/tmp/"; window.owned = 1; //'
        carried = folded_facts(build_bootstrap(facts(log_path=awkward)))
        assert carried["log_path"] == awkward


class TestTheToken:
    def test_it_appears_only_inside_the_addresses_the_panel_is_loaded_from(self):
        source = build_bootstrap(facts())
        carried = folded_facts(source)
        assert [key for key, value in carried.items() if isinstance(value, str) and TOKEN in value] == ["token"]
        assert all(TOKEN in url for url in carried["urls"])

    def test_the_marker_left_on_the_window_carries_no_secret(self):
        source = build_bootstrap(facts())
        marker_write = source[source.index("win[T.marker] =") : source.index("const redact")]
        assert "T.token" not in marker_write
        assert "T.urls" not in marker_write

    def test_what_comes_back_to_the_backend_is_redacted_against_the_token_itself(self):
        source = build_bootstrap(facts())
        assert "split(T.token).join" in source
        assert "redact((error && error.stack) || error)" in source

    def test_the_card_renders_no_address(self):
        source = build_bootstrap(facts())
        card = source[source.index("const showLoadFailure") : source.index("return loadAll()")]
        assert "T.urls" not in card
        assert "T.token" not in card


class TestTheCardNeverTakesTheMachineOver:
    def test_nothing_but_the_dismiss_button_accepts_a_press(self):
        source = build_bootstrap(facts())
        card = source[source.index("const showLoadFailure") : source.index("return loadAll()")]
        assert card.count('pointerEvents: "none"') == 1
        assert card.count('pointerEvents: "auto"') == 1
        assert card.index('pointerEvents: "none"') < card.index('pointerEvents: "auto"')

    def test_the_dismiss_button_removes_the_card(self):
        assert 'dismiss.addEventListener("click", () => card.remove());' in build_bootstrap(facts())


class TestTheCardIsBuiltFromNothingThatCanBeMissing:
    def test_it_imports_nothing_of_its_own(self):
        source = build_bootstrap(facts())
        card = source[source.index("const showLoadFailure") : source.index("return loadAll()")]
        assert "import(" not in card
        assert "fetch(" not in card

    def test_it_uses_no_react_and_no_steam_globals(self):
        source = build_bootstrap(facts())
        for forbidden in ("SP_REACT", "SP_REACTDOM", "SP_JSX", "DFL", "React", "createRoot"):
            assert forbidden not in source

    def test_text_is_set_as_text_rather_than_parsed_as_markup(self):
        source = build_bootstrap(facts())
        assert "innerHTML" not in source
        assert "textContent" in source


class TestTheMarker:
    def test_the_source_claims_the_marker_before_it_loads_anything(self):
        source = build_bootstrap(facts())
        assert source.index("win[T.marker] =") < source.index("return loadAll()")

    def test_a_context_that_already_carries_the_panel_is_left_alone(self):
        assert "if (win[T.marker]) {" in build_bootstrap(facts())

    def test_the_question_the_injector_asks_names_the_same_global(self):
        assert MARKER in marker_present_expression()
        assert marker_present_expression().startswith("typeof window[")


class TestItIsValidJavaScript:
    def test_node_accepts_the_source_it_would_evaluate(self, tmp_path):
        """The one mechanical check available for a language this repo does not
        compile here: the expression is handed to a parser rather than read."""
        node = shutil.which("node")
        if node is None:  # pragma: no cover - node is a mise-managed tool
            pytest.skip("node is not on PATH")
        written = tmp_path / "bootstrap.js"
        written.write_text(build_bootstrap(facts()), encoding="utf-8")
        finished = subprocess.run([node, "--check", str(written)], capture_output=True, text=True, check=False)
        assert finished.returncode == 0, finished.stderr
