import { create } from 'zustand'

interface LayoutState {
  navBarHeight: number;
  statusBarHeight: number;
  setNavHeights: (navBar: number, status: number) => void;
}

export const useLayoutStore = create<LayoutState>((set) => ({
  navBarHeight: 80, // 默认安全高度
  statusBarHeight: 20,
  setNavHeights: (navBar, status) => set({ navBarHeight: navBar, statusBarHeight: status }),
}))
