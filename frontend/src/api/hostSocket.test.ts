/**
 * The transport, against a socket this file supplies.
 *
 * **This is the only place any of `hostSocket.ts` runs.** The global stub in
 * `test-setup.ts` replaces `api/host` wholesale, so every other test in the
 * suite — all 3,600 of them — goes through a `vi.fn()` and never opens a socket,
 * never frames a call and never sees a reply. Framing, call numbering, the
 * outbox, the reconnection and the reply/error discrimination are invisible
 * everywhere else and are covered here or nowhere.
 *
 * The fake below is a socket, not a mock of this module's own calls: it records
 * what was written, and the test decides when it opens, what it delivers and
 * when it dies. A test that asserted "we called send" would pass against a
 * transport that framed nonsense.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HostSocket, HostTransportError, addressFromBundleUrl, type HostSocketConfig } from "./hostSocket";

/** A socket the test drives: it opens, delivers and dies when told to. */
class FakeSocket {
  static opened: FakeSocket[] = [];

  readyState = 0;
  readonly sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeSocket.opened.push(this);
  }

  send(frame: string): void {
    if (this.readyState !== 1) throw new Error("socket is not open");
    this.sent.push(frame);
  }

  close(): void {
    this.closed = true;
  }

  /** Complete the handshake. */
  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  /** Deliver one message from the backend. */
  deliver(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }

  /** Deliver a frame that is not a message at all. */
  deliverRaw(text: string): void {
    this.onmessage?.({ data: text } as MessageEvent);
  }

  /** The connection goes. */
  drop(): void {
    this.readyState = 3;
    this.onclose?.();
  }

  /** Every call frame it was given, decoded. */
  get calls(): { type: string; id: number; method: string; args: unknown[] }[] {
    return this.sent.map((frame) => JSON.parse(frame) as { type: string; id: number; method: string; args: unknown[] });
  }
}

/** Reconnection attempts the socket asked for, so a test can run them by hand. */
let scheduled: { run: () => void; afterMs: number }[] = [];

function build(overrides: Partial<HostSocketConfig> = {}): HostSocket {
  return new HostSocket({
    address: () => ({ origin: "http://127.0.0.1:27737", token: "tok en" }),
    open: (url) => new FakeSocket(url) as unknown as WebSocket,
    sessionId: "session-1",
    schedule: (run, afterMs) => {
      scheduled.push({ run, afterMs });
    },
    ...overrides,
  });
}

const latest = () => FakeSocket.opened[FakeSocket.opened.length - 1]!;

/** Issue a call whose answer this test does not read.
 *
 *  The rejection still has to be consumed: a dropped connection fails every call
 *  that was on the wire, and an unobserved rejection is a test-runner error even
 *  where it is exactly what the test arranged. */
const fireAndForget = (socket: HostSocket, method: string): void => {
  void socket.call(method, []).catch(() => {});
};

beforeEach(() => {
  FakeSocket.opened = [];
  scheduled = [];
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the address the bundle was loaded from", () => {
  it("carries the origin and the token", () => {
    expect(addressFromBundleUrl("http://127.0.0.1:27737/index.js?token=abc123")).toEqual({
      origin: "http://127.0.0.1:27737",
      token: "abc123",
    });
  });

  it("refuses an address with no token rather than opening a connection that cannot be admitted", () => {
    // The host checks Host, then Origin, then Token, and the token is the only
    // one that authorises. A bundle loaded without one has nothing to offer, and
    // saying so here names the cause where a 401 at the upgrade would not.
    expect(() => addressFromBundleUrl("http://127.0.0.1:27737/index.js")).toThrow(/no token/);
  });
});

describe("opening the connection", () => {
  it("upgrades on /ws, over ws, carrying the token and this bundle's session", () => {
    void build().call("get_sync_stats", []);
    // The session identity is the CALLER's, one per bundle instance, and it
    // travels in the address so the host can decide at connection time whether a
    // new connection is this panel reconnecting or a leftover of an older one.
    expect(latest().url).toBe("ws://127.0.0.1:27737/ws?token=tok%20en&session=session-1");
  });

  it("opens one socket however many calls are made before it is up", () => {
    const socket = build();
    void socket.call("a", []);
    void socket.call("b", []);
    void socket.call("c", []);
    expect(FakeSocket.opened).toHaveLength(1);
  });
});

