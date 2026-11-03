"""Unit and integration tests for ``X11FocusAdapter`` — the focused window, read from an X server over libxcb.

The Xauthority reading and the display name are pure and run everywhere. The
rest runs against an Xvfb of its own — its own display number and its own
authority file — and creates its windows through a second xcb connection of
the test's, so what the adapter reads is what an X client really set. Where
Xvfb is not installed those cases are skipped, except in CI, where a missing
Xvfb fails them: CI installs it, and a skip there would hide a broken tier.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import secrets
import shutil
import struct
import subprocess
import time
from typing import TYPE_CHECKING, Any

import pytest

from adapters import x11_focus
from adapters.x11_focus import X11FocusAdapter, cookie_for, display_number
from domain.controller_focus import FocusedWindow, XDisplay

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

COOKIE = bytes(range(16))
OTHER_COOKIE = bytes(range(16, 32))


def _entry(
    family: int, number: bytes, data: bytes, *, name: bytes = b"MIT-MAGIC-COOKIE-1", address: bytes = b""
) -> bytes:
    fields = (address, number, name, data)
    return struct.pack(">H", family) + b"".join(struct.pack(">H", len(field)) + field for field in fields)


class TestTheDisplayNumber:
    @pytest.mark.parametrize(
        ("name", "number"),
        [(":0", "0"), (":1.0", "1"), ("localhost:12", "12"), ("unix:3.1", "3")],
    )
    def test_it_is_read_off_the_name(self, name: str, number: str) -> None:
        assert display_number(name) == number

    @pytest.mark.parametrize("name", ["", "0", ":", ":x", "wayland-0"])
    def test_a_name_without_one_answers_none(self, name: str) -> None:
        assert display_number(name) is None


class TestTheCookie:
    def test_a_local_entry_for_this_display(self) -> None:
        assert cookie_for(_entry(256, b"0", COOKIE, address=b"steamdeck"), "0") == COOKIE

    def test_a_wildcard_entry_for_this_display(self) -> None:
        assert cookie_for(_entry(65535, b"0", COOKIE), "0") == COOKIE

    def test_a_wildcard_entry_for_any_display(self) -> None:
        assert cookie_for(_entry(65535, b"", COOKIE), "7") == COOKIE

    def test_an_entry_for_another_display_is_passed_over(self) -> None:
        authority = _entry(256, b"1", OTHER_COOKIE) + _entry(256, b"0", COOKIE)

        assert cookie_for(authority, "0") == COOKIE

    def test_the_first_matching_entry_wins(self) -> None:
        authority = _entry(65535, b"0", COOKIE) + _entry(256, b"0", OTHER_COOKIE)

        assert cookie_for(authority, "0") == COOKIE

    def test_another_authentication_scheme_is_passed_over(self) -> None:
        authority = _entry(256, b"0", OTHER_COOKIE, name=b"XDM-AUTHORIZATION-1") + _entry(256, b"0", COOKIE)

        assert cookie_for(authority, "0") == COOKIE

    def test_another_family_is_passed_over(self) -> None:
        # Family 0 is an IPv4 address; this backend only ever connects locally.
        assert cookie_for(_entry(0, b"0", COOKIE, address=b"\x7f\x00\x00\x01"), "0") is None

    def test_an_empty_cookie_is_passed_over(self) -> None:
        assert cookie_for(_entry(256, b"0", b"") + _entry(65535, b"0", COOKIE), "0") == COOKIE

    def test_no_entry_for_this_display(self) -> None:
        assert cookie_for(_entry(256, b"1", COOKIE), "0") is None

    def test_an_empty_file(self) -> None:
        assert cookie_for(b"", "0") is None

    def test_an_entry_cut_short_ends_the_reading(self) -> None:
        whole = _entry(256, b"0", COOKIE)

        assert cookie_for(whole[:-3], "0") is None
        assert cookie_for(_entry(256, b"1", OTHER_COOKIE) + whole[:5], "0") is None


class TestBeforeAnyConnection:
    def test_nothing_is_focused_and_nothing_is_lost(self) -> None:
        adapter = X11FocusAdapter()

        assert adapter.fileno() == -1
        assert adapter.take_focus_changes() is False
        assert adapter.focused_window() is None
        assert adapter.lost() is False
        adapter.close()

    def test_libraries_that_cannot_be_loaded_are_a_reason_not_a_raise(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(x11_focus, "_LIBXCB_RES", "libxcb-res-that-is-not-there.so.0")

        reason = X11FocusAdapter().connect(XDisplay(name=":0", authority_path=str(tmp_path / "auth")))

        assert reason is not None
        assert "could not be loaded" in reason

    def test_an_authority_file_that_cannot_be_read_is_a_reason(self, tmp_path: Path) -> None:
        reason = X11FocusAdapter().connect(XDisplay(name=":0", authority_path=str(tmp_path / "missing")))

        assert reason is not None
        assert "could not be read" in reason

    def test_an_authority_file_without_the_display_s_cookie_is_a_reason(self, tmp_path: Path) -> None:
        authority = tmp_path / "auth"
        authority.write_bytes(_entry(256, b"5", COOKIE))

        reason = X11FocusAdapter().connect(XDisplay(name=":0", authority_path=str(authority)))

        assert reason is not None
        assert "no cookie" in reason
        assert COOKIE.hex() not in reason

    def test_a_display_name_without_a_number_is_a_reason(self, tmp_path: Path) -> None:
        reason = X11FocusAdapter().connect(XDisplay(name="wayland-0", authority_path=str(tmp_path / "auth")))

        assert reason is not None
        assert "no display number" in reason


# ── Against a real X server ──────────────────────────────────────────────────


@pytest.fixture
def xvfb(tmp_path: Path) -> Iterator[tuple[XDisplay, subprocess.Popen[bytes]]]:
    """An Xvfb on a display of its own, admitting only the cookie in its own authority file."""
    binary = shutil.which("Xvfb")
    if binary is None:
        if os.environ.get("CI"):
            pytest.fail("Xvfb is not installed; CI installs it for this tier (.github/workflows/ci.yml)")
        pytest.skip("Xvfb is not installed")
    authority = tmp_path / "Xauthority"
    cookie = secrets.token_bytes(16)
    number = _free_display_number()
    authority.write_bytes(_entry(256, str(number).encode(), cookie))
    server = subprocess.Popen(
        [binary, f":{number}", "-auth", str(authority), "-nolisten", "tcp", "-screen", "0", "320x240x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_socket(number, server)
        yield XDisplay(name=f":{number}", authority_path=str(authority)), server
    finally:
        server.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            server.wait(5)
        if server.poll() is None:
            server.kill()
            server.wait(5)


def _free_display_number() -> int:
    for number in range(140, 400):
        if not os.path.exists(f"/tmp/.X11-unix/X{number}") and not os.path.exists(f"/tmp/.X{number}-lock"):
            return number
    pytest.fail("no free X display number")


def _wait_for_socket(number: int, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server.poll() is not None:
            pytest.fail(f"Xvfb exited with {server.returncode}")
        if os.path.exists(f"/tmp/.X11-unix/X{number}"):
            return
        time.sleep(0.05)
    pytest.fail("Xvfb did not come up")


class _Client:
    """The test's own X client: creates windows, sets their properties, and plays the window manager."""

    def __init__(self, display: XDisplay) -> None:
        xcb = ctypes.CDLL("libxcb.so.1")
        self._xcb = xcb
        void_p, u8, u32 = ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32
        _sign = x11_focus._sign
        _sign(xcb.xcb_connect_to_display_with_auth_info, void_p, ctypes.c_char_p, void_p, ctypes.POINTER(ctypes.c_int))
        _sign(xcb.xcb_connection_has_error, ctypes.c_int, void_p)
        _sign(xcb.xcb_disconnect, None, void_p)
        _sign(xcb.xcb_generate_id, u32, void_p)
        _sign(xcb.xcb_get_setup, void_p, void_p)
        _sign(xcb.xcb_setup_roots_iterator, x11_focus._ScreenIterator, void_p)
        _sign(
            xcb.xcb_create_window,
            x11_focus._Cookie,
            void_p,
            u8,
            u32,
            u32,
            ctypes.c_int16,
            ctypes.c_int16,
            ctypes.c_uint16,
            ctypes.c_uint16,
            ctypes.c_uint16,
            ctypes.c_uint16,
            u32,
            u32,
            void_p,
        )
        _sign(xcb.xcb_destroy_window, x11_focus._Cookie, void_p, u32)
        _sign(xcb.xcb_change_property, x11_focus._Cookie, void_p, u8, u32, u32, u32, u8, u32, void_p)
        _sign(xcb.xcb_intern_atom, x11_focus._Cookie, void_p, u8, ctypes.c_uint16, ctypes.c_char_p)
        _sign(xcb.xcb_intern_atom_reply, ctypes.POINTER(x11_focus._InternAtomReply), void_p, x11_focus._Cookie, void_p)
        _sign(xcb.xcb_get_input_focus, x11_focus._Cookie, void_p)
        _sign(xcb.xcb_get_input_focus_reply, void_p, void_p, x11_focus._Cookie, void_p)
        self._free = ctypes.CDLL(None).free
        self._free.argtypes = (void_p,)
        self._free.restype = None
        with open(display.authority_path, "rb") as f:
            cookie = cookie_for(f.read(), display_number(display.name) or "")
        assert cookie is not None
        name = ctypes.create_string_buffer(b"MIT-MAGIC-COOKIE-1", 18)
        data = ctypes.create_string_buffer(cookie, len(cookie))
        auth = x11_focus._AuthInfo(18, ctypes.addressof(name), len(cookie), ctypes.addressof(data))
        self._connection = xcb.xcb_connect_to_display_with_auth_info(display.name.encode(), ctypes.byref(auth), None)
        assert xcb.xcb_connection_has_error(self._connection) == 0
        self.root = int(xcb.xcb_setup_roots_iterator(xcb.xcb_get_setup(self._connection)).data.contents.root)

    def atom(self, name: bytes) -> int:
        reply = self._xcb.xcb_intern_atom_reply(
            self._connection, self._xcb.xcb_intern_atom(self._connection, 0, len(name), name), None
        )
        atom = int(reply.contents.atom)
        self._free(ctypes.cast(reply, ctypes.c_void_p))
        return atom

    def window(self) -> int:
        window = int(self._xcb.xcb_generate_id(self._connection))
        self._xcb.xcb_create_window(self._connection, 0, window, self.root, 0, 0, 10, 10, 0, 1, 0, 0, None)
        self.sync()
        return window

    def set_cardinal(self, window: int, name: bytes, value: int) -> None:
        self._set(window, self.atom(name), x11_focus._ATOM_CARDINAL, value)

    def focus(self, window: int) -> None:
        """Set the root's ``_NET_ACTIVE_WINDOW``, as a window manager does on a focus change."""
        self._set(self.root, self.atom(b"_NET_ACTIVE_WINDOW"), x11_focus._ATOM_WINDOW, window)

    def destroy(self, window: int) -> None:
        self._xcb.xcb_destroy_window(self._connection, window)
        self.sync()

    def _set(self, window: int, atom: int, kind: int, value: int) -> None:
        data = (ctypes.c_uint32 * 1)(value)
        self._xcb.xcb_change_property(
            self._connection, 0, window, atom, kind, 32, 1, ctypes.cast(data, ctypes.c_void_p)
        )
        self.sync()

    def sync(self) -> None:
        """Wait for the server to have handled every request sent so far."""
        reply = self._xcb.xcb_get_input_focus_reply(
            self._connection, self._xcb.xcb_get_input_focus(self._connection), None
        )
        self._free(reply)

    def close(self) -> None:
        self._xcb.xcb_disconnect(self._connection)


