import { useEffect, useState } from 'react'
import { ArrowUpRight, Building2, FileText, UsersRound } from 'lucide-react'
import { ApiError, client, result, errorMessage } from '@/lib/client'
import { useRemote } from '@/lib/useRemote'
import { dateLabel } from '@/lib/api'
import { useUI } from '@/lib/store'
import type { FunctionItem, Snapshot } from '@/lib/types'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog'
import { Status } from './Status'
import { LoadState, Pagination, SearchField } from './ListControls'

type Common = { data: Snapshot; revision: number; onSource: (id: string) => void }
function DepartmentFilter({
  data,
  value,
  onChange,
}: {
  data: Snapshot
  value: string
  onChange: (value: string) => void
}) {
  return (
    <select aria-label="Подразделение" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Все доступные подразделения</option>
      {data.departments.map((d) => (
        <option key={d.id} value={d.id}>
          {d.name}
        </option>
      ))}
    </select>
  )
}
export function DateField() {
  const { asOf, set } = useUI()
  return (
    <div className="toolbar-date">
      <label htmlFor="as-of">Состояние на</label>
      <Input
        id="as-of"
        type="date"
        value={asOf}
        onChange={(e) => {
          if (e.target.value) set({ asOf: e.target.value })
        }}
      />
    </div>
  )
}
function FunctionTable({
  items,
  data,
  onSource,
  onFunction,
}: {
  items: FunctionItem[]
  data: Snapshot
  onSource: (id: string) => void
  onFunction: (item: FunctionItem) => void
}) {
  return items.length ? (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Функция</TableHead>
          <TableHead>Подразделение</TableHead>
          <TableHead>Исполнитель на дату</TableHead>
          <TableHead>Покрытие</TableHead>
          <TableHead>Действия</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((f) => (
          <TableRow key={f.id}>
            <TableCell>
              <small className="table-kicker">{f.code}</small>
              <strong>{f.title}</strong>
            </TableCell>
            <TableCell>{data.departments.find((d) => d.id === f.department_id)?.name}</TableCell>
            <TableCell>
              {f.performers.map((p) => p.display_name).join(', ') || 'Не установлен'}
            </TableCell>
            <TableCell>
              <Status status={f.status} />
            </TableCell>
            <TableCell>
              <div className="row-actions">
                <Button variant="outline" size="sm" onClick={() => onFunction(f)}>
                  Открыть функцию
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Основание ${f.code}`}
                  onClick={() => onSource(f.source_anchor_id)}
                >
                  <FileText size={16} />
                </Button>
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ) : (
    <div className="empty-state">
      <h3>Записей не найдено</h3>
      <p>Измените поиск, подразделение или состояние покрытия.</p>
    </div>
  )
}
export function FunctionRegistry({
  data,
  revision,
  onSource,
  onFunction,
  overview = false,
}: Common & { onFunction: (item: FunctionItem) => void; overview?: boolean }) {
  const { asOf } = useUI()
  const [q, setQ] = useState('')
  const [department, setDepartment] = useState('')
  const [offset, setOffset] = useState(0)
  const [retry, setRetry] = useState(0)
  const [coverage, setCoverage] = useState<
    'all' | 'uncovered' | 'unknown' | 'covered' | 'abolished' | 'not_effective'
  >('all')
  useEffect(() => setOffset(0), [asOf, revision])
  const query = {
    as_of: asOf,
    q,
    department_id: department || undefined,
    offset,
    limit: 10,
    coverage,
  }
  const resource = useRemote(
    `${overview}:${JSON.stringify(query)}:${revision}:${retry}`,
    async (signal) => {
      if (overview)
        return result(
          client.GET('/api/v1/overview', {
            params: {
              query: {
                ...query,
                coverage: coverage === 'uncovered' || coverage === 'unknown' ? coverage : 'all',
              },
            },
            signal,
          }),
        )
      const page = await result(client.GET('/api/v1/functions', { params: { query }, signal }))
      return { attention: page, counts: null, unavailable: [] }
    },
  )
  return (
    <section className="content-panel function-registry">
      <div className="section-heading">
        <h2>{overview ? 'Требует внимания' : 'Документированные функции'}</h2>
        <DateField />
      </div>
      <div className="filter-bar">
        <SearchField
          label="Поиск по функциям"
          value={q}
          onChange={(v) => {
            setQ(v)
            setOffset(0)
          }}
        />
        <DepartmentFilter
          data={data}
          value={department}
          onChange={(v) => {
            setDepartment(v)
            setOffset(0)
          }}
        />
        <select
          aria-label="Покрытие функций"
          value={coverage}
          onChange={(e) => {
            setCoverage(e.target.value as typeof coverage)
            setOffset(0)
          }}
        >
          <option value="all">{overview ? 'Требует внимания' : 'Всё покрытие'}</option>
          <option value="uncovered">Нет исполнителя</option>
          <option value="unknown">Недостаточно данных</option>
          {!overview && (
            <>
              <option value="covered">Есть исполнитель</option>
              <option value="abolished">Упразднена</option>
            </>
          )}
        </select>
      </div>
      <LoadState {...resource} onRetry={() => setRetry((v) => v + 1)} />
      {resource.data && (
        <>
          <FunctionTable
            items={resource.data.attention.items}
            data={data}
            onSource={onSource}
            onFunction={onFunction}
          />
          <Pagination {...resource.data.attention} onChange={setOffset} />
          {overview && (
            <div className="availability-note">
              <strong>Задания и согласования</strong>
              <p>
                Эти процессы ещё не реализованы. Здесь показаны реальные вопросы покрытия в вашей
                области доступа.
              </p>
            </div>
          )}
        </>
      )}
    </section>
  )
}

export function OrganizationDirectory({
  data,
  revision,
  onEmployee,
  onDepartment,
}: {
  data: Snapshot
  revision: number
  onEmployee: (id: string) => void
  onDepartment: (id: string) => void
}) {
  const { asOf, query, department, set } = useUI()
  const [offset, setOffset] = useState(0)
  const [retry, setRetry] = useState(0)
  const [status, setStatus] = useState<'all' | 'active' | 'former' | 'unassigned'>('all')
  useEffect(() => setOffset(0), [asOf, department, revision])
  const showPeople = !!department || !!query
  const key = JSON.stringify([asOf, query, department, status, offset, revision, retry])
  const departments = useRemote(key + ':departments', (signal) =>
    result(
      client.GET('/api/v1/departments', { params: { query: { as_of: asOf, limit: 100 } }, signal }),
    ),
  )
  const employees = useRemote(key + ':people', (signal) =>
    result(
      client.GET('/api/v1/directory/employees', {
        params: {
          query: {
            as_of: asOf,
            q: query,
            department_id: department || undefined,
            status,
            limit: 10,
            offset,
          },
        },
        signal,
      }),
    ),
  )
  const reset = () => {
    set({ query: '', department: '' })
    setStatus('all')
    setOffset(0)
  }
  const state = showPeople ? employees : departments
  return (
    <div className="directory-list">
      <div className="filter-bar">
        <SearchField
          label="Поиск по сотрудникам и должностям"
          value={query}
          onChange={(v) => {
            set({ query: v })
            setOffset(0)
          }}
        />
        <DepartmentFilter
          data={data}
          value={department}
          onChange={(v) => {
            set({ department: v })
            setOffset(0)
          }}
        />
        {showPeople && (
          <select
            aria-label="Состояние назначения"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as typeof status)
              setOffset(0)
            }}
          >
            <option value="all">Все сотрудники</option>
            <option value="active">Действующее назначение</option>
            <option value="former">Назначение завершено</option>
            <option value="unassigned">Без назначения</option>
          </select>
        )}
        {showPeople && (
          <Button variant="ghost" onClick={reset}>
            Все подразделения
          </Button>
        )}
      </div>
      <LoadState {...state} onRetry={() => setRetry((v) => v + 1)} />
      {!showPeople && departments.data && (
        <div className="department-list">
          {departments.data.items.map((d) => (
            <div className="department-row" key={d.id}>
              <Building2 size={20} />
              <div>
                <h3>{d.name}</h3>
                <p>
                  С действующим назначением: {d.employee_count} · должностей: {d.position_count} ·{' '}
                  штатных единиц: {d.seat_count}
                </p>
              </div>
              <Button
                variant="outline"
                onClick={() => {
                  set({ department: d.id })
                  setOffset(0)
                }}
              >
                Раскрыть состав
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Штатные единицы ${d.name}`}
                onClick={() => onDepartment(d.id)}
              >
                <ArrowUpRight size={17} />
              </Button>
            </div>
          ))}
          {!departments.data.total && (
            <div className="empty-state">
              <h3>Нет назначенной области</h3>
              <p>Администратор доступа не получает бизнес-данные автоматически.</p>
            </div>
          )}
        </div>
      )}
      {showPeople && employees.data && (
        <>
          {employees.data.items.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Сотрудник</TableHead>
                  <TableHead>Подразделение / должность</TableHead>
                  <TableHead>Назначение</TableHead>
                  <TableHead>Функции</TableHead>
                  <TableHead>
                    <span className="sr-only">Карточка</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {employees.data.items.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell>
                      <button
                        className="employee-name"
                        aria-label={`Открыть карточку: ${e.display_name}`}
                        onClick={() => onEmployee(e.id)}
                      >
                        {e.display_name}
                      </button>
                    </TableCell>
                    <TableCell>
                      <small className="table-kicker">{e.department_name}</small>
                      {e.appointments
                        .filter((a) => a.active)
                        .map((a) => a.position_title)
                        .join(', ') || 'Нет действующей должности'}
                    </TableCell>
                    <TableCell>
                      {e.status === 'active'
                        ? e.appointments.some((a) => a.active && a.kind === 'temporary')
                          ? 'Временное замещение'
                          : 'Действующее'
                        : e.status === 'former'
                          ? 'Завершено'
                          : 'Не назначен'}
                    </TableCell>
                    <TableCell>{e.function_count}</TableCell>
                    <TableCell>
                      <Button size="sm" variant="outline" onClick={() => onEmployee(e.id)}>
                        Карточка
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="empty-state">
              <h3>Совпадений нет</h3>
              <p>Проверьте поиск и фильтры. Закрытые сведения других отделов не включаются.</p>
              <Button variant="outline" onClick={reset}>
                Сбросить фильтры
              </Button>
            </div>
          )}
          <Pagination {...employees.data} onChange={setOffset} />
        </>
      )}
    </div>
  )
}

