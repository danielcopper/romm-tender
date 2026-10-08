"""The loopback server — the port, the two routes, and what each one refuses.

Contract: the HTTP surface this backend offers and the WebSocket it upgrades to.
Owns the bind and its fallback, the static route, the upgrade handshake, and the
rule that the newest connection wins. It performs the admission checks by
calling :mod:`host.access`; it does not decide them.

**It is a hand-written server because the standard library has no asyncio HTTP
server** — ``http.server`` blocks a thread per request, and this backend is
asyncio throughout. What is written here is exactly as much HTTP as a ``GET``
and an upgrade need, and no more: no ``POST``, no body parsing, no directory
listings. Every call travels on the WebSocket.

**The directory served is handed in, never searched for.** A host that looked
for a build output relative to its own file would be the only piece of this
backend that knew the repository's layout, and it would be wrong the moment the
program was installed somewhere.

**The CORS header on the static route is load-bearing, not decoration.** The
panel bundle is fetched by a cross-origin ``import()``, which the browser makes
in CORS mode. Without ``Access-Control-Allow-Origin`` naming the asking origin
the bundle does not load slowly — it does not load at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections import OrderedDict
from typing import TYPE_CHECKING

from host.access import SESSION_PARAM, TOKEN_PARAM, AccessPolicy, check_access, new_token
from host.connection import HostConnection
from host.protocol import CLOSE_STRANDED_PANEL_RELOADS, CLOSE_STRANDED_PANEL_RESTART_STEAM, ReloadOutlook
from lib.http_messages import HEAD_TERMINATOR, MAX_HEAD_BYTES, HttpParseError, build_response_head, parse_request_head
from lib.path_safety import safe_join
from lib.websocket_frames import (
    OPCODE_CLOSE,
    WebSocketProtocolError,
    accept_key,
    close_frame,
    header_length,
    parse_frame_header,
)

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

    from host.dispatch import CallDispatcher
    from host.events import EventSink
    from lib.http_messages import RequestHead

# The port asked for first. Falling back is for a FOREIGN program holding it —
# a second copy of this backend never gets here, because the single-instance
# lock is taken before the bind is attempted.
DEFAULT_PORT = 27737
PORT_ATTEMPTS = 32

# The one path that upgrades. Everything else on this server is a file.
WS_PATH = "/ws"

# What the panel is loaded from. The host knows this name because the address it
# prints at start-up has to be complete enough to paste.
BUNDLE_FILENAME = "index.js"

_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
_DEFAULT_CONTENT_TYPE = "application/octet-stream"

# How many stranded panels' sessions are remembered for the log-once rule. Steam
# holds one panel per JS context and replaces a context only by a reload, so a
# backend meets one or two of them; a forgotten session costs one more line,
# never a flood: a panel that reads the close stops reconnecting once told, and
# one built before panels read it keeps knocking under one session, which stays
# remembered.
STRANDED_SESSIONS_REMEMBERED = 16

# How much of a stranded panel's session its log line shows. The session is the
# caller's own text, and nothing but the head limit bounds it.
STRANDED_SESSION_SHOWN = 64

# How long a stranded panel's socket is read after the close, before it is shut.
# A panel flushes its queued calls the moment the upgrade completes, and closing
# a socket over data it has not read ends the connection with a reset rather
# than in order; a client's stack may then discard what it had received but not
# yet read, the close frame included (RFC 9112 §9.6, Tear-down).
STRANDED_DRAIN_SECONDS = 1.0
_DRAIN_CHUNK = 65536

# What each close tells the panel, beside the code that carries it.
_STRANDED_ANSWERS = {
    ReloadOutlook.RELOAD_TO_COME: (
        CLOSE_STRANDED_PANEL_RELOADS,
        "stranded panel: Steam's interface reloads once no game is running",
    ),
    ReloadOutlook.NO_RELOAD: (CLOSE_STRANDED_PANEL_RESTART_STEAM, "stranded panel: restart Steam"),
}
_STRANDED_LOG_WORDING = {
    ReloadOutlook.RELOAD_TO_COME: "this backend reloads Steam's interface once no game is running",
    ReloadOutlook.NO_RELOAD: "Steam has to be restarted",
}


class HostServer:
    """Binds a loopback port and serves the panel: one file route, one upgrade."""

    def __init__(
        self,
        *,
        dispatcher: CallDispatcher,
        events: EventSink,
        static_root: str,
        logger: logging.Logger,
        server_identity: str,
        token: str | None = None,
        preferred_port: int = DEFAULT_PORT,
        port_attempts: int = PORT_ATTEMPTS,
    ) -> None:
        self._dispatcher = dispatcher
        self._events = events
        self._static_root = static_root
        self._logger = logger
        self._server_identity = server_identity
        self._token = token or new_token()
        self._preferred_port = preferred_port
        self._port_attempts = port_attempts

        self._server: asyncio.Server | None = None
        self._port = 0
        self._connection: HostConnection | None = None
        self._panel_present = asyncio.Event()
        # Summed across connections rather than read off the live one. The case
        # this counter exists for is a panel and a backend that disagree about
        # the wire, and the development loop reaches it by swapping the frontend
        # — which is a NEW connection, so a per-connection count would reset at
        # exactly the moment it had something to report.
        self._dropped_by_closed_connections = 0
        # None until the injector is wired: nothing has looked at Steam's context then.
        self._reload_outlook: Callable[[], Awaitable[ReloadOutlook]] | None = None
        self._stranded_sessions: OrderedDict[str, ReloadOutlook] = OrderedDict()

    @property
    def port(self) -> int:
        """The port actually bound; zero before :meth:`start`."""
        return self._port

    @property
    def token(self) -> str:
        """This process's admission token. Never served, never logged to file."""
        return self._token

    @property
    def bound_addresses(self) -> tuple[str, ...]:
        """The addresses this server is listening on; empty before :meth:`start`."""
        if self._server is None:
            return ()
        return tuple(sock.getsockname()[0] for sock in self._server.sockets)

    @property
    def connected(self) -> bool:
        """Is a panel connected right now?"""
        return self._connection is not None and not self._connection.closed

    async def wait_connected(self) -> None:
        """Return once a panel is connected — at once, if one already is."""
        await self._panel_present.wait()

    @property
    def dropped_messages(self) -> int:
        """How many messages this SERVER could not make sense of, across every connection."""
        live = self._connection.dropped_messages if self._connection is not None else 0
        return self._dropped_by_closed_connections + live

    def asset_url(self, filename: str) -> str:
        """The complete address *filename* is served at, token included.

        The one place an address of this server is spelled. What loads the panel
        runs in this process and asks here, so the token reaches the code that
        needs it without ever being stored anywhere.
        """
        return f"http://127.0.0.1:{self._port}/{filename}?{TOKEN_PARAM}={self._token}"

    def bundle_url(self) -> str:
        """The complete address the panel bundle is loaded from, token included."""
        return self.asset_url(BUNDLE_FILENAME)

    def answer_stranded_panels_from(self, reload_outlook: Callable[[], Awaitable[ReloadOutlook]]) -> None:
        """Tell a stranded panel, from now on, what *reload_outlook* answers.

        *reload_outlook* answers whether this backend will reload Steam's
        interface once no game is running — the recovery's own reading
        (``host.inject.recovery``). Handed in after :meth:`start`, because what
        reads it is built with the address this server answers on.

        Until then, and for as long as it answers ``NOT_YET_LOOKED``, a stranded
        panel's upgrade is refused with the plain 401. A backend that never
        reads Steam's context — its debugger never answers, or loading the panel
        is switched off — therefore refuses it with 401 for good, logged on
        every knock. That is accepted rather than answered with "restart Steam":
        a panel is stranded only in a context an earlier backend reached over
        the same debugger, so the case needs that debugger gone under a running
        Steam, or loading switched off by hand between two backend starts.
        """
        self._reload_outlook = reload_outlook

    async def start(self) -> int:
        """Bind a port and begin serving; return the port that was taken.

        Tries the preferred port first and then the ports above it. A fallback
        here means some other program holds the port — a second copy of this
        backend was refused by the lock long before it reached this line.
        """
        last_error: OSError | None = None
        for port in range(self._preferred_port, self._preferred_port + self._port_attempts):
            try:
                self._server = await asyncio.start_server(self._handle, host="127.0.0.1", port=port)
            except OSError as exc:
                last_error = exc
                continue
            self._port = port
            if port != self._preferred_port:
                self._logger.warning(f"host: port {self._preferred_port} was taken, listening on {port} instead")
            self._logger.info(f"host: listening on 127.0.0.1:{port}")
            return port

        raise OSError(
            f"no free port in {self._preferred_port}..{self._preferred_port + self._port_attempts - 1}"
        ) from last_error

    async def stop(self) -> None:
        """Stop accepting, and close every connection the server holds, upgraded or not."""
        if self._connection is not None:
            await self._connection.close(1001, "server shutting down")
            self._connection = None
            self._panel_present.clear()
        if self._server is not None:
            # A connection accepted in the previous tick is still a pending task
            # building its transport; the yield lets it attach, so close_clients()
            # reaches it. One whose accept lands in this same iteration can still
            # be abandoned half-made, and CPython 3.13 then raises one harmless
            # unraisable ``TypeError`` when the collector reaches that transport.
            await asyncio.sleep(0)
            self._server.close()
            # On 3.13 ``wait_closed`` waits for every client, so an accepted
            # connection that never sends its request head would hold it open.
            self._server.close_clients()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    # -- request handling ------------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one accepted socket: read the head, admit it, route it."""
        try:
            head = await self._read_head(reader)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError) as exc:
            self._logger.warning(f"host: unreadable request head: {exc}")
            await self._refuse(writer, 400)
            return
        except OSError:
            return

        policy = AccessPolicy(port=self._port, token=self._token)
        verdict = check_access(head, policy)
        if verdict.refused:
            if verdict.wrong_token and head.method == "GET" and head.path == WS_PATH and _handshake_key(head):
                outlook = ReloadOutlook.NOT_YET_LOOKED if self._reload_outlook is None else await self._reload_outlook()
                if outlook is not ReloadOutlook.NOT_YET_LOOKED:
                    await self._tell_stranded_panel(head, reader, writer, verdict.log_line, outlook)
                    return
            self._logger.warning(f"host: {verdict.log_line}")
            await self._refuse(writer, verdict.status)
            return

        if head.method != "GET":
            await self._refuse(writer, 405)
            return

        if head.path == WS_PATH:
            await self._upgrade(head, reader, writer)
            return

        await self._serve_file(head, writer, verdict.echo_origin)

    async def _read_head(self, reader: asyncio.StreamReader) -> RequestHead:
        """Read up to the blank line and parse what came before it."""
        raw = await reader.readuntil(HEAD_TERMINATOR)
        if len(raw) > MAX_HEAD_BYTES:
            raise HttpParseError(f"request head of {len(raw)} bytes is over the {MAX_HEAD_BYTES} limit")
        return parse_request_head(raw[: -len(HEAD_TERMINATOR)])

    async def _refuse(self, writer: asyncio.StreamWriter, status: int) -> None:
        """Answer *status* with nothing in the body, and close.

        The ``Server`` field is the whole of what a refusal answered here
        reveals: it says which program answered and nothing about why, which is
        the honest answer to a request that brought no token. The one refusal
        that says more is :meth:`_tell_stranded_panel`'s.
        """
        await self._write_response(writer, status, [("Content-Length", "0"), ("Connection", "close")])
        await self._shutdown(writer)

    async def _tell_stranded_panel(
        self,
        head: RequestHead,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        log_line: str,
        outlook: ReloadOutlook,
    ) -> None:
        """Complete the handshake of a stranded panel's upgrade, and close it at once with a code.

        A browser hides why a handshake failed: a refused upgrade reaches the
        page exactly as no server at all would (WHATWG WebSockets, "Feedback
        from the protocol"), so a 401 could not tell a panel that a backend is
        running and will never admit it. A close after a completed handshake is
        the one answer a page is shown. Nothing is attached to the socket — no
        connection, no dispatcher, no events — so completing the handshake
        authorises nothing; what the code adds to the ``Server`` field is
        whether this backend will reload Steam's interface.
        """
        self._log_stranded_panel(head.query.get(SESSION_PARAM, ""), log_line, outlook)
        code, reason = _STRANDED_ANSWERS[outlook]
        await self._write_response(
            writer,
            101,
            [
                ("Upgrade", "websocket"),
                ("Connection", "Upgrade"),
                ("Sec-WebSocket-Accept", accept_key(_handshake_key(head))),
            ],
        )
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            writer.write(close_frame(code, reason))
            await writer.drain()
        await _read_until_the_peer_closes(reader)
        await self._shutdown(writer)

    def _log_stranded_panel(self, session_id: str, log_line: str, outlook: ReloadOutlook) -> None:
        """Log a stranded panel's refusal once, and again only when what it is told changes.

        Such a panel knocks again on every re-check, and one built before panels
        read the close code knocks on every reconnection, several times a
        second, for as long as it stays; a line per knock would bury the one
        line that says what to do about it. The session is the panel's own
        identity, not a secret, and the remembering is bounded
        (:data:`STRANDED_SESSIONS_REMEMBERED`).
        """
        told_before = self._stranded_sessions.get(session_id)
        self._stranded_sessions[session_id] = outlook
        self._stranded_sessions.move_to_end(session_id)
        while len(self._stranded_sessions) > STRANDED_SESSIONS_REMEMBERED:
            self._stranded_sessions.popitem(last=False)
        if told_before == outlook:
            return
        changed = "" if told_before is None else "the answer changed: "
        self._logger.warning(
            f"host: {log_line} — the shape of a panel another backend process loaded (session "
            f"{_session_for_log(session_id)}); {changed}told it {_STRANDED_LOG_WORDING[outlook]}. Its further "
            f"refusals are logged only if that answer changes."
        )

    async def _serve_file(self, head: RequestHead, writer: asyncio.StreamWriter, echo_origin: str) -> None:
        """Serve one file from the static root, or 404.

        Containment is :func:`lib.path_safety.safe_join`, which resolves symlinks
        before comparing — so neither a ``..`` segment, an absolute path, nor a
        link planted inside the root can name a file outside it. A path that
        does not resolve to a regular file inside the root is a 404 and never a
        description of what is there.
        """
        requested = head.path.lstrip("/") or BUNDLE_FILENAME
        loop = asyncio.get_running_loop()
        try:
            found = await loop.run_in_executor(None, _read_from_root, self._static_root, requested)
        except ValueError:
            self._logger.warning(f"host: refusing a path outside the served root: {head.path!r}")
            await self._refuse(writer, 404)
            return
        except OSError as exc:
            self._logger.error(f"host: cannot read {head.path!r} under the served root: {exc}")
            await self._refuse(writer, 500)
            return

        if found is None:
            await self._refuse(writer, 404)
            return
        body, content_type = found

        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
            ("Connection", "close"),
        ]
        if echo_origin:
            # Named for the asking origin rather than ``*`` — and ``Vary`` with
            # it, because the answer genuinely differs by origin and a cache that
            # missed that would hand one origin another's header.
            headers.append(("Access-Control-Allow-Origin", echo_origin))
            headers.append(("Vary", "Origin"))

        await self._write_response(writer, 200, headers, body)
        await self._shutdown(writer)

    async def _upgrade(self, head: RequestHead, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Complete the WebSocket handshake and serve the connection.

        The newest connection always wins: an earlier one is closed here, as the
        replacement arrives. The session identity travels in the upgrade address
        rather than in a first message, which is what lets that decision be
        taken at connection time — the identity is the caller's own, one per
        bundle instance, so the log can tell a reconnect of the same panel from a
        leftover of an earlier one.
        """
        key = _handshake_key(head)
        if not key:
            upgrade = head.header("upgrade").lower()
            version = head.header("sec-websocket-version")
            self._logger.warning(f"host: not a WebSocket 13 handshake (upgrade={upgrade!r}, version={version!r})")
            await self._refuse(writer, 426)
            return

        await self._write_response(
            writer,
            101,
            [
                ("Upgrade", "websocket"),
                ("Connection", "Upgrade"),
                ("Sec-WebSocket-Accept", accept_key(key)),
            ],
        )

        session_id = head.query.get(SESSION_PARAM, "")
        previous = self._connection
        if previous is not None and not previous.closed:
            same = "the same panel reconnecting" if previous.session_id == session_id else "an older panel"
            self._logger.info(f"host: a new connection displaces {same} (session {previous.session_id!r})")
            await previous.close(1001, "displaced by a newer connection")

        connection = HostConnection(reader, writer, self._dispatcher, self._logger, session_id=session_id)
        self._connection = connection
        self._panel_present.set()
        sender = connection.send
        self._events.attach(sender)
        self._logger.info(f"host: panel connected (session {session_id!r})")
        try:
            await connection.run()
        finally:
            self._events.detach(sender)
            self._dropped_by_closed_connections += connection.dropped_messages
            if self._connection is connection:
                self._connection = None
                self._panel_present.clear()
            await self._shutdown(writer)

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        headers: list[tuple[str, str]],
        body: bytes = b"",
    ) -> None:
        """Write one response head, and a body if there is one."""
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            writer.write(build_response_head(status, [("Server", self._server_identity), *headers]))
            if body:
                writer.write(body)
            await writer.drain()

    async def _shutdown(self, writer: asyncio.StreamWriter) -> None:
        """Close one socket, tolerating a peer that closed it first."""
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            writer.close()
            await writer.wait_closed()


def _handshake_key(head: RequestHead) -> str:
    """The key of a WebSocket 13 handshake *head* makes, or ``""`` where it makes none.

    The key is answered with a digest over its ASCII bytes. A header is decoded
    latin-1, so a non-ASCII one would reach the digest intact and raise out of
    the connection callback — asyncio prints a bare traceback and the socket is
    simply left open — so it counts as no handshake.
    """
    key = head.header("sec-websocket-key")
    if head.header("upgrade").lower() != "websocket" or head.header("sec-websocket-version") != "13":
        return ""
    return key if key.isascii() else ""


def _session_for_log(session_id: str) -> str:
    """*session_id* as a log line shows it: quoted, cut at :data:`STRANDED_SESSION_SHOWN` with the cut marked."""
    if len(session_id) <= STRANDED_SESSION_SHOWN:
        return repr(session_id)
    return f"{session_id[:STRANDED_SESSION_SHOWN]!r}… ({len(session_id)} characters)"


async def _read_until_the_peer_closes(reader: asyncio.StreamReader) -> None:
    """Read and discard frames until the peer's close frame, its end of the stream, or the bound.

    The peer answers a close with a close of its own, so a browser is done in a
    round trip; :data:`STRANDED_DRAIN_SECONDS` bounds a peer that never says so.
    Payloads are discarded in chunks rather than read whole, so an announced
    length costs no memory.
    """
    with contextlib.suppress(
        TimeoutError, asyncio.IncompleteReadError, WebSocketProtocolError, ConnectionError, OSError
    ):
        async with asyncio.timeout(STRANDED_DRAIN_SECONDS):
            while True:
                first_two = await reader.readexactly(2)
                rest = header_length(first_two) - 2
                header = parse_frame_header(first_two + (await reader.readexactly(rest) if rest else b""))
                remaining = header.payload_length
                while remaining:
                    chunk = await reader.read(min(remaining, _DRAIN_CHUNK))
                    if not chunk:
                        return
                    remaining -= len(chunk)
                if header.opcode == OPCODE_CLOSE:
                    return


def _read_from_root(root: str, requested: str) -> tuple[bytes, str] | None:
    """Read *requested* from under *root*, or answer ``None`` if it is not a file there.

    Every filesystem question about a request is asked here, in one place, and
    run off the event loop — resolving a path stats it, and so does deciding
    whether it is a file. Raises :class:`lib.path_safety.PathTraversalError` when
    the request names something outside the root.
    """
    resolved = safe_join(root, requested)
    if not os.path.isfile(resolved):
        return None
    with open(resolved, "rb") as handle:
        body = handle.read()
    content_type = _CONTENT_TYPES.get(os.path.splitext(resolved)[1].lower(), _DEFAULT_CONTENT_TYPE)
    return body, content_type
