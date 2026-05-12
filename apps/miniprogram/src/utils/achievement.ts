/**
 * 学术成就系统 - 等级映射工具
 *
 * 遵循 docs/product/scholarly_achievement_system.md 规范
 */

export interface ReadingTier {
  title: string
  cnTitle: string
  icon: string
  color: string
  level: number
}

/**
 * 根据累积阅读篇数获取等级信息
 *
 * 设计原则：
 * 1. 前密后疏
 * 2. 首篇即升级 (0篇为L0, 1篇起即进入L1)
 */
export const getReadingTier = (count: number): ReadingTier => {
  // L0: Newcomer (0篇)
  if (count <= 0) {
    return {
      level: 0,
      title: 'Newcomer',
      cnTitle: '新来者',
      icon: 'userRound',
      color: '#d4d4d8'
    }
  }

  // L1: Explorer (1-29篇) - 注：虽然文档表格写5篇，但原则要求首篇即升级，故此处设为1
  if (count < 30) {
    return {
      level: 1,
      title: 'Explorer',
      cnTitle: '探索者',
      icon: 'award',
      color: '#a1a1aa'
    }
  }

  // L2: Scholar (30-79篇)
  if (count < 80) {
    return {
      level: 2,
      title: 'Scholar',
      cnTitle: '学者',
      icon: 'bookOpen',
      color: '#1e293b'
    }
  }

  // L3: Fellow (80-119篇)
  if (count < 120) {
    return {
      level: 3,
      title: 'Fellow',
      cnTitle: '研究员',
      icon: 'medal',
      color: '#B8860B'
    }
  }

  // L4: Luminary (120-299篇)
  if (count < 300) {
    return {
      level: 4,
      title: 'Luminary',
      cnTitle: '先驱',
      icon: 'crown',
      color: '#be123c'
    }
  }

  // L5: Polymath (300+篇)
  return {
    level: 5,
    title: 'Polymath',
    cnTitle: '通才',
    icon: 'gem',
    color: '#7c3aed'
  }
}

/**
 * 获取所有等级定义（用于展示等级体系说明）
 */
export const getAllTiers = (): ReadingTier[] => {
  return [0, 5, 30, 80, 120, 300].map(count => getReadingTier(count))
}
