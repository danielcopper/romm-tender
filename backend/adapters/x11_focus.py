"""X11 focus adapter — concrete ``FocusedWindowReader`` over the system's libxcb.

Owns the one connection this backend makes to the desktop session's X server:
admitting itself with the session's cookie, hearing the root window's
``_NET_ACTIVE_WINDOW`` change, and reading the three facts the watcher judges a
focused window by. The libraries are reached through ``ctypes`` and loaded only
when a connection is asked for — a library missing at import would take the
whole backend down with it. Why X rather than the compositor, and why xcb
rather than Xlib, is docs/architecture/controller-focus.md.

Every method answers rather than raises: a server that goes away is
:meth:`X11FocusAdapter.lost`, a window gone between the change and the question
is no window. Calls are made from executor threads, one at a time, under one
lock, so ``close`` never frees a connection another thread is using.
"""

from __future__ import annotations

import ctypes
import struct
import threading
from typing import TYPE_CHECKING, Any

from domain.controller_focus import FocusedWindow

if TYPE_CHECKING:
    from domain.controller_focus import XDisplay

_LIBXCB = "libxcb.so.1"
# A package of its own on Debian, Ubuntu, Mint and openSUSE (``libxcb-res0``),
# which an Xfce, MATE or LXDE install may not have pulled in.
_LIBXCB_RES = "libxcb-res.so.0"

# The Xauthority entry this backend admits itself with.
_COOKIE_NAME = b"MIT-MAGIC-COOKIE-1"
_FAMILY_LOCAL = 256
_FAMILY_WILD = 65535

# Protocol constants (xproto.h, res.h).
_PROPERTY_NOTIFY = 28
_EVENT_TYPE_MASK = 0x7F
_CW_EVENT_MASK = 1 << 11
_EVENT_MASK_PROPERTY_CHANGE = 1 << 22
_ATOM_WINDOW = 33
_ATOM_CARDINAL = 6
_GET_PROPERTY_TYPE_ANY = 0
_ATOM_NONE = 0
_RES_CLIENT_ID_MASK_LOCAL_CLIENT_PID = 2

_ACTIVE_WINDOW = b"_NET_ACTIVE_WINDOW"
_STEAM_GAME = b"STEAM_GAME"
_STEAM_INPUT_FOCUS = b"STEAM_INPUT_FOCUS"


class _AuthInfo(ctypes.Structure):
    _fields_ = (
        ("namelen", ctypes.c_int),
        ("name", ctypes.c_void_p),
        ("datalen", ctypes.c_int),
        ("data", ctypes.c_void_p),
    )


class _Cookie(ctypes.Structure):
    _fields_ = (("sequence", ctypes.c_uint),)


class _Screen(ctypes.Structure):
    # Only the leading field is read; the rest of ``xcb_screen_t`` follows it.
    _fields_ = (("root", ctypes.c_uint32),)


class _ScreenIterator(ctypes.Structure):
    _fields_ = (("data", ctypes.POINTER(_Screen)), ("rem", ctypes.c_int), ("index", ctypes.c_int))


