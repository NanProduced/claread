function getUTC8DayValue(ts: number): number {
  const d = new Date(ts + 8 * 3600 * 1000)
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
}

function formatDate(timestamp: number): string {
  const getParts = (ts: number) => {
    const d = new Date(ts + 8 * 3600 * 1000)
    return {
      year: d.getUTCFullYear(),
      month: d.getUTCMonth(),
      day: d.getUTCDate(),
      hours: d.getUTCHours(),
      minutes: d.getUTCMinutes(),
    }
  }

  const nowParts = getParts(Date.now())
  const dateParts = getParts(timestamp)

  const isToday = dateParts.year === nowParts.year
    && dateParts.month === nowParts.month
    && dateParts.day === nowParts.day

  if (isToday) {
    return `今天 ${String(dateParts.hours).padStart(2, '0')}:${String(dateParts.minutes).padStart(2, '0')}`
  }

  const dayDiff = Math.round(
    (getUTC8DayValue(Date.now()) - getUTC8DayValue(timestamp)) / (24 * 60 * 60 * 1000)
  )

  if (dayDiff === 1) {
    return '昨天'
  }

  if (dayDiff > 1 && dayDiff < 7) {
    return `${dayDiff}天前`
  }

  return `${dateParts.month + 1}月${dateParts.day}日`
}

export { formatDate, getUTC8DayValue }