@pytest.fixture
def connected(xvfb: tuple[XDisplay, Any]) -> Iterator[tuple[X11FocusAdapter, _Client, subprocess.Popen[bytes]]]:
    display, server = xvfb
    adapter = X11FocusAdapter()
    client = _Client(display)
    assert adapter.connect(display) is None
    try:
        yield adapter, client, server
    finally:
        adapter.close()
        client.close()


def _wait_for_change(adapter: X11FocusAdapter) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if adapter.take_focus_changes():
            return True
        time.sleep(0.01)
    return False


class TestAgainstARealServer:
    def test_it_connects_with_the_session_s_cookie_and_hands_out_its_socket(
        self, connected: tuple[X11FocusAdapter, _Client, Any]
    ) -> None:
        adapter, _client, _server = connected

        assert adapter.fileno() >= 0
        assert adapter.lost() is False

    def test_a_wrong_cookie_is_refused(self, xvfb: tuple[XDisplay, Any], tmp_path: Path) -> None:
        display, _server = xvfb
        wrong = tmp_path / "wrong"
        wrong.write_bytes(_entry(65535, b"", secrets.token_bytes(16)))

        reason = X11FocusAdapter().connect(XDisplay(name=display.name, authority_path=str(wrong)))

        assert reason is not None
        assert "refused" in reason

    def test_a_focus_change_on_the_root_is_heard(self, connected: tuple[X11FocusAdapter, _Client, Any]) -> None:
        adapter, client, _server = connected
        window = client.window()

        client.focus(window)

        assert _wait_for_change(adapter) is True
        assert adapter.take_focus_changes() is False

    def test_another_root_property_is_no_focus_change(self, connected: tuple[X11FocusAdapter, _Client, Any]) -> None:
        adapter, client, _server = connected

        client.set_cardinal(client.root, b"_TENDER_TEST_OTHER", 1)
        client.sync()
        time.sleep(0.1)

        assert adapter.take_focus_changes() is False

    def test_no_active_window_is_no_window(self, connected: tuple[X11FocusAdapter, _Client, Any]) -> None:
        adapter, client, _server = connected

        assert adapter.focused_window() is None
        client.focus(0)
        assert adapter.focused_window() is None

    def test_big_picture_s_properties_and_the_creating_process_are_read(
        self, connected: tuple[X11FocusAdapter, _Client, Any]
    ) -> None:
        adapter, client, _server = connected
        window = client.window()
        client.set_cardinal(window, b"STEAM_GAME", 769)
        client.set_cardinal(window, b"STEAM_INPUT_FOCUS", 1)
        client.focus(window)

        assert adapter.focused_window() == FocusedWindow(host_pid=os.getpid(), steam_game=769, steam_input_focus=True)

    def test_a_window_without_them_reads_as_neither(self, connected: tuple[X11FocusAdapter, _Client, Any]) -> None:
        adapter, client, _server = connected
        window = client.window()
        client.focus(window)

        assert adapter.focused_window() == FocusedWindow(host_pid=os.getpid(), steam_game=None, steam_input_focus=False)

    def test_a_window_gone_before_the_question_is_no_window(
        self, connected: tuple[X11FocusAdapter, _Client, Any]
    ) -> None:
        adapter, client, _server = connected
        window = client.window()
        client.focus(window)
        client.destroy(window)

        assert adapter.focused_window() is None
        assert adapter.lost() is False

    def test_a_change_read_off_the_socket_with_a_reply_is_not_lost(
        self, connected: tuple[X11FocusAdapter, _Client, Any]
    ) -> None:
        adapter, client, _server = connected
        first = client.window()
        client.focus(first)
        assert _wait_for_change(adapter) is True
        second = client.window()
        client.focus(second)

        # The event for the second change arrives while the adapter waits for
        # the replies of its own questions; it must still be reported.
        adapter.focused_window()
        time.sleep(0.1)
        adapter.focused_window()

        assert _wait_for_change(adapter) is True

    def test_a_server_that_dies_leaves_the_connection_lost(
        self, connected: tuple[X11FocusAdapter, _Client, subprocess.Popen[bytes]]
    ) -> None:
        adapter, _client, server = connected
        server.kill()
        server.wait(5)

        adapter.take_focus_changes()
        adapter.focused_window()

        assert adapter.lost() is True
        assert adapter.focused_window() is None

    def test_close_ends_the_connection(self, connected: tuple[X11FocusAdapter, _Client, Any]) -> None:
        adapter, _client, _server = connected

        adapter.close()

        assert adapter.fileno() == -1
        assert adapter.lost() is False
