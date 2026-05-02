"""Tests for app/repos/comment_triggers.py."""
from __future__ import annotations

import pytest

from app.repos import comment_triggers as ct_repo
from app.repos.pool import get_pool


@pytest.mark.usefixtures("_db_setup")
class TestFindForPost:
    async def _insert_trigger(
        self,
        pool,
        *,
        platform: str = "instagram",
        post_id: str = "post_1",
        keyword: str = "очищение",
        active: bool = True,
    ) -> int:
        scenario_id = await pool.fetchval(
            "SELECT id FROM scenarios WHERE name = 'default_purify_comment'"
        )
        row = await pool.fetchrow(
            """
            INSERT INTO comment_triggers (platform, post_id, keyword, scenario_id, active)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            platform, post_id, keyword, scenario_id, active,
        )
        return row["id"]

    async def test_finds_matching_trigger(self) -> None:
        pool = await get_pool()
        await self._insert_trigger(pool)
        result = await ct_repo.find_for_post("instagram", "post_1", "хочу очищение")
        assert result is not None

    async def test_case_insensitive(self) -> None:
        pool = await get_pool()
        await self._insert_trigger(pool, keyword="ОЧИЩЕНИЕ")
        result = await ct_repo.find_for_post("instagram", "post_1", "хочу очищение сейчас")
        assert result is not None

    async def test_returns_none_wrong_post(self) -> None:
        pool = await get_pool()
        await self._insert_trigger(pool)
        result = await ct_repo.find_for_post("instagram", "post_999", "хочу очищение")
        assert result is None

    async def test_returns_none_inactive(self) -> None:
        pool = await get_pool()
        await self._insert_trigger(pool, active=False)
        result = await ct_repo.find_for_post("instagram", "post_1", "хочу очищение")
        assert result is None

    async def test_returns_none_no_keyword_match(self) -> None:
        pool = await get_pool()
        await self._insert_trigger(pool)
        result = await ct_repo.find_for_post("instagram", "post_1", "просто комментарий")
        assert result is None


@pytest.mark.usefixtures("_db_setup")
class TestAlreadyRepliedAndMarkReplied:
    async def _get_scenario_id(self, pool) -> int:
        return await pool.fetchval(
            "SELECT id FROM scenarios WHERE name = 'default_purify_comment'"
        )

    async def _create_user(self, pool) -> int:
        return await pool.fetchval(
            """
            INSERT INTO social_users (platform, external_id, provider_name, short_id)
            VALUES ('instagram', 'ext_1', 'sendpulse', 'abcd1234')
            RETURNING id
            """
        )

    async def test_not_replied_initially(self) -> None:
        pool = await get_pool()
        user_id = await self._create_user(pool)
        scenario_id = await self._get_scenario_id(pool)
        result = await ct_repo.already_replied(
            user_id=user_id, platform="instagram", post_id="p1", scenario_id=scenario_id
        )
        assert result is False

    async def test_replied_after_mark(self) -> None:
        pool = await get_pool()
        user_id = await self._create_user(pool)
        scenario_id = await self._get_scenario_id(pool)
        await ct_repo.mark_replied(
            user_id=user_id, platform="instagram", post_id="p1", scenario_id=scenario_id
        )
        result = await ct_repo.already_replied(
            user_id=user_id, platform="instagram", post_id="p1", scenario_id=scenario_id
        )
        assert result is True

    async def test_duplicate_mark_does_not_raise(self) -> None:
        pool = await get_pool()
        user_id = await self._create_user(pool)
        scenario_id = await self._get_scenario_id(pool)
        kwargs = {"user_id": user_id, "platform": "instagram", "post_id": "p1", "scenario_id": scenario_id}
        await ct_repo.mark_replied(**kwargs)
        await ct_repo.mark_replied(**kwargs)  # should not raise
