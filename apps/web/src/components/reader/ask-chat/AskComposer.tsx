"use client";

import React from "react";
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
}: Omit<AskComposerProps, "errorMessage">) {
  const { textInput } = usePromptInputController();
  const canSend = textInput.value.trim().length > 0 && !sending;
  const hasContextStrip = Boolean(contextStrip) && React.Children.count(contextStrip) > 0;

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
            disabled={!canSend}
          />
        </PromptInputTools>
      </PromptInputFooter>
    </PromptInput>
  );
}

function userFacingErrorMessage(errorMessage: string): string {
  const normalizedMessage = errorMessage.trim();
  if (!normalizedMessage || /\b(?:internal|unexpected) server error\b/i.test(normalizedMessage)) {
    return "这次回答没有完成。请稍后重试，或换一种问法。";
  }
  return normalizedMessage;
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
        />
      </PromptInputProvider>
    </div>
  );
}
