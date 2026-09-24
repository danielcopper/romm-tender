/**
 * The panel's end of the WebSocket the backend hosts.
 *
 * One connection per bundle instance, carrying every call out and every event
 * in. The wire is defined once, on the other side, in `backend/host/protocol.py`
 * — four message kinds, each naming itself in `type`:
 *
 *     call   {type, id, method, args}
 *     reply  {type, id, result}
 *     error  {type, id, reason, message, traceback?}
 *     event  {type, name, payload}
 *
 * **A transport failure is not a callable's failure.** `error.reason` names
 * something that went wrong CARRYING a call — the method does not exist, the
 * answer is too large, the method raised. A callable's own failure is a
 * perfectly successful transport and arrives inside `result`, in the
 * `{success, reason, message}` shape the backend's own gate guards. Reading one
 * as the other shows a user a sentence about their game where a programming
 * error stands. So a transport failure is thrown, as `HostTransportError`, and
 * cannot arrive anywhere a `{success, ...}` answer is read.
 *
 * **There is no timeout, deliberately.** A call issued while the socket is down
 * waits for it to come back rather than failing, and `index.tsx` races its own
 * deadline around the calls that must not wait. Adding one here would change
 * what every call does when the socket is down, at once and silently.
 */

/** The reason a call fails when the socket goes before its reply comes back.
 *
 *  The vocabulary of `error.reason` is owned by `backend/host/protocol.py`, and
 *  this is the one value no backend ever sends: it is what a caller's own
 *  pending register answers with. It is named there too, for exactly this reason
 *  — so that the two ends cannot invent two spellings of it. Nothing mechanical
 *  holds the two files together; this is the only place the frontend spells it. */
const CONNECTION_LOST = "connection_lost";

/** A failure of the carriage, never of the thing carried. */
export class HostTransportError extends Error {
  /** One of `backend/host/protocol.py`'s `TRANSPORT_REASONS`. */
  readonly reason: string;
  /** Present only when the backend raised — a refused method name has no stack. */
  readonly traceback: string | undefined;

  constructor(reason: string, message: string, traceback?: string) {
    super(message);
    this.name = "HostTransportError";
    this.reason = reason;
    this.traceback = traceback;
  }
}

/** Where the backend is, and the credential to reach it with. */
export interface HostAddress {
  origin: string;
  token: string;
}

/**
 * Read the host's address off the URL this bundle was loaded from.
 *
 * The host mints exactly that URL — `http://127.0.0.1:<port>/index.js?token=…`
 * (`HostServer.bundle_url`) — and whoever injects the bundle loads it from
 * there, so the port and the token arrive with the code that needs them and
 * cannot be stale: the token is minted per process start, and a bundle loaded
 * from an address carries that start's token by construction.
 *
 * The alternative was a global the injector sets before evaluating this. It was
 * rejected because it invents a contract with a piece of work that does not
 * exist yet (#1900), to carry two values the bundle is already holding.
 *
 * Read on first connect rather than at module scope: importing this module then
 * costs nothing and cannot throw, which is what lets a test import it without a
 * URL that parses.
 */
export function addressFromBundleUrl(bundleUrl: string): HostAddress {
  const url = new URL(bundleUrl);
  const token = url.searchParams.get("token");
  if (!token) {
    throw new Error(
      `the panel bundle was loaded from ${url.origin}${url.pathname} with no token, so it cannot open a connection`,
    );
  }
  return { origin: url.origin, token };
}

/** A listener registered for one event name.
 *
 *  `never` rather than `unknown`: the parameters of a function type are
 *  contravariant, so this is the shape every concrete listener is assignable TO,
 *  which is what lets the registry hold listeners of different payload types
 *  without a cast through `unknown` at each call. */
export type Listener = (payload: never) => unknown;

interface PendingCall {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
}

/** How long to wait before each reconnection attempt, in order; the last repeats. */
const RECONNECT_DELAYS_MS = [250, 500, 1000, 2000, 5000];

