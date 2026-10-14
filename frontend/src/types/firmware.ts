/**
 * Firmware and BIOS types — server-side firmware inventory, local BIOS
 * file status, and the available-cores selection presented in the UI.
 */

import type { AnsweringSource, EmulatorDataReason, PlatformSystemAnswer } from "./emulatorSources";

/**
 * What the machine answers about one firmware file, in the four values the
 * resolver's per-file answer and the reading's completeness together produce.
 * `not_needed` is a finished answer — every emulator the platform offers was
 * read and none asks for the file — while `unknown` is the absence of one. They
 * are never folded together: the collapse is what made a file Tender could
 * not ask about look like a file nothing wanted.
 */
export type FirmwareWanted = "needed" | "optional" | "not_needed" | "unknown";

/**
 * What the emulator opens a firmware declaration AT — a file it reads, or a
 * folder it lists. A property of the DECLARATION, so it survives an empty
 * destination: a folder that is not there is still a folder to create, never a
 * file to fetch, which is why no download affordance may offer such a row.
 */
export type FirmwareDeclaredKind = "file" | "directory";

/**
 * How the emulator that supplied a row's `description` stated what it wants, in
 * the resolver's own vocabulary and carried verbatim.
 *
 * `"read"` is a libretro core's own `.info`, read off the machine beside it, and
 * its description is the packager's LABEL for the file — `"(PS1 JP BIOS)"` on a
 * row named `scph5500.bin`, which is a region the name does not state.
 * `"packaged"` is the card atlas keeps for an emulator
 * shipping no declaration of its own, and its description is atlas explaining
 * the REQUIREMENT in whole sentences — "a PlayStation BIOS image — the console
 * runs it before any disc, and DuckStation starts nothing without one". The two
 * are the same field and are not the same kind of prose, which is the whole
 * reason this value travels.
 *
 * The resolver states three further words (`absent`, `unreadable`,
 * `unsupported`), and none of them can reach a row here: an entry in one of
 * those states carries no requirement, so it declares no file and puts no
 * description on one. They are in the type because the value is the resolver's
 * and narrowing it to the two we see would be our own claim about its
 * vocabulary.
 */
export type FirmwareDeclarationState = "read" | "packaged" | "absent" | "unreadable" | "unsupported";

/**
 * What became of a row's BYTES, in the resolver's own stable vocabulary, carried
 * verbatim like {@link FirmwareDeclarationState}.
 *
 * Eight values and they are not one axis. Three say a comparison HAPPENED and
 * differ in what it found — `verified` (the bytes are the pinned ones),
 * `mismatch` (they are not), `unrecognised` (they were read and no table the
 * emulator keeps knows them, which DuckStation boots anyway with a warning) —
 * while `unread` says the bytes were asked for and did not come back, and
 * `unchecked` that nothing asked. `refused` is the emulator declining to open
 * the file at all on its size, before reading a byte.
 *
 * It is not a second verdict and never read as one: `satisfied` says whether the
 * requirement is met, this says what was done to establish it. What it exists
 * for is that `satisfied: null` alone cannot tell "the file was read and is
 * unknown to the emulator" from "its bytes could not be read", and the surfaces
 * worded both as the second. Absent for a payload from before the field existed
 * and `null` wherever the reading asked no content question at all, which is the
 * ordinary answer for a file that is simply not there.
 */
export type FirmwareChecked =
  "verified" | "mismatch" | "unchecked" | "unknown" | "not-comparable" | "unrecognised" | "refused" | "unread";

/**
 * This file as an option of a one-of group — a requirement whose launch needs
 * exactly ONE of the group's files, chosen by the console region of the disc.
 * `regions` are the console regions this file serves, in the resolver's own
 * words (`ntsc-u`, `pal`); `every_region` is true where it serves every region
 * the group speaks about, which a surface words as "every region" rather than
 * listing them. Being an option never makes a file required: the group is the
 * requirement, judged once (`OneOfGroupVerdict`).
 */
