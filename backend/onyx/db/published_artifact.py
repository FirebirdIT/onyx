"""Queries for chat HTML files published to the Deep X hub."""

import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from onyx.db.models import PublishedArtifact


def create_published_artifact(
    db_session: Session,
    user_id: UUID,
    file_id: str,
    hub_artifact_id: str,
    url: str,
    title: str,
    delete_token: str,
    expires_at: datetime.datetime,
) -> PublishedArtifact:
    artifact = PublishedArtifact(
        user_id=user_id,
        file_id=file_id,
        hub_artifact_id=hub_artifact_id,
        url=url,
        title=title,
        delete_token=delete_token,
        expires_at=expires_at,
    )
    db_session.add(artifact)
    db_session.commit()
    db_session.refresh(artifact)
    return artifact


def list_published_artifacts_for_user(
    db_session: Session, user_id: UUID, now: datetime.datetime
) -> list[PublishedArtifact]:
    """The user's artifacts that did not expire, newest first."""
    stmt = (
        select(PublishedArtifact)
        .where(PublishedArtifact.user_id == user_id)
        .where(PublishedArtifact.expires_at > now)
        .order_by(PublishedArtifact.created_at.desc())
    )
    return list(db_session.scalars(stmt).all())


def get_published_artifact_for_user(
    db_session: Session, artifact_id: UUID, user_id: UUID
) -> PublishedArtifact | None:
    stmt = select(PublishedArtifact).where(
        PublishedArtifact.id == artifact_id,
        PublishedArtifact.user_id == user_id,
    )
    return db_session.scalars(stmt).first()


def delete_published_artifact(db_session: Session, artifact_id: UUID) -> None:
    db_session.execute(
        delete(PublishedArtifact).where(PublishedArtifact.id == artifact_id)
    )
    db_session.commit()
