---
paths:
  - "backend/host/**/*.py"
---

# The host — six things that go wrong in silence

`backend/host/` is the process, not a layer of the application: the port, the protocol, the single-instance lock and the
lifetime. Standard library only. Two `.importlinter` contracts hold it apart from the code it hosts — it imports nothing
from `services`, `adapters`, `bootstrap` or `domain`, and **nothing but `main.py` imports it**. The second half is the
one that rots without a check: the first service that wants to send an event reaches in here for the sink, and from then
on the composition root is no longer the only place that knows a transport exists.

None of the six below has a mechanical check. Each of them fails green.

## 1. A transport error is not a callable's failure shape

`error.reason` (`method_unknown`, `payload_too_large`, `backend_exception`, `malformed_message`, `connection_lost`)
names something that went wrong **carrying** a call. A callable's own failure is a perfectly successful transport and
arrives inside `result`, in the `{success, reason, message}` shape `scripts/check_failure_shape.py` guards — **and that
gate does not see `host/` at all.** Collapse the two and a user is shown a sentence about their game where a programming
error stands.

## 2. An address with a token in it never reaches the log file

The token lives in memory for one process and travels as part of an address, never as a header. Redaction is
`RedactingFormatter`, set as the **file** handler's own formatter: it rewrites the string that handler renders and
touches nothing else. The one line that prints the whole load address is deliberate and goes to stderr — the terminal
for a hand start, the journal for a service — and the stderr handler keeps a plain formatter so that line arrives whole.

**Do not make this a `logging.Filter`, which is what it was.** A filter sees the record every handler shares, so
redacting there rewrote the message for stderr too, and whether it did depended on the order the handlers were added —
which nothing in the code read as an ordering decision. The deliberate exception silently never existed, and the test
covering it asserted where the filter was installed rather than what stderr received, so it stayed green. A formatter is
per handler by construction; assert on each handler's output.

Nothing may ever add a handler on **stdout**: this process reads the vendored resolver's core-probe child on stdout as
JSON.

## 3. Two entry points, one order: Host, then Origin, then Token

The static route and the WebSocket upgrade each call `check_access`, and it runs the three in that order. Host first so
a rebinding attempt is logged as one instead of as a bad credential; Origin second so the asking origin is on record
before the credential decides anything; Token last because it is the only one that authorises. A third entry point runs
the same function or it is not an entry point.

`AccessPolicy` is built from the port **actually bound**, never from the port that was asked for. After a fallback a
policy built from the wish refuses every request there is.

## 4. An event carrying a claim is awaited, never scheduled

`EventSink.emit` answers whether anybody heard. One caller acts on it — the funnel in `main.py` that attaches a prune
claim to the events whose Steam-side work outlives the backend's — and it can only act on it if it **awaited** the emit.
Scheduled as a task, the answer arrives after the claim has already been handed out, and a claim nobody can discharge
blocks every later operation until it expires.

## 5. The size cap is judged before the payload is buffered

A frame announces its length in its header. `HostConnection` refuses an oversized frame on that announcement, while the
payload is still on the network, and checks the running total the same way across fragments. A cap applied after
assembly protects no memory at all — it measures what has already been spent.

The two caps are separate on purpose and must fail differently: the connection's frame cap is a memory guard whose
breach closes the socket and rejects every call in flight, while the dispatcher's answer cap is one call's problem and
comes back as an ordinary error for that call. Folding them together lets one oversized cover image take every other
request down with it.

## 6. The served directory is handed in, never searched for

`HostServer` takes `static_root`. A host that looked for a build output relative to `__file__` would be the only piece
of this backend that knew the repository's layout, and it would be wrong the moment the program was installed anywhere.
Every request path goes through `lib.path_safety.safe_join`, which resolves symlinks before comparing.

## And one that is not about silence at all

**Without `Access-Control-Allow-Origin` on the static route the panel bundle does not load.** It is fetched by a
cross-origin `import()`, which the browser makes in CORS mode; the header names the asking origin and carries
`Vary: Origin` beside it. Not slower — not at all.