export interface OneOfMembership {
  regions: string[];
  every_region: boolean;
}

/**
 * The four answers about a one-of group, decided by the backend and never
 * re-derived here. `met`: what is in place serves every region the group speaks
 * about. `partial`: some regions are served and some are shown not to be.
 * `unmet`: nothing is in place. `unknown`: an option is there and could not be
 * read, or a region nobody could check stands beside the covered ones — never
 * folded into a colour of its own here.
 */
export type OneOfGroupState = "met" | "partial" | "unmet" | "unknown";

/**
 * The backend's verdict over one of the launching emulator's one-of groups.
 * Each region it speaks about is in exactly one of the three lists.
 * `game_regions` is empty for the platform's verdict, and names the game's own
 * console regions where the verdict was narrowed to one game — the three lists
 * then hold the game's regions only.
 */
export interface OneOfGroupVerdict {
  state: OneOfGroupState;
  covered: string[];
  missing: string[];
  unchecked: string[];
  /** The unchecked regions the group carries no option for, as the resolver
   *  names them (`unstated`). Absent on a payload from before the field. */
  unstated?: string[];
  game_regions: string[];
  /** Every region the group speaks about, whatever the scope. */
  regions: string[];
  /** Every option the group lists, whatever the scope — see {@link OneOfOption}. */
  options: OneOfOption[];
}

/**
 * One file a one-of group accepts: the console regions whose launch opens it,
 * and the resolver's verdict over it — in place, not, or nothing settled.
 */
export interface OneOfOption {
  file_name: string;
  regions: string[];
  satisfied: boolean | null;
}

/**
 * A setting of the emulator names a file inside its folder declaration, and the
 * file is not there — so the emulator lists the folder instead. Not a verdict:
 * the row's own verdict stands as it is. Either half is `null` where the
 * resolver did not state it.
 */
export interface MissingConfiguredImage {
  emulator_label: string | null;
  file_name: string | null;
}

/**
 * The reading's answer about one row, carried on both row shapes.
 *
 * `satisfied` is the verdict and the axis the REQUIRED counts key off: the
 * requirement is met, is not met, or nothing established which. It is not
 * `downloaded` — for a folder declaration the two come apart completely, since
 * what satisfies the core is a file *inside* the folder and RetroDECK links
 * LRPS2's `pcsx2/bios` onto the BIOS root, so the folder is always there. The
 * library's held/offered ratio is a third axis and keys off neither; only the
 * ratio a surface states is narrowed to the rows whose `satisfied` is not null.
 *
 * `caveats` are the resolver's own stable codes for what it found, and `images`
 * names what a satisfied folder holds, in the resolver's own words. A surface
 * takes the CAUSE of a verdict from those, because `satisfied` is deliberately
 * the verdict alone and carries none of it.
 *
 * `declaration` is not a verdict but rides with them because it is the same
 * kind of fact — the resolver's own word, carried verbatim, for a row nobody
 * here re-derives. See {@link FirmwareDeclarationState}.
 */
interface FirmwareVerdict {
  satisfied?: boolean | null;
  declared_kind?: FirmwareDeclaredKind;
  /** Which register this row's `description` is written in — see
   *  {@link FirmwareDeclarationState}. Absent for a row nothing declared, and
   *  for a payload from before the field existed. */
  declaration?: FirmwareDeclarationState;
  /** What became of this row's bytes — see {@link FirmwareChecked}. */
  checked?: FirmwareChecked | null;
  caveats?: string[];
  images?: string[];
  /** The launching emulator's group this row is an option of — see
   *  {@link OneOfMembership}. Absent or `null` claims nothing. */
  one_of?: OneOfMembership | null;
  /** Does "Download required" fetch this row? The backend's one rule for both
   *  the button's count and the download itself: a file the launching emulator
   *  requires, or an option of a region its group does not cover yet. Absent on
   *  a payload from before the field existed, which reads as
   *  `required_by_active`. */
  fetch_for_required?: boolean;
  /** See {@link MissingConfiguredImage}. Absent or `null` claims nothing. */
  missing_configured_image?: MissingConfiguredImage | null;
  /** The identity of the emulator whose requirement this row is, `null` for a
   *  library file no emulator claims. A row is shown under its own emulator's
   *  tab ({@link EmulatorTab}), and an unclaimed one under the launching one's. */
  declared_by?: string | null;
}

