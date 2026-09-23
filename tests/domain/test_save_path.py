"""Tests for backend/domain/save_path.py"""

from __future__ import annotations

import dataclasses

import pytest

from domain.save_path import (
    LocalSaveTarget,
    compute_local_save_target,
    sanitize_save_filename,
)


class TestSanitizeSaveFilename:
    def test_clean_basename_returned_unchanged(self) -> None:
        assert sanitize_save_filename("example.srm") == "example.srm"

    def test_clean_name_with_spaces_and_parens_unchanged(self) -> None:
        """RetroArch-friendly filenames with USA/Europe tags are valid."""
        name = "Example Quest - Second Journey (USA).srm"
        assert sanitize_save_filename(name) == name

    def test_traversal_components_stripped_to_basename(self) -> None:
        """``../../etc/passwd`` → ``passwd`` (basename of last component)."""
        assert sanitize_save_filename("../../etc/passwd") == "passwd"

    def test_absolute_path_stripped_to_basename(self) -> None:
        assert sanitize_save_filename("/etc/passwd") == "passwd"

    def test_null_byte_rejected(self) -> None:
        with pytest.raises(ValueError, match="NUL byte"):
            sanitize_save_filename("foo\x00bar.srm")

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            sanitize_save_filename("")

    def test_dot_rejected(self) -> None:
        with pytest.raises(ValueError):
            sanitize_save_filename(".")

    def test_dotdot_rejected(self) -> None:
        with pytest.raises(ValueError):
            sanitize_save_filename("..")

    def test_trailing_separator_rejected(self) -> None:
        """``foo/`` has an empty basename, which is not a valid component."""
        with pytest.raises(ValueError):
            sanitize_save_filename("foo/")


# ---------------------------------------------------------------------------
# compute_local_save_target
# ---------------------------------------------------------------------------


class TestComputeLocalSaveTarget:
    def test_happy_path_clean_extension(self) -> None:
        """Clean ``file_extension`` produces ``<rom_name>.<ext>`` unchanged."""
        result = compute_local_save_target({"file_extension": "srm"}, "pokemon")
        assert result == LocalSaveTarget("pokemon.srm")
        assert result.filename == "pokemon.srm"
        assert result.fallback_extension is None
        assert result.sanitized_from is None

    def test_default_extension_when_missing(self) -> None:
        """A server save without ``file_extension`` defaults to ``srm``."""
        result = compute_local_save_target({}, "pokemon")
        assert result == LocalSaveTarget("pokemon.srm")

    def test_traversal_in_extension_strips_and_flags(self) -> None:
        """A traversal extension is sanitized to the safe basename."""
        result = compute_local_save_target({"file_extension": "../etc/passwd"}, "pokemon")
        # Sanitized filename is the basename of the joined target.
        assert result.filename == "passwd"
        assert result.sanitized_from == "pokemon.../etc/passwd"
        assert result.fallback_extension is None

    def test_trailing_separator_extension_falls_back(self) -> None:
        """``evil/`` makes the joined target end with a separator → ValueError → fallback."""
        result = compute_local_save_target({"file_extension": "evil/"}, "pokemon")
        assert result.filename == "pokemon.srm"
        assert result.fallback_extension == "evil/"
        assert result.sanitized_from is None

    def test_nul_byte_extension_falls_back(self) -> None:
        """A NUL byte in the extension is unusable → fallback to ``srm``."""
        result = compute_local_save_target({"file_extension": "srm\x00evil"}, "pokemon")
        assert result.filename == "pokemon.srm"
        assert result.fallback_extension == "srm\x00evil"
        assert result.sanitized_from is None

    def test_empty_string_extension_keeps_trailing_dot(self) -> None:
        """An empty ``file_extension`` produces ``<rom_name>.``.

        ``sanitize_save_filename`` accepts ``pokemon.`` as a single safe
        component (its basename is ``pokemon.``), so no diagnostic flag
        is set.
        """
        result = compute_local_save_target({"file_extension": ""}, "pokemon")
        assert result.filename == "pokemon."
        assert result.fallback_extension is None
        assert result.sanitized_from is None

    def test_dotdot_extension_survives_untouched(self) -> None:
        """``..`` as an extension is accepted: ``pokemon...`` is a valid basename.

        Fallback only kicks in when ``sanitize_save_filename`` raises,
        which happens for empty basenames or basenames of exactly
        ``"."`` / ``".."`` — none of which a ``rom_name + "." + ext``
        join can produce when ``rom_name`` is non-empty.
        """
        result = compute_local_save_target({"file_extension": ".."}, "pokemon")
        assert result.filename == "pokemon..."
        assert result.fallback_extension is None
        assert result.sanitized_from is None

    def test_result_is_frozen_dataclass(self) -> None:
        """``LocalSaveTarget`` is immutable — assignment raises ``FrozenInstanceError``."""
        result = compute_local_save_target({"file_extension": "srm"}, "pokemon")
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.filename = "other"  # type: ignore[misc]

    def test_result_is_hashable(self) -> None:
        """Frozen dataclasses are hashable — usable as dict keys / set members."""
        result = compute_local_save_target({"file_extension": "srm"}, "pokemon")
        assert hash(result) == hash(LocalSaveTarget("pokemon.srm"))
        assert {result} == {LocalSaveTarget("pokemon.srm")}
