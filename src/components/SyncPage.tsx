/**
 * The Sync page: the preview as a table, the run as a plan of units, and
 * everything that is neither in a column of its own on the right.
 *
 * Wide and untabbed, so it owns its regions: two `Columns`, each scrolling
 * independently inside the frame's measured height. The left column shows
 * exactly one of three things, and the order they are decided in is the order of
 * authority — **a run in flight owns the page**, because the progress rows are
 * the true state of the machine at that moment; a preview held while one runs is
 * not dropped, the store keeps it and the table comes back when the run ends.
 *
 * The session-budget card sits above whichever of the three is showing, and only
 * while no run is in flight: a paused `last_attempt` survives into the resume
 * that clears it, so the card would otherwise stand over the very run it is
 * asking for.
 *
 * **A swap that takes the reader's focus with it hands it to the body that
 * replaces it**, and a swap that does not leaves focus alone —
 * `useEntryFocusOnBodySwap`, one rule for both directions, scoped to the body
 * rather than to the column so that the card above it is never what the column
 * lands on.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Sync.
 */

import { useRef, type CSSProperties, type FC, type ReactNode } from "react";
import { DialogButton } from "@decky/ui";
import { SessionBudgetBanner } from "./SessionBudgetBanner";
import { useEntryFocusOnBodySwap } from "../utils/entryFocus";
import { ButtonRow, FLAT_BUTTON, Muted, SectionTitle } from "./qam/pane";
import { Columns } from "./qam/Columns";
import { WidePage } from "./qam/WidePage";
import { PreviewPanel } from "./sync/PreviewPanel";
import { RunPanel } from "./sync/RunPanel";
import { SyncControls } from "./sync/SyncControls";
import { useSyncPage, type SyncPageState } from "./sync/useSyncPage";

/** The width the controls column is drawn at, and the reason the run rows are
 *  set small: what is left of the panel's 806 px is the table's. */
const CONTROLS_WIDTH = "270px";

export const SyncPage: FC<{ onBack: () => void }> = ({ onBack }) => {
  const state = useSyncPage();
  return (
    <WidePage title="Sync" onBack={onBack} ownRegions>
      <Columns
        columns={[
          { id: "run", content: <SyncMainColumn state={state} /> },
          { id: "controls", width: CONTROLS_WIDTH, content: <SyncControls state={state} /> },
        ]}
      />
    </WidePage>
  );
};

/**
 * The box the three bodies swap inside — a handle for the focus rule and
 * nothing else, so it generates no box of its own: the run body fills the
 * column with `height: 100%`, and a wrapper with a height of its own would
 * either take that reference away or stand at full height under the idle line.
 */
const BODY_BOX: CSSProperties = { display: "contents" };

const SyncMainColumn: FC<{ state: SyncPageState }> = ({ state }) => {
  const bodyBox = useRef<HTMLDivElement | null>(null);
  let body: ReactNode;
  let bodyKind: "run" | "preview" | "idle";
  if (state.run.running) {
    body = <RunPanel state={state} />;
    bodyKind = "run";
  } else if (state.preview !== null) {
    body = <PreviewPanel state={state} preview={state.preview} />;
    bodyKind = "preview";
  } else {
    body = <IdlePanel state={state} />;
    bodyKind = "idle";
  }
  // Focus follows the body it was standing in, and only that one: the button
  // that ends a preview and the button that stops a run each unmount with the
  // body they belong to, while a reader in the controls column chose where they
  // are.
  useEntryFocusOnBodySwap(bodyBox, bodyKind);
  return (
    <>
      {!state.run.running && (
        <SessionBudgetBanner
          lastAttemptStatus={state.stats?.last_attempt?.status}
          syncButton={state.primaryAction}
          rssKb={state.budget?.rss_kb ?? null}
          resumeReady={state.budget?.resume_ready ?? null}
          runDoneItems={state.budget?.run_done_items ?? null}
          runTotalItems={state.budget?.run_total_items ?? null}
        />
      )}
      <div ref={bodyBox} style={BODY_BOX}>
        {body}
      </div>
    </>
  );
};

/** Nothing pending and nothing running: one line saying so, and the button that
 *  changes it. The button's name is the resume question's answer, and it is the
 *  only button that asks it — the session-budget card above it quotes this one
 *  when it asks for a restart rather than deriving the name again. */
const IdlePanel: FC<{ state: SyncPageState }> = ({ state }) => (
  <>
    <SectionTitle title="Preview" />
    <Muted>
      {state.skipPreview
        ? "Nothing is waiting to be applied. Skip preview is on, so this starts the run straight away."
        : "Nothing is waiting to be applied. Working one out compares your library against RomM and adds nothing to Steam."}
    </Muted>
    <ButtonRow padding="4px 16px">
      <DialogButton style={FLAT_BUTTON} disabled={state.busy} onClick={state.startPreview}>
        {state.resume.label}
      </DialogButton>
    </ButtonRow>
    {state.resume.scopeText !== null && <Muted>{state.resume.scopeText}</Muted>}
    {state.status !== null && <Muted>{state.status}</Muted>}
  </>
);