interface FirmwareFile extends FirmwareVerdict {
  /** `null` for a file an installed emulator asks for that the RomM library does
   *  not hold — there is no server record to name. Nothing reads it: what the
   *  page filters the download buttons and its progress totals on is
   *  `on_server`. */
  id: number | null;
  file_name: string;
  size: number;
  md5: string;
  local_path: string;
  /** Where the emulator said the file goes, relative to the firmware root —
   *  `dc/dc_boot.bin` where a subdirectory was declared, the bare name
   *  otherwise. `file_name` is its basename and `local_path` is it joined under
   *  a root this side does not know, so this is the only field that can answer
   *  which folder a file placed by hand belongs in. Optional because a payload
   *  from before the field existed carries none. */
  declared_path?: string;
  downloaded: boolean;
  description: string;
  wanted: FirmwareWanted;
  /** Whether the platform's launching core is the one that requires it — the
   *  axis the "BIOS needed" badge and the required counts key off, distinct
   *  from `wanted`, which is about every installed emulator. */
  required_by_active: boolean;
  /** Whether the platform's launching emulator declares the file at all,
   *  required or optional. Also true of a row no emulator declares, and of every
   *  row when the launching emulator could not be identified, so it is read
   *  beside `wanted`, never alone. */
  used_by_active?: boolean;
  on_server: boolean;
  supplied_by?: string | null;
  /** How many of Tender's own downloads a delete on THIS row would remove:
   *  1 for a declared file a download record names and still holds, and for a
   *  declared FOLDER the number of recorded files written underneath it — the
   *  emulator lists the folder, but whatever we downloaded into it is ours.
   *  Having placed a file is the whole authority to remove it.
   *
   *  **Never substitute `downloaded` for this.** That is `os.path.exists` at the
   *  row's recomputed destination, equally true of firmware the emulator shipped
   *  with (`dolphin-emu/Sys/codehandler.bin`) and of a file the user placed by
   *  hand — neither of which any RomM library can hand back. Absent means the
   *  payload never spoke about it, which offers no delete rather than guessing. */
  deletable_count?: number;
}

/**
 * One platform as the `get_firmware_status` overview names it — and the whole of
 * what that call says about it.
 *
 * The overview answers WHICH platforms the page can speak for; what each one's
 * BIOS state IS costs a live per-system reading (59-175 ms on the reference
 * machine at emu-atlas 0.22), asked for one platform at a time through
 * `get_platform_firmware_status`. So there is deliberately no level, no count
 * and no file list here: every field a surface could colour a dot or word a
 * sentence from belongs to the answer that did the reading, and one that leaked
 * onto this payload would be rendered over a platform nobody has asked about
 * yet.
 */
export interface FirmwarePlatformNamed {
  platform_slug: string;
  has_games?: boolean;
}

/**
 * One ES-DE `<command>` classified for launch-bakeability (#1210), the shape the
 * backend `get_emulator_options` / picker payloads carry. `kind` is `"libretro"`
 * (`core_so` set to the bare core name) or `"standalone"` (`core_so` null).
 * `bakeable` is false for the `needs_setup` (`reason: "inject"`) and un-bakeable
 * forms; `reason` names why the frontend can't offer it as a clickable pick.
 *
 * `emulator` is the resolver's identity for the emulator behind the row and the
 * only field that names one for BOTH kinds — `dolphin_libretro.so` for a
 * libretro entry, `DUCKSTATION` for a standalone one. It is what a BIOS answer's
 * tabs and rows (`EmulatorTab.emulator`, `declared_by`) are keyed on, so it is the
 * join between a picker row and a BIOS answer; `core_so` carries no extension
 * and is null for every standalone emulator, and `label` identifies a ROW rather
 * than an emulator — ES-DE lists one `pcsx2_libretro.so` as both `LRPS2` and
 * `PCSX2`. `null` is an emulator the resolver could not identify, which nothing
 * may be keyed on.
 */
