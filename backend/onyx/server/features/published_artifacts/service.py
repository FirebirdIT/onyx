"""Publish a chat HTML file to the Deep X hub.

The REST endpoint and the `publish_html` chat tool both use
`publish_chat_html_file`, so they share one access check, one set of file
checks and one error mapping.

The hub accepts a Deep X Route API key or an install token. A Deep X Route
provider key wins. Without one, the install token in the encrypted key-value
store is used, and the install is registered with the hub when no token is
stored yet.

Never log the Deep X Route API key, an install token or a delete token.
"""

from typing import NamedTuple
from uuid import UUID

from sqlalchemy.orm import Session

from onyx.access.access import user_can_access_chat_file
from onyx.configs.app_configs import DEEPX_HUB_URL
from onyx.configs.constants import KV_DEEPX_HUB_INSTALL_TOKEN_KEY
from onyx.db.encrypted_kv_store import load_encrypted_kv, upsert_encrypted_kv
from onyx.db.file_record import FileRecordNotFoundError
from onyx.db.llm import fetch_first_accessible_llm_provider_by_type
from onyx.db.models import User
from onyx.db.published_artifact import create_published_artifact
from onyx.db.user_file import get_file_id_by_user_file_id
from onyx.deepx.hub_artifacts import (
    HubArtifact,
    delete_artifact_from_hub,
    publish_html_to_hub,
    register_install_with_hub,
)
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.file_store.file_store import get_default_file_store
from onyx.key_value_store.interface import KvKeyNotFoundError, unwrap_str
from onyx.llm.constants import LlmProviderNames
from onyx.server.features.published_artifacts.models import (
    PublishedArtifactSnapshot,
)
from onyx.utils.logger import setup_logger

logger = setup_logger()

MAX_PUBLISH_BYTES = 4 * 1024 * 1024
HTML_MIME_TYPE = "text/html"
HTML_EXTENSIONS = (".html", ".htm")


class HubCredential(NamedTuple):
    token: str
    is_install_token: bool


def is_html_file(file_type: str | None, display_name: str | None) -> bool:
    mime = (file_type or "").split(";", 1)[0].strip().lower()
    if mime == HTML_MIME_TYPE:
        return True
    return (display_name or "").strip().lower().endswith(HTML_EXTENSIONS)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _too_large() -> OnyxError:
    return OnyxError(
        OnyxErrorCode.PAYLOAD_TOO_LARGE,
        "The file is too large to publish. The limit is 4 MiB.",
    )


def load_accessible_html_file(
    file_id: str, user: User, db_session: Session
) -> tuple[str, str]:
    """Return (storage file id, HTML text) for a file the user may read.

    The access rule is the one GET /chat/file/{file_id} uses.
    """
    storage_file_id = file_id
    if _is_uuid(file_id):
        storage_file_id = get_file_id_by_user_file_id(file_id, db_session) or file_id
    if not user_can_access_chat_file(storage_file_id, user, db_session):
        # 404, not 403, so a caller cannot probe for files of other users.
        raise OnyxError(OnyxErrorCode.NOT_FOUND, "File not found")

    file_store = get_default_file_store()
    try:
        record = file_store.read_file_record(storage_file_id)
    except FileRecordNotFoundError:
        raise OnyxError(OnyxErrorCode.NOT_FOUND, "File not found")
    if not is_html_file(record.file_type, record.display_name):
        raise OnyxError(OnyxErrorCode.INVALID_INPUT, "You can publish only HTML files.")
    if record.file_size is not None and record.file_size > MAX_PUBLISH_BYTES:
        raise _too_large()

    file_io = file_store.read_file(storage_file_id, mode="b")
    try:
        content = file_io.read(MAX_PUBLISH_BYTES + 1)
    finally:
        file_io.close()
    if len(content) > MAX_PUBLISH_BYTES:
        raise _too_large()
    try:
        html = content.decode("utf-8")
    except UnicodeDecodeError:
        raise OnyxError(OnyxErrorCode.INVALID_INPUT, "The file is not UTF-8 text.")
    return storage_file_id, html


