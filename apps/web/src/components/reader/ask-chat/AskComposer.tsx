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
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSelectValue,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import { SystemMessage } from "@/components/ui/system-message";
import { userFacingErrorMessage } from "../ask/ask-error-messages";
import { cn } from "@/lib/cn";
import type { WebSearchModeDto } from "@/types/api/reader-ask";

type AskComposerProps = {
  onSubmit: (value: string) => void | Promise<void>;
  sending: boolean;
  placeholder: string;
  errorMessage?: string | null;
  onErrorRetry?: () => void;
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
      // Quiet low-elevation surface: very light border, no shadow,
      // no heavy ring. The composer recedes; focus + send own the blue.
      className="w-full rounded-lg border-border/50 bg-muted/30 shadow-none transition-colors focus-within:border-border/80"
    >
      {hasContextStrip ? (
        // Context strip is a fixed, horizontally scrollable row: chips never
        // wrap-stack and squeeze the textarea (mobile), and no second
        // vertical scroll owner is created. Chips are shrink-0; overflow
        // scrolls sideways.
        <PromptInputHeader
          className="w-full flex-nowrap items-center gap-1.5 overflow-x-auto border-b border-border/40 px-3 py-2 [scrollbar-width:thin]"
          data-ask-context-strip="true"
        >
          {contextStrip}
        </PromptInputHeader>
      ) : null}

      <PromptInputTextarea
        placeholder={placeholder}
        className="min-h-[4rem] bg-transparent text-[14px] leading-6 placeholder:text-muted-foreground"
        disabled={sending}
        onFocus={onTextareaFocus}
        onBlur={onTextareaBlur}
        data-ask-composer-textarea="true"
      />

      <PromptInputFooter className="w-full items-center justify-between gap-2 px-3 pb-2.5 pt-0.5">
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
              aria-label={
                webSearchEnabled ? "联网搜索已开启" : "联网搜索已关闭"
              }
              aria-pressed={webSearchEnabled}
              data-testid="ask-composer-web-search-toggle"
              data-state={webSearchEnabled ? "on" : "off"}
              disabled={sending}
              title={
                webSearchEnabled
                  ? "联网搜索已开启，Agent 会在需要最新信息时自行搜索"
                  : "联网搜索已关闭"
              }
              onClick={() =>
                onWebSearchModeChange?.(webSearchEnabled ? "disabled" : "allowed")
              }
              // Web toggle is a secondary control: neutral muted
              // surface when on (not primary blue — blue is reserved for
              // focus + send), quiet border when off. Blue appears only
              // in the focus-visible ring.
              className={cn(
                "inline-flex h-7 items-center gap-1 rounded-full border px-2 text-xs font-medium shadow-none transition-colors",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                "disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-3",
                webSearchEnabled
                  ? "border-border bg-muted text-foreground"
                  : "border-border/70 text-muted-foreground/70 hover:bg-muted/40 hover:text-muted-foreground",
              )}
            >
              <Globe aria-hidden="true" />
              <span>联网 · {webSearchEnabled ? "开" : "关"}</span>
            </button>
          ) : null}
        </PromptInputTools>
        <PromptInputTools className="justify-end">
          {modelOptions?.length ? (
            <PromptInputSelect
              value={selectedModelKey ?? undefined}
              onValueChange={(value) => onModelChange?.(value || null)}
              disabled={modelSelectDisabled}
            >
              <PromptInputSelectTrigger
                aria-label="切换 Ask Claread 模型"
                className="h-7 max-w-[9rem] truncate rounded-md px-1.5 text-xs font-normal text-muted-foreground shadow-none focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 focus:outline-none focus-visible:outline-none [&_svg]:ml-0.5 [&_svg]:size-3"
              >
                <PromptInputSelectValue
                  className="truncate"
                  placeholder={modelPlaceholder ?? "选择模型"}
                />
              </PromptInputSelectTrigger>
              <PromptInputSelectContent position="popper" side="top" align="end" className="mb-1">
                {modelOptions.map((item) => (
                  <PromptInputSelectItem key={item.value} value={item.value}>
                    {item.label}
                  </PromptInputSelectItem>
                ))}
              </PromptInputSelectContent>
            </PromptInputSelect>
          ) : null}
          <PromptInputSubmit
            aria-label={sending ? "停止生成" : "发送"}
            className="rounded-full"
            status={sending ? "streaming" : "ready"}
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
  onErrorRetry,
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
    <div className="shrink-0 border-t border-border/60 bg-background px-4 pb-3 pt-1.5">
      {displayErrorMessage ? (
        // Composer banner errors are recoverable (network/retry/
        // capability). Use warning+fill for a low-disturbance amber surface
        // instead of a full red border. Only unrecoverable validation
        // errors (not currently surfaced here) would use variant="error".
        <SystemMessage
          className="mb-2.5"
          variant="quiet"
          severity="warning"
          cta={
            onErrorRetry
              ? { label: "重试", onClick: onErrorRetry, variant: "ghost" }
              : undefined
          }
        >
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
