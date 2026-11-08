import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Download, FileText, Plus, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table'
import { client, result, errorMessage, ApiError } from '@/lib/client'
import { useRemote } from '@/lib/useRemote'
import { Reauthenticate } from '@/components/Reauthenticate'
import type { components } from '@/lib/schema.generated'
import '@/reports.css'

type S = components['schemas']
type Detail = S['ReportDetail']
type Content = S['ReportContent']
const states: Record<string, string> = {
  draft: 'Черновик',
  in_review: 'На проверке',
  returned: 'На доработке',
  reviewed: 'Проверен рецензентом',
  released: 'Выпущен',
}
const fields: [keyof Content, string][] = [
  ['objective', 'Цель проверки'],
  ['scope', 'Область проверки'],
  ['period', 'Период проверки'],
  ['criteria', 'Критерии'],
  ['method', 'Метод проверки'],
  ['limitations', 'Ограничения'],
  ['findings', 'Замечания и доказательства'],
  ['significance', 'Значимость замечаний'],
  ['recommendations', 'Рекомендации'],
  ['action_plan', 'Мероприятия ответственные и сроки'],
  ['conclusion', 'Профессиональный вывод'],
]
const eventNames: Record<string, string> = {
  created: 'Подготовлен черновик',
  saved: 'Сохранена редакция',
  submitted: 'Направлено рецензенту',
  returned: 'Возвращено на доработку',
  reviewed: 'Проверено рецензентом',
  released: 'Выпущен отчёт',
  exported: 'Выдан файл',
}
function Feedback({ error }: { error: string }) {
  return error ? (
    <p role="alert" className="error-box">
      {error}
    </p>
  ) : null
}
function useCommand() {
  const [pending, setPending] = useState(false),
    [error, setError] = useState('')
  const [reauth, setReauth] = useState(false)
  const request = useRef({ payload: '', key: '' })
  const headers = (body: unknown, action: string) => {
    const payload = JSON.stringify([action, body])
    if (request.current.payload !== payload) request.current = { payload, key: crypto.randomUUID() }
    return { 'idempotency-key': request.current.key }
  }
  const run = async (task: () => Promise<unknown>, done: () => void) => {
    if (pending) return
    setPending(true)
    setError('')
    try {
      await task()
      setReauth(false)
      done()
    } catch (e) {
      setError(errorMessage(e))
      setReauth(
        e instanceof ApiError &&
          e.status === 403 &&
          e.message.includes('повторное подтверждение входа'),
      )
    } finally {
      setPending(false)
    }
  }
  return {
    pending,
    error,
    headers,
    run,
    reauth,
    clearReauth: () => {
      setReauth(false)
      setError('Вход подтверждён. Повторите действие.')
    },
  }
}

