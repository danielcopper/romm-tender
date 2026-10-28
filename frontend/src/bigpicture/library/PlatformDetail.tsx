/**
 * Everything about one platform, in the Platforms tab's right-hand pane: what
 * it holds, which core launches it, the BIOS files that core wants, and the two
 * ways to take it back out of Steam.
 *
 * The pane scrolls by moving focus, so every row a reader must reach is a focus
 * stop — the BIOS table's rows included, which is why a row with nothing to
 * press still carries an activate handler.
 *
 * The sync toggle is deliberately absent: it lives in the list row, where the
 * focus already is.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Library.
 */

import type { FC } from "react";
import { ConfirmModal, DialogButton, Focusable, showContextMenu, showModal, Spinner } from "@decky/ui";
import { FaMicrochip } from "react-icons/fa";
import type { FirmwarePlatformExt, SystemCoreInfo } from "../../types";
import { biosColorForLevel } from "../../utils/biosColor";
import { storeOnlySentence } from "../../utils/biosPlaceWords";
import { biosHeldRatio } from "../../utils/biosHeldRatio";
import { biosSummary, saysNoBiosChosen } from "../../utils/biosSummary";
import { emulatorClause } from "../../utils/emulatorClause";
import { buildEmulatorMenu } from "../../utils/emulatorMenu";
import { activeEmulatorNote } from "../../utils/emulatorInstallation";
import {
  canPickAnother,
  emulatorStartRefusal,
  sourceDefaultSentence,
  vanishedPickSentence,
} from "../../utils/emulatorStart";
import { emulatorDataReasonSentence, platformSystemClause } from "../../utils/emulatorSourceWording";
import { getEventTarget } from "../../utils/events";
import { WaitingLine } from "../WaitingLine";
import { entryForLabel } from "../../utils/emulatorEntries";
import { pluralize } from "../../utils/pluralize";
import { SYNC_RUNNING_HINT, useSyncRunning } from "../../utils/syncRunning";
import {
  BiosButtons,
  BiosEmulatorSelector,
  BiosLibraryTable,
  BiosPartLegend,
  BiosShownTable,
  useBiosFiles,
} from "../bios/BiosFiles";
import {
  AMBER,
  BusyElsewhere,
  ButtonRow,
  FLAT_BUTTON,
  GroupStatus,
  MUTED,
  Muted,
  NOTE_FONT,
  RED,
  SELECTION_ACCENT,
  SectionTitle,
  type ScopedStatus,
} from "../layout/pane";
import type { CoreAnswer, DetailStatus, PlatformRow, PlatformsPageState, StatusScope } from "./usePlatformsPage";

/** The page's status line as the pane primitives read it: on this page the key
 *  a line is bound to is the platform slug. The scope type travels with it, so
 *  the literal each `GroupStatus` names is checked against the page's groups. */
const scopedStatus = (status: DetailStatus | null): ScopedStatus<StatusScope> | null =>
  status && { key: status.slug, scope: status.scope, text: status.text };

/**
 * The core picker's button in the header line — the game page's icon button, at
 * this line's scale.
 *
 * The chrome is written out rather than reusing `.romm-gear-btn`, for a reason
 * that needs no claim about which document the class reaches: that class is
 * 36×36 (`styleInjector.ts`), which is the game page's play row, and this line
 * is 28px tall. What IS shared is what the user asked to be shared — the icon
 * and its two colours.
 */
const CORE_BUTTON = {
  alignSelf: "center",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: "28px",
  minWidth: "28px",
  height: "28px",
  padding: 0,
  borderRadius: "4px",
  flexShrink: 0,
} as const;

/**
 * What the pane can offer for this platform's core: a pick, or a sentence
 * saying why there is nothing to pick.
 *
 * One decision, because the answer is rendered in two places — the header's
 * icon button and the line under it — and splitting it would let the two
 * disagree, which is a sentence saying there is nothing to switch under a
 * button that switches.
 */
/**
 * What the header's core control can do, and what to say about it.
 *
 * `pick` opens the menu. `blocked` renders the SAME chip, disabled, with
 * `reason` in its tooltip — a control that is always there and greyed when
 * there is nothing to press, which is the ruling the Remove group already
 * follows. A sentence costs a row to report that nothing can be done; the
 * tooltip costs none and keeps the header's shape constant across panes.
 *
 * `notice` is for the branches that are not "nothing to switch" but a problem
 * with the platform or the install — a failed read, no emulator list from the
 * answering source, no emulator at all. Those keep their line, because a greyed
 * chip would hide a failure behind a hover the Deck's controller has no way to
 * perform. A `pick` carries one too where the emulator RetroDECK would fall
 * back to is one whose installation could not be established.
 */
