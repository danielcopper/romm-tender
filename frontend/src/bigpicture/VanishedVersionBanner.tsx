import { FC, useSyncExternalStore } from "react";
import { boundAvailability, onBoundAvailabilityChange, type BoundAvailability } from "../utils/vanishedBinding";
import { WarningCard } from "./WarningCard";

/** What being gone from RomM means for a version that is installed here. */
const INSTALLED_EFFECTS = [
  "Its saves no longer sync, and a launch with a local save asks first.",
  "It cannot be downloaded again.",
  "Its saves stay on this device and do not follow a switch to another version.",
  "Its playtime still counts here but no longer reaches RomM.",
  "Its cover and details no longer update.",
] as const;

/** The one effect that needs another version to switch to. */
const SWITCH_EFFECT = INSTALLED_EFFECTS[2];

const CLEANUP_SENTENCE = "To remove it from this device, with a recovery bundle, use Data Management › Gone from RomM.";

/** What the banner says for one state of the bound version, or `null` while nothing says it is gone. */
export function vanishedVersionNotice(
  availability: BoundAvailability,
  installed: boolean,
): { title: string; effects: readonly string[] } | null {
  if (availability === "live") return null;
  if (availability === "game_gone") {
    return {
      title: "This game is no longer available on RomM.",
      effects: installed ? INSTALLED_EFFECTS.filter((effect) => effect !== SWITCH_EFFECT) : [],
    };
  }
  if (installed) {
    return {
      title: "This version is no longer available on RomM. Your downloaded copy still plays.",
      effects: INSTALLED_EFFECTS,
    };
  }
  return { title: "This version is no longer available on RomM. Choose another version to play.", effects: [] };
}

/**
 * The game page's notice for a bound version RomM answered 404 for. It names
 * where the game is removed and offers no button for it: the version picker on
 * the same page already opens that cleanup.
 */
export const VanishedVersionBanner: FC<{ appId: number; installed: boolean }> = ({ appId, installed }) => {
  const availability = useSyncExternalStore(onBoundAvailabilityChange, () => boundAvailability(appId));

  const notice = vanishedVersionNotice(availability, installed);
  if (!notice) return null;
  return (
    <WarningCard compact title={notice.title} message={CLEANUP_SENTENCE}>
      {notice.effects.length > 0 ? (
        <ul style={{ margin: 0, paddingLeft: "20px", textAlign: "left" }}>
          {notice.effects.map((effect) => (
            <li key={effect}>{effect}</li>
          ))}
        </ul>
      ) : null}
    </WarningCard>
  );
};