function CreateReport({
  options,
  previous,
  close,
  created,
}: {
  options: S['ReportOptions']
  previous?: Detail
  close: () => void
  created: (id: string) => void
}) {
  const [proposal, setProposal] = useState(previous?.proposal_id ?? options.proposals[0]?.id ?? '')
  const [department, setDepartment] = useState(
      previous?.kind === 'extract' ? previous.department_ids[0] : '',
    ),
    [reviewer, setReviewer] = useState(options.reviewers[0]?.id ?? ''),
    [recipients, setRecipients] = useState<string[]>([])
  const command = useCommand()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const body = {
      proposal_id: proposal,
      department_id: department || null,
      reviewer_id: reviewer,
      recipients,
      previous_id: previous?.id ?? null,
    }
    void command.run(
      async () => {
        const value = await result(
          client.POST('/api/v1/reports', {
            body,
            params: { header: command.headers(body, 'create') },
          }),
        )
        created(value.id)
      },
      () => {},
    )
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !command.pending) close()
      }}
    >
      <DialogContent className="report-dialog">
        <DialogHeader>
          <DialogTitle>{previous ? 'Исправленный выпуск' : 'Подготовить отчёт'}</DialogTitle>
          <DialogDescription>
            Сохраняется снимок версии предложения, доступных источников и ответов. Личные заметки
            исключаются. Черновик требует проверки человеком.
          </DialogDescription>
        </DialogHeader>
        <form className="report-form" onSubmit={submit}>
          <label>
            Предложение
            <select
              aria-label="Предложение"
              value={proposal}
              disabled={!!previous}
              onChange={(e) => setProposal(e.target.value)}
            >
              {options.proposals.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Область отчёта
            <select
              aria-label="Область отчёта"
              value={department}
              disabled={!!previous}
              onChange={(e) => setDepartment(e.target.value)}
            >
              <option value="">Итоговое заключение по всем разделам</option>
              {options.departments.map((d) => (
                <option key={d.id} value={d.id}>
                  Выписка: {d.label}
                </option>
              ))}
            </select>
          </label>
          <p className="small muted">
            Для выписки выберите отдел, включённый в предложение. Исправление должно сохранить
            область предыдущего выпуска.
          </p>
          <label>
            Рецензент аудита
            <select
              aria-label="Рецензент аудита"
              required
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
            >
              <option value="">Выберите рецензента</option>
              {options.reviewers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          {!options.reviewers.length && (
            <p role="status">
              Нет отдельного рецензента аудита. Для проверки и выпуска нужна учётная запись reviewer
              с доступом к области отчёта.
            </p>
          )}
          <fieldset>
            <legend>Разрешённые адресаты выпуска</legend>
            <div className="report-choices">
              {options.recipients.map((p) => (
                <label key={p.id}>
                  <input
                    type="checkbox"
                    checked={recipients.includes(p.id)}
                    onChange={(e) =>
                      setRecipients(
                        e.target.checked
                          ? [...recipients, p.id]
                          : recipients.filter((id) => id !== p.id),
                      )
                    }
                  />
                  {p.label}
                </label>
              ))}
            </div>
          </fieldset>
          <p className="small muted">
            Каждый адресат должен иметь доступ ко всем разделам этого отчёта. Выписка проходит
            отдельную проверку на сведения других отделов.
          </p>
          <Feedback error={command.error} />
          <Button disabled={command.pending || !proposal || !reviewer || !recipients.length}>
            Создать черновик отчёта
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function Evidence({
  snapshot,
  onSource,
}: {
  snapshot: S['ReportSnapshot']
  onSource: (id: string) => void
}) {
  return (
    <div className="report-evidence">
      {snapshot.blocks.map((block, i) => (
        <section key={i}>
          {block.heading && <h3>{block.heading}</h3>}
          {block.text && <p>{block.text}</p>}
          {block.source_id && (
            <Button variant="outline" size="sm" onClick={() => onSource(block.source_id!)}>
              Открыть сохранённый источник
            </Button>
          )}
        </section>
      ))}
    </div>
  )
}

function RevisionHistory({ report }: { report: Detail }) {
  const [number, setNumber] = useState(1),
    [revision, setRevision] = useState<S['ReportRevisionView'] | null>(null),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(false)
  const load = async () => {
    setLoading(true)
    setError('')
    setRevision(null)
    try {
      setRevision(
        await result(
          client.GET('/api/v1/reports/{report_id}/revisions/{number}', {
            params: { path: { report_id: report.id, number } },
          }),
        ),
      )
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }
  return (
    <details className="report-section">
      <summary>Сохранённые редакции</summary>
      <div className="report-actions">
        <label>
          Номер редакции
          <Input
            type="number"
            min={1}
            max={report.revision}
            value={number}
            onChange={(e) => setNumber(Number(e.target.value))}
          />
        </label>
        <Button
          variant="outline"
          disabled={loading || number < 1 || number > report.revision}
          onClick={() => void load()}
        >
          Открыть редакцию
        </Button>
      </div>
      <Feedback error={error} />
      {revision && (
        <div className="report-evidence">
          <h3>
            {revision.content.title} · редакция {revision.number}
          </h3>
          <p className="small muted">
            Только чтение. Сохранено {new Date(revision.created_at).toLocaleString('ru-RU')}
          </p>
          {fields.map(([key, label]) => (
            <section key={key}>
              <h3>{label}</h3>
              <p>{revision.content[key] || 'Не заполнено'}</p>
            </section>
          ))}
        </div>
      )}
    </details>
  )
}

function ReportDetail({
  id,
  back,
  correct,
  onSource,
}: {
  id: string
  back: () => void
  correct: (report: Detail) => void
  onSource: (id: string) => void
}) {
  const [refresh, setRefresh] = useState(0),
    [offset, setOffset] = useState(0)
  const [content, setContent] = useState<Content | null>(null),
    [dirty, setDirty] = useState(false),
    [note, setNote] = useState(''),
    [outcome, setOutcome] = useState<'accept' | 'return'>('accept'),
    [checked, setChecked] = useState(false)
  const [execution, setExecution] = useState<S['ReportSnapshot'] | null>(null),
    [extraError, setExtraError] = useState('')
  const remote = useRemote(`report:${id}:${refresh}:${offset}`, async (signal) => {
    try {
      return await result(
        client.GET('/api/v1/reports/{report_id}', {
          params: { path: { report_id: id }, query: { offset, limit: 25 } },
          signal,
        }),
      )
    } catch (error) {
      if (!signal.aborted && error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        setContent(null)
        setNote('')
        setExecution(null)
        setDirty(false)
      }
      throw error
    }
  })
  const command = useCommand()
  useEffect(() => {
    if (remote.data && !dirty) setContent(remote.data.content)
  }, [remote.data, dirty])
  const update = () => {
    setDirty(false)
    setNote('')
    setChecked(false)
    setRefresh((v) => v + 1)
  }
  const data = remote.data
  if (!data)
    return (
      <section className="content-panel">
        <Button variant="ghost" onClick={back}>
          <ArrowLeft size={16} />
          Все отчёты
        </Button>
        {remote.loading ? (
          <p role="status">Загружаем отчёт…</p>
        ) : (
          <>
            <Feedback error={remote.error} />
            <Button onClick={() => setRefresh((v) => v + 1)}>Повторить</Button>
          </>
        )}
      </section>
    )
  const action = (name: 'submit' | 'review' | 'release') => {
    const base = { expected_version: data.version, note }
    return command.run(async () => {
      if (name === 'review') {
        const body = { ...base, outcome, checked }
        return result(
          client.POST('/api/v1/reports/{report_id}/review', {
            body,
            params: { path: { report_id: id }, header: command.headers(body, name) },
          }),
        )
      }
      if (name === 'submit')
        return result(
          client.POST('/api/v1/reports/{report_id}/submit', {
            body: base,
            params: { path: { report_id: id }, header: command.headers(base, name) },
          }),
        )
      return result(
        client.POST('/api/v1/reports/{report_id}/release', {
          body: base,
          params: { path: { report_id: id }, header: command.headers(base, name) },
        }),
      )
    }, update)
  }
  const save = (e: FormEvent) => {
    e.preventDefault()
    if (!content) return
    const body = { expected_version: data.version, content, note: '' }
    void command.run(
      () =>
        result(
          client.POST('/api/v1/reports/{report_id}/save', {
            body,
            params: { path: { report_id: id }, header: command.headers(body, 'save') },
          }),
        ),
      update,
    )
  }
  const download = async (format: 'pdf' | 'docx') => {
    setExtraError('')
    try {
      const blob = await result(
        client.GET('/api/v1/reports/{report_id}/export/{format}', {
          params: { path: { report_id: id, format } },
          parseAs: 'blob',
        }),
      )
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `OrgProekt-${data.release?.id}.${format}`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      setExtraError(errorMessage(e))
    }
  }
  return (
    <section className="content-panel report-detail">
      <div className="report-actions">
        <Button variant="ghost" onClick={back}>
          <ArrowLeft size={16} />
          Все отчёты
        </Button>
        <Button variant="outline" onClick={() => setRefresh((v) => v + 1)}>
          <RefreshCw size={15} />
          Обновить сведения
        </Button>
      </div>
      <h2>{data.title}</h2>
      <p className="report-status">
        {states[data.state]} · Редакция {data.revision}
      </p>
      <p className="small muted">
        Автор: {data.owner} · Рецензент: {data.reviewer}
      </p>
      <p className="small muted">Адресаты выпуска: {data.recipients.join(', ')}</p>
      {data.previous_id && (
        <p className="small">Связано с предыдущим отчётом: {data.previous_id}</p>
      )}
      <p className="small muted">
        Снимок предложения v{data.snapshot.proposal_version} от{' '}
        {new Date(data.snapshot.captured_at).toLocaleString('ru-RU')}. Подготовлен без ИИ.
      </p>
      <Feedback error={command.error} />
      <Feedback error={extraError} />
      {command.reauth && <Reauthenticate onDone={command.clearReauth} />}
      {data.can_edit && content ? (
        <form className="report-form" onSubmit={save}>
          <label>
            Название отчёта
            <Input
              required
              minLength={3}
              maxLength={180}
              value={content.title}
              onChange={(e) => {
                setDirty(true)
                setContent({ ...content, title: e.target.value })
              }}
            />
          </label>
          <div className="report-fields">
            {fields.map(([key, label]) => (
              <label key={key}>
                {label}
                <textarea
                  aria-label={label}
                  rows={key === 'conclusion' ? 5 : 3}
                  maxLength={
                    key === 'period'
                      ? 300
                      : ['findings', 'recommendations', 'action_plan', 'conclusion'].includes(key)
                        ? 5000
                        : 3000
                  }
                  value={content[key] ?? ''}
                  onChange={(e) => {
                    setDirty(true)
                    setContent({ ...content, [key]: e.target.value })
                  }}
                />
              </label>
            ))}
          </div>
          <Button disabled={command.pending}>Сохранить редакцию</Button>
          {dirty && <span className="small muted">Есть несохранённый текст</span>}
        </form>
      ) : (
        <div className="report-evidence">
          {fields.map(([key, label]) => (
            <section key={key}>
              <h3>{label}</h3>
              <p>{data.content[key] || 'Не заполнено'}</p>
            </section>
          ))}
        </div>
      )}
      <div className="report-process">
        {(data.can_submit || data.can_review || data.can_release) && (
          <label>
            Комментарий к действию
            <textarea
              aria-label="Комментарий к действию"
              value={note}
              maxLength={3000}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
            />
          </label>
        )}
        {data.can_submit && (
          <>
            <p className="small muted">
              Перед отправкой заполните и сохраните все разделы, включая ограничения и
              профессиональный вывод.
            </p>
            <Button disabled={command.pending || dirty} onClick={() => void action('submit')}>
              Направить на проверку
            </Button>
          </>
        )}
        {data.can_review && (
          <>
            <label>
              Решение рецензента
              <select
                aria-label="Решение рецензента"
                value={outcome}
                onChange={(e) => setOutcome(e.target.value as 'accept' | 'return')}
              >
                <option value="accept">Проверено — готово к выпуску</option>
                <option value="return">Вернуть на доработку</option>
              </select>
            </label>
            <label className="report-check">
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => setChecked(e.target.checked)}
              />
              Проверены источники, выводы, ограничения и допустимость содержания для адресатов
            </label>
            <Button
              disabled={
                command.pending || note.trim().length < 3 || (outcome === 'accept' && !checked)
              }
              onClick={() => void action('review')}
            >
              Сохранить проверку
            </Button>
          </>
        )}
        {data.can_release && (
          <>
            <p>
              Выпуск зафиксирует проверенную редакцию и файлы PDF/DOCX. Исправления оформляются
              отдельным связанным выпуском.
            </p>
            <Button
              disabled={command.pending || note.trim().length < 3}
              onClick={() => void action('release')}
            >
              Выпустить отчёт
            </Button>
          </>
        )}
        {data.release && (
          <>
            <p>
              Выпущен {new Date(data.release.issued_at).toLocaleString('ru-RU')}. Файлы сохраняют
              содержание этого выпуска.
            </p>
            <div className="report-actions">
              <Button onClick={() => void download('pdf')}>
                <Download size={16} />
                Скачать PDF
              </Button>
              <Button variant="outline" onClick={() => void download('docx')}>
                <Download size={16} />
                Скачать DOCX
              </Button>
              {data.can_correct && (
                <Button variant="outline" onClick={() => correct(data)}>
                  Подготовить исправленный выпуск
                </Button>
              )}
            </div>
            <details className="small">
              <summary>Контрольные суммы файлов</summary>
              <p className="report-hash">
                PDF: {data.release.pdf_sha256}
                <br />
                DOCX: {data.release.docx_sha256}
              </p>
            </details>
          </>
        )}
      </div>
      <details open className="report-section">
        <summary>Зафиксированные источники и позиции сторон</summary>
        <Evidence snapshot={data.snapshot} onSource={onSource} />
      </details>
      {data.can_history && <RevisionHistory report={data} />}
      <details className="report-section">
        <summary>Текущее исполнение — отдельно от выпуска</summary>
        <p className="small muted">
          Загружается по текущим правам на предложение. Эти сведения не изменяют сохранённый отчёт.
        </p>
        <Button
          variant="outline"
          onClick={async () => {
            setExtraError('')
            setExecution(null)
            try {
              setExecution(
                await result(
                  client.GET('/api/v1/reports/{report_id}/execution', {
                    params: { path: { report_id: id } },
                  }),
                ),
              )
            } catch (e) {
              setExtraError(errorMessage(e))
            }
          }}
        >
          Загрузить текущий реестр исполнения
        </Button>
        {execution && (
          <>
            <p>
              Версия предложения: {execution.proposal_version}. Проверено{' '}
              {new Date(execution.captured_at).toLocaleString('ru-RU')}
            </p>
            {!execution.blocks.length ? (
              <p>Записей исполнения нет.</p>
            ) : (
              <Evidence snapshot={execution} onSource={onSource} />
            )}
          </>
        )}
      </details>
      <details className="report-section">
        <summary>История отчёта ({data.events_total})</summary>
        {data.events.map((e, i) => (
          <div className="report-history" key={i}>
            <strong>{eventNames[e.kind] ?? e.kind}</strong>
            <p>
              {e.actor} · редакция {e.revision} · {new Date(e.created_at).toLocaleString('ru-RU')}
            </p>
            <p>{e.note}</p>
          </div>
        ))}
        <div className="report-actions">
          <Button
            variant="outline"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 25))}
          >
            Предыдущие события
          </Button>
          <Button
            variant="outline"
            disabled={offset + 25 >= data.events_total}
            onClick={() => setOffset(offset + 25)}
          >
            Следующие события
          </Button>
        </div>
      </details>
    </section>
  )
}