/** What the socket needs from its surroundings, so a test can supply all of it. */
export interface HostSocketConfig {
  /** The address, read when the first connection is opened. */
  address: () => HostAddress;
  /** Opens one socket. */
  open: (url: string) => WebSocket;
  /** This bundle instance's identity, one per instance. */
  sessionId: string;
  /** Schedules a reconnection attempt. */
  schedule?: (run: () => void, afterMs: number) => void;
}

export class HostSocket {
  private readonly config: HostSocketConfig;
  private readonly pending = new Map<number, PendingCall>();
  private readonly listeners = new Map<string, Set<Listener>>();
  /** Frames written but not yet handed to a socket, oldest first. */
  private outbox: { id: number; frame: string }[] = [];
  private socket: WebSocket | null = null;
  private attempt = 0;
  private lastCallId = 0;
  private closed = false;
  /** Set when no connection can ever be formed, so every later call says so. */
  private unreachable: Error | null = null;

  constructor(config: HostSocketConfig) {
    this.config = config;
  }

  /** How many calls are on the wire, awaiting a reply. Test-facing. */
  get inFlight(): number {
    return this.pending.size - this.outbox.length;
  }

  /**
   * Put one call on the wire and answer with its result.
   *
   * The call number is monotonic **per bundle instance** and carries on across a
   * reconnection, because it lives here rather than on the connection. That is
   * what lets a log on either side follow one call through a socket that went.
   */
  call(method: string, args: readonly unknown[]): Promise<unknown> {
    // A bundle served without a token can never reach a backend, so a call made
    // after that was discovered is answered rather than queued. Queuing it would
    // leave it pending for the life of the session: there is no timeout here by
    // design, and no reconnection is scheduled for a fault no retry can fix.
    if (this.unreachable) return Promise.reject(this.unreachable);
    const id = ++this.lastCallId;
    const frame = JSON.stringify({ type: "call", id, method, args });
    const answer = new Promise<unknown>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.outbox.push({ id, frame });
    this.flush();
    return answer;
  }

  /** Register *listener* for the event *name*. */
  on(name: string, listener: Listener): void {
    let bucket = this.listeners.get(name);
    if (!bucket) {
      bucket = new Set();
      this.listeners.set(name, bucket);
    }
    bucket.add(listener);
    this.connect();
  }

  /** Drop *listener* from *name*. Silent when it was never registered. */
  off(name: string, listener: Listener): void {
    const bucket = this.listeners.get(name);
    if (!bucket) return;
    bucket.delete(listener);
    if (bucket.size === 0) this.listeners.delete(name);
  }

  /** Stop for good: no reconnection, and every call in flight fails. */
  close(): void {
    this.closed = true;
    this.socket?.close();
    this.socket = null;
    this.failSentCalls("the panel closed its connection");
  }

  // -- the connection ---------------------------------------------------------

