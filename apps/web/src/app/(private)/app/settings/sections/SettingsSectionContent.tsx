import type { SettingsSection } from "@/components/settings/SettingsDialogShell";
import type { ReadingDefaultState } from "@/lib/reading-defaults";
import type { ProfileBffStatus } from "@/services/bff/profile";
import type { QuotaVm } from "@/types/view/QuotaVm";
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

export interface UsageData {
  quota: QuotaVm | null;
  quotaUsed: number;
  quotaLimit: number;
  quotaPercentage: number;
}

interface SettingsSectionContentProps {
  mode?: "fallback";
  section?: SettingsSection;
  accountData?: AccountData;
  preferencesData?: PreferencesData;
  usageData?: UsageData;
  usageShowLedger?: boolean;
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

function renderUsage(data: UsageData, showLedger: boolean) {
  return (
    <UsageSection
      quota={data.quota}
      quotaUsed={data.quotaUsed}
      quotaLimit={data.quotaLimit}
      quotaPercentage={data.quotaPercentage}
      showLedger={showLedger}
    />
  );
}

export function SettingsSectionContent({
  mode,
  section,
  accountData,
  preferencesData,
  usageData,
  usageShowLedger = false,
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
        {usageData ? (
          <SettingsSectionLayout title="Quota">
            {renderUsage(usageData, usageShowLedger)}
          </SettingsSectionLayout>
        ) : null}
        <SettingsSectionLayout title="Support">
          <SupportSection />
        </SettingsSectionLayout>
      </>
    );
  }

  if (section === "account" && accountData) {
    return renderAccount(accountData);
  }

  if (section === "preferences" && preferencesData) {
    return renderPreferences(preferencesData);
  }

  if (section === "usage" && usageData) {
    return renderUsage(usageData, usageShowLedger);
  }

  if (section === "support") {
    return <SupportSection />;
  }

  return null;
}
