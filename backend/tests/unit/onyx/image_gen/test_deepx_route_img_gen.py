import base64
import json
from collections.abc import Callable, Iterator
from unittest.mock import patch

import httpx
import pytest

from onyx.image_gen.exceptions import (
    ImageProviderCredentialsError,
    ImageProviderError,
)
from onyx.image_gen.factory import get_image_generation_provider
from onyx.image_gen.interfaces import (
    ImageGenerationProviderCredentials,
    ImageShape,
    ReferenceImage,
)
from onyx.image_gen.providers import deepx_route_img_gen as dxr
from onyx.image_gen.providers.deepx_route_img_gen import (
    DeepXRouteImageGenerationProvider,
    resolve_deepx_route_size,
    shape_from_size,
)
from onyx.llm.well_known_providers.constants import DEEPX_ROUTE_API_BASE

_API_KEY = "dxr-test-key"
_IMAGE_BYTES = b"\x89PNG fake image bytes"

Handler = Callable[[httpx.Request], httpx.Response]


class _FakeDeepXRoute:
    """Records requests and answers like the Deep X Route media API."""

    def __init__(self, poll_states: list[str], job_error: str | None = None):
        self.requests: list[httpx.Request] = []
        self.submitted_bodies: list[dict[str, object]] = []
        self._poll_states = poll_states
        self._job_error = job_error
        self._job_count = 0
        self._polls = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        if request.method == "POST" and path == "/media/uploads":
            return httpx.Response(200, json={"id": "upload-1", "bytes": 10})
        if request.method == "POST" and path == "/media/jobs":
            self._job_count += 1
            self.submitted_bodies.append(json.loads(request.content))
            return httpx.Response(
                200, json={"id": f"job-{self._job_count}", "state": "queued"}
            )
        if request.method == "GET" and path.endswith("/asset"):
            return httpx.Response(200, content=_IMAGE_BYTES)
        if request.method == "GET" and path.startswith("/media/jobs/"):
            state = self._poll_states[min(self._polls, len(self._poll_states) - 1)]
            self._polls += 1
            body: dict[str, object] = {"id": path.split("/")[-1], "state": state}
            if self._job_error:
                body["error"] = self._job_error
            return httpx.Response(200, json=body)
        if request.method == "GET" and path == "/key":
            return httpx.Response(200, json={"id": "key-1"})
        return httpx.Response(404, json={"error": "not found"})


def _provider() -> DeepXRouteImageGenerationProvider:
    provider = get_image_generation_provider(
        "deepx_route",
        ImageGenerationProviderCredentials(
            api_key=_API_KEY, api_base="https://attacker.example.com/v1"
        ),
    )
    assert isinstance(provider, DeepXRouteImageGenerationProvider)
    return provider


@pytest.fixture
def no_sleep() -> Iterator[None]:
    with patch.object(dxr.time, "sleep"):
        yield


def _provider_with(handler: Handler) -> DeepXRouteImageGenerationProvider:
    return DeepXRouteImageGenerationProvider(
        api_key=_API_KEY, transport=httpx.MockTransport(handler)
    )


def test_build_pins_api_base_and_limits_reference_images() -> None:
    provider = _provider()
    assert provider._api_base == DEEPX_ROUTE_API_BASE
    assert provider.supports_reference_images is True
    assert provider.max_reference_images == 1


def test_build_fails_without_api_key() -> None:
    with pytest.raises(ImageProviderCredentialsError):
        get_image_generation_provider(
            "deepx_route", ImageGenerationProviderCredentials(api_key="  ")
        )


@pytest.mark.parametrize(
    ("size", "shape"),
    [
        ("1024x1024", ImageShape.SQUARE),
        ("1792x1024", ImageShape.LANDSCAPE),
        ("1024x1536", ImageShape.PORTRAIT),
        ("1080*1920", ImageShape.PORTRAIT),
        ("not-a-size", ImageShape.SQUARE),
    ],
)
def test_shape_from_size(size: str, shape: ImageShape) -> None:
    assert shape_from_size(size) == shape


@pytest.mark.parametrize(
    ("model", "size", "expected"),
    [
        ("dxr-image", "1024x1024", "1024*1024"),
        ("dxr-image", "1792x1024", "1920*1080"),
        ("dxr-image-pro", "1024x1792", "1080*1920"),
        ("dxr-edit", "1792x1024", "auto"),
    ],
)
def test_resolve_deepx_route_size(model: str, size: str, expected: str) -> None:
    assert resolve_deepx_route_size(model, size) == expected


def test_resolve_size_rejects_unknown_model() -> None:
    with pytest.raises(ValueError):
        resolve_deepx_route_size("gpt-image-1", "1024x1024")


