import { LLMProviderName, type LLMOption } from "@/lib/languageModels/types";

/**
 * Translation key, under `chat.modelSelector.modelPurpose`, of the short
 * "what to use it for" line of each Deep X Route chat model. The catalog
 * (`/v1/models`) has no descriptions, so the app keeps them.
 */
const DEEPX_ROUTE_MODEL_PURPOSE_KEYS = {
  "dxr-pro": "pro",
  "dxr-lite": "lite",
  "dxr-core-plus": "corePlus",
  "dxr-core": "core",
  "dxr-ultra": "ultra",
  "dxr-reason": "reason",
  "dxr-vision": "vision",
} as const;

export type ModelPurposeKey =
  (typeof DEEPX_ROUTE_MODEL_PURPOSE_KEYS)[keyof typeof DEEPX_ROUTE_MODEL_PURPOSE_KEYS];

function isDeepXRouteModel(
  modelName: string
): modelName is keyof typeof DEEPX_ROUTE_MODEL_PURPOSE_KEYS {
  return Object.hasOwn(DEEPX_ROUTE_MODEL_PURPOSE_KEYS, modelName);
}

/** The purpose translation key of a model, or null when the app has none. */
export function modelPurposeKey(
  option: Pick<LLMOption, "provider" | "modelName">
): ModelPurposeKey | null {
  if (option.provider !== LLMProviderName.DEEPX_ROUTE) return null;
  if (!isDeepXRouteModel(option.modelName)) return null;
  return DEEPX_ROUTE_MODEL_PURPOSE_KEYS[option.modelName];
}
