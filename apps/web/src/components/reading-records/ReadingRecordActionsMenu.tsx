"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Ellipsis } from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/primitives/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/primitives/dropdown-menu";
import { toast } from "@/components/primitives/toast";
import { useRecentReading } from "@/components/layout/recent-reading-context";
import { appLibraryRoute } from "@/lib/routes";
import { cn } from "@/lib/cn";

const FIXED_SAFE_ERROR_MESSAGE = "操作失败，请稍后重试。";

export interface ReadingRecordActionsMenuProps {
  recordId: string;
  title: string;
  /** Show the "从最近阅读中移除" item (Sidebar only). */
  showRemoveFromRecent?: boolean;
  /** True when this record is the currently open reader record. */
  isCurrentRecord?: boolean;
  onRemovedFromRecent?: (recordId: string) => void;
  onDeleted?: (recordId: string) => void;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

async function requestDelete(path: string): Promise<boolean> {
  const res = await fetch(path, { method: "DELETE" });
  const data = (await res.json().catch(() => null)) as { ok?: boolean } | null;
  return res.ok && data?.ok === true;
}

/**
 * Shared row-action menu for reading records, serving both the Sidebar
 * recent list and the Library full list.  One fetch/Dialog/toast state
 * machine — consumers only wire record identity and local-state hooks.
 */
export function ReadingRecordActionsMenu({
  recordId,
  title,
  showRemoveFromRecent = false,
  isCurrentRecord = false,
  onRemovedFromRecent,
  onDeleted,
  onOpenChange,
  className,
}: ReadingRecordActionsMenuProps) {
  const router = useRouter();
  const { removeLocal } = useRecentReading();
  const [menuOpen, setMenuOpen] = useState(false);
  const [hidePending, setHidePending] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [deletePending, setDeletePending] = useState(false);

  const handleMenuOpenChange = useCallback(
    (open: boolean) => {
      setMenuOpen(open);
      onOpenChange?.(open);
    },
    [onOpenChange],
  );

  const handleHideFromRecent = useCallback(async () => {
    if (hidePending) {
      return;
    }
    setHidePending(true);
    try {
      const ok = await requestDelete(
        `/api/web/reader/records/${encodeURIComponent(recordId)}/recent`,
      );
      if (!ok) {
        throw new Error("hide failed");
      }
      removeLocal(recordId);
      onRemovedFromRecent?.(recordId);
      toast.success("已从最近阅读中移除");
    } catch {
      // Keep the list item; fixed safe copy, never the upstream message.
      toast.error(FIXED_SAFE_ERROR_MESSAGE);
    } finally {
      setHidePending(false);
      handleMenuOpenChange(false);
    }
  }, [hidePending, recordId, removeLocal, onRemovedFromRecent, handleMenuOpenChange]);

  const handleConfirmDelete = useCallback(async () => {
    if (deletePending) {
      return;
    }
    setDeletePending(true);
    try {
      const ok = await requestDelete(
        `/api/web/reader/records/${encodeURIComponent(recordId)}`,
      );
      if (!ok) {
        throw new Error("delete failed");
      }
      removeLocal(recordId);
      onDeleted?.(recordId);
      router.refresh();
      toast.success("已删除阅读记录");
      setDeleteConfirmOpen(false);
      if (isCurrentRecord) {
        router.replace(appLibraryRoute);
      }
    } catch {
      // Dialog stays open so the user can retry or cancel.
      toast.error(FIXED_SAFE_ERROR_MESSAGE);
    } finally {
      setDeletePending(false);
    }
  }, [deletePending, recordId, removeLocal, onDeleted, router, isCurrentRecord]);

  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={handleMenuOpenChange}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={`打开“${title}”的操作菜单`}
            className={cn(
              "focus-ring flex items-center justify-center rounded-[6px] text-muted-foreground transition-colors",
              "hover:bg-[var(--app-control-quiet)] hover:text-ink",
              "data-[state=open]:bg-[var(--app-control-quiet)] data-[state=open]:text-ink",
              // Touch target: 44x44 on mobile, compact 32x32 from md up.
              "min-h-11 min-w-11 md:min-h-8 md:min-w-8",
              className,
            )}
          >
            <Ellipsis aria-hidden="true" className="h-4 w-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" sideOffset={8}>
          {showRemoveFromRecent ? (
            <DropdownMenuItem
              disabled={hidePending}
              onSelect={(event) => {
                event.preventDefault();
                void handleHideFromRecent();
              }}
            >
              {hidePending ? "正在移除…" : "从最近阅读中移除"}
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem
            className="text-destructive data-[highlighted]:text-destructive"
            onSelect={() => setDeleteConfirmOpen(true)}
          >
            删除阅读记录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除这条阅读记录？</AlertDialogTitle>
            <AlertDialogDescription>
              删除后，它将从最近阅读和全部阅读记录中消失，并且无法继续打开。此操作目前无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletePending}>取消</AlertDialogCancel>
            <button
              type="button"
              className="focus-ring inline-flex min-h-10 items-center justify-center gap-2 rounded-[var(--cl-radius-control-md)] bg-destructive px-3.5 text-sm font-semibold text-white transition-colors hover:bg-destructive/90 disabled:opacity-45"
              onClick={() => void handleConfirmDelete()}
              disabled={deletePending}
            >
              {deletePending ? "正在删除…" : "删除记录"}
            </button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
