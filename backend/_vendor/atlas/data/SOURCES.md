# Sources of the platform table

One block per upstream that `platform_table.json` is generated from (issue #784, D21 and D22). Each block names the
upstream's kind, the pinned revision, its licence as the upstream states it at that revision, the owner's decision to
ship it, and what is taken. Only keys are taken: system names, slugs, folder names and platform ids. Game data never is.
`scripts/generate_platform_table.py` reads this file and refuses to run when a block it reads from is missing or has no
licence or decision line.

Not shipped, by the same decision (D21): RetroBat, Recalbox, MobyGames, LaunchBox and libretro-database (its names reach
the table through libretro-core-info instead).

## `es-de` — ES-DE

- **kind:** frontend catalogue
- **revision:** tag `v3.5.0` = `50e4b600ae533d772bae3ff880d11a09b05dbe84`
- **licence:** MIT (`LICENSE` at the pin: "Copyright (c) 2024-2026 Northwestern Software AB, Copyright (c) 2020-2024
  Leon Styhre, Copyright (c) 2014 Alec Lofquist", MIT permission notice)
- **decision:** ship keys and the scraper maps' ids only, with notice (#784 D21, changed 2026-11-08)
- **taken:** system names (`<name>`) and their `<platform>` lists from `resources/systems/linux/es_systems.xml`;
  platform ids from `es-app/src/PlatformId.cpp`/`.h`; the ScreenScraper and TheGamesDB id each platform id is given in
  the scraper maps (`es-app/src/scrapers/ScreenScraper.cpp`, `GamesDBJSONScraper.cpp`) — the origin of the `thegamesdb`
  column and of most of the `screenscraper` column

## `retrodeck-es-de` — RetroDECK's ES-DE fork

- **kind:** frontend catalogue
- **revision:** tag `retrodeck-main-20260926-172324` = `15e1b236eafd2864bd99ffcc9853d2efa2a3916c` (the catalogue
  RetroDECK 0.10.10b deploys, byte-identical)
- **licence:** MIT (`LICENSE` at the pin, the same text as ES-DE's)
- **decision:** ship keys only, with notice (per #784 D21's rule for GPL/MIT catalogue sources)
- **taken:** system names from `resources/systems/linux/es_systems.xml`, live and inside XML comments

## `emudeck` — EmuDeck

- **kind:** distro catalogue
- **revision:** `dddb30229adbbd54d5ff7283cfd70e11c4322c26`
- **licence:** GPL-3.0 (`LICENSE` at the pin)
- **decision:** ship keys only, with notice (per #784 D21's rule for GPL/MIT catalogue sources)
- **taken:** the folder names of the `roms/` template and their display names in `roms/systems.txt`

## `romm` — RomM

- **kind:** server product
- **revision:** tag `5.4.1` = `9c74d0143fbb876f2f62260b91c6fd0b3f590226`
- **licence:** AGPL-3.0 (`LICENSE` at the pin)
- **decision:** ship the values only, re-keyed in atlas's own structure, never the files (#784 D21, changed 2026-11-08)
- **taken:** the `UniversalPlatformSlug` values (`backend/utils/platform_slugs.py`); the `PLATFORM_FS_ALIASES` folder
  names with the slug each resolves to (`backend/utils/platform_aliases.py`); the numeric IGDB platform id and the IGDB
  slug of each slug's record in `IGDB_PLATFORM_LIST` (`backend/adapters/services/igdb.py`) — the origin of the `igdb`
  column; and the ScreenScraper id of each slug in `SCREENSAVER_PLATFORM_LIST`
  (`backend/handler/metadata/ss_handler.py`), the second origin of the `screenscraper` column beside ES-DE's map — it
  adds the ids ES-DE's map lacks, such as Doom and the CPS boards; the `category` of an IGDB record is evidence for a
  kind override, never shipped

## `libretro-core-info` — libretro core info

- **kind:** core info
- **revision:** `eac3a2784d428444aa2bd472039b5c5540141af1`
- **licence:** MIT (`COPYING` at the pin, "The RetroArch team", 2019)
- **decision:** ship keys only, with notice (#784 D21; libretro-database, CC BY-SA 4.0, is not a source)
- **taken:** the `database` values of every `*_libretro.info`

## `batocera` — Batocera

- **kind:** distro catalogue
- **revision:** tag `batocera-43.1` = `155c2d8d304cbb53db52e9479dcf683392821d5c`
- **licence:** GPL-2.0-or-later (`COPYING` at the pin is Buildroot's text: "distributed under the terms of the GNU
  General Public License … either version 2 of the License, or (at your option) any later version"; the LGPL-3.0 text on
  the default branch came in after the pin, d30617e5)
- **decision:** ship keys only, with notice (#784 D21)
- **taken:** the system keys of `package/batocera/emulationstation/batocera-es-system/es_systems.yml` with their `name`,
  `hardware` and `platform` fields

## `knulli` — KNULLI

- **kind:** distro catalogue
- **revision:** tag `20260511` = `c1c7de479e56691c6665723046d9f25500e5ee74`
- **licence:** GPL-2.0-or-later (`COPYING` at the pin, Buildroot's text, byte-identical to Batocera's at its pin)
- **decision:** ship keys only, with notice (#784 D21)
- **taken:** the system keys of `package/emulationstation/knulli-es-system/es_systems.yml` with their `name`, `hardware`
  and `platform` fields

## `rocknix` — ROCKNIX

- **kind:** distro catalogue
- **revision:** tag `20261101` = `cefc14339a4ae18c02bb69888f27835e50664b60`
- **licence:** GPL-2.0 for ROCKNIX's own software and scripts; CC BY-NC-SA 4.0 for its branding and images (`LICENSE.md`
  at the pin)
- **decision:** ship keys only, with notice (#784 D21)
- **taken:** `SYSTEM_NAME`, `SYSTEM_FULLNAME`, `SYSTEM_HARDWARE` and `SYSTEM_PLATFORM` of every
  `config/emulators/<key>.conf`

## `wikidata` — Wikidata

- **kind:** registry
- **revision:** `57b505c0f8f866d7`, the digest of `scripts/wikidata_revisions.json`, which pins the revision of every
  item read (241 items, as of 2026-11-07); the generator reads exactly those revisions, and every statement cited
  carries its item, property and revision id
- **licence:** CC0 1.0 ("All structured data from the main, Property, Lexeme, and EntitySchema namespaces is available
  under the Creative Commons CC0 License")
- **decision:** a cross-check and a second citation, not the origin of any value (#784 D21, changed 2026-11-08)
- **taken:** nothing shipped as a value; an id the table carries gains the citation of every Wikidata statement that
  carries the same value (P5795 against the IGDB slug RomM records). Read: the values of normal or preferred rank of
  P5795 (Internet Game Database platform ID), P7757 (ScreenScraper platform ID) and P7623 (TheGamesDB platform ID);
  query
  `SELECT DISTINCT ?item WHERE { { ?item wdt:P5795 [] } UNION { ?item wdt:P7757 [] } UNION { ?item wdt:P7623 [] } }`
  chooses the items when the pin is renewed (`--repin-wikidata`); statements of deprecated rank are skipped
