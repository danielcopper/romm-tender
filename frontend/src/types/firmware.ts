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
 * What became of a row's BYTES, in the resolver's own stable vocabulary but for
 * `rejected` (below), carried verbatim like {@link FirmwareDeclarationState}.
 *
 * Nine values and they are not one axis. Three say a comparison HAPPENED and
 * differ in what it found — `verified` (the bytes are the pinned ones),
 * `mismatch` (they are not), `unrecognised` (they were read and no table the
 * emulator keeps knows them, which DuckStation boots anyway with a warning) —
 * while `unread` says the bytes were asked for and did not come back, and
 * `unchecked` that nothing asked. `refused` is the emulator declining to open
 * the file at all on its size, before reading a byte. `rejected` is Tender's own
 * word, not the resolver's: a file in a declared folder the emulator's own test
 * read and turned down, which the resolver states only as that file's verdict.
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
  | "verified"
  | "mismatch"
  | "rejected"
  | "unchecked"
  | "unknown"
  | "not-comparable"
  | "unrecognised"
  | "refused"
  | "unread";

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
   *  library file no emulator claims. A row is shown with its own emulator
   *  ({@link EmulatorTab}), and an unclaimed one in the library's own section. */
  declared_by?: string | null;
  /** The RomM library file that fills the row — see {@link LibraryMatch}.
   *  `null` where none does; absent on a payload from before the field. */
  library?: LibraryMatch | null;
  /** The name of this platform's store copy that fills the row, `null` where
   *  Tender's firmware store holds none for this platform. */
  store_file?: string | null;
  /** What lies at the row's place — see {@link PlaceLink}. `null` for a library
   *  row, which has no place. */
  link?: PlaceLink | null;
  /** The platform whose store copy a link of Tender's at the place points at,
   *  where that is another platform's (`link: "linked_elsewhere"`). */
  linked_for?: string | null;
  /** The name the platform `linked_for` slugs is shown under, as the library sync last recorded it. */
  linked_for_name?: string | null;
  /** Why no link may go to the row's place, `null` where one may — see
   *  {@link PlaceRefusal}. */
  place_refusal?: PlaceRefusal | null;
  /** Whether the row offers "Replace…": another file lies at the place, the
   *  resolver reads it as wrong, and the library copy is proven right by its
   *  checksum. Decided by the backend alone. */
  replace_offered?: boolean;
  /** The folder declaration this row is one file in — LRPS2's `pcsx2/bios` —
   *  `null` for every other row. */
  in_folder?: string | null;
  /** The name a Delete on this row is addressed by: the store file, an older
   *  download of the library file that fills the row, or an older download
   *  lying at the row's own place. `null` offers no Delete. */
  delete_name?: string | null;
}

/**
 * The RomM library file that fills a row, and how. `matched` is `content` for a
 * file whose MD5 is the one the resolver pins for the requirement, wherever RomM
 * files it; `archive_name` for an archive matched by its declared name, whose
 * contents cannot be compared; `no_checksum_name` for a requirement the resolver
 * knows no checksum for (Cemu's `keys.txt`); `null` on a library row, which
 * fills nothing. `no_checksum` is true where RomM states no checksum for the
 * file, so a download of it could not be checked and none is offered.
 */
export interface LibraryMatch {
  firmware_id: number | null;
  file_name: string;
  platform_slug: string;
  matched: "content" | "archive_name" | "no_checksum_name" | null;
  md5: string;
  size: number;
  no_checksum: boolean;
}

/**
 * What lies at a row's place. `linked`: Tender's link to this platform's store
 * copy. `linked_elsewhere`: Tender's link to another platform's copy
 * (`linked_for`). `not_linked`: nothing, or a link of Tender's pointing at
 * nothing, while this platform's store holds a copy — the link update places it.
 * `occupied`: a file, folder or link that is not Tender's, which is never
 * changed but by a confirmed "Replace…". `free`: nothing, and no store copy.
 */
export type PlaceLink = "linked" | "linked_elsewhere" | "not_linked" | "occupied" | "free";

