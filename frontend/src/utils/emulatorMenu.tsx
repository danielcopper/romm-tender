/**
 * Shared builder for the emulator-picker context menu (#1210).
 *
 * Both pickers — the game-detail core menu (`RomMPlaySection`) and the Library
 * page's per-platform menu (`PlatformDetail`) — render the same list: every ES-DE
 * `<command>`, each one choosable but an entry the resolver states not installed
 * (#2195 D9, D23, D31). An entry Tender cannot bake says so, so a pick of it does
 * not surprise: Play is then off (D7). The frontend owns the copy; the backend
 * only ships the reason slug. Keys are the emulator LABEL (not `core_so`), since
 * a standalone emulator has no core.
 *
 * Every entry is a row of its own, in the order the resolver lists them; two
 * labels that run one command are two rows, as in ES-DE, which stores a choice
 * by its label. The entry the user chose inside ES-DE, where it is not the
 * default, is marked so.
 */

import { useSyncExternalStore, type FC, type ReactNode } from "react";
import { Menu, MenuItem, MenuSeparator } from "@decky/ui";
import type { AnsweringSource, EmulatorDataReason, EmulatorOption, PlatformSystemAnswer } from "../types";
import { emulatorDataReasonSentence } from "./emulatorSourceWording";
import { entryForLabel } from "./emulatorEntries";
import { notEstablishedNote } from "./emulatorInstallation";
import { isChoosable } from "./emulatorStart";
import type { EntryLaunches } from "./emulatorLaunches";
import { entryLaunchLine } from "./gameLaunch";

/** The line an entry Tender cannot bake carries, so a pick of it does not surprise (#2195 D7). */
const CANNOT_START_YET = "Tender cannot start it yet";

/** Map a backend un-bakeable reason slug to short menu copy: the resolver's statement, else that Tender cannot start it. */
export function reasonCopy(reason: string | null): string {
  switch (reason) {
    case "not_installed":
      return "emulator not installed";
    case "core_unlocated":
      return "core location not established";
    case "start_script_runs_nothing":
      return "RetroDECK's start script finds no emulator for it";
    default:
      // inject / shortcut_script / no_rom_target / quoting / startdir / unknown_placeholder / null
      return CANNOT_START_YET;
  }
}

/** What an entry's row says after its label: its not-established note, then why Tender cannot start it, where either holds. */
function entryNotes(e: EmulatorOption): string {
  const note = notEstablishedNote(e.reason);
  const notes = note === null ? [] : [note];
  if (!e.bakeable) notes.push(note === null ? reasonCopy(e.reason) : CANNOT_START_YET);
  return notes.map((part) => ` — ${part}`).join("");
}

export interface EmulatorMenuConfig {
  emulators: EmulatorOption[];
  /** False where the answering emulator source gives no emulator list — the menu
   *  says why, from the two fields below, instead of showing an empty list. */
  emulatorDataAvailable: boolean;
  emulatorDataReason: EmulatorDataReason | null;
  emulatorSource: AnsweringSource | null;
  emulatorPlatformSystem: PlatformSystemAnswer | null;
  /** The active emulator's label — marked with a checkmark, its text drawn in `activeColor`. */
  activeLabel: string | null;
  /** The colour the active emulator's text is drawn in: the surface's selection accent. */
  activeColor: string;
  /** False where the emulator in effect is none of these rows — a default from a source games do not start through
   *  (#2195 D24) — so no row carries the checkmark. */
  markActive?: boolean;
  /** The per-platform override label — marked "(system)". Null in the platform
   *  detail, where the reader already is at the system level. */
  platformCoreLabel: string | null;
  /**
   * Game-detail only: the "Use System Override" reset item that CLEARS the
   * per-game pin. Omit in the platform detail, where picking the default entry
   * is itself the clear-to-empty-label action.
   */
  followSystem?: { hasGameOverride: boolean; onFollowSystem: () => void };
  /** Called with the chosen entry's LABEL — what a pick stores — when an entry is chosen. */
  onPick: (label: string) => void;
  /**
   * Game-detail only, for an installed game: whether each entry can start it
   * (#2195 D22, D31). The rows wait together on a spinner for the one request
   * answering them all, and stay choosable meanwhile.
   */
  launches?: EntryLaunches | undefined;
}

