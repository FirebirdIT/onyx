import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from onyx.db.models import PublishedArtifact

MIN_EXPIRES_IN_DAYS = 1
MAX_EXPIRES_IN_DAYS = 30
DEFAULT_EXPIRES_IN_DAYS = 7
MAX_TITLE_LENGTH = 200


class PublishArtifactRequest(BaseModel):
    # A FileRecord.file_id or a UserFile.id, the same as GET /chat/file/{file_id}.
    file_id: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    expires_in_days: int = Field(
        default=DEFAULT_EXPIRES_IN_DAYS,
        ge=MIN_EXPIRES_IN_DAYS,
        le=MAX_EXPIRES_IN_DAYS,
    )

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("The title must not be empty")
        return stripped


class PublishedArtifactSnapshot(BaseModel):
    id: UUID
    file_id: str
    hub_artifact_id: str
    url: str
    title: str
    expires_at: datetime.datetime
    created_at: datetime.datetime

    @classmethod
    def from_model(cls, artifact: PublishedArtifact) -> "PublishedArtifactSnapshot":
        return cls(
            id=artifact.id,
            file_id=artifact.file_id,
            hub_artifact_id=artifact.hub_artifact_id,
            url=artifact.url,
            title=artifact.title,
            expires_at=artifact.expires_at,
            created_at=artifact.created_at,
        )
