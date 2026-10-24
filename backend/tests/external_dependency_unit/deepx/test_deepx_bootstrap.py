"""Deep X startup bootstrap against a real Postgres schema made for this module.

The schema is new and empty, so the tests do not read or change the data of
the default schema. The Deep X Route catalog and the presets URL are mocked.
"""

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
import requests
from sqlalchemy import Table, delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from onyx.configs.constants import DEFAULT_PERSONA_ID
from onyx.db.engine.sql_engine import SqlEngine
from onyx.db.enums import LLMModelFlowType
from onyx.db.image_generation import get_all_image_generation_configs
from onyx.db.llm import fetch_default_model, fetch_existing_llm_providers
from onyx.db.models import (
    Base,
    ImageGenerationConfig,
    LLMProvider,
    Persona,
    Tool,
)
from onyx.deepx.bootstrap import run_deepx_bootstrap
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.server.manage.llm.models import DeepXRouteFinalModelResponse
from shared_configs.contextvars import CURRENT_TENANT_ID_CONTEXTVAR

FAKE_ROUTE_KEY = "sk-dxr-test-bootstrap-key"
PRESETS_URL = "https://hub.example.test/api/presets"
CATALOG_PATCH = "onyx.deepx.route_bootstrap.fetch_deepx_route_chat_models"
PRESETS_GET_PATCH = "onyx.deepx.presets_bootstrap.ssrf_safe_get"


_BOOTSTRAP_TABLES = (
    "llm_provider",
    "model_configuration",
    "llm_model_flow",
    "image_generation_config",
    "llm_provider__persona",
    "llm_provider__user_group",
    "persona",
    "tool",
    "persona__tool",
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
    schema_name = f"deepx_bootstrap_test_{uuid4().hex}"
    scoped_engine = engine.execution_options(schema_translate_map={None: schema_name})
    with scoped_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))
        connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
        Base.metadata.create_all(
            connection, tables=_tables_with_fk_targets(_BOOTSTRAP_TABLES)
        )
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
        session.execute(delete(ImageGenerationConfig))
        session.execute(delete(LLMProvider))
        session.execute(delete(Persona))
        session.execute(delete(Tool))
        session.commit()


def _catalog() -> list[DeepXRouteFinalModelResponse]:
    return [
        DeepXRouteFinalModelResponse(
            name=name,
            display_name=name.upper(),
            max_input_tokens=128_000,
            supports_image_input=name == "dxr-vision",
            supports_reasoning=False,
        )
        for name in ("dxr-lite", "dxr-pro", "dxr-vision")
    ]


def _presets_response(payload: Any) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(payload).encode()
    return response


def _preset(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} description",
        "system_prompt": f"You are {name}.",
        "task_prompt": "",
        "starter_messages": [{"name": "Start", "message": "Help me start."}],
    }


def _bootstrap_route(db_session: Session) -> None:
    run_deepx_bootstrap(db_session, route_api_key=FAKE_ROUTE_KEY, presets_url=None)


def test_route_key_creates_default_provider_and_image_generation(
    db_session: Session,
) -> None:
    with patch(CATALOG_PATCH, return_value=_catalog()) as catalog:
        _bootstrap_route(db_session)
        # A second startup changes nothing.
        _bootstrap_route(db_session)
    catalog.assert_called_once_with(FAKE_ROUTE_KEY)

    providers = fetch_existing_llm_providers(db_session, flow_type_filter=[])
    assert len(providers) == 1
    provider = providers[0]
    assert provider.provider == "deepx_route"
    assert provider.api_key is not None
    assert provider.api_key.get_value(apply_mask=False) == FAKE_ROUTE_KEY
    assert sorted(m.name for m in provider.model_configurations) == [
        "dxr-lite",
        "dxr-pro",
        "dxr-vision",
    ]

    default_model = fetch_default_model(db_session, LLMModelFlowType.CHAT)
    assert default_model is not None
    assert default_model.name == "dxr-pro"
    assert default_model.llm_provider_id == provider.id

    configs = get_all_image_generation_configs(db_session)
    assert len(configs) == 1
    assert configs[0].image_provider_id == "deepx_route_dxr_image"
    assert configs[0].is_default is True
    assert configs[0].model_configuration.name == "dxr-image"


