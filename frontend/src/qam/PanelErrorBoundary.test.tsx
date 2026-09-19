/**
 * What the reader is left with when the panel throws, and what Reload does.
 *
 * `console.error` is spied on in every failing case rather than silenced: React
 * writes its own report there when a boundary catches, and so does the boundary,
 * so a case that did not intercept it would leave the suite's output carrying a
 * stack. The spy's calls are asserted where the boundary's own line is the thing
 * under test.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState, type FC } from "react";
import { PanelErrorBoundary } from "./PanelErrorBoundary";

/**
 * A child that throws while its gate says to, and renders the panel otherwise.
 *
 * The gate is the caller's rather than the child's own: React re-renders a
 * component that threw, so a child that disarmed itself on the way out would
 * succeed on that second pass and the boundary would never catch anything.
 */
const Throws: FC<{ gate: { fail: boolean } }> = ({ gate }) => {
  if (gate.fail) throw new Error("the settings page fell over");
  return <div>the panel</div>;
};

/** A child that throws something that is not an `Error` and carries no message. */
const ThrowsAnObject: FC = () => {
  throw { noMessage: true };
};

const silenceReport = () => vi.spyOn(console, "error").mockImplementation(() => {});

describe("PanelErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the panel untouched while nothing throws", () => {
    render(
      <PanelErrorBoundary>
        <div>the panel</div>
      </PanelErrorBoundary>,
    );

    expect(screen.getByText("the panel")).toBeInTheDocument();
  });

  it("catches a throw, names it, and offers exactly one action", () => {
    const report = silenceReport();

    render(
      <PanelErrorBoundary>
        <Throws gate={{ fail: true }} />
      </PanelErrorBoundary>,
    );

    expect(screen.getByText("the settings page fell over")).toBeInTheDocument();
    expect(screen.getByText(/Reload Tender/)).toBeInTheDocument();
    expect(screen.queryByText("the panel")).not.toBeInTheDocument();
    // The console line is the whole record once the panel is gone, so it has to
    // carry the caught value rather than only the fact of a failure. React logs
    // the same throw on its own account, so the call is picked out by OUR first
    // argument — otherwise this passes on React's line and says nothing.
    const ours = report.mock.calls.filter((call) => String(call[0]).startsWith("[Tender] the panel threw"));
    expect(ours).toHaveLength(1);
    expect(ours[0]?.some((arg) => arg instanceof Error)).toBe(true);
  });

  it("still says something where what was thrown carries no message", () => {
    silenceReport();

    render(
      <PanelErrorBoundary>
        <ThrowsAnObject />
      </PanelErrorBoundary>,
    );

    expect(screen.getByText("an error with no message")).toBeInTheDocument();
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });

  it("rebuilds the panel on Reload, and runs the caller's reset first", () => {
    silenceReport();
    const order: string[] = [];
    const gate = { fail: true };

    render(
      <PanelErrorBoundary
        onReload={() => {
          order.push("reset");
          gate.fail = false;
        }}
      >
        <Throws gate={gate} />
      </PanelErrorBoundary>,
    );
    expect(screen.queryByText("the panel")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText(/Reload Tender/));

    expect(order).toEqual(["reset"]);
    expect(screen.getByText("the panel")).toBeInTheDocument();
    expect(screen.queryByText("the settings page fell over")).not.toBeInTheDocument();
  });

  it("brings the panel back with none of the state the failure left behind", () => {
    silenceReport();
    // The failing component is the one holding the state, so what the case
    // observes belongs to the tree that threw: a sibling's state would be gone
    // for reasons that have nothing to do with this boundary. Its gate is a
    // closure rather than a prop, because a component may not write its props.
    let fail = false;
    const Flaky: FC = () => {
      const [count, setCount] = useState(0);
      if (fail) throw new Error("the page fell over");
      return (
        <button
          type="button"
          onClick={() => {
            if (count >= 1) fail = true;
            setCount(count + 1);
          }}
        >
          count {count}
        </button>
      );
    };

    render(
      <PanelErrorBoundary
        onReload={() => {
          fail = false;
        }}
      >
        <Flaky />
      </PanelErrorBoundary>,
    );

    fireEvent.click(screen.getByText("count 0"));
    expect(screen.getByText("count 1")).toBeInTheDocument();
    fireEvent.click(screen.getByText("count 1"));
    expect(screen.getByText("the page fell over")).toBeInTheDocument();

    fireEvent.click(screen.getByText(/Reload Tender/));

    expect(screen.getByText("count 0")).toBeInTheDocument();
  });
});
