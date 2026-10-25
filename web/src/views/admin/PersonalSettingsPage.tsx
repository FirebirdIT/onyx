"use client";

import type { ReactNode } from "react";
import { SettingsLayouts } from "@opal/layouts";
import type { AdminRouteEntry } from "@/lib/admin-routes";

interface PersonalSettingsPageProps {
  route: AdminRouteEntry;
  title: string;
  children: ReactNode;
}

/**
 * Frame for a personal settings page in the Deep X desktop app, where the
 * admin area is the one Settings place. Same header and body as the admin
 * pages beside it.
 */
export default function PersonalSettingsPage({
  route,
  title,
  children,
}: PersonalSettingsPageProps) {
  return (
    <SettingsLayouts.Root>
      <SettingsLayouts.Header icon={route.icon} title={title} divider />
      <SettingsLayouts.Body>{children}</SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}