def test_catalog_failure_uses_fallback_models(db_session: Session) -> None:
    error = OnyxError(OnyxErrorCode.VALIDATION_ERROR, "Could not reach Deep X Route")
    with patch(CATALOG_PATCH, side_effect=error):
        _bootstrap_route(db_session)

    default_model = fetch_default_model(db_session, LLMModelFlowType.CHAT)
    assert default_model is not None
    assert default_model.name == "dxr-pro"
    providers = fetch_existing_llm_providers(db_session, flow_type_filter=[])
    assert len(providers) == 1
    assert "dxr-lite" in {m.name for m in providers[0].model_configurations}


def test_existing_provider_and_image_config_are_kept(db_session: Session) -> None:
    with patch(CATALOG_PATCH, return_value=_catalog()):
        _bootstrap_route(db_session)
    provider_ids_before = {
        p.id for p in fetch_existing_llm_providers(db_session, flow_type_filter=[])
    }

    with patch(CATALOG_PATCH) as catalog:
        run_deepx_bootstrap(
            db_session, route_api_key="sk-dxr-other-key", presets_url=None
        )
    catalog.assert_not_called()

    providers = fetch_existing_llm_providers(db_session, flow_type_filter=[])
    assert {p.id for p in providers} == provider_ids_before
    stored_key = providers[0].api_key
    assert stored_key is not None
    assert stored_key.get_value(apply_mask=False) == FAKE_ROUTE_KEY
    assert len(get_all_image_generation_configs(db_session)) == 1


def test_presets_create_missing_assistants_only(db_session: Session) -> None:
    tool = Tool(name="DeepXTestTool", description="test tool", enabled=True)
    db_session.add(tool)
    db_session.flush()
    db_session.add(
        Persona(
            id=DEFAULT_PERSONA_ID,
            name="Assistant",
            description="default",
            builtin_persona=True,
            is_public=True,
            tools=[tool],
        )
    )
    db_session.add(
        Persona(name="Kept", description="admin version", system_prompt="mine")
    )
    db_session.add(Persona(name="Removed", description="gone", deleted=True))
    db_session.commit()

    payload = {
        "version": 1,
        "assistants": [
            _preset("Writer"),
            _preset("Kept"),
            _preset("Removed"),
            {"name": ""},
            _preset("Writer"),
        ],
    }
    with patch(PRESETS_GET_PATCH, return_value=_presets_response(payload)):
        run_deepx_bootstrap(db_session, route_api_key=None, presets_url=PRESETS_URL)
        run_deepx_bootstrap(db_session, route_api_key=None, presets_url=PRESETS_URL)

    personas = list(db_session.scalars(select(Persona)).all())
    assert sorted(p.name for p in personas) == [
        "Assistant",
        "Kept",
        "Removed",
        "Writer",
    ]

    writer = next(p for p in personas if p.name == "Writer")
    assert writer.description == "Writer description"
    assert writer.system_prompt == "You are Writer."
    assert writer.is_public is True
    assert writer.user_id is None
    assert writer.starter_messages is not None
    assert [m.message for m in writer.starter_messages] == ["Help me start."]
    assert [t.id for t in writer.tools] == [tool.id]

    kept = next(p for p in personas if p.name == "Kept")
    assert kept.description == "admin version"
    assert kept.system_prompt == "mine"
    removed = next(p for p in personas if p.name == "Removed")
    assert removed.deleted is True


@pytest.mark.parametrize(
    "get_kwargs",
    [
        {"side_effect": requests.ConnectTimeout("timed out")},
        {"return_value": _presets_response({"assistants": "not a list"})},
        {"return_value": _presets_response(["not", "an", "object"])},
    ],
)
def test_presets_failure_does_not_raise(
    db_session: Session, get_kwargs: dict[str, Any]
) -> None:
    with patch(PRESETS_GET_PATCH, **get_kwargs):
        run_deepx_bootstrap(db_session, route_api_key=None, presets_url=PRESETS_URL)
    assert list(db_session.scalars(select(Persona)).all()) == []


def test_unset_env_does_nothing(db_session: Session) -> None:
    with patch(CATALOG_PATCH) as catalog, patch(PRESETS_GET_PATCH) as presets_get:
        run_deepx_bootstrap(db_session, route_api_key=None, presets_url=None)
    catalog.assert_not_called()
    presets_get.assert_not_called()
    assert fetch_existing_llm_providers(db_session, flow_type_filter=[]) == []
