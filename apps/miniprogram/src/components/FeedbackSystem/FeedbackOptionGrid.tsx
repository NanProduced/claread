import { View } from '@tarojs/components'
import { FeedbackTypeOption } from '../../config/feedback'
import './FeedbackOptionGrid.scss'

interface FeedbackOptionGridProps {
  options: FeedbackTypeOption[]
  selectedValues: string[]
  multiSelect?: boolean
  onChange: (values: string[]) => void
}

export default function FeedbackOptionGrid({
  options,
  selectedValues,
  multiSelect = false,
  onChange,
}: FeedbackOptionGridProps) {
  const toggleOption = (value: string) => {
    if (multiSelect) {
      if (selectedValues.includes(value)) {
        onChange(selectedValues.filter(v => v !== value))
      } else {
        onChange([...selectedValues, value])
      }
    } else {
      onChange([value])
    }
  }

  return (
    <View className='feedback-option-grid'>
      {options.map(opt => {
        const isSelected = selectedValues.includes(opt.value)
        return (
          <View
            key={opt.value}
            data-value={opt.value}
            className={`feedback-option-grid__item ${isSelected ? 'feedback-option-grid__item--selected' : ''}`}
            onClick={() => toggleOption(opt.value)}
          >
            {opt.label}
          </View>
        )
      })}
    </View>
  )
}
