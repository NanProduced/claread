import Taro from '@tarojs/taro'
import { create } from 'zustand'
import {
  AnalyzeRequest,
  submitAnalysisTask,
  fetchAnalyze,
  getTaskStatus,
  getCurrentTask,
  checkAnonymousQuota,
  ApiError,
} from '../services/api/client'
import { fetchCloudRecord, fetchCloudRecordByClientId } from '../services/api/records.client'
import { analyzeResponseDtoToVm } from '../services/api/adapters/render-scene.adapter'
import { normalizeServerAnalyzeParams } from '../config/purpose'
import { useAuthStore } from './auth'
import {
  AnyRenderSceneVm,
  ResultPageState,
} from '../types/view/render-scene.vm'
import {
  saveRecord, getRecord, saveRecordIdentity, resolveClientIdFromMap,
} from '../services/storage'
import type { AnalysisRecord } from '../types/view/analysis-record.vm'
import { track } from '../services/analytics'

function derivePageState(
  phase: ArticlePhase,
  errorCode: string | null,
  vm: AnyRenderSceneVm | null
): ResultPageState {
  if (phase === 'idle' || phase === 'loading' || phase === 'polling') return 'loading'
  if (phase === 'error') {
    if (errorCode === 'TIMEOUT') return 'timeout'
    if (errorCode === 'NETWORK_ERROR') return 'network_fail'
    if (errorCode === 'AUTH_REQUIRED') return 'failed'
    return 'failed'
  }
  if (phase === 'empty') return 'empty'

  const state = vm?.userFacingState
  if (!state || state === ('loading' as string)) return 'normal'
  return state
}

export type ArticlePhase = 'idle' | 'loading' | 'polling' | 'success' | 'empty' | 'error'

function isEmptyResult(vm: AnyRenderSceneVm): boolean {
  const sentences = vm.article?.sentences
  if (!sentences || sentences.length === 0) return true
  return sentences.every((s) => !s.text || s.text.trim() === '')
}

function deriveFallbackTitle(text: string): string | null {
  const firstLine = text.split('\n')[0]?.trim() || ''
  if (!firstLine) return null
  return firstLine.length > 50 ? `${firstLine.slice(0, 50)}...` : firstLine
}

function extractTitleFromScene(scene: AnyRenderSceneVm | null): string | null {
  if (!scene) return null
  if (scene.schemaVersion === '3.0.0-academic') {
    const academic = scene as import('../types/view/render-scene.vm').AcademicRenderSceneVm
    if (academic.title?.trim()) return academic.title.trim()
  }
  return null
}