export function ReportWorkspace({ onSource }: { onSource: (id: string) => void }) {
  const [selected, setSelected] = useState(''),
    [creating, setCreating] = useState(false),
    [previous, setPrevious] = useState<Detail | undefined>(),
    [refresh, setRefresh] = useState(0),
    [q, setQ] = useState(''),
    [state, setState] = useState(''),
    [offset, setOffset] = useState(0)
  const options = useRemote(`report-options:${refresh}`, (signal) =>
    result(client.GET('/api/v1/reports/options', { signal })),
  )
  const list = useRemote(`reports:${q}:${state}:${offset}:${refresh}`, (signal) =>
    result(
      client.GET('/api/v1/reports', {
        params: {
          query: {
            q,
            state: (state || undefined) as
              'draft' | 'in_review' | 'returned' | 'reviewed' | 'released' | undefined,
            offset,
            limit: 20,
          },
        },
        signal,
      }),
    ),
  )
  return (
    <>
      {creating && options.data && (
        <CreateReport
          options={options.data}
          previous={previous}
          close={() => setCreating(false)}
          created={(id) => {
            setCreating(false)
            setSelected(id)
            setRefresh((v) => v + 1)
          }}
        />
      )}
      {selected ? (
        <ReportDetail
          key={selected}
          id={selected}
          back={() => {
            setSelected('')
            setRefresh((v) => v + 1)
          }}
          correct={(report) => {
            setPrevious(report)
            setCreating(true)
          }}
          onSource={onSource}
        />
      ) : (
        <section className="content-panel">
          <div className="section-heading">
            <div>
              <h2>Заключения и выписки</h2>
              <p className="muted">Подготовка, профессиональная проверка и неизменяемые выпуски</p>
            </div>
            {!!options.data?.proposals.length && (
              <Button
                onClick={() => {
                  setPrevious(undefined)
                  setCreating(true)
                }}
              >
                <Plus size={16} />
                Подготовить отчёт
              </Button>
            )}
          </div>
          <div className="filter-bar">
            <Input
              aria-label="Поиск отчётов"
              placeholder="Найти отчёт"
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setOffset(0)
              }}
            />
            <select
              aria-label="Состояние отчёта"
              value={state}
              onChange={(e) => {
                setState(e.target.value)
                setOffset(0)
              }}
            >
              <option value="">Все состояния</option>
              {Object.entries(states).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
            <Button variant="outline" onClick={() => setRefresh((v) => v + 1)}>
              Обновить
            </Button>
          </div>
          {(list.error || options.error) && <Feedback error={list.error || options.error} />}
          {list.loading ? (
            <p role="status">Загружаем отчёты…</p>
          ) : list.data?.items.length ? (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Отчёт</TableHead>
                    <TableHead>Состояние</TableHead>
                    <TableHead>Редакция</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {list.data.items.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell>
                        <Button
                          variant="ghost"
                          className="report-title-button"
                          onClick={() => setSelected(item.id)}
                        >
                          <FileText size={16} />
                          {item.title}
                        </Button>
                        <p className="small muted">
                          {item.kind === 'extract'
                            ? 'Выписка подразделению'
                            : 'Итоговое заключение'}
                        </p>
                      </TableCell>
                      <TableCell>{states[item.state]}</TableCell>
                      <TableCell>{item.revision}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="report-actions">
                <Button
                  variant="outline"
                  disabled={!offset}
                  onClick={() => setOffset(Math.max(0, offset - 20))}
                >
                  Предыдущая страница
                </Button>
                <span>
                  {offset + 1}–{Math.min(offset + 20, list.data.total)} из {list.data.total}
                </span>
                <Button
                  variant="outline"
                  disabled={offset + 20 >= list.data.total}
                  onClick={() => setOffset(offset + 20)}
                >
                  Следующая страница
                </Button>
              </div>
            </>
          ) : (
            !list.error && (
              <p className="report-empty">
                Доступных отчётов пока нет. Автор готовит черновик из предложения; назначенный
                рецензент получает его после направления на проверку, адресаты — после выпуска.
              </p>
            )
          )}
        </section>
      )}
    </>
  )
}
