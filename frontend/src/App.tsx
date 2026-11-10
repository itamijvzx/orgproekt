import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { motion, MotionConfig } from 'motion/react'
import {
  ArrowUpRight,
  Bell,
  ArrowRight,
  CalendarDays,
  FileClock,
  FileText,
  FileDiff,
  History,
} from 'lucide-react'
import { toast } from 'sonner'
import { Toaster } from '@/components/ui/sonner'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { AuthScreen } from '@/components/AuthScreen'
import { AccountSettings } from '@/components/AccountSettings'
import { SessionWatch } from '@/components/SessionWatch'
import { SourceDialog } from '@/components/SourceDialog'
import { PersonnelDialog, type PersonnelTarget } from '@/components/PersonnelDialog'
import { Status } from '@/components/Status'
import { WorkspaceHeader } from '@/components/WorkspaceHeader'
import { WorkOverview } from '@/components/WorkOverview'
import { ChangeNotifications } from '@/components/ChangeNotifications'
import { PRODUCT_NAME, workspaceName } from '@/lib/branding'
import { DocumentRegistry, EmployeeCard, FunctionRegistry } from '@/components/WorkspaceLists'
import { LoadState, Pagination, SearchField } from '@/components/ListControls'
import { client, result, errorMessage, setCsrf } from '@/lib/client'
import { useLocalDemo } from '@/lib/authPolicy'
import { dateLabel } from '@/lib/api'
import { auditLabel, auditOutcome } from '@/lib/auditLabels'
import { useRemote } from '@/lib/useRemote'
import type { FunctionItem, PersonnelEvent, User } from '@/lib/types'
import { useUI } from '@/lib/store'

const OrganizationView = lazy(() =>
  import('@/components/OrganizationView').then((module) => ({ default: module.OrganizationView })),
)
const AnalysisWorkspace = lazy(() =>
  import('@/components/AnalysisWorkspace').then((m) => ({ default: m.AnalysisWorkspace })),
)
const HybridSearch = lazy(() =>
  import('@/components/AnalysisWorkspace').then((m) => ({ default: m.HybridSearch })),
)
const ChangeWorkspace = lazy(() =>
  import('@/components/ChangeWorkspace').then((m) => ({ default: m.ChangeWorkspace })),
)
const ReportWorkspace = lazy(() =>
  import('@/components/ReportWorkspace').then((m) => ({ default: m.ReportWorkspace })),
)
const AccessWorkspace = lazy(() =>
  import('@/components/AccessWorkspace').then((m) => ({ default: m.AccessWorkspace })),
)
const copy: Record<string, { title: string; description: string }> = {
  access: {
    title: 'Доступ',
    description: 'Приглашения, назначения ролей и контроль учётных записей',
  },
  reports: { title: 'Отчёты', description: 'Проверенные заключения, выписки и история выпусков' },
  changes: {
    title: 'Предложения изменений',
    description: 'Версии, обсуждение, согласование и подтверждение исполнения',
  },
  comparisons: {
    title: 'Комплекты и сравнение',
    description: 'Точные редакции «до/после», изменения текста и рассмотрение кандидатов',
  },
  search: {
    title: 'Поиск по содержанию',
    description: 'Слова, смысл и проверяемые источники в вашей области доступа',
  },
  overview: {
    title: 'Рабочий обзор',
    description: 'Контроль функций и организационных изменений',
  },
  organization: {
    title: 'Организация',
    description: 'Подразделения, сотрудники и назначения на выбранную дату',
  },
  functions: {
    title: 'Реестр функций',
    description: 'Документированные обязанности, исполнители и периоды покрытия',
  },
  documents: {
    title: 'Документы',
    description: 'Сохранённые редакции и точные выдержки в вашей области доступа',
  },
  history: {
    title: 'Кадровые события',
    description: 'Официальные изменения с датами действия и документальными основаниями',
  },
  notifications: {
    title: 'Уведомления',
    description: 'Адресные события и переход к разрешённым объектам',
  },
  audit: { title: 'Журнал действий', description: 'Сохраняемая история изменений и обращений' },
  settings: {
    title: 'Моё пространство',
    description: 'Организация, безопасность входа и состояние сервисов',
  },
  about: { title: 'О проекте', description: 'Назначение, авторство и границы текущей версии' },
}
const eventLabels: Record<string, string> = {
  terminate: 'Назначение прекращено',
  appoint: 'Зарегистрировано назначение',
  correct_termination: 'Исправлена дата прекращения',
}
const securityNoticeLabels: Record<string, string> = {
  password_reset: 'Пароль изменён',
  mfa_replaced: 'Второй фактор обновлён',
  passkey_added: 'Подключён ключ доступа',
  passkey_revoked: 'Отозван ключ доступа',
  access_changed: 'Изменены права доступа',
  account_block: 'Учётная запись была заблокирована',
  account_unblock: 'Учётная запись разблокирована',
  'account_revoke-sessions': 'Сессии завершены администратором',
}

