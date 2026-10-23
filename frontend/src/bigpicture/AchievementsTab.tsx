/**
 * AchievementsTab — the RetroAchievements tab of the game page.
 *
 * Draws what `useAchievements` (`utils/achievements.ts`) loads: a summary, a
 * progress bar and a row per achievement. The panel calls the hook, so that a
 * tab switch does not load again (`docs/architecture/qam-panel.md`, "The game
 * page's tabs").
 *
 * CSS classes prefixed with `romm-cheevo-` are injected separately by
 * styleInjector.
 */

import { FC, type ReactElement } from "react";
import { DialogButton } from "@decky/ui";
import type { Achievement, AchievementProgress, EarnedAchievement } from "../types";
import { scrollFocusedToCenter } from "../utils/scrollHelpers";
import { earnedByBadgeId, orderEarnedFirst, type AchievementsLoad } from "../utils/achievements";
import { formatCheevoDate } from "../utils/formatters";
import { ConnectingIndicator } from "./saves/ConnectingIndicator";

/** Deterministic sparkle positions for a hardcore badge, seeded per achievement
 *  so the same badge always sparkles the same way across re-renders. */
function makeHcSparkles(seed: number) {
  const rng = (i: number) => {
    const x = Math.sin(seed * 9301 + i * 4973) * 49297;
    return x - Math.floor(x);
  };
  return Array.from({ length: 4 }, (_, i) => ({
    top: `${Math.round(rng(i * 3) * 100)}%`,
    left: `${Math.round(rng(i * 3 + 1) * 100)}%`,
    dur: 2.2 + rng(i * 3 + 2) * 1.8, // 2.2–4.0s
    delay: rng(i * 7 + 5) * 2, // 0–2.0s
  }));
}

/** Render one achievement row — badge, title/description/rarity, earned dates,
 *  points. Earned state (and with it the hardcore treatment) comes from
 *  `earnedMap`. */
function renderCheevoRow(a: Achievement, earnedMap: Map<string, EarnedAchievement>) {
  const earnedData = earnedMap.get(a.badge_id);
  const isEarned = !!earnedData;
  const isHardcore = !!earnedData?.date_hardcore;

  const rowClasses = ["romm-cheevo-row", isEarned ? "romm-cheevo-row-earned" : ""].filter(Boolean).join(" ");

  const imgClasses = ["romm-cheevo-badge-img", isHardcore ? "romm-cheevo-badge-img-hc" : ""].filter(Boolean).join(" ");

  // Date column for earned achievements — show both normal and HC dates
  const dateChildren: ReactElement[] = [];
  if (earnedData?.date) {
    dateChildren.push(
      <span key="date" className="romm-cheevo-date">
        {formatCheevoDate(earnedData.date)}
      </span>,
    );
  }
  if (isHardcore && earnedData.date_hardcore) {
    dateChildren.push(
      <span key="hc-row" style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
        <span className="romm-cheevo-hc-badge">HC</span>
        <span className="romm-cheevo-date">{formatCheevoDate(earnedData.date_hardcore)}</span>
      </span>,
    );
  }

  // Badge image — wrapped with sparkle container for HC achievements
  const imgEl = (
    <img
      alt=""
      className={imgClasses}
      src={isEarned ? a.badge_url : a.badge_url_lock || a.badge_url}
      style={isEarned ? {} : { filter: "grayscale(0.7) opacity(0.6)" }}
    />
  );

  const badgeElement = isHardcore ? (
    <div className="romm-cheevo-img-wrap">
      {imgEl}
      <span className="romm-cheevo-img-sparkles">
        {makeHcSparkles(a.ra_id).map((sp) => {
          // Hoisted and annotated, not inlined into `style={{...}}`: an inline
          // literal is excess-property checked against React's own
          // CSSProperties, which rejects the `--*` keys outright.
          const sparkleStyle: CSSPropertiesWithVars = {
            "--romm-sparkle-top": sp.top,
            "--romm-sparkle-left": sp.left,
            "--romm-sparkle-delay": `${sp.delay.toFixed(1)}s`,
            "--romm-sparkle-dur": `${sp.dur.toFixed(1)}s`,
          };
          return (
            <span key={`hc-sp-${sp.top}-${sp.left}`} className="romm-cheevo-img-sparkle-dot" style={sparkleStyle} />
          );
        })}
      </span>
    </div>
  ) : (
    imgEl
  );

  return (
    <DialogButton
      key={`cheevo-${a.ra_id}`}
      className={rowClasses}
      noFocusRing={false}
      onFocus={scrollFocusedToCenter}
      style={{
        background: "transparent",
        border: "none",
        padding: 0,
        textAlign: "left" as const,
        cursor: "default",
        display: "flex",
        alignItems: "center",
        gap: "12px",
      }}
    >
      {badgeElement}
      <div className="romm-cheevo-details">
        <div className="romm-cheevo-title">{a.title}</div>
        <div className="romm-cheevo-desc">{a.description}</div>
        {a.num_awarded > 0 ? <div className="romm-cheevo-rarity">{`${a.num_awarded} players earned this`}</div> : null}
      </div>
      {dateChildren.length > 0 ? <div className="romm-cheevo-dates">{dateChildren}</div> : null}
      <div className={`romm-cheevo-points ${isEarned ? "" : "romm-cheevo-points-locked"}`}>{`${a.points} pts`}</div>
    </DialogButton>
  );
}

