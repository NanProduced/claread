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
      className="w-full rounded-2xl border-border/75 bg-background shadow-sm"
    >
      {hasContextStrip ? (
        <PromptInputHeader className="w-full flex-wrap gap-1.5 border-b border-border/60 px-3 py-2">
          {contextStrip}
        </PromptInputHeader>
      ) : null}

      <PromptInputTextarea
        placeholder={placeholder}
        className="min-h-[4.75rem] text-[15px] leading-7 placeholder:text-muted-foreground"
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
              onClick={() => onActionMenuOpenChange?.(true)}
            />
            <PromptInputActionMenuContent
              forceMount
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
                className="h-7 border-transparent bg-transparent shadow-none px-1.5 text-xs text-muted-foreground/85 font-normal hover:bg-muted/60 hover:text-foreground hover:font-medium rounded-md focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 focus:outline-none focus-visible:outline-none [&_svg]:size-3 [&_svg]:opacity-0 hover:[&_svg]:opacity-50 transition-all [&_svg]:ml-0.5"
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
  return (
    <div className="bg-background px-4 pb-4 pt-2">
      {errorMessage ? (
        <SystemMessage className="mb-3" fill variant="error">
          {errorMessage}
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
