"""HTTP client for the Deep X hub artifact API.

Never log the Deep X Route API key, an install token or a delete token.
"""

import datetime
from collections.abc import Callable
from urllib.parse import quote

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.utils.logger import setup_logger

logger = setup_logger()

# (connect, read) seconds. The publish body is at most 4 MiB.
HUB_PUBLISH_TIMEOUT_SECONDS: tuple[float, float] = (5.0, 30.0)
HUB_DELETE_TIMEOUT_SECONDS: tuple[float, float] = (5.0, 15.0)
HUB_INSTALL_TIMEOUT_SECONDS: tuple[float, float] = (5.0, 15.0)
INSTALL_TOKEN_PREFIX = "dxi_"
DELETE_TOKEN_HEADER = "X-Delete-Token"


class HubArtifact(BaseModel):
    """The hub response to a publish (`POST /api/artifacts`)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    expires_at: datetime.datetime = Field(alias="expiresAt")
    delete_token: str = Field(alias="deleteToken", min_length=1)


class HubInstall(BaseModel):
    """The hub response to an install registration (`POST /api/installs`)."""

    token: str = Field(min_length=len(INSTALL_TOKEN_PREFIX) + 1)


def _hub_error(response: requests.Response) -> OnyxError:
    status = response.status_code
    if status == 401:
        return OnyxError(
            OnyxErrorCode.CREDENTIAL_INVALID,
            "The Deep X hub did not accept the Deep X Route API key. "
            "Ask an admin to update the key of the Deep X Route provider.",
        )
    if status == 413:
        return OnyxError(
            OnyxErrorCode.PAYLOAD_TOO_LARGE,
            "The file is too large to publish. The limit is 4 MiB.",
        )
    if status == 429:
        return OnyxError(
            OnyxErrorCode.RATE_LIMITED,
            "You published too many files today. Try again tomorrow.",
        )
    if status >= 500:
        return OnyxError(
            OnyxErrorCode.BAD_GATEWAY,
            "The Deep X hub is not available. Try again later.",
        )
    return OnyxError(
        OnyxErrorCode.BAD_GATEWAY,
        f"The Deep X hub refused the request (HTTP {status}).",
    )


def _send(request: Callable[[], requests.Response]) -> requests.Response:
    try:
        return request()
    except requests.Timeout:
        raise OnyxError(
            OnyxErrorCode.GATEWAY_TIMEOUT,
            "The Deep X hub did not answer in time. Try again later.",
        )
    except requests.RequestException as e:
        # Only the type: the message can quote request headers.
        logger.warning("Deep X hub request failed (%s).", type(e).__name__)
        raise OnyxError(
            OnyxErrorCode.BAD_GATEWAY,
            "Could not connect to the Deep X hub. Try again later.",
        )


def publish_html_to_hub(
    hub_url: str,
    api_key: str,
    title: str,
    html: str,
    expires_in_days: int,
) -> HubArtifact:
    response = _send(
        lambda: requests.post(
            f"{hub_url}/api/artifacts",
            json={"title": title, "html": html, "expiresInDays": expires_in_days},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=HUB_PUBLISH_TIMEOUT_SECONDS,
        )
    )
    if response.status_code not in (200, 201):
        raise _hub_error(response)
    try:
        return HubArtifact.model_validate(response.json())
    except (ValueError, ValidationError):
        logger.warning("The Deep X hub sent a publish response that is not valid.")
        raise OnyxError(
            OnyxErrorCode.BAD_GATEWAY,
            "The Deep X hub sent a response that is not valid.",
        )


def register_install_with_hub(hub_url: str) -> str:
    """Register this Onyx install with the hub and return its install token.

    The hub limits new installs per IP and day (429)."""
    response = _send(
        lambda: requests.post(
            f"{hub_url}/api/installs",
            json={},
            timeout=HUB_INSTALL_TIMEOUT_SECONDS,
        )
    )
    if response.status_code == 429:
        raise OnyxError(
            OnyxErrorCode.RATE_LIMITED,
            "The Deep X hub refused to register this install today. "
            "Try again tomorrow.",
        )
    if response.status_code not in (200, 201):
        raise _hub_error(response)
    try:
        install = HubInstall.model_validate(response.json())
    except (ValueError, ValidationError):
        install = None
    if install is None or not install.token.startswith(INSTALL_TOKEN_PREFIX):
        logger.warning("The Deep X hub sent an install response that is not valid.")
        raise OnyxError(
            OnyxErrorCode.BAD_GATEWAY,
            "The Deep X hub sent a response that is not valid.",
        )
    return install.token


def delete_artifact_from_hub(
    hub_url: str, hub_artifact_id: str, delete_token: str
) -> None:
    """Delete the artifact on the hub. An artifact the hub does not know
    (404) or that expired (410) counts as deleted."""
    artifact_path = quote(hub_artifact_id, safe="")
    response = _send(
        lambda: requests.delete(
            f"{hub_url}/api/artifacts/{artifact_path}",
            headers={DELETE_TOKEN_HEADER: delete_token},
            timeout=HUB_DELETE_TIMEOUT_SECONDS,
        )
    )
    if response.status_code in (200, 204, 404, 410):
        return
    if response.status_code == 403:
        raise OnyxError(
            OnyxErrorCode.BAD_GATEWAY,
            "The Deep X hub did not accept the delete token. "
            "The link stays public until it expires.",
        )
    raise _hub_error(response)
