"""Where this program's directories are, for each rung of the ladder."""

from __future__ import annotations

import pytest

from domain.app_directories import (
    ENV_BIN_DIR,
    ENV_CACHE_DIR,
    ENV_CODE_DIR,
    ENV_CONFIG_DIR,
    ENV_DATA_DIR,
    ENV_STATE_DIR,
    XDG_CACHE_HOME,
    XDG_CONFIG_HOME,
    XDG_DATA_HOME,
    XDG_RUNTIME_DIR,
    XDG_STATE_HOME,
    resolve_directories,
)
from domain.user_data_location import APP_DIR_NAME

HOME = "/home/deck"
CODE_FALLBACK = "/checkout/backend"


def resolve(**environ):
    return resolve_directories(environ, HOME, CODE_FALLBACK)


class TestTheBuiltInDefaults:
    """The bottom rung — a start by hand with nothing set at all."""

    @pytest.mark.parametrize(
        ("attribute", "expected"),
        [
            ("config_dir", f"{HOME}/.config/{APP_DIR_NAME}"),
            ("data_dir", f"{HOME}/.local/share/{APP_DIR_NAME}"),
            ("cache_dir", f"{HOME}/.cache/{APP_DIR_NAME}"),
            ("state_dir", f"{HOME}/.local/state/{APP_DIR_NAME}"),
        ],
    )
    def test_each_root_lands_where_xdg_says(self, attribute, expected):
        assert getattr(resolve(), attribute) == expected

    def test_the_code_directory_falls_back_to_where_the_program_sits(self):
        assert resolve().code_dir == CODE_FALLBACK

    def test_the_bin_directory_is_where_a_users_own_executables_go(self):
        """Named by the basedir spec as a path, so there is no XDG rung above it."""
        assert resolve().bin_dir == f"{HOME}/.local/bin"

    def test_the_runtime_directory_falls_back_to_the_state_directory(self):
        """XDG names no default for it; the note there is a hint either way."""
        answer = resolve()

        assert answer.runtime_dir == answer.state_dir


class TestTheXdgRung:
    @pytest.mark.parametrize(
        ("variable", "attribute"),
        [
            (XDG_CONFIG_HOME, "config_dir"),
            (XDG_DATA_HOME, "data_dir"),
            (XDG_CACHE_HOME, "cache_dir"),
            (XDG_STATE_HOME, "state_dir"),
        ],
    )
    def test_each_variable_moves_its_root(self, variable, attribute):
        answer = resolve(**{variable: "/elsewhere"})

        assert getattr(answer, attribute) == f"/elsewhere/{APP_DIR_NAME}"

    def test_the_runtime_directory_is_named_after_the_program_too(self):
        answer = resolve(**{XDG_RUNTIME_DIR: "/run/user/1000"})

        assert answer.runtime_dir == f"/run/user/1000/{APP_DIR_NAME}"

    def test_one_variable_moves_only_its_own_root(self):
        answer = resolve(**{XDG_DATA_HOME: "/elsewhere"})

        assert answer.data_dir == f"/elsewhere/{APP_DIR_NAME}"
        assert answer.config_dir == f"{HOME}/.config/{APP_DIR_NAME}"

    def test_no_xdg_variable_moves_the_bin_directory(self):
        """There is none for it, and it is not named after this program either."""
        answer = resolve(**{XDG_DATA_HOME: "/elsewhere", XDG_CONFIG_HOME: "/elsewhere"})

        assert answer.bin_dir == f"{HOME}/.local/bin"


class TestTheInstallersRung:
    """The top rung: the answers written into the unit, used verbatim."""

    @pytest.mark.parametrize(
        ("variable", "attribute"),
        [
            (ENV_CONFIG_DIR, "config_dir"),
            (ENV_DATA_DIR, "data_dir"),
            (ENV_CACHE_DIR, "cache_dir"),
            (ENV_STATE_DIR, "state_dir"),
            (ENV_CODE_DIR, "code_dir"),
            (ENV_BIN_DIR, "bin_dir"),
        ],
    )
    def test_each_answer_is_taken_as_it_stands(self, variable, attribute):
        answer = resolve(**{variable: "/opt/tender/somewhere"})

        assert getattr(answer, attribute) == "/opt/tender/somewhere"

    def test_it_outranks_the_matching_xdg_variable(self):
        answer = resolve(**{ENV_DATA_DIR: "/opt/data", XDG_DATA_HOME: "/elsewhere"})

        assert answer.data_dir == "/opt/data"

    def test_the_runtime_directory_still_follows_the_state_answer(self):
        answer = resolve(**{ENV_STATE_DIR: "/opt/state"})

        assert answer.runtime_dir == "/opt/state"


class TestEmptyValues:
    @pytest.mark.parametrize("value", ["", "   "])
    @pytest.mark.parametrize("variable", [ENV_DATA_DIR, XDG_DATA_HOME])
    def test_a_variable_that_says_nothing_counts_as_unset(self, variable, value):
        """An empty path would resolve to wherever the process was started."""
        answer = resolve(**{variable: value})

        assert answer.data_dir == f"{HOME}/.local/share/{APP_DIR_NAME}"

    def test_an_empty_runtime_variable_falls_back_to_the_state_directory(self):
        answer = resolve(**{XDG_RUNTIME_DIR: ""})

        assert answer.runtime_dir == answer.state_dir


class TestTheSplitBetweenTheRoots:
    def test_data_and_cache_are_never_the_same_directory(self):
        """One holds the only copy of what the user chose; the other is re-fetchable."""
        answer = resolve()

        assert answer.data_dir != answer.cache_dir

    def test_every_root_is_named_after_the_program(self):
        answer = resolve()

        for root in (answer.config_dir, answer.data_dir, answer.cache_dir, answer.state_dir):
            assert root.endswith(f"/{APP_DIR_NAME}")

    def test_the_bin_directory_is_not(self):
        """It is shared with every other program the user installed for themselves."""
        assert not resolve().bin_dir.endswith(f"/{APP_DIR_NAME}")
