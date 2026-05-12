import { create } from 'zustand'
import type { DailyReaderArticle, DailyReaderListItem } from '../types/view/daily-reader.vm'
import { fetchTodayArticles, fetchArticleById, fetchArticleList } from '../services/api/daily-reader.client'

interface DailyReaderState {
  latestArticles: DailyReaderListItem[]
  currentArticle: DailyReaderArticle | null
  articleList: DailyReaderListItem[]
  listCursor: string | null
  listHasMore: boolean
  loading: boolean
  error: string | null

  fetchLatest: (limit?: number) => Promise<void>
  fetchArticle: (id: string) => Promise<void>
  fetchList: (cursor?: string) => Promise<void>
  setCurrentArticle: (article: DailyReaderArticle | null) => void
  reset: () => void
}

export const useDailyReaderStore = create<DailyReaderState>((set, get) => ({
  latestArticles: [],
  currentArticle: null,
  articleList: [],
  listCursor: null,
  listHasMore: false,
  loading: false,
  error: null,

  fetchLatest: async (limit: number = 5) => {
    set({ loading: true, error: null })
    try {
      const articles = await fetchTodayArticles()
      set({ latestArticles: articles.slice(0, limit), loading: false })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to fetch latest articles', loading: false })
    }
  },

  fetchArticle: async (id: string) => {
    set({ loading: true, error: null })
    try {
      const article = await fetchArticleById(id)
      set({ currentArticle: article, loading: false })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to fetch article', loading: false })
    }
  },

  fetchList: async (cursor?: string) => {
    set({ loading: true, error: null })
    try {
      const result = await fetchArticleList(cursor, 10)
      if (cursor) {
        const existing = get().articleList
        set({
          articleList: [...existing, ...result.items],
          listCursor: result.cursor,
          listHasMore: result.hasMore,
          loading: false,
        })
      } else {
        set({
          articleList: result.items,
          listCursor: result.cursor,
          listHasMore: result.hasMore,
          loading: false,
        })
      }
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to fetch article list', loading: false })
    }
  },

  setCurrentArticle: (article) => set({ currentArticle: article }),

  reset: () =>
    set({
      latestArticles: [],
      currentArticle: null,
      articleList: [],
      listCursor: null,
      listHasMore: false,
      loading: false,
      error: null,
    }),
}))
