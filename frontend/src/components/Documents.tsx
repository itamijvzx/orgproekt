import { useEffect, useState } from 'react'
import { Download, FileText, Upload } from 'lucide-react'
import { client, result, errorMessage } from '@/lib/client'
import { useRemote } from '@/lib/useRemote'
import { dateLabel } from '@/lib/api'
import type { Snapshot } from '@/lib/types'
import type { components, paths } from '@/lib/schema.generated'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog'
import { LoadState, Pagination, SearchField } from './ListControls'

type Detail = components['schemas']['DocumentDetail']
type Options = components['schemas']['DocumentOptions']
type Query = NonNullable<paths['/api/v1/documents']['get']['parameters']['query']>
const states: Record<string, string> = {
  manual_excerpt: 'Ручная выдержка',
  queued: 'В очереди',
  processing: 'Обрабатывается',
  ready: 'Текст извлечён',
  partial: 'Извлечён частично',
  needs_ocr: 'Требуется OCR',
  failed: 'Ошибка обработки',
  cancelled: 'Отменено',
  access_revoked: 'Доступ отозван',
}
const warnings: Record<string, string> = {
  external_links_not_fetched: 'Внешние ссылки не загружались.',
  ancillary_parts_not_extracted: 'Колонтитулы и сноски не извлечены.',
  unsupported_docx_content:
    'Есть изображения, исправления или другие части, которые требуют ручной проверки.',
  automatic_numbering_not_rendered: 'Автоматическая нумерация списков не восстановлена.',
  no_text_layer: 'Текстовый слой отсутствует.',
  pdf_layout_requires_review: 'Проверьте порядок текста, колонок и таблиц по оригиналу PDF.',
  form_fields_not_extracted: 'Поля PDF-формы не извлечены.',
}
const statusLabel = (value: string) =>
  ({
    draft: 'Черновик',
    unverified: 'Статус не проверен',
    approved: 'Учебная утверждённая запись',
    registered_excerpt: 'Зарегистрированная выдержка',
  })[value] ?? value

