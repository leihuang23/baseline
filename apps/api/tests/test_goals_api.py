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
from baseline_api.goals import GoalService


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
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


def test_goal_crud_pause_and_resume(db_session: Session) -> None:
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

    fetched = client.get(f"/v1/goals/{goal_id}")
    assert fetched.status_code == 200
    assert fetched.json()["data"] == created

    updated = client.put(
        f"/v1/goals/{goal_id}",
        json=_goal_payload(
            category="sleep",
            priority=4,
            time_horizon="short_term",
            success_metric="7h sleep average",
            constraints={"latest_caffeine": "14:00"},
        ),
    )
    assert updated.status_code == 200
    updated_data = updated.json()["data"]
    assert updated_data["id"] == str(goal_id)
    assert updated_data["category"] == "sleep"
    assert updated_data["priority"] == 4
    assert updated_data["time_horizon"] == "short_term"
    assert updated_data["success_metric"] == "7h sleep average"
    assert updated_data["constraints"] == {"latest_caffeine": "14:00"}
    assert updated_data["active"] is True

    paused = client.post(f"/v1/goals/{goal_id}/pause")
    assert paused.status_code == 200
    paused_data = paused.json()["data"]
    assert paused_data["id"] == str(goal_id)
    assert paused_data["active"] is False

    db_session.refresh(rows[0])
    assert rows[0].active is False
    assert rows[0].paused_at is not None

    resumed = client.post(f"/v1/goals/{goal_id}/resume")
    assert resumed.status_code == 200
    resumed_data = resumed.json()["data"]
    assert resumed_data["id"] == str(goal_id)
    assert resumed_data["active"] is True

    db_session.refresh(rows[0])
    assert rows[0].active is True
    assert rows[0].paused_at is None

    deleted = client.delete(f"/v1/goals/{goal_id}")
    assert deleted.status_code == 204
    assert db_session.get(Goal, goal_id) is None

    missing = client.get(f"/v1/goals/{goal_id}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "goal_not_found"


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


def test_active_goal_set_accessor_shape(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add_all(
        [
            Goal(
                user_id=user.id,
                category=GoalCategory.sleep,
                priority=2,
                time_horizon=TimeHorizon.short_term,
                success_metric="7h average",
                constraints={"latest_caffeine": "14:00"},
                active=True,
            ),
            Goal(
                user_id=user.id,
                category=GoalCategory.vo2_max,
                priority=5,
                time_horizon=TimeHorizon.long_term,
                success_metric="improve VO2 trend",
                constraints={"max_sessions_per_week": "3"},
                active=True,
            ),
            Goal(
                user_id=user.id,
                category=GoalCategory.strength,
                priority=5,
                time_horizon=TimeHorizon.medium_term,
                success_metric="maintain lifts",
                constraints={"no_max_attempts": "true"},
                active=False,
            ),
        ]
    )
    db_session.flush()

    active_goal_set = GoalService(db_session).get_active_goal_set().model_dump(mode="json")

    assert active_goal_set["schema_version"] == "v1"
    assert active_goal_set["user_id"] == str(user.id)
    assert [goal["category"] for goal in active_goal_set["goals"]] == ["vo2_max", "sleep"]
    assert [goal["priority_order"] for goal in active_goal_set["goals"]] == [1, 2]
    assert active_goal_set["category_priorities"] == {"vo2_max": 5, "sleep": 2}
    assert active_goal_set["horizons_by_category"] == {
        "vo2_max": ["long_term"],
        "sleep": ["short_term"],
    }
    assert active_goal_set["constraints_by_category"] == {
        "vo2_max": [{"max_sessions_per_week": "3"}],
        "sleep": [{"latest_caffeine": "14:00"}],
    }


def test_goal_validation_rejects_invalid_category_priority_and_constraints(
    db_session: Session,
) -> None:
    _seed_user(db_session)
    client = _client(db_session)

    invalid_category = client.post("/v1/goals", json=_goal_payload(category="weight_loss"))
    invalid_priority = client.post("/v1/goals", json=_goal_payload(priority=0))
    clinical_constraint = client.post(
        "/v1/goals",
        json=_goal_payload(constraints={"medication": "adjust dose"}),
    )
    clinical_constraint_value = client.post(
        "/v1/goals",
        json=_goal_payload(constraints={"sexual_health": "medication dose 50mg"}),
    )
    clinical_note_value = client.post(
        "/v1/goals",
        json=_goal_payload(constraints={"notes": "diagnosis should improve"}),
    )
    clinical_success_metric = client.post(
        "/v1/goals",
        json=_goal_payload(
            category="long_term_wellness",
            success_metric="reduce erectile dysfunction symptoms",
            constraints={},
        ),
    )
    detailed_constraint = client.post(
        "/v1/goals",
        json=_goal_payload(constraints={"training": "zone 2\nzone 5"}),
    )
    empty_constraint = client.post(
        "/v1/goals",
        json=_goal_payload(constraints={"training": "   "}),
    )

    assert invalid_category.status_code == 422
    assert invalid_priority.status_code == 422
    assert clinical_constraint.status_code == 422
    assert clinical_constraint_value.status_code == 422
    assert clinical_note_value.status_code == 422
    assert clinical_success_metric.status_code == 422
    assert detailed_constraint.status_code == 422
    assert empty_constraint.status_code == 422
