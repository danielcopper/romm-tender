---
status: accepted
decided: 2026-10-07
updated: 2026-10-07
amends: [0036]
---

# A stranded panel's upgrade is completed and closed with a code that tells it so

Recorded for [#2034](https://github.com/danielcopper/romm-tender/issues/2034), whose `## Decisions` D1–D4 and D7 this
ADR carries.

## Context

[ADR-0036](0036-the-backend-hosts-itself.md) decision 6 puts every request through three checks — Host, then Origin,
then Token, the only one that authorises — and `host.access` refuses a request without this process's token with a plain
401. The token is minted per backend process, so a panel an earlier process loaded (a stranded panel) is refused by
every later one for as long as it stays in Steam, and only a reload of Steam's JS context replaces it. A browser does
not show a page why a WebSocket handshake failed: a refused upgrade, a port nobody listens on and a server that went
away all reach the page as one `close` event with code 1006 (WHATWG WebSockets, "Feedback from the protocol"). So the
panel could not tell "no backend is running" from "a backend is running and will never admit me". It kept reconnecting
every few seconds — each attempt one line in the log — queued every call for a connection that would never come, and its
Main page concluded, from calls that never answered, that the backend had failed to start.

## Decision

**A `/ws` upgrade that passes the Host and Origin checks and carries a token that is not this process's is completed
(101) and closed at once with a close code of Tender's own:** 4001 when this backend's stranded-panel recovery will
reload Steam's interface once no game is running, 4002 when it will not and Steam has to be restarted. A close after a
completed handshake is the one answer a page is shown.

- **It authorises nothing.** No connection, dispatcher or event sink is attached to the socket; the close frame is the
  only frame the server sends on it, and what the client sends before its own close is read only to be discarded, never
  as a call.
- **It reveals one bit beyond the `Server` field every refusal already carries:** whether this backend will reload
  Steam's interface. Nothing is served, and the token is in no answer and no log line.
- **The check order and the one admission function stay.** `check_access` still runs Host, then Origin, then Token, on
  both entry points; only what the upgrade does with that one refusal changes. A request with no token at all — which a
  panel never sends — the static route, and the Host and Origin refusals keep their plain HTTP status.
- **"Reloads" is said only when it is so:** while the recovery for a stranded panel it has seen is under way and the
  reload limit would let it act now. Anything else — no stranded panel seen, a recovery that ended or gave up, a limit
  that refuses — is "restart Steam".
- **Nothing is said before the backend has looked.** Until the injector has read the marker of Steam's JS context for
  the first time after the backend starts — whether it found a stranded panel there or not — such an upgrade is refused
  with the plain 401, as before: the backend cannot yet know whether a reload is coming, and a panel told "restart
  Steam" in that moment is told something a reload contradicts a moment later. The panel keeps knocking and is told on
  its first knock after the reading. A backend that never reads the context — its debugger never answers, or loading the
  panel is switched off — refuses with 401 for good and logs each knock, which is accepted: a panel is stranded only in
  a context an earlier backend reached over the same debugger.
- **Two codes rather than one code and a reason to parse.** The page receives both, and a number is the half nobody
  rewords.
- **A refusal answered with a code is logged once per panel session,** and again only when the answer for that session
  changes; the memory of sessions is bounded. Every other refusal is logged each time, as before.

On that code the panel stops reconnecting for good, fails every queued and later call at once with the transport reason
`stranded_panel`, says the answer in one notification and on Main, and asks again — one bounded connection — when the
Quick Access menu is opened on Tender's page and when Stop is pressed.

## Considered options

- **Make the 401 readable through CORS** (`Access-Control-Allow-Origin` on the token refusal, read by a `fetch`). It
  keeps two entry points and the order, but makes a refusal carry more than today on every route, and answers only the
  status, not whether a reload is coming.
- **A token-free probe of the port.** It cannot tell Tender from any other program answering there, a backend that fell
  back to another port reads as none, and what Steam's content policy does to such a request from its UI is unknown.
- **A notice the new backend writes into Steam over the debugger.** It reaches only a panel whose code listens for it —
  never one built before the change, such as the panel an update leaves behind — and only while the debugger is
  attached.
