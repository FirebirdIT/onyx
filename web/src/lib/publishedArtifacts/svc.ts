import type { ErrorResponseBody } from "@/lib/fetcher";
import { SWR_KEYS } from "@/lib/swr-keys";
import type {
  PublishArtifactParams,
  PublishedArtifact,
} from "@/lib/publishedArtifacts/types";

/** An error whose message is the backend `detail`, when it sent one. */
export class PublishedArtifactError extends Error {}

async function errorFromResponse(
  response: Response
): Promise<PublishedArtifactError> {
  const body: ErrorResponseBody = await response.json().catch(() => ({}));
  return new PublishedArtifactError(body.detail || body.message || "");
}

export async function publishArtifact(
  params: PublishArtifactParams
): Promise<PublishedArtifact> {
  const response = await fetch(SWR_KEYS.publishedArtifacts, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      file_id: params.fileId,
      title: params.title,
      expires_in_days: params.expiresInDays,
    }),
  });
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
  const artifact: PublishedArtifact = await response.json();
  return artifact;
}

export async function unpublishArtifact(artifactId: string): Promise<void> {
  const response = await fetch(
    `${SWR_KEYS.publishedArtifacts}/${encodeURIComponent(artifactId)}`,
    { method: "DELETE" }
  );
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
}
