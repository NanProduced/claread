"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/primitives/button";
import { toast } from "@/components/primitives/toast";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/primitives/dialog";

type DeleteState = "idle" | "deleting" | "error";

type DeleteRecordApiResult =
  | {
      ok: true;
      deleted: boolean;
      message: string;
    }
  | {
      ok: false;
      status: number;
      code: string;
      message: string;
    };

interface DeleteRecordButtonProps {
  recordId: string;
  title: string;
  compact?: boolean;
  onDeleted?: (payload: { recordId: string; message: string }) => void;
}

async function readDeleteResponse(response: Response): Promise<DeleteRecordApiResult> {
  const payload = (await response.json().catch(() => null)) as DeleteRecordApiResult | null;

  if (payload) {
    return payload;
  }

  return {
    ok: false,
    status: response.status,
    code: "bad_response",
    message: "删除服务返回了无法识别的响应。",
  };
}

export function DeleteRecordButton({
  recordId,
  title,
  compact = false,
  onDeleted,
}: DeleteRecordButtonProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<DeleteState>("idle");
  const [message, setMessage] = useState<string | null>(null);

  async function deleteRecord() {
    if (state === "deleting") {
      return;
    }

    setState("deleting");
    setMessage(null);

    try {
      const response = await fetch(`/api/web/records/${encodeURIComponent(recordId)}`, {
        method: "DELETE",
      });
      const result = await readDeleteResponse(response);

      if (!result.ok) {
        setState("error");
        setMessage(result.message);
        toast.error(result.message);
        return;
      }

      setOpen(false);
      setState("idle");
      onDeleted?.({ recordId, message: result.message });
      router.refresh();
    } catch (error) {
      const nextMessage = error instanceof Error ? error.message : "删除记录失败。";
      setState("error");
      setMessage(nextMessage);
      toast.error(nextMessage);
    }
  }

  return (
    <>
      <button
        type="button"
        disabled={state === "deleting"}
        onClick={() => setOpen(true)}
        aria-label={`删除 ${title}`}
        title="删除记录"
        className={
          compact
            ? "focus-ring group inline-flex items-center justify-center h-8 w-8 rounded-md text-muted transition-all duration-200 hover:text-ink hover:scale-110 active:scale-95 disabled:cursor-not-allowed disabled:opacity-60"
            : "focus-ring inline-flex items-center gap-1.5 rounded-pill px-3 py-2 text-[0.72rem] font-semibold tracking-[0.08em] text-muted transition-colors hover:bg-surface-warm hover:text-error-red disabled:cursor-not-allowed disabled:opacity-60"
        }
      >
        <Trash2
          aria-hidden="true"
          className={
            compact
              ? "h-4.5 w-4.5 transition-all duration-200 stroke-[1.8] group-hover:stroke-[2.3] group-hover:text-ink"
              : "h-4 w-4"
          }
        />
        {!compact && "删除"}
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent variant="danger" size="sm" overlayClassName="bg-transparent! backdrop-blur-none!">
          <DialogHeader>
            <DialogTitle>删除阅读记录？</DialogTitle>
            <DialogDescription className="font-sans leading-relaxed text-[0.88rem] text-muted">
              你确定要删除阅读记录 <strong>「{title}」</strong> 吗？
              此操作会连同相关的句子解读、生词库及你记下的笔记一并移除。此操作无法撤销。
            </DialogDescription>
          </DialogHeader>
          
          {state === "error" && message ? (
            <p className="text-[0.75rem] font-medium text-error-red leading-5 bg-error-red/5 border border-error-red/10 rounded-md px-3 py-2">
              {message}
            </p>
          ) : null}

          <DialogFooter className="mt-2">
            <Button
              variant="subtle"
              density="compact"
              onClick={() => setOpen(false)}
              disabled={state === "deleting"}
            >
              取消
            </Button>
            <Button
              variant="danger"
              density="compact"
              onClick={deleteRecord}
              disabled={state === "deleting"}
              className="min-w-[80px]"
            >
              {state === "deleting" ? "删除中..." : "确认删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
