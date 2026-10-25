import SkillEditorPage from "@/views/SkillEditorPage";

interface SettingsSkillsEditPageProps {
  params: Promise<{ id: string }>;
}

export default async function SettingsSkillsEditPage({
  params,
}: SettingsSkillsEditPageProps) {
  const { id } = await params;
  return <SkillEditorPage skillId={id} library="userSettings" />;
}