/** The line under an entry saying whether it can start the game: Play's throbber until the answer lands, then its words. */
const EntryLaunchLine: FC<{
  launches: EntryLaunches;
  label: string;
  source: AnsweringSource | null;
  tenderStarts: boolean;
}> = ({ launches, label, source, tenderStarts }) => {
  const launch = useSyncExternalStore(launches.subscribe, () => launches.forLabel(label));
  if (launch === undefined) {
    return (
      <div>
        <span className="romm-throbber" style={{ width: "14px", height: "14px" }} />
      </div>
    );
  }
  if (launch === null) return null;
  const line = entryLaunchLine(launch, source, tenderStarts);
  return line === null ? null : <div style={{ fontSize: "12px", opacity: 0.7 }}>{line}</div>;
};

/**
 * The entry text: its label plus the markers saying what it is. "(default)" is
 * the entry the source ships first, unless its installation is not
 * established, "(chosen in ES-DE)" the one the user chose
 * there, "(system)" the per-platform override, ✓ the one actually in effect —
 * one entry can carry several, which is why they are suffixes rather than a
 * single state.
 */
function emulatorEntryLabel(e: EmulatorOption, isActive: boolean, isPlatformCore: boolean): string {
  const defaultMark = e.is_default ? " (default)" : "";
  const frontendMark = e.chosen_in_frontend ? " (chosen in ES-DE)" : "";
  const systemMark = isPlatformCore ? " (system)" : "";
  const activeMark = isActive ? " ✓" : "";
  return `${e.label}${defaultMark}${frontendMark}${systemMark}${activeMark}`;
}

/** Build the `<Menu>` element for `showContextMenu`. */
export function buildEmulatorMenu(config: EmulatorMenuConfig): ReactNode {
  const { emulators, emulatorDataAvailable, activeLabel, activeColor, platformCoreLabel, followSystem, onPick } =
    config;
  const entryText = (text: string, isActive: boolean): ReactNode =>
    isActive ? <span style={{ color: activeColor }}>{text}</span> : text;
  const { launches } = config;
  const withLaunch = (text: ReactNode, e: EmulatorOption): ReactNode =>
    launches === undefined ? (
      text
    ) : (
      <div>
        <div>{text}</div>
        <EntryLaunchLine launches={launches} label={e.label} source={config.emulatorSource} tenderStarts={e.bakeable} />
      </div>
    );

  if (!emulatorDataAvailable) {
    return (
      <Menu label="Emulator">
        <MenuItem key="unavailable" disabled={true}>
          {emulatorDataReasonSentence(config.emulatorDataReason, config.emulatorSource, config.emulatorPlatformSystem)}
        </MenuItem>
      </Menu>
    );
  }

  const defaultEntry = emulators.find((e) => e.is_default);
  // The active emulator is "the default" when nothing overrides it (no active
  // label, or the default entry's) — the checkmark then sits on the default.
  const activeIsDefault = !activeLabel || defaultEntry?.label === activeLabel;
  const platformCore = entryForLabel(emulators, platformCoreLabel);

  const children: ReactNode[] = [
    <MenuItem key="compat" disabled={true}>
      Switching cores may affect save compatibility
    </MenuItem>,
    <MenuSeparator key="compat-sep" />,
  ];

  if (followSystem) {
    // The core the game falls back to with no per-game pin: the per-platform
    // override when set, else the default. ✓ sits here when the game
    // already follows the system (no per-game pin). "System Override" stays
    // distinct from the "(default)" marker so the menu never shows two defaults.
    const fallbackName = platformCoreLabel ?? defaultEntry?.label ?? null;
    const fallbackSuffix = fallbackName ? ` (${fallbackName})` : "";
    const followsSystemMark = followSystem.hasGameOverride ? "" : " ✓";
    children.push(
      <MenuItem key="follow-system" onClick={followSystem.onFollowSystem}>
        {`Use System Override${fallbackSuffix}${followsSystemMark}`}
      </MenuItem>,
      <MenuSeparator key="follow-sep" />,
    );
  }

  for (const e of emulators) {
    const key = `emu-${e.label}`;
    // The active marker sits on the ACTIVE emulator: the default-marked entry
    // when nothing overrides, otherwise the one the active label names.
    const isActive = config.markActive !== false && (activeIsDefault ? e.is_default : e.label === activeLabel);
    const isPlatformCore = platformCore === e;
    const text = withLaunch(
      entryText(`${emulatorEntryLabel(e, isActive, isPlatformCore)}${entryNotes(e)}`, isActive),
      e,
    );
    children.push(
      isChoosable(e) ? (
        <MenuItem key={key} onClick={() => onPick(e.label)}>
          {text}
        </MenuItem>
      ) : (
        <MenuItem key={key} disabled={true}>
          {text}
        </MenuItem>
      ),
    );
  }

  return <Menu label="Emulator Core">{children}</Menu>;
}