describe("a call on the wire", () => {
  it("is framed with its method and its positional arguments", () => {
    const socket = build();
    void socket.call("download_rom", [42, true]);
    latest().open();

    expect(latest().calls).toEqual([{ type: "call", id: 1, method: "download_rom", args: [42, true] }]);
  });

  it("waits for the socket rather than failing when one is not up yet", () => {
    const socket = build();
    void socket.call("first", []);
    void socket.call("second", []);
    // Nothing is written before the handshake completes...
    expect(latest().sent).toEqual([]);
    latest().open();
    // ...and then everything is, oldest first.
    expect(latest().calls.map((call) => call.method)).toEqual(["first", "second"]);
  });

  it("is answered by the reply carrying its own id", async () => {
    const socket = build();
    const answer = socket.call("get_sync_stats", []);
    latest().open();
    // An id nobody is waiting on is ignored rather than mistaken for this one.
    latest().deliver({ type: "reply", id: 99, result: "not yours" });
    latest().deliver({ type: "reply", id: 1, result: { total: 7 } });

    await expect(answer).resolves.toEqual({ total: 7 });
  });

  it("fails with a transport error that cannot be read as a callable's own failure", async () => {
    const socket = build();
    const answer = socket.call("nope", []);
    latest().open();
    latest().deliver({ type: "error", id: 1, reason: "method_unknown", message: "no such method", traceback: "…" });

    // A callable's own failure is a successful transport and arrives inside
    // `result` as `{success, reason, message}`. This is the other thing, and it
    // is thrown so it cannot reach a reader of that shape.
    const error = await answer.catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HostTransportError);
    expect(error).toMatchObject({ reason: "method_unknown", message: "no such method", traceback: "…" });
  });

  it("carries no traceback when the failure had none", async () => {
    const socket = build();
    const answer = socket.call("nope", []);
    latest().open();
    latest().deliver({ type: "error", id: 1, reason: "method_unknown", message: "no such method" });

    await expect(answer).rejects.toMatchObject({ traceback: undefined });
  });

  it("takes reason and message only where the wire carried a string", async () => {
    const socket = build();
    const answer = socket.call("nope", []);
    latest().open();
    latest().deliver({ type: "error", id: 1, reason: { code: 7 }, message: ["a", "b"] });

    // Stringifying whatever arrived would put `[object Object]` into the reason
    // a caller matches on. The empty string is nothing `protocol.py` sends, so
    // it cannot be read as a reason the backend named.
    await expect(answer).rejects.toMatchObject({ reason: "", message: "" });
  });
});

describe("when the connection goes", () => {
  it("fails the calls that were already on the wire, and says why", async () => {
    const socket = build();
    const answer = socket.call("get_sync_stats", []);
    latest().open();
    expect(socket.inFlight).toBe(1);
    latest().drop();

    // `connection_lost` is the one reason no backend ever sends — it is what the
    // caller's own register answers with, and it is named in
    // `backend/host/protocol.py` so the two ends cannot spell it differently.
    await expect(answer).rejects.toMatchObject({ reason: "connection_lost" });
  });

  it("does NOT fail a call that never left, and sends it on the next connection", async () => {
    const socket = build();
    const answer = socket.call("get_sync_stats", []);
    // Dropped before the handshake ever completed, so the frame is still queued.
    latest().drop();

    let settled = false;
    void answer.then(
      () => (settled = true),
      () => (settled = true),
    );
    await Promise.resolve();
    // Re-sending a call that reached the backend could repeat whatever it did;
    // re-sending one that never left cannot. That is the whole distinction.
    expect(settled).toBe(false);

    scheduled[0]!.run();
    latest().open();
    expect(latest().calls.map((call) => call.method)).toEqual(["get_sync_stats"]);

    latest().deliver({ type: "reply", id: 1, result: "late but fine" });
    await expect(answer).resolves.toBe("late but fine");
  });

  it("keeps the call numbers going across the reconnection", () => {
    const socket = build();
    fireAndForget(socket, "first");
    latest().open();
    latest().drop();
    scheduled[0]!.run();
    void socket.call("second", []);
    latest().open();

    // The number lives in the bundle, not on the connection, which is what lets
    // a log on either side follow one call through a socket that went.
    expect(latest().calls.map((call) => call.id)).toEqual([2]);
  });

  it("backs off, and stops trying once the panel closes for good", () => {
    const socket = build();
    fireAndForget(socket, "x");
    latest().open();

    latest().drop();
    expect(scheduled.map((entry) => entry.afterMs)).toEqual([250]);
    scheduled[0]!.run();
    latest().drop();
    expect(scheduled.map((entry) => entry.afterMs)).toEqual([250, 500]);

    socket.close();
    scheduled[1]!.run();
    // Two sockets were opened, by the two attempts above; the close means no third.
    expect(FakeSocket.opened).toHaveLength(2);
  });

  it("starts the backoff over once a connection succeeds", () => {
    const socket = build();
    fireAndForget(socket, "x");
    latest().open();
    latest().drop();
    scheduled[0]!.run();
    latest().open();
    latest().drop();

    // Without the reset, a session that reconnected fine an hour ago would wait
    // five seconds for its next blip.
    expect(scheduled.map((entry) => entry.afterMs)).toEqual([250, 250]);
  });

  it("fails everything in flight when the panel closes", async () => {
    const socket = build();
    const answer = socket.call("x", []);
    latest().open();
    socket.close();

    await expect(answer).rejects.toMatchObject({ reason: "connection_lost" });
    expect(latest().closed).toBe(true);
  });
});