export function DocumentRegistry({
  data,
  revision,
  onSource,
}: {
  data: Snapshot
  revision: number
  onSource: (id: string) => void
}) {
  const [q, setQ] = useState('')
  const [department, setDepartment] = useState('')
  const [processing, setProcessing] = useState<Query['processing']>('all')
  const [format, setFormat] = useState<Query['file_format']>('all')
  const [offset, setOffset] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const [upload, setUpload] = useState<{ previous?: Detail } | null>(null)
  const options = useRemote(`document-options:${revision}:${refresh}`, (signal) =>
    result(client.GET('/api/v1/documents/options', { signal })),
  )
  const query: Query = {
    q,
    department_id: department || undefined,
    processing,
    file_format: format,
    offset,
    limit: 10,
  }
  const state = useRemote(`${JSON.stringify(query)}:${revision}:${refresh}`, (signal) =>
    result(client.GET('/api/v1/documents', { params: { query }, signal })),
  )
  return (
    <section className="content-panel">
      <div className="section-heading">
        <div>
          <h2>Документы и основания</h2>
          <p className="muted small">
            Оригиналы, редакции и проверяемые фрагменты в пределах ваших полномочий.
          </p>
        </div>
        {!!options.data?.writable_departments.length && (
          <Button onClick={() => setUpload({})}>
            <Upload size={16} />
            Загрузить документ
          </Button>
        )}
      </div>
      <div className="filter-bar">
        <SearchField
          label="Поиск документов"
          value={q}
          onChange={(v) => {
            setQ(v)
            setOffset(0)
          }}
        />
        <select
          aria-label="Подразделение"
          value={department}
          onChange={(e) => {
            setDepartment(e.target.value)
            setOffset(0)
          }}
        >
          <option value="">Все доступные подразделения</option>
          {data.departments.map((d) => (
            <option value={d.id} key={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Обработка документа"
          value={processing}
          onChange={(e) => {
            setProcessing(e.target.value as Query['processing'])
            setOffset(0)
          }}
        >
          <option value="all">Любая обработка</option>
          {Object.entries(states).map(([key, label]) => (
            <option value={key} key={key}>
              {label}
            </option>
          ))}
        </select>
        <select
          aria-label="Формат документа"
          value={format}
          onChange={(e) => {
            setFormat(e.target.value as Query['file_format'])
            setOffset(0)
          }}
        >
          <option value="all">Все форматы</option>
          <option value="docx">DOCX</option>
          <option value="pdf">PDF</option>
          <option value="manual">Ручные выдержки</option>
        </select>
      </div>
      <LoadState {...state} onRetry={() => setRefresh((v) => v + 1)} />
      {options.error && (
        <p className="error-box" role="alert">
          {options.error}
          <Button variant="outline" onClick={() => setRefresh((v) => v + 1)}>
            Повторить
          </Button>
        </p>
      )}
      {state.data && (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Документ / редакция</TableHead>
                <TableHead>Подразделение</TableHead>
                <TableHead>Обработка</TableHead>
                <TableHead>Источник</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {state.data.items.map((d) => (
                <TableRow key={d.id}>
                  <TableCell>
                    <button className="employee-name" onClick={() => setSelected(d.id)}>
                      {d.title}
                    </button>
                    <small className="table-kicker">
                      Редакция {d.revision} · {d.format?.toUpperCase() ?? 'Выдержка'} ·{' '}
                      {statusLabel(d.status)}
                    </small>
                  </TableCell>
                  <TableCell>{d.department_name}</TableCell>
                  <TableCell>{states[d.processing_status]}</TableCell>
                  <TableCell>
                    <div className="row-actions">
                      <Button size="sm" variant="outline" onClick={() => setSelected(d.id)}>
                        Открыть
                      </Button>
                      {!d.format &&
                        d.source_anchor_ids.map((id, i) => (
                          <Button key={id} variant="ghost" size="sm" onClick={() => onSource(id)}>
                            Фрагмент {i + 1}
                          </Button>
                        ))}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!state.data.total && (
            <div className="empty-state">
              <FileText />
              <h3>Документов не найдено</h3>
              <p>Измените поиск или загрузите DOCX/PDF.</p>
            </div>
          )}
          <Pagination {...state.data} onChange={setOffset} />
        </>
      )}
      {selected && (
        <DocumentCard
          key={selected}
          id={selected}
          onSource={onSource}
          onSelect={setSelected}
          onClose={() => {
            setSelected(null)
            setRefresh((v) => v + 1)
          }}
          onRevision={(previous) => {
            setSelected(null)
            setUpload({ previous })
          }}
        />
      )}
      {upload && options.data && (
        <UploadDocument
          data={data}
          options={options.data}
          previous={upload.previous}
          onClose={() => setUpload(null)}
          onUploaded={(id) => {
            setUpload(null)
            setSelected(id)
            setOffset(0)
            setRefresh((v) => v + 1)
          }}
        />
      )}
    </section>
  )
}

function UploadDocument({
  data,
  options,
  previous,
  onClose,
  onUploaded,
}: {
  data: Snapshot
  options: Options
  previous?: Detail
  onClose: () => void
  onUploaded: (id: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [synthetic, setSynthetic] = useState(false)
  const [key, setKey] = useState(() => crypto.randomUUID())
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <DialogContent className="document-dialog">
        <DialogHeader>
          <DialogTitle>{previous ? 'Новая редакция документа' : 'Загрузка документа'}</DialogTitle>
          <DialogDescription>
            DOCX или PDF с текстовым слоем · до 10 МиБ и 100 страниц PDF. Загрузка не утверждает
            документ и не меняет кадровые назначения.
          </DialogDescription>
        </DialogHeader>
        <form
          className="document-form"
          onChange={() => setKey(crypto.randomUUID())}
          onSubmit={async (e) => {
            e.preventDefault()
            if (busy) return
            const fields = new FormData(e.currentTarget)
            const file = fields.get('file')
            if (!(file instanceof File) || !file.size || file.size > options.max_file_bytes) {
              setError('Выберите непустой DOCX/PDF размером до 10 МиБ.')
              return
            }
            setBusy(true)
            setError('')
            try {
              const body = {
                file: '',
                department_id: previous?.department_id ?? String(fields.get('department_id')),
                title: String(fields.get('title')),
                revision: String(fields.get('revision')),
                expected_version: previous?.document_version ?? 0,
                document_id: previous?.document_id ?? undefined,
                document_kind: String(fields.get('document_kind')) as
                  'regulation' | 'job_description' | 'personnel_basis' | 'other',
                document_date: String(fields.get('document_date') ?? '') || undefined,
                effective_from: String(fields.get('effective_from') ?? '') || undefined,
                status: String(fields.get('status')) as 'draft' | 'unverified',
                synthetic,
                external_policy:
                  fields.has('external') && synthetic
                    ? ('synthetic_allowed' as const)
                    : ('prohibited' as const),
              }
              const response = await result(
                client.POST('/api/v1/documents/upload', {
                  params: { header: { 'idempotency-key': key } },
                  body,
                  bodySerializer: (values) => {
                    const form = new FormData()
                    for (const [k, v] of Object.entries(values))
                      if (k !== 'file' && v !== undefined && v !== null) form.append(k, String(v))
                    form.append('file', file)
                    return form
                  },
                }),
              )
              onUploaded(response.version_id)
            } catch (cause) {
              setError(errorMessage(cause))
            } finally {
              setBusy(false)
            }
          }}
        >
          <fieldset disabled={busy}>
            <label>
              Файл
              <Input name="file" type="file" accept=".docx,.pdf" required />
            </label>
            <label>
              Название документа
              <Input
                name="title"
                defaultValue={previous?.title ?? ''}
                minLength={3}
                maxLength={240}
                required
              />
            </label>
            <div className="document-form-grid">
              <label>
                Подразделение
                <select
                  name="department_id"
                  aria-label="Подразделение"
                  defaultValue={previous?.department_id ?? options.writable_departments[0]}
                  disabled={!!previous}
                >
                  {data.departments
                    .filter((d) => options.writable_departments.includes(d.id))
                    .map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Редакция
                <Input
                  name="revision"
                  defaultValue={previous ? '' : '1'}
                  placeholder={previous ? `После ${previous.revision}` : ''}
                  maxLength={30}
                  required
                />
              </label>
              <label>
                Вид документа
                <select
                  name="document_kind"
                  aria-label="Вид документа"
                  defaultValue={previous?.document_kind ?? 'regulation'}
                >
                  <option value="regulation">Положение</option>
                  <option value="job_description">Должностная инструкция</option>
                  <option value="personnel_basis">Кадровое основание</option>
                  <option value="other">Другой документ</option>
                </select>
              </label>
              <label>
                Официальный статус
                <select name="status" aria-label="Официальный статус" defaultValue="unverified">
                  <option value="unverified">Не проверен</option>
                  <option value="draft">Черновик</option>
                </select>
              </label>
              <label>
                Дата документа, если известна
                <Input type="date" name="document_date" />
              </label>
              <label>
                Действует с, если известно
                <Input type="date" name="effective_from" />
              </label>
            </div>
            <label className="document-check">
              <input
                type="checkbox"
                checked={synthetic}
                onChange={(e) => setSynthetic(e.target.checked)}
              />
              Все сведения в файле вымышлены
            </label>
            {synthetic && (
              <label className="document-check">
                <input type="checkbox" name="external" />
                Разрешить будущую внешнюю обработку этих синтетических данных
              </label>
            )}
            <p className="muted small">
              Сейчас извлечение выполняется локально. Внешние вызовы при загрузке не выполняются.
              Пустая дата означает «не установлена».
            </p>
          </fieldset>
          {error && (
            <p className="error-box" role="alert">
              {error}
              {error.includes('повторное подтверждение') &&
                ' Откройте настройки входа, подтвердите сессию и повторите загрузку.'}
            </p>
          )}
          <div className="row-actions">
            <Button type="submit" disabled={busy}>
              {busy ? 'Сохраняем файл…' : 'Загрузить и обработать'}
            </Button>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              Отмена
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function DocumentCard({
  id,
  onClose,
  onSelect,
  onSource,
  onRevision,
}: {
  id: string
  onClose: () => void
  onSelect: (id: string) => void
  onSource: (id: string) => void
  onRevision: (value: Detail) => void
}) {
  const [refresh, setRefresh] = useState(0)
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const state = useRemote(`${id}:${refresh}`, (signal) =>
    result(
      client.GET('/api/v1/documents/{version_id}', {
        params: { path: { version_id: id } },
        signal,
      }),
    ),
  )
  const job = state.data?.job
  useEffect(() => {
    if (!job || !['queued', 'processing'].includes(job.status)) return
    const timer = setTimeout(() => setRefresh((v) => v + 1), 2000)
    return () => clearTimeout(timer)
  }, [job])
  const fragments = useRemote(`${id}:${q}:${offset}:${job?.status}:${refresh}`, (signal) =>
    result(
      client.GET('/api/v1/documents/{version_id}/fragments', {
        params: { path: { version_id: id }, query: { q, offset, limit: 5 } },
        signal,
      }),
    ),
  )
  const history = useRemote(`${id}:${historyOffset}:${job?.status}:${refresh}`, (signal) =>
    result(
      client.GET('/api/v1/documents/{version_id}/versions', {
        params: { path: { version_id: id }, query: { offset: historyOffset, limit: 5 } },
        signal,
      }),
    ),
  )
  async function command(operation: 'retry' | 'cancel') {
    if (!job || busy) return
    setBusy(true)
    setError('')
    try {
      const args = { params: { path: { version_id: id } }, body: { expected_version: job.version } }
      await result(
        operation === 'retry'
          ? client.POST('/api/v1/documents/{version_id}/retry', args)
          : client.POST('/api/v1/documents/{version_id}/cancel', args),
      )
      setRefresh((v) => v + 1)
    } catch (cause) {
      setError(errorMessage(cause))
      setRefresh((v) => v + 1)
    } finally {
      setBusy(false)
    }
  }
  async function download() {
    setBusy(true)
    setError('')
    try {
      const blob = await result(
        client.GET('/api/v1/documents/{version_id}/file', {
          params: { path: { version_id: id } },
          parseAs: 'blob',
        }),
      )
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = state.data?.filename ?? 'document'
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="document-dialog">
        <DialogHeader>
          <DialogTitle>{state.data?.title ?? 'Документ'}</DialogTitle>
          <DialogDescription>
            Неизменяемая редакция и извлечённый текст. Полноту и применимость проверяет человек.
          </DialogDescription>
        </DialogHeader>
        <LoadState {...state} onRetry={() => setRefresh((v) => v + 1)} />
        {state.data && (
          <>
            <div className="document-meta">
              <span>
                Редакция {state.data.revision} · {statusLabel(state.data.status)}
              </span>
              <span>
                {state.data.synthetic
                  ? 'Вымышленные сведения'
                  : 'Синтетический характер не заявлен'}
              </span>
              <span>
                Дата документа:{' '}
                {state.data.document_date ? dateLabel(state.data.document_date) : 'не установлена'}
              </span>
              <span>
                Действует с:{' '}
                {state.data.effective_from
                  ? dateLabel(state.data.effective_from)
                  : 'не установлено'}
              </span>
              <span>
                {state.data.filename ?? 'Оригинал не загружен'}
                {state.data.size ? ` · ${(state.data.size / 1024).toFixed(1)} КиБ` : ''}
              </span>
              <span>
                Внешняя обработка:{' '}
                {state.data.external_policy === 'synthetic_allowed'
                  ? 'разрешена для синтетических данных'
                  : 'запрещена'}
              </span>
            </div>
            {job && (
              <section className="document-job" aria-label="Состояние обработки">
                <strong>{states[job.status]}</strong>
                <p role="status">{job.message}</p>
                <p className="small muted">
                  Блоков: {job.block_count} · фрагментов: {job.fragment_count}
                  {job.page_count !== null ? ` · страниц: ${job.page_count}` : ''} · попыток:{' '}
                  {job.attempts}/3
                </p>
                {job.warnings.map((w) => (
                  <p className="small" key={w}>
                    {warnings[w] ??
                      (w.startsWith('pages_without_text:')
                        ? `Страницы без текста: ${w.split(':')[1]}.`
                        : 'Часть содержимого требует ручной проверки.')}
                  </p>
                ))}
                <div className="row-actions">
                  {job.can_cancel && (
                    <Button disabled={busy} variant="outline" onClick={() => command('cancel')}>
                      Отменить обработку
                    </Button>
                  )}
                  {job.can_retry && (
                    <Button disabled={busy} variant="outline" onClick={() => command('retry')}>
                      Повторить обработку
                    </Button>
                  )}
                </div>
              </section>
            )}
            <div className="row-actions">
              {state.data.can_download && (
                <Button disabled={busy} variant="outline" onClick={download}>
                  <Download size={16} />
                  Скачать оригинал
                </Button>
              )}
              {state.data.can_upload_revision && (
                <Button disabled={busy} variant="outline" onClick={() => onRevision(state.data!)}>
                  Загрузить новую редакцию
                </Button>
              )}
            </div>
            {error && (
              <p role="alert" className="error-box">
                {error}
              </p>
            )}
            <section aria-label="Фрагменты документа">
              <h3>Текст и источники</h3>
              <SearchField
                label="Поиск по тексту документа"
                value={q}
                onChange={(v) => {
                  setQ(v)
                  setOffset(0)
                }}
              />
              <LoadState {...fragments} onRetry={() => setRefresh((v) => v + 1)} />
              {fragments.data && (
                <>
                  {fragments.data.items.map((f) => (
                    <article className="document-fragment" key={f.id}>
                      <span className="source-locator">{f.locator}</span>
                      <p>{f.quote}</p>
                      <Button size="sm" variant="outline" onClick={() => onSource(f.id)}>
                        Открыть источник
                      </Button>
                    </article>
                  ))}
                  {!fragments.data.total && (
                    <p className="muted">
                      Доступных фрагментов по этому запросу нет. Это не доказательство отсутствия
                      функции.
                    </p>
                  )}
                  <Pagination {...fragments.data} onChange={setOffset} />
                </>
              )}
            </section>
            <section aria-label="Редакции документа">
              <h3>История редакций</h3>
              <LoadState {...history} onRetry={() => setRefresh((v) => v + 1)} />
              {history.data && (
                <>
                  {history.data.items.map((v) => (
                    <div className="document-version" key={v.id}>
                      <span>
                        Редакция {v.revision} · {states[v.processing_status]}
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={v.id === id}
                        onClick={() => onSelect(v.id)}
                      >
                        {v.id === id ? 'Открыта' : 'Посмотреть'}
                      </Button>
                    </div>
                  ))}
                  {history.data.total > 5 && (
                    <Pagination {...history.data} onChange={setHistoryOffset} />
                  )}
                </>
              )}
            </section>
            <p className="document-hash small muted">SHA-256: {state.data.sha256}</p>
          </>
        )}
        <Button variant="outline" onClick={onClose}>
          Закрыть документ
        </Button>
      </DialogContent>
    </Dialog>
  )
}
