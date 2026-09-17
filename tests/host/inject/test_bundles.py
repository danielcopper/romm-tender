"""Which bundles are loaded, and the one pairing that must never occur."""

from __future__ import annotations

from host.inject.bundles import (
    COEXISTENCE,
    COEXISTENCE_PANEL,
    GLOBALS_BUNDLE,
    STANDALONE,
    STANDALONE_PANEL,
    bundle_digest,
    choose_bundles,
)


class TestAloneOnTheMachine:
    def test_it_installs_the_react_globals_before_the_panel(self):
        choice = choose_bundles(decky_is_serving=False)
        assert choice.files == (GLOBALS_BUNDLE, STANDALONE_PANEL)
        assert choice.kind == STANDALONE

    def test_it_waits_only_for_steams_own_module_registry(self):
        choice = choose_bundles(decky_is_serving=False)
        assert "webpackChunksteamui" in choice.ready_when
        assert "DFL" not in choice.ready_when


class TestBesideDeckyLoader:
    def test_globals_are_never_loaded_where_decky_is_serving(self):
        """The absolute rule: that pairing is the one crash ever observed here."""
        assert GLOBALS_BUNDLE not in choose_bundles(decky_is_serving=True).files

    def test_it_loads_the_panel_that_shares_deckys_copy(self):
        choice = choose_bundles(decky_is_serving=True)
        assert choice.files == (COEXISTENCE_PANEL,)
        assert choice.kind == COEXISTENCE

    def test_the_standalone_panel_is_never_loaded_beside_decky_either(self):
        assert STANDALONE_PANEL not in choose_bundles(decky_is_serving=True).files

    def test_it_waits_for_deckys_copy_as_well_as_the_registry(self):
        choice = choose_bundles(decky_is_serving=True)
        assert "webpackChunksteamui" in choice.ready_when
        assert "DFL" in choice.ready_when

    def test_each_choice_carries_the_reason_it_was_taken(self):
        assert "Decky" in choose_bundles(decky_is_serving=True).because
        assert choose_bundles(decky_is_serving=False).because


class TestTheDigest:
    def test_the_same_bytes_answer_the_same_digest_wherever_they_sit(self, tmp_path):
        """It is over the names and the bytes, and over nothing else.

        Asked twice of ONE directory this says only that the function is
        deterministic; asked of two, it also says the root is not folded in —
        which is the property the watchdog rests on, because the fingerprint it
        compares was written by an earlier run and a moved installation is not
        a changed panel.
        """
        here, there = tmp_path / "here", tmp_path / "there"
        here.mkdir()
        there.mkdir()
        (here / "index.js").write_text("panel", encoding="utf-8")
        (there / "index.js").write_text("panel", encoding="utf-8")
        assert bundle_digest(str(here), ("index.js",)) == bundle_digest(str(there), ("index.js",))

    def test_changed_bytes_answer_a_different_digest(self, tmp_path):
        (tmp_path / "index.js").write_text("panel", encoding="utf-8")
        before = bundle_digest(str(tmp_path), ("index.js",))
        (tmp_path / "index.js").write_text("a newer panel", encoding="utf-8")
        assert bundle_digest(str(tmp_path), ("index.js",)) != before

    def test_a_file_that_arrives_changes_the_digest(self, tmp_path):
        (tmp_path / "index.js").write_text("panel", encoding="utf-8")
        absent = bundle_digest(str(tmp_path), ("globals.js", "index.js"))
        (tmp_path / "globals.js").write_text("globals", encoding="utf-8")
        assert bundle_digest(str(tmp_path), ("globals.js", "index.js")) != absent

    def test_the_two_choices_do_not_share_a_digest_for_the_same_bytes(self, tmp_path):
        (tmp_path / "index.js").write_text("same", encoding="utf-8")
        (tmp_path / "index-coexistence.js").write_text("same", encoding="utf-8")
        assert bundle_digest(str(tmp_path), ("index.js",)) != bundle_digest(str(tmp_path), ("index-coexistence.js",))