/**
 * The header's second line: the facts about the platform, smaller and muted.
 *
 * Each part keeps itself on one line and carries the separator in front of it,
 * so the line wraps only between parts — at the Deck's width a free-flowing
 * line broke "RetroDECK system snes" after its first word — and never cuts one.
 */
const HeaderFacts: FC<{ parts: { text: string; color?: string }[] }> = ({ parts }) => (
  <div
    data-testid="platform-facts"
    style={{
      display: "flex",
      flexWrap: "wrap",
      columnGap: "4px",
      padding: "2px 16px 0",
      fontSize: NOTE_FONT,
      color: MUTED,
    }}
  >
    {parts.map((part, index) => (
      <span key={part.text} style={{ whiteSpace: "nowrap", color: part.color }}>
        {index === 0 ? part.text : `· ${part.text}`}
      </span>
    ))}
  </div>
);

type CoreOffer =
  { kind: "pick"; core: SystemCoreInfo; notice?: string } | { kind: "blocked"; reason: string; notice?: string };

/**
 * The offer where nothing is the default (`active_core_label` is null) though
 * the platform lists emulators: the entry its source ships first could not be
 * established installed, so it is never the default by itself (#2195 D9). The
 * picker stays open where an entry can be pinned.
 */
function noDefaultOffer(core: SystemCoreInfo): CoreOffer {
  const sentence =
    emulatorStartRefusal(core.start, "these games", canPickAnother(core.emulators, null)) ??
    "No emulator is the default here.";
  if (core.emulators.some((entry) => entry.bakeable)) return { kind: "pick", core, notice: sentence };
  return { kind: "blocked", reason: sentence, notice: sentence };
}

/** Whether Tender's default comes from a source games do not start through, under which no line of the starting
 *  source stands (#2195 D24). */
function defaultFromElsewhere(core: SystemCoreInfo): boolean {
  return core.tender_default !== null && core.tender_default.kind !== core.emulator_source?.kind;
}

function coreOffer(core: CoreAnswer): CoreOffer {
  if (core === undefined) return { kind: "blocked", reason: "Reading the emulators for this platform…" };
  if (core === null) {
    const failed = "Could not read the emulators for this platform. Reopen the page to try again.";
    return { kind: "blocked", reason: failed, notice: failed };
  }
  if (!core.emulator_data_available) {
    const absent = emulatorDataReasonSentence(core.emulator_data_reason, core.emulator_source, core.platform_system);
    return { kind: "blocked", reason: absent, notice: absent };
  }
  // An EMPTY menu first, because it is the one case where the fallback fails
  // too: `get_emulator_options` answers `available: true` with no options for a
  // system whose catalogue entry lists no command. RetroDECK's own launch then
  // reads `command[1]` for the system, finds nothing, logs "No valid emulator
  // found for system" and exits 1 (`libexec/run_game.sh`), so the games really
  // do not start.
  if (core.emulators.length === 0) {
    // Under another source's default, nothing the starting source states stands under the header.
    if (defaultFromElsewhere(core)) {
      return { kind: "blocked", reason: "RetroDECK lists no emulator for this platform." };
    }
    const none = "RetroDECK lists no emulator for this platform, so its games will not launch.";
    return { kind: "blocked", reason: none, notice: none };
  }
  // Then the menu with no default, ahead of the counts because it is not one.
  if (core.active_core_label === null) return noDefaultOffer(core);
  // A default the games cannot start with is said under the header, and the
  // picker stays open for another emulator wherever there is one to pin.
  const refusal = emulatorStartRefusal(
    core.start,
    "these games",
    canPickAnother(core.emulators, core.active_core_label),
  );
  const notice = refusal === null ? {} : { notice: refusal };
  if (core.emulators.length === 1) {
    const one = refusal ?? "This platform offers one emulator, so there is nothing to switch.";
    return { kind: "blocked", reason: one, ...notice };
  }
  if (!canPickAnother(core.emulators, core.active_core_label)) {
    const none = refusal ?? "None of this platform's other emulators can be pinned from here.";
    return { kind: "blocked", reason: none, ...notice };
  }
  return { kind: "pick", core, ...notice };
}

