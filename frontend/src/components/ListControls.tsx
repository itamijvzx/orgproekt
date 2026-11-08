import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { Button } from './ui/button'
import { Input } from './ui/input'

export function Pagination({
  total,
  offset,
  limit,
  onChange,
}: {
  total: number
  offset: number
  limit: number
  onChange: (offset: number) => void
}) {
  return (
    <div className="pagination">
      <span>
        {total ? `${offset + 1}–${Math.min(offset + limit, total)} из ${total}` : 'Нет записей'}
      </span>
      <div>
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
          aria-label="Предыдущая страница"
        >
          <ChevronLeft />
          Назад
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + limit >= total}
          onClick={() => onChange(offset + limit)}
          aria-label="Следующая страница"
        >
          Далее
          <ChevronRight />
        </Button>
      </div>
    </div>
  )
}
export function SearchField({
  value,
  onChange,
  label = 'Поиск',
}: {
  value: string
  onChange: (value: string) => void
  label?: string
}) {
  return (
    <div className="toolbar-search">
      <Search size={16} />
      <Input
        aria-label={label}
        maxLength={120}
        placeholder={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}
export function LoadState({
  loading,
  error,
  onRetry,
}: {
  loading: boolean
  error: string
  onRetry: () => void
}) {
  return loading ? (
    <div role="status" className="loading-state">
      Загружаем разрешённые данные…
    </div>
  ) : error ? (
    <div className="list-error error-box" role="alert">
      <span>{error}</span>
      <Button variant="outline" onClick={onRetry}>
        Повторить
      </Button>
    </div>
  ) : null
}
