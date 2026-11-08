import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  CheckCheck,
  FileText,
  GitPullRequest,
  History,
  Info,
  LockKeyhole,
  MessageSquare,
  Plus,
  Send,
  Trash2,
} from 'lucide-react'
import { ApiError, client, errorMessage, result } from '@/lib/client'
import { dateLabel, localToday } from '@/lib/api'
import { workspaceName } from '@/lib/branding'
import { useRemote } from '@/lib/useRemote'
import { useLocalDemo } from '@/lib/authPolicy'
import type { FunctionItem, Snapshot, User } from '@/lib/types'
import type { components } from '@/lib/schema.generated'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs'
import { LoadState, Pagination, SearchField } from './ListControls'
import '@/changes.css'

type S = components['schemas']
type Detail = S['ProposalDetail']
type Version = S['ChangeVersion']
type Entry = S['ChangeEntry']
type Options = S['ChangeOptions']
type Context = S['ChangeContext']
type ItemInput = S['ChangeItemInput']
type SectionInput = S['ChangeSectionInput']
type Draft = S['ProposalCreate']
type Kind = ItemInput['kind']
type State = S['ProposalSummary']['state']
type Props = {
  data: Snapshot
  user: User
  onSource: (id: string) => void
  revision: number
  initialFunction?: FunctionItem | null
  onInitialFunctionHandled?: () => void
  initialProposalId?: string | null
  onInitialProposalHandled?: () => void
}
const states: Record<State, string> = {
  draft: 'Черновик',
  in_review: 'На согласовании',
  returned: 'На доработке',
  rejected: 'Отклонено',
  approved: 'Утверждено',
  risk_accepted: 'Риск принят',
  planned: 'Исполнение запланировано',
  partially_executed: 'Частично исполнено',
  executed: 'Исполнено',
  verified: 'Проверено аудитором',
  cancelled: 'Отменено',
}
const kinds: Record<Kind, string> = {
  transfer_function: 'Перенос функции',
  responsibility: 'Изменение ответственности',
  split_function: 'Разделение функции',
  merge_function: 'Объединение функций',
  abolish_function: 'Упразднение функции',
  add_seat: 'Добавление штатной единицы',
  close_seat: 'Закрытие штатной единицы',
  position: 'Изменение должности',
  reporting: 'Изменение подчинённости',
  temporary_substitution: 'Временное замещение',
  document: 'Изменение документов',
}
const outcomes: Record<string, string> = {
  agree: 'Согласовано подразделением',
  return: 'Возвращено на доработку',
  reject: 'Отклонено',
  request_info: 'Запрошены сведения',
  approve: 'Утверждено руководителем',
  accept_risk: 'Риск принят руководителем',
  planned: 'Запланировано',
  completed: 'Зарегистрировано исполнение',
}
const entryLabels: Record<string, string> = {
  created: 'Создано предложение',
  version_created: 'Создана новая версия',
  revised: 'Создана новая версия',
  send: 'Направлено на согласование',
  message: 'Сообщение',
  private_note: 'Личная заметка',
  review: 'Ответ подразделения',
  decision: 'Управленческое решение',
  execution: 'Исполнение',
  verified: 'Проверка аудитора',
  verify: 'Проверка аудитора',
  reminder: 'Напоминание',
  escalation: 'Эскалация',
  cancelled: 'Предложение отменено',
  delivery_retry: 'Повторная доставка уведомления',
}
const stamp = (value: string, user: User) =>
  new Date(value).toLocaleString('ru-RU', {
    timeZone: user.timezone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
function useKey() {
  const ref = useRef({ signature: '', key: '' })
  return (body: unknown) => {
    const signature = JSON.stringify(body)
    if (ref.current.signature !== signature) ref.current = { signature, key: crypto.randomUUID() }
    return { 'idempotency-key': ref.current.key }
  }
}
function useCommand() {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [reauth, setReauth] = useState(false)
  const [conflict, setConflict] = useState(false)
  async function run(command: () => Promise<unknown>, success: () => void) {
    setPending(true)
    setError('')
    setConflict(false)
    try {
      await command()
      setReauth(false)
      success()
    } catch (e) {
      setError(
        [
          errorMessage(e),
          ...(e instanceof ApiError ? e.fields.map((field) => field.message) : []),
        ].join(' '),
      )
      setReauth(
        e instanceof ApiError &&
          (['reauthentication_required', 'reauth_required', 'fresh_auth_required'].includes(
            e.code,
          ) ||
            (e.status === 403 && e.message.includes('повторное подтверждение входа'))),
      )
      setConflict(e instanceof ApiError && e.status === 409)
    } finally {
      setPending(false)
    }
  }
  return {
    pending,
    error,
    reauth,
    conflict,
    run,
    clearReauth: () => {
      setReauth(false)
      setError('Вход подтверждён. Повторите действие в форме.')
    },
  }
}
function CommandFeedback({
  command,
  refresh,
}: {
  command: ReturnType<typeof useCommand>
  refresh?: () => void
}) {
  return (
    <>
      {command.error && (
        <div className="error-box change-error-actions" role="alert">
          <span>{command.error}</span>
          {command.conflict && (
            <span>
              Данные изменились. Введённый текст сохранён в форме. Обновите сведения и проверьте
              актуальную версию перед повторной отправкой.
            </span>
          )}
          {command.conflict && refresh && (
            <Button type="button" variant="outline" size="sm" onClick={refresh}>
              Обновить сведения
            </Button>
          )}
        </div>
      )}
      {command.reauth && <Reauthenticate onDone={command.clearReauth} />}
    </>
  )
}
function Reauthenticate({ onDone }: { onDone: () => void }) {
  const localDemo = useLocalDemo()
  const [password, setPassword] = useState(''),
    [code, setCode] = useState('')
  const [pending, setPending] = useState(false),
    [error, setError] = useState('')
  async function confirm() {
    setPending(true)
    setError('')
    try {
      await result(client.POST('/api/v1/auth/reauthenticate', { body: { password, code } }))
      setPassword('')
      setCode('')
      onDone()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <div className="change-reauth">
      <h4>Подтвердите вход для этого действия</h4>
      <label className="change-field">
        Текущий пароль
        <Input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {!localDemo && (
        <label className="change-field">
          Новый код MFA или резервный код
          <Input
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
      )}
      {error && (
        <p role="alert" className="error-box">
          {error}
        </p>
      )}
      <Button
        type="button"
        variant="outline"
        disabled={pending || !password || (!localDemo && !code)}
        onClick={confirm}
      >
        {pending ? 'Проверяем…' : 'Подтвердить вход'}
      </Button>
    </div>
  )
}
function Status({ state }: { state: State }) {
  const tone = ['approved', 'executed', 'verified'].includes(state)
    ? 'positive'
    : ['returned', 'risk_accepted', 'partially_executed'].includes(state)
      ? 'attention'
      : state === 'rejected'
        ? 'negative'
        : undefined
  return (
    <span className="change-status" data-tone={tone}>
      {states[state]}
    </span>
  )
}
function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="change-empty">
      <GitPullRequest size={24} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  )
}
function Sources({ ids, onSource }: { ids: string[]; onSource: (id: string) => void }) {
  return ids.length ? (
    <div className="change-source-links">
      {ids.map((id, i) => (
        <Button type="button" key={id} size="sm" variant="outline" onClick={() => onSource(id)}>
          <FileText size={14} />
          Основание {i + 1}
        </Button>
      ))}
    </div>
  ) : null
}
function SourcePicker({
  context,
  selected,
  onChange,
  label = 'Документальные основания',
}: {
  context: Context | null
  selected: string[]
  onChange: (ids: string[]) => void
  label?: string
}) {
  return (
    <div className="change-field">
      <label>
        {label}
        <select
          aria-label={label}
          value=""
          onChange={(e) => {
            if (e.target.value) onChange([...selected, e.target.value])
          }}
        >
          <option value="">Добавить точный фрагмент…</option>
          {context?.sources
            .filter((s) => !selected.includes(s.id))
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
        </select>
      </label>
      {selected.map((id, i) => {
        const source = context?.sources.find((s) => s.id === id)
        return (
          <div key={id} className="change-choice">
            <div style={{ flex: 1, minWidth: 0 }}>
              <strong>{source ? source.label : `Сохранённое основание ${i + 1}`}</strong>
              {source && (
                <details>
                  <summary className="small muted">Показать цитату</summary>
                  <p className="change-prose">{source.quote}</p>
                </details>
              )}
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Убрать основание ${i + 1}`}
              onClick={() => onChange(selected.filter((x) => x !== id))}
            >
              <Trash2 size={14} />
            </Button>
          </div>
        )
      })}
      {!context?.sources.length && (
        <p className="small muted">Нет доступных фрагментов для этого подразделения.</p>
      )}
    </div>
  )
}
const blankItem = (): ItemInput => ({
  key: crypto.randomUUID(),
  kind: 'responsibility',
  target_id: null,
  before: '',
  after: '',
  source_anchor_ids: [],
  requirements: '',
  candidate_employee_id: null,
  qualification_basis: '',
  qualification_source_ids: [],
})
const blankSection = (department: string): SectionInput => ({
  department_id: department,
  title: '',
  rationale: '',
  items: [blankItem()],
})
function SectionEditor({
  section,
  index,
  options,
  onChange,
  onRemove,
}: {
  section: SectionInput
  index: number
  options: Options
  onChange: (value: SectionInput) => void
  onRemove?: () => void
}) {
  const [retry, setRetry] = useState(0)
  const context = useRemote(`change-context:${section.department_id}:${retry}`, (signal) =>
    section.department_id
      ? result(
          client.GET('/api/v1/changes/context', {
            params: { query: { department_id: section.department_id } },
            signal,
          }),
        )
      : Promise.resolve(null),
  )
  const updateItem = (i: number, value: Partial<ItemInput>) =>
    onChange({
      ...section,
      items: section.items.map((item, j) => (i === j ? { ...item, ...value } : item)),
    })
  return (
    <fieldset>
      <legend>Раздел {index + 1}</legend>
      <div className="change-field-pair">
        <label className="change-field">
          Подразделение {index + 1}
          <select
            required
            value={section.department_id}
            onChange={(e) => onChange(blankSection(e.target.value))}
          >
            <option value="">Выберите подразделение</option>
            {options.departments
              .filter((d) => options.author_departments.includes(d.id))
              .map((d) => (
                <option key={d.id} value={d.id}>
                  {d.label}
                </option>
              ))}
          </select>
        </label>
        <label className="change-field">
          Название раздела {index + 1}
          <Input
            required
            minLength={3}
            maxLength={180}
            value={section.title}
            onChange={(e) => onChange({ ...section, title: e.target.value })}
          />
        </label>
      </div>
      <label className="change-field">
        Цель и обоснование {index + 1}
        <textarea
          required
          minLength={3}
          maxLength={5000}
          value={section.rationale}
          onChange={(e) => onChange({ ...section, rationale: e.target.value })}
        />
      </label>
      <LoadState
        loading={context.loading}
        error={context.error}
        onRetry={() => setRetry((x) => x + 1)}
      />
      {section.items.map((item, itemIndex) => (
        <fieldset key={item.key}>
          <legend>
            Изменение {index + 1}.{itemIndex + 1}
          </legend>
          <div className="change-field-pair">
            <label className="change-field">
              Вид изменения {index + 1}.{itemIndex + 1}
              <select
                value={item.kind}
                onChange={(e) =>
                  updateItem(itemIndex, { kind: e.target.value as Kind, target_id: null })
                }
              >
                {Object.entries(kinds).map(([key, text]) => (
                  <option key={key} value={key}>
                    {text}
                  </option>
                ))}
              </select>
            </label>
            <label className="change-field">
              Объект изменения {index + 1}.{itemIndex + 1}
              <select
                value={item.target_id ?? ''}
                onChange={(e) => updateItem(itemIndex, { target_id: e.target.value || null })}
              >
                <option value="">Не выбран / новый объект</option>
                {(item.kind.includes('function') || item.kind === 'responsibility'
                  ? context.data?.functions
                  : item.kind === 'reporting'
                    ? options.departments.filter((d) => d.id === section.department_id)
                    : ['close_seat', 'temporary_substitution'].includes(item.kind)
                      ? context.data?.seats
                      : item.kind === 'document'
                        ? []
                        : context.data?.positions
                )?.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="change-field-pair">
            <label className="change-field">
              До изменения {index + 1}.{itemIndex + 1}
              <textarea
                required
                maxLength={3000}
                value={item.before}
                onChange={(e) => updateItem(itemIndex, { before: e.target.value })}
              />
            </label>
            <label className="change-field">
              После изменения {index + 1}.{itemIndex + 1}
              <textarea
                required
                maxLength={3000}
                value={item.after}
                onChange={(e) => updateItem(itemIndex, { after: e.target.value })}
              />
            </label>
          </div>
          <label className="change-field">
            Требования и ограничения {index + 1}.{itemIndex + 1}
            <textarea
              maxLength={2000}
              value={item.requirements ?? ''}
              onChange={(e) => updateItem(itemIndex, { requirements: e.target.value })}
            />
          </label>
          <SourcePicker
            label={`Основания изменения ${index + 1}.${itemIndex + 1}`}
            context={context.data}
            selected={item.source_anchor_ids ?? []}
            onChange={(source_anchor_ids) => updateItem(itemIndex, { source_anchor_ids })}
          />
          <details>
            <summary className="small">Предлагаемый исполнитель и квалификация</summary>
            <div className="change-stack" style={{ marginTop: 12 }}>
              <label className="change-field">
                Предлагаемый исполнитель {index + 1}.{itemIndex + 1}
                <select
                  value={item.candidate_employee_id ?? ''}
                  onChange={(e) =>
                    updateItem(itemIndex, { candidate_employee_id: e.target.value || null })
                  }
                >
                  <option value="">Не предлагается</option>
                  {context.data?.employees.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="change-field">
                Обоснование квалификации {index + 1}.{itemIndex + 1}
                <textarea
                  maxLength={2000}
                  value={item.qualification_basis ?? ''}
                  onChange={(e) => updateItem(itemIndex, { qualification_basis: e.target.value })}
                />
              </label>
              <SourcePicker
                context={context.data}
                label={`Документы о квалификации ${index + 1}.${itemIndex + 1}`}
                selected={item.qualification_source_ids ?? []}
                onChange={(qualification_source_ids) =>
                  updateItem(itemIndex, { qualification_source_ids })
                }
              />
              <p className="small muted">
                Это кандидатура для рассмотрения. Назначение подтверждается отдельно официальным
                событием.
              </p>
            </div>
          </details>
          {section.items.length > 1 && (
            <Button
              type="button"
              variant="ghost"
              onClick={() =>
                onChange({ ...section, items: section.items.filter((_, i) => i !== itemIndex) })
              }
            >
              <Trash2 size={14} />
              Убрать изменение {index + 1}.{itemIndex + 1}
            </Button>
          )}
        </fieldset>
      ))}
      <div className="change-actions">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={section.items.length >= 30}
          onClick={() => onChange({ ...section, items: [...section.items, blankItem()] })}
        >
          <Plus size={14} />
          Добавить изменение
        </Button>
        {onRemove && (
          <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
            Убрать раздел
          </Button>
        )}
      </div>
    </fieldset>
  )
}

function SendDialog({
  detail,
  options,
  version,
  user,
  close,
  refresh,
}: {
  detail: Detail
  options: Options
  version: Version
  user: User
  close: () => void
  refresh: () => void
}) {
  const [recipients, setRecipients] = useState<S['Recipient'][]>([])
  const [due, setDue] = useState(() => {
    const d = new Date(Date.now() + 7 * 86400000)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T18:00`
  })
  const [message, setMessage] = useState(
    'Прошу рассмотреть эту версию предложения, проверить основания и зафиксировать ответ подразделения.',
  )
  const [ack, setAck] = useState(false),
    command = useCommand(),
    headers = useKey()
  const alreadySent = detail.process_entries.filter(
    (e) => e.kind === 'send' && e.version_number === version.number,
  )
  const available = options.participants.filter(
    (p) =>
      version.sections.some((s) => s.department_id === p.department_id) &&
      p.id !== user.id &&
      !alreadySent.some((e) => e.recipient_id === p.id && e.department_id === p.department_id),
  )
  const managerAvailable = options.participants.some(
    (participant) =>
      participant.id !== user.id &&
      version.sections.every((section) =>
        options.participants.some(
          (candidate) =>
            candidate.id === participant.id &&
            candidate.department_id === section.department_id &&
            candidate.roles.includes('manager'),
        ),
      ),
  )
  const covered =
    recipients.length > 0 &&
    version.sections.every(
      (s) =>
        recipients.some((r) => r.department_id === s.department_id) ||
        alreadySent.some((e) => e.department_id === s.department_id),
    )
  const impacts = version.sections.flatMap((s) =>
    s.items.flatMap((i) => i.impacts.map((text) => ({ department: s.department_name, text }))),
  )
  async function submit(event: FormEvent) {
    event.preventDefault()
    const body = {
      expected_version: detail.version,
      recipients,
      due_at: new Date(due).toISOString(),
      message,
    }
    await command.run(
      () =>
        result(
          client.POST('/api/v1/changes/{proposal_id}/send', {
            params: { path: { proposal_id: detail.id }, header: headers(body) },
            body,
          }),
        ),
      () => {
        close()
        refresh()
      },
    )
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !command.pending) close()
      }}
    >
      <DialogContent className="change-dialog change-dialog-small">
        <DialogHeader>
          <DialogTitle>Направить на согласование</DialogTitle>
          <DialogDescription>
            {version.title} · версия {version.number} · проектная дата{' '}
            {dateLabel(version.effective_date)}. Личные заметки не отправляются.
          </DialogDescription>
        </DialogHeader>
        <form className="change-form" onSubmit={submit} aria-busy={command.pending}>
          <div>
            <h3 className="small" style={{ marginBottom: 8 }}>
              Адресаты затронутых подразделений
            </h3>
            <div className="change-choices">
              {available.map((p) => {
                const checked = recipients.some(
                  (r) => r.user_id === p.id && r.department_id === p.department_id,
                )
                return (
                  <label className="change-choice" key={`${p.department_id}:${p.id}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setRecipients(
                          checked
                            ? recipients.filter(
                                (r) => !(r.user_id === p.id && r.department_id === p.department_id),
                              )
                            : [...recipients, { user_id: p.id, department_id: p.department_id }],
                        )
                      }
                    />
                    <span>
                      <strong>{p.label}</strong>
                      <small>
                        {options.departments.find((d) => d.id === p.department_id)?.label}
                      </small>
                    </span>
                  </label>
                )
              })}
            </div>
            {!available.length && (
              <p className="small muted">
                Нет доступных уполномоченных адресатов. Администратор доступа должен назначить
                полномочия в затронутых подразделениях.
              </p>
            )}
            {!covered && (
              <p className="small muted" style={{ marginTop: 8 }}>
                Выберите хотя бы одного адресата каждого раздела.
              </p>
            )}
            {!managerAvailable && (
              <p className="change-notice" style={{ marginTop: 12 }}>
                <Info size={16} />
                <span>
                  Среди доступных участников нет уполномоченного руководителя. Для управленческого
                  решения нужно назначить его отдельно.
                </span>
              </p>
            )}
          </div>
          <label className="change-field">
            Срок ответа
            <Input
              type="datetime-local"
              required
              value={due}
              onChange={(e) => setDue(e.target.value)}
            />
            <span className="small muted">
              Время вашего браузера. В карточке срок показывается в часовом поясе {user.timezone}.
            </span>
          </label>
          <label className="change-field">
            Сообщение адресатам
            <textarea
              required
              minLength={3}
              maxLength={3000}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
          </label>
          <details open>
            <summary className="small">
              Последствия и необходимые проверки ({impacts.length})
            </summary>
            <ul className="change-impact-list">
              {impacts.map((impact, i) => (
                <li key={i}>
                  <strong>{impact.department}:</strong> {impact.text}
                </li>
              ))}
            </ul>
            {!impacts.length && (
              <p className="small muted">
                Автоматические проверки не выявили дополнительных условий. Проверьте основания и
                исполнителей вручную.
              </p>
            )}
          </details>
          <label className="change-choice">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            <span>
              Я проверил(а) версию, адресатов и последствия. Отправить выбранным участникам внутри
              OrgProekt.
            </span>
          </label>
          <CommandFeedback command={command} refresh={refresh} />
          <div className="change-actions">
            <Button type="button" variant="outline" disabled={command.pending} onClick={close}>
              Отмена
            </Button>
            <Button disabled={command.pending || !covered || !ack}>
              {command.pending ? 'Отправляем…' : 'Отправить версию участникам'}
              <Send size={15} />
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function EntryCard({
  entry,
  user,
  departments,
  onSource,
}: {
  entry: Entry
  user: User
  departments: Detail['departments']
  onSource: (id: string) => void
}) {
  return (
    <article className="change-message">
      <header>
        <strong>{entry.actor_name}</strong>
        <span>
          {entryLabels[entry.kind] ?? 'Событие предложения'} · v{entry.version_number}
        </span>
        <time dateTime={entry.created_at}>{stamp(entry.created_at, user)}</time>
      </header>
      {entry.department_id && (
        <p className="small muted">
          {departments.find((d) => d.id === entry.department_id)?.label}
        </p>
      )}
      {entry.outcome && (
        <p className="small" style={{ marginTop: 6 }}>
          {outcomes[entry.outcome] ?? entry.outcome}
          {!entry.applicable && entry.kind === 'review'
            ? ' · не является актуальным согласованием'
            : ''}
        </p>
      )}
      <p className="change-prose">{entry.text}</p>
      <Sources ids={entry.source_anchor_ids} onSource={onSource} />
    </article>
  )
}

function Discussion({
  detail,
  version,
  user,
  onSource,
  refresh,
  privateMode = false,
}: {
  detail: Detail
  version: Version
  user: User
  onSource: (id: string) => void
  refresh: () => void
  privateMode?: boolean
}) {
  const [department, setDepartment] = useState(version.sections[0]?.department_id ?? '')
  const [text, setText] = useState(''),
    [sources, setSources] = useState<string[]>([])
  const [retry, setRetry] = useState(0),
    command = useCommand(),
    headers = useKey()
  const context = useRemote(`discussion-context:${department}:${retry}`, (signal) =>
    department
      ? result(
          client.GET('/api/v1/changes/context', {
            params: { query: { department_id: department } },
            signal,
          }),
        )
      : Promise.resolve(null),
  )
  const entries = detail.entries.filter(
    (e) =>
      e.version_number === version.number &&
      (privateMode
        ? e.kind === 'private_note'
        : ['message', 'review', 'decision', 'send', 'reminder', 'escalation'].includes(e.kind)),
  )
  const canWrite =
    version.number === detail.current_version &&
    (privateMode ? detail.permissions.private_note : detail.permissions.message)
  async function submit(event: FormEvent) {
    event.preventDefault()
    const body = {
      expected_version: detail.version,
      department_id: department,
      text,
      source_anchor_ids: sources,
    }
    await command.run(
      () =>
        privateMode
          ? result(
              client.POST('/api/v1/changes/{proposal_id}/notes', {
                params: { path: { proposal_id: detail.id }, header: headers(body) },
                body,
              }),
            )
          : result(
              client.POST('/api/v1/changes/{proposal_id}/messages', {
                params: { path: { proposal_id: detail.id }, header: headers(body) },
                body,
              }),
            ),
      () => {
        setText('')
        setSources([])
        refresh()
      },
    )
  }
  return (
    <div>
      {privateMode && (
        <div className="change-notice change-private">
          <LockKeyhole size={16} />
          <span>
            Личные рабочие заметки. Доступны только вам; не включаются в сообщения и отправку
            предложения.
          </span>
        </div>
      )}
      {entries.length ? (
        entries.map((e) => (
          <EntryCard
            key={e.id}
            entry={e}
            user={user}
            departments={detail.departments}
            onSource={onSource}
          />
        ))
      ) : (
        <Empty
          title={
            detail.history_total > detail.history_limit
              ? 'В этой части истории нет записей выбранной версии'
              : privateMode
                ? 'Личных заметок пока нет'
                : 'Обсуждение этой версии ещё не началось'
          }
        >
          {privateMode
            ? 'Зафиксируйте рабочие наблюдения отдельно от переписки с подразделением.'
            : 'Сообщения и ответы сохраняются с версией предложения. Ответ подразделения оформляется на вкладке «Согласование».'}
        </Empty>
      )}
      {canWrite && (
        <form onSubmit={submit} className="change-form change-composer" aria-busy={command.pending}>
          <label className="change-field">
            {privateMode ? 'Раздел личной заметки' : 'Раздел обсуждения'}
            <select
              value={department}
              onChange={(e) => {
                setDepartment(e.target.value)
                setSources([])
              }}
            >
              {version.sections.map((s) => (
                <option key={s.department_id} value={s.department_id}>
                  {s.department_name}
                </option>
              ))}
            </select>
          </label>
          <p className="change-form-description">
            {privateMode
              ? 'Заметка не публикуется для участников.'
              : 'Сообщение будет доступно уполномоченным участникам выбранного раздела, аудитору и руководителю в пределах их прав.'}
          </p>
          <label className="change-field">
            {privateMode ? 'Текст личной заметки' : 'Текст сообщения'}
            <textarea
              required
              maxLength={5000}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <LoadState
            loading={context.loading}
            error={context.error}
            onRetry={() => setRetry((x) => x + 1)}
          />
          <SourcePicker
            context={context.data}
            selected={sources}
            onChange={setSources}
            label={privateMode ? 'Основания личной заметки' : 'Основания сообщения'}
          />
          <CommandFeedback command={command} refresh={refresh} />
          <div className="change-actions">
            <Button disabled={command.pending || !department}>
              {command.pending
                ? 'Сохраняем…'
                : privateMode
                  ? 'Сохранить личную заметку'
                  : 'Отправить сообщение'}
              {privateMode ? <LockKeyhole size={15} /> : <Send size={15} />}
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}

function ReviewForm({
  detail,
  version,
  refresh,
}: {
  detail: Detail
  version: Version
  refresh: () => void
}) {
  const available = version.sections.filter((s) => s.can_review)
  const [department, setDepartment] = useState(available[0]?.department_id ?? '')
  const [outcome, setOutcome] = useState<S['ProposalReview']['outcome']>('agree')
  const [text, setText] = useState(''),
    [sources, setSources] = useState<string[]>([]),
    [retry, setRetry] = useState(0)
  const command = useCommand(),
    headers = useKey()
  const context = useRemote(`review-context:${department}:${retry}`, (signal) =>
    department
      ? result(
          client.GET('/api/v1/changes/context', {
            params: { query: { department_id: department } },
            signal,
          }),
        )
      : Promise.resolve(null),
  )
  const permittedDepartments = available.map((section) => section.department_id).join(',')
  useEffect(() => {
    const departments = permittedDepartments ? permittedDepartments.split(',') : []
    if (!departments.includes(department)) {
      setDepartment(departments[0] ?? '')
      setSources([])
    }
  }, [department, permittedDepartments])
  if (!available.length || version.number !== detail.current_version) return null
  async function submit(event: FormEvent) {
    event.preventDefault()
    const body = {
      expected_version: detail.version,
      department_id: department,
      outcome,
      text,
      source_anchor_ids: sources,
    }
    await command.run(
      () =>
        result(
          client.POST('/api/v1/changes/{proposal_id}/reviews', {
            params: { path: { proposal_id: detail.id }, header: headers(body) },
            body,
          }),
        ),
      () => {
        setText('')
        setSources([])
        refresh()
      },
    )
  }
  return (
    <section className="change-review-panel">
      <h3>Ответ подразделения · версия {version.number}</h3>
      <form className="change-form" onSubmit={submit} aria-busy={command.pending}>
        <label className="change-field">
          Подразделение для ответа
          <select
            value={department}
            onChange={(e) => {
              setDepartment(e.target.value)
              setSources([])
            }}
          >
            {available.map((s) => (
              <option value={s.department_id} key={s.department_id}>
                {s.department_name}
              </option>
            ))}
          </select>
        </label>
        <label className="change-field">
          Результат рассмотрения
          <select
            value={outcome}
            onChange={(e) => setOutcome(e.target.value as S['ProposalReview']['outcome'])}
          >
            <option value="agree">Согласовать</option>
            <option value="return">Вернуть на доработку</option>
            <option value="reject">Отклонить с обоснованием</option>
            <option value="request_info">Запросить сведения</option>
          </select>
        </label>
        <label className="change-field">
          Обоснование ответа
          <textarea
            required
            minLength={3}
            maxLength={5000}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <LoadState
          loading={context.loading}
          error={context.error}
          onRetry={() => setRetry((x) => x + 1)}
        />
        <SourcePicker
          context={context.data}
          selected={sources}
          onChange={setSources}
          label="Основания ответа"
        />
        <p className="change-form-description">
          Согласие относится к этой версии и разрешённому разделу. Оно не заменяет управленческое
          утверждение.
        </p>
        <CommandFeedback command={command} refresh={refresh} />
        <Button disabled={command.pending}>
          {command.pending ? 'Отправляем…' : 'Отправить ответ подразделения'}
        </Button>
      </form>
    </section>
  )
}

function DecisionForm({
  detail,
  version,
  refresh,
}: {
  detail: Detail
  version: Version
  refresh: () => void
}) {
  const [outcome, setOutcome] = useState<S['ProposalDecision']['outcome']>('approve')
  const [reason, setReason] = useState(''),
    [department, setDepartment] = useState(version.sections[0]?.department_id ?? ''),
    [sources, setSources] = useState<string[]>([]),
    [retry, setRetry] = useState(0)
  const command = useCommand(),
    headers = useKey()
  const context = useRemote(`decision-context:${department}:${retry}`, (signal) =>
    department
      ? result(
          client.GET('/api/v1/changes/context', {
            params: { query: { department_id: department } },
            signal,
          }),
        )
      : Promise.resolve(null),
  )
  if (!detail.permissions.decide || version.number !== detail.current_version) return null
  async function submit(event: FormEvent) {
    event.preventDefault()
    const body = { expected_version: detail.version, outcome, reason, source_anchor_ids: sources }
    await command.run(
      () =>
        result(
          client.POST('/api/v1/changes/{proposal_id}/decisions', {
            params: { path: { proposal_id: detail.id }, header: headers(body) },
            body,
          }),
        ),
      () => {
        setReason('')
        setSources([])
        refresh()
      },
    )
  }
  return (
    <section className="change-review-panel">
      <h3>Решение руководителя · версия {version.number}</h3>
      <form className="change-form" onSubmit={submit} aria-busy={command.pending}>
        <label className="change-field">
          Управленческое решение
          <select
            value={outcome}
            onChange={(e) => setOutcome(e.target.value as S['ProposalDecision']['outcome'])}
          >
            <option value="approve">Утвердить предложение</option>
            <option value="return">Вернуть на доработку</option>
            <option value="reject">Отклонить предложение</option>
            <option value="accept_risk">Принять риск</option>
          </select>
        </label>
        <label className="change-field">
          Основание решения
          <textarea
            required
            minLength={3}
            maxLength={5000}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <label className="change-field">
          Раздел документальных оснований
          <select value={department} onChange={(e) => setDepartment(e.target.value)}>
            {version.sections.map((s) => (
              <option key={s.department_id} value={s.department_id}>
                {s.department_name}
              </option>
            ))}
          </select>
        </label>
        <LoadState
          loading={context.loading}
          error={context.error}
          onRetry={() => setRetry((x) => x + 1)}
        />
        <SourcePicker
          context={context.data}
          selected={sources}
          onChange={setSources}
          label="Документы к решению"
        />
        <div className="change-notice">
          <Info size={16} />
          {outcome === 'accept_risk'
            ? 'Принятие риска фиксируется отдельно. Замечание аудитора и предыдущие ответы сохраняются.'
            : 'Утверждение фиксирует решение. Действующая структура меняется только по официальному основанию и отдельному событию исполнения.'}
        </div>
        <CommandFeedback command={command} refresh={refresh} />
        <Button disabled={command.pending}>
          {command.pending ? 'Сохраняем…' : 'Зафиксировать решение руководителя'}
        </Button>
      </form>
    </section>
  )
}

function ProposalEditor({
  options,
  initialFunction,
  detail,
  close,
  saved,
  refresh,
}: {
  options: Options
  initialFunction?: FunctionItem | null
  detail?: Detail
  close: () => void
  saved: (id: string) => void
  refresh: () => void
}) {
  const current = detail?.versions.find((v) => v.number === detail.current_version)
  const [draft, setDraft] = useState<Draft>(() =>
    current
      ? {
          title: current.title,
          effective_date: current.effective_date,
          sections: current.sections.map((s) => ({
            department_id: s.department_id,
            title: s.title,
            rationale: s.rationale,
            items: s.items.map(({ impacts: _impacts, ...item }) => item),
          })),
        }
      : {
          title: initialFunction ? `Изменение: ${initialFunction.title}`.slice(0, 180) : '',
          effective_date: localToday(),
          sections: [
            {
              ...blankSection(
                initialFunction?.department_id ?? options.author_departments[0] ?? '',
              ),
              title: initialFunction?.title ?? '',
              items: [
                {
                  ...blankItem(),
                  target_id: initialFunction?.id ?? null,
                  before: initialFunction
                    ? `${initialFunction.code} · ${initialFunction.title}`
                    : '',
                  source_anchor_ids: initialFunction ? [initialFunction.source_anchor_id] : [],
                },
              ],
            },
          ],
        },
  )
  const [reason, setReason] = useState(''),
    command = useCommand(),
    headers = useKey()
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (detail) {
      const body = { ...draft, expected_version: detail.version, change_reason: reason }
      await command.run(
        () =>
          result(
            client.POST('/api/v1/changes/{proposal_id}/versions', {
              params: { path: { proposal_id: detail.id }, header: headers(body) },
              body,
            }),
          ),
        () => saved(detail.id),
      )
    } else {
      const body = draft
      await command.run(
        async () => {
          const value = await result(
            client.POST('/api/v1/changes', { params: { header: headers(body) }, body }),
          )
          saved(value.id)
        },
        () => undefined,
      )
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !command.pending) close()
      }}
    >
      <DialogContent className="change-dialog">
        <DialogHeader>
          <DialogTitle>
            {detail
              ? `Новая версия предложения · после v${detail.current_version}`
              : 'Новое предложение'}
          </DialogTitle>
          <DialogDescription>
            Проект хранится отдельно от действующего реестра. Сохранение черновика не отправляет его
            участникам.
          </DialogDescription>
        </DialogHeader>
        <form className="change-form" onSubmit={submit} aria-busy={command.pending}>
          <div className="change-field-pair">
            <label className="change-field">
              Название предложения
              <Input
                required
                minLength={3}
                maxLength={180}
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            </label>
            <label className="change-field">
              Проектная дата
              <Input
                required
                type="date"
                value={draft.effective_date}
                onChange={(e) => setDraft({ ...draft, effective_date: e.target.value })}
              />
            </label>
          </div>
          {detail && (
            <>
              <label className="change-field">
                Причина новой версии
                <textarea
                  required
                  minLength={3}
                  maxLength={2000}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <div className="change-notice">
                <Info size={16} />
                Ответы и решения предыдущих версий сохранятся. Новую версию необходимо направить на
                согласование заново.
              </div>
            </>
          )}
          {draft.sections.map((section, i) => (
            <SectionEditor
              key={i}
              section={section}
              index={i}
              options={options}
              onChange={(value) =>
                setDraft({
                  ...draft,
                  sections: draft.sections.map((s, j) => (i === j ? value : s)),
                })
              }
              onRemove={
                draft.sections.length > 1
                  ? () => setDraft({ ...draft, sections: draft.sections.filter((_, j) => i !== j) })
                  : undefined
              }
            />
          ))}
          <Button
            type="button"
            variant="outline"
            disabled={draft.sections.length >= 12}
            onClick={() =>
              setDraft({
                ...draft,
                sections: [
                  ...draft.sections,
                  blankSection(
                    options.author_departments.find(
                      (id) => !draft.sections.some((s) => s.department_id === id),
                    ) ?? '',
                  ),
                ],
              })
            }
          >
            <Plus size={15} />
            Добавить подразделение
          </Button>
          <CommandFeedback command={command} refresh={refresh} />
          <div className="change-actions">
            <Button type="button" variant="outline" disabled={command.pending} onClick={close}>
              Отмена
            </Button>
            <Button disabled={command.pending}>
              {command.pending
                ? 'Сохраняем…'
                : detail
                  ? 'Сохранить новую версию'
                  : 'Сохранить черновик'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function ExecutionForm({
  detail,
  version,
  refresh,
}: {
  detail: Detail
  version: Version
  refresh: () => void
}) {
  const available = version.sections.filter((s) => s.can_execute)
  const [department, setDepartment] = useState(available[0]?.department_id ?? '')
  const [item, setItem] = useState(available[0]?.items[0]?.key ?? '')
  const [status, setStatus] = useState<S['ProposalExecution']['status']>('planned')
  const [date, setDate] = useState(version.effective_date),
    [note, setNote] = useState('')
  const [source, setSource] = useState(''),
    [eventId, setEventId] = useState(''),
    [retry, setRetry] = useState(0)
  const command = useCommand(),
    headers = useKey()
  const context = useRemote(`execution-context:${department}:${retry}`, (signal) =>
    department
      ? result(
          client.GET('/api/v1/changes/context', {
            params: { query: { department_id: department } },
            signal,
          }),
        )
      : Promise.resolve(null),
  )
  const executionScope = available
    .map((section) => `${section.department_id}:${section.items[0]?.key ?? ''}`)
    .join(',')
  useEffect(() => {
    const entries = executionScope ? executionScope.split(',').map((value) => value.split(':')) : []
    if (!entries.some(([id]) => id === department)) {
      setDepartment(entries[0]?.[0] ?? '')
      setItem(entries[0]?.[1] ?? '')
      setSource('')
      setEventId('')
    }
  }, [department, executionScope])
  if (!available.length || version.number !== detail.current_version) return null
  async function submit(event: FormEvent) {
    event.preventDefault()
    const body = {
      expected_version: detail.version,
      department_id: department,
      item_key: item,
      status,
      effective_date: date,
      note,
      source_anchor_id: source || null,
      personnel_event_id: eventId || null,
    }
    await command.run(
      () =>
        result(
          client.POST('/api/v1/changes/{proposal_id}/execution', {
            params: { path: { proposal_id: detail.id }, header: headers(body) },
            body,
          }),
        ),
      () => {
        setNote('')
        refresh()
      },
    )
  }
  return (
    <section className="change-review-panel">
      <h3>Регистрация исполнения</h3>
      <form className="change-form" onSubmit={submit} aria-busy={command.pending}>
        <div className="change-field-pair">
          <label className="change-field">
            Подразделение исполнения
            <select
              value={department}
              onChange={(e) => {
                setDepartment(e.target.value)
                setItem(
                  available.find((s) => s.department_id === e.target.value)?.items[0]?.key ?? '',
                )
                setSource('')
                setEventId('')
              }}
            >
              {available.map((s) => (
                <option key={s.department_id} value={s.department_id}>
                  {s.department_name}
                </option>
              ))}
            </select>
          </label>
          <label className="change-field">
            Пункт исполнения
            <select value={item} onChange={(e) => setItem(e.target.value)}>
              {available
                .find((s) => s.department_id === department)
                ?.items.map((i, n) => (
                  <option key={i.key} value={i.key}>
                    {n + 1}. {kinds[i.kind]} · {i.after.slice(0, 80)}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <div className="change-field-pair">
          <label className="change-field">
            Состояние исполнения
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as S['ProposalExecution']['status'])
                if (e.target.value === 'planned') {
                  setSource('')
                  setEventId('')
                }
              }}
            >
              <option value="planned">Запланировать исполнение</option>
              <option value="completed">Подтвердить выполненное действие</option>
            </select>
          </label>
          <label className="change-field">
            Дата действия
            <Input type="date" required value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
        </div>
        <label className="change-field">
          Что выполнено или запланировано
          <textarea
            required
            minLength={3}
            maxLength={3000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <LoadState
          loading={context.loading}
          error={context.error}
          onRetry={() => setRetry((x) => x + 1)}
        />
        <div className="change-field-pair">
          <label className="change-field">
            Документ-основание исполнения
            <select
              value={source}
              disabled={status === 'planned'}
              onChange={(e) => setSource(e.target.value)}
            >
              <option value="">Выберите основание</option>
              {context.data?.sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="change-field">
            Связанное кадровое событие
            <select
              value={eventId}
              disabled={status === 'planned'}
              onChange={(e) => setEventId(e.target.value)}
            >
              <option value="">Не связано с кадровым событием</option>
              {context.data?.personnel_events.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="change-notice">
          <Info size={16} />
          <span>
            Здесь фиксируются исполнение, основание и ответственный. Эта запись сама не изменяет
            реестр функций, штат или назначения. Кадровое действие сначала регистрируется в разделе
            «Кадровые события» и связывается с предложением.
          </span>
        </div>
        <CommandFeedback command={command} refresh={refresh} />
        <div className="change-actions">
          <Button
            disabled={
              command.pending ||
              !item ||
              (status === 'completed' &&
                (!source ||
                  (available
                    .find((s) => s.department_id === department)
                    ?.items.find((i) => i.key === item)?.kind === 'temporary_substitution' &&
                    !eventId)))
            }
          >
            {command.pending
              ? 'Сохраняем…'
              : status === 'planned'
                ? 'Сохранить план исполнения'
                : 'Зарегистрировать исполнение'}
          </Button>
        </div>
      </form>
    </section>
  )
}

function VerifyForm({ detail, refresh }: { detail: Detail; refresh: () => void }) {
  const [note, setNote] = useState(''),
    command = useCommand(),
    headers = useKey()
  if (!detail.permissions.verify) return null
  async function submit(event: FormEvent) {
    event.preventDefault()
    const body = { expected_version: detail.version, note }
    await command.run(
      () =>
        result(
          client.POST('/api/v1/changes/{proposal_id}/verify', {
            params: { path: { proposal_id: detail.id }, header: headers(body) },
            body,
          }),
        ),
      () => {
        setNote('')
        refresh()
      },
    )
  }
  return (
    <form className="change-form change-review-panel" onSubmit={submit} aria-busy={command.pending}>
      <h3>Проверка аудитора</h3>
      <label className="change-field">
        Заключение о проверке исполнения
        <textarea
          required
          minLength={3}
          maxLength={3000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <p className="change-form-description">
        Подтвердите фактическое исполнение по основаниям и связанным событиям. Завершение процедуры
        не заменяет профессиональную оценку аудитора.
      </p>
      <CommandFeedback command={command} refresh={refresh} />
      <Button disabled={command.pending}>
        {command.pending ? 'Сохраняем…' : 'Подтвердить проверку исполнения'}
      </Button>
    </form>
  )
}

function CancelDialog({
  detail,
  close,
  refresh,
}: {
  detail: Detail
  close: () => void
  refresh: () => void
}) {
  const [note, setNote] = useState(''),
    command = useCommand(),
    headers = useKey()
  async function submit(event: FormEvent) {
    event.preventDefault()
    const body = { expected_version: detail.version, note }
    await command.run(
      () =>
        result(
          client.POST('/api/v1/changes/{proposal_id}/cancel', {
            params: { path: { proposal_id: detail.id }, header: headers(body) },
            body,
          }),
        ),
      () => {
        close()
        refresh()
      },
    )
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !command.pending) close()
      }}
    >
      <DialogContent className="change-dialog change-dialog-small">
        <DialogHeader>
          <DialogTitle>Отменить предложение</DialogTitle>
          <DialogDescription>
            Все версии, основания, переписка и решения сохранятся в истории. Отмена доступна до
            регистрации фактического исполнения.
          </DialogDescription>
        </DialogHeader>
        <form className="change-form" onSubmit={submit} aria-busy={command.pending}>
          <label className="change-field">
            Причина отмены
            <textarea
              required
              minLength={3}
              maxLength={3000}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <CommandFeedback command={command} refresh={refresh} />
          <div className="change-actions">
            <Button type="button" variant="outline" onClick={close} disabled={command.pending}>
              Продолжить работу
            </Button>
            <Button variant="destructive" disabled={command.pending}>
              {command.pending ? 'Сохраняем…' : 'Подтвердить отмену предложения'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function ReminderForm({
  detail,
  version,
  options,
  refresh,
}: {
  detail: Detail
  version: Version
  options: Options
  refresh: () => void
}) {
  const [kind, setKind] = useState<S['ProposalReminder']['kind']>('reminder'),
    [recipient, setRecipient] = useState(''),
    [note, setNote] = useState('')
  const command = useCommand(),
    headers = useKey()
  const sent = detail.process_entries.filter(
    (e) =>
      e.kind === 'send' && e.version_number === version.number && e.recipient_id && e.department_id,
  )
  const choices =
    kind === 'reminder'
      ? sent
          .filter(
            (e, i, all) =>
              all.findIndex(
                (v) => v.department_id === e.department_id && v.recipient_id === e.recipient_id,
              ) === i,
          )
          .map((e) => ({
            key: `${e.department_id}:${e.recipient_id}`,
            user_id: e.recipient_id!,
            department_id: e.department_id!,
            label: `${e.recipient_name ?? 'Участник'} · ${detail.departments.find((d) => d.id === e.department_id)?.label ?? ''}`,
          }))
      : options.participants
          .filter(
            (p) =>
              p.roles.includes('manager') &&
              version.sections.some((s) => s.department_id === p.department_id),
          )
          .map((p) => ({
            key: `${p.department_id}:${p.id}`,
            user_id: p.id,
            department_id: p.department_id,
            label: `${p.label} · ${options.departments.find((d) => d.id === p.department_id)?.label ?? ''}`,
          }))
  if (!detail.permissions.remind || version.number !== detail.current_version) return null
  async function submit(event: FormEvent) {
    event.preventDefault()
    const selected = choices.find((c) => c.key === recipient)
    if (!selected) return
    const body = {
      expected_version: detail.version,
      kind,
      recipient: { department_id: selected.department_id, user_id: selected.user_id },
      note,
    }
    await command.run(
      () =>
        result(
          client.POST('/api/v1/changes/{proposal_id}/reminders', {
            params: { path: { proposal_id: detail.id }, header: headers(body) },
            body,
          }),
        ),
      () => {
        setNote('')
        refresh()
      },
    )
  }
  return (
    <details className="change-review-panel">
      <summary className="small">Напоминание или эскалация</summary>
      <form
        onSubmit={submit}
        className="change-form"
        style={{ marginTop: 14 }}
        aria-busy={command.pending}
      >
        <label className="change-field">
          Действие со сроком
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as S['ProposalReminder']['kind'])
              setRecipient('')
            }}
          >
            <option value="reminder">Напомнить адресату</option>
            <option value="escalation">Передать уполномоченному руководителю</option>
          </select>
        </label>
        <label className="change-field">
          Получатель напоминания
          <select required value={recipient} onChange={(e) => setRecipient(e.target.value)}>
            <option value="">Выберите адресата</option>
            {choices.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label className="change-field">
          Основание напоминания
          <textarea
            required
            minLength={3}
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <p className="change-form-description">
          Напоминания отправляются явно, с ограничением частоты на сервере. Повтор запроса не
          создаёт новую доставку.
        </p>
        <CommandFeedback command={command} refresh={refresh} />
        <Button disabled={command.pending || !recipient}>
          {command.pending
            ? 'Отправляем…'
            : kind === 'reminder'
              ? 'Отправить напоминание'
              : 'Передать руководителю'}
        </Button>
      </form>
    </details>
  )
}

function EvidenceView({
  version,
  detail,
  onSource,
}: {
  version: Version
  detail: Detail
  onSource: (id: string) => void
}) {
  return (
    <div className="change-stack">
      <div className="change-notice">
        <Info size={16} />
        <span>
          Сопоставление действующего и предлагаемого состояния на{' '}
          {dateLabel(version.effective_date)}. Предлагаемый текст — проект. Утверждение и
          фактическое исполнение учитываются отдельно.
        </span>
      </div>
      {version.sections.map((section) => (
        <section key={section.department_id} className="change-stack">
          <div className="change-section-heading">
            <div>
              <h3>{section.title}</h3>
              <p className="small muted">{section.department_name}</p>
            </div>
          </div>
          <p className="change-prose">{section.rationale}</p>
          {section.items.map((item, i) => (
            <article key={item.key} className="change-item">
              <header>
                <div>
                  <h3>
                    {i + 1}. {kinds[item.kind]}
                  </h3>
                  <p>{section.department_name}</p>
                </div>
              </header>
              <div className="change-before-after">
                <section>
                  <h4>Действующее / до изменения</h4>
                  <p className="change-prose">{item.before}</p>
                </section>
                <section>
                  <h4>Предлагаемое / после изменения</h4>
                  <p className="change-prose">{item.after}</p>
                </section>
              </div>
              <div className="change-item-footer">
                <h4>Основания и последствия</h4>
                {item.requirements && <p className="change-prose">{item.requirements}</p>}
                <Sources ids={item.source_anchor_ids ?? []} onSource={onSource} />
                {item.impacts.length > 0 && (
                  <ul className="change-impact-list">
                    {item.impacts.map((impact, n) => (
                      <li key={n}>{impact}</li>
                    ))}
                  </ul>
                )}
                {item.candidate_employee_id && (
                  <div className="change-notice" style={{ marginTop: 12 }}>
                    <Info size={15} />
                    <div>
                      <strong>Предложена кандидатура для рассмотрения</strong>
                      <p className="change-prose">
                        {item.qualification_basis || 'Обоснование квалификации не указано.'}
                      </p>
                      <Sources ids={item.qualification_source_ids ?? []} onSource={onSource} />
                      <p className="small muted">
                        Назначение подтверждается человеком и официальным основанием.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </article>
          ))}
        </section>
      ))}
      {detail.limitations.length > 0 && (
        <details>
          <summary className="small muted">Условия и ограничения</summary>
          <ul className="change-impact-list">
            {detail.limitations.map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

function DeliveryRetry({
  detail,
  entry,
  refresh,
}: {
  detail: Detail
  entry: Entry
  refresh: () => void
}) {
  const command = useCommand(),
    headers = useKey()
  if (!entry.can_retry_delivery || !entry.notification_id) return null
  async function retry() {
    if (!entry.notification_id) return
    const body = { expected_version: detail.version, notification_id: entry.notification_id }
    await command.run(
      () =>
        result(
          client.POST('/api/v1/changes/{proposal_id}/retry-delivery', {
            params: { path: { proposal_id: detail.id }, header: headers(body) },
            body,
          }),
        ),
      refresh,
    )
  }
  return (
    <div className="change-stack" style={{ marginTop: 8 }}>
      <Button type="button" size="sm" variant="outline" disabled={command.pending} onClick={retry}>
        {command.pending ? 'Ставим в очередь…' : 'Повторить доставку'}
      </Button>
      <CommandFeedback command={command} refresh={refresh} />
    </div>
  )
}

function ApprovalView({
  detail,
  version,
  user,
  options,
  onSource,
  refresh,
}: {
  detail: Detail
  version: Version
  user: User
  options: Options
  onSource: (id: string) => void
  refresh: () => void
}) {
  const reviewEntries =
    version.number === detail.current_version ? detail.process_entries : detail.entries
  const sent = reviewEntries.filter((e) => e.kind === 'send' && e.version_number === version.number)
  const reviews = reviewEntries.filter(
    (e) => ['review', 'decision'].includes(e.kind) && e.version_number === version.number,
  )
  return (
    <div className="change-stack">
      <div className="change-notice">
        <Info size={16} />
        <span>
          Доставка, чтение уведомления и ответ — разные события. Согласие подразделения не означает
          утверждение руководителем. Ответы относятся только к указанной версии.
        </span>
      </div>
      <div className="change-review-grid">
        <ReviewForm detail={detail} version={version} refresh={refresh} />
        <DecisionForm detail={detail} version={version} refresh={refresh} />
      </div>
      <div className="change-review-grid">
        <section className="change-review-panel">
          <h3>Адресаты и сроки · версия {version.number}</h3>
          {sent.length ? (
            sent.map((e) => (
              <article key={e.id} className="change-delivery">
                <strong>{e.recipient_name ?? 'Участник'}</strong>
                <p>{detail.departments.find((d) => d.id === e.department_id)?.label}</p>
                <p>
                  Срок: {e.due_at ? stamp(e.due_at, user) : 'не установлен'}{' '}
                  {e.overdue && (
                    <span className="change-status" data-tone="attention">
                      Просрочено
                    </span>
                  )}
                </p>
                <p>
                  Доставка:{' '}
                  {e.delivered_at
                    ? stamp(e.delivered_at, user)
                    : e.delivery_status === 'failed'
                      ? 'ошибка доставки'
                      : e.delivery_status === 'cancelled'
                        ? 'отменена'
                        : e.delivery_status === 'blocked'
                          ? 'заблокирована: изменились права адресата'
                          : 'ожидается'}
                  <br />
                  Уведомление прочитано: {e.read_at ? stamp(e.read_at, user) : 'не зафиксировано'}
                  <br />
                  Ответ: {e.replied_at ? stamp(e.replied_at, user) : 'не получен'}
                </p>
                <DeliveryRetry detail={detail} entry={e} refresh={refresh} />
              </article>
            ))
          ) : (
            <p className="small muted">Эта версия ещё не направлена участникам.</p>
          )}
        </section>
        <section className="change-review-panel">
          <h3>Ответы и решения</h3>
          {reviews.length ? (
            reviews.map((e) => (
              <EntryCard
                key={e.id}
                entry={e}
                user={user}
                departments={detail.departments}
                onSource={onSource}
              />
            ))
          ) : (
            <p className="small muted">Ответов на эту версию пока нет.</p>
          )}
        </section>
      </div>
      <ReminderForm detail={detail} version={version} options={options} refresh={refresh} />
    </div>
  )
}

function ProposalDetailView({
  proposalId,
  options,
  user,
  revision,
  onBack,
  onSource,
  onChanged,
}: {
  proposalId: string
  options: Options
  user: User
  revision: number
  onBack: () => void
  onSource: (id: string) => void
  onChanged: () => void
}) {
  const [detail, setDetail] = useState<Detail | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState('')
  const [retry, setRetry] = useState(0),
    [selectedVersion, setSelectedVersion] = useState(0),
    [tab, setTab] = useState('proposal')
  const [editing, setEditing] = useState(false),
    [sending, setSending] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [visited, setVisited] = useState(() => new Set(['proposal']))
  function selectTab(value: string) {
    setVisited((previous) => new Set(previous).add(value))
    setTab(value)
  }
  const [historyOffset, setHistoryOffset] = useState(0),
    [versionsOffset, setVersionsOffset] = useState(0)
  // Refresh the same object in place so a conflict does not erase an unsent form.
  // The parent keys this component by user and proposal. A failed access check clears all data.
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    result(
      client.GET('/api/v1/changes/{proposal_id}', {
        params: {
          path: { proposal_id: proposalId },
          query: {
            history_offset: historyOffset,
            history_limit: 100,
            versions_offset: versionsOffset,
            versions_limit: 20,
          },
        },
        signal: controller.signal,
      }),
    )
      .then((value) => {
        if (!controller.signal.aborted) {
          setDetail(value)
          setLoading(false)
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setDetail(null)
          setError(errorMessage(e))
          setLoading(false)
        }
      })
    return () => controller.abort()
  }, [proposalId, user.id, revision, retry, historyOffset, versionsOffset])
  const refresh = () => {
    setRetry((x) => x + 1)
    onChanged()
  }
  const version =
    detail?.versions.find((v) => v.number === (selectedVersion || detail.current_version)) ??
    detail?.versions.at(-1)
  const current = version?.number === detail?.current_version
  const panelScope = version
    ? `${version.number}:${version.sections
        .map((section) => section.department_id)
        .sort()
        .join(',')}:${detail?.permissions.private_note ? 'private' : 'shared'}`
    : ''
  const processEntries = current ? (detail?.process_entries ?? []) : (detail?.entries ?? [])
  const historyPager =
    detail && detail.history_total > detail.history_limit ? (
      <div aria-label="Страницы событий предложения">
        <p className="small muted">
          История загружается от последних событий к более ранним. Общие сообщения и личные заметки
          показываются раздельно.
        </p>
        <Pagination
          total={detail.history_total}
          offset={detail.history_offset}
          limit={detail.history_limit}
          onChange={setHistoryOffset}
        />
      </div>
    ) : null
  return (
    <section className="content-panel change-workspace">
      <div>
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft size={15} />
          Все предложения
        </Button>
      </div>
      {(!detail || loading) && (
        <LoadState loading={loading} error={error} onRetry={() => setRetry((x) => x + 1)} />
      )}
      {detail && version && (
        <div aria-busy={loading}>
          <div className="change-detail-heading">
            <div>
              <h2>{version.title}</h2>
              <div className="change-meta">
                <Status state={detail.state} />
                <span>Автор: {detail.owner_name}</span>
                <span>Обновлено {stamp(detail.updated_at, user)}</span>
              </div>
            </div>
            <div className="change-detail-actions">
              {detail.permissions.revise && current && (
                <Button variant="outline" onClick={() => setEditing(true)}>
                  <Plus size={15} />
                  Новая версия
                </Button>
              )}
              {detail.permissions.send && current && (
                <Button onClick={() => setSending(true)}>
                  <Send size={15} />
                  Направить на согласование
                </Button>
              )}
              {detail.permissions.cancel && current && (
                <Button variant="ghost" size="sm" onClick={() => setCancelling(true)}>
                  Отменить предложение
                </Button>
              )}
            </div>
          </div>
          {detail.partial_visibility && (
            <div className="change-notice">
              <LockKeyhole size={16} />
              Показаны только разделы, документы и сообщения в пределах ваших текущих прав. Другие
              разделы проекта скрыты.
            </div>
          )}
          <div className="change-version-bar">
            <label>
              Версия предложения
              <select
                value={version.number}
                onChange={(e) => {
                  setSelectedVersion(Number(e.target.value))
                  selectTab('proposal')
                }}
              >
                {detail.versions.map((v) => (
                  <option key={v.id} value={v.number}>
                    v{v.number}
                    {v.number === detail.current_version ? ' · последняя доступная' : ' · история'}
                  </option>
                ))}
              </select>
            </label>
            <span className="small muted">Проектная дата: {dateLabel(version.effective_date)}</span>
          </div>
          {!current && (
            <div className="change-notice">
              <History size={16} />
              Просмотр предыдущей версии. Ответы и решения этой версии сохранены в истории и не
              считаются согласованием текущей версии.
            </div>
          )}
          <Tabs key={panelScope} className="change-tabs" value={tab} onValueChange={selectTab}>
            <TabsList variant="line">
              <TabsTrigger value="proposal">
                <FileText size={15} />
                Предложение
              </TabsTrigger>
              <TabsTrigger value="discussion">
                <MessageSquare size={15} />
                Обсуждение
              </TabsTrigger>
              <TabsTrigger value="approval">
                <CheckCheck size={15} />
                Согласование
              </TabsTrigger>
              <TabsTrigger value="execution">Исполнение</TabsTrigger>
              <TabsTrigger value="history">
                <History size={15} />
                История
              </TabsTrigger>
              {detail.permissions.private_note && (
                <TabsTrigger value="notes">
                  <LockKeyhole size={15} />
                  Личные заметки
                </TabsTrigger>
              )}
            </TabsList>
            <TabsContent value="proposal" forceMount hidden={tab !== 'proposal'}>
              <EvidenceView detail={detail} version={version} onSource={onSource} />
            </TabsContent>
            <TabsContent value="discussion" forceMount hidden={tab !== 'discussion'}>
              {visited.has('discussion') && (
                <Discussion
                  key={`discussion:${version.number}`}
                  detail={detail}
                  version={version}
                  user={user}
                  onSource={onSource}
                  refresh={refresh}
                />
              )}
              {historyPager}
            </TabsContent>
            <TabsContent value="approval" forceMount hidden={tab !== 'approval'}>
              {visited.has('approval') && (
                <ApprovalView
                  key={`approval:${version.number}`}
                  detail={detail}
                  version={version}
                  user={user}
                  options={options}
                  onSource={onSource}
                  refresh={refresh}
                />
              )}
              {!current && historyPager}
            </TabsContent>
            <TabsContent value="execution" forceMount hidden={tab !== 'execution'}>
              {visited.has('execution') && (
                <div className="change-stack">
                  <div className="change-notice">
                    <Info size={16} />
                    Утверждение не применяет изменение автоматически. Для каждого пункта отдельно
                    сохраняются план, факт исполнения, дата, исполнитель и документальное основание.
                  </div>
                  {processEntries
                    .filter(
                      (e) =>
                        ['execution', 'verified', 'verify'].includes(e.kind) &&
                        e.version_number === version.number,
                    )
                    .map((e) => (
                      <EntryCard
                        key={e.id}
                        entry={e}
                        user={user}
                        departments={detail.departments}
                        onSource={onSource}
                      />
                    ))}
                  <ExecutionForm
                    key={`execution:${version.number}`}
                    detail={detail}
                    version={version}
                    refresh={refresh}
                  />
                  {current && <VerifyForm detail={detail} refresh={refresh} />}
                  {!version.sections.some((s) => s.can_execute) &&
                    !processEntries.some(
                      (e) => e.kind === 'execution' && e.version_number === version.number,
                    ) && (
                      <Empty title="Исполнение пока не зарегистрировано">
                        План и фактическое исполнение регистрируются уполномоченными участниками
                        после утверждения предложения.
                      </Empty>
                    )}
                </div>
              )}
            </TabsContent>
            <TabsContent value="history" forceMount hidden={tab !== 'history'}>
              <div className="change-stack">
                <h3>Неизменяемые версии</h3>
                {detail.versions.map((v) => (
                  <div key={v.id} className="change-version-bar">
                    <div>
                      <strong className="small">
                        Версия {v.number} · {v.title}
                      </strong>
                      <p className="small muted">
                        {stamp(v.created_at, user)} ·{' '}
                        {v.change_reason || 'Первоначальное предложение'}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setSelectedVersion(v.number)
                        selectTab('proposal')
                      }}
                    >
                      Открыть v{v.number}
                      <ArrowRight size={14} />
                    </Button>
                  </div>
                ))}
                {detail.versions_total > detail.versions_limit && (
                  <div aria-label="Страницы версий предложения">
                    <Pagination
                      total={detail.versions_total}
                      offset={detail.versions_offset}
                      limit={detail.versions_limit}
                      onChange={(value) => {
                        setSelectedVersion(0)
                        setVersionsOffset(value)
                      }}
                    />
                  </div>
                )}
                <h3>События и решения</h3>
                <ol className="change-history">
                  {detail.entries
                    .filter((e) => e.kind !== 'private_note')
                    .map((e) => (
                      <li key={e.id}>
                        <time dateTime={e.created_at}>{stamp(e.created_at, user)}</time>
                        <div>
                          <strong>
                            {entryLabels[e.kind] ?? 'Событие предложения'} · v{e.version_number} ·{' '}
                            {e.actor_name}
                          </strong>
                          <p>
                            {e.outcome ? `${outcomes[e.outcome] ?? e.outcome}. ` : ''}
                            {e.text}
                          </p>
                          {e.recipient_name && <p>Адресат: {e.recipient_name}</p>}
                          <Sources ids={e.source_anchor_ids} onSource={onSource} />
                        </div>
                      </li>
                    ))}
                </ol>
                {historyPager}
              </div>
            </TabsContent>
            {detail.permissions.private_note && (
              <TabsContent value="notes" forceMount hidden={tab !== 'notes'}>
                {visited.has('notes') && (
                  <Discussion
                    key={`notes:${version.number}`}
                    privateMode
                    detail={detail}
                    version={version}
                    user={user}
                    onSource={onSource}
                    refresh={refresh}
                  />
                )}
                {historyPager}
              </TabsContent>
            )}
          </Tabs>
        </div>
      )}
      {editing && detail && (
        <ProposalEditor
          options={options}
          detail={detail}
          close={() => setEditing(false)}
          saved={() => {
            setEditing(false)
            setSelectedVersion(0)
            refresh()
          }}
          refresh={refresh}
        />
      )}
      {sending && detail && version && (
        <SendDialog
          detail={detail}
          options={options}
          version={version}
          user={user}
          close={() => setSending(false)}
          refresh={refresh}
        />
      )}
      {cancelling && detail && (
        <CancelDialog detail={detail} close={() => setCancelling(false)} refresh={refresh} />
      )}
    </section>
  )
}

export function ChangeWorkspace({
  data,
  user,
  onSource,
  revision,
  initialFunction,
  onInitialFunctionHandled,
  initialProposalId,
  onInitialProposalHandled,
}: Props) {
  const [q, setQ] = useState(''),
    [state, setState] = useState<State | ''>(''),
    [department, setDepartment] = useState('')
  const [offset, setOffset] = useState(0),
    [retry, setRetry] = useState(0),
    [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false),
    [seed, setSeed] = useState<FunctionItem | null>(null)
  const options = useRemote(`change-options:${user.id}:${revision}:${retry}`, (signal) =>
    result(client.GET('/api/v1/changes/options', { signal })),
  )
  const list = useRemote(
    `changes:${user.id}:${revision}:${retry}:${q}:${state}:${department}:${offset}`,
    (signal) =>
      result(
        client.GET('/api/v1/changes', {
          params: {
            query: {
              q,
              state: state || undefined,
              department_id: department || undefined,
              limit: 15,
              offset,
            },
          },
          signal,
        }),
      ),
  )
  useEffect(() => {
    if (initialFunction) {
      setSeed(initialFunction)
      setCreating(true)
      setSelected(null)
      onInitialFunctionHandled?.()
    }
  }, [initialFunction, onInitialFunctionHandled])
  useEffect(() => {
    if (initialProposalId) {
      setSelected(initialProposalId)
      setCreating(false)
      onInitialProposalHandled?.()
    }
  }, [initialProposalId, onInitialProposalHandled])
  const refresh = () => setRetry((v) => v + 1)
  const close = () => {
    setCreating(false)
    setSeed(null)
  }
  return (
    <div className="change-workspace">
      <LoadState loading={options.loading} error={options.error} onRetry={refresh} />
      {options.data &&
        (selected ? (
          <ProposalDetailView
            key={`${user.id}:${selected}`}
            proposalId={selected}
            user={user}
            options={options.data}
            revision={revision}
            onBack={() => {
              setSelected(null)
              refresh()
            }}
            onSource={onSource}
            onChanged={() => undefined}
          />
        ) : (
          <section className="content-panel">
            <div className="change-heading">
              <div>
                <h2>Предложения изменений</h2>
                <p>Версии проекта, ответы подразделений, решения и исполнение.</p>
              </div>
              {options.data.can_create && (
                <Button
                  onClick={() => {
                    setSeed(null)
                    setCreating(true)
                  }}
                >
                  <Plus size={16} />
                  Новое предложение
                </Button>
              )}
            </div>
            <div className="change-toolbar">
              <SearchField
                label="Найти предложение"
                value={q}
                onChange={(value) => {
                  setQ(value)
                  setOffset(0)
                }}
              />
              <select
                aria-label="Состояние предложения"
                value={state}
                onChange={(e) => {
                  setState(e.target.value as State | '')
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
              <select
                aria-label="Подразделение предложения"
                value={department}
                onChange={(e) => {
                  setDepartment(e.target.value)
                  setOffset(0)
                }}
              >
                <option value="">Все доступные отделы</option>
                {options.data.departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.label}
                  </option>
                ))}
              </select>
            </div>
            <LoadState loading={list.loading} error={list.error} onRetry={refresh} />
            {list.data &&
              (list.data.items.length ? (
                <>
                  <div className="change-table-wrap">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Предложение</TableHead>
                          <TableHead>Состояние</TableHead>
                          <TableHead>Проектная дата</TableHead>
                          <TableHead>Обновлено</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {list.data.items.map((item) => (
                          <TableRow key={item.id}>
                            <TableCell>
                              <button
                                type="button"
                                className="change-row-title"
                                onClick={() => setSelected(item.id)}
                              >
                                {item.title}
                              </button>
                              <p className="change-row-subtitle">
                                v{item.current_version} ·{' '}
                                {item.departments.map((d) => d.label).join(', ')}
                                {item.partial_visibility ? ' · доступная часть проекта' : ''}
                              </p>
                            </TableCell>
                            <TableCell>
                              <Status state={item.state} />
                              {item.overdue && (
                                <p
                                  className="small"
                                  style={{ color: 'var(--warning-text)', marginTop: 5 }}
                                >
                                  Срок ответа истёк
                                </p>
                              )}
                            </TableCell>
                            <TableCell>{dateLabel(item.effective_date)}</TableCell>
                            <TableCell>{stamp(item.updated_at, user)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                  <Pagination
                    total={list.data.total}
                    offset={offset}
                    limit={15}
                    onChange={setOffset}
                  />
                </>
              ) : (
                <Empty
                  title={
                    q || state || department
                      ? 'Предложений по этим условиям нет'
                      : 'Предложений пока нет'
                  }
                >
                  {q || state || department
                    ? 'Измените поиск или фильтры. Здесь показываются только доступные вам предложения.'
                    : `Создайте предложение для ${workspaceName(data.organization)}, укажите состояние до и после изменения и документальные основания. Отправка участникам выполняется отдельно.`}
                </Empty>
              ))}
          </section>
        ))}
      {creating && options.data && options.data.can_create && (
        <ProposalEditor
          options={options.data}
          initialFunction={seed}
          close={close}
          saved={(id) => {
            close()
            setSelected(id)
          }}
          refresh={refresh}
        />
      )}
    </div>
  )
}