function generateLocalRecordId(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

let _pollSessionId = 0

interface ArticleState {
  sceneData: AnyRenderSceneVm | null
  requestParams: AnalyzeRequest | null
  recordId: string | null
  cloudId: string | null
  phase: ArticlePhase
  error: string | null
  errorCode: string | null
  pageState: ResultPageState
  isReplayMode: boolean
  analyze: (params: AnalyzeRequest) => Promise<void>
  recoverActiveTask: (targetRecordId?: string) => Promise<void>
  loadRecord: (recordId: string) => void
  reset: () => void
}

export const useArticleStore = create<ArticleState>((set, get) => {

  const applySuccessRecord = (
    cloudRecord: AnalysisRecord,
    fallbackCloudId: string
  ) => {
    if (!cloudRecord.renderScene) {
      throw new Error('Record not found or empty render scene')
    }
    const vm = cloudRecord.renderScene
    const phase = isEmptyResult(vm) ? 'empty' : 'success'
    let pageState = derivePageState(phase, null, vm)

    if (pageState === 'loading' && phase === 'success') {
      pageState = 'normal'
    }

    const sceneTitle = extractTitleFromScene(vm)
    const effectiveTitle = cloudRecord.title?.trim()
      ? cloudRecord.title
      : sceneTitle ?? cloudRecord.title

    const localRecord: AnalysisRecord = {
      ...cloudRecord,
      title: effectiveTitle,
      pageState,
    }
    saveRecord(localRecord)
    track('analyze_success', { pageState })

    if (get().phase !== 'polling' && get().phase !== 'loading') return
    set({
      sceneData: vm,
      phase,
      pageState,
      recordId: cloudRecord.recordId,
      cloudId: fallbackCloudId,
    })
  }

  const startPolling = async (taskId: string, sessionId: number) => {
    if (get().phase === 'success' || get().phase === 'empty') return

    const POLL_INTERVAL_MS = 2000
    const MAX_POLL_DURATION_MS = 180_000
    const MAX_CONSECUTIVE_ERRORS = 3
    const pollStartAt = Date.now()
    let consecutiveErrors = 0

    while (_pollSessionId === sessionId) {
      try {
        const statusRes = await getTaskStatus(taskId)
        consecutiveErrors = 0
        if (get().phase === 'success' || get().phase === 'empty') break

        if (['queued', 'running', 'finalizing'].includes(statusRes.status)) {
          if (Date.now() - pollStartAt > MAX_POLL_DURATION_MS) {
            throw new ApiError('分析超时，请稍后在历史记录中查看结果', 'TIMEOUT', 408)
          }
          await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS))
          continue
        }

        if (statusRes.status === 'succeeded') {
          const recordId = statusRes.cloud_record_id
          const cloudRecord = await fetchCloudRecord(recordId)
          if (!cloudRecord) {
            throw new Error('Record not found')
          }
          applySuccessRecord(cloudRecord, recordId)
          break
        }

        const errorCode = statusRes.failure_code || 'UNKNOWN'
        throw new ApiError(statusRes.failure_message || '分析失败', errorCode, 500)

      } catch (err) {
        if (_pollSessionId !== sessionId) break
        if (get().phase === 'success' || get().phase === 'empty') break

        const code = (err instanceof ApiError ? err.code : 'UNKNOWN') || 'UNKNOWN'

        if (code !== 'TIMEOUT' && code !== 'AUTH_REQUIRED' && consecutiveErrors < MAX_CONSECUTIVE_ERRORS) {
          consecutiveErrors++
          await new Promise(resolve => setTimeout(resolve, 3000))
          continue
        }

        const message = err instanceof Error ? err.message : '网络或服务异常，请稍后重试'
        const phase: ArticlePhase = 'error'
        const pageState = derivePageState(phase, code, null)

        track('analyze_failed', { errorCode: code })
        set({ error: message, errorCode: code, phase, pageState })
        break
      }
    }
  }

  return {
    sceneData: null,
    requestParams: null,
    recordId: null,
    cloudId: null,
    phase: 'idle',
    error: null,
    errorCode: null,
    pageState: 'loading',
    isReplayMode: false,

    analyze: async (params: AnalyzeRequest) => {
      const sessionId = ++_pollSessionId

      const normalizedRequest = {
        ...params,
        ...normalizeServerAnalyzeParams(params.reading_goal, params.reading_variant),
      } as AnalyzeRequest

      set({
        phase: 'loading',
        error: null,
        errorCode: null,
        sceneData: null,
        requestParams: normalizedRequest,
        isReplayMode: false,
      })

      // 匿名用户：先消耗试用次数（POST /quota/check 会在后端累加计数）
      if (!useAuthStore.getState().isLoggedIn) {
        const anonymousId = Taro.getStorageSync('anonymous_id') as string | undefined
        if (anonymousId) {
          try {
            const quotaResult = await checkAnonymousQuota(anonymousId)
            if (!quotaResult.allowed) {
              set({
                phase: 'error',
                error: '今日试用次数已用完，请登录后继续使用',
                errorCode: 'GUEST_QUOTA_EXCEEDED',
                pageState: 'failed',
              })
              return
            }
          } catch (e) {
            console.error('article.ts: anonymous quota check failed, allowing request', e)
          }
        }
      }

      const clientRecordId = `task-${generateLocalRecordId()}`
      let taskId = ''
      let serverRecordId = ''

      try {
        if (!useAuthStore.getState().isLoggedIn) {
          const res = await fetchAnalyze(normalizedRequest)
          const vm = analyzeResponseDtoToVm(res)
          const phase = isEmptyResult(vm) ? 'empty' : 'success'
          let pageState = derivePageState(phase, null, vm)
          if (pageState === 'loading' && phase === 'success') {
            pageState = 'normal'
          }

          const localRecord: AnalysisRecord = {
            recordId: clientRecordId,
            title: extractTitleFromScene(vm) ?? deriveFallbackTitle(normalizedRequest.text),
            sourceText: normalizedRequest.text,
            requestPayload: {
              reading_goal: normalizedRequest.reading_goal,
              reading_variant: normalizedRequest.reading_variant,
              source_type: normalizedRequest.source_type,
            },
            renderScene: vm,
            pageState,
            createdAt: Date.now(),
            updatedAt: Date.now(),
            isFavorited: false,
          }
          saveRecord(localRecord)
          track('analyze_success', { pageState })
          set({
            sceneData: vm,
            phase,
            pageState,
            recordId: clientRecordId,
            cloudId: null,
          })
          return
        }

        const res = await submitAnalysisTask({
          ...normalizedRequest,
          client_record_id: clientRecordId,
          wait_for_result: true,
          wait_timeout_seconds: 60,
        })
        taskId = res.task_id
        serverRecordId = res.cloud_record_id

        if (res.client_record_id && res.client_record_id !== clientRecordId) {
        }

        if (res.render_scene) {
          const vm = analyzeResponseDtoToVm(res.render_scene)
          const phase = isEmptyResult(vm) ? 'empty' : 'success'
          let pageState = derivePageState(phase, null, vm)
          if (pageState === 'loading' && phase === 'success') {
            pageState = 'normal'
          }

          const localRecord: AnalysisRecord = {
            recordId: clientRecordId,
            cloudId: serverRecordId,
            syncState: 'synced',
            title: extractTitleFromScene(vm) ?? deriveFallbackTitle(normalizedRequest.text),
            sourceText: normalizedRequest.text,
            requestPayload: {
              reading_goal: normalizedRequest.reading_goal,
              reading_variant: normalizedRequest.reading_variant,
              source_type: normalizedRequest.source_type,
            },
            renderScene: vm,
            pageState,
            createdAt: Date.now(),
            updatedAt: Date.now(),
            isFavorited: false,
          }
          saveRecord(localRecord)
          if (serverRecordId) {
            saveRecordIdentity(clientRecordId, serverRecordId)
          }
          track('analyze_success', { pageState })
          set({
            sceneData: vm,
            phase,
            pageState,
            recordId: clientRecordId,
            cloudId: serverRecordId,
          })
          return
        }

        const pageState = derivePageState('polling', null, null)

        // 进入轮询前先存一个初始记录到本地，确保在此期间收藏生词能找到关联记录
        const initialRecord: AnalysisRecord = {
          recordId: clientRecordId,
          cloudId: serverRecordId,
          syncState: 'local_only',
          title: deriveFallbackTitle(normalizedRequest.text),
          sourceText: normalizedRequest.text,
          requestPayload: {
            reading_goal: normalizedRequest.reading_goal,
            reading_variant: normalizedRequest.reading_variant,
            source_type: normalizedRequest.source_type,
          },
          renderScene: null,
          pageState,
          createdAt: Date.now(),
          updatedAt: Date.now(),
          isFavorited: false,
        }
        saveRecord(initialRecord)
        if (serverRecordId) {
          saveRecordIdentity(clientRecordId, serverRecordId)
        }

        set({ phase: 'polling', pageState, recordId: clientRecordId, cloudId: serverRecordId })
      } catch (err) {
        if (err instanceof ApiError && err.statusCode === 409) {
          const current = await getCurrentTask()
          if (current.has_active && current.task) {
             taskId = current.task.task_id
             serverRecordId = current.task.cloud_record_id

             const cloudRecord = await fetchCloudRecord(serverRecordId)
             if (!cloudRecord) {
                const message = '无法恢复当前任务，请稍后重试'
                set({ error: message, errorCode: 'RECOVERY_FAILED', phase: 'error', pageState: 'failed' })
                return
             }

             const realClientRecordId = current.task.client_record_id || cloudRecord.recordId
             if (realClientRecordId && serverRecordId) {
               saveRecordIdentity(realClientRecordId, serverRecordId)
             }
             const recoveryPageState = derivePageState('polling', null, null)
             set({ phase: 'polling', pageState: recoveryPageState, recordId: realClientRecordId, cloudId: serverRecordId })
             await startPolling(taskId, sessionId)
             return
          } else {
             throw err
          }
        } else if (err instanceof ApiError && err.statusCode === 402) {
          set({
            error: '今日解析积分已用尽',
            errorCode: 'INSUFFICIENT_CREDITS',
            phase: 'error',
            pageState: 'failed',
          })
          return
        } else if (err instanceof ApiError && err.statusCode === 422) {
          const resp = err.response as { error?: string; failure_code?: string; failure_message?: string } | undefined
          const failureCode = resp?.failure_code || resp?.error || 'TASK_FAILED'
          const failureMessage = resp?.failure_message || '分析任务执行失败'
          set({
            error: failureMessage,
            errorCode: failureCode,
            phase: 'error',
            pageState: 'failed',
          })
          return
        } else if (err instanceof ApiError && err.statusCode === 401) {
          set({
            error: '请先登录以开始解析',
            errorCode: 'AUTH_REQUIRED',
            phase: 'error',
            pageState: 'failed',
          })
          return
        } else {
          const message = err instanceof Error ? err.message : '网络或服务异常，请稍后重试'
          const code = (err instanceof ApiError ? err.code : 'UNKNOWN') || 'UNKNOWN'
          const phase: ArticlePhase = 'error'
          const pageState = derivePageState(phase, code, null)
          set({ error: message, errorCode: code, phase, pageState })
          return
        }
      }

      await startPolling(taskId, sessionId)
    },

    recoverActiveTask: async (targetRecordId?: string) => {
      if (get().phase === 'polling' || get().phase === 'loading') return

      try {
        const current = await getCurrentTask()
        if (current.has_active && current.task) {
          const serverRecordId = current.task.cloud_record_id

          const sessionId = ++_pollSessionId

          const cloudRecord = await fetchCloudRecord(serverRecordId)
          if (!cloudRecord) {
             return
          }

          if (targetRecordId && targetRecordId !== serverRecordId && cloudRecord.recordId !== targetRecordId) {
             return
          }

          const realClientRecordId = current.task.client_record_id || cloudRecord.recordId
          if (realClientRecordId && serverRecordId) {
            saveRecordIdentity(realClientRecordId, serverRecordId)
          }
          set({
            recordId: realClientRecordId,
            cloudId: serverRecordId,
            phase: 'polling',
            isReplayMode: false,
            error: null,
            errorCode: null,
            pageState: 'loading'
          })

          await startPolling(current.task.task_id, sessionId)
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : '恢复任务失败'
        const code = (err instanceof ApiError ? err.code : 'UNKNOWN') || 'UNKNOWN'
        console.error('[article] recoverActiveTask failed:', message)
        set({ error: message, errorCode: code, phase: 'error', pageState: derivePageState('error', code, null) })
      }
    },

    loadRecord: async (recordId: string) => {
      const mappedClientId = resolveClientIdFromMap(recordId)
      const localRecordId = mappedClientId || recordId
      const record = getRecord(localRecordId)
      if (!record) {
        const { isLoggedIn } = useAuthStore.getState()
        if (isLoggedIn) {
          try {
            const cloudRecord = await fetchCloudRecordByClientId(recordId) || await fetchCloudRecord(recordId)
            if (cloudRecord) {
              saveRecord(cloudRecord)
              if (cloudRecord.cloudId) {
                saveRecordIdentity(cloudRecord.recordId, cloudRecord.cloudId)
              }
              const pageState = cloudRecord.pageState
              const phase = pageState === 'empty' ? 'empty'
                : pageState === 'failed' || pageState === 'timeout' || pageState === 'network_fail' ? 'error'
                : cloudRecord.renderScene ? 'success' : 'error'
              set({
                sceneData: cloudRecord.renderScene,
                requestParams: cloudRecord.sourceText ? {
                  text: cloudRecord.sourceText,
                  ...normalizeServerAnalyzeParams(
                    cloudRecord.requestPayload.reading_goal,
                    cloudRecord.requestPayload.reading_variant
                  ),
                  source_type: cloudRecord.requestPayload.source_type || 'user_input',
                } : null,
                recordId: cloudRecord.recordId,
                cloudId: cloudRecord.cloudId || null,
                phase,
                error: null,
                errorCode: null,
                pageState,
                isReplayMode: true,
              })
              return
            }
          } catch (err) {
            // Silently ignore
          }
        }
        set({ phase: 'error', error: '记录不存在或已删除', errorCode: 'RECORD_NOT_FOUND', pageState: 'failed', recordId: null, cloudId: null, isReplayMode: true })
        return
      }

      if (record.pageState === 'loading' && !record.renderScene) {
         get().recoverActiveTask(recordId)
         return
      }

      const pageState = record.pageState
      const phase = pageState === 'empty' ? 'empty'
        : pageState === 'failed' || pageState === 'timeout' || pageState === 'network_fail' ? 'error'
        : record.renderScene ? 'success' : 'error'

      set({
        sceneData: record.renderScene,
        requestParams: record.sourceText ? {
          text: record.sourceText,
          ...normalizeServerAnalyzeParams(
            record.requestPayload.reading_goal,
            record.requestPayload.reading_variant
          ),
          source_type: record.requestPayload.source_type || 'user_input',
        } : null,
        recordId: record.recordId,
        cloudId: record.cloudId || null,
        phase,
        error: null,
        errorCode: null,
        pageState,
        isReplayMode: true,
      })
    },

    reset: () => {
      ++_pollSessionId
      set({
        sceneData: null,
        requestParams: null,
        recordId: null,
        cloudId: null,
        phase: 'idle',
        error: null,
        errorCode: null,
        pageState: 'loading',
        isReplayMode: false,
      })
    },
  }
})