export interface EmulatorOption {
  label: string;
  kind: "libretro" | "standalone";
  core_so: string | null;
  emulator: string | null;
  is_default: boolean;
  bakeable: boolean;
  reason: string | null;
}

/**
 * Response shape of the `get_platform_core_info` endpoint — the dedicated
 * single-platform emulator-info path, decoupled from the per-game BIOS payload
 * (#923). The per-game detail page (`RomMPlaySection` / `RomMGameInfoPanel`)
 * reads emulator data from here; the platform-keyed twin for a caller with no
 * ROM to layer is {@link SystemCoreInfo}. `emulator_data_available` is false
 * where the answering emulator source gives no emulator list, and
 * `emulator_data_reason` says why, so the picker can say so instead of showing
 * an empty list.
 */
export interface CoreInfo {
  emulators: EmulatorOption[];
  emulator_data_available: boolean;
  /** Why `emulators` could not be given; `null` while it is available. */
  emulator_data_reason: EmulatorDataReason | null;
  /** The source the answer came from; `null` where no source answers. */
  emulator_source: AnsweringSource | null;
  /** The system the emulator list is about — an installed game's own, otherwise
   *  the platform's in the answering source; `null` where no source was asked. */
  platform_system: PlatformSystemAnswer | null;
  /** The IDENTITY of the emulator this ROM launches with — the same spelling the
   *  picker rows carry in {@link EmulatorOption.emulator} and the same one a
   *  BIOS answer's tabs and rows (`EmulatorTab.emulator`, `declared_by`) are
   *  keyed on. Never the bare `core_so`: that names no
   *  standalone emulator and is a second spelling of a libretro one. */
  active_core: string | null;
  active_core_label: string | null;
  platform_core_label: string | null;
  has_game_override: boolean;
}

/**
 * Response shape of the `get_system_core_info` endpoint — the platform-keyed
 * core read the Library page's Platforms detail issues once per selected
 * platform (#1815). Carries what the core picker needs and nothing else: no
 * per-game layer (there is no ROM), and no BIOS data (the detail's table reads
 * that off `get_firmware_status`).
 *
 * `active_core_label` is the platform-layer resolution — the per-platform
 * override when it still resolves to a bakeable emulator, else the es_systems
 * default — so in both ordinary cases it IS the emulator's name. `null` means
 * no option is BAKEABLE, which is not the same as there being none: a menu of
 * uninstalled standalone emulators answers `null` with `emulators.length >= 1`.
 * RetroDECK then resolves the emulator itself at launch, and the Library page
 * says that rather than printing a name or claiming a failure.
 *
 * There is no `success`, for the reason its sibling has none: the endpoint has
 * no in-band failure to report. What can go wrong — no emulator list from the
 * answering source — is already `emulator_data_available: false` with its
 * `emulator_data_reason`, and anything else raises, which reaches the caller as
 * a rejected promise.
 */
export interface SystemCoreInfo {
  emulators: EmulatorOption[];
  emulator_data_available: boolean;
  /** As on {@link CoreInfo}. */
  emulator_data_reason: EmulatorDataReason | null;
  emulator_source: AnsweringSource | null;
  /** The platform's system in the answering source, which the page names. */
  platform_system: PlatformSystemAnswer | null;
  active_core_label: string | null;
}