/**
 * Why a link may not go to a row's place: the folder it lies in does not exist
 * (`folder_missing`), it lies under a root the resolver only assumed, or the
 * source's roots could not be asked at all (`root_assumed`), the resolver could
 * not look at it (`unreadable`), the answer names no place (`no_place`), or the
 * source states the row's emulator is not installed (`not_installed`).
 * `folder` is the folder a sentence names, `source` the emulator source the
 * row is read in.
 */
export interface PlaceRefusal {
  reason: "folder_missing" | "root_assumed" | "unreadable" | "no_place" | "not_installed";
  folder: string | null;
  source: string;
}

/**
 * Why a download on this page lands in Tender's firmware store and is linked
 * nowhere: the launching emulator is one the resolver does not cover, or no
 * emulator source answers for the platform. `null` where links follow.
 */
export type StoreOnly = "unsupported" | "no_source";

/** What a Delete left in place: a link of Tender's into the store under a
 *  root that could not be read, or a file an older version downloaded that
 *  Tender may not remove now (`why`). `folder` is the root a link lies under,
 *  where one is known, and the folder an older download lies in; `source` is
 *  the emulator source the reason is about. */
export interface BiosKeptLink {
  kind: "link" | "file";
  place: string;
  folder: string | null;
  source: string;
  /** The older download's name in RomM; a link names none. */
  file_name?: string;
  /** Why an older download stayed: RetroDECK's settings could not be read,
   *  RetroDECK is not installed, or it names no BIOS folder — in these it
   *  stays counted — or it lies outside RetroDECK's BIOS folder, and Tender
   *  dropped its record. */
  why?: "settings_unreadable" | "not_installed" | "no_bios_folder" | "outside_bios_folder";
}

/** A link a placement could not create, and the system's reason. */
export interface BiosLinkFailure {
  place: string;
  file_name: string | null;
  reason: string;
}

/** What bringing a platform's links up to date did, over every enabled source. */
export interface BiosLinkResult {
  success: true;
  placed: number;
  left_in_place: number;
  /** Links of Tender's that pointed at nothing and were removed. */
  removed: number;
  link_failures: BiosLinkFailure[];
}

/** A replacement's answer: where the old file went, and the links placed. */
export interface BiosReplaceResult extends BiosLinkResult {
  backup_path: string;
}

/** What the resolver knows a library file's content as: every name, `[]` for
 *  content it does not know, `null` where nothing could be asked. */
export interface FirmwareIdentification {
  success: true;
  known_as: string[] | null;
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
  /** Whether the platform's launching emulator requires it — the axis the
   *  "BIOS needed" badge and the required counts key off. Another emulator's
   *  entry carries its rows as {@link BiosFileStatus}, where the same field is
   *  that emulator's. `wanted` states the same need in the four-valued
   *  vocabulary. */
  required_by_active: boolean;
  /** Whether the platform's launching emulator declares the file at all,
   *  required or optional. Also true of a row no emulator declares, and of every
   *  row when the launching emulator could not be identified, so it is read
   *  beside `wanted`, never alone. */
  used_by_active?: boolean;
  on_server: boolean;
  supplied_by?: string | null;
  /** Whether a Delete on THIS row removes a file of Tender's: 1 where the row
   *  names one (`delete_name`), 0 otherwise. Having placed a file is the whole
   *  authority to remove it.
   *
   *  **Never substitute `downloaded` for this.** That is whether a file is at
   *  the row's place, equally true of firmware the emulator shipped with
   *  (`dolphin-emu/Sys/codehandler.bin`) and of a file the user placed by
   *  hand — neither of which any RomM library can hand back. Absent means the
   *  payload never spoke about it, which offers no delete rather than guessing. */
  deletable_count?: number;
}