def get_deepx_route_api_key(user: User, db_session: Session) -> str | None:
    """The API key of the first Deep X Route provider the user may use."""
    provider = fetch_first_accessible_llm_provider_by_type(
        LlmProviderNames.DEEPX_ROUTE, user, db_session
    )
    if provider is None or provider.api_key is None:
        return None
    return provider.api_key.get_value(apply_mask=False) or None


def _load_install_token() -> str | None:
    try:
        token = unwrap_str(load_encrypted_kv(KV_DEEPX_HUB_INSTALL_TOKEN_KEY))
    except KvKeyNotFoundError:
        return None
    except ValueError:
        logger.warning("The stored Deep X hub install token is not valid.")
        return None
    return token or None


def _register_install() -> str:
    token = register_install_with_hub(DEEPX_HUB_URL)
    upsert_encrypted_kv(KV_DEEPX_HUB_INSTALL_TOKEN_KEY, {"value": token})
    return token


def resolve_hub_credential(user: User, db_session: Session) -> HubCredential:
    route_key = get_deepx_route_api_key(user, db_session)
    if route_key:
        return HubCredential(token=route_key, is_install_token=False)
    stored = _load_install_token()
    if stored:
        return HubCredential(token=stored, is_install_token=True)
    return HubCredential(token=_register_install(), is_install_token=True)


def _publish_with_credential(
    credential: HubCredential, title: str, html: str, expires_in_days: int
) -> HubArtifact:
    try:
        return publish_html_to_hub(
            DEEPX_HUB_URL,
            api_key=credential.token,
            title=title,
            html=html,
            expires_in_days=expires_in_days,
        )
    except OnyxError as e:
        if not (
            credential.is_install_token
            and e.error_code == OnyxErrorCode.CREDENTIAL_INVALID
        ):
            raise
    # The hub does not know the stored install token: register again once.
    logger.info("The Deep X hub refused the install token. Registering again.")
    try:
        return publish_html_to_hub(
            DEEPX_HUB_URL,
            api_key=_register_install(),
            title=title,
            html=html,
            expires_in_days=expires_in_days,
        )
    except OnyxError as e:
        if e.error_code == OnyxErrorCode.CREDENTIAL_INVALID:
            raise OnyxError(
                OnyxErrorCode.CREDENTIAL_INVALID,
                "The Deep X hub did not accept the install token. Try again later.",
            )
        raise


def _store_or_roll_back(
    db_session: Session,
    user: User,
    storage_file_id: str,
    title: str,
    hub_artifact: HubArtifact,
) -> PublishedArtifactSnapshot:
    try:
        artifact = create_published_artifact(
            db_session,
            user_id=user.id,
            file_id=storage_file_id,
            hub_artifact_id=hub_artifact.id,
            url=hub_artifact.url,
            title=title,
            delete_token=hub_artifact.delete_token,
            expires_at=hub_artifact.expires_at,
        )
    except Exception:
        db_session.rollback()
        logger.exception("Could not save a published artifact. Deleting it.")
        try:
            delete_artifact_from_hub(
                DEEPX_HUB_URL, hub_artifact.id, hub_artifact.delete_token
            )
        except OnyxError as e:
            logger.warning("Could not delete the orphan hub artifact: %s", e.detail)
        raise OnyxError(
            OnyxErrorCode.INTERNAL_ERROR, "Could not save the published link."
        )
    return PublishedArtifactSnapshot.from_model(artifact)


def publish_chat_html_file(
    file_id: str,
    title: str,
    expires_in_days: int,
    user: User,
    db_session: Session,
) -> PublishedArtifactSnapshot:
    """Publish the HTML file and save the link. Raises `OnyxError` on failure.

    The caller validates `title` and `expires_in_days`
    (`PublishArtifactRequest`)."""
    storage_file_id, html = load_accessible_html_file(file_id, user, db_session)
    credential = resolve_hub_credential(user, db_session)
    hub_artifact = _publish_with_credential(credential, title, html, expires_in_days)
    return _store_or_roll_back(db_session, user, storage_file_id, title, hub_artifact)
