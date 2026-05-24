"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { IconButton } from "@/components/primitives/icon-button";

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

export function DeleteRecordButton({ recordId, title }: DeleteRecordButtonProps) {
  const router = useRouter();
  const [state, setState] = useState<DeleteState>("idle");
  const [message, setMessage] = useState<string | null>(null);

  async function deleteRecord() {
    if (state === "deleting") {
      return;
    }

    const confirmed = window.confirm(`删除「${title}」？此操作会移除这条阅读记录。`);
    if (!confirmed) {
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
        return;
      }

      router.refresh();
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "删除记录失败。");
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <IconButton
        disabled={state === "deleting"}
        onClick={deleteRecord}
        aria-label={`删除 ${title}`}
        title="删除记录"
        variant="danger"
      >
        <Trash2 aria-hidden="true" className="h-4 w-4" />
      </IconButton>
      {state === "error" && message ? (
        <p className="max-w-44 text-right text-[0.6875rem] leading-4 text-error-red">{message}</p>
      ) : null}
    </div>
  );
}