/**
 * One ES-DE `<command>` classified for launch-bakeability (#1210), the shape the
 * backend `get_emulator_options` / picker payloads carry. `kind` is Tender's word
 * for what the resolver states the entry launches: `"core"` (a RetroArch core of
 * this machine, `core_so` set to its bare name), `"program"` (an emulator program
 * of its own) or `"foreign_core"` (a RetroArch launch naming a core this machine
 * cannot load); `core_so` is null on the last two.
 * `bakeable` is false for the `needs_setup` (`reason: "inject"`, or
 * `"not_installed"` for an emulator the resolver states is not installed —
 * worded by `reasonCopy` in `utils/emulatorMenu.tsx`) and un-bakeable forms;
 * `reason` names why the frontend can't offer it as a clickable pick. A
 * bakeable entry's `reason` is a note instead: where the resolver could not
 * establish whether the emulator is installed, the cause, worded by
 * `utils/emulatorInstallation.ts`, and `null` otherwise.
 *
 * `emulator` is the resolver's identity for the emulator behind the row and the
 * only field that names one for every kind — `dolphin_libretro.so` for a core
 * entry, `DUCKSTATION` for a program. It is what a BIOS answer's
 * entries and rows (`EmulatorTab.emulator`, `declared_by`) are keyed on, so it is the
 * join between a picker row and a BIOS answer; `core_so` carries no extension
 * and is null for every program, and `label` identifies an entry
 * rather than an emulator — RetroDECK lists RPCS3 as one entry by game serial
 * and another from a folder or an ISO. `null` is an emulator the resolver could
 * not identify, which nothing may be keyed on.
 *
 * One entry stands for every label that runs the same launch command —
 * RetroDECK's `LRPS2` and `PCSX2` are one entry — while one emulator launched
 * different ways is one entry per way. `labels` are the entry's labels in
 * RetroDECK's order and `name` is them joined " / ", which is what a launch
 * place prints — the BIOS part names the emulator instead
 * (docs/architecture/core-emulator-selection.md, "The BIOS part names the
 * emulator; the launch places name the entry"); `label` is the first of them,
 * the one a pick stores. An override
 * stored under any of `labels` reads as this entry (`utils/emulatorEntries.ts`).
 */
export interface EmulatorOption {
  label: string;
  labels: string[];
  name: string;
  kind: "core" | "program" | "foreign_core";
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
   *  BIOS answer's entries and rows (`EmulatorTab.emulator`, `declared_by`) are
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
 * nothing is pinned and no option can be the default, which is not the same as
 * there being none: a menu of emulators that are not installed, standalone
 * emulators or cores alike, answers `null` with `emulators.length >= 1`, and so
 * does one whose only bakeable entries could not be established.
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
 * nothing extra once this platform's catalogue has been read.
 *
 * `files` is the union of what the RomM library offers for the platform and what
 * the platform's emulators ask for, so a row can be present with `on_server`
 * false — wanted, possibly missing, and not downloadable from here.
 */
export interface FirmwarePlatformExt extends EmulatorAnswer {
  platform_slug: string;
  has_games?: boolean;
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
  /** How many files the platform-wide delete would act on: every file of this
   *  platform's store Tender has a record of and that is on disk, whichever
   *  row it fills or none, and the older downloads still on disk. Deliberately
   *  not `local_count` — that is the library's progress ratio, which counts
   *  files nothing here put on disk. */
  deletable_count?: number;
  /** See {@link StoreOnly}; absent or `null` where a download is linked. */
  store_only?: StoreOnly | null;
  /** The folder a replaced file's backup goes under, each in a folder of its
   *  own date and time — what "Replace…" names before it runs. */
  backup_root?: string;
}

/**
 * The `get_firmware_status` overview: whether RomM's firmware listing could be
 * read, which the downloads stand on. It carries nothing about any platform —
 * which platforms get a BIOS answer is the page's list, and each answer is a
 * live per-system reading asked for through `get_platform_firmware_status`.
 */
export interface FirmwareStatus {
  success: boolean;
  message?: string;
  server_offline?: boolean;
}

/**
 * One platform's answer from `get_platform_firmware_status`.
 *
 * Every platform answers with its entry: one whose reading did not happen says
 * so through its verdict (`bios_level: "unknown"`, `emulator_read: false`), and
 * one whose launching emulator needs nothing through its verdict too. A read
 * that never came back is a rejected promise, and the page words it apart.
 *
 * The failing arm is not sent today; it is typed so that the page's handling
 * of one cannot read an entry off it.
 */
export type PlatformFirmwareStatus =
  { success: true; platform: FirmwarePlatformExt } | { success: false; message?: string };

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
  /** Whether the emulator whose list the row is on requires the file: the one
   *  THIS game launches with for the payload's own `files`, and the entry's
   *  emulator on an `emulator_tabs` row. `wanted` states that same emulator's
   *  need in the four-valued vocabulary. */
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
  /** How many of Tender's own downloads a delete on this row would remove — see
   *  the platform row's field of the same name. Carried on another emulator's
   *  rows on the platform page; absent offers no delete. */
  deletable_count?: number;
}

