import type { CommonEvent, ITouchEvent } from '@tarojs/components/types/common'

type ClickEvent = ITouchEvent
type InputEvent = CommonEvent<{ value: string }>
type ChooseAvatarEvent = CommonEvent<{ avatarUrl: string }>
type StopPropagationEvent = CommonEvent

export type { ClickEvent, InputEvent, ChooseAvatarEvent, StopPropagationEvent }
