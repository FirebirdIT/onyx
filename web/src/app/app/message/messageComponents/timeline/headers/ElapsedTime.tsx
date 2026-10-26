"use client";

import React from "react";
import { Text } from "@opal/components";
import { formatDurationSeconds } from "@opal/time";
import { useStreamingDuration } from "@/app/app/message/messageComponents/timeline/hooks/useStreamingDuration";

export interface ElapsedTimeProps {
  /** Timestamp (Date.now()) when the answer started streaming. */
  streamingStartTime?: number;
}

/** Live count of the seconds since the answer started. Renders nothing before the first second. */
export const ElapsedTime = React.memo(function ElapsedTime({
  streamingStartTime,
}: ElapsedTimeProps) {
  const elapsedSeconds = useStreamingDuration(true, streamingStartTime);
  if (!streamingStartTime || elapsedSeconds <= 0) return null;

  return (
    <Text font="secondary-mono" color="text-03" aria-live="off">
      {formatDurationSeconds(elapsedSeconds)}
    </Text>
  );
});