export function EmployeeCard({
  id,
  data,
  onClose,
  onSource,
  onSeat,
}: {
  id: string | null
  data: Snapshot
  onClose: () => void
  onSource: (id: string) => void
  onSeat: (id: string) => void
}) {
  const [retry, setRetry] = useState(0)
  const state = useRemote(`${id}:${data.as_of}:${retry}`, async (signal) =>
    id
      ? result(
          client.GET('/api/v1/directory/employees/{employee_id}', {
            params: { path: { employee_id: id }, query: { as_of: data.as_of } },
            signal,
          }),
        )
      : null,
  )
  return (
    <Dialog
      open={!!id}
      onOpenChange={(v) => {
        if (!v) onClose()
      }}
    >
      <DialogContent className="employee-dialog">
        <DialogHeader>
          <DialogTitle>{state.data?.display_name ?? 'Карточка сотрудника'}</DialogTitle>
          <DialogDescription>
            Разрешённые назначения и документированные функции на {dateLabel(data.as_of)}
          </DialogDescription>
        </DialogHeader>
        <LoadState {...state} onRetry={() => setRetry((v) => v + 1)} />
        {state.data && (
          <>
            <p className="muted">
              <UsersRound size={16} />
              {state.data.department_name}
            </p>
            <h3>Назначения и история</h3>
            {!state.data.appointments.length && <p>Назначения не зарегистрированы.</p>}
            {state.data.appointments.map((a) => (
              <section className="appointment-block" key={a.id}>
                <strong>
                  {a.position_title} · {a.seat_code}
                </strong>
                <p>
                  {a.kind === 'temporary' ? 'Временное замещение' : 'Постоянное назначение'} ·{' '}
                  {a.active ? 'действует' : 'не действует на дату'}
                </p>
                <p>
                  {dateLabel(a.start_date)} — {dateLabel(a.end_date)}
                </p>
                <div className="row-actions">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      onClose()
                      onSeat(a.seat_id)
                    }}
                  >
                    Штатная единица
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onSource(a.source_anchor_id)}>
                    Основание
                  </Button>
                </div>
              </section>
            ))}
            <h3>Документированные функции</h3>
            {state.data.functions.map((f) => (
              <div className="function-detail" key={f.id}>
                <strong>{f.title}</strong>
                <Status status={f.status} />
                <button className="source-link" onClick={() => onSource(f.source_anchor_id)}>
                  <FileText size={14} />
                  Открыть основание
                </button>
              </div>
            ))}
            {!state.data.functions.length && (
              <p>
                Подтверждённых функций на эту дату нет. Это не оценка навыков или
                производительности.
              </p>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

export { DocumentRegistry } from './Documents'

export function OrganizationName({ onSaved }: { onSaved: () => void }) {
  const [revision, setRevision] = useState(0)
  const [name, setName] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const state = useRemote(`org-name:${revision}`, (signal) =>
    result(client.GET('/api/v1/organization/settings', { signal })),
  )
  return (
    <section className="content-panel organization-name-form">
      <h2>Название организации</h2>
      <p className="muted">
        Пустое поле показывает OrgProekt в шапке. Прежние документы и история сохраняются.
      </p>
      <LoadState {...state} onRetry={() => setRevision((v) => v + 1)} />
      {state.data && (
        <form
          onSubmit={async (e) => {
            e.preventDefault()
            if (busy) return
            setBusy(true)
            setError('')
            setConflict(false)
            try {
              await result(
                client.PUT('/api/v1/organization/settings', {
                  body: {
                    name: (name ?? state.data!.name ?? '').trim() || null,
                    expected_version: state.data!.version,
                  },
                }),
              )
              setName(null)
              setRevision((v) => v + 1)
              onSaved()
            } catch (e) {
              setError(errorMessage(e))
              setConflict(e instanceof ApiError && e.status === 409)
            } finally {
              setBusy(false)
            }
          }}
        >
          <label htmlFor="organization-name">Отображаемое название</label>
          <p className="small muted">Текущее название: {state.data.display_name}</p>
          <Input
            id="organization-name"
            maxLength={160}
            placeholder="Название организации"
            disabled={!state.data.can_edit}
            value={name ?? state.data.name ?? ''}
            onChange={(e) => setName(e.target.value)}
          />
          {state.data.can_edit ? (
            <Button disabled={busy}>{busy ? 'Сохраняем…' : 'Сохранить название'}</Button>
          ) : (
            <p className="small muted">
              Изменение доступно HR или руководителю с полномочиями на всю организацию.
            </p>
          )}
          {error && (
            <div className="error-box" role="alert">
              {error}
            </div>
          )}
          {conflict && (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setRevision((value) => value + 1)
                setConflict(false)
                setError(
                  'Введённый текст сохранён. После загрузки проверьте текущее название и повторите сохранение.',
                )
              }}
            >
              Загрузить актуальную версию
            </Button>
          )}
        </form>
      )}
    </section>
  )
}
