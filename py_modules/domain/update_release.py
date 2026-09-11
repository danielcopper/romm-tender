"""How this plugin's own releases are named, and what the last update check saw.

Everything pure about a Tender release lives here: the spelling a release tag
uses, the spelling GitHub gives an asset digest, the fixed address a release is
downloaded from, and the encode/decode of the stamp one completed check leaves
behind. Talking to GitHub stays in the adapter, and reading the stamp out of
``kv_config`` stays in the service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Releases are tagged ``tender-v<version>`` — release-please's configured tag
# shape, which ``.github/workflows/release.yml`` builds from. Everything
# downstream — the comparison against the running version, the version a user
# dismisses — speaks the bare version, so the prefix is peeled off exactly here.
_TAG_PREFIX = "tender-v"

# GitHub reports an asset digest as ``<algorithm>:<hex>``. Decky compares a
# checksum it is handed against ``sha256(zip).hexdigest()``, so the bare hex is
# what may leave this module — and only for that one algorithm: an ``sha512:``
# digest passed on as if it were sha256 would fail Decky's check with nothing
# saying why.
_SHA256_PREFIX = "sha256:"

# The address a user is SHOWN — the one the install instructions name and a
# reader can type by hand. GitHub keeps this ``releases/latest/download/`` form
# pointed at whatever is newest, so it needs no API answer to build and never
# goes stale.
#
# It is deliberately NOT what an install is handed; that is
# ``LatestRelease.install_url``, which names one release. Swapping the two is
# the failure this pair exists to prevent: a checksum belongs to the release it
# was read from, and this address starts resolving to a newer one the moment the
# next release lands — Decky compares ``sha256(zip).hexdigest()`` against what it
# was given and refuses to unpack on a mismatch.
DOWNLOAD_URL = "https://github.com/danielcopper/romm-tender/releases/latest/download/Tender.zip"


@dataclass(frozen=True)
class LatestRelease:
    """The release GitHub calls latest, in this plugin's own vocabulary.

    ``version`` is the bare version the tag named.

    ``install_url`` and ``digest`` are a PAIR and are only ever true together:
    the address names one release and the checksum was read from that same
    release's asset. They are what an install is handed. ``install_url`` is
    ``""`` and ``digest`` ``None`` where the release carried no asset this
    plugin recognises, or the asset carried no such field — an install can still
    proceed from :data:`DOWNLOAD_URL` unverified, which is the one place the two
    addresses may be mixed and only because no checksum travels with it.
    """

    version: str
    digest: str | None
    install_url: str


@dataclass(frozen=True)
class UpdateCheck:
    """What one check saw, and when it ran.

    ``checked_at`` is a Unix timestamp and is what the once-a-day throttle
    reads; it stamps the ATTEMPT, so a check that reached nothing still holds
    the next one off. ``version``, ``digest`` and ``install_url`` are the newest
    answer that ever arrived, which is why a failed attempt carries the previous
    ones forward rather than clearing them — and carries all three together,
    because the address and the checksum describe one release and a mixed pair
    would fail Decky's unpack with nothing saying why.
    """

    checked_at: float
    version: str | None
    digest: str | None
    install_url: str


def version_from_tag(tag: object) -> str | None:
    """Return the bare version *tag* names, or ``None`` when it names none.

    Accepts the tag as GitHub reports it (``"tender-v0.33.0"``) and as the bare
    version (``"0.33.0"``), so a tag shape that loses the prefix does not
    silently stop every comparison downstream. Surrounding whitespace is
    tolerated; anything that is not a non-empty string after that — a number, an
    object, ``None``, the prefix alone — is ``None``, the answer that keeps the
    caller silent.
    """
    if not isinstance(tag, str):
        return None
    stripped = tag.strip()
    if stripped.startswith(_TAG_PREFIX):
        stripped = stripped[len(_TAG_PREFIX) :]
    return stripped or None


def sha256_hex(digest: object) -> str | None:
    """Return the bare sha256 hex of *digest*, or ``None`` when it is not one.

    GitHub's ``sha256:<hex>`` form is the only one accepted: a digest under
    another algorithm, or an unprefixed value whose algorithm nothing states,
    answers ``None`` rather than travelling on as a sha256 nobody established.
    """
    if not isinstance(digest, str):
        return None
    stripped = digest.strip()
    if not stripped.startswith(_SHA256_PREFIX):
        return None
    return stripped[len(_SHA256_PREFIX) :] or None


def encode_update_check(check: UpdateCheck) -> str:
    """Render *check* as the JSON text the ``kv_config`` row holds."""
    return json.dumps(
        {
            "checked_at": check.checked_at,
            "version": check.version,
            "digest": check.digest,
            "install_url": check.install_url,
        }
    )


def decode_update_check(raw: str | None) -> UpdateCheck | None:
    """Decode the stored stamp, or ``None`` when there is no usable one.

    ``None`` means "no check has completed", which is what the caller acts on,
    so every unusable value — absent, empty, not JSON, not an object, or
    carrying no numeric ``checked_at`` — collapses onto it and the next call
    simply checks again. A stored ``version`` / ``digest`` / ``install_url``
    that is not a non-empty string is dropped on its own: the timestamp is still
    a true statement about when we last asked. A stamp written before
    ``install_url`` existed decodes with it empty, which reads as "no
    version-bound address known" and sends the reader to the fixed one.
    """
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    checked_at = decoded.get("checked_at")
    if isinstance(checked_at, bool) or not isinstance(checked_at, (int, float)):
        return None
    version = decoded.get("version")
    digest = decoded.get("digest")
    install_url = decoded.get("install_url")
    return UpdateCheck(
        checked_at=float(checked_at),
        version=version if isinstance(version, str) and version else None,
        digest=digest if isinstance(digest, str) and digest else None,
        install_url=install_url if isinstance(install_url, str) and install_url else "",
    )
