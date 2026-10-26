# The conformance vectors — what they are and how to read them

These files are the normative artifact: a fixture machine goes in, the answers atlas must give come out. They are what a
**port** is checked against (a reimplementation in another language passes atlas's vectors or it is not a port), and
what a change to the resolver is measured by. The reference implementation runs them in `tests/test_machine_vectors.py`;
the shape rules below are enforced independently by `scripts/validate_vectors.py`, which is stdlib-only so a port author
can run it without importing atlas.

The whole idea in one line: **the expected block is the canonical serialization of a real answer, asserted with exact
equality.** Nothing here is a description of an answer — it is the answer.

## Layout

```
vectors/<family>/*.json
```

One family today, `machines`. Every file carries a header and its vectors:

| key           | meaning                                                   |
| ------------- | --------------------------------------------------------- |
| `family`      | must match the directory (`machines`)                     |
| `schema`      | the generation of the fixture grammar — **5** (see below) |
| `spec`        | which document this file's guarantees come from           |
| `description` | what this file covers                                     |
| `vectors`     | the vectors themselves                                    |

A vector is `{name, rationale?, input, expected}`. `name` is unique within its file; `rationale` is prose for the reader
and part of no contract. Two uniqueness rules matter more than they look:

- **names are unique per file** — a duplicate would make a failure impossible to place;
- **canonical inputs are unique across the whole family** — the same machine asked the same question twice, with two
  expectations, means one of them is never the answer and nothing says which.

### Why the schema number

Schema 5 asks more of a port than 4 did: a machine answers a core probe with the way it came back (`Machine.read_core`),
a fixture states that per core (`cores`), and the `core-unqueryable` caveat carries it as `reason`. A port built to 4
answers one unknown for every failure and states no reason, so the corpus is not the same promise — the number says so
once, instead of leaving it to be discovered one failing vector at a time. Schema 4 was the previous such step: a
machine answered two archive reads — the member list of a zip or an LhA, and the WHDLoad slave inside one — and a
fixture modeled both (`archives`, `whdload_slaves`). Schema 3 was the one before: `glob` began reporting how much of the
walk it could read, and a fixture could state a directory that exists and cannot be listed.

## `input` — a machine as plain data

`home` and `files` are required; everything else is optional.

```json
{
  "home": "/home/deck",
  "files": {
    "/home/deck/.config/retroarch/retroarch.cfg": "savefile_directory = \"/saves\"\n",
    "/saves/chmod-000.srm": { "status": "unreadable", "size": 8192 },
    "/bios/gba_bios.bin": { "md5": "a860e8c0…", "size": 16384 }
  },
  "dirs": ["/saves/empty"],
  "symlinks": { "/saves/link": "/elsewhere" },
  "cores": { "/cores/mgba_libretro.so": { "library_name": "mGBA" } },
  "unlistable": ["/saves/mode-000"],
  "inaccessible": ["/mnt/unplugged-card"]
}
```

**`files`** — one entry per file, keyed by absolute path. Three spellings, and each state has exactly one:

- **a string** — readable text content.
- **`{"status": "unreadable" | "invalid-text"}`**, optionally with `"size"` — a file that exists and yields that read
  outcome. The `size` belongs with it because the two come from different reads on a real machine: a chmod-000 file
  `stat`s fine and only its bytes fail.
- **`{"md5": …, "sha1": …, "size": …}`** — a binary blob: it exists, reads as `invalid-text`, and answers those values
  for `file_digest` / `file_size`. Any subset of the three, plus a **scoped** digest under `"<algorithm>:<bytes>"`
  (`"md5:524288"`): the digest of the file's first N bytes, which an emulator that hashes a fixed-length prefix asks for
  and which is a different fact from the file's own digest. It is needed only where the file is longer than the scope —
  inside it the read yields the whole file, so the bare algorithm answers both, and a scope at least the stated `size`
  is refused as one fact written twice. The scope is decimal, at least 1, with no leading zero.

**`dirs`** — directories that exist with nothing in them. Parents of every path named anywhere are directories already;
this list is how an _empty_ one is stated. A configured save root must be a directory: list it here, or put a file in
it.

**`symlinks`** — link path → target (absolute, or relative to the link's directory). A target that is in no other list
is a **dead** link, which is a state the corpus deliberately covers.

**`cores`** — `.so` path → what probing it answers: `{"library_name": …}`, optionally `library_version` and `options`
(`{key: {"default": str|null, "values": [str, …]}}`). The distinction between a core with no `options` key and one whose
`options` is `{}` is load-bearing: the first says nothing was captured, the second is evidence that the core registers
none.

A probe has more ways to come back empty than a file read has, and each is its own word, because the caller acts
differently on each: `"unloadable"` (the loader refused the `.so`), `"unusable"` (it ran to an end of its own and named
no core), `"crashed"` (it died by signal before naming one), `"timed-out"` (it was killed at the timeout before naming
one), `"no-interpreter"` (nothing here could run a probe, so no process started) and `"not-started"` (the spawn itself
failed). `null` is `"unloadable"` spelled the way it always was, and it still means exactly that.

Two things are declared by not declaring. A `.so` with no entry here is the loader's refusal too — a fixture's files are
text and blobs, never a loadable library, so what a real loader answers for those bytes is what an undeclared path
answers. And a path with nothing at it, or one under a directory whose `stat` fails, is a binary nothing could be loaded
from (`binary-inaccessible`), which is why that word is refused as a state string: one machine would have two spellings
a resolver cannot tell apart. A declaration outranks what the rest of the tree says about its path, the way an
AppImage's entries do.

**`appimages`** — AppImage path (absolute) → what a read inside it answers, modeled at the seam rather than as bytes: an
object of entries, each a relative entry path → its text or `{"status": "unreadable" | "invalid-text"}`, or one
whole-image state — `"unreadable"`, `"not-appimage"` (the file is not an AppImage with a squashfs inside) or
`"capability-missing"` (the runtime lacks the codec the image is compressed with, which is the process's failure, not
the file's). A fixture never carries the image itself, so a vector stays independent of the codec and of the
interpreter's version. Absence is declared by not declaring: a path with no entry here is a missing AppImage, and an
entry its image does not list answers `entry-missing`.

**`appimage_stamps`** — AppImage path → entry path → the build stamp a search of that entry's bytes yields, or
`"no-stamp"` (nothing matches) / `"ambiguous"` (more than one place does, and the read refuses to pick between them).
The search pattern is the caller's and is never applied to a fixture: the declaration states what applying it yields. A
stamp describes an entry's bytes, so its path must be an AppImage `appimages` declares with entries, not a whole-image
state, and its entry one that image declares and not one declared `unreadable`, whose bytes never come back to be
searched; an entry declared `invalid-text` is searched like any other, because a binary is what a stamp is read out of.
A declared entry with no stamp here answers **no stamp**, so a vector that forgot the key cannot pass a build off as
stamped.

**`ps2_bios_headers`** — the path of a file `files` declares → what the core's own ROMDIR read of it yields:
`{"romver": …, "serial": …}`, the two strings the walk reads — `romver` exactly 14 characters, `serial` at most 15, each
character one byte (latin-1), and no NUL in `serial`, which a C string read from fifteen bytes cannot hold — from which
the reference builds the same header fields its reader builds; or `"unreadable"` / `"not-a-bios"` (the file was read and
fails the core's own test, which is a finding about the bytes, not a read failure). It is modeled at the seam for the
reason `appimages` is: the real read walks a table inside a whole BIOS image, which no fixture should carry. The file
must not be one declared `unreadable` — its bytes are what cannot be read, so a real read answers `unreadable` before
any test. A declared file with no entry here answers **missing**, which the firmware folder route states as
`firmware-unreadable`, a read that did not happen, so a vector that forgot the key cannot pass a file off as no BIOS.

**`disc_identities`** — the path of a PlayStation disc image `files` declares (a `.cue`, `.bin`, `.img`, `.iso`, `.chd`
or `.pbp`) → what reading its first data track yields:
`{"license": …, "system_cnf": …, "boot_found": …, "edc_valid": …}`, where `license` is the start of sector 4's user
data, at most 72 characters and padded with NUL to that length, `system_cnf` the text of `SYSTEM.CNF` or `null` where
the disc has none — the sector Beetle reads is that text, cut or NUL-padded to 2048 bytes — `boot_found` whether the
executable DuckStation's `BOOT` parse names exists on the disc, and `edc_valid` whether every sector Beetle's route
reads passes its checksum; every character is one byte (latin-1). Or one of the states `"missing"` (a file the image
names is not there — the image's own absence is an undeclared path), `"unreadable"` or `"not-a-disc"` (a cue sheet the
parser refuses, or one whose first track is audio). It is modeled at the seam for the reason `ps2_bios_headers` is: the
real read walks a disc image no fixture should carry. A path with any other suffix answers `format-not-read` from its
suffix alone, so it is never declared here, and the file must not be one declared `unreadable`. A declared image with no
entry answers **missing**, so a vector that forgot the key cannot prove a region nobody modeled. A `.m3u` is never
declared here either: each emulator reads its own playlist from `files` as text, and the entry it names is what this
read is asked about.

A `.chd` takes its own two shapes. Where its header was not read, a state alone: `"unreadable"`, `"not-a-disc"` (no CHD
header libchdr opens) or `"format-not-read"` (a CHD of a version before 5). Where it was,
`{"codecs": […], "requires_parent": …, "identity": …}`: `codecs` the tags of the header's non-empty codec slots in slot
order (at most four, each four one-byte characters, such as `"cdlz"`), which each emulator's own open compares with the
codecs its libchdr carries; `requires_parent` whether the header names a parent CHD; and `identity` either the four
facts above or the state a read past the header ended in — `"unreadable"`, `"not-a-disc"`, `"format-not-read"` (a hunk
in a codec atlas does not read, or a track 1 the emulators read apart), `"codec-unavailable"` (a codec atlas reads and
the running Python lacks) or `"parent-not-found"`, which only a header naming a parent can answer: the parent
DuckStation looks for among the files of the CHD's folder is not there. That is a fact about the parent rather than a
missing file, because no path names the parent, so a CHD is never `"missing"`. Whether an emulator opens the image at
all follows from the two header facts and that state, never from the identity: SwanStation and Beetle PSX open no CHD
that names a parent, found or not, and SwanStation none with a `cdzs` codec.

A `.pbp` takes two shapes of its own. `"unreadable"` alone, where its bytes did not come back for either reader; else
`{"duckstation_refuses": …, "encrypted": …, "identity": …}`: `duckstation_refuses` whether DuckStation's and
SwanStation's open refuses the file before its disc — over its `PARAM.SFO` or an encrypted image — `encrypted` whether
Beetle PSX finds an encrypted image, which it decrypts and atlas does not read, and `identity` the four facts as the
readers that reach the disc read it, or the state that read ended in — `"unreadable"`, `"not-a-disc"`,
`"format-not-read"` (no reader reaches the disc, or two read it apart) or `"codec-unavailable"` (the runtime lacks
`zlib`). A PBP is one file, so it is never `"missing"`. Which emulator opens it follows from the two facts, never from
the identity.

An **archive entry** is declared here too, under RetroArch's own spelling for content inside an archive:
`"<archive>#<entry>"`, the archive a path `archives` declares and the entry one its listing names. The value takes the
shapes the entry's own suffix takes, and answers the read of the entry where RetroArch writes it before the core opens
it — so a `.cue` entry is `"missing"` where the file it names is not beside where it was written.

**`archives`** — archive path → the member list a walk of it answers (`["Game.info", "Game/Game.slave"]`, relative and
`/`-separated), or `"unreadable"` / `"not-archive"`. The path must be a declared file whose name ends in `zip`, `apk`,
`lha` or `lzh`, because a listing here describes that file's bytes and those are the formats a machine opens; a declared
file with no entry answers **not an archive**, so a vector that forgot the key cannot pass a file off as one that lists.
The same listing answers both reads of an archive: PUAE's walk of a mounted container, and the listing of a zip
RetroArch extracts for a libretro core, in which the members are the entry names as the archive stores them and in its
order.

**`archive_texts`** — `"<archive>#<entry>"` → the text of that archive entry, for an entry read as text: an `.m3u`
RetroArch extracts for a libretro core. The entry must be one `archives` lists in that archive; a listed entry with no
text here answers **missing**.

A **directory** is a container too, and it is never declared here: a machine lists one as itself and its members follow
from the paths in `files`. That holds whatever the directory is called — a directory named `Game.zip` is a directory,
because the core asks what a path _is_ before it looks at the name, so declaring it under `archives` is a shape no
machine can answer.

**`whdload_slaves`** — the path of the container the core **mounts** → what the WHDLoad slave inside it states:
`{"slave": "Game/Game.slave", "version": 17, "name": "Alien Breed", "selected_by": "script"}`, where `name` is `null`
for a slave older than version 10 (which has no such field) and `selected_by` is `"script"` (the core's own boot-script
search named the member) or `"only-slave"` (the script named none and the container offers WHDLoad exactly one), or one
of the states `"no-slave"` / `"ambiguous"` / `"slave-unreadable"`. The key is the mount rather than the launch path, so
an archive and a directory stand under themselves while a `.slave` or an `.info` stands under the **drawer** beside it.
The container's own failures are not spelled here — they come from `archives`, or from `files` for a directory, so the
two reads can never describe different machines — and a stated `slave` must be a member that container lists. A listed
container with no entry here answers **no slave**.

Both fields of that entry are held to the reader: the reference suite selects a slave over the declared listing and
fails the vector where the member or the route it returns is not the one declared, so a fixture cannot state a route the
boot script would not have taken.

**`unlistable` vs `inaccessible`** — two ways to be unreadable, told apart by one question: does the `stat` succeed?

- `unlistable` — it does. The path _is_ a directory and its contents cannot be read. That is what a mode-000 directory
  answers about itself, and the only such state a resolver reaches after passing an "is it a directory?" check.
- `inaccessible` — it does not, so nothing about the path can be told. This is what the paths _below_ a mode-000
  directory answer, and what a mount point answers when the card behind it stops responding. **Declaring a directory
  declares its whole subtree.**

A path in both lists is a contradiction, not a mode-000 shorthand, and is refused.

### The question a vector asks

Each expectation is paired with the input key that asks it. Several may appear in one vector; each pair must be complete
— a query with no expectation asks nothing, an expectation with no query is checked by nobody.

| input key                | expected key                  | the question                                                |
| ------------------------ | ----------------------------- | ----------------------------------------------------------- |
| `savefile_query`         | `savefile_location`           | where does this savefile live                               |
| `entry_savefile_query`   | `entry_savefile_location`     | the same placement, asked _through_ a catalogue entry       |
| `savestate_query`        | `savestate_location`          | where does this savestate live                              |
| `entry_savestate_query`  | `entry_savestate_location`    | the same placement, asked _through_ a catalogue entry       |
| `screenshot_query`       | `screenshot_location`         | where does RetroArch write screenshots for this content     |
| `texture_query`          | `texture_pack_location`       | where does this emulator read texture packs                 |
| `entry_texture_query`    | `entry_texture_pack_location` | the same, asked _through_ a catalogue entry                 |
| `mod_query`              | `mod_location`                | where does this emulator read mods                          |
| `entry_mod_query`        | `entry_mod_location`          | the same, asked _through_ a catalogue entry                 |
| `soft_patch_query`       | `soft_patch_candidates`       | which patch files this content would be patched with        |
| `catalogue_query`        | `catalogue`                   | which emulators can launch this system                      |
| `systems_query`          | `systems`                     | which systems does the frontend declare                     |
| `platform_systems_query` | `systems_for_platform`        | which systems here answer to a public platform id           |
| `platform_ids_query`     | `platform_ids`                | which public platform ids belong to this system             |
| `launchable_query`       | `launchable`                  | does this file launch as this system's content, and why not |
| `rom_location_query`     | `rom_location`                | where do this system's ROMs live                            |
| `rom_locations_query`    | `rom_locations`               | where do the ROMs of every listed system live               |
| `launch_command_query`   | `launch_command`              | the command the frontend would run for this file            |
| `roots_query`            | `roots`                       | where each root folder is, and what decided it              |
| `firmware_query`         | `firmware`                    | `kind` is `core`, `system` or `inventory`                   |
| `identify_query`         | `identification`              | what is this content, by `md5` / `sha1` / `size`            |
| `aggregate_query`        | `aggregate`                   | one question put to **every** detected installation         |

`soft_patch_query` is the one family whose `content_path` is required rather than optional: the content is that
question's subject — the patch files are named after it — so a vector asking it without one asks about no file at all.

`launch_command_query` carries `system` and an absolute `content_path`, as the question requires, and may name the
catalogue entry by its `label`; without one the frontend's own choice answers.

`rom_locations_query` asks every system the `systems` answer lists at once and, like `systems_query`, carries nothing
but an optional `installation`: each entry of its answer is the `rom_location` answer for that system, and the answer's
own `caveats` are the `systems` answer's.

`firmware_query` may carry an absolute `content_path` with `kind` `core` or `system`: the game the launch is for, whose
disc decides the region of a group the console region selects. An `inventory` query takes none, because it asks about no
launch.

`launchable_query` carries `system` and `content_path`, both required — a verdict without a file has no extension to
judge, and one without a system has no accept-list to judge it by — and takes no `label`.

`screenshot_query` carries what `savefile_query` and `texture_query` carry, a non-empty subset of `content_path`,
`core_so` and `installation`, and like `texture_query` it takes no `cwd`.

`platform_systems_query` carries `vocabulary` and `value`: the vocabulary is one of `igdb`, `libretro`, `screenscraper`
and `thegamesdb`, a closed set, so any other is refused; the value is the id in that vocabulary as a string, and a
numeric id is written as its decimal string, never as a JSON number. `platform_ids_query` asks the reverse direction and
carries `system` alone.

`cwd` — the working folder the launch will use, an absolute path — may ride only the questions whose answer can depend
on it: `savefile_query`, `entry_savefile_query`, `savestate_query`, `entry_savestate_query`, `firmware_query`, and an
`aggregate_query` whose `question` is `savefile_location` or `savestate_location`. Another family carrying one, or a
value that is not absolute, is refused: the first would state an input no answer reflects, the second is one the library
refuses at the call.

Every single-question family may name `installation` (a handle kind) to choose which detected installation answers;
without it the first one does. `roots_query` must name it, as `retrodeck` or `emudeck`: no other arrangement answers
that question, so it is always asked of one by name. `aggregate_query` may not — asking the aggregate to choose is the
one thing it does not do — and names its `question` instead.

## `expected` — the answers, canonically serialized

`installations` is always present: what `detect()` must find, in detection order, each `{kind, kinds, root, health}`.
Every other key is the canonical serialization of that question's answer, from `atlas/contract.py`.

Two rules decide what belongs in a vector:

- **structured fields are contractual** — every one is asserted, including caveat `code` and `data`, granularity
  identity, `fallback_dir` / `physical_dir`, file sets and their completeness, and health findings; `file_set.complete`
  is **reserved**: it is `false` in every vector because no shipped rule card can yet establish which files a core
  writes at all for the active mode, and a port that answers `true` anywhere is answering something atlas does not
  claim; every entry of `file_set.groups` carries `size_fixed` — `true`, `false`, or `null` where the size was not
  established — and the validator refuses a group without it, because a port that leaves the key out has answered a
  question about the file's size by silence;
- **prose is not** — `sources`, caveat `message` and the `*provenance` fields are for humans and change freely, so they
  are not serialized and not asserted.

Health findings ride twice by design: in `installations[].health`, and in the `caveats` of the answers computed on that
installation — the marker, root, saves root, BIOS folder and companion config findings and a bare RetroArch's
`config-unreadable` on every answer (a stated no — `no_savestates` — aside: it names no path for them to qualify),
`catalogue-invalid` on the answers that read the catalogue, while `content-tree-unwired` and the ROM root findings stay
in `installations[].health` alone. They keep their own codes in both places — nothing wraps them.

Caveats are `{code, data}` with `data` a flat object of strings. The codes are a closed vocabulary; so are root kinds,
granularities, file-set states, holes (`needs`), emulator kinds, declaration states, system sources, path kinds and
firmware needs. `scripts/validate_vectors.py` carries all of them, and the reference suite checks that list against
atlas's own exports in both directions.

## Running them

```bash
python scripts/validate_vectors.py     # shape: does every vector state a legal machine and a legal answer
python -m pytest tests/test_machine_vectors.py    # conformance: does the resolver answer exactly this
```

The validator ships beside the corpus and runs on a bare Python — no atlas import, no dependencies, and it finds the
vectors relative to its own location, so it works the same from an unpacked sdist as from the repo. The conformance run
is the reference implementation's, and is where a port substitutes its own.

A port implements the machine seam (read a file, list a directory, stat a path, read a link, load a core, size and
digest a file), feeds each `input` to it, and compares its serialized answers to `expected`. Nothing else is needed —
that is the point of the family.

## When an expected block changes

Changing what an existing input maps to is a **breaking change** to this corpus: the PR title needs the `!` marker or
its body a `BREAKING CHANGE:` footer, because release-please turns that into the major bump. Adding vectors only grows
the contract. Editing a fixture's `input` counts as breaking too — the machine the old guarantee described is gone from
the corpus — and the checker keys on the **whole** `input` object, query keys included: editing only a vector's query is
the same retirement, because the guarantee was always the pair of machine and question, not the machine alone. The gate
that decides this (`scripts/check_vector_breaking_change.py`) lives in the repository rather than in this package: it
diffs the corpus against a git ref, which a release artifact does not carry.

Vectors for old generations of an emulator's behaviour are never deleted: when a new generation lands, it gets its own
fixture machine beside the existing one.
