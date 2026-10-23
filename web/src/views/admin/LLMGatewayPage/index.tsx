"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { mutate } from "swr";
import { SWR_KEYS } from "@/lib/swr-keys";
import { SettingsLayouts, toast } from "@opal/layouts";
import { Content, InputHorizontal } from "@opal/layouts";
import { Card, InputSwitch, MessageCard } from "@opal/components";
import { Disabled } from "@opal/core";
import { useLLMGatewayAvailable, usePlans } from "@/lib/tiers/hooks";
import { Section } from "@/layouts/general-layouts";
import { ADMIN_ROUTES } from "@/lib/admin-routes";
import { useSettings } from "@/lib/settings/hooks";
import { updateAdminSettings } from "@/lib/settings/svc";
import { Tier } from "@/lib/tiers/types";

const route = ADMIN_ROUTES.LLM_GATEWAY;

export default function LLMGatewayPage() {
  const t = useTranslations("admin.llmGateway");
  const plans = usePlans();
  const router = useRouter();
  const settings = useSettings();
  const gatewayAvailable = useLLMGatewayAvailable();
  const enabled = settings.llm_gateway_enabled ?? true;

  async function save(checked: boolean) {
    try {
      await updateAdminSettings({ llm_gateway_enabled: checked });
      router.refresh();
      await mutate(SWR_KEYS.settings);
      toast.success(t("toasts.settingsUpdated"));
    } catch {
      toast.error(t("toasts.settingsUpdateFailed"));
    }
  }

  return (
    <SettingsLayouts.Root>
      <SettingsLayouts.Header
        icon={route.icon}
        title={t("header.title")}
        description={t("header.description")}
        divider
      />
      <SettingsLayouts.Body>
        <Section gap={3} alignItems="stretch">
          <Content
            title={t("access.title")}
            description={t("access.description")}
            sizePreset="main-content"
            variant="section"
          />
          <Card border="solid" rounding={4}>
            <Disabled
              disabled={!gatewayAvailable}
              allowClick={gatewayAvailable}
              tooltip={!gatewayAvailable ? t("tierTooltip") : undefined}
            >
              <InputHorizontal
                title={t("access.toggle.title")}
                tag={!gatewayAvailable ? plans[Tier.BUSINESS].tag : undefined}
                description={t("access.toggle.description")}
                disabled={!gatewayAvailable}
                withLabel
              >
                <InputSwitch
                  id="llm_gateway_enabled"
                  checked={gatewayAvailable ? enabled : false}
                  onCheckedChange={(checked) => {
                    void save(checked);
                  }}
                  disabled={!gatewayAvailable}
                />
              </InputHorizontal>
            </Disabled>
          </Card>
          <MessageCard variant="info" title={t("scopeNote.title")} />
        </Section>
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}
