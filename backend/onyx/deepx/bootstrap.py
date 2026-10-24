"""Deep X engine bootstrap. The single-tenant api_server runs it at startup."""

from collections.abc import Callable

from sqlalchemy.orm import Session

from onyx.deepx.presets_bootstrap import (
    create_missing_preset_assistants,
    fetch_deepx_presets,
)
from onyx.deepx.route_bootstrap import (
    ensure_deepx_route_image_generation,
    ensure_deepx_route_llm_provider,
)
from onyx.utils.logger import setup_logger

logger = setup_logger()


def run_deepx_bootstrap(
    db_session: Session,
    route_api_key: str | None,
    presets_url: str | None,
) -> None:
    """Run each configured bootstrap step. A failed step only logs a warning."""
    if route_api_key:
        key = route_api_key
        _run_step(
            db_session,
            "Deep X Route LLM provider",
            lambda: ensure_deepx_route_llm_provider(db_session, key),
            log_traceback=False,
        )
        _run_step(
            db_session,
            "Deep X Route image generation",
            lambda: ensure_deepx_route_image_generation(db_session, key),
            log_traceback=False,
        )

    if presets_url:
        url = presets_url

        def _presets_step() -> None:
            presets = fetch_deepx_presets(url)
            if presets is not None:
                create_missing_preset_assistants(db_session, presets)

        _run_step(db_session, "Deep X presets", _presets_step, log_traceback=True)


def _run_step(
    db_session: Session,
    label: str,
    step: Callable[[], object],
    log_traceback: bool,
) -> None:
    try:
        step()
    except Exception as e:
        db_session.rollback()
        if log_traceback:
            logger.exception("%s bootstrap failed.", label)
        else:
            # Only the type: the message of an error near the API key can quote it.
            logger.warning("%s bootstrap failed (%s).", label, type(e).__name__)