/**
 * The line under the header when there is no core to pick, and the place a
 * refused switch is reported.
 *
 * There is no heading and no button: the button moved into the header line the
 * platform is already named on, which is where the game page keeps its own. The
 * save-compatibility warning moved with it — into the picker, where
 * `buildEmulatorMenu` has always rendered it, so a copy here was the same
 * sentence on the page that opens the menu carrying it.
 */
const CoreNotice: FC<{ row: PlatformRow; state: PlatformsPageState; offer: CoreOffer; core: CoreAnswer }> = ({
  row,
  state,
  offer,
  core,
}) => (
  <>
    {core?.vanished_pick && <Muted>{vanishedPickSentence(core.vanished_pick)}</Muted>}
    {offer.notice !== undefined && <Muted>{offer.notice}</Muted>}
    {core && <ActiveEmulatorNote core={core} />}
    {core && <SourceDefaults core={core} />}
    <GroupStatus status={scopedStatus(state.status)} forKey={row.slug} scope="core" />
  </>
);

/** Each enabled source's own default for the platform, the user's ES-DE choice where it differs, or why there is none
 *  (#2195 D4). */
const SourceDefaults: FC<{ core: SystemCoreInfo }> = ({ core }) =>
  core.source_defaults.length === 0 ? null : <Muted>{core.source_defaults.map(sourceDefaultSentence).join(" ")}</Muted>;

/** The note under the header where the emulator this platform launches with is one whose installation could not be
 *  established. */
const ActiveEmulatorNote: FC<{ core: SystemCoreInfo }> = ({ core }) => {
  const note = activeEmulatorNote(core.emulators, core.active_core_label);
  return note === null ? null : <Muted>{note}</Muted>;
};

/** The removal confirmation's title. The count is interleaved with the
 *  platform's name, so this is the one count label here that `pluralize`
 *  cannot spell; an unread count drops the number rather than guessing one. */
function removeShortcutsTitle(row: PlatformRow): string {
  if (row.shortcutCount === null) return `Remove ${row.name} shortcuts?`;
  return `Remove ${row.shortcutCount} ${row.name} shortcut${row.shortcutCount === 1 ? "" : "s"}?`;
}

/**
 * The colour the header's core clause and its chip both take.
 *
 * Three outcomes in priority order, which is what the nested form obscured:
 * a state the games cannot start in is red, an override the user chose is gold,
 * and everything else — including a platform running its default — is muted.
 * Both callers read it from here so the icon and the words beside it cannot
 * disagree.
 */
function coreClauseColour(gamesCannotStart: boolean, isOverride: boolean): string {
  if (gamesCannotStart) return RED;
  return isOverride ? AMBER : MUTED;
}

