"""What one question does once: read and parse a configuration layer.

A question asks several things of the same configuration — the firmware route
reads ``retroarch.cfg`` for its override directory, its option gates and its
options file, and then reads a core's options file once per option it composes
a name from — and each of those reads used to parse the file again. Within one
question the files are the same files, so the work is done once and handed on
(issue #589).

Nothing outlives the question. The memo lives for one call of a public
question and is dropped when it returns, so the next question reads the
machine again: atlas answers what is on the machine *now*, and a memo kept
across questions would answer what was there when it was filled. A question
asked from inside another one shares the outer memo rather than opening its
own, so the two cannot disagree about a file the outer one already read.

The memo is a context variable rather than an argument, because the reads it
covers sit several calls below the question and the layers between them pass
texts, not a memo. A context variable is also per thread, so two questions
asked at once on two threads never see each other's work.
"""

from __future__ import annotations

import functools
from contextvars import ContextVar
from typing import Callable, Hashable, ParamSpec, TypeVar, cast

_P = ParamSpec("_P")
_R = TypeVar("_R")
_T = TypeVar("_T")

# The open question's memo, or ``None`` outside every question.
_MEMO: ContextVar[dict[Hashable, object] | None] = ContextVar("atlas_question_memo", default=None)


def one_question(method: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run *method* as one question: what it computes through :func:`once`, it computes once."""

    @functools.wraps(method)
    def asked(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if _MEMO.get() is not None:
            return method(*args, **kwargs)
        token = _MEMO.set({})
        try:
            return method(*args, **kwargs)
        finally:
            _MEMO.reset(token)

    return asked


def once(key: Hashable, compute: Callable[[], _T]) -> _T:
    """*compute*'s result for *key*, computed once within the open question.

    Outside a question every call computes. The result is shared by every
    caller within the question, so *compute* must return a value no caller can
    change — or the caller of :func:`once` hands out copies.
    """
    memo = _MEMO.get()
    if memo is None:
        return compute()
    if key not in memo:
        memo[key] = compute()
    return cast(_T, memo[key])
