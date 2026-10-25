"use client";

import { useTranslations } from "next-intl";
import { ADMIN_ROUTES } from "@/lib/admin-routes";
import PublishedLinksSettings from "@/app/app/settings/published-links/PublishedLinksSettings";
import PersonalSettingsPage from "@/views/admin/PersonalSettingsPage";

export default function Page() {
  const t = useTranslations("sidebar.adminNav.items");
  return (
    <PersonalSettingsPage
      route={ADMIN_ROUTES.PUBLISHED_LINKS}
      title={t("publishedLinks.label")}
    >
      <PublishedLinksSettings />
    </PersonalSettingsPage>
  );
}
