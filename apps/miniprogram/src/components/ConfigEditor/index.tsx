import { useState } from 'react'
import { View, Text } from '@tarojs/components'
import { ReadingGoal, READING_CONFIG_MAP, ReadingVariant } from '../../config/purpose'
import LucideIcon from '../LucideIcon'
import './index.scss'

export type ConfigEditorMode = 'detailed' | 'compact' | 'reparse'

interface ConfigEditorProps {
  mode?: ConfigEditorMode
  initialGoal?: ReadingGoal
  initialLevel?: string | null
  showSubmit?: boolean
  submitText?: string
  layout?: 'column' | 'grid'
  onSelect?: (goal: ReadingGoal, level: string | null) => void
  onComplete?: (goal: ReadingGoal, level: string | null) => void
}

export default function ConfigEditor({
  mode = 'detailed',
  initialGoal,
  initialLevel,
  showSubmit = false,
  submitText = '确定',
  layout = 'column',
  onSelect,
  onComplete
}: ConfigEditorProps) {
  const [step, setStep] = useState<1 | 2>(1)
  const [goal, setGoal] = useState<ReadingGoal | null>(initialGoal || null)
  const [level, setLevel] = useState<string | null>(initialLevel || null)

  const handleGoalSelect = (selectedGoal: ReadingGoal) => {
    setGoal(selectedGoal)
    const config = READING_CONFIG_MAP[selectedGoal]

    if (onSelect) onSelect(selectedGoal, level)

    if (!config.variants) {
      if (onComplete) onComplete(selectedGoal, config.defaultVariant)
    } else {
      setStep(2)
    }
  }

  const handleLevelSelect = (selectedLevel: string) => {
    setLevel(selectedLevel)
    if (onSelect) onSelect(goal!, selectedLevel)
    if (!showSubmit && onComplete) {
      onComplete(goal!, selectedLevel)
    }
  }

  const handleBack = () => {
    setStep(1)
  }

  const goalList = Object.keys(READING_CONFIG_MAP) as ReadingGoal[]
  const isDetailed = mode === 'detailed'
  const isReparse = mode === 'reparse'

  return (
    <View className={`config-editor ${mode} step-${step} layout-${layout}`}>
      {step === 1 ? (
        <View className='goals-container fade-in'>
          {isDetailed && (
             <View className='step-header'>
               <Text className='step-title'>你主要用它来读什么？</Text>
               <Text className='step-subtitle'>我们会根据你的目的，自动调整 AI 的解读重点</Text>
             </View>
          )}
          <View className='options-grid'>
            {goalList.map((g) => {
              const config = READING_CONFIG_MAP[g]
              const isSelected = goal === g
              return (
                <View
                  key={g}
                  className={`goal-card ${isSelected ? 'active' : ''}`}
                  onClick={() => handleGoalSelect(g)}
                >
                  <View className='card-icon'>
                    <LucideIcon name={config.icon} size={isDetailed ? 64 : (isReparse ? 48 : 24)} color={isSelected ? 'var(--color-info)' : 'var(--text-muted)'} strokeWidth={isDetailed ? 2 : (isReparse ? 1.8 : 1.8)} />
                  </View>
                  <View className='card-content'>
                    <Text className='card-label'>{config.label}</Text>
                    {isDetailed && (
                      <Text className='card-desc'>{config.description}</Text>
                    )}
                  </View>
                  {!isDetailed && !isReparse && config.variants && (
                    <LucideIcon name='chevronRight' size={16} color='var(--text-muted)' />
                  )}
                </View>
              )
            })}
          </View>
        </View>
      ) : (
        <View className='variants-container fade-in'>
          <View className='step-header'>
             <View className='back-btn' onClick={handleBack}>
               <LucideIcon name='arrowLeft' size={40} color='var(--text-main)' />
               <Text className='back-text'>返回</Text>
             </View>
             {isDetailed && (
                <View className='header-text'>
                  <Text className='step-title'>
                    {goal === 'exam' ? '你的备考目标是？' : '你的当前水平大概在？'}
                  </Text>
                  <Text className='step-subtitle'>帮助 AI 更精准地为你过滤掉太简单的单词</Text>
                </View>
             )}
          </View>

          <View className='variants-list'>
            {goal && READING_CONFIG_MAP[goal].variants?.map((v) => {
              const isSelected = level === v.value
              return (
                <View
                  key={v.value}
                  className={`variant-item ${isSelected ? 'active' : ''}`}
                  onClick={() => handleLevelSelect(v.value)}
                >
                  <View className='variant-info'>
                    <Text className='variant-label'>{v.label}</Text>
                    {isDetailed && (
                      <Text className='variant-desc'>{v.description}</Text>
                    )}
                  </View>
                  <View className='check-box'>
                    {isSelected && <LucideIcon name='check' size={28} color='var(--color-white)' strokeWidth={3} />}
                  </View>
                </View>
              )
            })}
          </View>
        </View>
      )}

      {showSubmit && goal && (step === 2 || !READING_CONFIG_MAP[goal].variants) && (
        <View className='submit-area safe-area-bottom'>
          <View
            className={`submit-btn ${(!level && READING_CONFIG_MAP[goal].variants) ? 'disabled' : ''}`}
            onClick={() => onComplete && onComplete(goal!, level)}
          >
            <Text>{submitText}</Text>
          </View>
        </View>
      )}
    </View>
  )
}
