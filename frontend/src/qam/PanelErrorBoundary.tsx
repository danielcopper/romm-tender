/**
 * The boundary Tender's Quick Access panel renders inside, and its one action.
 *
 * `@decky/ui` has an `ErrorBoundary`, and it is not a component of its own: the
 * whole module is one `findModuleExport` sweep for a class of STEAM's with a
 * `Reset` and a `componentDidCatch`, whose `render` mentions `lastErrorKey`. Both bundles would resolve
 * that same class. So the reason this one is ours is not whose it would
 * otherwise be — it is that a search into Steam's bundle can stop matching on a
 * client update, and a boundary is the one component whose absence is discovered
 * by the fault it was there to catch. Ours cannot go missing, and its one action
 * is the one #1942 asks for. What Steam's own fallback renders is not
 * established here: the class lives in Steam's bundle, not in `node_modules`.
 *
 * **What the boundary buys is the rest of the menu.** Without one, a throw
 * inside the panel unmounts the tree it was rendered in — and that tree is
 * Steam's Quick Access view, not ours, so the fault costs the reader every tab
 * in the strip rather than one.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { PLUGIN_NAME } from "../utils/toast";

interface PanelErrorBoundaryProps {
  children: ReactNode;
  /**
   * Run before the panel is rebuilt, for whatever state outside the boundary
   * should not survive the failure. Optional: the rebuild happens either way,
   * and a boundary that could only reload with a collaborator would be one more
   * thing to forget to wire.
   */
  onReload?: () => void;
}

interface PanelErrorBoundaryState {
  /** The message shown, or null while the panel is rendering normally. */
  failure: string | null;
}

/** What a caught value is worth saying. Not every throw is an `Error`. */
function describe(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  const text = String(error);
  return text === "[object Object]" ? "an error with no message" : text;
}

export class PanelErrorBoundary extends Component<PanelErrorBoundaryProps, PanelErrorBoundaryState> {
  override state: PanelErrorBoundaryState = { failure: null };

  static getDerivedStateFromError(error: unknown): Pick<PanelErrorBoundaryState, "failure"> {
    return { failure: describe(error) };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    // The console is the whole record: the panel is gone, so there is no surface
    // of ours left to carry a stack, and the component stack is the only thing
    // that says WHERE — the message alone rarely does.
    console.error(`[${PLUGIN_NAME}] the panel threw`, error, info.componentStack);
  }

  /**
   * What Reload does, and what it deliberately does not.
   *
   * It builds the panel again from nothing, and clearing the caught error is the
   * whole of that: React unmounts the subtree below a boundary when it catches,
   * so the children have already been thrown away by the time this button
   * exists, and rendering them again mounts a new tree with no state carried
   * over. **A `key` here would be inert**, which is why there is none — it would
   * read as the mechanism while React had already done the work.
   *
   * It does NOT re-evaluate the bundle, and it does not re-run the plugin
   * factory. Re-evaluating is the injector's (`backend/host/inject/`) and
   * nothing in this tree can ask for it; re-running the factory would install a
   * second copy of every listener and patch it registers, which are
   * process-wide and already installed — so the "reload" that sounds most
   * thorough is the one that would leave the process worse than the failure did.
   *
   * What survives is therefore the panel's module-level state — the open page,
   * the sync progress, the caches. That is on purpose for the page: a reader
   * whose Settings page threw comes back to Settings rather than to a Main page
   * that tells them nothing about what happened. `onReload` is where a caller
   * puts the state that should not survive.
   */
  private readonly reload = (): void => {
    this.props.onReload?.();
    this.setState({ failure: null });
  };

  override render(): ReactNode {
    const { failure } = this.state;
    if (failure === null) return this.props.children;
    return (
      <PanelSection title={PLUGIN_NAME}>
        <PanelSectionRow>
          <div style={{ fontSize: "13px", lineHeight: 1.4 }}>
            {PLUGIN_NAME} ran into an error and stopped drawing this panel. The rest of the Quick Access menu is
            unaffected.
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: "11px", opacity: 0.75, wordBreak: "break-word" }}>{failure}</div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={this.reload}>
            Reload {PLUGIN_NAME}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }
}
