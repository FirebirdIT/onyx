"use client";

import React, { useMemo } from "react";
import { useTranslations } from "next-intl";
import { InputPasswordTypeIn } from "@opal/components";
import * as Yup from "yup";
import { FormikField } from "@/refresh-components/form/FormikField";
import { FormField } from "@/refresh-components/form/FormField";
import { InputSwitchField } from "@opal/form";
import { InputHorizontal } from "@opal/layouts";
import { ImageGenFormWrapper } from "@/views/admin/ImageGenerationPage/forms/ImageGenFormWrapper";
import {
  ImageGenFormBaseProps,
  ImageGenFormChildProps,
  ImageGenSubmitPayload,
} from "@/views/admin/ImageGenerationPage/forms/types";
import { ImageGenerationCredentials } from "@/views/admin/ImageGenerationPage/svc";
import {
  DEEPX_ROUTE_IMAGE_PROVIDER_NAME,
  ImageProvider,
} from "@/views/admin/ImageGenerationPage/constants";
import { useAdminLanguageModels } from "@/lib/languageModels/hooks";
import { LLMProviderName, LLMProviderView } from "@/lib/languageModels/types";

// The base URL is fixed server-side, so the admin only supplies a key.
interface DeepXRouteFormValues {
  api_key: string;
  use_llm_provider_key: boolean;
}

const initialValues: DeepXRouteFormValues = {
  api_key: "",
  use_llm_provider_key: false,
};

interface DeepXRouteFormFieldsProps extends ImageGenFormChildProps<DeepXRouteFormValues> {
  sourceProvider: LLMProviderView | undefined;
}

function DeepXRouteFormFields(props: DeepXRouteFormFieldsProps) {
  const t = useTranslations("admin.imageGeneration");
  const {
    formikProps,
    apiStatus,
    showApiMessage,
    errorMessage,
    disabled,
    isLoadingCredentials,
    resetApiState,
    imageProvider,
    sourceProvider,
  } = props;
  const useLlmProviderKey =
    !!sourceProvider && formikProps.values.use_llm_provider_key;

  const apiMessage = (
    <FormField.APIMessage
      state={apiStatus}
      messages={{
        loading: t("form.apiKeyTest.loading", { title: imageProvider.title }),
        success: t("form.apiKeyTest.success"),
        error: errorMessage || t("form.apiKeyTest.error"),
      }}
    />
  );

  return (
    <>
      {sourceProvider && (
        <InputHorizontal
          withLabel="use_llm_provider_key"
          title={t("form.reuseLlmProviderKey.label")}
          description={t("form.reuseLlmProviderKey.description", {
            name: sourceProvider.name || imageProvider.title,
          })}
        >
          <InputSwitchField
            name="use_llm_provider_key"
            disabled={disabled}
            onCheckedChange={() => resetApiState()}
          />
        </InputHorizontal>
      )}

      {useLlmProviderKey ? (
        showApiMessage && apiMessage
      ) : (
        <FormikField<string>
          name="api_key"
          render={(field, _helper, meta, state) => (
            <FormField
              name="api_key"
              state={apiStatus === "error" ? "error" : state}
              className="w-full"
            >
              <FormField.Label>{t("form.apiKey.label")}</FormField.Label>
              <FormField.Control>
                <InputPasswordTypeIn
                  {...field}
                  onChange={(e) => {
                    field.onChange(e);
                    resetApiState();
                  }}
                  // Fetched credentials become the initial value on edit.
                  existingValue={meta.initialValue}
                  placeholder={
                    isLoadingCredentials
                      ? t("form.loading.placeholder")
                      : t("form.apiKey.placeholder")
                  }
                  disabled={disabled}
                  error={apiStatus === "error"}
                />
              </FormField.Control>
              {showApiMessage ? (
                apiMessage
              ) : (
                <FormField.Message
                  messages={{
                    idle: t("form.deepxRouteApiKey.idle"),
                    error: meta.error,
                  }}
                />
              )}
            </FormField>
          )}
        />
      )}
    </>
  );
}

function getInitialValuesFromCredentials(
  credentials: ImageGenerationCredentials,
  _imageProvider: ImageProvider
): Partial<DeepXRouteFormValues> {
  return {
    api_key: credentials.api_key || "",
  };
}

function transformValues(
  values: DeepXRouteFormValues,
  imageProvider: ImageProvider,
  sourceProvider: LLMProviderView | undefined
): ImageGenSubmitPayload {
  const payload: ImageGenSubmitPayload = {
    modelName: imageProvider.model_name,
    imageProviderId: imageProvider.image_provider_id,
    provider: DEEPX_ROUTE_IMAGE_PROVIDER_NAME,
  };
  if (values.use_llm_provider_key && sourceProvider) {
    return { ...payload, sourceLlmProviderId: sourceProvider.id };
  }
  return { ...payload, apiKey: values.api_key };
}

export function DeepXRouteImageGenForm(props: ImageGenFormBaseProps) {
  const t = useTranslations("admin.imageGeneration");
  const { imageProvider, existingConfig } = props;
  const { llmProviders } = useAdminLanguageModels();

  // An existing Deep X Route language model provider can lend its key.
  const sourceProvider = useMemo(
    () =>
      llmProviders?.find(
        (provider) =>
          provider.provider === LLMProviderName.DEEPX_ROUTE &&
          !!provider.api_key
      ),
    [llmProviders]
  );

  const validationSchema = useMemo(
    () =>
      Yup.object().shape({
        use_llm_provider_key: Yup.boolean(),
        api_key: Yup.string().when("use_llm_provider_key", {
          is: true,
          then: (schema) => schema,
          otherwise: (schema) => schema.required(t("form.apiKey.required")),
        }),
      }),
    [t]
  );

  return (
    <ImageGenFormWrapper<DeepXRouteFormValues>
      {...props}
      title={
        existingConfig
          ? t("form.editHeader.title", { title: imageProvider.title })
          : t("form.connectHeader.title", { title: imageProvider.title })
      }
      description={t(imageProvider.descriptionKey)}
      initialValues={initialValues}
      validationSchema={validationSchema}
      getInitialValuesFromCredentials={getInitialValuesFromCredentials}
      transformValues={(values) =>
        transformValues(values, imageProvider, sourceProvider)
      }
    >
      {(childProps) => (
        <DeepXRouteFormFields {...childProps} sourceProvider={sourceProvider} />
      )}
    </ImageGenFormWrapper>
  );
}
