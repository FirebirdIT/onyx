import SkillEditorPage from "@/views/SkillEditorPage";

interface LocalSettingsSkillsEditPageProps {
  params: Promise<{ id: string }>;
}

export default async function LocalSettingsSkillsEditPage({
  params,
}: LocalSettingsSkillsEditPageProps) {
  const { id } = await params;
  return <SkillEditorPage skillId={id} library="localSettings" />;
}
