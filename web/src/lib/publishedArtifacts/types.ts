export interface PublishedArtifact {
  id: string;
  file_id: string;
  hub_artifact_id: string;
  url: string;
  title: string;
  expires_at: string;
  created_at: string;
}

export interface PublishArtifactParams {
  fileId: string;
  title: string;
  expiresInDays: number;
}

/** The expiry choices the publish modal offers, in days. */
export const PUBLISH_EXPIRY_DAYS = [1, 7, 30] as const;
export const DEFAULT_PUBLISH_EXPIRY_DAYS = 7;
export const MAX_PUBLISH_TITLE_LENGTH = 200;
