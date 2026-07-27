"use client";

import React from "react";
import { Globe } from "lucide-react";
import {
  PromptInput,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuTrigger,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputProvider,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SystemMessage } from "@/components/ui/system-message";
import { userFacingErrorMessage } from "../ask/ask-error-messages";
import type { WebSearchModeDto } from "@/types/api/reader-ask";

type AskComposerProps = {
  onSubmit: (value: string) => void | Promise<void>;
  sending: boolean;
  placeholder: string;
  errorMessage?: string | null;
  contextStrip?: React.ReactNode;
  actionMenu?: React.ReactNode;
  actionMenuOpen?: boolean;
  onActionMenuOpenChange?: (open: boolean) => void;
  modelOptions?: { label: string; value: string }[];
  modelSelectDisabled?: boolean;
  selectedModelKey?: string | null;
  modelPlaceholder?: string;
  onModelChange?: (value: string | null) => void;
  onTextareaFocus?: () => void;
  onTextareaBlur?: () => void;
  /** Called when the user clicks the stop button while generating. */
  onStop?: () => void;
  /**
   * User-visible web search request mode (mirrors backend WebSearchMode).
   * `allowed` only grants turn capability; it never forces a search.
   * Undefined when the host does not support web search.
   */
  webSearchMode?: WebSearchModeDto;
  onWebSearchModeChange?: (mode: WebSearchModeDto) => void;
};

