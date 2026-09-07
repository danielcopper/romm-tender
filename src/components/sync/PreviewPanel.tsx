/**
 * The Sync page's left column while a preview is pending: what the run would
 * change, as a table, and the three buttons that end the preview.
 *
 * The table is the page. One row per platform the backend reports a change for,
 * one for the RomM collections, one for the Steam collections kept per platform,
 * and one total — and the total comes from the summary's own counts rather than
 * from adding the rows up: the platform rows sum to it by construction, and the
 * two collection rows count collections rather than games, so they carry what
 * changed on a second line instead of in the columns. That leaves the total
 * reading zero over a preview whose only change is a collection, so the line
 * under it states what the columns cannot hold.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Sync.
 */

import type { FC, ReactNode } from "react";
import { DialogButton } from "@decky/ui";
import type { SyncPreview, SyncPreviewSummary } from "../../types";
import { pluralize } from "../../utils/pluralize";
import { previewHasChanges } from "../../utils/previewState";
import { formatDuration, formatTimeRemaining, previewApplySeconds } from "../../utils/syncEstimate";
import { AMBER, ButtonRow, FLAT_BUTTON, MUTED, Muted, SECONDARY_FONT, SectionTitle } from "../qam/pane";
import type { SyncPageState } from "./useSyncPage";
import { PaneRow, TableHeader, TableRow, TABLE_LINE } from "./paneTable";

/** Platform, then the three counts. The numeric columns are sized for the
 *  widest heading rather than the widest number — "Removed" is 52px at the
 *  Deck's scale and a six-figure count is narrower — so what is left goes to the
 *  name, which is the column with something to say. */
const PREVIEW_COLUMNS = "minmax(0, 1fr) 52px 62px 68px";

/** Apply-time (seconds) at or above which the hint appends the sleep caveat.
 *  Below ~10 minutes a sync finishes fast enough that the note is noise. */
const LONG_SYNC_HINT_THRESHOLD_SEC = 600;

/**
 * True when every platform this run spans is being re-fetched AND re-applied —
 * the derived "Force Full Sync" signal (#1318). After Force Full Sync every
 * platform loses its completion stamp, so ``restamp_platform_count`` equals
 * ``sync_platform_count``; and the recorded launch options are cleared, so the
 * whole library counts as ``changed``. The ``changed_count`` leg is what
 * separates a force from a first-ever sync — a fresh install is all-unstamped
 * too, but its delta is pure ``new_count``, so the odd wording is suppressed
 * there. A partial resume reads unequal; an absent count (older backend) is 0.
 */
function isFullResync(s: SyncPreviewSummary): boolean {
  const platforms = s.sync_platform_count ?? 0;
  return platforms > 0 && (s.restamp_platform_count ?? 0) === platforms && s.changed_count > 0;
}

/**
 * Informational scope line — "3 platforms · 2 collections" — the enabled
 * platforms and collections the run spans, shown independently of the diffs
 * (#29). Empty when both counts are 0 (an older backend that omits them), so the
 * caller shows the estimate alone rather than a misleading "0 platforms".
 */
function formatSyncScope(s: SyncPreviewSummary): string {
  const parts: string[] = [];
  if ((s.sync_platform_count ?? 0) > 0) parts.push(pluralize(s.sync_platform_count ?? 0, "platform"));
  if ((s.sync_collection_count ?? 0) > 0) parts.push(pluralize(s.sync_collection_count ?? 0, "collection"));
  return parts.join(" · ");
}

/**
 * What a preview with no rows to draw says instead of a table of zeros.
 *
 * The three cases are three different facts and the reader acts on each
 * differently: cover work still has an Apply to press (#1386), an unstamped
 * platform needs a 0-delta apply to heal a lingering "interrupted" (#1416), and
 * a genuinely empty delta has nothing to do at all.
 *
 * *hasChanges* is `previewHasChanges` — the same condition that arms Apply Sync
 * — and it is what decides between the last sentence and the rest, rather than
 * this function asking the same question a second way. So "up to date" is said
 * only where the button is dead, and a leg of that condition with no wording of
 * its own here falls to the generic line instead.
 */
function emptyPreviewSentence(s: SyncPreviewSummary, hasChanges: boolean): string {
  if (!hasChanges) return "Everything is up to date.";
  const covers = s.cover_refresh_count ?? 0;
  if (covers > 0) return `No shortcut changes — ${pluralize(covers, "cover update")}.`;
  if ((s.restamp_platform_count ?? 0) > 0) return "No changes — finishing a previous sync.";
  return "No shortcut changes — there is still something to apply.";
}

/** The second line of a row whose subject is not games: what was added and what
 *  was removed, with an empty side dropped. `null` when neither side has
 *  anything, which is also what says the row has nothing to draw. */
