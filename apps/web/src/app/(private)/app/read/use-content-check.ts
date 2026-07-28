"use client";

/**
 * Content Check 状态机（L2 Confirmed Source，mock BFF 开发）。
 *
 * 冻结合同：docs/tmp/TMP-reader-confirmed-source-schema-api-design-2026-07-28.md §4
 *   - GET  /records/{id}/confirmed-source — draft 读取 / resume 入口（§4.1）
 *   - PUT  /records/{id}/confirmed-source — 整篇更新 + reparse（§4.2，
 *     expected_revision 乐观并发；stale 409 可恢复：重取最新草稿重放编辑）
 *   - POST /records/{id}/candidate-documents/{cid}/confirm — 请求体不变（§4.3）
 *
 * 恢复语义：
 *   - stale_source_revision：自动重取最新 revision，以用户当前编辑文本重放
 *     一次 PUT；再次 stale 才进入 conflict，由用户选择"载入最新"或
 *     "以我的版本重试"，服务端永不覆盖较新草稿。
 *   - source_frozen / record_state_advanced：记录已推进，交回 onOpenReader。
 *   - reparse/网络失败：保留用户编辑（dirty 不丢），可显式重试。
 *   - 404：该 record 没有 Confirmed Source 行（L2 之前的存量记录），
 *     交回 onLegacyFallback 走旧 candidate-document 流。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ReaderAdaptationRecordDto,
  ReaderConfirmedSourceCandidateSummaryDto,
  ReaderConfirmedSourceReadResponseDto,
  ReaderConfirmedSourceUpdateOutcomeDto,
  ReaderConfirmedSourceUpdateResponseDto,
} from "@/types/api/reader-plate";

const AUTOSAVE_DEBOUNCE_MS = 1200;

export interface ContentCheckDraft {
  readingRecordId: string;
  sourceDocumentId: string;
  recordGeneration: number;
  revision: number;
  contentSha256: string;
  /** 服务端最近一次保存的完整 Markdown（编辑基线）。 */
  savedMarkdown: string;
  updatedAt: string;
  candidate: ReaderConfirmedSourceCandidateSummaryDto | null;
  /**
   * `_candidate_quality_json` 超集（含 suitability 五元组）。rejected
   * outcome 的原因经 `readRejectedReasons` 从这里与 contentCheck 派生。
   */
  quality: Record<string, unknown> | null;
  adaptationNotice: ReaderAdaptationRecordDto[];
  contentCheck: ReaderAdaptationRecordDto[];
  outcome: ReaderConfirmedSourceUpdateOutcomeDto | null;
}

/**
 * rejected outcome 的原因通道（真实后端合同）：PUT 响应无顶层
 * suitability，原因在 quality.suitability.reasons 与 content_check。
 */
export function readRejectedReasons(
  quality: Record<string, unknown> | null,
  contentCheck: ReaderAdaptationRecordDto[],
): string[] {
  const suitability = quality?.suitability;
  if (suitability && typeof suitability === "object") {
    const reasons = (suitability as { reasons?: unknown }).reasons;
    if (Array.isArray(reasons)) {
      const strings = reasons.filter(
        (item): item is string => typeof item === "string" && item.trim().length > 0,
      );
      if (strings.length > 0) return strings;
    }
  }
  return contentCheck
    .map((item) => item.message)
    .filter((message) => message.trim().length > 0);
}

export type ContentCheckPhase =
  | "loading"
  | "ready"
  | "saving"
  | "confirming"
  | "conflict"
  | "error";

export interface ContentCheckState {
  phase: ContentCheckPhase;
  draft: ContentCheckDraft | null;
  /** 编辑器文本相对 savedMarkdown 有未保存修改。 */
  dirty: boolean;
  errorMessage: string | null;
  infoMessage: string | null;
}

type BffError = {
  ok: false;
  status: number;
  code?: string;
  message?: string;
  currentRevision?: number;
};

type ReadResult = ({ ok: true } & ReaderConfirmedSourceReadResponseDto) | BffError;
type UpdateResult =
  | ({ ok: true } & ReaderConfirmedSourceUpdateResponseDto)
  | BffError;
