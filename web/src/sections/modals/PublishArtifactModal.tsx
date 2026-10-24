"use client";

import { useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { useSWRConfig } from "swr";
import { IconLoader } from "@opal/loaders";
import {
  Button,
  CopyButton,
  InputSingleSelect,
  InputTypeIn,
  Modal,
  Text,
} from "@opal/components";
import { InputErrorText, InputVertical, toast } from "@opal/layouts";
import { SvgExternalLink, SvgGlobe, SvgTrash } from "@opal/icons";
import { Section } from "@/layouts/general-layouts";
import { SWR_KEYS } from "@/lib/swr-keys";
import {
  publishArtifact,
  unpublishArtifact,
} from "@/lib/publishedArtifacts/svc";
import {
  DEFAULT_PUBLISH_EXPIRY_DAYS,
  MAX_PUBLISH_TITLE_LENGTH,
  PUBLISH_EXPIRY_DAYS,
  type PublishedArtifact,
} from "@/lib/publishedArtifacts/types";

const TITLE_INPUT_ID = "publish-artifact-title";
const EXPIRY_INPUT_ID = "publish-artifact-expiry";

/** A default link title from a file name: the name without its extension. */
export function titleFromFileName(fileName: string): string {
  const withoutExtension = fileName.replace(/\.html?$/i, "").trim();
  return (withoutExtension || fileName).slice(0, MAX_PUBLISH_TITLE_LENGTH);
}

export interface PublishArtifactModalProps {
  /** A chat file id, the same id `GET /api/chat/file/{id}` takes. */
  fileId: string;
  fileName: string;
  onClose: () => void;
}

export default function PublishArtifactModal({
  fileId,
  fileName,
  onClose,
}: PublishArtifactModalProps) {
  const t = useTranslations("chat.modals.publishArtifact");
  const format = useFormatter();
  const { mutate } = useSWRConfig();
  const [title, setTitle] = useState(() => titleFromFileName(fileName));
  const [expiryDays, setExpiryDays] = useState(
    String(DEFAULT_PUBLISH_EXPIRY_DAYS)
  );
  const [published, setPublished] = useState<PublishedArtifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const trimmedTitle = title.trim();

  async function handlePublish() {
    setPending(true);
    setError(null);
    try {
      const artifact = await publishArtifact({
        fileId,
        title: trimmedTitle,
        expiresInDays: Number(expiryDays),
      });
      setPublished(artifact);
      void mutate(SWR_KEYS.publishedArtifacts);
    } catch (e) {
      setError((e instanceof Error && e.message) || t("publishError.message"));
    } finally {
      setPending(false);
    }
  }

  async function handleUnpublish(artifact: PublishedArtifact) {
    setPending(true);
    setError(null);
    try {
      await unpublishArtifact(artifact.id);
      void mutate(SWR_KEYS.publishedArtifacts);
      toast.success(t("unpublishedToast.message"));
      onClose();
    } catch (e) {
      setError(
        (e instanceof Error && e.message) || t("unpublishError.message")
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal open onOpenChange={(isOpen) => !isOpen && onClose()}>
      <Modal.Content width="sm" preventAccidentalClose={false}>
        <Modal.Header
          icon={SvgGlobe}
          title={published ? t("header.publishedTitle") : t("header.title")}
          description={published ? undefined : t("header.description")}
          onClose={onClose}
        />
        <Modal.Body alignItems="stretch">
          {published ? (
            <Section alignItems="stretch" gap={0.75} height="auto">
              <Text as="p" font="main-ui-body" color="text-03">
                {t("published.description", {
                  date: format.dateTime(new Date(published.expires_at), {
                    dateStyle: "medium",
                    timeStyle: "short",
                  }),
                })}
              </Text>
              <InputTypeIn
                aria-label={t("linkInput.copyTooltip")}
                variant="readOnly"
                value={published.url}
                rightChildren={
                  <Section flexDirection="row" width="fit" gap={0}>
                    <CopyButton
                      size="sm"
                      getCopyText={() => published.url}
                      tooltip={t("linkInput.copyTooltip")}
                    />
                    <Button
                      size="sm"
                      prominence="tertiary"
                      icon={SvgExternalLink}
                      tooltip={t("openButton.tooltip")}
                      href={published.url}
                      target="_blank"
                    />
                  </Section>
                }
              />
            </Section>
          ) : (
            <Section alignItems="stretch" gap={1} height="auto">
              <Text as="p" font="main-ui-body" color="status-warning-05">
                {t("publicWarning.message")}
              </Text>
              <InputVertical
                title={t("titleField.label")}
                withLabel={TITLE_INPUT_ID}
              >
                <InputTypeIn
                  id={TITLE_INPUT_ID}
                  value={title}
                  maxLength={MAX_PUBLISH_TITLE_LENGTH}
                  placeholder={t("titleField.placeholder")}
                  onChange={(event) => setTitle(event.target.value)}
                  variant={pending ? "disabled" : "primary"}
                />
              </InputVertical>
              <InputVertical
                title={t("expiryField.label")}
                withLabel={EXPIRY_INPUT_ID}
              >
                <InputSingleSelect
                  id={EXPIRY_INPUT_ID}
                  value={expiryDays}
                  onValueChange={setExpiryDays}
                  defaultOption={String(DEFAULT_PUBLISH_EXPIRY_DAYS)}
                  placeholder={t("expiryField.placeholder")}
                  options={PUBLISH_EXPIRY_DAYS.map((days) => ({
                    value: String(days),
                    title: t("expiryField.option", { days }),
                  }))}
                />
              </InputVertical>
            </Section>
          )}
          {error && <InputErrorText>{error}</InputErrorText>}
        </Modal.Body>
        <Modal.Footer>
          {published ? (
            <>
              <Button
                variant="danger"
                prominence="secondary"
                icon={pending ? IconLoader : SvgTrash}
                disabled={pending}
                onClick={() => void handleUnpublish(published)}
              >
                {pending
                  ? t("unpublishButton.pendingLabel")
                  : t("unpublishButton.label")}
              </Button>
              <Button onClick={onClose}>{t("doneButton.label")}</Button>
            </>
          ) : (
            <>
              <Button prominence="secondary" onClick={onClose}>
                {t("cancelButton.label")}
              </Button>
              <Button
                icon={pending ? IconLoader : SvgGlobe}
                disabled={pending || trimmedTitle.length === 0}
                onClick={() => void handlePublish()}
              >
                {pending
                  ? t("publishButton.pendingLabel")
                  : t("publishButton.label")}
              </Button>
            </>
          )}
        </Modal.Footer>
      </Modal.Content>
    </Modal>
  );
}
