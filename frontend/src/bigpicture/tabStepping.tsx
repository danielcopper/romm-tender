/**
 * What passes between the game page's play row and its tabs, which are siblings
 * in Tender's section: L1 and R1 step the tabs from the play row as well, and B
 * on the tab row puts focus on the play row.
 *
 * Inside the tabs, L1 and R1 are Steam's: its tabbed page handles them on its
 * tab row and its content, and stops the event there (`steamui/sp.js`, the
 * tabbed page's bumper handler). The play row is outside that page, so a bumper
 * pressed on it bubbles past it — the play row's handler takes it here, and the
 * tabs register how they step while they are shown, as the BIOS tab's emulator
 * selector does for L2 and R2 (`emulatorStepping.tsx`). The play row registers
 * how it takes focus the same way.
 */

import { createContext, useCallback, useContext, useEffect, useRef, type FC, type ReactNode } from "react";
import { GamepadButton, type GamepadEvent } from "@decky/ui";
import { firstBodyStop, placeEntryFocus } from "../utils/entryFocus";

/** How the shown tabs step, if tabs are shown. */
interface Stepper {
  current: ((by: number) => void) | null;
}

const StepContext = createContext<Stepper | null>(null);
const PlayRowContext = createContext<{ current: HTMLElement | null } | null>(null);

/** How far *button* steps: L1 back, R1 on, anything else not at all. */
function bumperStep(button: number): number | null {
  if (button === GamepadButton.BUMPER_LEFT) return -1;
  if (button === GamepadButton.BUMPER_RIGHT) return 1;
  return null;
}

/** The part of a game page whose bumpers step its tabs, and whose play row B on the tab row reaches. It renders nothing of its own. */
export const TabStepScope: FC<{ children: ReactNode }> = ({ children }) => {
  const stepperRef = useRef<((by: number) => void) | null>(null);
  const playRowRef = useRef<HTMLElement | null>(null);
  return (
    <StepContext.Provider value={stepperRef}>
      <PlayRowContext.Provider value={playRowRef}>{children}</PlayRowContext.Provider>
    </StepContext.Provider>
  );
};

/** The ref for the scope's play row. */
export function usePlayRowRef(): (el: HTMLElement | null) => void {
  const playRowRef = useContext(PlayRowContext);
  return useCallback(
    (el: HTMLElement | null) => {
      if (playRowRef !== null) playRowRef.current = el;
    },
    [playRowRef],
  );
}

/** Puts focus on the play row's first stop — the Play button — and answers whether there was one to take it. */
export function useFocusPlayRow(): () => boolean {
  const playRowRef = useContext(PlayRowContext);
  return useCallback(() => {
    const row = playRowRef?.current;
    return row ? placeEntryFocus(row, firstBodyStop) : false;
  }, [playRowRef]);
}

/** Makes *step* what the scope's bumpers do, for as long as it is given. */
export function useStepTabsOnBumpers(step: ((by: number) => void) | null): void {
  const stepperRef = useContext(StepContext);
  useEffect(() => {
    if (stepperRef === null || step === null) return;
    stepperRef.current = step;
    return () => {
      if (stepperRef.current === step) stepperRef.current = null;
    };
  }, [stepperRef, step]);
}

/** The `onButtonDown` for a node of the scope: a bumper steps the shown tabs, and only then stops there. */
export function useBumperHandler(): (evt: GamepadEvent) => void {
  const stepperRef = useContext(StepContext);
  return (evt) => {
    const step = stepperRef?.current;
    if (!step) return;
    const by = bumperStep(evt.detail.button);
    if (by === null) return;
    evt.stopPropagation();
    step(by);
  };
}

/** The tab *by* steps from *active* among *ids*, going round at either end; *active* outside *ids* counts as the first. */
export function steppedTab(ids: readonly string[], active: string, by: number): string | undefined {
  const at = Math.max(0, ids.indexOf(active));
  return ids[(at + by + ids.length) % ids.length];
}