type ConfirmResult = { ok: true } | BffError;

export interface UseContentCheckOptions {
  recordId: string;
  onOpenReader: (recordId: string) => void;
  onLegacyFallback: () => void;
  onConfirmed: (recordId: string) => void;
}

export interface ContentCheckController {
  state: ContentCheckState;
  /** 编辑器当前文本（工作副本）。 */
  workingMarkdown: string;
  resolvedCheckCodes: ReadonlySet<string>;
  /** 编辑器 onChange 入口：更新工作副本、标脏、调度防抖自动保存。 */
  handleEdit: (markdown: string) => void;
  /** 显式保存（confirm 前 flush 也走这里）。成功返回 true。 */
  saveNow: (markdown?: string) => Promise<boolean>;
  /** 确认并开始阅读：先 flush 保存，再走 confirm / stable 直达。 */
  confirmAndStart: (markdown?: string) => Promise<void>;
  /** 冲突恢复：放弃本地修改，载入服务端最新草稿。 */
  reloadLatest: () => Promise<string | null>;
  /** 冲突恢复：以最新 revision 重放当前编辑文本。 */
  retryWithLatestRevision: () => Promise<boolean>;
  /** 重试加载（initial GET 失败）。 */
  retryLoad: () => void;
  resolveCheckCode: (code: string) => void;
  resolveAllCheckCodes: (resolutionKeys?: readonly string[]) => void;
}

function buildDraftFromRead(
  recordId: string,
  payload: ReaderConfirmedSourceReadResponseDto,
): ContentCheckDraft {
  return {
    readingRecordId: recordId,
    sourceDocumentId: payload.source_document_id,
    recordGeneration: payload.record_generation,
    revision: payload.revision,
    contentSha256: payload.content_sha256,
    savedMarkdown: payload.markdown_text,
    updatedAt: payload.updated_at,
    candidate: payload.candidate ?? null,
    quality: payload.quality ?? null,
    adaptationNotice: payload.adaptation_notice ?? [],
    contentCheck: payload.content_check ?? [],
    outcome: payload.candidate ? "candidate_document_required" : null,
  };
}

function applyUpdateToDraft(
  draft: ContentCheckDraft,
  payload: ReaderConfirmedSourceUpdateResponseDto,
  savedMarkdown: string,
): ContentCheckDraft {
  // idempotent_noop（同 hash 幂等，合同 §4.2 步骤 4）：revision 不推进、
  // candidate 未 supersede——保留现有 revision/candidate/outcome，仅对齐
  // 已保存文本与 hash。
  if (payload.outcome === "idempotent_noop") {
    return {
      ...draft,
      contentSha256: payload.content_sha256,
      savedMarkdown,
    };
  }
  return {
    ...draft,
    revision: payload.revision,
    contentSha256: payload.content_sha256,
    savedMarkdown,
    candidate: payload.candidate ?? null,
    quality: payload.quality ?? null,
    adaptationNotice: payload.adaptation_notice ?? [],
    contentCheck: payload.content_check ?? [],
    outcome: payload.outcome,
  };
}