/** The list body: summary bar, progress bar, then the earned rows and the
 *  locked ones, in `orderEarnedFirst`'s order. */
function renderCheevoList(achievements: Achievement[], progress: AchievementProgress | null): ReactElement {
  const earned = progress?.earned ?? 0;
  const total = progress?.total ?? achievements.length;

  const earnedMap = earnedByBadgeId(progress);
  const sorted = orderEarnedFirst(achievements, earnedMap);

  const earnedList = sorted.filter((a) => earnedMap.has(a.badge_id));
  const lockedList = sorted.filter((a) => !earnedMap.has(a.badge_id));

  const cheevoChildren: ReactElement[] = [];

  // Summary bar
  cheevoChildren.push(
    <div key="summary" className="romm-cheevo-summary">
      <span className="romm-cheevo-summary-text">{`${earned} / ${total} Achievements`}</span>
      {progress?.earned_hardcore ? (
        <span className="romm-cheevo-summary-sub">{`${progress.earned_hardcore} hardcore`}</span>
      ) : null}
    </div>,
  );

  // Progress bar
  const pct = total > 0 ? (earned / total) * 100 : 0;
  cheevoChildren.push(
    <div key="progress-bar" className="romm-cheevo-progress-bar">
      <div className="romm-cheevo-progress-fill" style={{ width: `${pct}%` }} />
    </div>,
  );

  if (earnedList.length > 0) {
    cheevoChildren.push(
      <div key="earned-title" className="romm-cheevo-section-title">{`Earned (${earnedList.length})`}</div>,
    );
    earnedList.forEach((a) => cheevoChildren.push(renderCheevoRow(a, earnedMap)));
  }

  if (lockedList.length > 0) {
    cheevoChildren.push(
      <div key="locked-title" className="romm-cheevo-section-title">{`Locked (${lockedList.length})`}</div>,
    );
    lockedList.forEach((a) => cheevoChildren.push(renderCheevoRow(a, earnedMap)));
  }

  // Not wrapped in a section() — that would make the whole list ONE giant
  // focusable element. The individual rows are DialogButtons instead, which is
  // what gives the pane focus-driven scrolling.
  return <div className="romm-cheevo-list">{cheevoChildren}</div>;
}

export const AchievementsTab: FC<{ state: AchievementsLoad }> = ({
  state: { achievements, progress, loading, isOffline },
}) => {
  if (loading) {
    // The load pays the backend retry ladder — surface the shared
    // ConnectingIndicator (with live "(attempt N/M)" progress) instead of
    // frozen "Loading…" text (#1345).
    return <ConnectingIndicator label="Loading achievements" />;
  }

  if (achievements.length === 0) {
    // No cached list to fall back on. When the server is known-offline this is
    // the degraded state for the fast path (no ladder hang); otherwise it's the
    // genuine "this game has none" case (#1345).
    return (
      <div className="romm-panel-muted">
        {isOffline ? "RomM offline — achievements unavailable." : "No achievements found for this game"}
      </div>
    );
  }

  return renderCheevoList(achievements, progress);
};
