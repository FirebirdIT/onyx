"""Publishing chat HTML files to the Deep X hub, against a real Postgres schema
made for this module.

The schema is new and empty, so the tests do not read or change the data of
the default schema. The hub HTTP calls and the file bytes are mocked.
The `publish_html` chat tool tests use the same schema and mocks.
"""

import datetime
import json
from collections.abc import Generator
from io import BytesIO
from typing import IO, Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import requests
from sqlalchemy import Table, delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from onyx.configs.constants import KV_DEEPX_HUB_INSTALL_TOKEN_KEY, FileOrigin
from onyx.db.encrypted_kv_store import load_encrypted_kv, upsert_encrypted_kv
from onyx.db.engine.sql_engine import SqlEngine
from onyx.db.enums import AccountType
from onyx.db.file_record import FileRecordNotFoundError
from onyx.db.models import (
    Base,
    ChatSession,
    EncryptedJson,
    EncryptedKeyValueStore,
    EncryptedString,
    FileRecord,
    LLMProvider,
    PublishedArtifact,
    User,
)
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.file_store.utils import chat_image_gen_metadata
from onyx.key_value_store.interface import KvKeyNotFoundError, unwrap_str
from onyx.llm.constants import LlmProviderNames
from onyx.server.features.published_artifacts.api import (
    list_published_artifacts,
    publish_artifact,
    unpublish_artifact,
)
from onyx.server.features.published_artifacts.models import PublishArtifactRequest
from onyx.server.features.published_artifacts.service import MAX_PUBLISH_BYTES
from onyx.server.query_and_chat.placement import Placement
from onyx.server.query_and_chat.session_loading import create_publish_html_packets
from onyx.server.query_and_chat.streaming_models import CustomToolDelta
from onyx.tools.built_in_tools import BUILT_IN_TOOL_MAP, TOOL_NAME_TO_CLASS
from onyx.tools.models import ToolCallException
from onyx.tools.tool_implementations.publish_html.publish_html_tool import (
    PublishHtmlResult,
    PublishHtmlTool,
    extract_chat_file_id,
    summarize_publish_result,
)
from shared_configs.contextvars import CURRENT_TENANT_ID_CONTEXTVAR

API_MODULE = "onyx.server.features.published_artifacts.api"
SERVICE_MODULE = "onyx.server.features.published_artifacts.service"
HUB_MODULE = "onyx.deepx.hub_artifacts"
TOOL_MODULE = "onyx.tools.tool_implementations.publish_html.publish_html_tool"
HUB_URL = "https://hub.example.test"
FAKE_ROUTE_KEY = "sk-dxr-test-publish-key"
INSTALL_TOKEN = "dxi_" + "I" * 22 + "." + "1" * 64
NEW_INSTALL_TOKEN = "dxi_" + "N" * 22 + "." + "2" * 64
DELETE_TOKEN = "a" * 64
HTML = "<!doctype html><html><body><h1>Report</h1></body></html>"


class FakeFileStore:
    """Bytes from memory; records from the test schema."""

    def __init__(self, db_session: Session) -> None:
        self._db_session = db_session
        self.contents: dict[str, bytes] = {}

    def read_file_record(self, file_id: str) -> FileRecord:
        record = self._db_session.get(FileRecord, file_id)
        if record is None:
            raise FileRecordNotFoundError(file_id)
        return record

    def read_file(self, file_id: str, mode: str | None = None) -> IO[bytes]:  # noqa: ARG002
        return BytesIO(self.contents[file_id])


# The tables the publish path and the chat file access check read.
_TABLES = (
    "published_artifact",
    "file_record",
    "user_file",
    "chat_session",
    "chat_message",
    "persona",
    "persona__user_file",
    "persona__user",
    "persona__user_group",
    "user__user_group",
    "document",
    "document_by_connector_credential_pair",
    "connector",
    "oauth_account",
    "user__pinned_persona",
    "llm_provider",
    "llm_provider__persona",
    "llm_provider__user_group",
    "encrypted_key_value_store",
)