export function useContentCheck({
  recordId,
  onOpenReader,
  onLegacyFallback,
  onConfirmed,
}: UseContentCheckOptions): ContentCheckController {
  const [state, setState] = useState<ContentCheckState>({
    phase: "loading",
    draft: null,
    dirty: false,
    errorMessage: null,
    infoMessage: null,
  });
  const [workingMarkdown, setWorkingMarkdown] = useState("");
  const [resolvedCheckCodes, setResolvedCheckCodes] = useState<ReadonlySet<string>>(
    new Set(),
  );

  const draftRef = useRef<ContentCheckDraft | null>(null);
  const workingRef = useRef("");
  const autosaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveChainRef = useRef<Promise<boolean>>(Promise.resolve(true));
  // 回调 ref，避免 async 流程捕获过期闭包。写入只能在 effect 中进行。
  const onOpenReaderRef = useRef(onOpenReader);
  const onLegacyFallbackRef = useRef(onLegacyFallback);
  const onConfirmedRef = useRef(onConfirmed);
  useEffect(() => {
    onOpenReaderRef.current = onOpenReader;
    onLegacyFallbackRef.current = onLegacyFallback;
    onConfirmedRef.current = onConfirmed;
  });

  const clearAutosave = useCallback(() => {
    if (autosaveTimerRef.current !== null) {
      clearTimeout(autosaveTimerRef.current);
      autosaveTimerRef.current = null;
    }
  }, []);

  useEffect(() => clearAutosave, [clearAutosave]);

  const patchState = useCallback((patch: Partial<ContentCheckState>) => {
    setState((current) => ({ ...current, ...patch }));
  }, []);

  const fetchConfirmedSource = useCallback(async (): Promise<ReadResult> => {
    const response = await fetch(
      `/api/web/reader-plate/records/${encodeURIComponent(recordId)}/confirmed-source`,
      { method: "GET" },
    );
    return (await response.json()) as ReadResult;
  }, [recordId]);

  const loadLatest = useCallback(async (): Promise<ContentCheckDraft | null> => {
    const payload = await fetchConfirmedSource();
    if (payload.ok) {
      const draft = buildDraftFromRead(recordId, payload);
      const previous = draftRef.current;
      draftRef.current = draft;
      if (
        previous === null ||
        previous.revision !== draft.revision ||
        previous.contentSha256 !== draft.contentSha256
      ) {
        setResolvedCheckCodes(new Set());
      }
      return draft;
    }
    if (payload.status === 404) {
      onLegacyFallbackRef.current();
      return null;
    }
    if (
      payload.status === 409 &&
      (payload.code === "candidate_conflict_open_reader" ||
        payload.code === "source_frozen" ||
        payload.code === "record_state_advanced")
    ) {
      onOpenReaderRef.current(recordId);
      return null;
    }
    throw new Error(payload.message || "加载草稿失败，请稍后重试。");
  }, [fetchConfirmedSource, recordId]);

  const runInitialLoad = useCallback(() => {
    patchState({ phase: "loading", errorMessage: null, infoMessage: null });
    void loadLatest()
      .then((draft) => {
        if (!draft) return; // fallback / open_reader 已接管
        workingRef.current = draft.savedMarkdown;
        setWorkingMarkdown(draft.savedMarkdown);
        patchState({ phase: "ready", draft, dirty: false });
      })
      .catch((error: unknown) => {
        patchState({
          phase: "error",
          errorMessage:
            error instanceof Error ? error.message : "加载草稿失败，请稍后重试。",
        });
      });
  }, [loadLatest, patchState]);

  // 挂载加载：初始 state 已是 loading，effect 内不做同步 setState。
  useEffect(() => {
    let cancelled = false;
    void loadLatest()
      .then((draft) => {
        if (cancelled || !draft) return;
        workingRef.current = draft.savedMarkdown;
        setWorkingMarkdown(draft.savedMarkdown);
        patchState({ phase: "ready", draft, dirty: false });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        patchState({
          phase: "error",
          errorMessage:
            error instanceof Error ? error.message : "加载草稿失败，请稍后重试。",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [loadLatest, patchState]);

  const putDraft = useCallback(
    async (expectedRevision: number, markdown: string): Promise<UpdateResult> => {
      const response = await fetch(
        `/api/web/reader-plate/records/${encodeURIComponent(recordId)}/confirmed-source`,
        {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            expected_revision: expectedRevision,
            markdown_text: markdown,
            edit_source: "content_check",
          }),
        },
      );
      return (await response.json()) as UpdateResult;
    },
    [recordId],
  );

  /**
   * PUT 主流程。串行化（saveChainRef）避免防抖保存与 confirm flush 并发
   * 造成自挤 stale。stale 409：重取最新草稿并以用户文本重放一次；
   * 重放仍 stale 进入 conflict 等待用户决策。
   */
  const saveInternal = useCallback(
    async (markdown: string): Promise<boolean> => {
      let replayed = false;
      for (;;) {
        const draft = draftRef.current;
        if (!draft) return false;

        if (!replayed) {
          patchState({ phase: "saving", errorMessage: null });
        }
        let payload: UpdateResult;
        try {
          payload = await putDraft(draft.revision, markdown);
        } catch {
          patchState({
            phase: "ready",
            dirty: true,
            errorMessage: "保存失败，修改未丢失，请检查网络后重试。",
          });
          return false;
        }

        if (payload.ok) {
          const next = applyUpdateToDraft(draft, payload, markdown);
          draftRef.current = next;
          if (
            next.revision !== draft.revision ||
            next.contentSha256 !== draft.contentSha256
          ) {
            setResolvedCheckCodes(new Set());
          }
          patchState({
            phase: "ready",
            draft: next,
            dirty: workingRef.current !== next.savedMarkdown,
          });
          return true;
        }

        if (payload.code === "stale_source_revision") {
          if (replayed) {
            patchState({
              phase: "conflict",
              errorMessage:
                "草稿刚被其他更新抢先保存。你可以载入最新版本，或继续以你的修改覆盖提交。",
            });
            return false;
          }
          let latest: ContentCheckDraft | null = null;
          try {
            latest = await loadLatest();
          } catch {
            latest = null;
          }
          if (!latest) return false; // fallback / open_reader 已接管
          if (latest.savedMarkdown === markdown) {
            // 幂等 no-op 已在服务端发生：内容一致，直接采用最新草稿。
            patchState({
              phase: "ready",
              draft: latest,
              dirty: false,
              infoMessage: "修改已保存。",
            });
            return true;
          }
          patchState({
            draft: latest,
            infoMessage: "检测到草稿有新版本，已基于最新版本重新提交你的修改。",
          });
          replayed = true;
          continue;
        }

        if (
          payload.code === "candidate_conflict_open_reader" ||
          payload.code === "source_frozen" ||
          payload.code === "record_state_advanced"
        ) {
          onOpenReaderRef.current(recordId);
          return false;
        }

        // 服务失败 / reparse 失败：保留用户修改，可重试。
        patchState({
          phase: "ready",
          dirty: true,
          errorMessage: payload.message || "保存失败，修改未丢失，请重试。",
        });
        return false;
      }
    },
    [loadLatest, patchState, putDraft, recordId],
  );

  const enqueueSave = useCallback(
    (markdown: string): Promise<boolean> => {
      const chained = saveChainRef.current.then(() => saveInternal(markdown));
      // 链上任意一环失败都吞掉结果——失败态已写进 state，链条必须继续。
      saveChainRef.current = chained.then(
        () => true,
        () => true,
      );
      return chained;
    },
    [saveInternal],
  );

  const saveNow = useCallback(
    async (markdown?: string): Promise<boolean> => {
      clearAutosave();
      const text = markdown ?? workingRef.current;
      if (!text.trim()) {
        patchState({ errorMessage: "内容不能为空。" });
        return false;
      }
      return enqueueSave(text);
    },
    [clearAutosave, enqueueSave, patchState],
  );

  const handleEdit = useCallback(
    (markdown: string) => {
      workingRef.current = markdown;
      setWorkingMarkdown(markdown);
      patchState({
        dirty: markdown !== (draftRef.current?.savedMarkdown ?? ""),
        infoMessage: null,
      });
      clearAutosave();
      if (!markdown.trim()) return;
      // 非脏（如挂载时 editor 首帧 onChange 回显同一文本）不调度保存，
      // 避免无意义 PUT；服务端同 hash 幂等 no-op 只是兜底。
      if (markdown === (draftRef.current?.savedMarkdown ?? "")) return;
      autosaveTimerRef.current = setTimeout(() => {
        autosaveTimerRef.current = null;
        void enqueueSave(workingRef.current);
      }, AUTOSAVE_DEBOUNCE_MS);
    },
    [clearAutosave, enqueueSave, patchState],
  );

  const confirmAndStart = useCallback(
    async (markdown?: string): Promise<void> => {
      clearAutosave();
      const text = markdown ?? workingRef.current;
      const dirty =
        text !== (draftRef.current?.savedMarkdown ?? "") || state.dirty;
      if (dirty) {
        const saved = await saveNow(text);
        if (!saved) return;
      }

      const draft = draftRef.current;
      if (!draft) return;

      // PUT outcome=stable_document_ready：服务端已同事务冻结（合同 §4.2
      // 步骤 7 镜像 submit 行为），直接打开 Reader。
      if (draft.outcome === "stable_document_ready") {
        onConfirmedRef.current(recordId);
        return;
      }

      const candidate = draft.candidate;
      if (!candidate) {
        patchState({
          errorMessage:
            "当前内容还没有可确认的阅读版本，请先根据提示修改内容。",
        });
        return;
      }

      patchState({ phase: "confirming", errorMessage: null });
      const postConfirm = async (candidateDocumentId: string): Promise<ConfirmResult> => {
        const response = await fetch(
          `/api/web/reader-plate/records/${encodeURIComponent(recordId)}/candidate-documents/${encodeURIComponent(candidateDocumentId)}/confirm`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ language: "en" }),
          },
        );
        return (await response.json()) as ConfirmResult;
      };

      let payload = await postConfirm(candidate.candidate_document_id);
      if (!payload.ok && payload.code === "stale_candidate_revision") {
        // 候选引用了过期 source revision：重取最新 candidate 重试一次（§4.3）。
        const latest = await loadLatest().catch(() => null);
        if (latest?.candidate) {
          patchState({ draft: latest });
          payload = await postConfirm(latest.candidate.candidate_document_id);
        }
      }

      if (payload.ok) {
        onConfirmedRef.current(recordId);
        return;
      }
      if (
        payload.code === "candidate_conflict_open_reader" ||
        payload.code === "source_frozen" ||
        payload.code === "record_state_advanced"
      ) {
        onOpenReaderRef.current(recordId);
        return;
      }
      patchState({
        phase: "ready",
        errorMessage: payload.message || "确认失败，请稍后重试。",
      });
    },
    [clearAutosave, loadLatest, patchState, recordId, saveNow, state.dirty],
  );

  const reloadLatest = useCallback(async (): Promise<string | null> => {
    clearAutosave();
    patchState({ phase: "loading", errorMessage: null, infoMessage: null });
    try {
      const draft = await loadLatest();
      if (!draft) return null;
      workingRef.current = draft.savedMarkdown;
      setWorkingMarkdown(draft.savedMarkdown);
      patchState({ phase: "ready", draft, dirty: false });
      return draft.savedMarkdown;
    } catch (error: unknown) {
      patchState({
        phase: "conflict",
        errorMessage:
          error instanceof Error ? error.message : "加载最新草稿失败。",
      });
      return null;
    }
  }, [clearAutosave, loadLatest, patchState]);

  const retryWithLatestRevision = useCallback(async (): Promise<boolean> => {
    patchState({ phase: "ready", errorMessage: null });
    return enqueueSave(workingRef.current);
  }, [enqueueSave, patchState]);

  const retryLoad = useCallback(() => {
    runInitialLoad();
  }, [runInitialLoad]);

  const resolveCheckCode = useCallback((code: string) => {
    setResolvedCheckCodes((current) => {
      const next = new Set(current);
      next.add(code);
      return next;
    });
  }, []);

  const resolveAllCheckCodes = useCallback((resolutionKeys?: readonly string[]) => {
    const codes =
      resolutionKeys ??
      draftRef.current?.contentCheck.map((item) => item.code) ??
      [];
    setResolvedCheckCodes((current) => {
      const next = new Set(current);
      for (const code of codes) next.add(code);
      return next;
    });
  }, []);

  return {
    state,
    workingMarkdown,
    resolvedCheckCodes,
    handleEdit,
    saveNow,
    confirmAndStart,
    reloadLatest,
    retryWithLatestRevision,
    retryLoad,
    resolveCheckCode,
    resolveAllCheckCodes,
  };
}