function addedRemovedLine(added: string | null, removed: string | null): string | null {
  const parts: string[] = [];
  if (added !== null) parts.push(`Added: ${added}`);
  if (removed !== null) parts.push(`Removed: ${removed}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** The RomM collections the run would add to Steam and remove from it, by name.
 *  `null` when the diff names none. */
function collectionNames(s: SyncPreviewSummary): string | null {
  const diff = s.collection_diff;
  if (!diff) return null;
  return addedRemovedLine(
    diff.added.length > 0 ? diff.added.join(", ") : null,
    diff.removed.length > 0 ? diff.removed.join(", ") : null,
  );
}

/** The Steam collections the sync keeps one of per platform, as the counts the
 *  backend sends for them — it sends no names. `null` where nothing changed on
 *  either side. */
function platformCollectionCounts(s: SyncPreviewSummary): string | null {
  const diff = s.platform_collection_diff;
  if (!diff) return null;
  return addedRemovedLine(
    diff.added_count > 0 ? String(diff.added_count) : null,
    diff.removed_count > 0 ? String(diff.removed_count) : null,
  );
}

/** The deadline clause on the section title: what the preview's remaining life
 *  is, or nothing at all when the backend sent no deadline (an older backend). */
function deadlineNote(expired: boolean, secondsLeft: number | null): { note?: string; noteColor?: string } {
  if (expired) return { note: "expired", noteColor: AMBER };
  if (secondsLeft === null) return {};
  return { note: `expires in ${formatTimeRemaining(secondsLeft)}` };
}

/** What a platform row says about a platform the run's platform list does not
 *  hold — its toggle went off, RomM stopped listing it, or the only route to it
 *  is an enabled collection. The wording has to fit all three, so it states what
 *  is known (the platform itself is not synced) rather than guessing the cause. */
const NOT_SYNCED_NOTE = "not synced as a platform";

/** What a row whose subject is not games puts in the three game columns. The
 *  rows above the Total have to add up to it, and a collection count in one of
 *  those columns would not. */
const NO_COUNT = "—";

/**
 * What the Total cannot carry, said under it — the collection changes, which the
 * three game columns hold an em dash for.
 *
 * Without it a preview whose only change is a collection membership reads
 * "Total 0 0 0" under a live Apply Sync, and the reader is left to choose
 * between the number and the button. `null` where both diffs are quiet, which is
 * every preview whose whole story the columns already tell.
 */
function collectionChangeLine(s: SyncPreviewSummary): string | null {
  const parts: string[] = [];
  const added = s.collection_diff?.added.length ?? 0;
  const removed = s.collection_diff?.removed.length ?? 0;
  if (added > 0) parts.push(`${pluralize(added, "collection")} added`);
  if (removed > 0) parts.push(`${pluralize(removed, "collection")} removed`);
  // The per-platform Steam collections come as counts and no names, so they are
  // stated as one changed count rather than split into added and removed —
  // which is also what the row above says about them.
  const platform = (s.platform_collection_diff?.added_count ?? 0) + (s.platform_collection_diff?.removed_count ?? 0);
  if (platform > 0) parts.push(`${pluralize(platform, "platform collection")} changed`);
  return parts.length > 0 ? `plus ${parts.join(", ")}` : null;
}

export const PreviewPanel: FC<{ state: SyncPageState; preview: SyncPreview }> = ({ state, preview }) => {
  const summary = preview.summary;
  const breakdown = summary.platform_breakdown;
  const names = collectionNames(summary);
  const platformCollections = platformCollectionCounts(summary);
  const totals: [number, number, number] = [summary.new_count, summary.changed_count, summary.remove_count];
  const hasCollectionsRow = names !== null;
  // The platform-collections row is offered on the backend's own `has_changes`,
  // which is the field the Apply button's condition reads too
  // (`previewHasChanges`). Keying the row on the counts instead would make it a
  // second reading of the same fact, free to drift from the button's.
  const hasPlatformCollectionsRow = summary.platform_collection_diff?.has_changes === true;
  const showTable = totals[0] + totals[1] + totals[2] > 0 || hasCollectionsRow || hasPlatformCollectionsRow;
  const hasChanges = previewHasChanges(preview);
  const beyondTheColumns = collectionChangeLine(summary);
  const applySeconds = previewApplySeconds(summary);
  const scopeText = formatSyncScope(summary);
  const secondsLeft = state.previewSecondsLeft;
  const expired = state.previewExpired;
  // The sleep caveat is only worth its line for a genuinely long run.
  const hintText =
    "Progress is saved about every 200 games — cancelling is safe." +
    (applySeconds >= LONG_SYNC_HINT_THRESHOLD_SEC ? " Long syncs pause during sleep; keep the Deck powered." : "");

  return (
    <>
      {/* The deadline rides the section title rather than taking a line of its
          own: on the Deck the column has about four rows to spend and the table
          is what they are for. Conditional spreads because the frame's props are
          optional under `exactOptionalPropertyTypes`. */}
      <SectionTitle title="Preview" {...deadlineNote(expired, secondsLeft)} />
      {isFullResync(summary) && <Muted>Full re-sync — all platforms re-fetched.</Muted>}
      {showTable ? (
        <>
          <TableHeader columns={PREVIEW_COLUMNS} cells={["Platform", "New", "Updated", "Removed"]} numericFrom={1} />
          {breakdown === undefined ? (
            // No per-platform split to draw. The totals row below still stands,
            // and nothing here adds anything up — the summary is the authority
            // and this page never reconstructs what it was not sent.
            <Muted>Your server did not send the per-platform split, so only the totals are shown.</Muted>
          ) : (
            breakdown.map((row) => (
              <PreviewRow
                key={row.slug}
                columns={PREVIEW_COLUMNS}
                name={row.name}
                inlineNote={row.synced ? undefined : NOT_SYNCED_NOTE}
                counts={[row.new_count, row.changed_count, row.remove_count]}
              />
            ))
          )}
          {hasCollectionsRow && <PreviewRow columns={PREVIEW_COLUMNS} name="Collections" subline={names} />}
          {hasPlatformCollectionsRow && (
            <PreviewRow
              columns={PREVIEW_COLUMNS}
              name="Platform collections"
              subline={platformCollections ?? undefined}
            />
          )}
          <PreviewRow columns={PREVIEW_COLUMNS} name="Total" counts={totals} total />
          {beyondTheColumns !== null && <Muted>{beyondTheColumns}</Muted>}
        </>
      ) : (
        <Muted>{emptyPreviewSentence(summary, hasChanges)}</Muted>
      )}
      {hasChanges && (
        <PaneRow>
          <span style={{ fontSize: SECONDARY_FONT, color: MUTED }}>
            {scopeText ? `Syncing ${scopeText} · ` : ""}
            {`estimated duration ${formatDuration(applySeconds)}`}
          </span>
        </PaneRow>
      )}
      {hasChanges && <Muted>{hintText}</Muted>}
      {preview.pause_likely === true && (
        <div
          data-testid="budget-advisory"
          style={{
            fontSize: SECONDARY_FONT,
            color: "#7fbcff",
            borderLeft: "3px solid rgba(61, 157, 246, 0.6)",
            padding: "2px 8px",
            margin: "2px 16px 6px",
            lineHeight: 1.4,
          }}
        >
          Will likely pause partway to protect Steam&apos;s memory — normal for large syncs. Restart Steam when
          prompted, then resume.
        </div>
      )}
      {expired && <Muted>This preview is too old to apply. Refresh works out a fresh one.</Muted>}
      <ButtonRow padding="6px 16px 4px">
        <DialogButton style={FLAT_BUTTON} disabled={state.busy || expired || !hasChanges} onClick={state.applyPreview}>
          Apply Sync
        </DialogButton>
        <DialogButton style={FLAT_BUTTON} disabled={state.busy} onClick={state.refreshPreview}>
          Refresh
        </DialogButton>
        <DialogButton style={FLAT_BUTTON} disabled={state.busy} onClick={state.cancelPreview}>
          Cancel
        </DialogButton>
      </ButtonRow>
      {state.status !== null && <Muted>{state.status}</Muted>}
    </>
  );
};

const PreviewRow: FC<{
  columns: string;
  name: string;
  inlineNote?: string | undefined;
  subline?: string | undefined;
  /** The row's three game counts. Absent for a row counting something else,
   *  which draws {@link NO_COUNT} in each column and says what it changed on the
   *  line below. */
  counts?: [number, number, number] | undefined;
  total?: boolean;
}> = ({ columns, name, inlineNote, subline, counts, total }) => {
  // The cell clips; the title is what the reader gets back, so it carries the
  // whole of what the cell would have said.
  const label: ReactNode = (
    <span title={inlineNote ? `${name} · ${inlineNote}` : name} style={{ fontWeight: total ? 600 : 400 }}>
      {name}
      {inlineNote && <span style={{ color: MUTED }}>{` · ${inlineNote}`}</span>}
    </span>
  );
  const countCells: ReactNode[] = counts
    ? [
        <CountCell key="new" value={counts[0]} bold={total ?? false} />,
        <CountCell key="changed" value={counts[1]} bold={total ?? false} />,
        <CountCell key="removed" value={counts[2]} bold={total ?? false} />,
      ]
    : ["new", "changed", "removed"].map((column) => (
        <span key={column} style={{ color: MUTED }}>
          {NO_COUNT}
        </span>
      ));
  return (
    <TableRow
      columns={columns}
      style={total ? { borderTop: TABLE_LINE, marginTop: "2px", paddingTop: "5px" } : undefined}
      cells={[label, ...countCells]}
      numericFrom={1}
      subline={subline}
    />
  );
};

const CountCell: FC<{ value: number; bold: boolean }> = ({ value, bold }) => (
  <span style={{ color: value === 0 ? MUTED : undefined, fontWeight: bold ? 600 : 400 }}>{value}</span>
);
