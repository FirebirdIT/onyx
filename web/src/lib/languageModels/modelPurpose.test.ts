import { modelPurposeKey } from "@/lib/languageModels/modelPurpose";
import { LLMProviderName } from "@/lib/languageModels/types";
import en from "@/i18n/messages/en.json";

describe("modelPurposeKey", () => {
  it("names the purpose of a known Deep X Route model", () => {
    expect(
      modelPurposeKey({
        provider: LLMProviderName.DEEPX_ROUTE,
        modelName: "dxr-reason",
      })
    ).toBe("reason");
  });

  it("returns null for an unknown Deep X Route model", () => {
    expect(
      modelPurposeKey({
        provider: LLMProviderName.DEEPX_ROUTE,
        modelName: "dxr-new",
      })
    ).toBeNull();
  });

  it("returns null for the same model name from another provider", () => {
    expect(
      modelPurposeKey({ provider: "openai", modelName: "dxr-pro" })
    ).toBeNull();
  });

  it("has an English text for every key", () => {
    const texts: Record<string, string> = en.chat.modelSelector.modelPurpose;
    for (const name of [
      "dxr-pro",
      "dxr-lite",
      "dxr-core-plus",
      "dxr-core",
      "dxr-ultra",
      "dxr-reason",
      "dxr-vision",
    ]) {
      const key = modelPurposeKey({
        provider: LLMProviderName.DEEPX_ROUTE,
        modelName: name,
      });
      expect(key && texts[key]).toBeTruthy();
    }
  });
});
