import SkillEditorPage from "@/views/SkillEditorPage";

interface LocalSettingsSkillsCreatePageProps {
  searchParams: Promise<{ draft?: string | string[] }>;
}

export default async function LocalSettingsSkillsCreatePage({
  searchParams,
}: LocalSettingsSkillsCreatePageProps) {
  const params = await searchParams;
  return (
    <SkillEditorPage
      draftId={typeof params.draft === "string" ? params.draft : undefined}
      library="localSettings"
    />
  );
}
