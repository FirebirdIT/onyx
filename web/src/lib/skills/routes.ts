import type { Route } from "next";

/**
 * Where the skills library and its editor live. Craft keeps them under
 * `/craft/v1/skills`. Settings keeps them under the personal settings area,
 * so they work when Craft is off.
 */
type SkillLibraryPath =
  | "/craft/v1/skills"
  | "/app/settings/skills"
  | "/admin/settings/skills";

export interface SkillLibraryRoutes {
  /** Which product the library serves. Chat hides Craft-only copy. */
  context: "craft" | "chat";
  library: Route;
  create: Route;
  createFromDraft: (draftId: string) => Route;
  edit: (skillId: string) => `${SkillLibraryPath}/edit/${string}`;
}

export const CRAFT_SKILL_ROUTES: SkillLibraryRoutes = {
  context: "craft",
  library: "/craft/v1/skills",
  create: "/craft/v1/skills/new",
  createFromDraft: (draftId) => `/craft/v1/skills/new?draft=${draftId}`,
  edit: (skillId) => `/craft/v1/skills/edit/${skillId}`,
};

/** Personal settings in a server deployment. */
export const USER_SETTINGS_SKILL_ROUTES: SkillLibraryRoutes = {
  context: "chat",
  library: "/app/settings/skills",
  create: "/app/settings/skills/new",
  createFromDraft: (draftId) => `/app/settings/skills/new?draft=${draftId}`,
  edit: (skillId) => `/app/settings/skills/edit/${skillId}`,
};

/** The Settings area of the Deep X desktop app (local engine). */
export const LOCAL_SETTINGS_SKILL_ROUTES: SkillLibraryRoutes = {
  context: "chat",
  library: "/admin/settings/skills",
  create: "/admin/settings/skills/new",
  createFromDraft: (draftId) => `/admin/settings/skills/new?draft=${draftId}`,
  edit: (skillId) => `/admin/settings/skills/edit/${skillId}`,
};

/** A serializable name for a library, so server pages can pass it to client views. */
export type SkillLibraryId = "craft" | "userSettings" | "localSettings";

export const SKILL_LIBRARY_ROUTES: Record<SkillLibraryId, SkillLibraryRoutes> =
  {
    craft: CRAFT_SKILL_ROUTES,
    userSettings: USER_SETTINGS_SKILL_ROUTES,
    localSettings: LOCAL_SETTINGS_SKILL_ROUTES,
  };