function EventHistory({
  user,
  revision,
  onSource,
  onInspect,
  onCorrect,
}: {
  user: User
  revision: number
  onSource: (id: string) => void
  onInspect: (event: PersonnelEvent) => void
  onCorrect: (event: PersonnelEvent) => void
}) {
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [retry, setRetry] = useState(0)
  const state = useRemote(`events:${q}:${offset}:${revision}:${retry}`, (signal) =>
    result(
      client.GET('/api/v1/personnel/events', {
        params: { query: { q, offset, limit: 10 } },
        signal,
      }),
    ),
  )
  return (
    <section className="content-panel history-panel">
      <div className="section-heading">
        <h2>Официальная история</h2>
      </div>
      <div className="filter-bar">
        <SearchField
          label="Поиск по должности"
          value={q}
          onChange={(v) => {
            setQ(v)
            setOffset(0)
          }}
        />
      </div>
      <LoadState {...state} onRetry={() => setRetry((v) => v + 1)} />
      {state.data && (
        <>
          {!state.data.total && (
            <Empty
              title="Кадровых событий нет"
              text="После регистрации события здесь появятся дата, основание и сохранённый расчёт покрытия."
            />
          )}
          <div className="event-timeline">
            {state.data.items.map((e) => (
              <article className="event-item" key={e.id}>
                <div className="event-dot">
                  <FileClock size={18} />
                </div>
                <div className="event-body">
                  <div className="event-meta">
                    <span>{dateLabel(e.effective_date)}</span>
                    {e.corrects_event_id && <span>Исправляющее событие</span>}
                  </div>
                  <h3>{eventLabels[e.kind]}</h3>
                  <p>{e.position_title}</p>
                  <div className="event-links">
                    <button className="source-link" onClick={() => onSource(e.source_anchor_id)}>
                      <FileText size={14} />
                      Документ-основание
                    </button>
                    <button className="source-link" onClick={() => onInspect(e)}>
                      Открыть штатную единицу
                      <ArrowUpRight size={14} />
                    </button>
                    {e.kind !== 'appoint' &&
                      user.roles.some(
                        (g) => g.role === 'hr' && g.department_id === e.department_id,
                      ) && (
                        <button className="source-link" onClick={() => onCorrect(e)}>
                          Исправить дату
                        </button>
                      )}
                  </div>
                  <small className="muted">
                    Зарегистрировано:{' '}
                    {new Date(e.registered_at).toLocaleString('ru-RU', { timeZone: user.timezone })}
                  </small>
                  <details className="event-details">
                    <summary>Сохранённый расчёт покрытия после события</summary>
                    {e.after.coverage.map((f) => (
                      <div key={f.function_id}>
                        {f.periods.map((p) => (
                          <div className="period" key={p.start}>
                            <Status status={p.status} />
                            <span>
                              {dateLabel(p.start)} — {p.end ? dateLabel(p.end) : 'открытый период'}
                            </span>
                          </div>
                        ))}
                      </div>
                    ))}
                  </details>
                </div>
              </article>
            ))}
          </div>
          <Pagination {...state.data} onChange={setOffset} />
        </>
      )}
    </section>
  )
}
function Notifications({
  revision,
  onInspect,
  onSource,
}: {
  revision: number
  onInspect: (e: PersonnelEvent) => void
  onSource: (id: string) => void
}) {
  const [offset, setOffset] = useState(0)
  const [tick, setTick] = useState(0)
  const [status, setStatus] = useState<'all' | 'read' | 'unread'>('all')
  const [operationError, setOperationError] = useState('')
  useEffect(() => {
    const timer = setInterval(() => setTick((v) => v + 1), 15000)
    return () => clearInterval(timer)
  }, [])
  const state = useRemote(`notices:${offset}:${status}:${tick}:${revision}`, (signal) =>
    result(
      client.GET('/api/v1/notifications', {
        params: { query: { offset, limit: 10, status } },
        signal,
      }),
    ),
  )
  return (
    <section className="content-panel notifications-panel">
      <div className="section-heading">
        <h2>Входящие события</h2>
        <select
          aria-label="Состояние уведомлений"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as typeof status)
            setOffset(0)
          }}
        >
          <option value="all">Все</option>
          <option value="unread">Непрочитанные</option>
          <option value="read">Прочитанные</option>
        </select>
      </div>
      {operationError && (
        <p role="alert" className="error-box">
          {operationError}
        </p>
      )}
      <LoadState {...state} onRetry={() => setTick((v) => v + 1)} />
      {state.data && (
        <>
          {state.data.items.map((n) => (
            <article key={n.id} className={`notification-item ${n.read_at ? '' : 'unread'}`}>
              <div className="notice-icon">
                <Bell size={18} />
              </div>
              <div>
                <h3>
                  {n.event
                    ? eventLabels[n.event.kind]
                    : (securityNoticeLabels[n.kind] ?? 'Изменена защита входа')}
                </h3>
                <p>{n.event?.position_title ?? 'Проверьте безопасность своего аккаунта.'}</p>
                <small className="muted">{n.read_at ? 'Прочитано' : 'Не прочитано'}</small>
                <div className="event-links">
                  {n.event && (
                    <>
                      <Button variant="outline" size="sm" onClick={() => onInspect(n.event!)}>
                        Открыть контекст
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onSource(n.event!.source_anchor_id)}
                      >
                        Основание
                      </Button>
                    </>
                  )}
                  {!n.read_at && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={async () => {
                        try {
                          await result(
                            client.POST('/api/v1/notifications/{notification_id}/read', {
                              params: { path: { notification_id: n.id } },
                            }),
                          )
                          setTick((v) => v + 1)
                        } catch (e) {
                          setOperationError(errorMessage(e))
                        }
                      }}
                    >
                      Отметить прочитанным
                    </Button>
                  )}
                </div>
              </div>
            </article>
          ))}
          {!state.data.total && (
            <Empty
              title="Уведомлений нет"
              text="События появляются после официальной регистрации и доставки в пределах ваших прав."
            />
          )}
          <Pagination {...state.data} onChange={setOffset} />
        </>
      )}
    </section>
  )
}
function AuditLog({ user, revision }: { user: User; revision: number }) {
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [retry, setRetry] = useState(0)
  const category = user.roles.some((g) => g.role === 'access_admin') ? 'security' : 'business'
  const state = useRemote(`audit:${q}:${offset}:${revision}:${retry}`, (signal) =>
    result(
      client.GET('/api/v1/audit', {
        params: { query: { q, offset, limit: 10, category } },
        signal,
      }),
    ),
  )
  return (
    <section className="content-panel">
      <div className="section-heading">
        <h2>{category === 'security' ? 'События безопасности' : 'Бизнес-история'}</h2>
      </div>
      <div className="filter-bar">
        <SearchField
          label="Поиск по типу события"
          value={q}
          onChange={(v) => {
            setQ(v)
            setOffset(0)
          }}
        />
      </div>
      <LoadState {...state} onRetry={() => setRetry((v) => v + 1)} />
      {state.data && (
        <>
          <Table className="audit-table">
            <TableHeader>
              <TableRow>
                <TableHead>Время</TableHead>
                <TableHead>Автор</TableHead>
                <TableHead>Действие</TableHead>
                <TableHead>Объект</TableHead>
                <TableHead>Результат</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {state.data.items.map((l) => (
                <TableRow key={l.id}>
                  <TableCell>
                    {new Date(l.occurred_at).toLocaleString('ru-RU', { timeZone: user.timezone })}
                  </TableCell>
                  <TableCell>{l.actor_display_name ?? 'Не указан'}</TableCell>
                  <TableCell>
                    <strong>{auditLabel(l.kind)}</strong>
                    <details className="audit-event-details">
                      <summary>Сведения о записи</summary>
                      <p>{l.kind}</p>
                      <span>
                        Обращение: <code>{l.correlation_id}</code>
                      </span>
                    </details>
                  </TableCell>
                  <TableCell>
                    {l.object_id ? (
                      <code className="audit-object">{l.object_id}</code>
                    ) : (
                      'Не указан'
                    )}
                  </TableCell>
                  <TableCell>{auditOutcome(l.kind, l.payload)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!state.data.total && (
            <Empty title="Нет доступных записей" text="Журнал ограничен вашей областью доступа." />
          )}
          <Pagination {...state.data} onChange={setOffset} />
        </>
      )}
    </section>
  )
}

function Workspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const localDemo = useLocalDemo()
  const { page, asOf, set } = useUI()
  const [revision, setRevision] = useState(0)
  const [source, setSource] = useState<string | null>(null)
  const [employee, setEmployee] = useState<string | null>(null)
  const [target, setTarget] = useState<PersonnelTarget | null>(null)
  const [proposalFunction, setProposalFunction] = useState<FunctionItem | null>(null)
  const [proposalId, setProposalId] = useState<string | null>(null)
  const state = useRemote(`snapshot:${asOf}:${revision}`, (signal) =>
    result(client.GET('/api/v1/organization', { params: { query: { as_of: asOf } }, signal })),
  )
  const data = state.data
  const name = workspaceName(data?.organization)
  useEffect(() => {
    document.title = `${name} · ${copy[page]?.title ?? 'Рабочий обзор'}`
  }, [name, page])
  const refresh = () => {
    setTarget(null)
    setRevision((v) => v + 1)
  }
  function selectSeat(id: string) {
    const seat = data?.seats.find((s) => s.id === id)
    const p = data?.positions.find((p) => p.id === seat?.position_id)
    if (seat && p)
      set({
        page: 'organization',
        selection: { kind: 'seat', id },
        department: p.department_id,
        expanded: [p.department_id],
      })
  }
  function inspect(event: PersonnelEvent) {
    const seat = data?.seats.find((s) => s.appointments.some((a) => a.id === event.appointment_id))
    if (seat) {
      selectSeat(seat.id)
      set({ asOf: event.effective_date })
    }
  }
  function correct(event: PersonnelEvent) {
    const seat = data?.seats.find((s) => s.appointments.some((a) => a.id === event.appointment_id))
    if (seat)
      setTarget({
        seat,
        action: 'correct_termination',
        appointment: seat.appointments.find((a) => a.id === event.appointment_id),
        corrects_event_id: event.id,
      })
  }
  function openFunction(f: FunctionItem) {
    const assignment = data?.assignments.find((a) => a.function_id === f.id && a.position_id)
    const seat = data?.seats.find((s) => s.position_id === assignment?.position_id)
    if (seat) selectSeat(seat.id)
    else
      set({
        page: 'organization',
        department: f.department_id,
        selection: { kind: 'department', id: f.department_id },
      })
  }
  const navigate = (id: string) => {
    set({ page: id })
  }
  const text = copy[page] ?? copy.overview
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        К содержимому
      </a>
      <div className="main-shell">
        <WorkspaceHeader
          name={name}
          page={page}
          user={user}
          onNavigate={navigate}
          onRefresh={refresh}
          onLogout={onLogout}
        />
        <main id="main-content" className="main-content">
          {localDemo && (
            <p className="small muted" role="status">
              Локальный учебный режим · MFA отключена · используйте только вымышленные данные
            </p>
          )}
          <div className="page-heading">
            <div>
              {page === 'overview' && <div className="workspace-context">Внутренний аудит</div>}
              <h1>{text.title}</h1>
              <p>{text.description}</p>
            </div>
            {page === 'overview' && (
              <div className="overview-heading-actions">
                <Button onClick={() => navigate('comparisons')}>
                  <FileDiff size={18} />
                  Сравнить документы
                  <ArrowRight size={18} />
                </Button>
                <label className="overview-date">
                  <CalendarDays size={17} />
                  <span className="sr-only">Дата рабочего обзора</span>
                  <input
                    type="date"
                    aria-label="Дата рабочего обзора"
                    value={asOf}
                    required
                    onChange={(e) => {
                      if (e.target.value) set({ asOf: e.target.value, selection: null })
                    }}
                  />
                </label>
              </div>
            )}
            {page === 'organization' && (
              <Button variant="outline" onClick={() => navigate('history')}>
                <History size={16} />
                История изменений
              </Button>
            )}
          </div>
          <LoadState {...state} onRetry={refresh} />
          {!user.roles.length && (
            <p role="status" className="content-panel">
              Учётная запись активна. Права на подразделение ещё не назначены или их срок
              завершился. Обратитесь к администратору доступа; после подтверждения назначения
              войдите заново.
            </p>
          )}
          {data && (
            <Suspense
              fallback={
                <div role="status" className="loading-state">
                  Загружаем представление…
                </div>
              }
            >
              <motion.div
                className="page-content"
                key={page}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.12 }}
              >
                {page === 'overview' && (
                  <WorkOverview
                    data={data}
                    user={user}
                    revision={revision}
                    onSource={setSource}
                    onFunction={openFunction}
                    onPropose={(item) => {
                      setProposalFunction(item)
                      navigate('changes')
                    }}
                    onNavigate={navigate}
                  />
                )}
                {page === 'organization' && (
                  <OrganizationView
                    data={data}
                    user={user}
                    revision={revision}
                    onSource={setSource}
                    onPersonnel={setTarget}
                    onEmployee={setEmployee}
                  />
                )}
                {page === 'functions' && (
                  <FunctionRegistry
                    data={data}
                    revision={revision}
                    onSource={setSource}
                    onFunction={openFunction}
                  />
                )}
                {page === 'documents' && (
                  <DocumentRegistry data={data} revision={revision} onSource={setSource} />
                )}
                {page === 'comparisons' && <AnalysisWorkspace data={data} onSource={setSource} />}
                {page === 'changes' && (
                  <ChangeWorkspace
                    data={data}
                    user={user}
                    revision={revision}
                    onSource={setSource}
                    initialFunction={proposalFunction}
                    onInitialFunctionHandled={() => setProposalFunction(null)}
                    initialProposalId={proposalId}
                    onInitialProposalHandled={() => setProposalId(null)}
                  />
                )}
                {page === 'search' && <HybridSearch data={data} onSource={setSource} />}
                {page === 'reports' && <ReportWorkspace onSource={setSource} />}
                {page === 'history' && (
                  <EventHistory
                    user={user}
                    revision={revision}
                    onSource={setSource}
                    onInspect={inspect}
                    onCorrect={correct}
                  />
                )}
                {page === 'notifications' && (
                  <>
                    <ChangeNotifications
                      revision={revision}
                      user={user}
                      onOpen={(id) => {
                        setProposalId(id)
                        navigate('changes')
                      }}
                    />
                    <Notifications revision={revision} onSource={setSource} onInspect={inspect} />
                  </>
                )}
                {page === 'audit' && <AuditLog user={user} revision={revision} />}
                {page === 'access' && <AccessWorkspace user={user} />}
                {page === 'settings' && (
                  <AccountSettings user={user} onLogout={onLogout} onSaved={refresh} />
                )}
                {page === 'about' && (
                  <section className="content-panel about-panel">
                    <h2>OrgProekt</h2>
                    <p>Документальный анализ структуры, назначений и организационных изменений.</p>
                    <p>
                      Автор: <strong>Синельников Иван Евгеньевич</strong>. Учебный портфолио-проект
                      со значительным применением AI-assisted разработки. Сотрудники и документы
                      вымышлены.
                    </p>
                    <h3>Текущая версия</h3>
                    <p>
                      Права и MFA, списки сотрудников и функций, документальные выдержки, кадровые
                      события, история покрытия и адресные уведомления. Загрузка оригиналов
                      DOCX/PDF, локальное извлечение текста, редакции и точные источники.
                      Зафиксированные комплекты «до/после», сравнение с историей рассмотрения и
                      гибридный поиск по словам и смыслу на локальной модели. Предложения с версиями
                      «до/после», обсуждения по отделам, закрытые заметки, адресное согласование,
                      отдельные управленческие решения и учёт исполнения. Проверка и выпуск отчётов
                      с PDF/DOCX, приглашения и управление правами доступа.
                    </p>
                    <h3>Пока не реализовано</h3>
                    <p>
                      Управляемый процесс LangGraph. Адаптер Gemini подготовлен; подключение и
                      реальный вызов проверяются владельцем отдельно.
                    </p>
                  </section>
                )}
              </motion.div>
            </Suspense>
          )}
        </main>
        <footer className="workspace-footer">
          <span>{name} · документальные основания и история решений</span>
          <span>Учебные данные · {dateLabel(asOf)}</span>
        </footer>
      </div>
      <SourceDialog id={source} onClose={() => setSource(null)} />
      {data && (
        <>
          <EmployeeCard
            id={employee}
            data={data}
            onClose={() => setEmployee(null)}
            onSource={setSource}
            onSeat={selectSeat}
          />
          <PersonnelDialog
            target={target}
            data={data}
            onClose={() => setTarget(null)}
            onSaved={() => {
              refresh()
              toast.success(
                'Событие зарегистрировано. Покрытие пересчитано, уведомление поставлено в очередь.',
              )
            }}
          />
        </>
      )}
    </div>
  )
}
function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  )
}
export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [initializing, setInitializing] = useState(true)
  const reset = useUI((s) => s.reset)
  const ready = useCallback(async () => {
    try {
      const u = await result(client.GET('/api/v1/auth/me'))
      setCsrf(u.csrf_token)
      if (u.roles.some((r) => r.role === 'access_admin')) useUI.getState().set({ page: 'access' })
      else if (!u.roles.length) useUI.getState().set({ page: 'settings' })
      setUser(u)
    } catch {
      setUser(null)
    } finally {
      setInitializing(false)
    }
  }, [])
  useEffect(() => {
    void ready()
    const expired = () => {
      setUser(null)
      setCsrf('')
      reset()
    }
    window.addEventListener('session-expired', expired)
    return () => window.removeEventListener('session-expired', expired)
  }, [ready, reset])
  async function logout() {
    try {
      await result(client.POST('/api/v1/auth/logout'))
    } catch (e) {
      toast.error(errorMessage(e))
      return
    }
    setUser(null)
    setCsrf('')
    reset()
  }
  return (
    <MotionConfig reducedMotion="user">
      {initializing ? (
        <div className="loading-screen" role="status">
          {PRODUCT_NAME} · проверяем сессию…
        </div>
      ) : user ? (
        <>
          <Workspace
            user={user}
            onLogout={() => {
              void logout()
            }}
          />
          <SessionWatch key={user.session_id} user={user} onRenewed={setUser} />
        </>
      ) : (
        <AuthScreen
          onReady={() => {
            void ready()
          }}
        />
      )}
      <Toaster position="bottom-right" closeButton />
    </MotionConfig>
  )
}
