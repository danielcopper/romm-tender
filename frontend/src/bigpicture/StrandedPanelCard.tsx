import { FC, useEffect, useState } from "react";
import { WarningCard } from "./WarningCard";
import { readRunningApps } from "../utils/runningApps";
import { readGameRunning } from "../utils/sessionManager";
import { useStrandedAnswer } from "../utils/strandedPanelStore";
import { strandedPanelSentence } from "../utils/strandedPanelWording";

/** Whether any app runs: {@link readGameRunning}'s rule over every app, with the
 *  card's own last lifetime notification per app answering first. In which order
 *  Steam calls the card's callback and the session manager's is not known, so
 *  the card does not count on a stop having reached the session manager yet;
 *  and a start it saw counts before the store lists the app. */
function anyAppRunning(observed: ReadonlyMap<number, boolean>): boolean {
  for (const running of observed.values()) if (running) return true;
  return readRunningApps().apps.some((app) => observed.get(app.appid) ?? readGameRunning(app.appid, null).running);
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
function useAnyAppRunning(): boolean {
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

/** Shown on the game detail page in place of the details a stranded panel could
 *  not read. Tender's Stop cannot reach the backend then, so while any game runs
 *  the card says to quit it another way. */
export const StrandedPanelCard: FC = () => {
  const answer = useStrandedAnswer();
  const running = useAnyAppRunning();
  if (!answer) return null;
  const sentence = strandedPanelSentence(answer);
  if (!running) return <WarningCard title={sentence} />;
  return <WarningCard title={sentence} message="Quit the running game yourself — Tender can't stop it right now." />;
};
