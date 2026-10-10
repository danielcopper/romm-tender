"""Which zstd codec the resolver gets, and whether a zstd AppImage opens through it.

Read through the resolver's own answers — ``atlas.zstd_provider()`` and a real
AppImage read on ``RealMachine`` — rather than through what the adapter did,
because what is under test is the codec the resolver would use. Absence is
staged in ``sys.modules``, which the import machinery consults first: ``None``
there makes an import raise ``ImportError``, the way a compiled extension this
machine's loader refuses does.
"""

from __future__ import annotations

import base64
import sys
import types
from typing import TYPE_CHECKING

import pytest
from _vendor import atlas
from _vendor.atlas.machine import RealMachine

from adapters.atlas_zstd import STANDARD_LIBRARY_CODEC, VENDORED_CODEC, register_zstd_codec

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

ON_PYTHON_3_13 = sys.version_info[:2] == (3, 13)

# A 64-bit ELF header with one empty section header, then a squashfs 4.0 image
# compressed with zstd that holds ES-DE's catalogue at the path EmuDeck's
# AppImage carries it under — the read the codec exists for.
_SEALED_APPIMAGE = base64.b64decode(
    "f0VMRgIBAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAEAAAQAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABoc3FzBQAAAAAAAAAAAAIAAQAAAAYA"
    "EQDAAAEABAAAAIAAAAAAAAAAFAIAAAAAAAAMAgAAAAAAAP//////////IwEAAAAAAABvAQAAAAAAANQBAAAAAAAA/gEAAAAAAAAo"
    "tS/9YEsLzQUAMggYFZCrAxj4UT4Uqp1XZZFkN9nt9O+vE+ndu5l3VTMR78wKC927mXdVMxHvzOrq3LuZd1UzEe/Mysrcu5l3VTMR"
    "78zSp69CqDASwhiSxDk+fVVgKMTyAUJA0DCIoBgN8zAfV6ggMkOGPQOgFTRtBhKIUBgChqAh+IbAIbiiFvYHQYygVwp962vpZSXX"
    "11ouNXlx/UP7WVHHrZ68XnL9bOVRk4/CXCQd2bJ1svbYuawF2xsCzUgpkhRKACi1L/0goA0CAHJDChXAZxgD///X///QufViRNQP"
    "DC1pUqYXLo1zJgvpkdgd+f5ANfWgDIq5CSBQKz6G0FUGQ38rQxhlcGXZ3PUGUQAotS/9IHNFAgByRA0TkE1rwEIw2nUizDg3zcYR"
    "JGttp8eUJ3KOPYfqydVC9eNS1Cztql482BjJOIZBNx1yLBFYAwYAIIOtMmJVCd1s3ywZuAIQgGAAAAAAAAAAwwAAAAAAAADCAQAA"
    "AAAAACAAKLUv/SAovQAAosEDCfAZAxuJEhI1Bf/FP38FAQBkEAbcAQAAAAAAAASAAAAAAAYCAAAAAAAA"
)
_CATALOGUE_INSIDE = "resources/systems/linux/es_systems.xml"


@pytest.fixture(autouse=True)
def _no_registration() -> Iterator[None]:
    atlas.register_zstd_provider(None)
    yield
    atlas.register_zstd_provider(None)


@pytest.fixture
def sealed_appimage(tmp_path: Path) -> str:
    path = tmp_path / "ES-DE.AppImage"
    path.write_bytes(_SEALED_APPIMAGE)
    return str(path)


@pytest.fixture
def no_standard_library_codec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, STANDARD_LIBRARY_CODEC, None)


@pytest.fixture
def vendored_copy_does_not_load(monkeypatch: pytest.MonkeyPatch, no_standard_library_codec: None) -> None:
    monkeypatch.delitem(sys.modules, VENDORED_CODEC, raising=False)
    monkeypatch.setitem(sys.modules, f"{VENDORED_CODEC}._zstd", None)


@pytest.mark.skipif(not ON_PYTHON_3_13, reason="the vendored build is cp313")
class TestOnPython313:
    def test_the_vendored_backport_is_registered_and_named(self, no_standard_library_codec: None) -> None:
        line = register_zstd_codec()

        assert atlas.zstd_provider() == atlas.ZstdProvider(VENDORED_CODEC, True)
        assert line == "atlas zstd codec: _vendor.backports.zstd (registered)"

    def test_a_zstd_appimage_opens_through_it(self, no_standard_library_codec: None, sealed_appimage: str) -> None:
        register_zstd_codec()

        read = RealMachine().read_appimage_text(sealed_appimage, _CATALOGUE_INSIDE)

        assert read.status == "ok"
        assert read.text is not None
        assert read.text.startswith("<systemList>\n")


class TestWhereTheStandardLibraryHasTheCodec:
    def test_nothing_is_registered_and_the_standard_library_answers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        standard_library = types.SimpleNamespace(decompress=lambda data: data)
        monkeypatch.setitem(sys.modules, STANDARD_LIBRARY_CODEC, standard_library)

        line = register_zstd_codec()

        assert atlas.zstd_provider() == atlas.ZstdProvider(STANDARD_LIBRARY_CODEC, False)
        assert line == "atlas zstd codec: compression.zstd (atlas's own)"

    @pytest.mark.skipif(sys.version_info < (3, 14), reason="compression.zstd is in the standard library from 3.14")
    def test_on_python_3_14_the_real_module_answers(self, sealed_appimage: str) -> None:
        register_zstd_codec()

        assert atlas.zstd_provider() == atlas.ZstdProvider(STANDARD_LIBRARY_CODEC, False)
        assert RealMachine().read_appimage_text(sealed_appimage, _CATALOGUE_INSIDE).status == "ok"


@pytest.mark.usefixtures("vendored_copy_does_not_load")
class TestWhereTheVendoredCopyDoesNotLoad:
    def test_nothing_is_registered_and_no_provider_answers(self) -> None:
        line = register_zstd_codec()

        assert atlas.zstd_provider() is None
        assert line.startswith("atlas zstd codec: none — _vendor.backports.zstd does not load (")

    def test_a_zstd_appimage_stays_sealed(self, sealed_appimage: str) -> None:
        # ``capability-missing`` is the read the resolver answers
        # ``emulator-catalogue-sealed`` from.
        register_zstd_codec()

        read = RealMachine().read_appimage_text(sealed_appimage, _CATALOGUE_INSIDE)

        assert read.status == "capability-missing"
