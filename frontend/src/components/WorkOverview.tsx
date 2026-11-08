import { useState } from 'react'
import {
  ArrowRight,
  ArrowRightLeft,
  Building2,
  ChevronRight,
  Clock3,
  FileText,
  History,
  Info,
  UserRound,
  UserRoundPlus,
} from 'lucide-react'
import { client, result } from '@/lib/client'
import { dateLabel } from '@/lib/api'
import { auditLabel } from '@/lib/auditLabels'
import { useRemote } from '@/lib/useRemote'
import { useUI } from '@/lib/store'
import type { FunctionItem, Snapshot, User } from '@/lib/types'
import { Button } from './ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs'
import { LoadState, Pagination, SearchField } from './ListControls'
import { Status } from './Status'

type CoverageFilter = 'all' | 'uncovered' | 'unknown'
type Props = {
  data: Snapshot
  user: User
  revision: number
  onSource: (id: string) => void
  onFunction: (item: FunctionItem) => void
  onPropose?: (item: FunctionItem) => void
  onNavigate: (page: string) => void
}

function FunctionIssueDetail({
  id,
  data,
  user,
  revision,
  onSource,
  onFunction,
  onPropose,
}: Omit<Props, 'onNavigate'> & { id: string }) {
  const [retry, setRetry] = useState(0)
  const detail = useRemote(`overview-detail:${id}:${data.as_of}:${revision}:${retry}`, (signal) =>
    result(
      client.GET('/api/v1/functions/{function_id}', {
        params: { path: { function_id: id }, query: { as_of: data.as_of } },
        signal,
      }),
    ),
  )
  const item = detail.data
  const anchor = item?.source_anchor_id
  const source = useRemote(
    `overview-source:${id}:${anchor}:${data.as_of}:${revision}:${retry}`,
    (signal) =>
      anchor
        ? result(
            client.GET('/api/v1/sources/{anchor_id}', {
              params: { path: { anchor_id: anchor } },
              signal,
            }),
          )
        : Promise.resolve(null),
  )
  const department = data.departments.find((d) => d.id === item?.department_id)
  const positions = data.assignments
    .filter((a) => a.function_id === id && a.position_id)
    .map((a) => a.position_id)
  const appointments = data.seats
    .filter((s) => positions.includes(s.position_id))
    .flatMap((s) => s.appointments)
  const previous = appointments
    .filter((a) => a.end_date && a.end_date <= data.as_of)
    .sort((a, b) => (b.end_date ?? '').localeCompare(a.end_date ?? ''))[0]
  const temporary = item?.performers.filter((p) => p.kind === 'temporary') ?? []
  const canAppoint = user.roles.some(
    (g) => g.role === 'hr' && g.department_id === item?.department_id,
  )
  const canPropose =
    onPropose &&
    user.roles.some((g) => g.role === 'auditor' && g.department_id === item?.department_id)
  return (
    <section
      className="issue-detail"
      aria-label="Подробности выбранного вопроса"
      aria-busy={detail.loading}
    >
      <LoadState {...detail} onRetry={() => setRetry((n) => n + 1)} />
      {item && (
        <>
          <div className="detail-heading">
            <div className="detail-meta">
              <span>
                Функция <span className="detail-code">{item.code}</span>
              </span>
              <time dateTime={data.as_of}>{dateLabel(data.as_of)}</time>
            </div>
            <h2>{item.title}</h2>
            <Status status={item.status} />
          </div>
          <Tabs defaultValue="basis" className="detail-tabs">
            <TabsList variant="line" aria-label="Сведения о вопросе">
              <TabsTrigger value="basis">Основание</TabsTrigger>
              <TabsTrigger value="history">История покрытия</TabsTrigger>
            </TabsList>
            <TabsContent value="basis">
              <dl className="detail-facts">
                <div>
                  <span className="fact-icon">
                    <Building2 size={20} />
                  </span>
                  <div>
                    <dt>Подразделение</dt>
                    <dd>{department?.name ?? 'Не указано'}</dd>
                  </div>
                </div>
                <div>
                  <span className="fact-icon">
                    <UserRound size={20} />
                  </span>
                  <div>
                    <dt>Исполнитель</dt>
                    <dd>
                      {item.performers.map((p) => p.display_name).join(', ') ||
                        (item.status === 'unknown' ? 'Недостаточно данных' : 'Не зарегистрирован')}
                    </dd>
                  </div>
                </div>
                <div>
                  <span className="fact-icon">
                    <Clock3 size={20} />
                  </span>
                  <div>
                    <dt>Последнее назначение по должности</dt>
                    <dd>
                      {previous
                        ? `Завершено ${dateLabel(previous.end_date)}`
                        : positions.length
                          ? 'Завершённых назначений нет'
                          : 'Не установлено по данным реестра'}
                    </dd>
                  </div>
                </div>
                <div>
                  <span className="fact-icon">
                    <ArrowRightLeft size={20} />
                  </span>
                  <div>
                    <dt>Временное замещение</dt>
                    <dd>
                      {temporary.map((p) => p.display_name).join(', ') ||
                        (item.status === 'unknown'
                          ? 'Не установлено по реестру'
                          : 'Не зарегистрировано')}
                    </dd>
                  </div>
                </div>
              </dl>
              <div className="detail-evidence">
                <LoadState {...source} onRetry={() => setRetry((n) => n + 1)} />
                {source.data && (
                  <>
                    <div className="evidence-heading">
                      <FileText size={18} />
                      <span>
                        {source.data.synthetic
                          ? 'Учебное документальное основание'
                          : 'Документальное основание'}
                      </span>
                    </div>
                    <blockquote>{source.data.quote}</blockquote>
                    <button className="source-link" onClick={() => onSource(source.data!.id)}>
                      <span>
                        {source.data.title} · редакция {source.data.revision} ·{' '}
                        {source.data.locator}
                      </span>
                      <ChevronRight size={16} />
                    </button>
                  </>
                )}
              </div>
            </TabsContent>
            <TabsContent value="history">
              <div className="detail-history">
                <p className="muted">
                  Зарегистрированные периоды покрытия этой функции. Конец периода не включается.
                </p>
                {item.periods.length ? (
                  item.periods.map((period) => (
                    <div className="period" key={`${period.start}:${period.end}:${period.status}`}>
                      <Status status={period.status} />
                      <span>
                        {dateLabel(period.start)} — {dateLabel(period.end)}
                      </span>
                    </div>
                  ))
                ) : (
                  <p>Периоды покрытия не зарегистрированы.</p>
                )}
              </div>
            </TabsContent>
          </Tabs>
          <div className="detail-next">
            <p>
              {item.status === 'unknown'
                ? 'Уточните документальное закрепление и полноту реестра. Недостаток сведений не доказывает отсутствие исполнителя.'
                : 'Проверьте документальное закрепление исполнения и действующие назначения на выбранную дату.'}
            </p>
            <div className="detail-actions">
              {canPropose && (
                <Button onClick={() => onPropose(item)}>
                  <FileText size={18} />
                  Подготовить предложение
                  <ArrowRight size={18} />
                </Button>
              )}
              <Button variant={canPropose ? 'outline' : 'default'} onClick={() => onFunction(item)}>
                <UserRoundPlus size={18} />
                {canAppoint ? 'Перейти к назначению' : 'Открыть назначения'}
                <ArrowRight size={18} />
              </Button>
              <Button variant="outline" onClick={() => onSource(item.source_anchor_id)}>
                <FileText size={17} />
                Открыть источник
              </Button>
            </div>
          </div>
        </>
      )}
    </section>
  )
}

