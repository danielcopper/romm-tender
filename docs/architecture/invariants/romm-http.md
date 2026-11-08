# Invariants: RomM HTTP

How a RomM answer is classified — a 404's authority above all — the one choke point every RomM request goes out through,
custom proxy headers, and the minimum RomM version. How an entry reads and binds, and the other areas:
[the register's index](index.md).

- **A definitive 404 is `not_found`, never `server_unreachable` — `classify_error`'s table answers every not-found class
  `not_found`** — check + test

  And a catch-all `except Exception` in `services/` may neither bind a verdict key (`reason` / `status` /
  `recommended_action`) to a hardcoded `SERVER_UNREACHABLE` nor raise `ServerUnreachable` or a refusal whose reason is
  that slug; let the RomM error reach the entrypoint or route it through `classify_error`, or peel the 404 off with a
  sibling `except RommNotFoundError` where the verdict is a status or a partial-success flag

  `scripts/check_404_not_unreachable.py --check` (unseen: a verdict or refusal a helper builds or a variable holds,
  `ServerUnreachable` under another name, the slug held in a variable, or in a constant other than an attribute named
  `SERVER_UNREACHABLE`) and `tests/lib/test_errors.py::TestEveryNotFoundClassAnswersNotFound` (`RommNotFoundError` and
  every class defined in or imported into `lib/errors.py` deriving from it; unseen: one `lib/errors.py` neither defines
  nor imports, and a 404 that is no `RommNotFoundError` — the pairing-code exchange's two 404 errors answer
  `server_unreachable` in the table (the connection service catches both first))
- **A 404 becomes `RommNotFoundError` only when RomM's entity layer is proven to have answered it; only the three
  byte-stream fetches opt out** — test + prompt-only — `TestNotFoundDiscrimination` plus the per-call-site
  `test_generic_route_404_still_raises_not_found` trio in `tests/adapters/romm/test_http.py`; a fourth byte-stream
  fetch's opt-out is prompt-only — `.claude/rules/romm-http.md`
- **Every RomM request goes out through `RommHttpAdapter._urlopen`, the single point that clears the known-unreachable
  state** — check — A request method calling `urllib.request.urlopen` itself leaves every retry ladder degraded to one
  attempt until an unrelated path happens to succeed, and nothing else fails. `scripts/check_urlopen_choke_point.py`
  (structural, AST call sites — an aliased `urlopen` or a `getattr` slips past it. Which requests may skip the ladder,
  and which pass `romm_origin=False` because they do not talk to RomM at all, stays prompt-only in
  `.claude/rules/romm-http.md`)
- **Tender attaches a configured custom header to a RomM-origin request and to no other request it issues, and never
  over a header the adapter sets itself** — test + prompt-only — `tests/domain/test_custom_headers.py` pins the
  validation in both directions (every reserved name case-insensitively, a CRLF in a value, and what the persisted
  reading skips), and `tests/adapters/romm/test_http.py::TestCustomProxyHeaders` pins the three attachment points, the
  one exclusion, and that a hand-planted `Authorization` / `Host` still loses. **Nothing joins them.** The rule spans
  `domain/custom_headers.py`, the transport's `_apply_origin_headers` and its three callers — `_apply_default_headers`
  (every authenticated route), `unauthenticated_post_json` (the pairing-code exchange) and `basic_auth_request` (the
  token mint) — plus the one place that must NOT call it, `download_external`. Both directions fail in silence and each
  one is worse than it looks. A fourth request method that forgets the helper works perfectly for the user who has no
  proxy and 403s for the user who has one, on that path only. Adding it to `download_external` hands the user's proxy
  credential to a third-party metadata CDN, which no test would notice because the fetch still succeeds. **What Tender
  issues is the whole of the claim**: `_urlopen` uses the default opener, so urllib's redirect handler follows a 30x by
  copying every header but `content-length` / `content-type` onto the next request with no same-origin test — measured,
  not read: a cross-host 302 delivers both the configured header and the RomM bearer to the foreign host. That is the
  transport's behaviour and predates this rule (the bearer always travelled it), which is why the invariant is worded
  about attachment rather than about arrival; #1889 holds the gap. The reserved set is held by **two** mechanisms, not
  three, and they are not equally strong. One is validation — `_name_refusal` against `RESERVED_NAMES`, reached from
  both `resolve_custom_headers` (the wire) and `stored_custom_headers` (every request, so a hand-edited `settings.json`
  cannot route around it). Those are two call sites of ONE frozenset: drop a name from it and both gates open in a
  single edit. The other is attachment ORDER, and it covers only a name the adapter itself re-adds after
  `_apply_origin_headers` — `User-Agent`, `Authorization`, `Content-Type`, and conditionally `Accept-Encoding` / `Range`
  / `If-None-Match` / `If-Modified-Since`. It covers `Host` and `Content-Length` **not at all**, because the adapter
  sets neither: `http.client._send_request` suppresses its own derived `Host` when the caller supplied one, so a
  configured `Host` retargets every request's virtual host — and for that name, the one this list singles out as
  dangerous, the frozenset is the only defence there is. Nothing pins the ordering leg either: the two tests that look
  like they do (`..._never_displaces_the_bearer`, `..._never_retargets_the_request`) pass because
  `stored_custom_headers` drops the entry long before `add_header` is reached, so both stay green if the ordering is
  reversed. Detail: `docs/architecture/backend-architecture.md` → "the headers every RomM-origin request carries"
- **Every RomM minimum stated for a reader matches the enforced `MIN_ROMM_VERSION`** — check —
  `scripts/check_romm_min_version.py`. The constant in `backend/domain/identity.py` is the floor `test_connection()`
  refuses a server below; every other place the number appears is a restatement for a reader, and a restatement drifts.
  The check holds exactly the statements its `CLAIMS` list names — each one a narrow regex that captures the version and
  nothing around it, so `--fix` can rewrite it in place — and fails both when a named statement says another number and
  when it no longer matches at all, because a regex that silently stopped matching would read as a claim that holds.
  **What it cannot see is a restatement nobody added to that list**: a new page stating the floor is unchecked until its
  sentence is listed there. The worked examples of the floor split three ways. The ones above the floor (`5.4.2-beta`,
  `5.5.0-alpha.1`) are unchecked, because no equality test fits them, and a floor raise makes them false, so they are
  rewritten by hand. The ones at the floor (`5.4.1-beta.1`, `5.4.1-alpha.1`) are listed and checked where a page calls
  them the floor's own tags, as both save-sync pages do. The conditional one in the ConnectionService notes of
  [backend-architecture.md](../backend-architecture.md) is not listed, because it stays true whatever the floor is. ADRs
  are out of scope: they record the floor as it stood when the decision was taken, so the numbers in them are history
  and must not be rewritten
