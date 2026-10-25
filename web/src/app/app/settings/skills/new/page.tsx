import SkillEditorPage from "@/views/SkillEditorPage";

interface SettingsSkillsCreatePageProps {
  searchParams: Promise<{ draft?: string | string[] }>;
}

export default async function SettingsSkillsCreatePage({
  searchParams,
}: SettingsSkillsCreatePageProps) {
  const params = await searchParams;
  return (
    <SkillEditorPage
      draftId={typeof params.draft === "string" ? params.draft : undefined}
      library="userSettings"
    />
  );
}
