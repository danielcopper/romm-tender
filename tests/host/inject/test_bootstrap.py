"""The source that is evaluated into Steam: what it carries and what it must not."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from host.inject.bootstrap import (
    GLOBALS_INSTALLER,
    GLOBALS_MISSING,
    GLOBALS_UNREPORTED,
    MARKER,
    NO_INSTALLER,
    STEAM_NOT_READY,
    STEAM_READY,
    STOP,
    STOP_BINDING,
    STOP_NOTE,
    STOP_PAYLOAD,
    TITLE,
    build_bootstrap,
    build_facts,
    marker_present_expression,
)

TOKEN = "a-secret-admission-token"
URLS = (f"http://127.0.0.1:27737/globals.js?token={TOKEN}", f"http://127.0.0.1:27737/index.js?token={TOKEN}")

_STEAM_GLOBALS_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "boot" / "steamGlobals.ts"


def facts(**overrides):
    values = {
        "kind": "standalone",
        "version": "1.2.3",
        "steam_build": "1788652215",
        "log_path": "/home/deck/.local/state/romm-tender/backend.log",
        "urls": URLS,
        "globals_at": 0,
        "token": TOKEN,
        "binding": STOP_BINDING,
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

    def test_the_card_says_what_happened_and_where_updates_are_listed(self):
        carried = folded_facts(build_bootstrap(facts()))
        assert carried["title"] == TITLE
        assert "releases" in carried["updates"]

    def test_the_address_of_the_releases_is_text_rather_than_a_button(self):
        """Nothing here updates Tender yet, so a button would be a button that lies."""
        source = build_bootstrap(facts())
        card = source[source.index("const showLoadFailure") : source.index("return loadAll()")]
        assert "T.updates" in card
        assert card.count('el("button"') == 1

    def test_a_log_path_with_javascript_in_its_name_is_carried_as_data(self):
        awkward = '/tmp/"; window.owned = 1; //'
        carried = folded_facts(build_bootstrap(facts(log_path=awkward)))
        assert carried["log_path"] == awkward


def loading(source: str) -> str:
    """The part that imports the bundles, without the card that may follow."""
    return source[source.index("  const installTheGlobals") : source.index("  const el =")]


def installer(source: str) -> str:
    """The helper that calls the installer and reads what it answered."""
    return source[source.index("  const installTheGlobals") : source.index("  const loadAll")]


class TestInstallingTheGlobals:
    def test_the_installer_is_called_between_the_two_imports(self):
        """There is one import statement, in a loop, and the call is after it.

        So the order is read off three facts rather than off a second import
        written out below the first: the loop imports, the call follows it in the
        same turn, and it is gated on the one index the choice named — which for
        the standalone pair is the bundle that defines the installer.
        """
        source = build_bootstrap(facts())
        body = loading(source)
        assert body.count("await import(") == 1
        assert body.index("await import(T.urls[i]);") < body.index("if (i === T.globals_at)")
        assert body.index("if (i === T.globals_at)") < body.index("await installTheGlobals();")
        assert folded_facts(source)["globals_at"] == 0
        assert len(folded_facts(source)["urls"]) == 2

    def test_nothing_is_called_where_no_bundle_in_the_list_installs_anything(self):
        """Beside Decky the loader installed them, so this may install nothing.

        ``i`` counts from 0 and is never null, so the gate the loop is written
        around never opens for this choice.
        """
        source = build_bootstrap(facts(globals_at=None, urls=URLS[1:]))
        assert folded_facts(source)["globals_at"] is None

    def test_a_missing_global_stops_the_panel_being_imported(self):
        """The call throws and is awaited, so the loop never reaches the panel.

        A report read into a flag, or a call left un-awaited, would import the
        panel anyway — and the panel reads those globals while its own modules
        evaluate, so it would throw there instead, with nothing left to say why.
        """
        source = build_bootstrap(facts())
        assert "await installTheGlobals();" in loading(source)
        assert "throw new Error(" in installer(source)

    def test_the_refusal_names_the_missing_globals_and_whether_steam_was_ready(self):
        source = build_bootstrap(facts())
        carried = folded_facts(source)
        assert carried["globals_missing"] == GLOBALS_MISSING
        assert carried["steam_ready"] == STEAM_READY
        assert carried["steam_not_ready"] == STEAM_NOT_READY
        body = installer(source)
        assert 'missing.join(", ")' in body
        assert "report.steamReady ? T.steam_ready : T.steam_not_ready" in body

    def test_the_names_are_the_installers_own_rather_than_a_list_kept_here(self):
        """A list spelled here is one more thing to keep in step with the panel."""
        body = installer(build_bootstrap(facts()))
        assert "Object.keys(installed)" in body

    def test_a_report_that_names_nothing_is_a_refusal_rather_than_a_pass(self):
        """Thrown on its own, so the sentence claims nothing about the registry.

        An answer that did not say what it installed names no missing global,
        and is no evidence about Steam's registry either.
        """
        source = build_bootstrap(facts())
        assert folded_facts(source)["globals_unreported"] == GLOBALS_UNREPORTED
        assert "throw new Error(T.globals_unreported);" in installer(source)

    def test_a_bundle_that_left_no_installer_is_reported_as_that(self):
        source = build_bootstrap(facts())
        assert folded_facts(source)["no_installer"] == NO_INSTALLER
        assert 'typeof install !== "function"' in installer(source)

    def test_the_refusal_carries_no_address_and_no_token(self):
        """The sentence reaches the card and the log, so it carries neither."""
        body = installer(build_bootstrap(facts()))
        assert "T.urls" not in body
        assert "T.token" not in body
        assert TOKEN not in body


class TestTheInstallersName:
    def test_it_is_spelled_the_same_in_both_languages(self):
        """The one name this backend and the panel's own bundle both write.

        Read as text rather than imported, because nothing here runs TypeScript:
        a rename on either side has to fail somewhere, and this is the only place
        the two spellings meet.
        """
        written = _STEAM_GLOBALS_TS.read_text(encoding="utf-8")
        assert f"w.{GLOBALS_INSTALLER} = installGlobals;" in written

    def test_the_source_reaches_it_by_that_name(self):
        assert folded_facts(build_bootstrap(facts()))["installer"] == GLOBALS_INSTALLER
        assert "win[T.installer]" in build_bootstrap(facts())


class TestTheToken:
    def test_the_only_field_carrying_it_is_the_one_the_redaction_reads(self):
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


class TestTheOneButton:
    def test_it_says_which_program_restarting_means(self):
        carried = folded_facts(build_bootstrap(facts()))
        assert carried["stop"] == STOP
        assert carried["stop_note"] == STOP_NOTE
        assert "backend process" in carried["stop_note"]

    def test_pressing_it_tells_the_backend_and_takes_the_card_away(self):
        source = build_bootstrap(facts())
        assert "ask(T.stop_payload);" in source
        assert "card.remove();" in source
        assert folded_facts(source)["stop_payload"] == STOP_PAYLOAD

    def test_it_reaches_the_backend_through_the_debugger_and_not_a_socket(self):
        source = build_bootstrap(facts())
        assert folded_facts(source)["binding"] == STOP_BINDING
        assert "win[T.binding]" in source
        for forbidden in ("WebSocket", "fetch(", "XMLHttpRequest"):
            assert forbidden not in source

    def test_no_button_is_drawn_where_there_is_nothing_for_it_to_reach(self):
        """A button that cannot report a press is worse here than no button."""
        source = build_bootstrap(facts(binding=""))
        assert folded_facts(source)["binding"] == ""
        assert "if (T.binding) {" in source


class TestTheCardNeverTakesTheMachineOver:
    def test_nothing_but_the_one_button_accepts_a_press(self):
        source = build_bootstrap(facts())
        card = source[source.index("const showLoadFailure") : source.index("return loadAll()")]
        assert card.count('pointerEvents: "none"') == 1
        assert card.count('pointerEvents: "auto"') == 1
        assert card.index('pointerEvents: "none"') < card.index('pointerEvents: "auto"')

    def test_the_button_is_the_only_thing_that_accepts_one(self):
        source = build_bootstrap(facts())
        card = source[source.index("const showLoadFailure") : source.index("return loadAll()")]
        assert card.count('el("button"') == 1


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