describe("events arriving from the backend", () => {
  it("reach every listener for their name, with the one payload the wire carries", () => {
    const socket = build();
    const heard: unknown[] = [];
    socket.on("sync_progress", (payload: never) => heard.push(payload));
    socket.on("sync_progress", (payload: never) => heard.push(payload));
    socket.on("download_complete", (payload: never) => heard.push(payload));
    latest().open();

    latest().deliver({ type: "event", name: "sync_progress", payload: { running: true } });

    // `backend/host/events.py` refuses more than one argument, so one payload is
    // the whole of what a listener can be handed.
    expect(heard).toEqual([{ running: true }, { running: true }]);
  });

  it("stop reaching a listener that was removed", () => {
    const socket = build();
    const heard: unknown[] = [];
    const listener = (payload: never) => heard.push(payload);
    socket.on("sync_progress", listener);
    socket.off("sync_progress", listener);
    latest().open();

    latest().deliver({ type: "event", name: "sync_progress", payload: 1 });
    expect(heard).toEqual([]);
  });

  it("reach the listeners registered when the frame arrived, even ones removed part-way through", () => {
    const socket = build();
    const heard: string[] = [];
    const second = () => heard.push("second");
    const first = () => {
      heard.push("first");
      socket.off("sync_progress", first);
      socket.off("sync_progress", second);
    };
    socket.on("sync_progress", first);
    socket.on("sync_progress", second);
    latest().open();

    latest().deliver({ type: "event", name: "sync_progress", payload: 1 });

    // A Set iterator walks the LIVE set, so an entry deleted before it is
    // reached is skipped in silence — no throw, `second` simply never runs.
    // Dispatching over a snapshot is what stops one listener's teardown from
    // taking the frame away from a peer that was registered when it arrived.
    expect(heard).toEqual(["first", "second"]);

    latest().deliver({ type: "event", name: "sync_progress", payload: 2 });
    // The removals did take effect — for the NEXT frame, not the one they ran in.
    expect(heard).toEqual(["first", "second"]);
  });

  it("reach nobody when the frame names no event", () => {
    const socket = build();
    const heard: unknown[] = [];
    socket.on("sync_progress", (payload: never) => heard.push(payload));
    // Registered under "" as well, so what is asserted is that a nameless frame
    // is DROPPED rather than folded onto the empty-string event. Without this
    // listener the frame would fall through to a bucket that merely does not
    // exist, and the test would pass either way.
    socket.on("", (payload: never) => heard.push(payload));
    latest().open();

    latest().deliver({ type: "event", payload: 1 });
    latest().deliver({ type: "event", name: { not: "a name" }, payload: 2 });

    expect(heard).toEqual([]);
  });

  it("tolerates removing a listener that was never registered", () => {
    const socket = build();
    expect(() => socket.off("never_subscribed", () => {})).not.toThrow();
  });

  it("still reach the other listeners when one of them throws", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const socket = build();
    const heard: unknown[] = [];
    socket.on("sync_progress", () => {
      throw new Error("boom");
    });
    socket.on("sync_progress", (payload: never) => heard.push(payload));
    latest().open();

    latest().deliver({ type: "event", name: "sync_progress", payload: "second still runs" });

    // One listener's throw must not cost the others their event, and the socket
    // has to keep reading either way.
    expect(heard).toEqual(["second still runs"]);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("sync_progress"), expect.any(Error));
  });

  it("open the connection even when nothing has been called yet", () => {
    // A panel that only listens still needs a socket; without this a run started
    // from elsewhere would report nothing until the first callable happened.
    build().on("sync_progress", () => {});
    expect(FakeSocket.opened).toHaveLength(1);
  });
});

describe("an address no connection can be formed from", () => {
  const noAddress = () => {
    throw new Error("the panel bundle was loaded with no token");
  };

  it("fails the calls already queued, and says what is wrong", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const socket = build({ address: noAddress });

    await expect(socket.call("get_sync_stats", [])).rejects.toThrow(/no token/);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("cannot reach its backend"), expect.any(Error));
  });

  it("fails every LATER call too, rather than leaving it pending for the session", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const socket = build({ address: noAddress });
    await expect(socket.call("first", [])).rejects.toThrow(/no token/);

    // There is no timeout here by design and no retry is scheduled for a fault
    // no retry can fix, so a queued call would stay pending until the panel
    // reloaded — which reads to a user as a backend that never answers.
    await expect(socket.call("second", [])).rejects.toThrow(/no token/);
  });

  it("schedules no reconnection, because no retry can change the address", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const socket = build({ address: noAddress });
    void socket.call("x", []).catch(() => {});

    expect(scheduled).toEqual([]);
    expect(FakeSocket.opened).toEqual([]);
  });
});

describe("a frame the panel cannot make sense of", () => {
  it("is ignored rather than taken down the connection", async () => {
    const socket = build();
    const answer = socket.call("x", []);
    latest().open();

    latest().deliverRaw("not json at all");
    latest().deliverRaw("[1, 2, 3]");
    latest().deliver({ type: "something_new", id: 1 });
    latest().deliver({ type: "reply", id: "not a number", result: 1 });

    // None of the above settles the call, and the socket is still live enough to
    // deliver the real answer.
    latest().deliver({ type: "reply", id: 1, result: "fine" });
    await expect(answer).resolves.toBe("fine");
  });
});
