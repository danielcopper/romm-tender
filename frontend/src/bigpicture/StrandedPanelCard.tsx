import { FC, useEffect, useState } from "react";
import { WarningCard } from "./WarningCard";
import { readGameRunning } from "../utils/sessionManager";
import { useStrandedAnswer } from "../utils/strandedPanelStore";
import { strandedPanelSentence } from "../utils/strandedPanelWording";

interface StrandedPanelCardProps {
  appId: number;
}

/** Whether this page's game runs, as Steam reports it. No ROM is known on a page
 *  whose detail could not be read, so the reading starts from Steam's running
 *  apps alone, and every lifetime notification for the game moves it. */
function useGameRunning(appId: number): boolean {
  const [running, setRunning] = useState(() => readGameRunning(appId, null).running);
  useEffect(() => {
    const registration = SteamClient.GameSessions.RegisterForAppLifetimeNotifications((update) => {
      if (update.unAppID === appId) setRunning(update.bRunning);
    });
    return () => registration.unregister();
  }, [appId]);
  return running;
}

/** Shown on the game detail page in place of the details a stranded panel could
 *  not read. Tender's Stop is not on the page then, so while the game runs the
 *  card says how else to leave it. */
export const StrandedPanelCard: FC<StrandedPanelCardProps> = ({ appId }) => {
  const answer = useStrandedAnswer();
  const running = useGameRunning(appId);
  if (!answer) return null;
  const sentence = strandedPanelSentence(answer);
  if (!running) return <WarningCard title={sentence} />;
  return <WarningCard title={sentence} message="Use Steam's menu to exit the game." />;
};
