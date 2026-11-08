import { Check, CircleDashed, TriangleAlert, Minus } from 'lucide-react'
import type { Coverage } from '@/lib/types'
const labels: Record<Coverage, string> = {
  covered: 'Есть исполнитель',
  uncovered: 'Нет исполнителя',
  unknown: 'Недостаточно данных',
  abolished: 'Упразднена',
  not_effective: 'Ещё не действует',
}
export function Status({ status, compact = false }: { status: Coverage; compact?: boolean }) {
  const Icon =
    status === 'covered'
      ? Check
      : status === 'uncovered'
        ? TriangleAlert
        : status === 'unknown'
          ? CircleDashed
          : Minus
  return (
    <span className={`status status-${status}`}>
      <Icon size={compact ? 12 : 14} />
      {labels[status]}
    </span>
  )
}
