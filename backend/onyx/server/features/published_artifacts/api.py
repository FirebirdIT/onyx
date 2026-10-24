"""Publish chat HTML files to the Deep X hub as public links."""

import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from onyx.auth.permissions import require_permission
from onyx.configs.app_configs import DEEPX_HUB_URL
from onyx.db.engine.sql_engine import get_session
from onyx.db.enums import Permission
from onyx.db.models import User
from onyx.db.published_artifact import (
    delete_published_artifact,
    get_published_artifact_for_user,
    list_published_artifacts_for_user,
)
from onyx.deepx.hub_artifacts import delete_artifact_from_hub
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.server.features.published_artifacts.models import (
    PublishArtifactRequest,
    PublishedArtifactSnapshot,
)
from onyx.server.features.published_artifacts.service import publish_chat_html_file

router = APIRouter(prefix="/user/published-artifacts")


@router.post("")
def publish_artifact(
    request: PublishArtifactRequest,
    user: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> PublishedArtifactSnapshot:
    return publish_chat_html_file(
        file_id=request.file_id,
        title=request.title,
        expires_in_days=request.expires_in_days,
        user=user,
        db_session=db_session,
    )


@router.get("")
def list_published_artifacts(
    user: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> list[PublishedArtifactSnapshot]:
    now = datetime.datetime.now(datetime.timezone.utc)
    return [
        PublishedArtifactSnapshot.from_model(artifact)
        for artifact in list_published_artifacts_for_user(db_session, user.id, now)
    ]


@router.delete("/{artifact_id}")
def unpublish_artifact(
    artifact_id: UUID,
    user: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> None:
    artifact = get_published_artifact_for_user(db_session, artifact_id, user.id)
    if artifact is None:
        raise OnyxError(OnyxErrorCode.NOT_FOUND, "Published link not found")

    now = datetime.datetime.now(datetime.timezone.utc)
    if artifact.expires_at > now:
        delete_artifact_from_hub(
            DEEPX_HUB_URL,
            artifact.hub_artifact_id,
            artifact.delete_token.get_value(apply_mask=False),
        )
    delete_published_artifact(db_session, artifact.id)
