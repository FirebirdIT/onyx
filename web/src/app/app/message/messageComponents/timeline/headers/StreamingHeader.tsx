"use client";

import React from "react";
import { useTranslations } from "next-intl";
import { SvgFold, SvgExpand } from "@opal/icons";
import { Button, Text } from "@opal/components";
import { TextLoader } from "@opal/loaders";
import { useStreamingDuration } from "@/app/app/message/messageComponents/timeline/hooks/useStreamingDuration";
import { formatDurationSeconds } from "@opal/time";

export interface StreamingHeaderProps {
  headerText: string;
  collapsible: boolean;
  buttonTitle?: string;
  isExpanded: boolean;
  onToggle: () => void;
  streamingStartTime?: number;
  /** Tool processing duration from backend (freezes timer when available) */
  toolProcessingDuration?: number;
}

/** Header during streaming - shimmer text with current activity */
export const StreamingHeader = React.memo(function StreamingHeader({
  headerText,
  collapsible,
  buttonTitle,
  isExpanded,
  onToggle,
  streamingStartTime,
  toolProcessingDuration,
}: StreamingHeaderProps) {
  const t = useTranslations("chat.messages.timeline");
  // Use backend duration when available, otherwise continue live timer
  const elapsedSeconds = useStreamingDuration(
    toolProcessingDuration === undefined, // Stop updating when we have backend duration
    streamingStartTime,
    toolProcessingDuration
  );
  const showElapsedTime = streamingStartTime && elapsedSeconds > 0;

  return (
    <>
      <div className="px-(--timeline-header-text-padding-x) py-(--timeline-header-text-padding-y)">
        <TextLoader>{headerText}</TextLoader>
      </div>

      {!collapsible && showElapsedTime && (
        <Text font="secondary-mono" color="text-03">
          {formatDurationSeconds(elapsedSeconds)}
        </Text>
      )}

      {collapsible &&
        (buttonTitle ? (
          <Button
            prominence="tertiary"
            size="md"
            onClick={onToggle}
            rightIcon={isExpanded ? SvgFold : SvgExpand}
            aria-expanded={isExpanded}
          >
            {buttonTitle}
          </Button>
        ) : showElapsedTime ? (
          <Button
            prominence="tertiary"
            size="md"
            onClick={onToggle}
            rightIcon={isExpanded ? SvgFold : SvgExpand}
            aria-label={
              isExpanded
                ? t("collapseButton.ariaLabel")
                : t("expandButton.ariaLabel")
            }
            aria-expanded={isExpanded}
          >
            {formatDurationSeconds(elapsedSeconds)}
          </Button>
        ) : (
          <Button
            prominence="tertiary"
            size="md"
            onClick={onToggle}
            icon={isExpanded ? SvgFold : SvgExpand}
            aria-label={
              isExpanded
                ? t("collapseButton.ariaLabel")
                : t("expandButton.ariaLabel")
            }
            aria-expanded={isExpanded}
          />
        ))}
    </>
  );
});
