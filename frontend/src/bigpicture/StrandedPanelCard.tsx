import { FC, useEffect, useState } from "react";
import { WarningCard } from "./WarningCard";
import { readRunningOrStartingApps } from "../utils/runningApps";
import { statusUnreadSentence } from "../utils/runningAppsWording";
import { isStopObserved } from "../utils/sessionManager";
import { useStrandedAnswer } from "../utils/strandedPanelStore";
import { strandedPanelSentence } from "../utils/strandedPanelWording";
import { useInstallerStarted } from "../utils/updateInstallStore";

const UPDATE_LINE = "The update's result shows after that.";
const QUIT_LINE = "Quit the running game yourself — Tender can't stop it right now.";

/** Whether a game runs: `true`; or else, where the store lists apps whose
 *  display status could not be read, their names; or `false`. */
type AnyAppRunning = boolean | { statusUnread: string[] };

/** {@link AnyAppRunning}. A game runs by a start the card saw, or by every
 *  app the store lists as running or starting whose status was read, unless a
 *  stop was seen for it since its last start ({@link isStopObserved}) — no ROM
 *  is known here, so an open session does not count — with the card's own last
 *  lifetime notification per app answering first. In which order Steam calls
 *  the card's callback and the session manager's is not known, so the card does
 *  not count on a stop having reached the session manager yet; and a start it
 *  saw counts before the store lists the app. An app whose status could not be read is named whatever stop
 *  was seen for it: the backend's reload waits on it all the same. */
function anyAppRunning(observed: ReadonlyMap<number, boolean>): AnyAppRunning {
  for (const running of observed.values()) if (running) return true;
  const reading = readRunningOrStartingApps();
  const unread = reading.apps.filter((app) => reading.statusUnread.has(app.appid));
  const read = reading.apps.filter((app) => !reading.statusUnread.has(app.appid));
  if (read.some((app) => observed.get(app.appid) ?? !isStopObserved(app.appid))) return true;
  if (unread.length === 0) return false;
  return { statusUnread: unread.map((app) => app.display_name || "a game") };
}

/** `SteamClient.GameSessions` is declared present, and this card is the one
 *  place its absence must not throw: it would take the page down to lose only
 *  the live update. */
function gameSessions(): typeof SteamClient.GameSessions | undefined {
  return SteamClient.GameSessions;
}

/** Whether any app runs, as Steam reports it, moved by every app's lifetime
 *  notifications. Without game sessions the reading stays the one the page
 *  opened with. */
function useAnyAppRunning(): AnyAppRunning {
  const [running, setRunning] = useState(() => anyAppRunning(new Map()));
  useEffect(() => {
    const observed = new Map<number, boolean>();
    const registration = gameSessions()?.RegisterForAppLifetimeNotifications((update) => {
      observed.set(update.unAppID, update.bRunning);
      setRunning(anyAppRunning(observed));
    });
    return () => registration?.unregister();
  }, []);
  return running;
}

/** Shown on a stranded panel in place of Tender's Quick Access pages and of the
 *  game page's section below the play row. Where an update attempt has started
 *  the installer, the reload or restart the sentence names is what shows its
 *  result. Tender's Stop cannot reach the backend, so while any game runs or is
 *  starting the card says to quit it another way; where none is seen to run but the store
 *  lists apps whose status could not be read, which hold the reload all the
 *  same, it says what to do about them (`utils/runningAppsWording.ts`). */
export const StrandedPanelCard: FC<{ compact?: boolean }> = ({ compact = false }) => {
  const answer = useStrandedAnswer();
  const running = useAnyAppRunning();
  const installerStarted = useInstallerStarted();
  if (!answer) return null;
  const runningLine =
    running === false ? "" : running === true ? QUIT_LINE : statusUnreadSentence(running.statusUnread);
  const message = [installerStarted ? UPDATE_LINE : "", runningLine].filter(Boolean).join(" ");
  return <WarningCard title={strandedPanelSentence(answer)} message={message} compact={compact} />;
};
