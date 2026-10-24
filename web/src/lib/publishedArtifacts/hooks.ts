"use client";

import useSWR from "swr";
import { errorHandlingFetcher, skipRetryOnAuthError } from "@/lib/fetcher";
import { SWR_KEYS } from "@/lib/swr-keys";
import type { PublishedArtifact } from "@/lib/publishedArtifacts/types";

/** The current user's published links that did not expire, newest first. */
export function usePublishedArtifacts() {
  return useSWR<PublishedArtifact[]>(
    SWR_KEYS.publishedArtifacts,
    errorHandlingFetcher,
    { revalidateOnFocus: false, onErrorRetry: skipRetryOnAuthError }
  );
}