def _tables_with_fk_targets(names: tuple[str, ...]) -> list[Table]:
    """The named tables plus every table their foreign keys reach."""
    tables: dict[str, Table] = {}
    pending = [Base.metadata.tables[name] for name in names]
    while pending:
        table = pending.pop()
        if table.name in tables:
            continue
        tables[table.name] = table
        pending.extend(fk.column.table for fk in table.foreign_keys)
    return list(tables.values())


@pytest.fixture(scope="module")
def schema_engine() -> Generator[Engine, None, None]:
    SqlEngine.init_engine(pool_size=10, max_overflow=5)
    engine = SqlEngine.get_engine()
    schema_name = f"published_artifact_test_{uuid4().hex}"
    scoped_engine = engine.execution_options(schema_translate_map={None: schema_name})
    with scoped_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))
        connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
        Base.metadata.create_all(connection, tables=_tables_with_fk_targets(_TABLES))
    tenant_token = CURRENT_TENANT_ID_CONTEXTVAR.set(schema_name)
    try:
        yield scoped_engine
    finally:
        CURRENT_TENANT_ID_CONTEXTVAR.reset(tenant_token)
        with engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True))


@pytest.fixture
def db_session(schema_engine: Engine) -> Generator[Session, None, None]:
    with Session(schema_engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
        for model in (
            PublishedArtifact,
            FileRecord,
            ChatSession,
            LLMProvider,
            EncryptedKeyValueStore,
            User,
        ):
            session.execute(delete(model))
        session.commit()


@pytest.fixture
def file_store(db_session: Session) -> Generator[FakeFileStore, None, None]:
    store = FakeFileStore(db_session)
    with patch(f"{SERVICE_MODULE}.get_default_file_store", return_value=store):
        yield store


@pytest.fixture(autouse=True)
def hub_url() -> Generator[None, None, None]:
    with (
        patch(f"{SERVICE_MODULE}.DEEPX_HUB_URL", HUB_URL),
        patch(f"{API_MODULE}.DEEPX_HUB_URL", HUB_URL),
    ):
        yield


def _user(db_session: Session) -> User:
    user = User(
        id=uuid4(),
        email=f"publisher_{uuid4().hex[:8]}@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        account_type=AccountType.STANDARD,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _route_provider(db_session: Session) -> None:
    db_session.add(
        LLMProvider(
            name="Deep X Route",
            provider=LlmProviderNames.DEEPX_ROUTE,
            api_key=FAKE_ROUTE_KEY,
            is_public=True,
        )
    )
    db_session.commit()


def _generated_file(
    db_session: Session,
    store: FakeFileStore,
    owner: User,
    content: bytes = HTML.encode(),
    display_name: str = "report.html",
    file_type: str = "text/html",
) -> str:
    chat_session = ChatSession(id=uuid4(), user_id=owner.id, description="chat")
    db_session.add(chat_session)
    file_id = str(uuid4())
    db_session.add(
        FileRecord(
            file_id=file_id,
            display_name=display_name,
            file_origin=FileOrigin.CHAT_IMAGE_GEN,
            file_type=file_type,
            file_metadata=chat_image_gen_metadata(chat_session.id),
            bucket_name="test",
            object_key=file_id,
            file_size=len(content),
        )
    )
    db_session.commit()
    store.contents[file_id] = content
    return file_id


def _response(status: int, payload: dict[str, Any] | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode() if payload is not None else b""
    return response


def _hub_publish_response(
    artifact_id: str = "AbCdEfGhIjKlMnOpQrStUv",
) -> requests.Response:
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=7
    )
    return _response(
        201,
        {
            "id": artifact_id,
            "url": f"{HUB_URL}/a/{artifact_id}",
            "expiresAt": expires_at.isoformat(),
            "deleteToken": DELETE_TOKEN,
        },
    )


def _publish(db_session: Session, user: User, file_id: str, days: int = 7) -> Any:
    return publish_artifact(
        PublishArtifactRequest(
            file_id=file_id, title="  My report ", expires_in_days=days
        ),
        user=user,
        db_session=db_session,
    )


def test_publish_stores_artifact_with_encrypted_token(
    db_session: Session, file_store: FakeFileStore
) -> None:
    user = _user(db_session)
    _route_provider(db_session)
    file_id = _generated_file(db_session, file_store, user)

    with patch(
        f"{HUB_MODULE}.requests.post", return_value=_hub_publish_response()
    ) as post:
        snapshot = _publish(db_session, user, file_id, days=3)

    post.assert_called_once()
    assert post.call_args.args[0] == f"{HUB_URL}/api/artifacts"
    assert post.call_args.kwargs["headers"] == {
        "Authorization": f"Bearer {FAKE_ROUTE_KEY}"
    }
    assert post.call_args.kwargs["json"] == {
        "title": "My report",
        "html": HTML,
        "expiresInDays": 3,
    }
    assert post.call_args.kwargs["timeout"] is not None

    assert snapshot.title == "My report"
    assert snapshot.file_id == file_id
    assert snapshot.url == f"{HUB_URL}/a/AbCdEfGhIjKlMnOpQrStUv"

    stored = db_session.get(PublishedArtifact, snapshot.id)
    assert stored is not None
    assert stored.user_id == user.id
    assert stored.delete_token.get_value(apply_mask=False) == DELETE_TOKEN
    # The column uses the encrypted type of the codebase. The bytes are
    # ciphertext when an encryption key is set.
    assert isinstance(PublishedArtifact.__table__.c.delete_token.type, EncryptedString)


def test_file_the_user_cannot_download_is_not_published(
    db_session: Session, file_store: FakeFileStore
) -> None:
    """A file the user cannot download cannot be published (404, no hub call)."""
    owner = _user(db_session)
    other = _user(db_session)
    _route_provider(db_session)
    file_id = _generated_file(db_session, file_store, owner)

    with patch(f"{HUB_MODULE}.requests.post") as post:
        with pytest.raises(OnyxError) as exc:
            _publish(db_session, other, file_id)
        with pytest.raises(OnyxError) as missing:
            _publish(db_session, owner, "not-a-uuid")
    assert exc.value.error_code == OnyxErrorCode.NOT_FOUND
    assert missing.value.error_code == OnyxErrorCode.NOT_FOUND
    post.assert_not_called()


@pytest.mark.parametrize(
    "display_name,file_type,accepted",
    [
        ("page.htm", "application/octet-stream", True),
        ("page", "text/html; charset=utf-8", True),
        ("notes.txt", "text/plain", False),
        ("chart.png", "image/png", False),
    ],
)
def test_only_html_files_are_published(
    db_session: Session,
    file_store: FakeFileStore,
    display_name: str,
    file_type: str,
    accepted: bool,
) -> None:
    user = _user(db_session)
    _route_provider(db_session)
    file_id = _generated_file(
        db_session, file_store, user, display_name=display_name, file_type=file_type
    )
    with patch(
        f"{HUB_MODULE}.requests.post", return_value=_hub_publish_response()
    ) as post:
        if accepted:
            _publish(db_session, user, file_id)
        else:
            with pytest.raises(OnyxError) as exc:
                _publish(db_session, user, file_id)
            assert exc.value.error_code == OnyxErrorCode.INVALID_INPUT
    assert post.called is accepted


def test_file_over_limit_is_rejected(
    db_session: Session, file_store: FakeFileStore
) -> None:
    user = _user(db_session)
    _route_provider(db_session)
    file_id = _generated_file(
        db_session, file_store, user, content=b"a" * (MAX_PUBLISH_BYTES + 1)
    )
    with patch(f"{HUB_MODULE}.requests.post") as post:
        with pytest.raises(OnyxError) as exc:
            _publish(db_session, user, file_id)
    assert exc.value.error_code == OnyxErrorCode.PAYLOAD_TOO_LARGE
    post.assert_not_called()


@pytest.mark.parametrize(
    "hub_status,expected",
    [
        (401, OnyxErrorCode.CREDENTIAL_INVALID),
        (413, OnyxErrorCode.PAYLOAD_TOO_LARGE),
        (429, OnyxErrorCode.RATE_LIMITED),
        (500, OnyxErrorCode.BAD_GATEWAY),
        (503, OnyxErrorCode.BAD_GATEWAY),
        (400, OnyxErrorCode.BAD_GATEWAY),
    ],
)
def test_hub_errors_map_to_onyx_errors(
    db_session: Session,
    file_store: FakeFileStore,
    hub_status: int,
    expected: OnyxErrorCode,
) -> None:
    user = _user(db_session)
    _route_provider(db_session)
    file_id = _generated_file(db_session, file_store, user)
    with patch(
        f"{HUB_MODULE}.requests.post",
        return_value=_response(hub_status, {"error": "no"}),
    ):
        with pytest.raises(OnyxError) as exc:
            _publish(db_session, user, file_id)
    assert exc.value.error_code == expected
    assert FAKE_ROUTE_KEY not in exc.value.detail
    assert db_session.scalars(select(PublishedArtifact)).first() is None


def test_hub_timeout_maps_to_gateway_timeout(
    db_session: Session, file_store: FakeFileStore
) -> None:
    user = _user(db_session)
    _route_provider(db_session)
    file_id = _generated_file(db_session, file_store, user)
    with patch(f"{HUB_MODULE}.requests.post", side_effect=requests.Timeout()):
        with pytest.raises(OnyxError) as exc:
            _publish(db_session, user, file_id)
    assert exc.value.error_code == OnyxErrorCode.GATEWAY_TIMEOUT


def test_list_and_unpublish(db_session: Session, file_store: FakeFileStore) -> None:
    user = _user(db_session)
    other = _user(db_session)
    _route_provider(db_session)
    mine = _generated_file(db_session, file_store, user)
    theirs = _generated_file(db_session, file_store, other)

    with patch(
        f"{HUB_MODULE}.requests.post",
        side_effect=[
            _hub_publish_response("mine0000000000000000000"),
            _hub_publish_response("theirs00000000000000000"),
        ],
    ):
        published = _publish(db_session, user, mine)
        _publish(db_session, other, theirs)

    listed = list_published_artifacts(user=user, db_session=db_session)
    assert [a.id for a in listed] == [published.id]

    # Another user cannot unpublish it.
    with patch(f"{HUB_MODULE}.requests.delete") as hub_delete:
        with pytest.raises(OnyxError) as exc:
            unpublish_artifact(published.id, user=other, db_session=db_session)
    assert exc.value.error_code == OnyxErrorCode.NOT_FOUND
    hub_delete.assert_not_called()

    # A refused token keeps the row: the link is still public.
    with patch(f"{HUB_MODULE}.requests.delete", return_value=_response(403)):
        with pytest.raises(OnyxError):
            unpublish_artifact(published.id, user=user, db_session=db_session)
    assert db_session.get(PublishedArtifact, published.id) is not None

    with patch(
        f"{HUB_MODULE}.requests.delete", return_value=_response(204)
    ) as hub_delete:
        unpublish_artifact(published.id, user=user, db_session=db_session)
    assert hub_delete.call_args.args[0] == (
        f"{HUB_URL}/api/artifacts/mine0000000000000000000"
    )
    assert hub_delete.call_args.kwargs["headers"] == {"X-Delete-Token": DELETE_TOKEN}
    db_session.expire_all()
    assert db_session.get(PublishedArtifact, published.id) is None
    assert list_published_artifacts(user=user, db_session=db_session) == []


def test_expired_artifact_is_hidden_and_unpublished_without_hub(
    db_session: Session, file_store: FakeFileStore
) -> None:
    user = _user(db_session)
    _route_provider(db_session)
    file_id = _generated_file(db_session, file_store, user)
    with patch(f"{HUB_MODULE}.requests.post", return_value=_hub_publish_response()):
        published = _publish(db_session, user, file_id)

    stored = db_session.get(PublishedArtifact, published.id)
    assert stored is not None
    stored.expires_at = datetime.datetime.now(
        datetime.timezone.utc
    ) - datetime.timedelta(minutes=1)
    db_session.commit()

    assert list_published_artifacts(user=user, db_session=db_session) == []
    hub_delete = MagicMock()
    with patch(f"{HUB_MODULE}.requests.delete", hub_delete):
        unpublish_artifact(published.id, user=user, db_session=db_session)
    hub_delete.assert_not_called()


def test_request_validation() -> None:
    with pytest.raises(ValueError):
        PublishArtifactRequest(file_id="f", title="t", expires_in_days=0)
    with pytest.raises(ValueError):
        PublishArtifactRequest(file_id="f", title="t", expires_in_days=31)
    with pytest.raises(ValueError):
        PublishArtifactRequest(file_id="f", title="   ")
    assert PublishArtifactRequest(file_id="f", title="t").expires_in_days == 7


# --- Install token: publishing without a Deep X Route provider ---


class FakeHubPost:
    """Answers hub POSTs by path, in order, and records each call."""

    def __init__(
        self,
        installs: list[requests.Response],
        artifacts: list[requests.Response],
    ) -> None:
        self._responses = {"/api/installs": installs, "/api/artifacts": artifacts}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> requests.Response:
        path = url.removeprefix(HUB_URL)
        self.calls.append((path, kwargs))
        return self._responses[path].pop(0)

    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]

    def bearer_tokens(self) -> list[str]:
        return [
            kwargs["headers"]["Authorization"].removeprefix("Bearer ")
            for path, kwargs in self.calls
            if path == "/api/artifacts"
        ]


def _install_response(token: str = INSTALL_TOKEN) -> requests.Response:
    return _response(201, {"token": token})


def _stored_install_token() -> str | None:
    try:
        return unwrap_str(load_encrypted_kv(KV_DEEPX_HUB_INSTALL_TOKEN_KEY))
    except KvKeyNotFoundError:
        return None


def test_publish_without_route_provider_registers_install_once(
    db_session: Session, file_store: FakeFileStore
) -> None:
    user = _user(db_session)
    first = _generated_file(db_session, file_store, user)
    second = _generated_file(db_session, file_store, user)
    hub = FakeHubPost(
        installs=[_install_response()],
        artifacts=[
            _hub_publish_response("first00000000000000000"),
            _hub_publish_response("second0000000000000000"),
        ],
    )

    with patch(f"{HUB_MODULE}.requests.post", side_effect=hub):
        _publish(db_session, user, first)
        _publish(db_session, user, second)

    assert hub.paths() == ["/api/installs", "/api/artifacts", "/api/artifacts"]
    install_kwargs = hub.calls[0][1]
    assert install_kwargs["json"] == {}
    assert "headers" not in install_kwargs
    assert hub.bearer_tokens() == [INSTALL_TOKEN, INSTALL_TOKEN]
    assert _stored_install_token() == INSTALL_TOKEN
    # The token is in an encrypted column.
    assert isinstance(EncryptedKeyValueStore.__table__.c.value.type, EncryptedJson)


def test_refused_install_token_registers_again_once(
    db_session: Session, file_store: FakeFileStore
) -> None:
    user = _user(db_session)
    upsert_encrypted_kv(KV_DEEPX_HUB_INSTALL_TOKEN_KEY, {"value": INSTALL_TOKEN})
    file_id = _generated_file(db_session, file_store, user)
    hub = FakeHubPost(
        installs=[_install_response(NEW_INSTALL_TOKEN)],
        artifacts=[_response(401, {"error": "unknown"}), _hub_publish_response()],
    )

    with patch(f"{HUB_MODULE}.requests.post", side_effect=hub):
        snapshot = _publish(db_session, user, file_id)

    assert hub.paths() == ["/api/artifacts", "/api/installs", "/api/artifacts"]
    assert hub.bearer_tokens() == [INSTALL_TOKEN, NEW_INSTALL_TOKEN]
    assert _stored_install_token() == NEW_INSTALL_TOKEN
    assert db_session.get(PublishedArtifact, snapshot.id) is not None


def test_install_token_refused_twice_fails_without_a_loop(
    db_session: Session, file_store: FakeFileStore
) -> None:
    user = _user(db_session)
    upsert_encrypted_kv(KV_DEEPX_HUB_INSTALL_TOKEN_KEY, {"value": INSTALL_TOKEN})
    file_id = _generated_file(db_session, file_store, user)
    hub = FakeHubPost(
        installs=[_install_response(NEW_INSTALL_TOKEN)],
        artifacts=[_response(401), _response(401)],
    )

    with patch(f"{HUB_MODULE}.requests.post", side_effect=hub):
        with pytest.raises(OnyxError) as exc:
            _publish(db_session, user, file_id)

    assert exc.value.error_code == OnyxErrorCode.CREDENTIAL_INVALID
    assert "install token" in exc.value.detail
    assert NEW_INSTALL_TOKEN not in exc.value.detail
    assert hub.paths() == ["/api/artifacts", "/api/installs", "/api/artifacts"]


def test_route_key_401_does_not_register_an_install(
    db_session: Session, file_store: FakeFileStore
) -> None:
    user = _user(db_session)
    _route_provider(db_session)
    file_id = _generated_file(db_session, file_store, user)
    hub = FakeHubPost(installs=[], artifacts=[_response(401)])

    with patch(f"{HUB_MODULE}.requests.post", side_effect=hub):
        with pytest.raises(OnyxError) as exc:
            _publish(db_session, user, file_id)

    assert exc.value.error_code == OnyxErrorCode.CREDENTIAL_INVALID
    assert hub.paths() == ["/api/artifacts"]
    assert _stored_install_token() is None


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (429, None, OnyxErrorCode.RATE_LIMITED),
        (503, None, OnyxErrorCode.BAD_GATEWAY),
        (201, {"token": "not-an-install-token"}, OnyxErrorCode.BAD_GATEWAY),
    ],
)
def test_install_registration_errors(
    db_session: Session,
    file_store: FakeFileStore,
    status: int,
    payload: dict[str, Any] | None,
    expected: OnyxErrorCode,
) -> None:
    user = _user(db_session)
    file_id = _generated_file(db_session, file_store, user)
    hub = FakeHubPost(installs=[_response(status, payload)], artifacts=[])

    with patch(f"{HUB_MODULE}.requests.post", side_effect=hub):
        with pytest.raises(OnyxError) as exc:
            _publish(db_session, user, file_id)

    assert exc.value.error_code == expected
    assert hub.paths() == ["/api/installs"]
    assert _stored_install_token() is None


