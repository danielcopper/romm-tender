/**
 * Tender's glyph in Steam's Quick Access tab strip, and the four states it
 * animates in.
 *
 * The geometry is generated — `tabIconArt.ts`, written by `scripts/logo` from
 * the mark's own drawing routines — so the strip glyph cannot drift away from
 * the mark. What is decided HERE is only the motion:
 *
 * - **at rest** the cross stands and the ring is level. That is the pose the
 *   generated data's first stop carries, and it is what every path's own `d`
 *   attribute says, so it is also what an engine that animates nothing draws.
 * - **a sync in flight** turns the ring; the cross stands.
 * - **the entry active** — the Quick Access menu open, or the pointer on the
 *   glyph — folds the cross into the button pair and back; the ring stays level.
 * - **both** runs both.
 *
 * **The motion is SMIL, and that is a choice about degradation rather than a
 * preference.** An `<animate>` element that is not rendered leaves the path's own
 * `d` standing, so "not animating" and "at rest" are the same picture and there
 * is no second description of the resting pose to keep in step. The alternative
 * — CSS `@keyframes` over the `d` property — would have put the resting pose in
 * two places and would degrade the same way only on an engine new enough to
 * animate `d` at all.
 *
 * **Four things about this are UNMEASURED**, all of them on the device list for
 * this cut and none of them guessed at here. Whether Steam's tab strip runs an
 * animation there at all. Whether the glyph takes the colour of the selected tab
 * (it asks for `currentColor` and nothing here establishes what that inherits).
 * What size the strip draws it at — the `1.4em` default below is the spike's
 * value, carried over rather than measured. And whether the mirrored half folds:
 * the second pair of bars is a `<use>` of the first, so it moves only if the
 * engine runs the `<animate>` it clones into the shadow tree, and the suite
 * cannot tell — it counts the two authored elements either way. The first three
 * cost at worst a resting pose in the wrong tone or size; the fourth would show
 * as two of the four arms folding.
 */

import { useState, useSyncExternalStore, type FC } from "react";
import { useQuickAccessVisible } from "@decky/ui";
import { getSyncProgress, onSyncProgressChange } from "../utils/syncProgress";
import {
  TAB_ICON_ARC,
  TAB_ICON_CENTRE,
  TAB_ICON_FOLD,
  TAB_ICON_LOOP_SECONDS,
  TAB_ICON_REST,
  TAB_ICON_VIEW_BOX,
} from "./tabIconArt";

/**
 * Where the glyph's two halves are defined, for the `<use>` that draws each one
 * a second time under a `rotate(180)`.
 *
 * Constants rather than `useId()`: one entry exists per Quick Access menu, and
 * a generated id would carry React's own punctuation into a URL fragment for no
 * gain. Two glyphs in one document would both reflect the first one's halves —
 * the same picture while their states agree, which is every state but the
 * pointer, the one input held per instance.
 */
const RING_ID = "tender-tab-icon-ring";
const BODY_ID = "tender-tab-icon-body";

const LOOP = `${TAB_ICON_LOOP_SECONDS}s`;

/** The fold's stops as SMIL wants them: one value list, one time list. */
const foldValues = (arm: "a" | "b") => TAB_ICON_FOLD.map((stop) => stop[arm]).join(";");
const FOLD_TIMES = TAB_ICON_FOLD.map((stop) => stop.at.toFixed(4)).join(";");

/** Is a sync run in flight? The one fact the glyph reads from the plugin. */
function useSyncInFlight(): boolean {
  return useSyncExternalStore(onSyncProgressChange, () => getSyncProgress().running);
}

export interface TabIconProps {
  /**
   * Turn the ring. Defaults to the sync store, which is what the strip renders
   * with; a caller passes it to draw one state on purpose.
   */
  syncing?: boolean;
  /**
   * Fold the cross. Defaults to the Quick Access menu being open, which the
   * glyph reads for itself — `useQuickAccessVisible` resolves the menu's own
   * window off Steam's navigation tree and listens on it, so it is asked and
   * re-bound from inside the menu's React tree rather than held across a remount
   * of it.
   */
  active?: boolean;
  /** Edge length. `1.4em` is the #1897 spike's value, not a measurement (see the header). */
  size?: string;
}

/**
 * One sync arc with its arrowhead. Drawn twice — the pair is point-symmetric
 * about the centre, which is why the generator emits one of them.
 */
const Arc: FC = () => (
  <g id={RING_ID}>
    <path d={TAB_ICON_ARC.d} fill="none" stroke="currentColor" strokeWidth={TAB_ICON_ARC.width} strokeLinecap="round" />
    <polygon
      points={TAB_ICON_ARC.head}
      fill="currentColor"
      stroke="currentColor"
      strokeWidth={TAB_ICON_ARC.headRound}
      strokeLinejoin="round"
    />
  </g>
);

export const TabIcon: FC<TabIconProps> = ({ syncing, active, size = "1.4em" }) => {
  const storeSyncing = useSyncInFlight();
  const menuOpen = useQuickAccessVisible();
  const [pointerOn, setPointerOn] = useState(false);

  const turning = syncing ?? storeSyncing;
  // The pointer is its own reason to be active, and it is read here rather than
  // with a `:hover` rule because a rule would need a stylesheet in the menu's
  // document — one more thing bound to a view that is replaced on every remount,
  // for a state React already delivers.
  const folding = (active ?? menuOpen) || pointerOn;
  const turn = `rotate(180 ${TAB_ICON_CENTRE})`;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={TAB_ICON_VIEW_BOX}
      width={size}
      height={size}
      role="presentation"
      style={{ display: "block" }}
      onPointerEnter={() => setPointerOn(true)}
      onPointerLeave={() => setPointerOn(false)}
      data-testid="tender-tab-icon"
    >
      <g>
        <Arc />
        <use href={`#${RING_ID}`} transform={turn} />
        {turning && (
          <animateTransform
            attributeName="transform"
            attributeType="XML"
            type="rotate"
            from={`0 ${TAB_ICON_CENTRE}`}
            to={`360 ${TAB_ICON_CENTRE}`}
            dur={LOOP}
            repeatCount="indefinite"
          />
        )}
      </g>
      <g id={BODY_ID}>
        <path d={TAB_ICON_REST.a} fill="currentColor">
          {folding && (
            <animate
              attributeName="d"
              values={foldValues("a")}
              keyTimes={FOLD_TIMES}
              dur={LOOP}
              repeatCount="indefinite"
            />
          )}
        </path>
        <path d={TAB_ICON_REST.b} fill="currentColor">
          {folding && (
            <animate
              attributeName="d"
              values={foldValues("b")}
              keyTimes={FOLD_TIMES}
              dur={LOOP}
              repeatCount="indefinite"
            />
          )}
        </path>
      </g>
      <use href={`#${BODY_ID}`} transform={turn} />
    </svg>
  );
};
