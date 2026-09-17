# How the panel gets into Steam

The backend serves the panel and also puts it there. Nothing else loads it: there is no plugin loader in this path, no
file copied into a plugin directory, and no manifest anybody reads. `backend/host/inject/` opens Steam's CEF debugger,
finds the renderer, and evaluates one expression into it.

[Frontend bundles](frontend-bundles.md) owns what the files ARE. This page owns how one of them reaches Steam, which of
them is chosen, and what happens when that goes wrong.

## The sequence

From the debugger port answering to the panel being there:

| Step               | What it is                                                                                                   |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| List the targets   | `GET /json` on `127.0.0.1:8080`, retried until Steam has named its renderer                                  |
| Attach             | one WebSocket to the `SharedJSContext` target's `webSocketDebuggerUrl`                                       |
| `Page.enable`      | so `Page.domContentEventFired` arrives; subscribed to before it is enabled, so none is missed                |
| Ask for the marker | `typeof window["__tender_panel__"] !== "undefined"` — a context that already carries the panel is left alone |
| Wait until ready   | Steam's module registry, and beside Decky its copy of `@decky/ui` as well                                    |
| Evaluate           | one expression that claims the marker and imports the chosen bundles in order                                |
| Ask again, later   | is Steam's interface still there? — see [the crash watchdog](#the-crash-watchdog)                            |

