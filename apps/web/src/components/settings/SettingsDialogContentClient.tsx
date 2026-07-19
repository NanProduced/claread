"use client";

import * as React from "react";

import type { SettingsDialogSectionWidth } from "@/components/settings/SettingsDialogSectionFrame";
import { SettingsDialogSectionFrame } from "@/components/settings/SettingsDialogSectionFrame";
import type { SettingsDialogData } from "@/lib/settings-dialog-data";
import { AccountSection } from "@/app/(private)/app/settings/sections/AccountSection";
import { PreferencesSection } from "@/app/(private)/app/settings/sections/PreferencesSection";
import { SupportSection } from "@/app/(private)/app/settings/sections/SupportSection";
import { UsageSection } from "@/app/(private)/app/settings/sections/UsageSection";
import { parseSettingsSection, type SettingsSection } from "./settings-dialog-history";

/**
 * Section metadata for the AppShell Settings Dialog content adapter.
 *
 * These are minimal, read-only title/description pairs rendered inside
 * `SettingsDialogSectionFrame`. The data layer (`SettingsDialogData`
 * from `lib/settings-dialog-data.ts`) is the source of truth for the
 * DTO; this adapter does not import or fall back to any legacy
 * page-mode composition component.
 */
interface SectionMeta {
  title: string;
  description: string;
  width: SettingsDialogSectionWidth;
}

const SECTION_META: Record<SettingsSection, SectionMeta> = {
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

export interface SettingsDialogContentClientProps {
  /** Source-of-truth DTO from GET /api/web/settings-dialog. */
  data: SettingsDialogData;
  /** Section to render; invalid values fall back to `preferences`. */
  section: SettingsSection;
}

/**
 * Client content adapter for the AppShell Settings Dialog.
 *
 * Renders the active section by composing the per-section components
 * (`AccountSection` / `PreferencesSection` / `UsageSection` /
 * `SupportSection`) inside a `SettingsDialogSectionFrame`.
 *
 * Account / Preferences props come from `SettingsDialogData`
 * (the data-layer DTO from `lib/settings-dialog-data.ts`), not a
 * second view-model. The DTO is the single source of truth.
 */
export function SettingsDialogContentClient({
  data,
  section,
}: SettingsDialogContentClientProps) {
  const safeSection = parseSettingsSection(section);
  const meta = SECTION_META[safeSection];

  return (
    <SettingsDialogSectionFrame
      title={meta.title}
      description={meta.description}
      width={meta.width}
    >
      {renderSection(safeSection, data)}
    </SettingsDialogSectionFrame>
  );
}

function renderSection(
  section: SettingsSection,
  data: SettingsDialogData,
): React.ReactNode {
  if (section === "account") {
    return (
      <AccountSection
        nickname={data.accountData.nickname}
        displayFallback={data.accountData.displayFallback}
        phone={data.accountData.phone}
        status={data.accountData.status}
        avatarText={data.accountData.avatarText}
      />
    );
  }

  if (section === "preferences") {
    return (
      <PreferencesSection
        readingGoal={data.preferencesData.readingGoal}
        readingVariant={data.preferencesData.readingVariant}
        canEdit={data.preferencesData.canEdit}
      />
    );
  }

  if (section === "usage") {
    return <UsageSection />;
  }

  return <SupportSection />;
}
