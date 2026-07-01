"""Smoke tests for repository stubs."""

from baseline_api.db.models.enums import PrivacyMode
from baseline_api.db.models.user import User
from baseline_api.db.repositories.user import UserRepository


def test_user_repository_create_and_get(db_session) -> None:
    """The generic base repository can persist and retrieve an entity."""
    repo = UserRepository(db_session)
    user = User(
        privacy_mode=PrivacyMode.local_only,
        active_consent_version="v1",
    )
    created = repo.create(user)
    db_session.flush()

    read = repo.get_by_id(created.id)
    assert read is not None
    assert read.id == created.id
    assert read.privacy_mode == PrivacyMode.local_only


def test_user_repository_list(db_session) -> None:
    """The generic base repository can list persisted entities."""
    repo = UserRepository(db_session)
    for _ in range(3):
        repo.create(
            User(
                privacy_mode=PrivacyMode.cloud_assisted,
                active_consent_version="v1",
            )
        )
    db_session.flush()

    users = repo.list_all(limit=10)
    assert len(users) == 3
