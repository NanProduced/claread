import { create } from 'zustand'
import Taro from '@tarojs/taro'
import { useAuthStore } from './auth'
import { updateProfile } from '../services/api/client'
import { ReadingGoal, SERVER_GOAL_TO_UI_GOAL, READING_CONFIG_MAP } from '../config/purpose'

export type UserPurpose = ReadingGoal

interface ConfigState {
  purpose: UserPurpose;
  level: string | null;
  defaultCardExpanded: boolean;
  setPurpose: (purpose: UserPurpose) => void;
  setLevel: (level: string | null) => void;
  setDefaultCardExpanded: (expanded: boolean) => void;
  syncToCloud: () => Promise<void>;
  initializeFromCloud: () => void;
}

export const useConfigStore = create<ConfigState>((set, get) => ({
  purpose: (Taro.getStorageSync('user_purpose') as UserPurpose) || 'daily',
  level: Taro.getStorageSync('user_level') || null,
  defaultCardExpanded: Taro.getStorageSync('default_card_expanded') === 'true',

  setPurpose: (purpose) => {
    set({ purpose })
    Taro.setStorageSync('user_purpose', purpose)
    get().syncToCloud()
  },
  setLevel: (level) => {
    set({ level })
    Taro.setStorageSync('user_level', level)
    get().syncToCloud()
  },
  setDefaultCardExpanded: (expanded) => {
    set({ defaultCardExpanded: expanded })
    Taro.setStorageSync('default_card_expanded', expanded ? 'true' : 'false')
  },

  syncToCloud: async () => {
    const { isLoggedIn } = useAuthStore.getState()
    if (!isLoggedIn) return

    const { purpose, level } = get()
    const serverGoal = READING_CONFIG_MAP[purpose]?.serverGoal || purpose
    try {
      await updateProfile({
        settings: {
          default_reading_goal: serverGoal,
          default_reading_variant: level
        }
      })
    } catch (e) {
      console.warn('[config] failed to sync settings to cloud:', e)
    }
  },

  initializeFromCloud: () => {
    const { userInfo } = useAuthStore.getState()
    if (userInfo?.settings) {
      const { default_reading_goal, default_reading_variant } = userInfo.settings
      const updates: Partial<ConfigState> = {}
      if (default_reading_goal) {
        const uiGoal = SERVER_GOAL_TO_UI_GOAL[default_reading_goal] || default_reading_goal
        updates.purpose = uiGoal as UserPurpose
        Taro.setStorageSync('user_purpose', uiGoal)
      }
      if (default_reading_variant) {
        updates.level = default_reading_variant
        Taro.setStorageSync('user_level', default_reading_variant)
      }
      if (Object.keys(updates).length > 0) {
        set(updates)
        Taro.setStorageSync('user_configured', true)
      }
    }
  }
}))