/**
 * One platform's whole BIOS state — the answer `get_platform_firmware_status`
 * gives for the platform it was asked about, and what the Platforms detail
 * renders.
 *
 * The library-wide path, distinct from the per-game `check_platform_bios`
 * payload, which no longer carries any core fields (#923). Its core fields are
 * a second answer to {@link SystemCoreInfo}'s question, kept because they cost
 * nothing extra once this platform's catalogue has been read; a platform the
 * backend has nothing to say about answers with no entry at all, which is why
 * the Platforms detail asks the core read directly rather than joining onto
 * this one.
 *
 * `files` is the union of what the RomM library offers for the platform and what
 * the platform's emulators ask for, so a row can be present with `on_server`
 * false — wanted, possibly missing, and not downloadable from here.
 */
export interface FirmwarePlatformExt extends FirmwarePlatformNamed, EmulatorAnswer {
  files: FirmwareFile[];
  all_downloaded?: boolean;
  /** The platform pick's IDENTITY — the same space as {@link CoreInfo.active_core}
   *  and as a row's `declared_by`. Its label is the field below, and both are
   *  projections of ONE backend resolution. */
  active_core?: string;
  active_core_label?: string;
  emulators?: EmulatorOption[];
  emulator_data_available?: boolean;
  emulator_data_reason: EmulatorDataReason | null;
  emulator_source: AnsweringSource | null;
  platform_system: PlatformSystemAnswer | null;
  // The backend's level — the resolver's verdict for the launching emulator
  // (`compute_bios_level`) — and the counts that word it, so the platform
  // detail reads the colour off the payload and never computes one. The
  // optional-missing breakdown stays a local file-level computation (a richer
  // axis the level doesn't model).
  bios_level?: BiosLevel | null;
  required_count?: number;
  required_downloaded?: number;
  /** How many of `required_count` nothing could judge — see `BiosStatus`. Above
   *  zero it is why `bios_level` is `"unknown"`, and it is what separates that
   *  from a platform nothing could speak for: here the rows have answers and
   *  only the one-line verdict declines, so the downloads stay. */
  required_withheld?: number;
  /** The verdicts over the launching emulator's one-of groups — see
   *  {@link OneOfGroupVerdict}. Each is ONE requirement inside `required_count`. */
  one_of_groups?: OneOfGroupVerdict[];
  server_count?: number;
  local_count?: number;
  /** The pair the "n/N RomM library files" ratio states — `server_count` /
   *  `local_count` over the rows whose verdict was established. */
  ratio_server_count?: number;
  ratio_local_count?: number;
  known_count?: number;
  unknown_count?: number;
  /** How many files Delete BIOS would remove: download records Tender
   *  wrote whose file is still on disk. Deliberately not `local_count` — that is
   *  the library's progress ratio, which counts files nothing here put on disk
   *  and drops our own downloads once RomM stops listing them. */
  deletable_count?: number;
}

export interface FirmwareStatus {
  success: boolean;
  message?: string;
  server_offline?: boolean;
  platforms: FirmwarePlatformNamed[];
}

/**
 * One platform's answer from `get_platform_firmware_status`.
 *
 * `platform` is `null` where the backend has nothing to say — a platform the
 * RomM library holds no firmware for, whose reading finished and found its
 * emulators want none either. That is a FINISHED answer and not a failure: a
 * read that never came back is a rejected promise, and the page words the two
 * differently.
 */
export interface PlatformFirmwareStatus {
  success: boolean;
  message?: string;
  platform: FirmwarePlatformExt | null;
}

export interface BiosFileStatus extends FirmwareVerdict {
  file_name: string;
  downloaded: boolean;
  local_path: string;
  /** Where the emulator said the file goes, relative to the firmware root —
   *  `dc/dc_boot.bin` where a subdirectory was declared, the bare name
   *  otherwise. `file_name` is its basename and `local_path` is it joined under
   *  a root this side does not know, so this is the only field that can answer
   *  which folder a file placed by hand belongs in. Optional because a payload
   *  from before the field existed carries none. */
  declared_path?: string;
  description: string;
  wanted: FirmwareWanted;
  /** Whether the emulator THIS game launches with requires the file. `wanted` is
   *  the need of the emulator whose requirement the row is; this one is the
   *  launch's. */
  required_by_active: boolean;
  used_by_active?: boolean;
  /** False for a file an emulator asks for that the RomM library does not hold.
   *  It still counts as missing — Tender just cannot fetch it. */
  on_server?: boolean;
  /** The distribution whose own copy is sitting at the destination, as the
   *  resolver writes that distribution's name — printed verbatim, never mapped
   *  to a display form of ours. Absent claims nothing: the resolver states it
   *  only where it established the provenance. */
  supplied_by?: string | null;
}