@pytest.mark.usefixtures("no_sleep")
def test_generate_submits_polls_and_downloads() -> None:
    fake = _FakeDeepXRoute(poll_states=["running", "completed"])
    provider = _provider_with(fake.handle)

    response = provider.generate_image(
        prompt="a lighthouse", model="dxr-image-pro", size="1792x1024", n=2
    )

    assert response.data is not None
    assert len(response.data) == 2
    for item in response.data:
        assert base64.b64decode(item.model_dump()["b64_json"]) == _IMAGE_BYTES
    assert len(fake.submitted_bodies) == 2
    first = fake.submitted_bodies[0]
    assert first["model"] == "dxr-image-pro"
    assert first["size"] == "1920*1080"
    assert first["prompt"] == "a lighthouse"
    assert "input_images" not in first
    # Each job carries its own idempotency key.
    assert first["idempotencyKey"] != fake.submitted_bodies[1]["idempotencyKey"]
    for request in fake.requests:
        assert request.url.host == "api.deepxroute.com"
        assert request.headers["Authorization"] == f"Bearer {_API_KEY}"


@pytest.mark.usefixtures("no_sleep")
def test_edit_uploads_reference_image() -> None:
    fake = _FakeDeepXRoute(poll_states=["completed"])
    provider = _provider_with(fake.handle)

    response = provider.generate_image(
        prompt="make the sky pink",
        model="dxr-image",
        size="1024x1024",
        n=1,
        reference_images=[ReferenceImage(data=b"source", mime_type="image/png")],
    )

    assert response.data is not None and len(response.data) == 1
    upload = fake.requests[0]
    assert upload.url.path == "/v1/media/uploads"
    assert upload.content == b"source"
    assert upload.headers["Content-Type"] == "image/png"
    assert upload.headers["Idempotency-Key"]
    body = fake.submitted_bodies[0]
    assert body["model"] == "dxr-edit"
    assert body["size"] == "auto"
    assert body["input_images"] == ["upload-1"]


def test_edit_model_without_reference_image_raises() -> None:
    with pytest.raises(ValueError, match="edits an existing image"):
        _provider().generate_image(prompt="x", model="dxr-edit", size="1024x1024", n=1)


def test_edit_rejects_unsupported_mime_type() -> None:
    with pytest.raises(ValueError, match="PNG, JPEG or WebP"):
        _provider().generate_image(
            prompt="x",
            model="dxr-edit",
            size="1024x1024",
            n=1,
            reference_images=[ReferenceImage(data=b"gif", mime_type="image/gif")],
        )


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("failed", "could not generate the image"),
        ("review", "holding job"),
        ("cancelled", "cancelled job"),
    ],
)
def test_terminal_failure_states_raise(state: str, message: str) -> None:
    fake = _FakeDeepXRoute(poll_states=[state], job_error="supplier error")
    provider = _provider_with(fake.handle)

    with pytest.raises(ImageProviderError, match=message):
        provider.generate_image(prompt="x", model="dxr-image", size="1024x1024", n=1)
    assert not any(r.url.path.endswith("/asset") for r in fake.requests)


@pytest.mark.usefixtures("no_sleep")
def test_job_timeout_raises() -> None:
    fake = _FakeDeepXRoute(poll_states=["running"])
    provider = _provider_with(fake.handle)
    # Every clock read jumps a third of the budget, so the loop ends.
    step = dxr.JOB_TIMEOUT_SECONDS / 3
    calls: list[float] = []

    def fake_monotonic() -> float:
        calls.append(step)
        return sum(calls)

    with patch.object(dxr.time, "monotonic", side_effect=fake_monotonic):
        with pytest.raises(ImageProviderError, match="did not finish within"):
            provider.generate_image(
                prompt="x", model="dxr-image", size="1024x1024", n=1
            )


def test_unauthorized_raises_credentials_error() -> None:
    provider = _provider_with(lambda _request: httpx.Response(401, json={}))

    with pytest.raises(ImageProviderCredentialsError):
        provider.generate_image(prompt="x", model="dxr-image", size="1024x1024", n=1)


def test_http_error_message_omits_api_key() -> None:
    provider = _provider_with(
        lambda _request: httpx.Response(400, text="size not allowed")
    )

    with pytest.raises(ImageProviderError) as exc_info:
        provider.generate_image(prompt="x", model="dxr-image", size="1024x1024", n=1)
    assert "size not allowed" in str(exc_info.value)
    assert _API_KEY not in str(exc_info.value)


def test_check_connection_reads_key_without_generating() -> None:
    fake = _FakeDeepXRoute(poll_states=["completed"])
    provider = _provider_with(fake.handle)

    provider.check_connection(model="dxr-edit")

    assert [r.url.path for r in fake.requests] == ["/v1/key"]


def test_check_connection_rejects_unknown_model() -> None:
    with pytest.raises(ValueError):
        _provider().check_connection(model="gpt-image-1")
