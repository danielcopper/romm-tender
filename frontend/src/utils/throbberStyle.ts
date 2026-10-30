/**
 * The stylesheet behind every throbber the panel draws (`romm-throbber`), put
 * into the document the throbber stands in rather than the one the panel's code
 * runs in — why: `docs/architecture/qam-panel.md`, "Waiting". Once put there it
 * stays: a throbber can mount in that document again at any moment.
 */

export const THROBBER_STYLE_ID = "romm-throbber-style";

const THROBBER_CSS = `@keyframes romm-spin {
  to { transform: rotate(360deg); }
}
.romm-throbber {
  display: inline-block;
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: romm-spin 0.8s linear infinite;
  flex-shrink: 0;
}`;

/** Put the throbber's rules into *doc*, unless they are there already. */
export function ensureThrobberStyle(doc: Document): void {
  if (doc.getElementById(THROBBER_STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = THROBBER_STYLE_ID;
  style.textContent = THROBBER_CSS;
  doc.head.appendChild(style);
}
