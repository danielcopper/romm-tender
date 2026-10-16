/**
 * What the fallback page offers under its report: the Tender update that
 * repairs it, where the page's sentence names updating Tender
 * (`sentenceNamesATenderUpdate`), and Restart Steam. Plain elements, like the
 * page itself; the buttons are handed in (`fallbackButtons.tsx`).
 *
 * It registers nothing — no event listener, no update dot, no toast watcher —
 * and learns everything from endpoint calls: the due check when it appears,
 * Check for updates, and the install state polled by `useUpdateInstall`, which
 * here carries the attempt's progress alone because no frame reaches the store
 * it would otherwise be pushed into. Why the fallback path stays that bare is
 * `docs/architecture/frontend-bundles.md`, "The start-up check".
 *
 * The release check's answers are kept here rather than in
 * `utils/updateNoticeStore.ts`: the tab icon's dot reads that store, and the
 * fallback path shows no dot.
 *
 * The install's home is Settings › Updates, and this is its one other: where
 * the panel does not mount, this page is all there is. Every state is worded
 * with the sentences that section uses.
 */

import { type CSSProperties, type FC, type ReactNode, useEffect, useReducer, useRef, useState } from "react";

import { checkForUpdateNow, getUpdateNotice, logError, type UpdateNotice } from "../api/backend";
import { NOT_INSTALLED_PROGRAM } from "../bigpicture/settings/UpdatesSection";
import {
  type Block,
  STEP_MARKS,
  buttonLabel,
  installButtonShown,
  installStateUnread,
  shownBlock,
} from "../bigpicture/settings/UpdateInstallRows";
import { type UpdateInstall, useUpdateInstall } from "../bigpicture/settings/useUpdateInstall";
import { cardFrame } from "../bigpicture/UpdateCard";
import { AMBER, AMBER_WASH, MUTED } from "../bigpicture/layout/pane";
import { detach } from "../utils/detach";
import { restartSteamOrSay } from "../utils/steamRestart";
import { useStrandedAnswer } from "../utils/strandedPanelStore";
import { UPDATE_RESULT_AFTER_RELOAD, strandedPanelSentence } from "../utils/strandedPanelWording";
import { useInstallerStarted } from "../utils/updateInstallStore";
import {
  INSTALL_STATE_UNREAD,
  WAITING_FOR,
  installSteps,
  pausedDownloadsHint,
  waitReasonLines,
} from "../utils/updateInstallView";
import { UPDATE_CHECK_FAILED, UPDATE_CHECK_UNREACHABLE } from "../utils/updateNoticeStore";
import type { FallbackButtons } from "./fallbackButtons";

/** Above plain buttons, which Steam's navigation does not reach. */
export const TOUCH_SCREEN_LINE = "Use the touch screen to press these.";

/** A check answered and found no release newer than the running one. */
export const NO_NEWER_RELEASE = "No newer release of Tender is out yet.";

const paragraph: CSSProperties = { margin: "0 0 10px" };
const small: CSSProperties = { fontSize: "12px", color: MUTED, paddingTop: "6px" };
const bar: CSSProperties = { height: "4px", borderRadius: "2px", background: "rgba(255, 255, 255, 0.15)" };

interface Press {
  /** Stable across the label's changes, so the button keeps its place — and Steam's focus — while it relabels. */
  id: string;
  label: string;
  onClick: () => void;
  disabled: boolean;
}

const Presses: FC<{ buttons: FallbackButtons; presses: readonly Press[] }> = ({ buttons, presses }) => {
  const { Column, Button } = buttons;
  return (
    <>
      {buttons.touchOnly && <p style={paragraph}>{TOUCH_SCREEN_LINE}</p>}
      <Column>
        {presses.map((press) => (
          <Button key={press.id} onClick={press.onClick} disabled={press.disabled}>
            {press.label}
          </Button>
        ))}
      </Column>
    </>
  );
};

/** Restart Steam, and the sentence a refused press leaves on the page instead of a toast. */
function useRestartPress(): { press: Press; refusal: string } {
  const [refusal, setRefusal] = useState("");
  return {
    press: {
      id: "restart",
      label: "Restart Steam",
      onClick: () => {
        setRefusal("");
        restartSteamOrSay(setRefusal);
      },
      disabled: false,
    },
    refusal,
  };
}

const Line: FC<{ testId: string; children: ReactNode }> = ({ testId, children }) => (
  <p style={paragraph} data-testid={testId}>
    {children}
  </p>
);