/**
 * The backend's readiness decision for a platform's BIOS: the resolver's verdict
 * for the launching emulator. `"ok"` met, `"missing"` not met,
 * `"unknown"` not established — including an emulator the answer says nothing
 * about, which counts as not read. `"partial"` is the one exception: the
 * verdict is open only because the disc's region decides it, and a
 * one-of group's images serve some regions. No colour is ever computed here from
 * a count.
 */
export type BiosLevel = "ok" | "partial" | "missing" | "unknown";

/** How an emulator finds its firmware, in the resolver's words: it opens the
 *  names it declares, it searches the BIOS folder by content after a configured
 *  name, or nobody has established which. It words an answer and never moves a
 *  colour. */
export type FirmwareLocating = "by-name" | "by-name-then-content" | "unestablished";

/** A declaration the resolver would not follow to a place, with its stable
 *  reason code. Only a required one keeps the emulator's verdict open. */
export interface RefusedDeclaration {
  declared: string;
  required: boolean;
  reason: string;
}

/** A statement the resolver makes about the emulator rather than a file:
 *  `bios-unchosen` — a setting that names its BIOS is empty, so no game boots
 *  until one is chosen (`setting` names it); `images-tied` — `tied` images rank
 *  alike, for `regions` where stated, and which one boots is not established. */
export interface EmulatorNote {
  kind: "bios-unchosen" | "images-tied" | "search-found-nothing";
  setting: string | null;
  tied: number | null;
  /** The folder a search looked in, for `search-found-nothing`. */
  dir?: string | null;
  /** How many files of a size the emulator accepts that folder held, for
   *  `search-found-nothing` — `null` where the search states no count
   *  (DuckStation's). */
  candidates?: number | null;
  regions: string[];
}

/**
 * What the resolver says about the launching emulator as a whole, carried on
 * both BIOS payloads for a surface to word — never to weigh.
 *
 * `emulator_read` is whether the emulator stated what it wants; the verdict,
 * which can stand where it did not, is `bios_level`. `console_needs_image` is the resolver's world knowledge that
 * the console does not start without an image this emulator does not carry —
 * whether one is in place is the rows' own verdicts. `refused` is what keeps a
 * verdict open that a sentence names, and `emulator_notes` the statements a
 * sentence carries. Every field is optional
 * because a payload from before it existed carries none.
 */
export interface EmulatorAnswer {
  emulator_read?: boolean;
  locating?: FirmwareLocating;
  emulator_declaration?: FirmwareDeclarationState | null;
  console_needs_image?: boolean;
  refused?: RefusedDeclaration[];
  emulator_notes?: EmulatorNote[];
  /** What the emulator's search of the BIOS folder established, for one that
   *  searches by content and states no one-of group — `null` for every other.
   *  Words the answer and moves no colour. */
  content_search?: ContentSearch | null;
  /** One tab per emulator the resolver answered for, the launching one first —
   *  see {@link EmulatorTab}. Absent on a payload from before the field. */
  emulator_tabs?: EmulatorTab[];
}

/**
 * One emulator's tab over the BIOS table (#2190): its own verdict and label, and
 * — for every emulator but the launching one — its own requirements exactly as
 * the resolver answers for it. The launching tab carries no `rows`: its rows
 * are the payload's `files`, which the counts and the downloads read. A tab
 * changes what a surface shows and never which emulator launches.
 */