/**
 * The backend's readiness decision for a platform's BIOS: the resolver's verdict
 * for the launching emulator. `"ok"` met, `"missing"` not met,
 * `"unknown"` not established — including an emulator the answer says nothing
 * about, which counts as not read. `"partial"` is the one exception: the
 * verdict is open only because the disc's region decides it, and a
 * one-of group's images serve some regions. On a game's page the image serving
 * the game's own region decides, and the level is `"unknown"` where
 * no image serves that region, even under a met verdict. No colour is ever
 * computed here from a count.
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
  /** One entry per emulator the resolver answered for, the launching one first —
   *  see {@link EmulatorTab}. Absent on a payload from before the field. */
  emulator_tabs?: EmulatorTab[];
}

/**
 * One emulator the BIOS tables' selector offers: its own verdict and
 * label, and — for every emulator but the launching one — its own requirements
 * exactly as the resolver answers for it. The launching entry carries no
 * `rows`: its rows are the payload's `files`, which the header's counts read.
 * Choosing an entry changes what a surface shows and never which emulator
 * launches.
 */
export interface EmulatorTab {
  emulator: string | null;
  label: string | null;
  launching: boolean;
  requirements_met: boolean | null;
  /** `false` where the emulator is not installed; it is listed all the same,
   *  since what it would need is answered either way. Absent reads as installed. */
  installed?: boolean;
  rows?: BiosFileStatus[];
}

/** What a by-content search of the BIOS folder established: every required
 *  file met, a listing that did not finish, or a file it reached that could not
 *  be read. A search that found nothing is the emulator's note instead
 *  (`search-found-nothing`). */
export type ContentSearch = "found" | "incomplete" | "unreadable";

export interface BiosStatus extends EmulatorAnswer {
  /** `true` wherever there is a row to show or the verdict is not met; `false`
   *  on the answer that the launching emulator needs no BIOS and on the unknown
   *  stub below. */
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
  // threshold logic. Absent on the unknown stub alone.
  bios_level?: BiosLevel | null;
  // The play row's compact token (compute_bios_label): "Missing", "Unknown",
  // "OK", or the plain required files' "X/Y required", never counting a one-of
  // group. It is not a restatement of the level — a partial group beside plain
  // files all in place reads "OK" — so read the level for the verdict. Absent on
  // the unknown stub alone.
  bios_label?: string;
  // Set when the check could not determine the requirement — it found no file
  // to speak for AND its reading of the platform's emulators was not complete,
  // which one unread core is already enough for, so it cannot say that nothing
  // is wanted. A failed firmware fetch alone does not raise it: the listing is
  // caught and the file list simply comes back empty, which the machine's own
  // answer can still fill. The `needs_bios: false` it rides on never means
  // that nothing is needed. Whether a payload carrying the flag is an answer is
  // decided by its level, as `BiosAnswer` in `api/backend.ts` states.
  bios_status_unknown?: boolean;
  /** See {@link StoreOnly}. */
  store_only?: StoreOnly | null;
  /** As on {@link FirmwarePlatformExt}. */
  backup_root?: string;
  /** As on {@link FirmwarePlatformExt}. */
  deletable_count?: number;
  /** Whether RomM's firmware listing could not be read — as `FirmwareStatus`
   *  says it for the platform page: the downloads are withdrawn. */
  server_offline?: boolean;
}

/** A firmware download's success answer — a batch whose files partly or wholly failed answers it too; its failure is
 *  the endpoints' shared one. `failed` comes only from the two batches, naming the files they could not fetch, and
 *  once it is non-empty `message` is the sentence that says so. Every download then brings the platform's links up
 *  to date, and answers what that did beside it; `backups` are where files lying in the store without a record of
 *  Tender's went before the download. */
export interface FirmwareDownloadResult extends Partial<Omit<BiosLinkResult, "success">> {
  success: true;
  message: string;
  downloaded: number;
  failed?: string[];
  backups?: string[];
}
