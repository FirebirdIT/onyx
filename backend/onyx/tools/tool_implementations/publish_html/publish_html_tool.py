"""Built-in tool that publishes an HTML chat file to the Deep X hub.

It uses the same code path as `POST /user/published-artifacts`
(`published_artifacts/service.py:publish_chat_html_file`)."""

import datetime
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy.orm import Session

from onyx.chat.emitter import Emitter
from onyx.configs.app_configs import DEEPX_HUB_URL
from onyx.db.engine.sql_engine import get_session_with_current_tenant
from onyx.db.models import User
from onyx.error_handling.exceptions import OnyxError
from onyx.llm.models import ToolDefinition
from onyx.server.features.published_artifacts.models import (
    DEFAULT_EXPIRES_IN_DAYS,
    MAX_EXPIRES_IN_DAYS,
    MAX_TITLE_LENGTH,
    MIN_EXPIRES_IN_DAYS,
    PublishArtifactRequest,
)
from onyx.server.features.published_artifacts.service import publish_chat_html_file
from onyx.server.query_and_chat.placement import Placement
from onyx.server.query_and_chat.streaming_models import (
    CustomToolDelta,
    CustomToolStart,
    Packet,
)
from onyx.tools.interface import Tool
from onyx.tools.models import ToolCallException, ToolResponse
from onyx.tools.tool_implementations.office.office_tools import (
    format_validation_error,
)
from onyx.utils.logger import setup_logger

logger = setup_logger()

FILE_ID_FIELD = "file_id"
TITLE_FIELD = "title"
EXPIRES_IN_DAYS_FIELD = "expires_in_days"

# The id in a chat file link: `/api/chat/file/<id>`, absolute or relative.
_CHAT_FILE_LINK_PATTERN = re.compile(r"/api/chat/file/([^/?#\s()\[\]]+)")


class PublishHtmlResult(BaseModel):
    """The `llm_facing_response` of a successful call."""

    url: str
    title: str
    expires_at: datetime.datetime


def extract_chat_file_id(value: str) -> str:
    """Accept a bare file id or a chat file link and return the id."""
    stripped = value.strip()
    match = _CHAT_FILE_LINK_PATTERN.search(stripped)
    return match.group(1) if match else stripped


def summarize_publish_result(result: PublishHtmlResult) -> str:
    """The text the UI shows for a published page, live and on replay."""
    return (
        f"Published {result.title}: {result.url} "
        f"(expires {result.expires_at.date().isoformat()})"
    )


class PublishHtmlTool(Tool[None]):
    NAME = "publish_html"
    DISPLAY_NAME = "Publish Web Page"
    DESCRIPTION = (
        "Publish an HTML file from this conversation as a public web page on the "
        "Deep X hub and return its link. Use it only when the user asks to "
        "publish, share or host a web page, for example an index.html that "
        "run_python made. Anyone who has the link can open the page. Give the "
        "file id or the chat file link (/api/chat/file/<id>) of the HTML file. "
        "After publishing, give the user the link, tell them when it expires, and "
        "tell them they can unpublish it in Settings > Published Links."
    )

    def __init__(self, tool_id: int, emitter: Emitter, user: User) -> None:
        super().__init__(emitter=emitter)
        self._id = tool_id
        self._user_id: UUID = user.id

    @property
    def id(self) -> int:
        return self._id

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def description(self) -> str:
        return self.DESCRIPTION

    @property
    def display_name(self) -> str:
        return self.DISPLAY_NAME

    @classmethod
    def is_available(cls, db_session: Session) -> bool:  # noqa: ARG003
        # The hub accepts an install token when no Deep X Route key exists.
        return bool(DEEPX_HUB_URL)

    def tool_definition(self) -> ToolDefinition:
        parameters: dict[str, JsonValue] = {
            "type": "object",
            "properties": {
                FILE_ID_FIELD: {
                    "type": "string",
                    "description": (
                        "The id of the HTML file, or its chat file link "
                        "(/api/chat/file/<id>)."
                    ),
                },
                TITLE_FIELD: {
                    "type": "string",
                    "maxLength": MAX_TITLE_LENGTH,
                    "description": "A short title for the published page.",
                },
                EXPIRES_IN_DAYS_FIELD: {
                    "type": "integer",
                    "minimum": MIN_EXPIRES_IN_DAYS,
                    "maximum": MAX_EXPIRES_IN_DAYS,
                    "default": DEFAULT_EXPIRES_IN_DAYS,
                    "description": (
                        "Days until the link expires "
                        f"({MIN_EXPIRES_IN_DAYS}-{MAX_EXPIRES_IN_DAYS})."
                    ),
                },
            },
            "required": [FILE_ID_FIELD, TITLE_FIELD],
        }
        return ToolDefinition(
            name=self.name, description=self.description, parameters=parameters
        )

    def emit_start(self, placement: Placement) -> None:
        self.emitter.emit(
            Packet(
                placement=placement,
                obj=CustomToolStart(tool_name=self.display_name, tool_id=self._id),
            )
        )

    def _emit_text(self, placement: Placement, text: str) -> None:
        self.emitter.emit(
            Packet(
                placement=placement,
                obj=CustomToolDelta(
                    tool_name=self.display_name,
                    tool_id=self._id,
                    response_type="text",
                    data=text,
                ),
            )
        )

    def _fail(self, placement: Placement, message: str) -> ToolCallException:
        self._emit_text(placement, f"The page was not published. {message}")
        return ToolCallException(
            message=f"{self.name} failed: {message}",
            llm_facing_message=f"The page was not published. {message}",
        )

    def _parse_request(self, llm_kwargs: dict[str, Any]) -> PublishArtifactRequest:
        raw_file_id = llm_kwargs.get(FILE_ID_FIELD)
        arguments = dict(llm_kwargs)
        if isinstance(raw_file_id, str):
            arguments[FILE_ID_FIELD] = extract_chat_file_id(raw_file_id)
        if arguments.get(EXPIRES_IN_DAYS_FIELD) is None:
            arguments.pop(EXPIRES_IN_DAYS_FIELD, None)
        return PublishArtifactRequest.model_validate(arguments)

    def run(
        self,
        placement: Placement,
        override_kwargs: None,  # noqa: ARG002
        **llm_kwargs: Any,
    ) -> ToolResponse:
        try:
            request = self._parse_request(llm_kwargs)
        except ValidationError as e:
            raise self._fail(
                placement,
                f"Invalid arguments: {format_validation_error(e)}. "
                f"Fix them and call {self.name} again.",
            )

        with get_session_with_current_tenant() as db_session:
            user = db_session.get(User, self._user_id)
            if user is None:
                raise self._fail(placement, "The user account was not found.")
            try:
                snapshot = publish_chat_html_file(
                    file_id=request.file_id,
                    title=request.title,
                    expires_in_days=request.expires_in_days,
                    user=user,
                    db_session=db_session,
                )
            except OnyxError as e:
                logger.info("%s failed: %s", self.name, e.error_code.code)
                raise self._fail(placement, e.detail)

        result = PublishHtmlResult(
            url=snapshot.url, title=snapshot.title, expires_at=snapshot.expires_at
        )
        self._emit_text(placement, summarize_publish_result(result))
        return ToolResponse(
            rich_response=None, llm_facing_response=result.model_dump_json()
        )
