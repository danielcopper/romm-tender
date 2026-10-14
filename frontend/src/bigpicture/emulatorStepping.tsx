/**
 * L2 and R2 step the emulator selector from anywhere in the part of a page that
 * holds it: on the Library's Platforms page that is the whole page,
 * and on a game's page all of Tender's section while its BIOS tab is open.
 *
 * As Steam's shipped client code shows (`steamui/sp.js`), it dispatches a
 * gamepad button as a DOM event on the focused element, and it bubbles up
 * through that element's ancestors until a handler stops it — the client wires
 * `onButtonDown` as a listener and passes an unstopped event on to its root. So the handler sits on the nodes the scope is made of, and the
 * selector, wherever it renders inside the scope, registers how to step it.
 * Where no selector is shown — fewer than two emulators, or the BIOS tab
 * closed — the triggers go on to Steam untouched.
 */

import { createContext, useContext, useEffect, useRef, type FC, type ReactNode } from "react";
import { GamepadButton, type GamepadEvent } from "@decky/ui";

/** How the selector shown in a scope steps, if one is shown. */
interface Stepper {
  current: ((by: number) => void) | null;
}

const StepContext = createContext<Stepper | null>(null);

/** How far *button* steps: L2 back, R2 on, anything else not at all. */
function triggerStep(button: number): number | null {
  if (button === GamepadButton.TRIGGER_LEFT) return -1;
  if (button === GamepadButton.TRIGGER_RIGHT) return 1;
  return null;
}

/**
 * The part of a page L2 and R2 reach. It renders nothing of its own: *children*
 * is handed the `onButtonDown` for a node of its own that is around the rest,
 * and a node further in takes it from {@link useTriggerHandler}.
 */
export const EmulatorStepScope: FC<{ children: (onButtonDown: (evt: GamepadEvent) => void) => ReactNode }> = ({
  children,
}) => {
  const stepperRef = useRef<((by: number) => void) | null>(null);
  return (
    <StepContext.Provider value={stepperRef}>
      <WithHandler>{children}</WithHandler>
    </StepContext.Provider>
  );
};

const WithHandler: FC<{ children: (onButtonDown: (evt: GamepadEvent) => void) => ReactNode }> = ({ children }) => {
  const onButtonDown = useTriggerHandler();
  return <>{children(onButtonDown)}</>;
};

/** Makes *step* what the scope's triggers do, for as long as it is given. */
export function useStepOnTriggers(step: ((by: number) => void) | null): void {
  const stepperRef = useContext(StepContext);
  useEffect(() => {
    if (stepperRef === null || step === null) return;
    stepperRef.current = step;
    return () => {
      if (stepperRef.current === step) stepperRef.current = null;
    };
  }, [stepperRef, step]);
}

/** The `onButtonDown` for a node of the scope: a trigger steps the shown selector, and only then stops there. */
export function useTriggerHandler(): (evt: GamepadEvent) => void {
  const stepperRef = useContext(StepContext);
  return (evt) => {
    const step = stepperRef?.current;
    if (!step) return;
    const by = triggerStep(evt.detail.button);
    if (by === null) return;
    evt.stopPropagation();
    step(by);
  };
}