/** Settings' install block, drawn without Steam's progress bar. */
const InstallBlock: FC<{ block: Block }> = ({ block }) => (
  <div style={{ ...(block.failed ? cardFrame(AMBER, AMBER_WASH) : {}), ...paragraph }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", paddingBottom: "6px" }}>
      <span data-testid="fallback-caption" style={block.failed ? { fontWeight: "bold", color: AMBER } : undefined}>
        {block.caption}
      </span>
      <span style={{ color: MUTED, fontVariantNumeric: "tabular-nums" }}>{block.aside}</span>
    </div>
    {typeof block.percent === "number" && (
      <div style={bar} data-testid="fallback-progress" data-percent={block.percent}>
        <div style={{ ...bar, width: `${block.percent}%`, background: "currentColor" }} />
      </div>
    )}
    {block.at && (
      <div style={{ fontSize: "12px", paddingTop: "6px", display: "flex", flexWrap: "wrap", gap: "4px 14px" }}>
        {installSteps(block.at, block.failed).map(({ id, label, status }) => (
          <span key={id} data-testid={`fallback-step-${id}`} data-status={status}>
            <span style={{ color: STEP_MARKS[status][1] }}>{STEP_MARKS[status][0]}</span> {label}
          </span>
        ))}
      </div>
    )}
    {block.note && (
      <div data-testid="fallback-note" style={small}>
        {block.note}
      </div>
    )}
  </div>
);

/** What the install is waiting for, one reason per line. */
const WaitLines: FC<{ install: UpdateInstall }> = ({ install }) => {
  if (install.underWay || install.waitReasons.length === 0) return null;
  return (
    <div style={paragraph} data-testid="fallback-waiting">
      <div>{WAITING_FOR}</div>
      {install.waitReasons.flatMap(waitReasonLines).map((line) => (
        <div key={line} data-testid="fallback-wait-reason">
          {line}
        </div>
      ))}
    </div>
  );
};

/**
 * The update: what the due check and the install state say, Install, Check
 * for updates, and Restart Steam beside them.
 */
const UpdateOffer: FC<{ buttons: FallbackButtons }> = ({ buttons }) => {
  const install = useUpdateInstall();
  const restart = useRestartPress();
  const [notice, setNotice] = useState<UpdateNotice | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState("");
  const [, tick] = useReducer((n: number) => n + 1, 0);
  // Bumped by every read of the notice: an answer to an older read writes nothing once it lands, so the due check
  // still in flight cannot undo what Check for updates found.
  const noticeReads = useRef(0);

  useEffect(() => {
    const issued = ++noticeReads.current;
    getUpdateNotice().then(
      (answer) => {
        if (issued === noticeReads.current) setNotice(answer);
      },
      (e: unknown) => logError(`Failed to check for a newer release: ${e}`),
    );
  }, []);

  // The clock beside an attempt under way, as Settings keeps it.
  useEffect(() => {
    if (!install.underWay) return;
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [install.underWay]);

  const checkNow = async () => {
    // A disabled control still reports a press on the device.
    if (checking || install.underWay) return;
    setChecking(true);
    setCheckResult("");
    const issued = ++noticeReads.current;
    try {
      const answer = await checkForUpdateNow();
      if (issued !== noticeReads.current) return;
      setNotice(answer);
      if (!answer.reached) setCheckResult(UPDATE_CHECK_UNREACHABLE);
    } catch (e) {
      logError(`Failed to check for updates: ${e}`);
      setCheckResult(UPDATE_CHECK_FAILED);
    } finally {
      setChecking(false);
    }
  };

  const showInstall = installButtonShown(install);
  const block = shownBlock(install, null, notice?.current_version || "the earlier version");
  const pausedHint = pausedDownloadsHint(install.pausedDownloads);
  const noneNewer =
    notice !== null &&
    notice.installed_program &&
    notice.latest_version !== null &&
    !notice.newer &&
    !showInstall &&
    !block;

  const presses: Press[] = [
    ...(showInstall
      ? [
          {
            id: "install",
            label: buttonLabel(install),
            onClick: install.install,
            disabled:
              install.pressing || install.underWay || install.waitReasons.length > 0 || install.version === null,
          },
        ]
      : []),
    {
      id: "check",
      label: checking ? "Checking…" : "Check for updates",
      onClick: () => detach(checkNow()),
      disabled: checking || install.underWay,
    },
    restart.press,
  ];

  return (
    <>
      {notice !== null && !notice.installed_program && (
        <Line testId="fallback-not-installed">{NOT_INSTALLED_PROGRAM}</Line>
      )}
      {showInstall && !install.underWay && install.version !== null && (
        <Line testId="fallback-available">Available: {install.version}</Line>
      )}
      <WaitLines install={install} />
      {pausedHint && <Line testId="fallback-paused-hint">{pausedHint}</Line>}
      {install.refusal && <Line testId="fallback-install-refusal">{install.refusal}</Line>}
      {installStateUnread(install) && <Line testId="fallback-install-unread">{INSTALL_STATE_UNREAD}</Line>}
      {block && <InstallBlock block={block} />}
      {noneNewer && <Line testId="fallback-none-newer">{NO_NEWER_RELEASE}</Line>}
      {checkResult && <Line testId="fallback-check-result">{checkResult}</Line>}
      {restart.refusal && <Line testId="fallback-restart-refusal">{restart.refusal}</Line>}
      <Presses buttons={buttons} presses={presses} />
    </>
  );
};

/** Restart Steam alone, where the page's sentence names no update of Tender's. */
const RestartOnly: FC<{ buttons: FallbackButtons }> = ({ buttons }) => {
  const restart = useRestartPress();
  return (
    <>
      {restart.refusal && <Line testId="fallback-restart-refusal">{restart.refusal}</Line>}
      <Presses buttons={buttons} presses={[restart.press]} />
    </>
  );
};

/**
 * The page's actions, or — once the panel is stranded — none: nothing pressed
 * then could reach the backend, so the page says what the backend answered,
 * and that an install's result shows after the reload it names.
 */
export const FallbackActions: FC<{ offerUpdate: boolean; buttons: FallbackButtons }> = ({ offerUpdate, buttons }) => {
  const stranded = useStrandedAnswer();
  const installerStarted = useInstallerStarted();
  if (stranded) {
    const sentence = [strandedPanelSentence(stranded), installerStarted ? UPDATE_RESULT_AFTER_RELOAD : ""];
    return <Line testId="fallback-stranded">{sentence.filter(Boolean).join(" ")}</Line>;
  }
  return offerUpdate ? <UpdateOffer buttons={buttons} /> : <RestartOnly buttons={buttons} />;
};
