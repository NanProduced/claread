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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
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
    <TooltipProvider>
      <PromptInput
        onSubmit={({ text }) => {
          const value = text.trim();
          if (!value || sending) {
            return;
          }
          textInput.clear();
          return onSubmit(value);
        }}
        className="w-full"
        inputGroupClassName="rounded-2xl border border-hairline bg-surface shadow-none transition-[border-color,box-shadow] dark:bg-surface has-[[data-slot=input-group-control]:focus-visible]:border-ring/60 has-[[data-slot=input-group-control]:focus-visible]:ring-2 has-[[data-slot=input-group-control]:focus-visible]:ring-ring/15"
      >
      {hasContextStrip ? (
        // Let compact context chips wrap as a group, matching their visual
        // relationship instead of clipping a selected passage into a
        // horizontal scroller.
        <PromptInputHeader
          className="w-full flex-wrap items-center gap-1.5 px-3 pb-1 pt-2"
          data-ask-context-strip="true"
        >
          {contextStrip}
        </PromptInputHeader>
      ) : null}

      <PromptInputTextarea
        placeholder={placeholder}
        className="min-h-14 bg-transparent py-2.5 !text-sm leading-6 placeholder:text-muted-foreground"
        disabled={sending}
        onFocus={onTextareaFocus}
        onBlur={onTextareaBlur}
        data-ask-composer-textarea="true"
      />

      <PromptInputFooter className="w-full items-center justify-between gap-2 px-3 pb-2 pt-1">
        <PromptInputTools>
          {actionMenu ? (
            <PromptInputActionMenu
              open={actionMenuOpen}
              onOpenChange={onActionMenuOpenChange}
            >
              <PromptInputActionMenuTrigger
                aria-label="添加其他文章"
                className="cursor-pointer rounded-md"
                tooltip="添加其他文章"
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
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={
                    webSearchEnabled ? "联网搜索已开启" : "联网搜索已关闭"
                  }
                  aria-pressed={webSearchEnabled}
                  data-testid="ask-composer-web-search-toggle"
                  data-state={webSearchEnabled ? "on" : "off"}
                  disabled={sending}
                  onClick={() =>
                    onWebSearchModeChange?.(webSearchEnabled ? "disabled" : "allowed")
                  }
                  className={cn(
                    "inline-flex size-7 cursor-pointer items-center justify-center rounded-md text-muted-foreground shadow-none transition-colors",
                    "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    "disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-3.5",
                    webSearchEnabled
                      ? "bg-muted text-foreground"
                      : "hover:bg-muted/55 hover:text-foreground",
                  )}
                >
                  <Globe aria-hidden="true" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top">
                {webSearchEnabled
                  ? "联网搜索已开启，必要时自动搜索"
                  : "联网搜索已关闭"}
              </TooltipContent>
            </Tooltip>
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
                size="sm"
                className="!h-7 max-w-[11.5rem] cursor-pointer truncate rounded-md px-1.5 !text-xs !font-normal text-muted-foreground shadow-none hover:bg-muted/55 aria-expanded:bg-muted/55 focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 focus:outline-none focus-visible:outline-none [&_svg]:ml-0.5 [&_svg]:size-3"
              >
                <PromptInputSelectValue
                  className="truncate"
                  placeholder={modelPlaceholder ?? "选择模型"}
                />
              </PromptInputSelectTrigger>
              <PromptInputSelectContent
                position="popper"
                side="top"
                align="end"
                className="mb-1 rounded-md border-border bg-surface p-1 shadow-sm"
              >
                {modelOptions.map((item) => (
                  <PromptInputSelectItem
                    key={item.value}
                    value={item.value}
                    className="cursor-pointer rounded-sm py-1.5 focus:bg-muted/60 focus:text-foreground"
                  >
                    {item.label}
                  </PromptInputSelectItem>
                ))}
              </PromptInputSelectContent>
            </PromptInputSelect>
          ) : null}
          <PromptInputSubmit
            aria-label={sending ? "停止生成" : "发送"}
            className="!size-7 rounded-full"
            status={sending ? "streaming" : "ready"}
            disabled={submitDisabled}
            onStop={sending ? onStop : undefined}
          />
        </PromptInputTools>
      </PromptInputFooter>
      </PromptInput>
    </TooltipProvider>
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
    <div className="shrink-0 bg-surface px-4 pb-3 pt-2">
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
