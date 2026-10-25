"use client";

import { SvgOnyxOctagon, SvgPlus } from "@opal/icons";
import { Button } from "@opal/components";
import { SettingsLayouts } from "@opal/layouts";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useSettings } from "@/lib/settings/hooks";

import AgentsTable from "./AgentsPage/AgentsTable";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AgentsPage() {
  const t = useTranslations("admin.agents");
  const localEngine = useSettings().deepx_local_engine ?? false;

  return (
    <SettingsLayouts.Root>
      <SettingsLayouts.Header
        title={t("header.title")}
        description={
          localEngine ? t("header.localDescription") : t("header.description")
        }
        icon={SvgOnyxOctagon}
        actions={[
          <Button
            key="primary"
            href="/app/agents/create?admin=true"
            icon={SvgPlus}
          >
            {t("newAgentButton.label")}
          </Button>,
        ]}
      />
      <SettingsLayouts.Body>
        <AgentsTable />
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}
