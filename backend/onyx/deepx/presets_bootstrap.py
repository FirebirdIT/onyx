"""Create the Deep X preset assistants from DEEPX_PRESETS_URL at startup.

The presets JSON is `{version, assistants: [{name, description, system_prompt,
task_prompt, starter_messages: [{name, message}]}]}`. Startup creates each
assistant whose name no persona has, deleted personas included, so a restart
never changes or brings back an assistant an admin edited or deleted.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from onyx.db.models import StarterMessage
from onyx.db.persona import (
    fetch_all_persona_names,
    get_default_assistant,
    upsert_persona,
)
from onyx.utils.logger import setup_logger
from onyx.utils.url import ssrf_safe_get

logger = setup_logger()

PRESETS_FETCH_TIMEOUT_SECONDS: tuple[float, float] = (3.0, 5.0)
PRESETS_MAX_RESPONSE_BYTES = 1024 * 1024
PRESET_NAME_MAX_CHARS = 200


class DeepXPresetStarterMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    message: str = Field(min_length=1)


class DeepXPresetAssistant(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=PRESET_NAME_MAX_CHARS)
    description: str = ""
    system_prompt: str = ""
    task_prompt: str = ""
    starter_messages: list[DeepXPresetStarterMessage] = Field(default_factory=list)


class DeepXPresets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: float
    assistants: list[DeepXPresetAssistant]


def _parse_assistants(raw_assistants: list[Any]) -> list[DeepXPresetAssistant]:
    """Validate each entry by itself, so one bad entry skips only itself."""
    assistants: list[DeepXPresetAssistant] = []
    for index, raw in enumerate(raw_assistants):
        try:
            assistants.append(DeepXPresetAssistant.model_validate(raw))
        except ValidationError as e:
            logger.warning(
                "Skipped Deep X preset assistant %s: %s error(s) in the entry.",
                index,
                e.error_count(),
            )
    return assistants


def parse_deepx_presets(payload: Any) -> DeepXPresets | None:
    """Return the presets in `payload`, or None when its shape is not valid."""
    if not isinstance(payload, dict):
        logger.warning("Deep X presets JSON is not an object. Presets skipped.")
        return None
    raw_assistants = payload.get("assistants")
    if not isinstance(raw_assistants, list):
        logger.warning("Deep X presets JSON has no assistants list. Presets skipped.")
        return None
    try:
        return DeepXPresets(
            version=payload.get("version", 0),
            assistants=_parse_assistants(raw_assistants),
        )
    except ValidationError:
        logger.warning("Deep X presets JSON has no valid version. Presets skipped.")
        return None


def fetch_deepx_presets(url: str) -> DeepXPresets | None:
    """GET the presets JSON. Return None and log a warning on any failure."""
    try:
        # The operator sets the URL, so private hosts are allowed. Cloud
        # metadata (link-local) addresses stay blocked.
        response = ssrf_safe_get(
            url,
            headers={"Accept": "application/json"},
            timeout=PRESETS_FETCH_TIMEOUT_SECONDS,
            allow_private_network=True,
            block_loopback_and_link_local=False,
            block_link_local_only=True,
        )
        response.raise_for_status()
        if len(response.content) > PRESETS_MAX_RESPONSE_BYTES:
            logger.warning("Deep X presets response is too large. Presets skipped.")
            return None
        payload = response.json()
    except Exception as e:
        logger.warning(
            "Could not fetch the Deep X presets (%s). Presets skipped.",
            type(e).__name__,
        )
        return None
    return parse_deepx_presets(payload)


def _default_assistant_tool_ids(db_session: Session) -> list[int]:
    """Available tool ids of the default assistant, so presets get the same tools.

    Built-in tools that are not available (for example search in Onyx Lite)
    are left out, because persona validation refuses them.
    """
    # Local import: the tools layer imports the DB layer.
    from onyx.tools.built_in_tools import get_built_in_tool_by_id

    default_assistant = get_default_assistant(db_session)
    if default_assistant is None:
        return []
    return [
        tool.id
        for tool in default_assistant.tools
        if tool.in_code_tool_id is None
        or get_built_in_tool_by_id(tool.in_code_tool_id).is_available(db_session)
    ]


def create_missing_preset_assistants(
    db_session: Session, presets: DeepXPresets
) -> list[str]:
    """Create the preset assistants that do not exist. Return the created names."""
    taken_names = fetch_all_persona_names(db_session)
    tool_ids = _default_assistant_tool_ids(db_session)
    created: list[str] = []

    for preset in presets.assistants:
        if preset.name in taken_names:
            continue
        taken_names.add(preset.name)
        try:
            upsert_persona(
                user=None,
                name=preset.name,
                description=preset.description,
                starter_messages=[
                    StarterMessage(name=starter.name, message=starter.message)
                    for starter in preset.starter_messages
                ]
                or None,
                system_prompt=preset.system_prompt,
                task_prompt=preset.task_prompt,
                datetime_aware=True,
                is_public=True,
                tool_ids=tool_ids or None,
                db_session=db_session,
                commit=True,
            )
        except Exception as e:
            db_session.rollback()
            logger.warning(
                "Could not create the Deep X preset assistant %r (%s).",
                preset.name,
                type(e).__name__,
            )
            continue
        created.append(preset.name)

    if created:
        logger.notice(
            "Created %s Deep X preset assistant(s): %s",
            len(created),
            ", ".join(created),
        )
    return created