# --- The publish_html chat tool ---

_PLACEMENT = Placement(turn_index=1, tab_index=0)


def _tool(user: User) -> tuple[PublishHtmlTool, MagicMock]:
    emitter = MagicMock()
    return PublishHtmlTool(tool_id=7, emitter=emitter, user=user), emitter


def _last_delta(emitter: MagicMock) -> CustomToolDelta:
    delta = emitter.emit.call_args_list[-1].args[0].obj
    assert isinstance(delta, CustomToolDelta)
    return delta


def test_tool_is_registered_and_available() -> None:
    assert BUILT_IN_TOOL_MAP[PublishHtmlTool.__name__] is PublishHtmlTool
    assert TOOL_NAME_TO_CLASS["publish_html"] is PublishHtmlTool
    assert PublishHtmlTool.is_available(MagicMock()) is True
    with patch(f"{TOOL_MODULE}.DEEPX_HUB_URL", ""):
        assert PublishHtmlTool.is_available(MagicMock()) is False


def test_tool_definition_tells_the_model_the_rules(db_session: Session) -> None:
    tool, _ = _tool(_user(db_session))
    definition = tool.tool_definition()
    assert definition.name == "publish_html"
    properties = definition.parameters["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"file_id", "title", "expires_in_days"}
    assert definition.parameters["required"] == ["file_id", "title"]
    description = definition.description
    assert "only when the user asks to publish" in description
    assert "Anyone who has the link" in description
    assert "Settings > Published Links" in description
    assert "expires" in description


@pytest.mark.parametrize(
    "value,expected",
    [
        ("abc-123", "abc-123"),
        ("  abc-123 ", "abc-123"),
        ("/api/chat/file/abc-123", "abc-123"),
        ("https://deepx.example/api/chat/file/abc-123?download=1", "abc-123"),
        ("[index.html](/api/chat/file/abc-123)", "abc-123"),
    ],
)
def test_extract_chat_file_id(value: str, expected: str) -> None:
    assert extract_chat_file_id(value) == expected


def test_tool_publishes_a_chat_file_link(
    db_session: Session, file_store: FakeFileStore
) -> None:
    user = _user(db_session)
    file_id = _generated_file(db_session, file_store, user, display_name="index.html")
    tool, emitter = _tool(user)
    hub = FakeHubPost(
        installs=[_install_response()], artifacts=[_hub_publish_response()]
    )

    with patch(f"{HUB_MODULE}.requests.post", side_effect=hub):
        response = tool.run(
            _PLACEMENT,
            None,
            file_id=f"https://deepx.example/api/chat/file/{file_id}",
            title=" Landing page ",
        )

    assert set(json.loads(response.llm_facing_response)) == {
        "url",
        "title",
        "expires_at",
    }
    result = PublishHtmlResult.model_validate_json(response.llm_facing_response)
    assert result.url == f"{HUB_URL}/a/AbCdEfGhIjKlMnOpQrStUv"
    assert result.title == "Landing page"
    assert hub.calls[-1][1]["json"]["expiresInDays"] == 7

    stored = db_session.scalars(select(PublishedArtifact)).one()
    assert stored.file_id == file_id
    assert stored.user_id == user.id

    delta = _last_delta(emitter)
    assert delta.response_type == "text"
    assert delta.data == summarize_publish_result(result)
    assert INSTALL_TOKEN not in str(delta.data)


def test_tool_reports_publish_errors_to_the_model(
    db_session: Session, file_store: FakeFileStore
) -> None:
    owner = _user(db_session)
    other = _user(db_session)
    file_id = _generated_file(db_session, file_store, owner)
    tool, emitter = _tool(other)

    with patch(f"{HUB_MODULE}.requests.post") as post:
        with pytest.raises(ToolCallException) as exc:
            tool.run(_PLACEMENT, None, file_id=file_id, title="Not mine")

    post.assert_not_called()
    assert exc.value.llm_facing_message == (
        "The page was not published. File not found"
    )
    assert "not published" in str(_last_delta(emitter).data)


def test_tool_rejects_invalid_arguments(db_session: Session) -> None:
    tool, _ = _tool(_user(db_session))
    with patch(f"{HUB_MODULE}.requests.post") as post:
        with pytest.raises(ToolCallException) as exc:
            tool.run(_PLACEMENT, None, file_id="f", title="t", expires_in_days=31)
    post.assert_not_called()
    assert "expires_in_days" in exc.value.llm_facing_message


def test_publish_html_replay_matches_live_text() -> None:
    result = PublishHtmlResult(
        url=f"{HUB_URL}/a/x",
        title="Page",
        expires_at=datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc),
    )
    packets = create_publish_html_packets(
        tool_name="Publish Web Page",
        tool_id=7,
        saved_response=result.model_dump_json(),
        turn_index=1,
    )
    delta = packets[1].obj
    assert isinstance(delta, CustomToolDelta)
    assert delta.data == summarize_publish_result(result)
    assert "2026-11-01" in str(delta.data)

    failed = create_publish_html_packets(
        tool_name="Publish Web Page",
        tool_id=7,
        saved_response="The page was not published. File not found",
        turn_index=1,
    )
    failed_delta = failed[1].obj
    assert isinstance(failed_delta, CustomToolDelta)
    assert failed_delta.data == "The page was not published. File not found"
