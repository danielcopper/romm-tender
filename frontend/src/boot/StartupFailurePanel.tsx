/**
 * What the panel shows instead of itself when a Steam search came back empty.
 *
 * **It uses no `@decky/ui`.** Most of that package is searches into Steam's own
 * bundle — `steamModules.ts` classifies 25 of the 33 members this project imports
 * as exactly that — and this page exists precisely because such a search missed.
 * A page built from them could be the next thing to render nothing.
 * Plain elements and inline styles only, so that whatever else is broken, this
 * still draws.
 *
 * `boot/` is Steam-specific throughout — what makes it so, and why nothing
 * should be built on it as general start-up code, is stated at `steamGlobals.ts`.
 *
 * **Report, do not diagnose.** The page's whole job is to make one fault
 * distinguishable from another: an empty panel looks exactly like a backend that
 * is not running, and the user's next step is different in each case. So it says
 * what missed, whose copy of `@decky/ui` the missed searches belonged to, the
 * one repair that follows, and where to report it. It does not try to work out
 * which Steam update did it, or to carry on with the parts that still resolve.
 *
 * Whose copy that is arrives as a prop rather than being read here. The machine
 * is not what would stand in the way — `searchingCopy.test.ts` sets
 * `window.DFL` and `window.DeckyPluginLoader` freely. The BUILD is: the answer
 * turns on `virtual:tender-bundle-kind` (`searchingCopy.ts`), which resolves
 * under Vitest through one alias to one value for the whole suite, so a page
 * that read it while rendering could be shown Tender's answer and never
 * Decky's.
 */

import type { CSSProperties, FC } from "react";

import type { SearchingCopy } from "./searchingCopy";
import { describeFailure, type StartupReport } from "./steamModules";

const ISSUES_URL = "github.com/danielcopper/romm-tender/issues";

const page: CSSProperties = { padding: "16px", lineHeight: 1.45, fontSize: "14px" };
const heading: CSSProperties = { margin: "0 0 10px", fontSize: "16px", fontWeight: 600 };
const paragraph: CSSProperties = { margin: "0 0 10px" };
const nameList: CSSProperties = {
  margin: "0 0 10px",
  padding: "8px",
  // `monospace` alone, with no named family in front of it: the point is that
  // these are identifiers rather than prose, and a family Steam's UI does not
  // ship would silently fall back to the body font and lose that.
  fontFamily: "monospace",
  fontSize: "12px",
  wordBreak: "break-all",
  background: "rgba(0, 0, 0, 0.25)",
  borderRadius: "4px",
};
const footnote: CSSProperties = { margin: 0, opacity: 0.75, fontSize: "12px" };

/**
 * The names, as one block.
 *
 * Every name is printed rather than a first few and a count: the list IS the
 * bug report, and a truncated one costs a round trip to get the rest.
 */
const MissingNames: FC<{ names: readonly string[] }> = ({ names }) => <div style={nameList}>{names.join(", ")}</div>;

export const StartupFailurePanel: FC<{ report: StartupReport; copy: SearchingCopy }> = ({ report, copy }) => (
  <div style={page}>
    <div style={heading}>Tender could not read Steam&apos;s interface</div>
    <p style={paragraph}>{describeFailure(report, copy)}</p>
    <p style={paragraph}>These are what it looked for and did not find:</p>
    <MissingNames names={report.missing} />
    <p style={paragraph}>
      Nothing is wrong with your library and nothing has been changed. Tender has not started, so that a half-working
      panel cannot act on what it cannot see.
    </p>
    <p style={footnote}>Please report this at {ISSUES_URL}, with the names above.</p>
  </div>
);

export default StartupFailurePanel;
