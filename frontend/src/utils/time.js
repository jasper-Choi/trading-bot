const KST_ZONE = 'Asia/Seoul'

function parseDate(value) {
  if (!value) return null
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function partsFor(date) {
  return Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: KST_ZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
      .formatToParts(date)
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value])
  )
}

export function getKstClock() {
  return partsFor(new Date())
}

export function formatKstTime(value) {
  const date = parseDate(value)
  if (!date) return '--:--'
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: KST_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatKstDateTime(value) {
  const date = parseDate(value)
  if (!date) return '--'
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: KST_ZONE,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}
