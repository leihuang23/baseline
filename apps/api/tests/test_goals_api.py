"""Tests for the thin goal-management API used by the iOS goal setup UI."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.db.models.enums import GoalCategory, PrivacyMode, TimeHorizon
from baseline_api.db.models.goals import Goal
from baseline_api.db.models.user import User
from baseline_api.db.session import get_db_session


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5432/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )


def _client(db_session: Session) -> TestClient:
    app = create_app(_settings())

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


def _seed_user(db_session: Session) -> User:
    user = User(
        privacy_mode=PrivacyMode.local_only,
        active_consent_version="v1",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _goal_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": "strength",
        "priority": 5,
        "time_horizon": "long_term",
        "success_metric": "deadlift consistency",
        "constraints": {"notes": "no max attempts"},
    }
    payload.update(overrides)
    return payload


def test_create_list_and_pause_goal(db_session: Session) -> None:
    user = _seed_user(db_session)
    client = _client(db_session)

    create = client.post("/v1/goals", json=_goal_payload())

    assert create.status_code == 200
    created = create.json()["data"]
    goal_id = UUID(created["id"])
    assert created == {
        "schema_version": "v1",
        "id": str(goal_id),
        "category": "strength",
        "priority": 5,
        "time_horizon": "long_term",
        "success_metric": "deadlift consistency",
        "constraints": {"notes": "no max attempts"},
        "active": True,
    }

    rows = list(db_session.exec(select(Goal)).all())
    assert len(rows) == 1
    assert rows[0].user_id == user.id

    listed = client.get("/v1/goals")
    assert listed.status_code == 200
    assert listed.json()["data"] == [created]

    paused = client.post(f"/v1/goals/{goal_id}/pause")
    assert paused.status_code == 200
    paused_data = paused.json()["data"]
    assert paused_data["id"] == str(goal_id)
    assert paused_data["active"] is False

    db_session.refresh(rows[0])
    assert rows[0].active is False
    assert rows[0].paused_at is not None


def test_list_goals_returns_active_then_priority_order(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add(
        Goal(
            user_id=user.id,
            category=GoalCategory.sleep,
            priority=1,
            time_horizon=TimeHorizon.short_term,
            success_metric="7h average",
            active=True,
        )
    )
    db_session.add(
        Goal(
            user_id=user.id,
            category=GoalCategory.recovery,
            priority=4,
            time_horizon=TimeHorizon.medium_term,
            success_metric="lower soreness",
            active=False,
        )
    )
    db_session.flush()
    client = _client(db_session)

    response = client.get("/v1/goals")

    assert response.status_code == 200
    data = response.json()["data"]
    assert [goal["category"] for goal in data] == ["sleep", "recovery"]


def test_pause_missing_goal_returns_typed_error(db_session: Session) -> None:
    _seed_user(db_session)
    client = _client(db_session)

    response = client.post(f"/v1/goals/{uuid4()}/pause")

    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "goal_not_found"
