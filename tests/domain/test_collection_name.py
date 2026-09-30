"""Tests for domain.collection_name.

The names and folds are read from ``collection_name_folds.json`` beside this
file, which ``frontend/src/utils/collectionName.test.ts`` reads too, so the
backend and the frontend are held to the same key for every name in it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.collection_name import fold_collection_name

_FOLDS = json.loads(Path(__file__).with_name("collection_name_folds.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("entry", _FOLDS, ids=[entry["case"] for entry in _FOLDS])
def test_a_name_folds_to_the_key_both_sides_agree_on(entry):
    assert fold_collection_name(entry["name"]) == entry["fold"]


def test_names_that_differ_beyond_case_keep_different_keys():
    assert fold_collection_name("7 Up") != fold_collection_name("7 Up Deluxe")


def test_an_empty_name_folds_to_itself():
    assert fold_collection_name("") == ""
