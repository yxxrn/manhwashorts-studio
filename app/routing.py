"""Route class that commits the request's transaction before replying.

Why this exists
---------------

``app.db.get_db`` is a dependency with ``yield``, and since FastAPI 0.106 the
code after the ``yield`` runs *after* the response has already been sent to the
client. That put the ``db.commit()`` on the wrong side of the reply:

    POST /api/auth/register  ->  201 Created   (client now has its cookie)
                             ->  ...commit happens here, milliseconds later

A caller that immediately issued its next request opened a new session which
could not yet see the uncommitted row, and got ``401 Account not found``.
Measured against a live server: 12/12 failures with no delay, 0/6 with a 1.5s
delay, and a read-only connection confirmed the row was absent at the exact
moment the 201 arrived.

Browsers never noticed because a human takes far longer than a millisecond to
trigger the next call, and the test suite never noticed because ``TestClient``
finishes the whole ASGI cycle, teardown included, before returning. Only a fast
programmatic client — exactly the AI-agent usage this project is built for — hit
it, and the symptom looked like an auth bug rather than a persistence one.

The fix keeps the commit in one place instead of pushing ``db.commit()`` into
every route: the handler runs, the response is built, the transaction is
committed, and only then is the response returned.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute


class CommitRoute(APIRoute):
    """Commit the request-scoped session after the handler, before replying.

    Nothing happens when a request never asked for a database session, so
    read-only endpoints such as ``/api/health`` are unaffected.

    Exceptions deliberately bypass the commit: the handler raising means the
    response is an error, and ``get_db`` rolls back in its own ``except``
    branch. A half-finished write must not be persisted just because the
    response object was already constructed.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def commit_after_handler(request: Request) -> Response:
            response = await original_handler(request)

            session = getattr(request.state, "db", None)
            if session is not None and session.in_transaction():
                # Safe to commit here: the response is already serialised, and
                # the session is configured with expire_on_commit=False so ORM
                # objects stay usable either side of this call.
                session.commit()

            return response

        return commit_after_handler