class _InternAtomReply(ctypes.Structure):
    _fields_ = (
        ("response_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("atom", ctypes.c_uint32),
    )


class _GetPropertyReply(ctypes.Structure):
    _fields_ = (
        ("response_type", ctypes.c_uint8),
        ("format", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("bytes_after", ctypes.c_uint32),
        ("value_len", ctypes.c_uint32),
        ("pad0", ctypes.c_uint8 * 12),
    )


class _PropertyNotifyEvent(ctypes.Structure):
    _fields_ = (
        ("response_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("window", ctypes.c_uint32),
        ("atom", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("state", ctypes.c_uint8),
        ("pad1", ctypes.c_uint8 * 3),
    )


class _QueryExtensionReply(ctypes.Structure):
    _fields_ = (
        ("response_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("present", ctypes.c_uint8),
        ("major_opcode", ctypes.c_uint8),
        ("first_event", ctypes.c_uint8),
        ("first_error", ctypes.c_uint8),
    )


class _Extension(ctypes.Structure):
    _fields_ = (("name", ctypes.c_char_p), ("global_id", ctypes.c_int))


class _ClientIdSpec(ctypes.Structure):
    _fields_ = (("client", ctypes.c_uint32), ("mask", ctypes.c_uint32))


class _ClientIdValue(ctypes.Structure):
    _fields_ = (("spec", _ClientIdSpec), ("length", ctypes.c_uint32))


class _ClientIdValueIterator(ctypes.Structure):
    _fields_ = (("data", ctypes.POINTER(_ClientIdValue)), ("rem", ctypes.c_int), ("index", ctypes.c_int))


def display_number(name: str) -> str | None:
    """The display number in an X display name (``:0``, ``:1.0``, ``host:2``), or ``None`` where it has none."""
    _, sep, rest = name.rpartition(":")
    number = rest.partition(".")[0]
    return number if sep and number.isdigit() else None


def cookie_for(authority: bytes, number: str) -> bytes | None:
    """The MIT-MAGIC-COOKIE-1 an Xauthority file holds for display *number*, or ``None``.

    The first entry that matches wins, as Xlib's lookup does: a local entry
    for this display number, or a wildcard entry for this number or for any.
    An entry cut short ends the reading; what came before it still counts.
    """
    offset = 0
    while offset + 2 <= len(authority):
        (family,) = struct.unpack_from(">H", authority, offset)
        offset += 2
        fields: list[bytes] = []
        for _ in range(4):
            if offset + 2 > len(authority):
                return None
            (length,) = struct.unpack_from(">H", authority, offset)
            offset += 2
            if offset + length > len(authority):
                return None
            fields.append(authority[offset : offset + length])
            offset += length
        _address, entry_number, name, data = fields
        if name != _COOKIE_NAME or not data:
            continue
        if family == _FAMILY_LOCAL and entry_number == number.encode():
            return data
        if family == _FAMILY_WILD and entry_number in (b"", number.encode()):
            return data
    return None


class _Libraries:
    """libxcb and libxcb-res with the signatures this adapter calls them with."""

    def __init__(self) -> None:
        xcb = ctypes.CDLL(_LIBXCB)
        res = ctypes.CDLL(_LIBXCB_RES)
        self.libc = ctypes.CDLL(None)
        self.xcb = xcb
        self.res = res
        self.res_id = _Extension.in_dll(res, "xcb_res_id")
        void_p, c_int, u8, u32 = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint8, ctypes.c_uint32
        error_out = ctypes.POINTER(ctypes.c_void_p)
        _sign(
            xcb.xcb_connect_to_display_with_auth_info,
            void_p,
            ctypes.c_char_p,
            ctypes.POINTER(_AuthInfo),
            ctypes.POINTER(c_int),
        )
        _sign(xcb.xcb_connection_has_error, c_int, void_p)
        _sign(xcb.xcb_disconnect, None, void_p)
        _sign(xcb.xcb_get_file_descriptor, c_int, void_p)
        _sign(xcb.xcb_flush, c_int, void_p)
        _sign(xcb.xcb_get_setup, void_p, void_p)
        _sign(xcb.xcb_setup_roots_iterator, _ScreenIterator, void_p)
        _sign(xcb.xcb_screen_next, None, ctypes.POINTER(_ScreenIterator))
        _sign(xcb.xcb_get_extension_data, ctypes.POINTER(_QueryExtensionReply), void_p, ctypes.POINTER(_Extension))
        _sign(xcb.xcb_intern_atom, _Cookie, void_p, u8, ctypes.c_uint16, ctypes.c_char_p)
        _sign(xcb.xcb_intern_atom_reply, ctypes.POINTER(_InternAtomReply), void_p, _Cookie, error_out)
        _sign(xcb.xcb_change_window_attributes_checked, _Cookie, void_p, u32, u32, ctypes.POINTER(u32))
        _sign(xcb.xcb_request_check, void_p, void_p, _Cookie)
        _sign(xcb.xcb_get_property, _Cookie, void_p, u8, u32, u32, u32, u32, u32)
        _sign(xcb.xcb_get_property_reply, ctypes.POINTER(_GetPropertyReply), void_p, _Cookie, error_out)
        _sign(xcb.xcb_get_property_value, void_p, ctypes.POINTER(_GetPropertyReply))
        _sign(xcb.xcb_get_property_value_length, c_int, ctypes.POINTER(_GetPropertyReply))
        _sign(xcb.xcb_poll_for_event, void_p, void_p)
        _sign(xcb.xcb_poll_for_queued_event, void_p, void_p)
        _sign(res.xcb_res_query_client_ids, _Cookie, void_p, u32, ctypes.POINTER(_ClientIdSpec))
        _sign(res.xcb_res_query_client_ids_reply, void_p, void_p, _Cookie, error_out)
        _sign(res.xcb_res_query_client_ids_ids_iterator, _ClientIdValueIterator, void_p)
        _sign(res.xcb_res_client_id_value_next, None, ctypes.POINTER(_ClientIdValueIterator))
        _sign(res.xcb_res_client_id_value_value, ctypes.POINTER(u32), ctypes.POINTER(_ClientIdValue))
        _sign(res.xcb_res_client_id_value_value_length, c_int, ctypes.POINTER(_ClientIdValue))
        _sign(self.libc.free, None, void_p)

    def free(self, pointer: Any) -> None:
        if pointer:
            self.libc.free(ctypes.cast(pointer, ctypes.c_void_p))


def _sign(function: Any, restype: Any, *argtypes: Any) -> None:
    function.restype = restype
    function.argtypes = argtypes


class X11FocusAdapter:
    """Real ``FocusedWindowReader``: one xcb connection to the session's X server at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._libs: _Libraries | None = None
        self._connection: int | None = None
        self._root = 0
        self._atoms: dict[bytes, int] = {}
        self._has_res = False
        self._queued_change = False

    def connect(self, display: XDisplay) -> str | None:
        """Connect to *display* and listen for focus changes; answer ``None``, or why it could not.

        Any connection open before is closed first. The reason never carries
        the cookie.
        """
        with self._lock:
            self._close_locked()
            return self._connect_locked(display)

    def fileno(self) -> int:
        """The connection's socket, readable when the server has sent something; -1 while not connected."""
        with self._lock:
            if self._connection is None or self._libs is None:
                return -1
            return int(self._libs.xcb.xcb_get_file_descriptor(self._connection))

    def take_focus_changes(self) -> bool:
        """Read every event the server has sent, and answer whether the focused window changed since the last call."""
        with self._lock:
            libs, connection = self._libs, self._connection
            if libs is None or connection is None:
                return False
            changed = self._queued_change
            self._queued_change = False
            while event := libs.xcb.xcb_poll_for_event(connection):
                changed = self._is_focus_change(event) or changed
                libs.free(event)
            return changed

    def focused_window(self) -> FocusedWindow | None:
        """The focused window as the server describes it; ``None`` where none is focused or it is already gone."""
        with self._lock:
            libs, connection = self._libs, self._connection
            if libs is None or connection is None:
                return None
            try:
                return self._focused_window_locked(libs, connection)
            finally:
                # A reply read off the socket may have brought events with it,
                # and those never make the socket readable again.
                while event := libs.xcb.xcb_poll_for_queued_event(connection):
                    self._queued_change = self._is_focus_change(event) or self._queued_change
                    libs.free(event)

    def lost(self) -> bool:
        """Whether the connection has failed; a connection never opened is not lost."""
        with self._lock:
            if self._connection is None or self._libs is None:
                return False
            return bool(self._libs.xcb.xcb_connection_has_error(self._connection))

    def close(self) -> None:
        """Close the connection, if one is open."""
        with self._lock:
            self._close_locked()

    # -- connecting ------------------------------------------------------------

    def _connect_locked(self, display: XDisplay) -> str | None:
        try:
            if self._libs is None:
                self._libs = _Libraries()
        except (OSError, AttributeError, ValueError) as exc:
            return f"the X libraries could not be loaded ({exc})"
        libs = self._libs
        number = display_number(display.name)
        if number is None:
            return f"the display name {display.name!r} names no display number"
        try:
            with open(display.authority_path, "rb") as f:
                authority = f.read()
        except OSError as exc:
            return f"the X authority file {display.authority_path} could not be read ({exc.strerror})"
        cookie = cookie_for(authority, number)
        if cookie is None:
            return f"the X authority file {display.authority_path} holds no cookie for display {display.name}"
        name_buffer = ctypes.create_string_buffer(_COOKIE_NAME, len(_COOKIE_NAME))
        data_buffer = ctypes.create_string_buffer(cookie, len(cookie))
        auth = _AuthInfo(len(_COOKIE_NAME), ctypes.addressof(name_buffer), len(cookie), ctypes.addressof(data_buffer))
        screen = ctypes.c_int(0)
        connection = libs.xcb.xcb_connect_to_display_with_auth_info(
            display.name.encode(), ctypes.byref(auth), ctypes.byref(screen)
        )
        if not connection:
            return f"the X display {display.name} could not be reached"
        error = libs.xcb.xcb_connection_has_error(connection)
        if error:
            libs.xcb.xcb_disconnect(connection)
            return f"the X display {display.name} refused the connection (xcb error {error})"
        self._connection = connection
        reason = self._listen_locked(libs, connection, screen.value)
        if reason is not None:
            self._close_locked()
        return reason

    def _listen_locked(self, libs: _Libraries, connection: int, screen: int) -> str | None:
        iterator = libs.xcb.xcb_setup_roots_iterator(libs.xcb.xcb_get_setup(connection))
        for _ in range(screen):
            libs.xcb.xcb_screen_next(ctypes.byref(iterator))
        if iterator.rem <= 0 or not iterator.data:
            return f"the X server has no screen {screen}"
        self._root = int(iterator.data.contents.root)
        extension = libs.xcb.xcb_get_extension_data(connection, ctypes.byref(libs.res_id))
        self._has_res = bool(extension) and bool(extension.contents.present)
        cookies = {
            name: libs.xcb.xcb_intern_atom(connection, 0, len(name), name)
            for name in (_ACTIVE_WINDOW, _STEAM_GAME, _STEAM_INPUT_FOCUS)
        }
        for name, cookie in cookies.items():
            reply = libs.xcb.xcb_intern_atom_reply(connection, cookie, None)
            if not reply:
                return f"the X server would not name the atom {name.decode()}"
            self._atoms[name] = int(reply.contents.atom)
            libs.free(reply)
        mask = (ctypes.c_uint32 * 1)(_EVENT_MASK_PROPERTY_CHANGE)
        cookie = libs.xcb.xcb_change_window_attributes_checked(connection, self._root, _CW_EVENT_MASK, mask)
        error = libs.xcb.xcb_request_check(connection, cookie)
        if error:
            libs.free(error)
            return "the X server would not report the root window's property changes"
        libs.xcb.xcb_flush(connection)
        return None

    def _close_locked(self) -> None:
        if self._connection is not None and self._libs is not None:
            self._libs.xcb.xcb_disconnect(self._connection)
        self._connection = None
        self._atoms = {}
        self._has_res = False
        self._queued_change = False

    # -- reading ---------------------------------------------------------------

    def _is_focus_change(self, event: int) -> bool:
        notify = ctypes.cast(event, ctypes.POINTER(_PropertyNotifyEvent)).contents
        return (
            notify.response_type & _EVENT_TYPE_MASK == _PROPERTY_NOTIFY
            and notify.window == self._root
            and notify.atom == self._atoms.get(_ACTIVE_WINDOW)
        )

    def _focused_window_locked(self, libs: _Libraries, connection: int) -> FocusedWindow | None:
        active = self._property(libs, connection, self._root, self._atoms[_ACTIVE_WINDOW], _ATOM_WINDOW)
        if active is None or active[0] != _ATOM_WINDOW or not active[1]:
            return None
        window = active[1]
        steam_game_cookie = libs.xcb.xcb_get_property(
            connection, 0, window, self._atoms[_STEAM_GAME], _ATOM_CARDINAL, 0, 1
        )
        input_focus_cookie = libs.xcb.xcb_get_property(
            connection, 0, window, self._atoms[_STEAM_INPUT_FOCUS], _GET_PROPERTY_TYPE_ANY, 0, 1
        )
        pid_cookie = None
        if self._has_res:
            spec = _ClientIdSpec(window, _RES_CLIENT_ID_MASK_LOCAL_CLIENT_PID)
            pid_cookie = libs.res.xcb_res_query_client_ids(connection, 1, ctypes.byref(spec))
        steam_game = self._property_reply(libs, connection, steam_game_cookie)
        input_focus = self._property_reply(libs, connection, input_focus_cookie)
        host_pid = self._pid_reply(libs, connection, pid_cookie) if pid_cookie is not None else None
        if steam_game is None or input_focus is None:
            return None
        return FocusedWindow(
            host_pid=host_pid,
            steam_game=steam_game[1] if steam_game[0] == _ATOM_CARDINAL else None,
            steam_input_focus=input_focus[0] != _ATOM_NONE,
        )

    def _property(self, libs: _Libraries, connection: int, window: int, atom: int, kind: int) -> tuple[int, int] | None:
        cookie = libs.xcb.xcb_get_property(connection, 0, window, atom, kind, 0, 1)
        return self._property_reply(libs, connection, cookie)

    @staticmethod
    def _property_reply(libs: _Libraries, connection: int, cookie: _Cookie) -> tuple[int, int] | None:
        """The property's type and its first 32-bit value (0 where it has none); ``None`` on an error."""
        error = ctypes.c_void_p()
        reply = libs.xcb.xcb_get_property_reply(connection, cookie, ctypes.byref(error))
        libs.free(error)
        if not reply:
            return None
        try:
            kind = int(reply.contents.type)
            value = 0
            if reply.contents.format == 32 and libs.xcb.xcb_get_property_value_length(reply) >= 4:
                value = int(ctypes.cast(libs.xcb.xcb_get_property_value(reply), ctypes.POINTER(ctypes.c_uint32))[0])
            return kind, value
        finally:
            libs.free(reply)

    @staticmethod
    def _pid_reply(libs: _Libraries, connection: int, cookie: _Cookie) -> int | None:
        error = ctypes.c_void_p()
        reply = libs.res.xcb_res_query_client_ids_reply(connection, cookie, ctypes.byref(error))
        libs.free(error)
        if not reply:
            return None
        try:
            iterator = libs.res.xcb_res_query_client_ids_ids_iterator(reply)
            while iterator.rem > 0:
                value = iterator.data
                if (
                    value.contents.spec.mask & _RES_CLIENT_ID_MASK_LOCAL_CLIENT_PID
                    and libs.res.xcb_res_client_id_value_value_length(value) >= 1
                ):
                    pid = int(libs.res.xcb_res_client_id_value_value(value)[0])
                    return pid if pid > 0 else None
                libs.res.xcb_res_client_id_value_next(ctypes.byref(iterator))
            return None
        finally:
            libs.free(reply)
