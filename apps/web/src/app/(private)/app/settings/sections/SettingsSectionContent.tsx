import type { SettingsSection } from "@/components/settings/SettingsDialogShell";
import type { SettingsDialogSectionWidth } from "@/components/settings/SettingsDialogSectionFrame";
import { SettingsDialogSectionFrame } from "@/components/settings/SettingsDialogSectionFrame";
import type { ReadingDefaultState } from "@/lib/reading-defaults";
import type { ProfileBffStatus } from "@/services/bff/profile";
import { AccountSection } from "./AccountSection";
import { PreferencesSection } from "./PreferencesSection";
import { SettingsSectionLayout } from "./SettingsSectionLayout";
import { SupportSection } from "./SupportSection";
import { UsageSection } from "./UsageSection";

export interface AccountData {
  nickname: string;
  displayFallback: string;
  phone: string | undefined;
  status: ProfileBffStatus;
  avatarText: string;
}

export interface PreferencesData extends ReadingDefaultState {
  canEdit: boolean;
}

/** Section metadata for Dialog mode (title, description, content width). */
const SECTION_META: Record<
  SettingsSection,
  { title: string; description: string; width: SettingsDialogSectionWidth }
> = {
  account: {
    title: "个人资料",
    description: "管理你的档案与登录状态。",
    width: "standard",
  },
  preferences: {
    title: "偏好",
    description: "设置外观与新阅读的默认方式。",
    width: "standard",
  },
  usage: {
    title: "用量与积分",
    description: "当前无需操作。",
    width: "standard",
  },
  support: {
    title: "支持",
    description: "提交反馈，查看处理进度。",
    width: "standard",
  },
};

interface SettingsSectionContentProps {
  mode?: "fallback";
  section?: SettingsSection;
  accountData?: AccountData;
  preferencesData?: PreferencesData;
}

function renderAccount(data: AccountData) {
  return (
    <AccountSection
      nickname={data.nickname}
      displayFallback={data.displayFallback}
      phone={data.phone}
      status={data.status}
      avatarText={data.avatarText}
    />
  );
}

function renderPreferences(data: PreferencesData) {
  return (
    <PreferencesSection
      readingGoal={data.readingGoal}
      readingVariant={data.readingVariant}
      canEdit={data.canEdit}
    />
  );
}

function renderUsage() {
  return <UsageSection />;
}

/**
 * Wraps section content in a `SettingsDialogSectionFrame` for Dialog mode.
 * Provides unified title, description, column width, and scroll boundary.
 */
function wrapInFrame(
  section: SettingsSection,
  content: React.ReactNode,
): React.ReactNode {
  const meta = SECTION_META[section];
  return (
    <SettingsDialogSectionFrame
      title={meta.title}
      description={meta.description}
      width={meta.width}
    >
      {content}
    </SettingsDialogSectionFrame>
  );
}

export function SettingsSectionContent({
  mode,
  section,
  accountData,
  preferencesData,
}: SettingsSectionContentProps) {
  if (mode === "fallback") {
    return (
      <>
        {accountData ? (
          <SettingsSectionLayout title="Account">{renderAccount(accountData)}</SettingsSectionLayout>
        ) : null}
        {preferencesData ? (
          <SettingsSectionLayout title="Preferences">
            {renderPreferences(preferencesData)}
          </SettingsSectionLayout>
        ) : null}
        <SettingsSectionLayout title="Quota">{renderUsage()}</SettingsSectionLayout>
        <SettingsSectionLayout title="Support">
          <SupportSection />
        </SettingsSectionLayout>
      </>
    );
  }

  // Dialog mode: wrap each section in SettingsDialogSectionFrame for
  // unified title, description, column width, and scroll boundary.
  if (section === "account" && accountData) {
    return wrapInFrame("account", renderAccount(accountData));
  }

  if (section === "preferences" && preferencesData) {
    return wrapInFrame("preferences", renderPreferences(preferencesData));
  }

  if (section === "usage") {
    return wrapInFrame("usage", renderUsage());
  }

  if (section === "support") {
    return wrapInFrame("support", <SupportSection />);
  }

  return null;
}
