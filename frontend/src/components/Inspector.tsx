import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { ArrowUpRight, CalendarDays, FileText, Plus, UserRound, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Status } from './Status'
import type { FunctionItem, Period, Snapshot, User } from '@/lib/types'
import type { PersonnelTarget } from './PersonnelDialog'
import { useUI } from '@/lib/store'
import { dateLabel } from '@/lib/api'
import { client, result, errorMessage } from '@/lib/client'

function FunctionDetail({
  item,
  onSource,
}: {
  item: FunctionItem
  onSource: (id: string) => void
}) {
  const [periods, setPeriods] = useState<Period[] | null>(null)
  const [error, setError] = useState('')
  return (
    <div className="function-detail">
      <div className="function-code">
        {item.code}
        <Status status={item.status} compact />
      </div>
      <h4>{item.title}</h4>
      <button className="source-link" onClick={() => onSource(item.source_anchor_id)}>
        <FileText size={14} />
        Открыть основание
        <ArrowUpRight size={13} />
      </button>
      <details
        onToggle={(e) => {
          if (e.currentTarget.open && !periods)
            result(
              client.GET('/api/v1/functions/{function_id}', {
                params: { path: { function_id: item.id } },
              }),
            )
              .then((r) => setPeriods(r.periods))
              .catch((e) => setError(errorMessage(e)))
        }}
      >
        <summary>Периоды покрытия</summary>
        {error && <p role="alert">{error}</p>}
        {!periods && !error ? (
          <p className="small muted">Загрузка…</p>
        ) : (
          periods?.map((p) => (
            <div className="period" key={p.start}>
              <Status status={p.status} compact />
              <span>
                {dateLabel(p.start)} — {p.end ? dateLabel(p.end) : 'открытый период'}
              </span>
            </div>
          ))
        )}
      </details>
    </div>
  )
}

export function Inspector({
  data,
  user,
  onSource,
  onPersonnel,
}: {
  data: Snapshot
  user: User
  onSource: (id: string) => void
  onPersonnel: (t: PersonnelTarget) => void
}) {
  const { selection, set } = useUI()
  const seat =
    selection?.kind === 'seat' ? data.seats.find((s) => s.id === selection.id) : undefined
  const position = data.positions.find(
    (p) => p.id === (seat?.position_id ?? (selection?.kind === 'position' ? selection.id : null)),
  )
  const department = data.departments.find(
    (d) =>
      d.id ===
      (position?.department_id ?? (selection?.kind === 'department' ? selection.id : null)),
  )
  const functions = data.functions.filter((f) =>
    position
      ? data.assignments.some((a) => a.function_id === f.id && a.position_id === position.id)
      : f.department_id === department?.id,
  )
  const departmentSeats = data.seats.filter((s) =>
    position
      ? s.position_id === position.id
      : data.positions.some((p) => p.id === s.position_id && p.department_id === department?.id),
  )
  const canWrite = user.roles.some((g) => g.role === 'hr' && g.department_id === department?.id)
  useEffect(() => {
    if (selection && !department) set({ selection: null })
  }, [selection, department, set])
  if (!selection || !department) return null
  return (
    <aside className="inspector">
      <AnimatePresence mode="wait">
        <motion.div
          key={`${selection.kind}-${selection.id}`}
          initial={{ opacity: 0, x: 8 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.14 }}
        >
          <div className="inspector-label">
            {selection.kind === 'department'
              ? 'ПОДРАЗДЕЛЕНИЕ'
              : selection.kind === 'position'
                ? 'ДОЛЖНОСТЬ'
                : 'ШТАТНАЯ ЕДИНИЦА'}
            <Button
              variant="ghost"
              size="icon"
              aria-label="Закрыть инспектор"
              onClick={() => set({ selection: null })}
            >
              <X size={16} />
            </Button>
          </div>
          <div className="inspector-heading">
            <span className="department-code">{seat?.code ?? department.code}</span>
            <h2>{position?.title ?? department.name}</h2>
            <p>{position ? department.name : 'Документированная структура'}</p>
          </div>
          <div className="inspector-date">
            <CalendarDays size={14} />
            <span>Состояние на {dateLabel(data.as_of)}</span>
          </div>
          <Tabs defaultValue="overview" className="inspector-tabs">
            <TabsList>
              <TabsTrigger value="overview">Состав</TabsTrigger>
              <TabsTrigger value="functions">
                Функции <span>{functions.length}</span>
              </TabsTrigger>
            </TabsList>
            <TabsContent value="overview">
              <div className="inspector-section">
                <h3>{seat ? 'Назначения' : 'Штатные единицы'}</h3>
                {(seat ? [seat] : departmentSeats).map((s) => (
                  <div className="appointment-block" key={s.id}>
                    {!seat && (
                      <button
                        className="seat-link"
                        onClick={() => set({ selection: { kind: 'seat', id: s.id } })}
                      >
                        {data.positions.find((p) => p.id === s.position_id)?.title}
                        <ArrowUpRight size={14} />
                      </button>
                    )}
                    {s.appointments.filter((a) => a.active).length === 0 && (
                      <div className="vacancy">
                        <UserRound size={16} />
                        <div>
                          <strong>Штатная единица вакантна</strong>
                          <span>На выбранную дату нет назначения</span>
                        </div>
                      </div>
                    )}
                    {s.appointments.map((a) => (
                      <div className={`appointment ${a.active ? '' : 'past'}`} key={a.id}>
                        <div className="person-avatar">
                          {a.employee_name
                            .split(' ')
                            .map((p) => p[0])
                            .slice(0, 2)
                            .join('')}
                        </div>
                        <div>
                          <strong>{a.employee_name}</strong>
                          <span>
                            {a.kind === 'temporary'
                              ? 'Временное назначение'
                              : 'Постоянное назначение'}
                            {!a.active ? ' · не действует на дату' : ''}
                          </span>
                          <small>
                            {dateLabel(a.start_date)} — {dateLabel(a.end_date)}
                          </small>
                          <button
                            className="source-link"
                            onClick={() => onSource(a.source_anchor_id)}
                          >
                            <FileText size={12} />
                            Основание
                          </button>
                          {canWrite && !a.end_date && (
                            <button
                              className="text-button danger-text"
                              onClick={() =>
                                onPersonnel({ seat: s, action: 'terminate', appointment: a })
                              }
                            >
                              Прекратить назначение
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                    {canWrite && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="w-full"
                        onClick={() => onPersonnel({ seat: s, action: 'appoint' })}
                      >
                        <Plus size={14} />
                        Назначить исполнителя
                      </Button>
                    )}
                  </div>
                ))}
              </div>
              <div className="inspector-section">
                <h3>
                  Покрытие функций <span>{functions.length}</span>
                </h3>
                {functions.map((f) => (
                  <FunctionDetail
                    key={`${f.id}-${data.as_of}-${data.seats.map((s) => s.version).join('-')}`}
                    item={f}
                    onSource={onSource}
                  />
                ))}
              </div>
            </TabsContent>
            <TabsContent value="functions">
              <div className="inspector-section">
                {functions.length ? (
                  functions.map((f) => <FunctionDetail key={f.id} item={f} onSource={onSource} />)
                ) : (
                  <p className="empty-copy">Документированные функции пока не закреплены.</p>
                )}
              </div>
            </TabsContent>
          </Tabs>
        </motion.div>
      </AnimatePresence>
    </aside>
  )
}