  private connect(): void {
    if (this.closed || this.socket) return;

    let url: string;
    try {
      const { origin, token } = this.config.address();
      url = `${origin.replace(/^http/, "ws")}/ws?token=${encodeURIComponent(token)}&session=${encodeURIComponent(this.config.sessionId)}`;
    } catch (error) {
      // The address is read off the URL this bundle was loaded from, so a
      // failure here is not a connection that went — it is a bundle that was
      // served without a token, and no retry can change that. Caught rather than
      // thrown because `connect` is reached from `call`, and a synchronous throw
      // there would arrive at 150 call sites that are written to `await` and
      // `.catch()`, past every one of their handlers.
      this.closed = true;
      this.unreachable = error instanceof Error ? error : new Error(String(error));
      this.failEveryCall(this.unreachable);
      console.warn("[Tender] the panel cannot reach its backend", this.unreachable);
      return;
    }

    const socket = this.config.open(url);
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.flush();
    };
    socket.onmessage = (event: MessageEvent) => this.receive(String(event.data));
    socket.onclose = () => this.handleClose(socket);
    // An error is always followed by a close, so the reconnection is driven from
    // one place rather than two that could both fire.
    socket.onerror = () => {};
  }

  private handleClose(socket: WebSocket): void {
    if (this.socket !== socket) return;
    this.socket = null;
    // Only the calls that actually reached the wire fail. A frame still in the
    // outbox never left, so re-sending it on the next connection is safe — while
    // one already sent may have RUN, and silently retrying it would repeat
    // whatever it did.
    this.failSentCalls("the connection to the backend went away");
    if (this.closed) return;
    const delay = RECONNECT_DELAYS_MS[Math.min(this.attempt, RECONNECT_DELAYS_MS.length - 1)]!;
    this.attempt += 1;
    const schedule = this.config.schedule ?? ((run, afterMs) => setTimeout(run, afterMs));
    schedule(() => this.connect(), delay);
  }

  /** Fail every call there is, sent or not, with *error*. */
  private failEveryCall(error: Error): void {
    this.outbox = [];
    for (const [id, call] of this.pending) {
      this.pending.delete(id);
      call.reject(error);
    }
  }

  private failSentCalls(message: string): void {
    const unsent = new Set(this.outbox.map((entry) => entry.id));
    for (const [id, call] of this.pending) {
      if (unsent.has(id)) continue;
      this.pending.delete(id);
      call.reject(new HostTransportError(CONNECTION_LOST, message));
    }
  }

  private flush(): void {
    if (this.socket?.readyState !== 1) {
      this.connect();
      return;
    }
    // Spliced entry by entry rather than cleared after the loop: `send` can
    // throw on a socket that closed between the readyState read and the write,
    // and the frames after it must stay queued rather than be dropped.
    while (this.outbox.length > 0) {
      const next = this.outbox[0]!;
      try {
        this.socket.send(next.frame);
      } catch {
        return;
      }
      this.outbox.shift();
    }
  }

  // -- incoming ---------------------------------------------------------------

  private receive(text: string): void {
    let message: Record<string, unknown>;
    try {
      const decoded: unknown = JSON.parse(text);
      if (typeof decoded !== "object" || decoded === null) return;
      message = decoded as Record<string, unknown>;
    } catch {
      return;
    }

    switch (message.type) {
      case "reply":
        this.settle(message.id, (call) => call.resolve(message.result));
        return;
      case "error":
        // The empty string rather than a stand-in of our own: it is nothing
        // `protocol.py` sends, so a frame that carried no usable reason cannot
        // be read as one the backend named. Stringifying what arrived instead
        // would put `[object Object]` into the reason a caller matches on.
        this.settle(message.id, (call) =>
          call.reject(
            new HostTransportError(
              typeof message.reason === "string" ? message.reason : "",
              typeof message.message === "string" ? message.message : "",
              typeof message.traceback === "string" ? message.traceback : undefined,
            ),
          ),
        );
        return;
      case "event":
        // Not folded onto "": a frame carrying no usable name is dropped, like
        // any other frame the panel cannot make sense of.
        if (typeof message.name === "string") this.dispatch(message.name, message.payload);
        return;
      default:
        return;
    }
  }

  private settle(id: unknown, finish: (call: PendingCall) => void): void {
    if (typeof id !== "number") return;
    const call = this.pending.get(id);
    if (!call) return;
    this.pending.delete(id);
    finish(call);
  }

  private dispatch(name: string, payload: unknown): void {
    const bucket = this.listeners.get(name);
    if (!bucket) return;
    // A snapshot, because a listener may remove ANOTHER listener while it runs
    // — and a live walk would then skip that one, silently, with no throw to
    // catch. Removing only itself is harmless: the walk is already standing on
    // it and the peers still run.
    const registered = [...bucket];
    for (const listener of registered) {
      try {
        (listener as (payload: unknown) => unknown)(payload);
      } catch (error) {
        // One listener's throw must not cost the others their event, and the
        // socket must keep reading either way.
        console.warn(`[Tender] a listener for "${name}" threw`, error);
      }
    }
  }
}
