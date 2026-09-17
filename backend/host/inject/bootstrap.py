"""The source evaluated into Steam: load the panel, or say why it did not.

Contract: the one expression the injector hands to ``Runtime.evaluate``, built
from facts the backend knows and the page does not. It talks to nothing; it is a
string.

**The load-failure card is written here rather than in the frontend, and it is
not a component.** It is what the user sees when the panel bundle did not load,
so it may not be built out of anything the panel needs: not React — the card's
own subject is that Steam's React globals may be missing — and not a second file
fetched at the moment of failure, because a fetch is one of the things that can
have failed. What is left is nodes built by hand in the expression that is
already running. ``frontend/src/boot/StartupFailurePanel.tsx`` is its near
relative and answers a DIFFERENT question, which is why it is not called a
fallback page here (CONTEXT.md → Load-failure card): that one is a React
component rendered inside a panel that DID mount, when a search into Steam's own
interface came back empty.

**It never takes the machine over.** Whether Steam's controller focus can reach
a node appended to its document from outside its own React tree is not
established here, and the card is built so that the answer does not matter: it is
drawn with ``pointer-events: none`` everywhere except its dismiss button, so
every control underneath it stays reachable whatever happens to the button. A
fixed overlay that swallowed input would be worse than the fault it reports.

**The token appears once, inside the addresses.** It has to be there — the panel
reads the port and the token off the URL it was imported from
(``frontend/src/api/host.ts`` hands ``import.meta.url`` to
``hostSocket.ts``'s ``addressFromBundleUrl``) — and it is put nowhere else: not on the
marker, not in the page, and not in what comes back to the backend, which is
redacted against the token itself rather than against a pattern.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

# The global that says this context already has the panel. Measured: a JS-context
# rebuild wipes it and everything short of one leaves it standing, which is
# exactly the question the injector puts to it.
MARKER = "__tender_panel__"

_FACTS_PLACEHOLDER = "__TENDER_FACTS__"

# Where the facts placeholder is filled from. Everything the evaluated source
# needs arrives as ONE JSON object, so the template below has a single
# substitution and no string of ours is ever spliced into JavaScript syntax.
_SOURCE = """
(() => {
  const T = __TENDER_FACTS__;
  const win = window;
  if (win[T.marker]) {
    return Promise.resolve({ ok: true, already: true });
  }
  win[T.marker] = { version: T.version, kind: T.kind };

  const redact = (value) => {
    const text = String(value);
    return T.token ? text.split(T.token).join("<token>") : text;
  };

  const loadAll = async () => {
    for (let i = 0; i < T.urls.length; i += 1) {
      await import(T.urls[i]);
    }
  };

  const el = (tag, style, text) => {
    const node = win.document.createElement(tag);
    Object.assign(node.style, style);
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  };

  const row = (label, value) => {
    const line = el("div", { display: "flex", gap: "8px", margin: "2px 0" });
    line.appendChild(el("span", { opacity: "0.7", minWidth: "96px" }, label));
    line.appendChild(el("span", { fontFamily: "monospace", wordBreak: "break-all" }, value));
    return line;
  };

  const showLoadFailure = (reason) => {
    const card = el("div", {
      position: "fixed",
      left: "16px",
      bottom: "16px",
      zIndex: "2147483000",
      maxWidth: "620px",
      maxHeight: "60vh",
      overflow: "auto",
      padding: "16px 18px",
      borderRadius: "8px",
      border: "1px solid rgba(255, 255, 255, 0.18)",
      background: "rgba(16, 19, 24, 0.96)",
      color: "#e6e8eb",
      font: "14px/1.45 system-ui, sans-serif",
      pointerEvents: "none"
    });
    card.appendChild(el("div", { fontSize: "16px", fontWeight: "600", marginBottom: "8px" }, T.title));
    card.appendChild(el("p", { margin: "0 0 10px" }, T.explanation));
    card.appendChild(el("div", {
      margin: "0 0 10px",
      padding: "8px",
      fontFamily: "monospace",
      fontSize: "12px",
      wordBreak: "break-all",
      background: "rgba(0, 0, 0, 0.35)",
      borderRadius: "4px"
    }, reason));
    const facts = el("div", { fontSize: "12px", margin: "0 0 10px" });
    facts.appendChild(row("Tender", T.version));
    facts.appendChild(row("Steam build", T.steam_build));
    facts.appendChild(row("Log", T.log_path));
    card.appendChild(facts);
    card.appendChild(el("p", { margin: "0 0 10px", fontSize: "12px", opacity: "0.75" }, T.updates));
    const dismiss = el("button", {
      pointerEvents: "auto",
      padding: "6px 14px",
      fontSize: "13px",
      color: "inherit",
      background: "rgba(255, 255, 255, 0.12)",
      border: "1px solid rgba(255, 255, 255, 0.24)",
      borderRadius: "4px",
      cursor: "pointer"
    }, T.dismiss);
    dismiss.addEventListener("click", () => card.remove());
    card.appendChild(dismiss);
    win.document.body.appendChild(card);
  };

  return loadAll().then(
    () => ({ ok: true }),
    (error) => {
      const reason = redact((error && error.stack) || error);
      try {
        showLoadFailure(reason);
        return { ok: false, reason: reason, shown: true };
      } catch (drawing) {
        return { ok: false, reason: reason, shown: false, drawing: redact(drawing) };
      }
    }
  );
})()
"""


@dataclass(frozen=True)
class BootstrapFacts:
    """Everything the evaluated source is told, and the whole of it.

    The wording lives here rather than in the JavaScript so that the sentences a
    user reads are in the language the rest of this backend is written in, and
    so a test can assert them without parsing a script.
    """

    marker: str
    kind: str
    version: str
    steam_build: str
    log_path: str
    urls: tuple[str, ...]
    token: str
    title: str
    explanation: str
    updates: str
    dismiss: str


TITLE = "Tender could not load its panel"
EXPLANATION = (
    "Steam is unaffected and nothing in your library has been changed. Tender did not start at all, "
    "rather than starting half-way, and it will try again when Steam's interface reloads."
)
UPDATES_AT = "Releases are listed at github.com/danielcopper/romm-tender/releases"
DISMISS = "Dismiss"


def build_facts(
    *,
    kind: str,
    version: str,
    steam_build: str,
    log_path: str,
    urls: tuple[str, ...],
    token: str,
) -> BootstrapFacts:
    """Assemble what the evaluated source is given."""
    return BootstrapFacts(
        marker=MARKER,
        kind=kind,
        version=version,
        steam_build=steam_build or "unknown",
        log_path=log_path,
        urls=urls,
        token=token,
        title=TITLE,
        explanation=EXPLANATION,
        updates=UPDATES_AT,
        dismiss=DISMISS,
    )


def build_bootstrap(facts: BootstrapFacts) -> str:
    """The expression to evaluate, with *facts* folded in as one JSON object."""
    return _SOURCE.replace(_FACTS_PLACEHOLDER, json.dumps(asdict(facts)))


def marker_present_expression(marker: str = MARKER) -> str:
    """An expression answering whether this context already carries the panel."""
    return f'typeof window[{json.dumps(marker)}] !== "undefined"'
