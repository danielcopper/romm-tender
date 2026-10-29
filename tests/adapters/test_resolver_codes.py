"""Every code the vendored resolver states is classified by a reading module.

A code is classified once a reading module names it: it imports the code's
constant from the resolver, to read it or to hold it in a set it deliberately
ignores or cannot reach, with the reason beside that set. This test sees the
import and not the reason. An update of the resolver that adds a code fails
here until a reading module classifies it, and one that drops a code a reading
module names fails here too.

The codes and the modules that read the answer are the ones
``scripts/check_resolver_reading.py`` watches: ``CODE_FAMILIES`` and
``READING_MODULES``.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

_REPO = Path(__file__).resolve().parents[2]


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_resolver_reading", _REPO / "scripts" / "check_resolver_reading.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_check = _load_check_module()
_FAMILIES: tuple[str, ...] = _check.CODE_FAMILIES


def _codes() -> frozenset[str]:
    return frozenset(_check.resolver_codes())


def _named_in(source: str) -> frozenset[str]:
    """The constants of the code families *source* imports from the resolver."""
    return frozenset(
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("_vendor.atlas")
        for alias in node.names
        if alias.name.startswith(_FAMILIES)
    )


def _classified() -> frozenset[str]:
    backend = _REPO / "backend"
    return frozenset().union(
        *(_named_in((backend / module).read_text(encoding="utf-8")) for module in _check.READING_MODULES)
    )


# The codes no reading module classifies today. The list only shrinks: a code a
# reading module comes to classify leaves it, and a code the resolver adds is
# classified rather than added here — except a code of an answer no reading
# module reads yet (``launch_command``'s codes), which waits here with that
# answer's other codes until its reading module classifies them (#2644 D1,
# #2658 D1).
_UNCLASSIFIED = frozenset(
    {
        "CAVEAT_APPENDED_CONFIG_NOT_READ",
        "CAVEAT_APPENDED_CONFIG_OVERRIDDEN",
        "CAVEAT_APPENDED_CONFIG_VISIBILITY_UNESTABLISHED",
        "CAVEAT_APP_RELATIVE_PATH_UNEXPANDED",
        "CAVEAT_ARRANGEMENT_UNVERIFIED",
        "CAVEAT_CFG_LINE_DROPPED",
        "CAVEAT_CFG_VALUE_REJECTED",
        "CAVEAT_CONTENT_DIR_OBSERVATION",
        "CAVEAT_CONTENT_PATH_UNNAMED",
        "CAVEAT_CORE_DIR_UNRESOLVED",
        "CAVEAT_CORE_ENUMERATION_INCOMPLETE",
        "CAVEAT_CORE_GENERATION_MISMATCH",
        "CAVEAT_CORE_GENERATION_UNESTABLISHED",
        "CAVEAT_CORE_INFO_UNREADABLE",
        "CAVEAT_CORE_MULTI_OPTION",
        "CAVEAT_CORE_OPTIONS_UNAUDITED",
        "CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED",
        "CAVEAT_CORE_OWN_WRITES_UNESTABLISHED",
        "CAVEAT_CORE_SAVESTATES_UNSUPPORTED",
        "CAVEAT_CORE_SUSPECT",
        "CAVEAT_CORE_UNAUDITED",
        "CAVEAT_CORE_UNQUERYABLE",
        "CAVEAT_CORE_WITHOUT_SYSTEMNAME",
        "CAVEAT_CWD_HIDDEN_IN_SANDBOX",
        "CAVEAT_DEAD_SYMLINK",
        "CAVEAT_EMULATOR_CATALOGUE_EXCLUSIVE",
        "CAVEAT_EMULATOR_CONFIG_MISSING",
        "CAVEAT_EMULATOR_CONFIG_UNREAD",
        "CAVEAT_EMULATOR_CONFIG_UNREADABLE",
        "CAVEAT_EMULATOR_LIST_DERIVED",
        "CAVEAT_EMULATOR_READ_UNESTABLISHED",
        "CAVEAT_FEATURE_SWITCH_ABSENT",
        "CAVEAT_FILENAMES_CONTENT_CONDITIONAL",
        "CAVEAT_FILENAMES_UNVERIFIED",
        "CAVEAT_FILE_NAMES_UNESTABLISHED",
        "CAVEAT_FILE_SET_ACROSS_SYSTEMS",
        "CAVEAT_FILE_SET_CLAIMS_MERGED",
        "CAVEAT_FILE_SET_DIRECTORIES_UNREAD",
        "CAVEAT_FILE_SET_SPANS_ROOTS",
        "CAVEAT_FIRMWARE_BUILTIN_REPLACEMENT",
        "CAVEAT_FIRMWARE_CONTENT_CONTRADICTORY",
        "CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED",
        "CAVEAT_FIRMWARE_CONTENT_UNSTATED",
        "CAVEAT_FIRMWARE_CORE_SUBSTITUTE",
        "CAVEAT_FIRMWARE_DECLARATION_UNKNOWN",
        "CAVEAT_FIRMWARE_DECLARATION_UNREAD",
        "CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE",
        "CAVEAT_FIRMWARE_IDENTITY_NOT_COMPARABLE",
        "CAVEAT_FIRMWARE_IMAGE_CONFIGURED",
        "CAVEAT_FIRMWARE_IMAGE_REFUSED",
        "CAVEAT_FIRMWARE_INSTALLER_DOWNLOAD",
        "CAVEAT_FIRMWARE_NAME_SPELLINGS",
        "CAVEAT_FIRMWARE_PACKAGED_DECLARATION",
        "CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT",
        "CAVEAT_FIRMWARE_PATH_INACCESSIBLE",
        "CAVEAT_FIRMWARE_PATH_LAUNCH_DEPENDENT",
        "CAVEAT_FIRMWARE_PATH_NOT_A_DIRECTORY",
        "CAVEAT_FIRMWARE_PATH_OBSTRUCTED",
        "CAVEAT_FIRMWARE_PATH_UNRESOLVABLE",
        "CAVEAT_FIRMWARE_ROOT_MISSING",
        "CAVEAT_FIRMWARE_ROOT_UNUSABLE",
        "CAVEAT_FIRMWARE_SEARCH_CANDIDATES",
        "CAVEAT_FIRMWARE_SUPPLIED_SOURCE_UNREADABLE",
        "CAVEAT_FRONTEND_BUILD_SEALED",
        "CAVEAT_FRONTEND_BUILD_UNPINNED",
        "CAVEAT_FRONTEND_MARKER_MISMATCH",
        "CAVEAT_FRONTEND_SETTINGS_UNREADABLE",
        "CAVEAT_INFO_PATH_UNRESOLVED",
        "CAVEAT_INVALID_SAVE_DIRECTORY",
        "CAVEAT_INVALID_SCREENSHOT_DIRECTORY",
        "CAVEAT_LAUNCHER_SCRIPT_UNPINNED",
        "CAVEAT_LAUNCH_COMMAND_ARGUMENT_BROKEN",
        "CAVEAT_LAUNCH_COMMAND_BEYOND_LIMITS",
        "CAVEAT_LAUNCH_COMMAND_DESKTOP_FILE",
        "CAVEAT_LAUNCH_COMMAND_ENTRY_INVALID",
        "CAVEAT_LAUNCH_COMMAND_ENV_OPTION",
        "CAVEAT_LAUNCH_COMMAND_INJECT_LOOP",
        "CAVEAT_LAUNCH_COMMAND_INJECT_UNREADABLE",
        "CAVEAT_LAUNCH_COMMAND_NO_PROGRAM",
        "CAVEAT_LAUNCH_COMMAND_PLACEHOLDER_UNKNOWN",
        "CAVEAT_LAUNCH_COMMAND_ROM_ROOT_UNESTABLISHED",
        "CAVEAT_LAUNCH_COMMAND_SECOND_SHELL_UNSAFE",
        "CAVEAT_LAUNCH_COMMAND_SHELL_SYNTAX",
        "CAVEAT_LAUNCH_ENVIRONMENT_UNPINNED",
        "CAVEAT_NO_CORE",
        "CAVEAT_NO_FIRMWARE_DECLARATION",
        "CAVEAT_NO_FIRMWARE_REQUIREMENT",
        "CAVEAT_OPTION_ENTRY_RETIRED",
        "CAVEAT_PATCH_FORMATS_UNESTABLISHED",
        "CAVEAT_PER_GAME_ALTERNATIVE_EMULATOR",
        "CAVEAT_PER_GAME_BUILD_LAYER_UNREAD",
        "CAVEAT_PER_GAME_LAYER_UNREAD",
        "CAVEAT_PER_GAME_OVERRIDE",
        "CAVEAT_PER_GAME_OVERRIDES_PRESENT",
        "CAVEAT_PLATFORM_SCRAPING_IGNORED",
        "CAVEAT_PLATFORM_UNKNOWN",
        "CAVEAT_PLATFORM_UNMAPPED",
        "CAVEAT_REGION_PROPERTY_UNCONFIRMED",
        "CAVEAT_ROM_PATH_UNRESOLVED",
        "CAVEAT_SANDBOX_PATH_UNTRANSLATED",
        "CAVEAT_SAVESTATE_INSIDE_IMAGE",
        "CAVEAT_SAVESTATE_SUPPORT_MACHINE_DEPENDENT",
        "CAVEAT_SAVE_DIRECTORY_VISIBILITY_UNESTABLISHED",
        "CAVEAT_SAVE_DIR_LAUNCH_DEPENDENT",
        "CAVEAT_SAVE_DIR_UNLISTABLE",
        "CAVEAT_SAVE_FILES_COMPRESSED",
        "CAVEAT_SAVE_ID_CANDIDATES",
        "CAVEAT_SAVE_INSIDE_CONTENT",
        "CAVEAT_SAVE_INSIDE_IMAGE",
        "CAVEAT_SAVE_ROOT_REDIRECTED",
        "CAVEAT_SAVE_ROOT_REVOKED",
        "CAVEAT_SAVE_ROOT_UNRESOLVABLE",
        "CAVEAT_SAVE_WRITES_DISCARDED",
        "CAVEAT_SIZE_FIXED_EXCEPT_TRAILING_FILL",
        "CAVEAT_SOFT_PATCHING_APPLIES",
        "CAVEAT_SORTED_DIR_MISSING",
        "CAVEAT_SORTED_DIR_UNCREATABLE",
        "CAVEAT_STANDALONE_UNSUPPORTED",
        "CAVEAT_SYMLINK_LOOP",
        "CAVEAT_SYSTEM_ASSIGNMENT_DERIVED",
        "CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES",
        "CAVEAT_SYSTEM_DIRECTORY_CLEARED",
        "CAVEAT_SYSTEM_NOT_IN_CATALOGUE",
        "CAVEAT_UNKNOWN_OPTION_VALUE",
        "CAVEAT_UNVERIFIED_VERSION",
        "HEALTH_ISSUE_BIOS_ROOT_MISSING",
        "HEALTH_ISSUE_COMPANION_CONFIG_MISSING",
        "HEALTH_ISSUE_CONFIG_UNREADABLE",
        "HEALTH_ISSUE_CONTENT_TREE_UNWIRED",
        "HEALTH_ISSUE_EMULATOR_CONFIG_MISSING",
        "HEALTH_ISSUE_MARKER_INVALID",
        "HEALTH_ISSUE_MARKER_MISSING",
        "HEALTH_ISSUE_MARKER_UNREADABLE",
        "HEALTH_ISSUE_ROMS_ROOT_MISSING",
        "HEALTH_ISSUE_ROMS_ROOT_NOT_ABSOLUTE",
        "HEALTH_ISSUE_SAVES_ROOT_MISSING",
        "REASON_ACTIVE_USER_UNRECORDED",
        "REASON_ARCHIVE_CONTENT_AMBIGUOUS",
        "REASON_ARCHIVE_CONTENT_MIXED",
        "REASON_ARCHIVE_CONTENT_UNRECOGNISED",
        "REASON_ARCHIVE_FORMAT_UNREAD",
        "REASON_ARCHIVE_MEMBER_PINNED",
        "REASON_ARCHIVE_UNREAD",
        "REASON_CARD_INDEX_OUTSIDE_RECORDED_NAMES",
        "REASON_CONFIGURED_USER_HAS_NO_TREE",
        "REASON_CONFIGURED_USER_ID_UNREAD",
        "REASON_CONFIGURED_USER_NOT_SET_UP",
        "REASON_CONFIGURED_USER_REACH_UNESTABLISHED",
        "REASON_CONFIGURED_USER_SETUP_UNESTABLISHED",
        "REASON_CONFIGURED_USER_TREE_NAMED",
        "REASON_CONTENT_CLASS_UNNAMED",
        "REASON_CONTENT_CLASS_UNRECORDED",
        "REASON_CONTENT_UNOPENABLE",
        "REASON_DATA_ROOT_DECIDED_BY_LAUNCH",
        "REASON_DISC_FORMAT_UNREAD",
        "REASON_DISC_UNREAD",
        "REASON_EMULATED_MODEL_UNRECORDED",
        "REASON_EMULATOR_BUILD_UNESTABLISHED",
        "REASON_FIRST_LISTED_USER_OPENED",
        "REASON_FIRST_USER_CREATED",
        "REASON_HDD_PATH_UNSET",
        "REASON_HD_BOOT_ABSENT",
        "REASON_HD_IMAGE_UNREAD",
        "REASON_INI_OUTRANKED_BY_CASCADE",
        "REASON_INI_PRESENCE_UNESTABLISHED",
        "REASON_INI_SEARCH_PATH_UNLISTABLE",
        "REASON_KEY_REPEATED",
        "REASON_KEY_UNREAD",
        "REASON_LISTED_USER_ACCOUNT_UNESTABLISHED",
        "REASON_MLC_LAUNCH_FLAG_OUTRANKS_CONFIG",
        "REASON_NO_LISTED_USER_ACCOUNT",
        "REASON_NO_USER_DIRECTORY",
        "REASON_NO_USER_PRESELECTED",
        "REASON_REGION_PROPERTY_UNREAD",
        "REASON_SAVEPATH_CONFIG_UNREADABLE",
        "REASON_SAVEPATH_UNTRANSLATABLE",
        "REASON_SESSION_OVERRIDE_SET",
        "REASON_SLOT_DEVICE_UNINTERPRETED",
        "REASON_SLOT_HOLDS_AGP_DEVICE",
        "REASON_UNPARSEABLE",
        "REASON_UNSET_USER_ID_IS_LISTED",
        "REASON_USER_LISTING_UNESTABLISHED",
        "REASON_VIRTUAL_SD_DISABLED",
        "REASON_VOLUME_BOOTS_ITSELF",
        "REASON_WHDLOAD_PREFS_UNREAD",
        "REASON_WHDLOAD_SAVEPATH_UNRECORDED",
        "UNRESOLVED_APPENDED_CONFIG_UNREACHABLE",
        "UNRESOLVED_CORE_FILE_FOREIGN",
        "UNRESOLVED_CORE_NOT_INSTALLED",
        "UNRESOLVED_EMULATOR_CONFIG_MISSING",
        "UNRESOLVED_EMULATOR_CONFIG_PATH_UNTRANSLATABLE",
        "UNRESOLVED_EMULATOR_CONFIG_UNREADABLE",
        "UNRESOLVED_MOD_WIRING_UNESTABLISHED",
        "UNRESOLVED_NOT_SET_UP",
        "UNRESOLVED_SLOT_DEVICE_UNINTERPRETED",
        "UNRESOLVED_STANDALONE",
        "UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED",
        "UNRESOLVED_TEXTURE_WIRING_UNESTABLISHED",
    }
)


class TestEveryCodeIsClassified:
    def test_every_code_the_resolver_states_is_classified_or_listed(self):
        assert _codes() - _classified() - _UNCLASSIFIED == set()

    def test_every_code_a_reading_module_names_is_the_resolvers(self):
        assert _classified() - _codes() == set()

    def test_the_list_holds_only_codes_the_resolver_states_and_nothing_classifies(self):
        assert _UNCLASSIFIED - _codes() == set()
        assert _UNCLASSIFIED & _classified() == set()

    def test_a_code_is_classified_by_an_import_from_the_resolver(self):
        source = (
            "from _vendor.atlas import CAVEAT_FIRMWARE_UNREADABLE, FirmwareAnswer\n"
            "from _vendor.atlas.firmware import HEALTH_ISSUE_NOT_SET_UP\n"
            "from domain.save_answer import CAVEAT_SORTED_DIR_MISSING\n"
        )

        assert _named_in(source) == {"CAVEAT_FIRMWARE_UNREADABLE", "HEALTH_ISSUE_NOT_SET_UP"}
