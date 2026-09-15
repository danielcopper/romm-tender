"""The file route: it serves that directory, and provably nothing else.

Every case here goes through a real socket with a path this test wrote by hand,
because the interesting inputs are exactly the ones a helper would have
normalised away before the server saw them.
"""

from __future__ import annotations

import os

import pytest

from tests.host.conftest import SERVER_IDENTITY
from tests.host.ws_client import http_get


class TestServingTheBundle:
    async def test_it_serves_the_bundle(self, running_host):
        status, headers, body = await http_get(running_host.port, "/index.js", token=running_host.token)

        assert status == 200
        assert body == b"export const panel = 1;\n"
        assert headers["content-type"].startswith("text/javascript")

    async def test_the_root_path_answers_with_the_bundle(self, running_host):
        status, _, body = await http_get(running_host.port, "/", token=running_host.token)

        assert (status, body) == (200, b"export const panel = 1;\n")

    async def test_a_served_file_is_never_cached(self, running_host):
        _, headers, _ = await http_get(running_host.port, "/index.js", token=running_host.token)

        assert headers["cache-control"] == "no-cache"

    async def test_every_answer_names_the_program(self, running_host):
        _, headers, _ = await http_get(running_host.port, "/index.js", token=running_host.token)

        assert headers["server"] == SERVER_IDENTITY

    async def test_a_second_file_in_the_root_is_served_too(self, running_host):
        """The root is what is served — the fallback page lives there as well."""
        with open(os.path.join(running_host.static_root, "fallback.html"), "w", encoding="utf-8") as handle:
            handle.write("<p>no panel</p>")

        status, headers, body = await http_get(running_host.port, "/fallback.html", token=running_host.token)

        assert (status, body) == (200, b"<p>no panel</p>")
        assert headers["content-type"].startswith("text/html")

    async def test_only_get_is_answered(self, running_host):
        status, _, _ = await http_get(running_host.port, "/index.js", token=running_host.token, method="POST")

        assert status == 405


class TestItServesNothingOutsideTheRoot:
    """Each of these must be a 404 with no byte of the named file in the body."""

    @pytest.mark.parametrize(
        "path",
        [
            "/../secret.txt",
            "/../../secret.txt",
            "/subdir/../../secret.txt",
            "/%2e%2e/secret.txt",
            "/%2E%2E%2Fsecret.txt",
            "/..%2fsecret.txt",
            "//etc/passwd",
            "/etc/passwd",
        ],
    )
    async def test_a_path_that_climbs_out_is_refused(self, running_host, tmp_path, path):
        (tmp_path / "secret.txt").write_text("the file above the root", encoding="utf-8")

        status, _, body = await http_get(running_host.port, path, token=running_host.token)

        assert status == 404
        assert b"the file above the root" not in body

    async def test_a_symlink_pointing_out_of_the_root_is_refused(self, running_host, tmp_path):
        """``safe_join`` resolves links before comparing, so the target decides."""
        (tmp_path / "secret.txt").write_text("the file above the root", encoding="utf-8")
        os.symlink(tmp_path / "secret.txt", os.path.join(running_host.static_root, "escape.txt"))

        status, _, body = await http_get(running_host.port, "/escape.txt", token=running_host.token)

        assert status == 404
        assert b"the file above the root" not in body

    async def test_a_symlink_staying_inside_the_root_is_served(self, running_host):
        """The link is not the problem — where it points is."""
        os.symlink(
            os.path.join(running_host.static_root, "index.js"),
            os.path.join(running_host.static_root, "alias.js"),
        )

        status, _, body = await http_get(running_host.port, "/alias.js", token=running_host.token)

        assert (status, body) == (200, b"export const panel = 1;\n")

    async def test_a_directory_is_not_a_file(self, running_host):
        os.mkdir(os.path.join(running_host.static_root, "assets"))

        status, _, body = await http_get(running_host.port, "/assets", token=running_host.token)

        assert status == 404
        assert body == b""

    async def test_there_are_no_directory_listings(self, running_host):
        os.mkdir(os.path.join(running_host.static_root, "assets"))

        status, _, body = await http_get(running_host.port, "/assets/", token=running_host.token)

        assert status == 404
        assert b"index.js" not in body

    async def test_a_name_with_a_nul_byte_is_refused(self, running_host):
        status, _, _ = await http_get(running_host.port, "/index.js\x00.txt", token=running_host.token)

        assert status == 404

    async def test_a_file_that_is_simply_absent_is_a_404(self, running_host):
        status, _, _ = await http_get(running_host.port, "/nothing-here.js", token=running_host.token)

        assert status == 404


class TestCors:
    async def test_an_allowed_origin_is_named_back(self, running_host):
        """Without this header the cross-origin import does not load at all."""
        status, headers, _ = await http_get(
            running_host.port,
            "/index.js",
            token=running_host.token,
            origin="https://steamloopback.host",
        )

        assert status == 200
        assert headers["access-control-allow-origin"] == "https://steamloopback.host"
        assert headers["vary"] == "Origin"

    @pytest.mark.parametrize("spelling", ["127.0.0.1", "localhost"])
    async def test_both_spellings_of_our_own_address_are_named_back(self, running_host, spelling):
        """A browser counts the two as different origins even though they are one address."""
        origin = f"http://{spelling}:{running_host.port}"

        status, headers, _ = await http_get(running_host.port, "/index.js", token=running_host.token, origin=origin)

        assert status == 200
        assert headers["access-control-allow-origin"] == origin

    async def test_a_request_with_no_origin_gets_no_cors_header(self, running_host):
        status, headers, _ = await http_get(running_host.port, "/index.js", token=running_host.token, origin=None)

        assert status == 200
        assert "access-control-allow-origin" not in headers