const BiosSection: FC<{ row: PlatformRow; state: PlatformsPageState; firmware: FirmwarePlatformExt }> = ({
  row,
  state,
  firmware,
}) => {
  const part = useBiosFiles({
    slug: row.slug,
    platformName: row.name,
    answer: firmware,
    busySlug: state.busySlug,
    serverOffline: state.serverOffline,
    actions: state,
    launching: "pick",
    stop: "focusable",
    platformNameOf: (slug) => state.rows.get(slug)?.name ?? slug,
  });
  const { attachRoot } = part.focus;
  const files = firmware.files;

  // **The pane words none of the states itself** — `utils/biosSummary.ts` holds
  // them, and this surface takes both halves of one answer: the short `status`
  // as the section's coloured note, where the title beside it says what is being
  // counted, and the `sentence` under it. The order they are read in is decided
  // in there too, which is what stops this pane from reading it differently from
  // the game page's.
  const summary = biosSummary(firmware, files, firmware.bios_level ?? null, row.name || null);
  const { status: summaryLabel, sentence: summaryDescription } = summary;
  // The library's own ratio rides along behind the sentence, in every state and
  // in the words the game page uses — `utils/biosHeldRatio`
  // writes it for both, because the two surfaces say one thing about a platform
  // and a fact one of them carries is a fact the other is missing.
  const heldRatio = biosHeldRatio(firmware);
  // The narrowest of the declines: the emulator this platform launches with was
  // not read at all, so the pane has nothing to point the reader at and says
  // where a file can be put instead. A verdict the resolver left open over an
  // emulator it DID read is not this. It decides one extra LINE only — what the
  // pane offers to fetch is a separate question with a separate input, in
  // `bios/BiosFiles.tsx`.
  const nothingEstablished = firmware.bios_level === "unknown" && firmware.emulator_read === false;
  // Why a download here lands in Tender's firmware store and is linked nowhere.
  const storeOnly = storeOnlySentence(firmware.store_only, firmware.active_core_label ?? null, row.name);

  return (
    <div ref={attachRoot}>
      {/* The ratio is stated once, here, and takes the same mapping the list's
          dot takes — the header carried a second copy of it until the device
          pass, and its width was what wrapped that line three times. Two places
          state a platform's BIOS state and they now agree by construction. */}
      <SectionTitle title="BIOS files" note={summaryLabel} noteColor={biosColorForLevel(firmware.bios_level ?? null)} />
      <Muted fontSize={NOTE_FONT}>{`${summaryDescription}${heldRatio}`}</Muted>
      {/* The route the summary above cannot name: nothing here could say which
          files this system wants, so the reader has to be told that placing one
          by hand still works. Not under the sentence that no BIOS is chosen in
          the emulator's settings: there the setting is what is
          missing, and placing a file changes nothing. */}
      {storeOnly !== null && <Muted fontSize={NOTE_FONT}>{storeOnly}</Muted>}
      {nothingEstablished && !saysNoBiosChosen(summary) && (
        <Muted fontSize={NOTE_FONT}>You can still put BIOS files in your BIOS folder by hand.</Muted>
      )}
      <BiosEmulatorSelector part={part} padding="4px 16px 6px" />
      <BiosShownTable part={part} />
      <BiosButtons part={part} status={scopedStatus(state.status)} />
      <BiosLibraryTable part={part} />
      <BiosPartLegend part={part} />
    </div>
  );
};

/**
 * Taking a platform back out of Steam, and taking its save files off the disk.
 *
 * Neither button is hidden when its own count is zero. Hiding the group on the
 * shortcut count alone strands a platform whose shortcuts were removed but whose
 * saves remain: those saves are then unreachable, and this is the only page that
 * offers them.
 *
 * The saves count is its own read (`count_platform_saves`) rather than a number
 * taken from somewhere cheaper, because nowhere else has it — the delete finds
 * its files through the platform's installed ROMs and counts only what it
 * removed, afterwards.
 */
const RemoveSection: FC<{ row: PlatformRow; state: PlatformsPageState }> = ({ row, state }) => {
  const syncRunning = useSyncRunning();
  const saveCount = state.saveCountFor(row.slug);
  const confirmDeleteSaves = () =>
    showModal(
      <ConfirmModal
        strTitle={`Delete all save files for ${row.name}?`}
        strDescription="This will delete every local save file for ROMs on this platform. Any local changes that haven't been uploaded to RomM yet will be lost permanently. Make sure saves are synced first."
        strOKButtonText="Delete Save Files"
        strCancelButtonText="Cancel"
        onOK={() => state.deleteSaves(row)}
      />,
    );
  const confirmRemoveShortcuts = () =>
    showModal(
      <ConfirmModal
        strTitle={removeShortcutsTitle(row)}
        strDescription="This takes this platform's games out of your Steam library. Downloaded ROM files and save files are left where they are, and the games come back on the next sync while the platform stays enabled."
        strOKButtonText="Remove Shortcuts"
        strCancelButtonText="Cancel"
        onOK={() => state.removeShortcuts(row)}
      />,
    );
  return (
    <>
      {/* One row, and no REMOVE heading over it: both buttons say what they
          remove and are drawn in red, so a title above them names nothing the
          buttons do not — and it would cost the pane a row. */}
      <ButtonRow padding="6px 16px 4px">
        <DialogButton
          style={FLAT_BUTTON}
          disabled={state.busySlug !== null || syncRunning || row.shortcutCount === 0}
          onClick={confirmRemoveShortcuts}
        >
          <span style={{ color: RED }}>
            {row.shortcutCount === null ? "Remove shortcuts" : `Remove ${pluralize(row.shortcutCount, "shortcut")}`}
          </span>
        </DialogButton>
        {/* Unread is not zero and is not a failure either, and the button must
            not look like either: while the count is still coming it is disabled
            and spins, which claims nothing. A pressable plain label would invite
            a press over an unknown set; a `0` would state an emptiness nobody
            established. A failed read is the third case and says so below. */}
        <DialogButton
          style={FLAT_BUTTON}
          disabled={state.busySlug !== null || saveCount === undefined || saveCount === 0}
          onClick={confirmDeleteSaves}
        >
          <span style={{ color: RED, display: "inline-flex", alignItems: "center", gap: "6px" }}>
            {saveCount === undefined && <Spinner width={12} height={12} />}
            {typeof saveCount === "number" ? `Delete ${pluralize(saveCount, "save file")}` : "Delete save files"}
          </span>
        </DialogButton>
      </ButtonRow>
      {/* One of the six reads this page makes. Its failure gets a line because
          with the spinner above it, a silent failure is a spinner that never
          stops — so the two land together. */}
      {saveCount === null && (
        <Muted>Could not read how many save files this platform holds. Pick the platform again to retry.</Muted>
      )}
      {/* The hint was a ButtonItem `description`, attached to the one button it
          was about; under a row it has nowhere to hang, so it names that button
          instead. Only the shortcut removal is refused during a sync — the
          `remove_platform_shortcuts` use case names the sync rule and
          `delete_platform_saves` deliberately does not — so an unscoped sentence
          claims a restriction the backend does not impose. Scoping the sentence
          rather than refusing the delete too: the sync rule is the authority on
          what a sync blocks, and widening it to make a line true would be the
          tail wagging the dog. */}
      {syncRunning && <Muted>{`Removing shortcuts: ${SYNC_RUNNING_HINT}`}</Muted>}
      <GroupStatus status={scopedStatus(state.status)} forKey={row.slug} scope="remove" />
    </>
  );
};

