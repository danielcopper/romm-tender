/**
 * The one spinner the panel shows while it waits, alone or beside the label
 * that says what is under way. What it draws, which waits show it and which do
 * not, and why it carries no class of Steam's: `docs/architecture/qam-panel.md`,
 * "Waiting".
 */

import { type CSSProperties, type FC, useLayoutEffect, useRef } from "react";
import { ensureThrobberStyle } from "../utils/throbberStyle";

const ThrobberSpan: FC<{ size: string; style?: CSSProperties }> = ({ size, style }) => {
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const doc = ref.current?.ownerDocument;
    if (doc) ensureThrobberStyle(doc);
  }, []);
  return (
    <span
      ref={ref}
      data-testid="spinner"
      aria-hidden="true"
      className="romm-throbber"
      style={{ width: size, height: size, boxSizing: "border-box", ...style }}
    />
  );
};

/** The throbber alone, for a place that lays it out itself. */
export const Throbber: FC<{ size?: string }> = ({ size = "1em" }) => <ThrobberSpan size={size} />;

/**
 * *children* with the throbber left of it while *busy*. The label stays a text
 * child of whatever holds this, so the control's text and accessible name are
 * the label's alone, busy or not.
 */
export const ThrobberLabel: FC<{ busy: boolean; children: string }> = ({ busy, children }) => (
  <>
    {busy && <ThrobberSpan size="1em" style={{ marginRight: "0.5em", verticalAlign: "-0.15em" }} />}
    {children}
  </>
);
