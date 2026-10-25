import useSWR from "swr";
import {
  LLMProviderName,
  WellKnownLLMProviderDescriptor,
} from "@/lib/languageModels/types";
import { errorHandlingFetcher } from "@/lib/fetcher";
import { SWR_KEYS } from "@/lib/swr-keys";
import { getProvider } from "@/lib/languageModels/utils";

export function useLLMProviderOptions() {
  const { data, error, mutate } = useSWR<
    WellKnownLLMProviderDescriptor[] | undefined
  >(SWR_KEYS.wellKnownLlmProviders, errorHandlingFetcher, {
    revalidateOnFocus: false,
    revalidateIfStale: false,
    dedupingInterval: 60000,
  });

  return {
    // Deep X Route is the product's own model gateway, so it leads the list.
    llmProviderOptions: data
      ?.filter((provider) => !getProvider(provider.name).decisionOnly)
      .sort(
        (a, b) =>
          Number(b.name === LLMProviderName.DEEPX_ROUTE) -
          Number(a.name === LLMProviderName.DEEPX_ROUTE)
      ),
    isLoading: !error && !data,
    error,
    refetch: mutate,
  };
}
