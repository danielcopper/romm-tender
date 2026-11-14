/**
 * Shared helpers and context-menu builder for emulator selection (#1210).
 *
 * Every emulator surface — the game-detail core menu, the desktop game page's
 * Emulation card, and the Library page's per-platform menu — renders the same
 * list: every ES-DE `<command>`, each one choosable but an entry the resolver
 * states not installed (#2195 D9, D23, D31). On a game's page, an entry the
 * resolver gives no complete start command for says so under it, so a pick of
 * it does not surprise: Play is then off (#2221 D20). The frontend owns the
 * copy; the backend only ships the reason slug. Keys are the emulator LABEL
 * (not `core_so`), since a standalone emulator has no core.
 *
 * Every entry is a row of its own, in the order the resolver lists them; two
 * labels that run one command are two rows, as in ES-DE, which stores a choice
 * by its label. The entry the user chose inside ES-DE, where it is not the
 * default, is marked so.
 */

import { useSyncExternalStore, type FC, type ReactNode } from "react";
import { Menu, MenuItem, MenuSeparator } from "@decky/ui";
import type { AnsweringSource, EmulatorDataReason, EmulatorOption, GameLaunch, PlatformSystemAnswer } from "../types";
import { emulatorDataReasonSentence } from "./emulatorSourceWording";
import { entryForLabel } from "./emulatorEntries";
import { notEstablishedNote } from "./emulatorInstallation";
import { isChoosable, startVerdictLine } from "./emulatorStart";
import type { EntryLaunches } from "./emulatorLaunches";
import { entryLaunchLine } from "./gameLaunch";

/** Notice shown above game-level and platform-level core lists. */
export const CORE_SWITCH_COMPAT_NOTE = "Switching cores may affect save compatibility";

/** The words an entry the resolver states not installed carries after its label. */
const NOT_INSTALLED_NOTE = "emulator not installed";

/** What an entry's row says after its label: its not-established note, or that it is not installed, where either holds. */
export function emulatorEntryNotes(e: EmulatorOption): string {
  const note = e.reason === "not_installed" ? NOT_INSTALLED_NOTE : notEstablishedNote(e.reason);
  return note === null ? "" : ` — ${note}`;
}

/** Format the per-entry launch verdict line once `askEntryLaunches` has settled for *launch*. */
export function entryLaunchVerdictLine(launch: GameLaunch, source: AnsweringSource | null): string | null {
  const verdict = startVerdictLine(launch.start_verdict ?? null);
  return entryLaunchLine(launch, source, verdict.tenderStarts, verdict.line);
}

/** Whether *entry* is the active emulator row in *emulators*. */
export function isEmulatorEntryActive(
  emulators: readonly EmulatorOption[],
  activeLabel: string | null,
  entry: EmulatorOption,
  markActive = true,
): boolean {
  if (!markActive) return false;
  const defaultEntry = emulators.find((e) => e.is_default);
  const activeIsDefault = !activeLabel || defaultEntry?.label === activeLabel;
  return activeIsDefault ? entry.is_default : entry.label === activeLabel;
}

/** Label for the game-level "Use System Override" reset option. */
function followSystemLabel(
  emulators: readonly EmulatorOption[],
  platformCoreLabel: string | null,
  hasGameOverride: boolean,
): string {
  const defaultEntry = emulators.find((e) => e.is_default);
  const fallbackName = platformCoreLabel ?? defaultEntry?.label ?? null;
  const fallbackSuffix = fallbackName ? ` (${fallbackName})` : "";
  const followsSystemMark = hasGameOverride ? "" : " ✓";
  return `Use System Override${fallbackSuffix}${followsSystemMark}`;
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

/**
 * The line under an entry saying whether it can start the game: Play's throbber until the answer lands, then its
 * words — the file's verdict, and where the resolver gives no complete command for the entry, why (#2221 D20).
 */
const EntryLaunchLine: FC<{
  launches: EntryLaunches;
  label: string;
  source: AnsweringSource | null;
}> = ({ launches, label, source }) => {
  const launch = useSyncExternalStore(launches.subscribe, () => launches.forLabel(label));
  if (launch === undefined) {
    return (
      <div>
        <span className="romm-throbber" style={{ width: "14px", height: "14px" }} />
      </div>
    );
  }
  if (launch === null) return null;
  const line = entryLaunchVerdictLine(launch, source);
  return line === null ? null : <div style={{ fontSize: "12px", opacity: 0.7 }}>{line}</div>;
};

/**
 * The entry text: its label plus the markers saying what it is. "(default)" is
 * the entry the source ships first, unless its installation is not
 * established, "(pinned)" the per-game override on a surface without a separate
 * reset row, "(chosen in ES-DE)" the one the user chose there, "(system)" the
 * per-platform override, ✓ the one actually in effect — one entry can carry
 * several, which is why they are suffixes rather than a single state.
 */
export function emulatorEntryLabel(
  e: EmulatorOption,
  isActive: boolean,
  isPlatformCore: boolean,
  isPinned = false,
): string {
  const defaultMark = e.is_default ? " (default)" : "";
  const pinnedMark = isPinned ? " (pinned)" : "";
  const frontendMark = e.chosen_in_frontend ? " (chosen in ES-DE)" : "";
  const systemMark = isPlatformCore ? " (system)" : "";
  const activeMark = isActive ? " ✓" : "";
  return `${e.label}${defaultMark}${pinnedMark}${frontendMark}${systemMark}${activeMark}`;
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
        <EntryLaunchLine launches={launches} label={e.label} source={config.emulatorSource} />
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

  const platformCore = entryForLabel(emulators, platformCoreLabel);

  const children: ReactNode[] = [
    <MenuItem key="compat" disabled={true}>
      {CORE_SWITCH_COMPAT_NOTE}
    </MenuItem>,
    <MenuSeparator key="compat-sep" />,
  ];

  if (followSystem) {
    children.push(
      <MenuItem key="follow-system" onClick={followSystem.onFollowSystem}>
        {followSystemLabel(emulators, platformCoreLabel, followSystem.hasGameOverride)}
      </MenuItem>,
      <MenuSeparator key="follow-sep" />,
    );
  }

  for (const e of emulators) {
    const key = `emu-${e.label}`;
    const isActive = isEmulatorEntryActive(emulators, activeLabel, e, config.markActive !== false);
    const isPlatformCore = platformCore === e;
    const text = withLaunch(
      entryText(`${emulatorEntryLabel(e, isActive, isPlatformCore)}${emulatorEntryNotes(e)}`, isActive),
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
