"""Most important test in the project: full funnel end-to-end.

Pipeline:
1. User comments "ОЧИЩЕНИЕ" under a Reels (event_type='comment')
2. Webhook → events_log → worker
3. comment-to-DM scenario fires → produces DM with deep-link
4. FakeProvider receives the DM
5. Extract short_id from the DM text (simulates user clicking the link)
6. GET /api/lead/{short_id} → bot_purify-style response
7. POST /api/lead/{short_id}/handover → record the conversion
8. Verify tg_handover_at and tg_user_id are set in DB

If this test passes, the entire social_inbox → bot_purify integration is wired correctly.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import asyncpg
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import users
from app.workers.tasks_messages import process_incoming_event
from tests.fakes.fake_provider import FakeProvider

VALID_TOKEN = "test-token"


async def test_full_funnel_comment_to_telegram_handover(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db: asyncpg.Connection,
) -> None:
    # Step 1: user comments with keyword
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id="funnel_user_1",
        external_event_id="funnel_evt_1",
        username="anna_purify",
        full_name="Anna Purifier",
        text="хочу ОЧИЩЕНИЕ программу!",
        post_id="reels_42",
        comment_id="comment_42",
        occurred_at=datetime(2026, 4, 30, 10, 0, tzinfo=UTC),
    )
    fake_provider.queue_event(event)

    # Step 2: webhook
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = 'funnel_evt_1'",
    )
    assert log_row is not None

    # Step 3-4: worker processes; FakeProvider receives outgoing
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])
    assert len(fake_provider.sent) == 1
    sent_dm = fake_provider.sent[0]

    # Step 5: extract short_id from deep-link in the DM text
    match = re.search(r"ig_([0-9A-Za-z]{8})_(\w+)", sent_dm.text or "")
    assert match is not None, f"No deep-link found in: {sent_dm.text}"
    short_id, slug_in_link = match.groups()

    # Step 6: bot_purify dereferences the deep-link
    lead_response = await client.get(
        f"/api/lead/{short_id}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert lead_response.status_code == 200
    lead_body = lead_response.json()
    assert lead_body["user"]["full_name"] == "Anna Purifier"
    assert lead_body["scenario"] == slug_in_link
    assert len(lead_body["recent_messages"]) >= 2

    # Step 7: bot_purify confirms successful landing
    handover_response = await client.post(
        f"/api/lead/{short_id}/handover",
        json={"tg_user_id": 555111222},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert handover_response.status_code == 200

    # Step 8: verify DB state
    user = await users.get_by_external("sendpulse", "instagram", "funnel_user_1")
    assert user is not None
    assert user["tg_user_id"] == 555111222
    assert user["tg_handover_at"] is not None
