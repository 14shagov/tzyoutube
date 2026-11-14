"""Review B6: LIKE/ILIKE wildcards in the user search query must be escaped.

A raw `%` or `_` in the query matches EVERYTHING (or single chars) — search
for "100%" must not return all videos. The pattern is escaped with a
backslash and the statement declares ESCAPE '\\'.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import create_autospec

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.repos import SqlFeedRepo, escape_ilike


def _session_with_rows(rows: list) -> AsyncSession:
    """Autospec session whose execute().scalars().all() returns `rows`.

    create_autospec makes execute()'s result chain async, so replace the
    scalars() step with a plain sync lambda.
    """
    session = create_autospec(AsyncSession, instance=True)
    session.execute.return_value.scalars = lambda: SimpleNamespace(all=lambda: rows)
    return session


class TestEscapeIlike:
    def test_percent_and_underscore_are_escaped(self) -> None:
        assert escape_ilike("100%_off") == "100\\%\\_off"

    def test_backslash_itself_is_escaped_first(self) -> None:
        assert escape_ilike("a\\b") == "a\\\\b"
        # A crafted "a\%" must not turn into an escaped percent literal.
        assert escape_ilike("\\%") == "\\\\\\%"

    def test_plain_text_unchanged(self) -> None:
        assert escape_ilike("hello world") == "hello world"


class TestSearchEscapesWildcards:
    async def _captured_pattern(self, query: str) -> tuple[str, str]:
        session = _session_with_rows([])
        repo = SqlFeedRepo(session)
        await repo.search(query, limit=20, offset=0)
        stmt = session.execute.call_args[0][0]
        compiled = stmt.compile()
        # The ilike pattern is the bind param that starts with the wildcard.
        pattern = next(
            v for v in compiled.params.values() if isinstance(v, str) and v.startswith("%")
        )
        return pattern, str(compiled).upper()

    async def test_wildcards_escaped_in_pattern(self) -> None:
        pattern, _ = await self._captured_pattern("100%_x\\")
        assert pattern == "%100\\%\\_x\\\\%", pattern

    async def test_escape_clause_declared(self) -> None:
        _, sql = await self._captured_pattern("50%")
        assert "ESCAPE" in sql, sql

    async def test_search_returns_rows(self) -> None:
        row = object()
        session = _session_with_rows([row])
        repo = SqlFeedRepo(session)
        result = await repo.search("clip")
        assert result == [row]

    def test_feed_repo_requires_session(self) -> None:
        # Guard against regression of A1: constructor takes the session.
        session = create_autospec(AsyncSession, instance=True)
        repo = SqlFeedRepo(session)
        assert repo.session is session
        assert uuid  # keep import used if assertions change