After that the loop waits on two subscriptions and nothing else — `Page.domContentEventFired`, which says the marker has
been wiped, and `Runtime.bindingCalled`, which is [the card's one button](#one-button-and-an-address-in-plain-text). It
polls in exactly two places, both before the panel is in: while Steam is still naming the renderer, and while the page
is becoming something the panel can load into.

**A target the debugger lists with no title is not "nothing is open".** Measured on the device from the port answering:
a target appears at **+0.20 s with an empty title**, the **same** target is renamed `SharedJSContext` at **+0.6 s**, and
`webpackChunksteamui` is ready at **+0.84 s**. Discovery that read the first miss as a verdict would give up half a
second before the answer existed, which is why every miss here is retried and the log distinguishes "targets, none named
yet" from "nothing there".

## Which bundles, and the rule that cannot bend

**`dist/globals.js` is never loaded where Decky Loader is running.** It carries `@decky/ui`'s module sweep at import
scope, and re-running that sweep under an interface already rendering from those modules is the crash that takes the Big
Picture window down — the only crash cause ever observed here
([frontend bundles](frontend-bundles.md#why-two-copies-of-the-panel)).

So there are two answers and they are not two spellings of one thing:

| The machine                 | Loaded                        | Why                                                                      |
| --------------------------- | ----------------------------- | ------------------------------------------------------------------------ |
| Decky Loader is not serving | `globals.js`, then `index.js` | nothing else is loading into Steam, so Tender installs the React globals |
| Decky Loader is serving     | `index-coexistence.js` alone  | the loader has installed those globals and holds a loaded `@decky/ui`    |

**The answer comes from the machine, never from the window.** At the earliest moment an injection is possible, every
marker Decky eventually sets — `DFL`, `DeckyPluginLoader`, `DeckyBackend`, `deckyAuthToken`, `deckyHasLoaded` — is still
`undefined` (measured); a machine with Decky and one without look identical there. The injector is a local process, so
it asks the system: does Decky Loader's own server answer on its port? Three questions look alike and only that one is
worth asking — "is it installed" is a directory, and a machine that installed it and turned it off answers yes; "is the
unit active" is systemd's word for a process that may be starting or wedged. Everything Decky renders into Steam is
served from that port, so a loader that is rendering has answered there.

It is still not "Decky is rendering", and the remaining gap is left on the safe side: a loader that started five seconds
ago has not injected yet and this reads it as serving. That costs a machine with Decky nothing — it gets the bundle that
shares Decky's copy, which is what it wants either way.

Beside a serving Decky the injector then WAITS for `DFL` before loading, which is not a reading of whether Decky is
there (the machine has already answered that) but a wait for something known to be coming: `DFL` was still `undefined`
at +4.65 s on the reference machine, and Decky had finished at +10.6 s with ten plugins.

## The marker

`window.__tender_panel__` is how a context says it already carries the panel. Measured, in two device runs with a mark
of their own rather than with this marker: a JS-context rebuild WIPES such a mark, while a mark planted on the first
`Page.domContentEventFired` survived the settle that followed — eleven `Page.windowOpen` events over 16 s. In the other
run a mark planted at +0.96 s was still there after Decky had finished at +10.6 s with ten plugins, which is what says
Decky starting beside us does not rebuild the context.

The expression claims the marker BEFORE it imports anything, so a second evaluation cannot load the panel twice. It
holds the marker even when the import fails, which is what stops a broken bundle being retried into the same context —
the [load-failure card](#the-load-failure-card) explains that state instead.

## The crash watchdog

The one way this feature can go catastrophically wrong is taking the Steam interface down, so it is watched.

**Not a crash counter.** The signature, provoked deliberately on the device: the `Runtime.evaluate` returns successfully
after 0.13 s, **3.04 s later four page targets vanish at once** leaving only `SharedJSContext`, the debugger keeps
answering, and nothing recovers within 45 s. Because `SharedJSContext` survives, our marker survives with it — so the
injector sees "already injected" and never tries again. Within one Steam session the crash happens at most once, and
"three crashes in a minute" could never be observed.

What is watched instead is the other side of it:

1. **A record is opened before the expression is evaluated** and closed once the interface is still there afterwards.
   "Still there" is measured as at least one page target besides the renderer, discounted by target ID rather than by
   title.
2. **The check sits ten seconds after the injection** — three times the one measured interval between the evaluate
   returning and the targets going. Waiting longer costs only a record staying open a few seconds more; waiting less
   would record a crash as a survival.
3. **An attempt nothing could be established about is not counted.** If the debugger stopped answering, if the backend
   is shutting down, or if there was no other page target when the panel was loaded, there was no collapse to observe —
   and an unobserved attempt recorded as a failure would stop the injection over a user closing Steam.
4. **Two consecutive failures stop it**, not three. One can be anything; two is evidence; three dead Steam starts is too
   much to ask of someone who has no reason to suspect this program.
5. **It starts trying again by itself.** The record carries a fingerprint of the three things that could have repaired
   the fault — Tender's version, a digest of the bundles' bytes, Steam's client build — and a change in any of them
   drops the count. The user updates something and it works again, with no file to find and nothing to delete.

The record lives at `<state_dir>/injection-guard.json`. A state directory it cannot be written to leaves the guard
unable to count, which is deliberately the lenient direction: this file exists to stop a crash loop, and refusing to
load the panel because a directory is read-only would be a fault of its own.

**The way out cannot be inside Steam**, because in this state Steam's interface is the thing that is gone. It is a log
line naming the state and one environment variable:

| `TENDER_INJECT` | What it does                                                      |
| --------------- | ----------------------------------------------------------------- |
| `off`           | load nothing into Steam at all, and say so once at start-up       |
| `force`         | load the panel even where the watchdog has stopped                |
| anything else   | nothing — a typo in a unit file may not stop the backend starting |

Steam's build is read from Steam's own record of what it installed:
`<steam>/package/steam_client_<branch>_ubuntu12.manifest` carries a `"version"` field, and `<steam>/package/beta` names
the branch. Deliberately not the debugger's version string, which is CEF's (`Chrome/126…` on the reference machine) and
moves only when Valve changes CEF — where Steam's interface is rebuilt far more often, and those rebuilds are exactly
what this reading is for.

## The load-failure card

For the other failure: the interface is alive and our bundle does not mount. The evaluated expression catches it and
draws a small card into Steam's own document naming Tender's version, Steam's build, the log path, where releases are
listed, and the reason the import gave.

**It is not a React component and it fetches nothing.** Its own subject is that Steam's React globals may be missing,
and a second file fetched at the moment of failure could fail for the reason the first one did. It is plain nodes built
by hand in the expression that is already running. `frontend/src/boot/StartupFailurePanel.tsx` is its near relative and
answers a different question — see CONTEXT.md → Load-failure card for why the two are not called the same thing.

**It never takes the machine over.** Whether Steam's controller focus can reach a node appended to its document from
outside its own React tree is not established here, so the card is built so that the answer does not matter: it is drawn
with `pointer-events: none` everywhere except its one button, and every control underneath stays reachable whatever
happens to that button. A fixed overlay that swallowed input would be worse than the fault it reports.

### One button, and an address in plain text

The card carries exactly one action — **Stop trying until Tender restarts** — and the releases address as text.

The button reaches this process through a **debugger binding** (`Runtime.addBinding`), which is the whole of the card's
way back: the card holds no token, opens no socket of its own, and the backend grows no route for it. Pressed, it sends
one word, the injector stops loading anything into Steam for the rest of this process, and the card is taken off the
screen. "Restart" there means **this backend's own process** — not Steam, not the machine — and the card says so under
the button, because that is the word a reader is most likely to get wrong.

Two properties of the binding decide where it is installed:

- It goes on **before** the source that may draw the card is evaluated, so the button is wired from the moment it
  exists. Whether it is drawn at all is the injector's answer: `Runtime.addBinding` is asked first, and a refusal is
  carried into the card as "no button", because a button that cannot report a press is worse here than none.
- It is **re-installed on every injection** rather than once per attachment. Whether a binding survives a JS-context
  rebuild is not established here; adding one that survived costs a round trip, and missing one costs the card its only
  button.

`Runtime.bindingCalled` is a Runtime-domain event, so it arrives only while that domain is enabled — and enabling it
turns on every other Runtime event for that connection. So it is enabled **only once a card is up**: after a load that
failed, where the card is the only thing left to act on, and never on the path where the panel came up.

**Checking for an update is text rather than a button**, because nothing in this program updates itself yet and no way
to open a web page out of Steam's UI has been measured here. It becomes a button in the cut that gives it something to
do ([#1903](https://github.com/danielcopper/romm-tender/issues/1903)).

## The token

The panel reads its port and its token off the URL it was imported from (`frontend/src/api/host.ts` hands
`import.meta.url` to `hostSocket.ts`), so the address the expression imports carries the token and there is nowhere else
for it to be. The same facts object carries it once more, as a field, because the expression's redaction matches on the
token itself rather than on a pattern — and both die with the expression that holds them.

What it is kept out of is everything that outlives that: the marker left on the window, the card on screen, and what
comes back to the backend — any error text has the token replaced with `<token>` before the injector ever logs it.

## Running it

`mise run dev` builds the panel and runs the backend, which serves `dist/` and loads it. It needs
`~/.steam/steam/.cef-enable-remote-debugging` to exist and Steam to have started since it appeared. See
[the dev loop](../contributing/frontend-dev-loop.md).

## What the tests here can and cannot see

`tests/host/inject/` drives the real client against a fake debugger on a real loopback port: real HTTP, real RFC 6455
frames through `lib/websocket_frames.py` in both directions. What is faked is the page — there is no JavaScript engine
in the suite, so `Runtime.evaluate` is answered by a stand-in that recognises the three expressions the injector sends.

So the suite holds the framing, the reconnection, the discovery rule, the watchdog's state machine, the bundle choice
and what the evaluated source carries — and it can say nothing at all about whether that source RUNS. `node --check`
parses it, which is the one mechanical check available for a language this repo does not compile here. Everything else
is a device test: whether the panel appears, whether a forced context rebuild brings it back, and whether the card draws
where a broken bundle is served.

## Related

- [Frontend bundles](frontend-bundles.md) — what the three build outputs are, and why there are two panels.
- [ADR-0036](../adr/0036-the-backend-hosts-itself.md) — the backend became its own process and serves these files.
