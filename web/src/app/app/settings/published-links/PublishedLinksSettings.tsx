"use client";

import { useEffect, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { IconLoader } from "@opal/loaders";
import {
  Button,
  Card,
  CopyButton,
  Divider,
  EmptyMessageCard,
  Text,
} from "@opal/components";
import { toast } from "@opal/layouts";
import { SvgExternalLink, SvgTrash } from "@opal/icons";
import { Section } from "@/layouts/general-layouts";
import { usePublishedArtifacts } from "@/lib/publishedArtifacts/hooks";
import { unpublishArtifact } from "@/lib/publishedArtifacts/svc";
import type { PublishedArtifact } from "@/lib/publishedArtifacts/types";

interface PublishedLinkRowProps {
  artifact: PublishedArtifact;
  onUnpublished: () => void;
}

function PublishedLinkRow({ artifact, onUnpublished }: PublishedLinkRowProps) {
  const t = useTranslations("settings.publishedLinks");
  const format = useFormatter();
  const [pending, setPending] = useState(false);

  async function handleUnpublish() {
    setPending(true);
    try {
      await unpublishArtifact(artifact.id);
      toast.success(t("unpublishedToast.message"));
      onUnpublished();
    } catch (e) {
      toast.error(
        (e instanceof Error && e.message) || t("unpublishError.message")
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <Section
      flexDirection="row"
      justifyContent="between"
      alignItems="center"
      width="full"
      gap={1}
    >
      <Section gap={0.25} alignItems="start" justifyContent="start">
        <Text font="main-ui-action" color="text-04">
          {artifact.title}
        </Text>
        <Text font="secondary-body" color="text-03">
          {artifact.url}
        </Text>
        <Text font="secondary-body" color="text-03">
          {t("expires.label", {
            date: format.dateTime(new Date(artifact.expires_at), {
              dateStyle: "medium",
              timeStyle: "short",
            }),
          })}
        </Text>
      </Section>
      <Section flexDirection="row" width="fit" gap={0.25}>
        <CopyButton
          size="sm"
          getCopyText={() => artifact.url}
          tooltip={t("copyButton.tooltip")}
        />
        <Button
          size="sm"
          prominence="tertiary"
          icon={SvgExternalLink}
          tooltip={t("openButton.tooltip")}
          href={artifact.url}
          target="_blank"
        />
        <Button
          size="sm"
          variant="danger"
          prominence="secondary"
          icon={pending ? IconLoader : SvgTrash}
          disabled={pending}
          onClick={() => void handleUnpublish()}
        >
          {t("unpublishButton.label")}
        </Button>
      </Section>
    </Section>
  );
}

export default function PublishedLinksSettings() {
  const t = useTranslations("settings.publishedLinks");
  const { data, error, isLoading, mutate } = usePublishedArtifacts();

  useEffect(() => {
    if (error) console.error("Failed to load published links", error);
  }, [error]);

  return (
    <Section gap={0.75} justifyContent="start">
      <Section gap={0.25} alignItems="start" justifyContent="start">
        <Text font="heading-h3">{t("header.title")}</Text>
        <Text font="main-ui-body" color="text-03">
          {t("header.description")}
        </Text>
      </Section>

      {isLoading ? (
        <Card border="solid" rounding={4}>
          <Section justifyContent="center" alignItems="center" width="full">
            <IconLoader />
          </Section>
        </Card>
      ) : error || !data ? (
        <EmptyMessageCard sizePreset="main-ui" title={t("loadError.title")} />
      ) : data.length === 0 ? (
        <EmptyMessageCard
          sizePreset="main-ui"
          title={t("empty.title")}
          description={t("empty.description")}
        />
      ) : (
        <Card border="solid" rounding={4}>
          <Section alignItems="stretch" height="fit" gap={0.75}>
            {data.map((artifact, index) => (
              <Section key={artifact.id} alignItems="stretch" gap={0.75}>
                {index > 0 && <Divider />}
                <PublishedLinkRow
                  artifact={artifact}
                  onUnpublished={() => void mutate()}
                />
              </Section>
            ))}
          </Section>
        </Card>
      )}
    </Section>
  );
}
