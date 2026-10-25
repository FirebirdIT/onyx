"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { useSWRConfig } from "swr";
import { Button, Card } from "@opal/components";
import { Content, Section } from "@opal/layouts";
import { SvgDeepXRoute } from "@opal/logos";
import { ADMIN_ROUTES } from "@/lib/admin-routes";
import { LLMProviderName } from "@/lib/languageModels/types";
import ProviderSetupModal from "@/sections/modals/languageModels/ProviderSetupModal";

/**
 * First run of the Deep X desktop engine, while no model is connected. One
 * clear next step (connect Deep X Route) replaces the team onboarding flow;
 * other providers stay one click away in Settings.
 */
export default function LocalFirstRun() {
  const t = useTranslations("onboarding.localFirstRun");
  const { mutate } = useSWRConfig();
  const [isModalOpen, setIsModalOpen] = useState(false);

  return (
    <Card padding={4} border="solid" data-testid="local-first-run">
      <Section gap={2} alignItems="stretch">
        <Content
          icon={SvgDeepXRoute}
          title={t("title")}
          description={t("description")}
          sizePreset="main-ui"
          variant="section"
        />
        <Section flexDirection="row" justifyContent="end" gap={1}>
          <Button prominence="tertiary" href={ADMIN_ROUTES.LLM_MODELS.path}>
            {t("otherProviders.label")}
          </Button>
          <Button onClick={() => setIsModalOpen(true)}>
            {t("connect.label")}
          </Button>
        </Section>
      </Section>
      <ProviderSetupModal
        providerKey={isModalOpen ? LLMProviderName.DEEPX_ROUTE : null}
        shouldMarkAsDefault
        onOpenChange={setIsModalOpen}
        onSuccess={async () => {
          // The chat page reads the provider lists, so refresh all of them.
          await mutate(
            (key) => typeof key === "string" && key.startsWith("/api/llm")
          );
        }}
      />
    </Card>
  );
}
