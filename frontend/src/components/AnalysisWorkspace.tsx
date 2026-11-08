import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, FileDiff, FolderOpen, Plus, Search } from 'lucide-react'
import { client, result, errorMessage } from '@/lib/client'
import { dateLabel, localToday } from '@/lib/api'
import { useRemote } from '@/lib/useRemote'
import type { Snapshot } from '@/lib/types'
import type { components, paths } from '@/lib/schema.generated'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'
import { LoadState, Pagination, SearchField } from './ListControls'

type S = components['schemas']
type Corpus = S['CorpusView']
type Run = S['ComparisonView']
type Finding = S['FindingView']
type Job = S['AnalysisJobView']
type Category = Finding['category']
type Evidence = S['Evidence']
type Props = { data: Snapshot; onSource: (id: string) => void }
const categories: Record<Category, string> = {
  unchanged: 'Без изменения текста',
  possible_rephrasing: 'Возможное переформулирование',
  possible_transfer: 'Возможный перенос',
  possible_duplication: 'Возможное дублирование',
  split: 'Возможное разделение',
  merge: 'Возможное объединение',
  changed_content: 'Изменение содержания',
  new_text: 'Новый текст в комплекте',
  documented_abolition: 'Текст об упразднении',
  not_found: 'Не найдено в комплекте',
  conflict: 'Возможное противоречие',
  insufficient_data: 'Недостаточно данных',
}
const decisions: Record<string, string> = {
  confirmed: 'Кандидат подтверждён аудитором',
  dismissed: 'Кандидат отклонён',
  needs_evidence: 'Нужны дополнительные основания',
}
const busy = (job: Job | undefined) => !!job && ['queued', 'processing'].includes(job.status)
function useKey() {
  const ref = useRef({ signature: '', key: '' })
  return (body: unknown) => {
    const signature = JSON.stringify(body)
    if (ref.current.signature !== signature) ref.current = { signature, key: crypto.randomUUID() }
    return { 'idempotency-key': ref.current.key }
  }
}
function Notes({ items }: { items: string[] }) {
  return items.length ? (
    <details className="analysis-notes">
      <summary>Ограничения и условия ({items.length})</summary>
      <ul>
        {items.map((n, i) => (
          <li key={i}>{n}</li>
        ))}
      </ul>
    </details>
  ) : null
}
function JobState({ job, refresh }: { job: Job; refresh: () => void }) {
  const [error, setError] = useState(''),
    [pending, setPending] = useState(false)
  async function command(operation: 'retry' | 'cancel') {
    setPending(true)
    setError('')
    try {
      await result(
        client.POST('/api/v1/analysis-jobs/{job_id}/{operation}', {
          params: { path: { job_id: job.id, operation } },
          body: { expected_version: job.version },
        }),
      )
      refresh()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <div className="analysis-job">
      <p role="status">{job.message}</p>
      <div className="analysis-actions">
        {job.can_cancel && (
          <Button size="sm" variant="outline" disabled={pending} onClick={() => command('cancel')}>
            Отменить обработку
          </Button>
        )}
        {job.can_retry && (
          <Button size="sm" variant="outline" disabled={pending} onClick={() => command('retry')}>
            Повторить обработку
          </Button>
        )}
        {error && (
          <span role="alert" className="error-box">
            {error}
          </span>
        )}
      </div>
    </div>
  )
}
function CorpusSummary({ value }: { value: Corpus }) {
  return (
    <div className="corpus-summary">
      <h3>{value.title}</h3>
      <p className="muted small">
        На {dateLabel(value.as_of)} · {value.documents.length} редакций · {value.fragment_count}{' '}
        фрагментов
      </p>
      <details>
        <summary>Зафиксированные редакции</summary>
        <ul>
          {value.documents.map((d) => (
            <li key={d.id}>
              <strong>{d.title}</strong> · {d.revision}
              <br />
              <span className="muted small">
                {d.anchor_count} фрагментов ·{' '}
                {d.processing_status === 'manual_excerpt'
                  ? 'Ручная выдержка'
                  : d.processing_status === 'ready'
                    ? 'Текст извлечён'
                    : 'Есть ограничения извлечения'}{' '}
                · начало действия:{' '}
                {d.effective_from ? dateLabel(d.effective_from) : 'не установлено'}
              </span>
            </li>
          ))}
        </ul>
        <p className="integrity-hash small">SHA-256 комплекта: {value.digest}</p>
      </details>
      <Notes items={value.limitations} />
    </div>
  )
}
function CorpusPicker({
  label,
  value,
  onChange,
}: {
  label: string
  value: Corpus | null
  onChange: (c: Corpus | null) => void
}) {
  const [open, setOpen] = useState(false),
    [q, setQ] = useState(''),
    [offset, setOffset] = useState(0),
    [retry, setRetry] = useState(0)
  const state = useRemote(`corpus-pick:${open}:${q}:${offset}:${retry}`, (signal) =>
    open
      ? result(
          client.GET('/api/v1/corpora', { params: { query: { q, offset, limit: 10 } }, signal }),
        )
      : Promise.resolve(null),
  )
  return (
    <div className="corpus-picker">
      <span className="form-label">{label}</span>
      <div className="analysis-actions">
        <Button type="button" variant="outline" aria-label={label} onClick={() => setOpen(true)}>
          {value?.title ?? 'Выбрать комплект'}
        </Button>
        {value && (
          <Button
            type="button"
            variant="ghost"
            aria-label={`Сбросить ${label.toLowerCase()}`}
            onClick={() => onChange(null)}
          >
            Сбросить
          </Button>
        )}
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="analysis-dialog">
          <DialogHeader>
            <DialogTitle>{label}</DialogTitle>
            <DialogDescription>
              Выберите сохранённый набор редакций. Показаны комплекты, доступные вам целиком.
            </DialogDescription>
          </DialogHeader>
          <SearchField
            label="Найти комплект"
            value={q}
            onChange={(v) => {
              setQ(v)
              setOffset(0)
            }}
          />
          <LoadState {...state} onRetry={() => setRetry((v) => v + 1)} />
          {state.data && (
            <>
              <div className="analysis-pick-list">
                {state.data.items.map((c) => (
                  <Button
                    type="button"
                    variant="outline"
                    key={c.id}
                    onClick={() => {
                      onChange(c)
                      setOpen(false)
                    }}
                  >
                    <span>
                      {c.title}
                      <small>
                        {dateLabel(c.as_of)} · {c.documents.length} редакций
                      </small>
                    </span>
                  </Button>
                ))}
              </div>
              <Pagination {...state.data} onChange={setOffset} />
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
function CreateCorpus({
  data,
  close,
  saved,
}: {
  data: Snapshot
  close: () => void
  saved: () => void
}) {
  const [title, setTitle] = useState(''),
    [asOf, setAsOf] = useState(localToday()),
    [complete, setComplete] = useState(false)
  const [q, setQ] = useState(''),
    [department, setDepartment] = useState(''),
    [offset, setOffset] = useState(0),
    [retry, setRetry] = useState(0)
  const [selected, setSelected] = useState<Record<string, string>>({}),
    [error, setError] = useState(''),
    [pending, setPending] = useState(false)
  const key = useKey()
  const options = useRemote('corpus-options', (signal) =>
    result(client.GET('/api/v1/documents/options', { signal })),
  )
  const docs = useRemote(`corpus-docs:${q}:${department}:${offset}:${retry}`, (signal) =>
    result(
      client.GET('/api/v1/documents', {
        params: { query: { q, department_id: department || undefined, offset, limit: 10 } },
        signal,
      }),
    ),
  )
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setPending(true)
    setError('')
    const body = {
      title,
      as_of: asOf,
      version_ids: Object.keys(selected).sort(),
      declared_complete: complete,
    }
    try {
      await result(client.POST('/api/v1/corpora', { body, params: { header: key(body) } }))
      saved()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !pending) close()
      }}
    >
      <DialogContent className="analysis-dialog wide">
        <DialogHeader>
          <DialogTitle>Новый комплект</DialogTitle>
          <DialogDescription>
            Зафиксируйте точные редакции для одной стороны сравнения. Последующие загрузки не
            изменят этот комплект.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit}>
          <fieldset disabled={pending}>
            <div className="field-row">
              <label>
                Название комплекта
                <Input
                  required
                  minLength={3}
                  maxLength={180}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <label>
                Дата комплекта
                <Input
                  type="date"
                  required
                  value={asOf}
                  onChange={(e) => setAsOf(e.target.value)}
                />
              </label>
            </div>
            <div className="filter-bar">
              <SearchField
                label="Выбрать редакции документов"
                value={q}
                onChange={(v) => {
                  setQ(v)
                  setOffset(0)
                }}
              />
              <select
                aria-label="Отдел для комплекта"
                value={department}
                onChange={(e) => {
                  setDepartment(e.target.value)
                  setOffset(0)
                }}
              >
                <option value="">Все подразделения</option>
                {data.departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
            <LoadState {...docs} onRetry={() => setRetry((v) => v + 1)} />
            {docs.data && (
              <>
                <div className="analysis-pick-list">
                  {docs.data.items.map((d) => (
                    <label className="document-choice" key={d.id}>
                      <input
                        type="checkbox"
                        checked={d.id in selected}
                        disabled={
                          !options.data?.writable_departments.includes(d.department_id) ||
                          ['queued', 'processing'].includes(d.processing_status) ||
                          (Object.keys(selected).length >= 20 && !(d.id in selected))
                        }
                        onChange={(e) =>
                          setSelected((old) => {
                            const next = { ...old }
                            if (e.target.checked) next[d.id] = `${d.title} · ${d.revision}`
                            else delete next[d.id]
                            return next
                          })
                        }
                      />
                      <span>
                        {d.title}
                        <small>
                          Редакция {d.revision} · {dateLabel(d.effective_from)}
                        </small>
                      </span>
                    </label>
                  ))}
                </div>
                <Pagination {...docs.data} onChange={setOffset} />
              </>
            )}
            <p className="small">Выбрано: {Object.keys(selected).length} из 20</p>
            {!!Object.keys(selected).length && (
              <details>
                <summary>Выбранные редакции</summary>
                <ul>
                  {Object.entries(selected).map(([id, name]) => (
                    <li key={id}>
                      {name}{' '}
                      <button
                        type="button"
                        className="text-link"
                        onClick={() =>
                          setSelected((old) => {
                            const next = { ...old }
                            delete next[id]
                            return next
                          })
                        }
                      >
                        Убрать
                      </button>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <label className="document-choice">
              <input
                type="checkbox"
                checked={complete}
                onChange={(e) => setComplete(e.target.checked)}
              />
              <span>
                Заявляю полноту выбранного комплекта для этой проверки
                <small>
                  Не заменяет проверку применимости, утверждения документов и реестра функций.
                </small>
              </span>
            </label>
            {error && (
              <p role="alert" className="error-box">
                {error}
              </p>
            )}
            <div className="dialog-actions">
              <Button type="button" variant="outline" onClick={close}>
                Закрыть
              </Button>
              <Button type="submit" disabled={!Object.keys(selected).length}>
                {pending ? 'Сохраняем…' : 'Зафиксировать комплект'}
              </Button>
            </div>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  )
}
function CreateComparison({ close, saved }: { close: () => void; saved: (id: string) => void }) {
  const [title, setTitle] = useState(''),
    [before, setBefore] = useState<Corpus | null>(null),
    [after, setAfter] = useState<Corpus | null>(null)
  const [criteria, setCriteria] = useState(
      'Сопоставить документированные функции и границы ответственности в выбранных редакциях.',
    ),
    [pending, setPending] = useState(false),
    [error, setError] = useState('')
  const key = useKey()
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!before || !after) return
    const body = { title, before_id: before.id, after_id: after.id, criteria }
    setPending(true)
    setError('')
    try {
      const run = await result(
        client.POST('/api/v1/comparisons', { body, params: { header: key(body) } }),
      )
      saved(run.id)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !pending) close()
      }}
    >
      <DialogContent className="analysis-dialog">
        <DialogHeader>
          <DialogTitle>Новое сравнение</DialogTitle>
          <DialogDescription>
            Результат сохранится отдельным запуском. Совпадения по смыслу потребуют проверки
            аудитором.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit}>
          <fieldset disabled={pending}>
            <label>
              Название сравнения
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
                minLength={3}
                maxLength={180}
              />
            </label>
            <div className="comparison-columns">
              <CorpusPicker label="Комплект до" value={before} onChange={setBefore} />
              <CorpusPicker label="Комплект после" value={after} onChange={setAfter} />
            </div>
            <label>
              Критерии проверки
              <textarea
                required
                minLength={10}
                maxLength={2000}
                value={criteria}
                onChange={(e) => setCriteria(e.target.value)}
              />
            </label>
            <p className="muted small">
              Локальный алгоритм сопоставляет текст. Критерии сохраняются для предметной проверки;
              Gemini не вызывается.
            </p>
            {error && (
              <p className="error-box" role="alert">
                {error}
              </p>
            )}
            <div className="dialog-actions">
              <Button type="button" variant="outline" onClick={close}>
                Закрыть
              </Button>
              <Button disabled={!before || !after || before.id === after.id} type="submit">
                {pending ? 'Сохраняем…' : 'Начать сравнение'}
              </Button>
            </div>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  )
}
function EvidenceColumn({
  label,
  items,
  onSource,
}: {
  label: string
  items: Evidence[]
  onSource: Props['onSource']
}) {
  return (
    <div className="evidence-column">
      <h4>{label}</h4>
      {!items.length && (
        <p className="muted small">Подтверждающий фрагмент этой стороны не установлен.</p>
      )}
      {items.map((e) => (
        <div key={e.anchor_id}>
          <blockquote>{e.quote}</blockquote>
          <Button variant="link" className="evidence-link" onClick={() => onSource(e.anchor_id)}>
            {e.title} · {e.revision} · {e.locator}
          </Button>
        </div>
      ))}
    </div>
  )
}
function ReviewDialog({
  run,
  finding,
  close,
  saved,
}: {
  run: Run
  finding: Finding
  close: () => void
  saved: () => void
}) {
  const [decision, setDecision] = useState<S['ReviewInput']['decision']>('needs_evidence'),
    [category, setCategory] = useState<Category>(
      (finding.review?.category as Category) ?? finding.category,
    ),
    [note, setNote] = useState('')
  const [pending, setPending] = useState(false),
    [error, setError] = useState(''),
    [offset, setOffset] = useState(0),
    [retry, setRetry] = useState(0)
  const key = useKey(),
    path = { run_id: run.id, finding_id: finding.id }
  const history = useRemote(`review:${finding.id}:${offset}:${retry}`, (signal) =>
    result(
      client.GET('/api/v1/comparisons/{run_id}/findings/{finding_id}/reviews', {
        params: { path, query: { offset, limit: 5 } },
        signal,
      }),
    ),
  )
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setPending(true)
    setError('')
    const body = { expected_version: finding.review?.version ?? 0, decision, category, note }
    try {
      await result(
        client.POST('/api/v1/comparisons/{run_id}/findings/{finding_id}/reviews', {
          params: { path, header: key({ path, body }) },
          body,
        }),
      )
      saved()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !pending) close()
      }}
    >
      <DialogContent className="analysis-dialog">
        <DialogHeader>
          <DialogTitle>Рассмотрение кандидата</DialogTitle>
          <DialogDescription>
            {finding.title}. Решение сохраняется с автором и датой; реестр функций автоматически не
            изменяется.
          </DialogDescription>
        </DialogHeader>
        {run.can_review && (
          <form onSubmit={submit}>
            <fieldset disabled={pending}>
              <label>
                Решение
                <select
                  value={decision}
                  onChange={(e) => setDecision(e.target.value as typeof decision)}
                >
                  {Object.entries(decisions).map(([v, title]) => (
                    <option key={v} value={v}>
                      {title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Категория решения
                <select value={category} onChange={(e) => setCategory(e.target.value as Category)}>
                  {Object.entries(categories).map(([v, title]) => (
                    <option key={v} value={v}>
                      {title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Основание решения
                <textarea
                  required
                  minLength={10}
                  maxLength={2000}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              {error && (
                <div className="error-box" role="alert">
                  {error}
                  <Button type="button" variant="outline" onClick={saved}>
                    Загрузить актуальный результат
                  </Button>
                </div>
              )}
              <div className="dialog-actions">
                <Button type="submit">{pending ? 'Сохраняем…' : 'Сохранить рассмотрение'}</Button>
              </div>
            </fieldset>
          </form>
        )}
        <h3>История рассмотрения</h3>
        <LoadState {...history} onRetry={() => setRetry((v) => v + 1)} />
        {history.data && (
          <>
            <div className="review-history">
              {history.data.items.map((r) => (
                <article key={r.id}>
                  <strong>{decisions[r.decision]}</strong>
                  <p>
                    {categories[r.category as Category]} · {r.author} · {dateLabel(r.created_at)}
                  </p>
                  <p>{r.note}</p>
                </article>
              ))}
            </div>
            <Pagination {...history.data} onChange={setOffset} />
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
function ComparisonDetail({
  id,
  back,
  onSource,
}: {
  id: string
  back: () => void
  onSource: Props['onSource']
}) {
  const [revision, setRevision] = useState(0),
    [offset, setOffset] = useState(0),
    [category, setCategory] = useState<Category | ''>(''),
    [reviewed, setReviewed] = useState(''),
    [review, setReview] = useState<Finding | null>(null)
  const refresh = () => setRevision((v) => v + 1)
  const state = useRemote(`run:${id}:${revision}`, (signal) =>
    result(
      client.GET('/api/v1/comparisons/{run_id}', { params: { path: { run_id: id } }, signal }),
    ),
  )
  const findings = useRemote(
    `findings:${id}:${offset}:${category}:${reviewed}:${revision}`,
    (signal) =>
      result(
        client.GET('/api/v1/comparisons/{run_id}/findings', {
          params: {
            path: { run_id: id },
            query: {
              offset,
              limit: 5,
              category: category || undefined,
              reviewed: reviewed ? reviewed === 'yes' : undefined,
            },
          },
          signal,
        }),
      ),
  )
  const running = busy(state.data?.job)
  useEffect(() => {
    if (running) {
      const timer = setTimeout(refresh, 2500)
      return () => clearTimeout(timer)
    }
  }, [running, revision])
  const run = state.data
  return (
    <section className="content-panel analysis-panel">
      <Button variant="ghost" onClick={back}>
        <ArrowLeft size={16} />
        Все сравнения
      </Button>
      <LoadState {...state} onRetry={refresh} />
      {run && (
        <>
          <div className="section-heading">
            <div>
              <h2>{run.title}</h2>
              <p className="muted small">Сохранённый запуск · {dateLabel(run.created_at)}</p>
            </div>
          </div>
          <div className="comparison-columns">
            <CorpusSummary value={run.before} />
            <CorpusSummary value={run.after} />
          </div>
          <div className="analysis-criteria">
            <strong>Критерии проверки</strong>
            <p>{run.criteria}</p>
          </div>
          <details className="analysis-notes">
            <summary>Сверка с реестром функций</summary>
            <p>
              Действующих функций в области проверки: до — {run.registry_check.before_count}, после
              — {run.registry_check.after_count}. Полнота реестра{' '}
              {run.registry_check.declared_complete ? 'заявлена' : 'не подтверждена'}.
            </p>
            <p>{run.registry_check.limitation}</p>
            <ul>
              {run.registry_check.linked_functions.map((f) => (
                <li key={f.id}>
                  <Button variant="link" onClick={() => onSource(f.source_anchor_id)}>
                    {f.title}
                  </Button>{' '}
                  · до: {f.active_before ? 'действует' : 'не действует'}, после:{' '}
                  {f.active_after ? 'действует' : 'не действует'}
                </li>
              ))}
            </ul>
          </details>
          <JobState job={run.job} refresh={refresh} />
          <p className="muted small">
            Автоматические карточки — кандидаты. Причина изменения и последствия требуют отдельных
            оснований. Сравнение не заменяет сверку реестра функций.
          </p>
          <div className="filter-bar">
            <select
              aria-label="Категория сравнения"
              value={category}
              onChange={(e) => {
                setCategory(e.target.value as typeof category)
                setOffset(0)
              }}
            >
              <option value="">Все категории</option>
              {Object.entries(categories).map(([v, label]) => (
                <option value={v} key={v}>
                  {label}
                </option>
              ))}
            </select>
            <select
              aria-label="Статус рассмотрения"
              value={reviewed}
              onChange={(e) => {
                setReviewed(e.target.value)
                setOffset(0)
              }}
            >
              <option value="">Все кандидаты</option>
              <option value="no">Без рассмотрения</option>
              <option value="yes">Есть рассмотрение</option>
            </select>
          </div>
          <LoadState {...findings} onRetry={refresh} />
          {findings.data && (
            <>
              <div className="finding-list">
                {findings.data.items.map((f) => (
                  <article key={f.id} className="finding-card">
                    <div className="section-heading">
                      <div>
                        <span className="eyebrow">Кандидат {f.ordinal + 1}</span>
                        <h3>{categories[f.category]}</h3>
                      </div>
                      <span className="muted small">
                        {f.provenance === 'deterministic'
                          ? 'Сопоставление текста'
                          : 'Локальное сопоставление'}
                      </span>
                    </div>
                    <p>{f.explanation}</p>
                    <div className="comparison-columns">
                      <EvidenceColumn label="До" items={f.before} onSource={onSource} />
                      <EvidenceColumn label="После" items={f.after} onSource={onSource} />
                    </div>
                    {!!f.diff.length && (
                      <details className="word-diff">
                        <summary>
                          Изменения текста: зачёркнуто — удалено, подчёркнуто — добавлено
                        </summary>
                        <p>
                          {f.diff.map((d, i) =>
                            d.kind === 'delete' ? (
                              <del key={i}>{d.text}</del>
                            ) : d.kind === 'insert' ? (
                              <ins key={i}>{d.text}</ins>
                            ) : (
                              <span key={i}>{d.text}</span>
                            ),
                          )}
                        </p>
                      </details>
                    )}
                    <Notes items={f.limitations} />
                    <div className="finding-review">
                      <span>
                        {f.review
                          ? `${decisions[f.review.decision]} · ${f.review.author}`
                          : 'Ожидает предметной проверки'}
                      </span>
                      <Button size="sm" variant="outline" onClick={() => setReview(f)}>
                        {run.can_review ? 'Рассмотреть' : 'История рассмотрения'}
                      </Button>
                    </div>
                  </article>
                ))}
              </div>
              {!findings.data.total && !running && (
                <p className="empty-state">
                  Кандидатов по выбранному фильтру нет. Проверьте состояние обработки и ограничения
                  комплектов.
                </p>
              )}
              <Pagination {...findings.data} onChange={setOffset} />
            </>
          )}
          <details className="analysis-notes">
            <summary>Версии обработки</summary>
            <p className="integrity-hash small">
              {run.algorithm_version}
              <br />
              {run.index_key}
              <br />
              Внешние вызовы: 0. Ключ Gemini не требуется.
            </p>
          </details>
          {review && (
            <ReviewDialog
              run={run}
              finding={review}
              close={() => setReview(null)}
              saved={() => {
                setReview(null)
                refresh()
              }}
            />
          )}
        </>
      )}
    </section>
  )
}
export function AnalysisWorkspace({ data, onSource }: Props) {
  const [tab, setTab] = useState<'comparisons' | 'corpora'>('comparisons'),
    [create, setCreate] = useState(false),
    [selected, setSelected] = useState<string | null>(null),
    [inspect, setInspect] = useState<Corpus | null>(null)
  const [revision, setRevision] = useState(0),
    [offset, setOffset] = useState(0),
    [q, setQ] = useState('')
  const options = useRemote('analysis-options', (signal) =>
    result(client.GET('/api/v1/documents/options', { signal })),
  )
  const corpora = useRemote(`corpora:${tab}:${q}:${offset}:${revision}`, (signal) =>
    tab === 'corpora'
      ? result(
          client.GET('/api/v1/corpora', { params: { query: { q, offset, limit: 10 } }, signal }),
        )
      : Promise.resolve(null),
  )
  const runs = useRemote(`runs:${tab}:${offset}:${revision}`, (signal) =>
    tab === 'comparisons'
      ? result(
          client.GET('/api/v1/comparisons', { params: { query: { offset, limit: 10 } }, signal }),
        )
      : Promise.resolve(null),
  )
  if (selected)
    return (
      <ComparisonDetail
        id={selected}
        back={() => {
          setSelected(null)
          setRevision((v) => v + 1)
        }}
        onSource={onSource}
      />
    )
  return (
    <section className="content-panel analysis-panel">
      <div className="section-heading">
        <div className="view-toggle">
          <Button
            variant={tab === 'comparisons' ? 'default' : 'ghost'}
            onClick={() => {
              setTab('comparisons')
              setOffset(0)
            }}
          >
            <FileDiff size={16} />
            Сравнения
          </Button>
          <Button
            variant={tab === 'corpora' ? 'default' : 'ghost'}
            onClick={() => {
              setTab('corpora')
              setOffset(0)
            }}
          >
            <FolderOpen size={16} />
            Комплекты
          </Button>
        </div>
        {!!options.data?.writable_departments.length && (
          <Button onClick={() => setCreate(true)}>
            <Plus size={16} />
            {tab === 'corpora' ? 'Новый комплект' : 'Новое сравнение'}
          </Button>
        )}
      </div>
      <p className="muted">
        Сначала зафиксируйте комплекты «до» и «после», затем сопоставьте их тексты и рассмотрите
        найденные изменения.
      </p>
      {tab === 'corpora' ? (
        <>
          <div className="filter-bar">
            <SearchField
              label="Поиск комплектов"
              value={q}
              onChange={(v) => {
                setQ(v)
                setOffset(0)
              }}
            />
          </div>
          <LoadState {...corpora} onRetry={() => setRevision((v) => v + 1)} />
          {corpora.data && (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Комплект</TableHead>
                    <TableHead>На дату</TableHead>
                    <TableHead>Редакций</TableHead>
                    <TableHead>Фрагментов</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {corpora.data.items.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell>
                        <Button variant="link" onClick={() => setInspect(c)}>
                          {c.title}
                        </Button>
                      </TableCell>
                      <TableCell>{dateLabel(c.as_of)}</TableCell>
                      <TableCell>{c.documents.length}</TableCell>
                      <TableCell>{c.fragment_count}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {!corpora.data.total && (
                <p className="empty-state">
                  Комплектов пока нет. Создайте первый из доступных редакций документов.
                </p>
              )}
              <Pagination {...corpora.data} onChange={setOffset} />
            </>
          )}
        </>
      ) : (
        <>
          <LoadState {...runs} onRetry={() => setRevision((v) => v + 1)} />
          {runs.data && (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Сравнение</TableHead>
                    <TableHead>До → после</TableHead>
                    <TableHead>Состояние</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.data.items.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell>
                        <Button variant="link" onClick={() => setSelected(r.id)}>
                          {r.title}
                        </Button>
                      </TableCell>
                      <TableCell>
                        {r.before.title} → {r.after.title}
                      </TableCell>
                      <TableCell>
                        {busy(r.job)
                          ? 'Обрабатывается'
                          : r.job.status === 'ready'
                            ? 'Готово к рассмотрению'
                            : r.job.message}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {!runs.data.total && (
                <p className="empty-state">
                  Сравнений пока нет. Результаты каждого запуска сохраняются отдельно.
                </p>
              )}
              <Pagination {...runs.data} onChange={setOffset} />
            </>
          )}
        </>
      )}
      {create &&
        (tab === 'corpora' ? (
          <CreateCorpus
            data={data}
            close={() => setCreate(false)}
            saved={() => {
              setCreate(false)
              setOffset(0)
              setRevision((v) => v + 1)
            }}
          />
        ) : (
          <CreateComparison
            close={() => setCreate(false)}
            saved={(id) => {
              setCreate(false)
              setSelected(id)
            }}
          />
        ))}
      <Dialog
        open={!!inspect}
        onOpenChange={(v) => {
          if (!v) setInspect(null)
        }}
      >
        <DialogContent className="analysis-dialog">
          <DialogHeader>
            <DialogTitle>Сохранённый комплект</DialogTitle>
            <DialogDescription>Точные редакции и ограничения на момент создания.</DialogDescription>
          </DialogHeader>
          {inspect && <CorpusSummary value={inspect} />}
        </DialogContent>
      </Dialog>
    </section>
  )
}
type SearchQuery = NonNullable<paths['/api/v1/search']['get']['parameters']['query']>
export function HybridSearch({ data, onSource }: Props) {
  const [q, setQ] = useState(''),
    [mode, setMode] = useState<NonNullable<SearchQuery['mode']>>('hybrid'),
    [dept, setDept] = useState(''),
    [asOf, setAsOf] = useState(''),
    [version, setVersion] = useState(''),
    [corpus, setCorpus] = useState<Corpus | null>(null)
  const [query, setQuery] = useState<SearchQuery | null>(null),
    [offset, setOffset] = useState(0),
    [revision, setRevision] = useState(0),
    [pending, setPending] = useState(false),
    [error, setError] = useState('')
  const key = useKey(),
    refresh = () => setRevision((v) => v + 1)
  const status = useRemote(`search-status:${revision}`, (signal) =>
    result(client.GET('/api/v1/search/status', { signal })),
  )
  const results = useRemote(`search:${JSON.stringify(query)}:${offset}:${revision}`, (signal) =>
    query
      ? result(
          client.GET('/api/v1/search', {
            params: { query: { ...query, offset, limit: 10 } },
            signal,
          }),
        )
      : Promise.resolve(null),
  )
  const running = status.data?.active_jobs.some(busy)
  useEffect(() => {
    if (running) {
      const timer = setTimeout(refresh, 3000)
      return () => clearTimeout(timer)
    }
  }, [running, revision])
  async function index() {
    if (!status.data?.pending_version_ids.length) return
    const body = { version_ids: status.data.pending_version_ids }
    setPending(true)
    setError('')
    try {
      await result(client.POST('/api/v1/search/index', { body, params: { header: key(body) } }))
      refresh()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }
  const draft = {
    q,
    mode,
    department_id: dept || undefined,
    corpus_id: corpus?.id,
    version_id: version || undefined,
    as_of: asOf || undefined,
  }
  const changed = !!query && JSON.stringify(query) !== JSON.stringify(draft)
  return (
    <section className="content-panel analysis-panel">
      <div className="section-heading">
        <div>
          <h2>Поиск по содержанию</h2>
          <p className="muted small">
            Совпадения по словам и смыслу, с переходом к исходной цитате.
          </p>
        </div>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setOffset(0)
          setQuery(draft)
        }}
      >
        <div className="filter-bar search-primary">
          <Input
            aria-label="Запрос по содержанию"
            placeholder="Например, кто учитывает поступающие документы"
            required
            minLength={2}
            maxLength={200}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <Button type="submit">
            <Search size={16} />
            Найти
          </Button>
        </div>
        <div className="filter-bar">
          <select
            aria-label="Режим поиска"
            value={mode}
            onChange={(e) => setMode(e.target.value as typeof mode)}
          >
            <option value="hybrid">Слова и смысл</option>
            <option value="lexical">Только слова</option>
            <option value="semantic">Только смысл</option>
          </select>
          <select aria-label="Отдел поиска" value={dept} onChange={(e) => setDept(e.target.value)}>
            <option value="">Все доступные подразделения</option>
            {data.departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </div>
        <details className="search-scope">
          <summary>Комплект, редакция и дата</summary>
          <CorpusPicker
            label="Комплект поиска"
            value={corpus}
            onChange={(c) => {
              setCorpus(c)
              setVersion('')
            }}
          />
          {corpus && (
            <label>
              Редакция в комплекте
              <select value={version} onChange={(e) => setVersion(e.target.value)}>
                <option value="">Все редакции комплекта</option>
                {corpus.documents.map((d) => (
                  <option value={d.id} key={d.id}>
                    {d.title} · {d.revision}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            Начало действия не позднее
            <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          </label>
          <p className="muted small">
            При заданной дате редакции с неизвестным началом действия исключаются. Поиск не выбирает
            «актуальную» редакцию автоматически.
          </p>
        </details>
      </form>
      {changed && (
        <p role="status" className="muted">
          Условия изменены. Нажмите «Найти», чтобы обновить результаты.
        </p>
      )}
      <LoadState {...status} onRetry={refresh} />
      {status.data && (
        <div className="search-index">
          <p className="small">
            Поиск по смыслу подготовлен для {status.data.indexed_fragments} из{' '}
            {status.data.available_fragments} доступных фрагментов.
          </p>
          {!!status.data.pending_version_ids.length && (
            <Button variant="outline" size="sm" disabled={pending || running} onClick={index}>
              {pending ? 'Сохраняем задание…' : 'Подготовить поиск'}
            </Button>
          )}
          {status.data.active_jobs
            .filter((j) => busy(j) || j.can_retry)
            .map((j) => (
              <JobState key={j.id} job={j} refresh={refresh} />
            ))}
          {error && (
            <p role="alert" className="error-box">
              {error}
            </p>
          )}
        </div>
      )}
      {query && <LoadState {...results} onRetry={refresh} />}
      {results.data && (
        <>
          <p className="small muted">
            Режим выдачи:{' '}
            {results.data.mode_used === 'hybrid'
              ? 'слова и смысл'
              : results.data.mode_used === 'semantic'
                ? 'только смысл'
                : 'только слова'}{' '}
            · кандидатов: {results.data.total}
          </p>
          <Notes items={results.data.warnings} />
          <div className="search-results">
            {results.data.items.map((h, i) => (
              <article className="search-hit" key={h.anchor_id}>
                <div className="section-heading">
                  <h3>
                    {offset + i + 1}. {h.title} <span className="muted small">· {h.revision}</span>
                  </h3>
                  <span className="small muted">
                    {data.departments.find((d) => d.id === h.department_id)?.name}
                  </span>
                </div>
                <blockquote>{h.semantic_excerpt ?? h.quote}</blockquote>
                <div className="finding-review">
                  <span className="muted small">
                    {h.lexical_rank ? `Слова: место ${h.lexical_rank}` : ''}
                    {h.lexical_rank && h.semantic_rank ? ' · ' : ''}
                    {h.semantic_rank ? `Смысл: место ${h.semantic_rank}` : ''}
                  </span>
                  <Button variant="outline" size="sm" onClick={() => onSource(h.anchor_id)}>
                    Открыть источник
                  </Button>
                </div>
              </article>
            ))}
          </div>
          {!results.data.total && (
            <p className="empty-state">
              В выбранной области кандидаты не найдены. Это не доказывает отсутствие функции.
            </p>
          )}
          <Pagination {...results.data} onChange={setOffset} />
        </>
      )}
    </section>
  )
}
