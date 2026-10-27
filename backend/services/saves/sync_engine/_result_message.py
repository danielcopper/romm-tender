"""How a sync's answer words what it moved and what failed."""

from __future__ import annotations


def first_error_reason(errors: list[str]) -> str:
    """Return the classified reason of the first sync error, stripped of its source.

    Per-file dispatch records each failure as ``"<source>: <reason>"`` where
    ``<source>`` is the save filename (or a fixed label like ``"Failed to fetch
    saves"``) and ``<reason>`` is the :func:`lib.errors.classify_error` message.
    Split on the LAST ``": "`` (``rpartition``): the source can itself contain a
    colon — a save filename derives from the ROM name, which may include one
    (e.g. ``"Grand Theft Auto: San Andreas.srm"``) — so splitting on the last
    separator keeps that colon on the source side and isolates the reason.

    Tradeoff, stated honestly: a ``<reason>`` that itself contained ``": "``
    would be truncated to its final segment. Every classified ``classify_error``
    message (auth, forbidden, SSL, timeout, connection, rate limit, server,
    refused request, not-found, unsupported) uses an em-dash separator or none
    and never contains ``": "`` of its own, and the fixed dispatch labels don't
    either — so the common cases are exact; only the ``detail`` RomM reports
    with its own 5xx and the rare ``str(exc)`` fallback (UNKNOWN / status-less
    ``RommApiError``) can carry an internal ``": "`` and lose their head. Falls
    back to the whole entry when there is no ``": "`` separator. *errors* must
    be non-empty.
    """
    _source, sep, reason = errors[0].rpartition(": ")
    return reason if sep and reason else errors[0]


def summarize_sync_result(base: str, *, synced: int, errors: list[str], conflicts: int) -> str:
    """Compose a sync use case's result ``message``, surfacing the failure reason (#1334).

    A total failure (``synced == 0`` with errors) leads with the first error's
    classified reason — never the "Uploaded 0 save(s), 1 error(s)" count summary
    that buries it in ``errors[0]`` — and appends ``"(+N more)"`` when other files
    also failed. A partial run (some transferred, some failed) keeps the ``base``
    count summary and appends the first reason after an em-dash. A clean run
    returns ``base`` unchanged. A surfaced-conflict count is appended after the
    error clause in every case, preserving the pre-#1334 conflict suffix.
    """
    if errors:
        reason = first_error_reason(errors)
        if synced == 0:
            extra = len(errors) - 1
            msg = f"{reason} (+{extra} more)" if extra else reason
        else:
            msg = f"{base}, {len(errors)} error(s) — {reason}"
    else:
        msg = base
    if conflicts:
        msg += f", {conflicts} conflict(s)"
    return msg
