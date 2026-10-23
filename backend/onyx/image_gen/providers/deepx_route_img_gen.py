"""Deep X Route image generation through its asynchronous media job API.

Each image is one job: (upload the reference image), submit, poll until a
terminal state, then download the asset bytes.
"""

from __future__ import annotations

import base64
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ConfigDict

from onyx.image_gen.exceptions import (
    ImageProviderCredentialsError,
    ImageProviderError,
)
from onyx.image_gen.interfaces import (
    ImageGenerationProvider,
    ImageGenerationProviderCredentials,
    ImageShape,
    ReferenceImage,
)
from onyx.llm.well_known_providers.constants import (
    DEEPX_ROUTE_API_BASE,
    DEEPX_ROUTE_PROVIDER_NAME,
)
from onyx.tracing.flows import LLMFlow
from onyx.tracing.llm_utils import traced_llm_call
from onyx.utils.logger import setup_logger

if TYPE_CHECKING:
    from onyx.image_gen.interfaces import ImageGenerationResponse

logger = setup_logger()

DXR_IMAGE_MODEL = "dxr-image"
DXR_IMAGE_PRO_MODEL = "dxr-image-pro"
DXR_EDIT_MODEL = "dxr-edit"

# Deep X Route writes image sizes as `W*H`.
_SQUARE_SIZE = "1024*1024"
_PORTRAIT_SIZE = "1080*1920"
_LANDSCAPE_SIZE = "1920*1080"
_EDIT_SIZE = "auto"

_TEXT_TO_IMAGE_SIZES: dict[ImageShape, str] = {
    ImageShape.SQUARE: _SQUARE_SIZE,
    ImageShape.PORTRAIT: _PORTRAIT_SIZE,
    ImageShape.LANDSCAPE: _LANDSCAPE_SIZE,
}

MODEL_SIZES: dict[str, dict[ImageShape, str]] = {
    DXR_IMAGE_MODEL: _TEXT_TO_IMAGE_SIZES,
    DXR_IMAGE_PRO_MODEL: _TEXT_TO_IMAGE_SIZES,
}
SUPPORTED_MODELS: frozenset[str] = frozenset({*MODEL_SIZES, DXR_EDIT_MODEL})

MAX_REFERENCE_IMAGES = 1
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_UPLOAD_MIME_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)

HTTP_TIMEOUT_SECONDS = 30.0
ASSET_DOWNLOAD_TIMEOUT_SECONDS = 120.0
JOB_POLL_INTERVAL_SECONDS = 3.0
JOB_TIMEOUT_SECONDS = 300.0
_ERROR_DETAIL_MAX_CHARS = 300
# The service answers 429 with Retry-After when its job slots are full. A retry
# with the same idempotency key cannot charge twice.
HTTP_TOO_MANY_REQUESTS = 429
BUSY_RETRY_DEFAULT_SECONDS = 5.0
BUSY_RETRY_MAX_WAIT_SECONDS = 60.0

JOB_STATE_COMPLETED = "completed"
JOB_STATE_FAILED = "failed"
JOB_STATE_CANCELLED = "cancelled"
JOB_STATE_REVIEW = "review"
TERMINAL_JOB_STATES: frozenset[str] = frozenset(
    {JOB_STATE_COMPLETED, JOB_STATE_FAILED, JOB_STATE_CANCELLED, JOB_STATE_REVIEW}
)


