/**
 * The boundary Tender's Quick Access panel and its section on a game page
 * render inside. It catches; what is drawn in place of what threw is each
 * caller's `fallback`.
 *
 * `@decky/ui` has an `ErrorBoundary`, and it is not a component of its own: the
 * whole module is one `findModuleExport` sweep for a class of STEAM's with a
 * `Reset` and a `componentDidCatch`, whose `render` mentions `lastErrorKey`. Both bundles would resolve
 * that same class. So the reason this one is ours is not whose it would
 * otherwise be — it is that a search into Steam's bundle can stop matching on a
 * client update, and a boundary is the one component whose absence is discovered
 * by the fault it was there to catch. Ours cannot go missing. What Steam's own
 * fallback renders is not established here: the class lives in Steam's bundle,
 * not in `node_modules`.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { DISPLAY_NAME } from "../utils/toast";

interface PanelErrorBoundaryProps {
  children: ReactNode;
  /** Drawn in place of the children once one has thrown, with the failure worded and the rebuild to offer. */
  fallback: (failure: string, reload: () => void) => ReactNode;
  /** Told of each catch, for a caller that keeps a record beyond the console. */
  onCatch?: (failure: string, info: ErrorInfo) => void;
  /**
   * Run before the children are rebuilt, for whatever state outside the boundary
   * should not survive the failure. Optional: the rebuild happens either way,
   * and a boundary that could only reload with a collaborator would be one more
   * thing to forget to wire.
   */
  onReload?: () => void;
}

interface PanelErrorBoundaryState {
  /** The message shown, or null while the children are rendering normally. */
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
    // The console line is written for every caller: what threw is gone, and the
    // component stack is the only thing that says WHERE — the message alone
    // rarely does.
    console.error(`[${DISPLAY_NAME}] the panel threw`, error, info.componentStack);
    this.props.onCatch?.(describe(error), info);
  }

  /**
   * What a rebuild does, and what it deliberately does not.
   *
   * It builds the children again from nothing, and clearing the caught error is
   * the whole of that: React unmounts the subtree below a boundary when it
   * catches, so the children have already been thrown away by the time a
   * fallback can offer this, and rendering them again mounts a new tree with no
   * state carried over. **A `key` here would be inert**, which is why there is
   * none — it would read as the mechanism while React had already done the work.
   *
   * It does NOT re-evaluate the bundle or re-run anything that built the
   * children, so module-level state survives it. `onReload` is where a caller
   * puts the state that should not; what the Quick Access panel keeps, and why,
   * is `docs/architecture/qam-panel.md`, "The boundary".
   */
  private readonly reload = (): void => {
    this.props.onReload?.();
    this.setState({ failure: null });
  };

  override render(): ReactNode {
    const { failure } = this.state;
    if (failure === null) return this.props.children;
    return this.props.fallback(failure, this.reload);
  }
}
