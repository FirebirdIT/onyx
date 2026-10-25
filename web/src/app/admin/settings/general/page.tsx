"use client";

import { useTranslations } from "next-intl";
import { ADMIN_ROUTES } from "@/lib/admin-routes";
import { GeneralSettings } from "@/views/SettingsPage";
import PersonalSettingsPage from "@/views/admin/PersonalSettingsPage";

export default function Page() {
  const t = useTranslations("sidebar.adminNav.items");
  return (
    <PersonalSettingsPage
      route={ADMIN_ROUTES.PERSONAL_GENERAL}
      title={t("personalGeneral.label")}
    >
      <GeneralSettings />
    </PersonalSettingsPage>
  );
}
