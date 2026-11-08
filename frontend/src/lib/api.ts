// Formatting helpers. All network operations use the generated OpenAPI client in client.ts.
export const dateLabel = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat('ru-RU', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        timeZone: 'UTC',
      }).format(new Date(`${value.slice(0, 10)}T12:00:00Z`))
    : 'по настоящее время'
export const localToday = () =>
  new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Almaty',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
