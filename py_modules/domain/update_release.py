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

# Where a user is sent to fetch the update. Deliberately a constant rather than
# the ``browser_download_url`` of the release we parsed: GitHub keeps this
# ``releases/latest/download/`` form pointed at whatever is latest, so it is
# still right when the stamp below is a day old, and it is the address the
# install instructions name.
DOWNLOAD_URL = "https://github.com/danielcopper/romm-tender/releases/latest/download/Tender.zip"


@dataclass(frozen=True)
class LatestRelease:
    """The release GitHub calls latest, in this plugin's own vocabulary.

    ``version`` is the bare version the tag named. ``digest`` is the release
    asset's sha256 hex, or ``None`` when the release carries no asset this
    plugin recognises — the download stays possible without it, unverified.
    """

    version: str
    digest: str | None


@dataclass(frozen=True)
class UpdateCheck:
    """What one check saw, and when it ran.

    ``checked_at`` is a Unix timestamp and is what the once-a-day throttle
    reads; it stamps the ATTEMPT, so a check that reached nothing still holds
    the next one off. ``version`` and ``digest`` are the newest answer that ever
    arrived, which is why a failed attempt carries the previous ones forward
    rather than clearing them. Both are ``None`` until an attempt has succeeded.
    """

    checked_at: float
    version: str | None
    digest: str | None


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
    return json.dumps({"checked_at": check.checked_at, "version": check.version, "digest": check.digest})


def decode_update_check(raw: str | None) -> UpdateCheck | None:
    """Decode the stored stamp, or ``None`` when there is no usable one.

    ``None`` means "no check has completed", which is what the caller acts on,
    so every unusable value — absent, empty, not JSON, not an object, or
    carrying no numeric ``checked_at`` — collapses onto it and the next call
    simply checks again. A stored ``version`` / ``digest`` that is not a
    non-empty string is dropped to ``None`` on its own: the timestamp is still a
    true statement about when we last asked.
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
    return UpdateCheck(
        checked_at=float(checked_at),
        version=version if isinstance(version, str) and version else None,
        digest=digest if isinstance(digest, str) and digest else None,
    )