function RecentChanges({
  user,
  data,
  revision,
  onNavigate,
}: Pick<Props, 'user' | 'data' | 'revision' | 'onNavigate'>) {
  const [retry, setRetry] = useState(0)
  const state = useRemote(`overview-recent:${revision}:${retry}`, (signal) =>
    result(
      client.GET('/api/v1/audit', {
        params: { query: { category: 'business', limit: 3, offset: 0 } },
        signal,
      }),
    ),
  )
  return (
    <section className="recent-changes" aria-label="Последние изменения">
      <div className="section-heading">
        <h2>Последние изменения</h2>
        <Button variant="ghost" size="sm" onClick={() => onNavigate('audit')}>
          Открыть журнал
          <ArrowRight size={17} />
        </Button>
      </div>
      <LoadState {...state} onRetry={() => setRetry((n) => n + 1)} />
      {state.data && (
        <>
          {state.data.items.length ? (
            <div className="recent-rows">
              {state.data.items.map((event) => (
                <div className="recent-row" key={event.id}>
                  <History size={17} />
                  <time dateTime={event.occurred_at}>
                    {new Date(event.occurred_at).toLocaleString('ru-RU', {
                      timeZone: user.timezone,
                      day: 'numeric',
                      month: 'short',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </time>
                  <span>{auditLabel(event.kind)}</span>
                  <span className="muted">
                    {data.departments.find((d) => d.id === event.department_id)?.name ??
                      'В области доступа'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted recent-empty">
              В вашей области доступа нет зарегистрированных изменений.
            </p>
          )}
          <p className="recent-scope">
            Последние зарегистрированные события во всех доступных подразделениях; независимо от
            даты обзора.
          </p>
        </>
      )}
    </section>
  )
}

export function WorkOverview(props: Props) {
  const { data, revision } = props
  const {
    asOf,
    overviewQuery: q,
    overviewDepartment: department,
    overviewCoverage: coverage,
    set,
  } = useUI()
  const setQ = (value: string) => set({ overviewQuery: value })
  const setDepartment = (value: string) => set({ overviewDepartment: value })
  const setCoverage = (value: CoverageFilter) => set({ overviewCoverage: value })
  const [page, setPage] = useState({ key: '', offset: 0 })
  const [selection, setSelection] = useState({ key: '', id: '' })
  const [retry, setRetry] = useState(0)
  const filterKey = JSON.stringify([q, department, coverage, asOf, revision])
  const offset = page.key === filterKey ? page.offset : 0
  const requestKey = `${filterKey}:${offset}:${retry}`
  const state = useRemote(`overview:${requestKey}`, (signal) =>
    result(
      client.GET('/api/v1/overview', {
        params: {
          query: {
            q,
            department_id: department || undefined,
            coverage,
            as_of: asOf,
            offset,
            limit: 6,
          },
        },
        signal,
      }),
    ),
  )
  const items = state.data?.attention.items ?? []
  const selected =
    (selection.key === requestKey && items.find((f) => f.id === selection.id)) || items[0]
  return (
    <section className="work-overview" aria-label="Рабочие вопросы">
      <div className="overview-toolbar">
        <Tabs value={coverage} onValueChange={(v) => setCoverage(v as CoverageFilter)}>
          <TabsList aria-label="Вопросы покрытия">
            <TabsTrigger value="all">
              Все вопросы
              {coverage === 'all' && state.data && (
                <span className="issue-count">{state.data.attention.total}</span>
              )}
            </TabsTrigger>
            <TabsTrigger value="uncovered">Нет исполнителя</TabsTrigger>
            <TabsTrigger value="unknown">Недостаточно данных</TabsTrigger>
          </TabsList>
        </Tabs>
        <SearchField label="Найти вопрос" value={q} onChange={setQ} />
        <select
          aria-label="Подразделение вопросов"
          value={department}
          onChange={(e) => setDepartment(e.target.value)}
        >
          <option value="">Все отделы</option>
          {data.departments.map((d) => (
            <option value={d.id} key={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </div>
      <LoadState {...state} onRetry={() => setRetry((n) => n + 1)} />
      {state.data &&
        (items.length ? (
          <div className="overview-columns">
            <div className="issue-list" aria-label="Список вопросов">
              {items.map((f) => (
                <button
                  type="button"
                  className={`issue-card ${f.id === selected?.id ? 'selected' : ''}`}
                  key={f.id}
                  aria-pressed={f.id === selected?.id}
                  aria-label={`Открыть вопрос: ${f.title}`}
                  onClick={() => setSelection({ key: requestKey, id: f.id })}
                >
                  <span className="issue-icon">
                    {f.status === 'uncovered' ? <UserRound size={24} /> : <FileText size={24} />}
                  </span>
                  <span className="issue-copy">
                    <span className="issue-state">
                      <span aria-hidden="true" />
                      {f.status === 'uncovered' ? 'Требует проверки' : 'Неполные сведения'}
                    </span>
                    <strong>
                      {f.status === 'uncovered'
                        ? 'У функции нет исполнителя'
                        : 'Не установлено исполнение функции'}
                    </strong>
                    <span className="issue-description">{f.title}</span>
                    <span className="issue-department">
                      <Building2 size={15} />
                      {data.departments.find((d) => d.id === f.department_id)?.name}
                    </span>
                  </span>
                  <ChevronRight size={18} className="issue-chevron" />
                </button>
              ))}
              {state.data.attention.total > state.data.attention.limit && (
                <Pagination
                  {...state.data.attention}
                  onChange={(next) => setPage({ key: filterKey, offset: next })}
                />
              )}
            </div>
            {selected && (
              <FunctionIssueDetail
                key={`${selected.id}:${asOf}:${revision}`}
                id={selected.id}
                {...props}
              />
            )}
          </div>
        ) : (
          <div className="overview-empty empty-state">
            <Info size={24} />
            <h2>
              {q || department || coverage !== 'all'
                ? 'По этим условиям вопросов нет'
                : 'Вопросов покрытия на эту дату нет'}
            </h2>
            <p>
              {data.departments.length
                ? 'Обзор показывает функции без исполнителя и функции с неполными сведениями. Все документированные обязанности доступны в реестре.'
                : 'Бизнес-область не назначена. Права администратора доступа не открывают документы и сотрудников.'}
            </p>
            {(q || department || coverage !== 'all') && (
              <Button
                variant="outline"
                onClick={() => {
                  setQ('')
                  setDepartment('')
                  setCoverage('all')
                }}
              >
                Сбросить фильтры
              </Button>
            )}
            {!!data.departments.length && (
              <Button variant="ghost" onClick={() => props.onNavigate('functions')}>
                Открыть реестр функций
                <ArrowRight size={16} />
              </Button>
            )}
          </div>
        ))}
      <RecentChanges
        data={data}
        user={props.user}
        revision={revision}
        onNavigate={props.onNavigate}
      />
    </section>
  )
}