export interface EmulatorTab {
  emulator: string | null;
  label: string | null;
  launching: boolean;
  requirements_met: boolean | null;
  rows?: BiosFileStatus[];
}

/** What a by-content search of the BIOS folder established: every required
 *  file met, a listing that did not finish, or a file it reached that could not
 *  be read. A search that found nothing is the emulator's note instead
 *  (`search-found-nothing`). */
export type ContentSearch = "found" | "incomplete" | "unreadable";

export interface BiosStatus extends EmulatorAnswer {
  needs_bios: boolean;
  server_count?: number;
  local_count?: number;
  /** The pair the "n/N RomM library files" ratio states — see `FirmwarePlatformExt`. */
  ratio_server_count?: number;
  ratio_local_count?: number;
  all_downloaded?: boolean;
  required_count?: number;
  required_downloaded?: number;
  /** How many of `required_count` nothing could judge — rows whose `satisfied`
   *  is null, such as a file the resolver could not compare. Such a row raises
   *  neither `required_downloaded` nor the count of files known to be absent —
   *  subtract it from `required_count` for that. A row answered `false` is NOT
   *  here: that is a requirement shown to be unmet. */
  required_withheld?: number;
  /** The verdicts over the launching emulator's one-of groups — see
   *  {@link OneOfGroupVerdict}. On the game page each is narrowed to the game's
   *  own regions wherever RomM's region names for it map onto a console region. */
  one_of_groups?: OneOfGroupVerdict[];
  /** The NAME of the emulator every answer here was scoped to — the label half
   *  of the one pick the backend filtered these counts by, taken off that same
   *  resolution rather than resolved again, so a sentence over them cannot name
   *  one emulator while the counts under it are about another. Not the picker's
   *  data: which emulators a platform offers, and the identity this one is keyed
   *  on, reach the frontend through `get_platform_core_info` and not from here.
   *  `null` or absent where no pick could be made or it carries no label, and a
   *  surface then words its sentence without a name rather than inventing one. */
  active_core_label?: string | null;
  // Server files an installed emulator asks for, and files nothing could answer
  // about. A `not_needed` file is in neither — it is answered for, and wanted by
  // nothing — which is what keeps "nothing here is needed" apart from "nothing
  // could be established".
  known_count?: number;
  unknown_count?: number;
  files?: BiosFileStatus[];
  // unknown/ok/partial/missing state computed by the backend (compute_bios_level)
  // so the frontend reads the decision off the payload instead of re-deriving the
  // threshold logic. Present only when needs_bios is true.
  bios_level?: BiosLevel | null;
  // The play row's compact token (compute_bios_label): "Missing", "Unknown",
  // "OK", or the plain required files' "X/Y required", never counting a one-of
  // group. It is not a restatement of the level — a partial group beside plain
  // files all in place reads "OK" — so read the level for the verdict. Present
  // only when needs_bios is true.
  bios_label?: string;
  // Set when the check could not determine the requirement — it found no file
  // to speak for AND its reading of the platform's emulators was not complete,
  // which one unread core is already enough for, so it cannot say that nothing
  // is wanted. A failed firmware fetch alone does not raise it: the listing is
  // caught and the file list simply comes back empty, which the machine's own
  // answer can still fill. The `needs_bios: false` it rides on is
  // ignorance, not an answer, so no consumer may clear a shown requirement on it
  // (#1693) — and the panel renders it as its own state rather than hiding the
  // BIOS tab, which would say the same thing (#1660).
  bios_status_unknown?: boolean;
}

/** A firmware download's success answer — a batch whose files partly or wholly failed answers it too; its failure is
 *  the endpoints' shared one. `failed` comes only from the two batches, naming the files they could not fetch, and
 *  once it is non-empty `message` is the sentence that says so. `file_path` and `md5_match` come only from the
 *  per-row download, and only when it fetched. */
export interface FirmwareDownloadResult {
  success: true;
  message: string;
  downloaded: number;
  failed?: string[];
  file_path?: string;
  md5_match?: boolean | null;
}