function AskComposerSurface({
  onSubmit,
  sending,
  placeholder,
  contextStrip,
  actionMenu,
  actionMenuOpen,
  onActionMenuOpenChange,
  modelOptions,
  modelSelectDisabled,
  selectedModelKey,
  modelPlaceholder,
  onModelChange,
  onTextareaFocus,
  onTextareaBlur,
  onStop,
  webSearchMode,
  onWebSearchModeChange,
}: Omit<AskComposerProps, "errorMessage">) {
  const { textInput } = usePromptInputController();
  const canSend = textInput.value.trim().length > 0 && !sending;
  const hasContextStrip = Boolean(contextStrip) && React.Children.count(contextStrip) > 0;
  // When generating, the submit button becomes a stop button and must NOT
  // be disabled — otherwise the user cannot click it to abort the stream.
  const submitDisabled = sending ? false : !canSend;
  const webSearchEnabled = webSearchMode === "allowed";
  const webSearchSupported = onWebSearchModeChange != null;

  return (
    <PromptInput
      onSubmit={({ text }) => {
        const value = text.trim();
        if (!value || sending) {
          return;
        }
        return onSubmit(value);
      }}
      className="w-full rounded-md border-border/70 bg-background shadow-none"
    >
      {hasContextStrip ? (
        <PromptInputHeader className="w-full flex-wrap gap-1.5 border-b border-border/60 px-3 py-2">
          {contextStrip}
        </PromptInputHeader>
      ) : null}

      <PromptInputTextarea
        placeholder={placeholder}
        className="min-h-[4rem] text-[15px] leading-6 placeholder:text-muted-foreground"
        disabled={sending}
        onFocus={onTextareaFocus}
        onBlur={onTextareaBlur}
        data-ask-composer-textarea="true"
      />

      <PromptInputFooter className="w-full items-center justify-between gap-2 px-3 pb-3 pt-0.5">
        <PromptInputTools>
          {actionMenu ? (
            <PromptInputActionMenu
              open={actionMenuOpen}
              onOpenChange={onActionMenuOpenChange}
            >
              <PromptInputActionMenuTrigger
                aria-label="添加其他文章"
                className="rounded-full"
              />
              <PromptInputActionMenuContent
                side="top"
                collisionPadding={8}
                className="w-[18rem] p-0"
              >
                {actionMenu}
              </PromptInputActionMenuContent>
            </PromptInputActionMenu>
          ) : null}
          {webSearchSupported ? (
            <button
              type="button"
              aria-label="联网搜索"
              aria-pressed={webSearchEnabled}
              data-testid="ask-composer-web-search-toggle"
              data-state={webSearchEnabled ? "on" : "off"}
              disabled={sending}
              onClick={() =>
                onWebSearchModeChange?.(webSearchEnabled ? "disabled" : "allowed")
              }
              className="inline-flex h-7 items-center gap-1 rounded-md border-transparent px-1.5 text-xs font-normal shadow-none transition-colors focus:outline-none focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-3"
              style={
                webSearchEnabled
                  ? {
                      backgroundColor: "hsl(var(--primary) / 0.1)",
                      color: "hsl(var(--primary))",
                    }
                  : {
                      color: "hsl(var(--muted-foreground) / 0.7)",
                    }
              }
            >
              <Globe aria-hidden="true" />
              <span>联网搜索</span>
            </button>
          ) : null}
        </PromptInputTools>
        <PromptInputTools className="justify-end">
          {modelOptions?.length ? (
            <Select
              value={selectedModelKey ?? undefined}
              onValueChange={(value) => onModelChange?.(value || null)}
              disabled={modelSelectDisabled}
            >
              <SelectTrigger
                aria-label="切换 Ask Claread 模型"
                className="h-7 rounded-md border-transparent bg-transparent px-1.5 text-xs font-normal text-muted-foreground/70 shadow-none transition-colors hover:bg-muted/60 hover:text-foreground focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 focus:outline-none focus-visible:outline-none [&_svg]:ml-0.5 [&_svg]:size-3 [&_svg]:opacity-0 hover:[&_svg]:opacity-50"
              >
                <SelectValue placeholder={modelPlaceholder ?? "选择模型"} />
              </SelectTrigger>
              <SelectContent position="popper" side="top" align="end" className="mb-1">
                {modelOptions.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
          <PromptInputSubmit
            aria-label={sending ? "停止生成" : "发送"}
            className="rounded-full"
            status={sending ? "submitted" : "ready"}
            disabled={submitDisabled}
            onStop={sending ? onStop : undefined}
          />
        </PromptInputTools>
      </PromptInputFooter>
    </PromptInput>
  );
}

export function AskComposer({
  onSubmit,
  sending,
  placeholder,
  errorMessage,
  contextStrip,
  actionMenu,
  actionMenuOpen,
  onActionMenuOpenChange,
  modelOptions,
  modelSelectDisabled,
  selectedModelKey,
  modelPlaceholder,
  onModelChange,
  onTextareaFocus,
  onTextareaBlur,
  onStop,
  webSearchMode,
  onWebSearchModeChange,
}: AskComposerProps) {
  const displayErrorMessage = errorMessage ? userFacingErrorMessage(errorMessage) : null;

  return (
    <div className="bg-background px-4 pb-3 pt-1.5">
      {displayErrorMessage ? (
        <SystemMessage className="mb-2.5 rounded-md px-3 py-2 text-xs leading-5" variant="error">
          {displayErrorMessage}
        </SystemMessage>
      ) : null}

      <PromptInputProvider>
        <AskComposerSurface
          onSubmit={onSubmit}
          sending={sending}
          placeholder={placeholder}
          contextStrip={contextStrip}
          actionMenu={actionMenu}
          actionMenuOpen={actionMenuOpen}
          onActionMenuOpenChange={onActionMenuOpenChange}
          modelOptions={modelOptions}
          modelSelectDisabled={modelSelectDisabled}
          selectedModelKey={selectedModelKey}
          modelPlaceholder={modelPlaceholder}
          onModelChange={onModelChange}
          onTextareaFocus={onTextareaFocus}
          onTextareaBlur={onTextareaBlur}
          onStop={onStop}
          webSearchMode={webSearchMode}
          onWebSearchModeChange={onWebSearchModeChange}
        />
      </PromptInputProvider>
    </div>
  );
}