class DeepXRouteMediaJob(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    state: str
    error: str | dict[str, Any] | None = None


class DeepXRouteUpload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str


def shape_from_size(size: str) -> ImageShape:
    """Read the shape from an OpenAI-style `WxH` (or `W*H`) size string."""
    normalized = size.lower().replace("*", "x")
    width_text, _, height_text = normalized.partition("x")
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError:
        return ImageShape.SQUARE
    if width > height:
        return ImageShape.LANDSCAPE
    if height > width:
        return ImageShape.PORTRAIT
    return ImageShape.SQUARE


def resolve_deepx_route_size(model: str, size: str) -> str:
    """Map the size Onyx requests to one the Deep X Route model accepts."""
    if model == DXR_EDIT_MODEL:
        return _EDIT_SIZE
    sizes = MODEL_SIZES.get(model)
    if sizes is None:
        raise ValueError(f"Unsupported Deep X Route image model: '{model}'")
    return sizes[shape_from_size(size)]


def _retry_after_seconds(response: httpx.Response) -> float:
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return BUSY_RETRY_DEFAULT_SECONDS
    return min(max(seconds, 0.0), BUSY_RETRY_MAX_WAIT_SECONDS)


def _error_detail(response: httpx.Response) -> str:
    text = response.text.strip()
    return text[:_ERROR_DETAIL_MAX_CHARS] if text else response.reason_phrase


class DeepXRouteImageGenerationProvider(ImageGenerationProvider):
    def __init__(
        self,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self._api_key = api_key
        # Tests inject a mock transport; production uses httpx's default.
        self._transport = transport
        # Fixed server-side, whatever the stored row says, so the key only
        # ever reaches Deep X Route.
        self._api_base = DEEPX_ROUTE_API_BASE

    @classmethod
    def validate_credentials(
        cls,
        credentials: ImageGenerationProviderCredentials,
    ) -> bool:
        return bool(credentials.api_key and credentials.api_key.strip())

    @classmethod
    def _build_from_credentials(
        cls,
        credentials: ImageGenerationProviderCredentials,
    ) -> DeepXRouteImageGenerationProvider:
        if not credentials.api_key:
            raise ImageProviderCredentialsError("Deep X Route requires an API key")
        return cls(api_key=credentials.api_key.strip())

    @property
    def supports_reference_images(self) -> bool:
        return True

    @property
    def max_reference_images(self) -> int:
        return MAX_REFERENCE_IMAGES

    def check_connection(
        self,
        model: str,
        quality: str | None = None,  # noqa: ARG002 - no quality tiers
    ) -> None:
        """Check the key with `GET /v1/key`, which does not charge credit."""
        if model not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported Deep X Route image model: '{model}'")
        with self._client(HTTP_TIMEOUT_SECONDS) as client:
            self._send(client, "GET", "/key")

    def generate_image(
        self,
        prompt: str,
        model: str,
        size: str,
        n: int,
        # Deep X Route has no quality tiers or extra image options.
        quality: str | None = None,  # noqa: ARG002
        reference_images: list[ReferenceImage] | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> ImageGenerationResponse:
        from litellm.types.utils import ImageObject, ImageResponse

        if n < 1:
            raise ValueError("Image count must be at least 1")

        # Deep X Route has one edit model, so every edit request goes to it.
        job_model = DXR_EDIT_MODEL if reference_images else model
        if job_model == DXR_EDIT_MODEL and not reference_images:
            raise ValueError(
                f"Model '{DXR_EDIT_MODEL}' edits an existing image. Attach an image, "
                f"or use '{DXR_IMAGE_MODEL}' to make a new one."
            )
        job_size = resolve_deepx_route_size(job_model, size)
        if reference_images:
            self._validate_reference_images(reference_images)

        flow = LLMFlow.IMAGE_EDIT if reference_images else LLMFlow.IMAGE_GENERATION
        images: list[ImageObject] = []
        with traced_llm_call(
            flow=flow,
            model=job_model,
            provider=DEEPX_ROUTE_PROVIDER_NAME,
            image_count=n,
            input_messages=[{"role": "user", "content": prompt}],
        ):
            with self._client(HTTP_TIMEOUT_SECONDS) as client:
                input_image_ids = [
                    self._upload_image(client, image)
                    for image in reference_images or []
                ]
                for _ in range(n):
                    image_bytes = self._run_job(
                        client,
                        model=job_model,
                        prompt=prompt,
                        size=job_size,
                        input_image_ids=input_image_ids,
                    )
                    images.append(
                        ImageObject(
                            b64_json=base64.b64encode(image_bytes).decode("utf-8"),
                            revised_prompt=prompt,
                        )
                    )

        return ImageResponse(
            created=int(datetime.now().timestamp()),
            data=images,
        )

    def _validate_reference_images(
        self, reference_images: list[ReferenceImage]
    ) -> None:
        if len(reference_images) > MAX_REFERENCE_IMAGES:
            raise ValueError(
                f"Model '{DXR_EDIT_MODEL}' edits {MAX_REFERENCE_IMAGES} image at a time."
            )
        for image in reference_images:
            if image.mime_type not in ALLOWED_UPLOAD_MIME_TYPES:
                raise ValueError(
                    f"Deep X Route accepts PNG, JPEG or WebP images, not '{image.mime_type}'."
                )
            if len(image.data) > MAX_UPLOAD_BYTES:
                raise ValueError("Deep X Route accepts images of 10 MB or less.")

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(
            base_url=self._api_base,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=timeout,
            transport=self._transport,
        )

    def _send(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        retry_when_busy: bool = False,
        **request_kwargs: Any,
    ) -> httpx.Response:
        busy_deadline = time.monotonic() + BUSY_RETRY_MAX_WAIT_SECONDS
        while True:
            try:
                response = client.request(method, path, **request_kwargs)
            except httpx.HTTPError as e:
                raise ImageProviderError(
                    f"Could not reach Deep X Route ({type(e).__name__})."
                ) from e
            if not (
                retry_when_busy
                and response.status_code == HTTP_TOO_MANY_REQUESTS
                and time.monotonic() < busy_deadline
            ):
                break
            time.sleep(_retry_after_seconds(response))
        if response.status_code in (401, 403):
            raise ImageProviderCredentialsError(
                "Deep X Route rejected the API key. Check the key and its permissions."
            )
        if response.is_error:
            raise ImageProviderError(
                f"Deep X Route returned HTTP {response.status_code} for "
                f"{method} {path}: {_error_detail(response)}"
            )
        return response

    def _upload_image(self, client: httpx.Client, image: ReferenceImage) -> str:
        response = self._send(
            client,
            "POST",
            "/media/uploads",
            content=image.data,
            headers={
                "Content-Type": image.mime_type,
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
        return DeepXRouteUpload.model_validate(response.json()).id

    def _run_job(
        self,
        client: httpx.Client,
        model: str,
        prompt: str,
        size: str,
        input_image_ids: list[str],
    ) -> bytes:
        body: dict[str, Any] = {
            "idempotencyKey": str(uuid.uuid4()),
            "model": model,
            "prompt": prompt,
            "size": size,
        }
        if input_image_ids:
            body["input_images"] = input_image_ids
        response = self._send(
            client, "POST", "/media/jobs", retry_when_busy=True, json=body
        )
        job = DeepXRouteMediaJob.model_validate(response.json())
        job = self._wait_for_job(client, job)

        if job.state != JOB_STATE_COMPLETED:
            raise ImageProviderError(_terminal_state_message(job))

        asset = self._send(
            client,
            "GET",
            f"/media/jobs/{job.id}/asset",
            timeout=ASSET_DOWNLOAD_TIMEOUT_SECONDS,
        )
        if not asset.content:
            raise ImageProviderError(
                f"Deep X Route job {job.id} completed without image data."
            )
        return asset.content

    def _wait_for_job(
        self, client: httpx.Client, job: DeepXRouteMediaJob
    ) -> DeepXRouteMediaJob:
        deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
        while job.state not in TERMINAL_JOB_STATES:
            if time.monotonic() >= deadline:
                raise ImageProviderError(
                    f"Deep X Route job {job.id} did not finish within "
                    f"{int(JOB_TIMEOUT_SECONDS)} seconds (last state: {job.state})."
                )
            time.sleep(JOB_POLL_INTERVAL_SECONDS)
            response = self._send(client, "GET", f"/media/jobs/{job.id}")
            job = DeepXRouteMediaJob.model_validate(response.json())
            logger.debug("Deep X Route job %s state=%s", job.id, job.state)
        return job


def _terminal_state_message(job: DeepXRouteMediaJob) -> str:
    if job.state == JOB_STATE_REVIEW:
        return (
            f"Deep X Route is holding job {job.id} for review, so no image is "
            "available yet. Try a different prompt."
        )
    if job.state == JOB_STATE_CANCELLED:
        return f"Deep X Route cancelled job {job.id}."
    reason = f": {job.error}" if job.error else "."
    return (
        f"Deep X Route could not generate the image (job {job.id} {job.state}){reason}"
    )