export const PlatformDetail: FC<{ row: PlatformRow; state: PlatformsPageState }> = ({ row, state }) => {
  const core = state.coreFor(row.slug);
  const firmware = row.firmware;
  const offer = coreOffer(core);
  // The core clause is absent while this platform's core read is in flight, and
  // stays absent if it failed — the read is issued per selection, so walking the
  // list shows each newly focused platform's header without a core until its own
  // answer lands. That, and a failed shortcut-count read dropping "· N in
  // Steam", are the two ways this line loses a piece; both failures now say so
  // on the pane, and the in-flight one is a beat rather than a state.
  const activeLabel = core ? core.active_core_label : null;
  const defaultElsewhere = core ? defaultFromElsewhere(core) : false;
  // The other source's default is named even where the starting source lists none.
  const clause =
    defaultElsewhere && activeLabel !== null
      ? { text: activeLabel, gamesCannotStart: false }
      : emulatorClause(core ?? null);
  // The platform-level twin of the game page's `activeCoreIsDefault`, read off
  // the payload's own `is_default`, which marks the entry `default_option`
  // takes. One expression feeds the clause AND the icon, so the two cannot
  // disagree — and no active label is muted rather than gold, because no entry
  // lists a null label and "not the default" is not the same statement as "an
  // override". Where Tender's default comes from a source games do not start
  // through, no row lists it, and it is the default all the same; nothing the
  // starting source states draws the clause red (#2195 D24).
  const activeEntry = entryForLabel(core?.emulators ?? [], activeLabel);
  const activeIsDefault =
    activeEntry === undefined
      ? activeLabel !== null && core?.tender_default?.label === activeLabel
      : activeEntry.is_default;
  const coreColor = coreClauseColour(
    (clause?.gamesCannotStart ?? false) && !defaultElsewhere,
    activeLabel !== null && !activeIsDefault,
  );
  const coreClause = clause === null ? null : { text: clause.text, color: coreColor };
  const systemClause = core?.platform_system ? platformSystemClause(core.platform_system) : null;

  return (
    <>
      {/* Two header lines rather than a Sync section: the toggle is in the list
          row, so what is left here is what the platform IS — and, since the
          device round, the core picker too: a full-width button under the
          header cost the pane a `Field`-height row and a warning line to say
          what the picker itself says. The name has the first line to itself,
          because sharing it with the facts squeezed a two-word name onto two
          lines over the counts at the Deck's width. */}
      <Focusable
        flow-children="horizontal"
        style={{ display: "flex", alignItems: "center", gap: "10px", padding: "8px 16px 0" }}
      >
        <span style={{ flex: "1 1 auto", minWidth: 0, fontSize: "16px", fontWeight: 600, color: "#dcdedf" }}>
          {row.name}
        </span>
        {/* Always rendered, disabled when there is nothing to pick, with the
            reason in the tooltip — the same ruling the Remove group follows.
            A disabled button is still a focus stop, and the wide page's own
            sheet gives it Steam's focus outline, so a reader walking the header
            still lands on it and is told why it is dead. */}
        <DialogButton
          style={CORE_BUTTON}
          title={offer.kind === "pick" ? "Emulator Core" : offer.reason}
          disabled={offer.kind !== "pick" || state.busySlug !== null}
          onClick={(e: MouseEvent) => {
            if (offer.kind !== "pick") return;
            showContextMenu(
              buildEmulatorMenu({
                emulators: offer.core.emulators,
                emulatorDataAvailable: offer.core.emulator_data_available,
                emulatorDataReason: offer.core.emulator_data_reason,
                emulatorSource: offer.core.emulator_source,
                emulatorPlatformSystem: offer.core.platform_system,
                activeLabel: offer.core.active_core_label,
                activeColor: SELECTION_ACCENT,
                // Null on purpose: this pane IS the platform level, so marking
                // an entry "(system)" would restate where the reader already is.
                platformCoreLabel: null,
                onPick: (label) => state.changeCore(row.slug, label),
              }),
              getEventTarget(e),
            );
          }}
        >
          <FaMicrochip size={16} color={coreColor} />
        </DialogButton>
      </Focusable>
      {/* Both counts are ROM FILES, which is what makes the pair readable: one
          shortcut serves a whole sibling group and the game's page switches
          versions across it, so a version that did not win the binding is
          still reachable and still belongs on the right. Counting shortcuts
          there instead read as "207 are missing" on a platform where nothing
          was. The Remove button below keeps the shortcut count — that one
          really is about Steam entries. */}
      <HeaderFacts
        parts={[
          ...(coreClause ? [{ text: coreClause.text, color: coreClause.color }] : []),
          { text: `${pluralize(row.romCount, "version")} on RomM` },
          ...(row.reachableCount === null ? [] : [{ text: `${pluralize(row.reachableCount, "version")} in Steam` }]),
          ...(systemClause === null ? [] : [{ text: systemClause }]),
        ]}
      />
      {/* The count is what failed, not the removal: taking the platform's games
          out of Steam needs only the slug. So the line says the number is
          missing and stops there — the buttons below stay live. */}
      {state.shortcutCountsFailed && (
        <Muted>
          Could not read how many of these games are in Steam. Removing them still works, it just cannot say how many.
          Reopen the page to try again.
        </Muted>
      )}
      {state.removalProgress?.slug === row.slug && (
        <Muted>{`Removing ${state.removalProgress.removed} of ${state.removalProgress.total}…`}</Muted>
      )}
      <BusyElsewhere
        busyKey={state.busySlug}
        ownKey={row.slug}
        busyName={state.rows.get(state.busySlug ?? "")?.name ?? "another platform"}
      />
      <CoreNotice row={row} state={state} offer={offer} core={core} />
      {/* A failed RE-read keeps the answer it could not replace, so what is
          below is the state from before whatever changed it — which is exactly
          where a reader needs telling, and where the notice used to be silent.
          A read that failed with nothing behind it says so where the rows would
          be, below. */}
      {row.firmwareStale && (
        <Muted>
          Could not re-read the BIOS state, so what is below may be out of date. Pick the platform again to retry.
        </Muted>
      )}
      {firmware ? (
        <BiosSection key={row.slug} row={row} state={state} firmware={firmware} />
      ) : (
        <>
          <SectionTitle title="BIOS files" />
          {/* Two ways to have no answer, and they are two different
              sentences. A read still out is a question nobody has answered yet
              — said as one, because the alternative is the reader taking the
              silence for "nothing needed" on a platform whose turn simply has
              not come. A read that did not come back is a question that could
              not be asked. */}
          {row.firmwareState === "pending" && (
            <Muted fontSize={NOTE_FONT}>
              <WaitingLine>Checking what this platform needs…</WaitingLine>
            </Muted>
          )}
          {row.firmwareState === "failed" && (
            <Muted fontSize={NOTE_FONT}>Could not read the BIOS state. Pick the platform again to retry.</Muted>
          )}
        </>
      )}
      <RemoveSection row={row} state={state} />
    </>
  );
};
