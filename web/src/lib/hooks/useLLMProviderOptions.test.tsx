import { renderHook } from "@testing-library/react";
import { useLLMProviderOptions } from "@/lib/hooks/useLLMProviderOptions";

jest.mock("swr", () => ({
  __esModule: true,
  default: () => ({
    data: [
      { name: "typesafe", known_models: [] },
      { name: "zoom_ai", known_models: [] },
      { name: "openai", known_models: [] },
      { name: "deepx_route", known_models: [] },
    ],
    error: undefined,
    mutate: jest.fn(),
  }),
}));

it("offers only chat providers, with Deep X Route first", () => {
  const { result } = renderHook(() => useLLMProviderOptions());
  expect(
    result.current.llmProviderOptions?.map((provider) => provider.name)
  ).toEqual(["deepx_route", "openai"]);
});
