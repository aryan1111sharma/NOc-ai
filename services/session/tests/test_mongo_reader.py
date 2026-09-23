"""Focused tests for session/mongo_reader.py against the confirmed real
LibreChat schema shapes (conversations/messages, embedded feedback,
content[].tool_call) -- see that module's docstring."""

from __future__ import annotations

from datetime import datetime, timezone

from session import mongo_reader
from tests.fakes import FakeDB, tool_call_part


def _conversation(**overrides) -> dict:
    base = {
        "conversationId": "conv-1",
        "user": "user-1",
        "agent_id": "agent_kimi26NOCEngineerV1",
        "endpoint": "NOC Router",
        "title": "Test conversation",
        "createdAt": datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc),
        "updatedAt": datetime(2026, 9, 23, 10, 1, 0, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def _user_message(**overrides) -> dict:
    base = {
        "messageId": "msg-user-1",
        "conversationId": "conv-1",
        "parentMessageId": None,
        "isCreatedByUser": True,
        "sender": "User",
        "model": None,
        "text": "hello there",
        "tokenCount": 5,
        "createdAt": datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def _assistant_message(**overrides) -> dict:
    base = {
        "messageId": "msg-assistant-1",
        "conversationId": "conv-1",
        "parentMessageId": "msg-user-1",
        "isCreatedByUser": False,
        "sender": "Kimi",
        "model": "kimi-for-coding",
        "text": "hi, how can I help?",
        "tokenCount": 220,
        "createdAt": datetime(2026, 9, 23, 10, 0, 5, tzinfo=timezone.utc),
        "finish_reason": "stop",
        "error": False,
        "unfinished": False,
        "feedback": None,
        "content": [],
    }
    base.update(overrides)
    return base


def test_list_sessions_returns_newest_first():
    db = FakeDB(
        conversations=[
            _conversation(conversationId="conv-1", createdAt=datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)),
            _conversation(conversationId="conv-2", createdAt=datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)),
        ],
        messages=[],
    )

    sessions = mongo_reader.list_sessions(db)

    assert [s.conversation_id for s in sessions] == ["conv-2", "conv-1"]


def test_list_sessions_filters_by_agent_id():
    db = FakeDB(
        conversations=[
            _conversation(conversationId="conv-1", agent_id="agent-a"),
            _conversation(conversationId="conv-2", agent_id="agent-b"),
        ],
        messages=[],
    )

    sessions = mongo_reader.list_sessions(db, agent_id="agent-b")

    assert [s.conversation_id for s in sessions] == ["conv-2"]


def test_get_session_detail_returns_none_for_unknown_conversation():
    db = FakeDB(conversations=[], messages=[])

    assert mongo_reader.get_session_detail(db, "no-such-conversation") is None


def test_get_session_detail_orders_turns_oldest_first():
    db = FakeDB(
        conversations=[_conversation()],
        messages=[
            _assistant_message(messageId="m2", createdAt=datetime(2026, 9, 23, 10, 0, 10, tzinfo=timezone.utc)),
            _user_message(messageId="m1", createdAt=datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc)),
        ],
    )

    detail = mongo_reader.get_session_detail(db, "conv-1")

    assert [t.message_id for t in detail.turns] == ["m1", "m2"]


def test_get_session_detail_extracts_invoked_tools_with_output_status():
    db = FakeDB(
        conversations=[_conversation()],
        messages=[
            _user_message(),
            _assistant_message(
                content=[
                    tool_call_part(id="call-1", name="jira_search", output="found 3 issues"),
                    tool_call_part(id="call-2", name="cms_lookup", output=None),
                ]
            ),
        ],
    )

    detail = mongo_reader.get_session_detail(db, "conv-1")
    tools = detail.turns[1].tools

    assert [t.name for t in tools] == ["jira_search", "cms_lookup"]
    assert tools[0].has_output is True
    assert tools[1].has_output is False


def test_get_session_detail_surfaces_embedded_feedback():
    db = FakeDB(
        conversations=[_conversation()],
        messages=[
            _user_message(),
            _assistant_message(feedback={"rating": "thumbsUp", "tag": "helpful"}),
        ],
    )

    detail = mongo_reader.get_session_detail(db, "conv-1")

    assert detail.turns[1].feedback == {"rating": "thumbsUp", "tag": "helpful"}


def test_get_session_detail_reports_per_message_token_counts():
    db = FakeDB(
        conversations=[_conversation()],
        messages=[_user_message(tokenCount=5), _assistant_message(tokenCount=220)],
    )

    detail = mongo_reader.get_session_detail(db, "conv-1")

    assert detail.turns[0].token_count == 5
    assert detail.turns[1].token_count == 220
