import { useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Plus, RefreshCw } from 'lucide-react'
import { client, result, errorMessage, ApiError } from '@/lib/client'
import { useRemote } from '@/lib/useRemote'
import { auditLabel } from '@/lib/auditLabels'
import type { components } from '@/lib/schema.generated'
import type { User } from '@/lib/types'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs'
import { LoadState, Pagination, SearchField } from './ListControls'
import { Reauthenticate } from './Reauthenticate'
import '@/access.css'

type S = components['schemas']
type Person = S['AccessUserDetail']
type Permission = S['PermissionRequestView']
type Role = S['PermissionRequestCreate']['role']
const roleNames: Record<string, string> = {
  department: 'Представитель отдела',
  hr: 'Кадровый специалист',
  auditor: 'Аудитор',
  reviewer: 'Рецензент аудита',
  manager: 'Руководитель',
  access_admin: 'Администратор доступа',
}
const states: Record<string, string> = {
  pending: 'Ожидает подтверждения',
  approved: 'Назначено',
  rejected: 'Отклонено',
  cancelled: 'Отменено',
  accepted: 'Активировано',
  expired: 'Срок истёк',
  active: 'Действует',
  scheduled: 'Начнёт действовать',
  revoked: 'Отозвано',
}
const date = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString('ru-RU') : 'Без установленного окончания'
const localTime = (value: Date) =>
  new Date(value.getTime() - value.getTimezoneOffset() * 60000).toISOString().slice(0, 16)

function useCommand() {
  const [pending, setPending] = useState(false),
    [error, setError] = useState(''),
    [reauth, setReauth] = useState(false)
  const ref = useRef({ payload: '', key: '' })
  const headers = (name: string, body: unknown) => {
    const payload = JSON.stringify([name, body])
    if (ref.current.payload !== payload) ref.current = { payload, key: crypto.randomUUID() }
    return { 'idempotency-key': ref.current.key }
  }
  async function run(task: () => Promise<unknown>, success: () => void) {
    if (pending) return
    setPending(true)
    setError('')
    try {
      await task()
      setReauth(false)
      success()
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
    reauth,
    headers,
    run,
    confirm: () => {
      setReauth(false)
      setError('Вход подтверждён. Повторите действие.')
    },
  }
}
function Feedback({ command }: { command: ReturnType<typeof useCommand> }) {
  return (
    <>
      {command.error && (
        <p role="alert" className="error-box">
          {command.error}
        </p>
      )}
      {command.reauth && <Reauthenticate onDone={command.confirm} />}
    </>
  )
}

function InviteForm({ close, done }: { close: () => void; done: () => void }) {
  const [username, setUsername] = useState(''),
    [display, setDisplay] = useState(''),
    [basis, setBasis] = useState(''),
    [hours, setHours] = useState(24)
  const command = useCommand()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const body = { username, display_name: display, basis, expires_hours: hours }
    void command.run(
      () =>
        result(
          client.POST('/api/v1/access/invitations', {
            body,
            params: { header: command.headers('invite', body) },
          }),
        ),
      done,
    )
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !command.pending) close()
      }}
    >
      <DialogContent className="access-dialog">
        <DialogHeader>
          <DialogTitle>Пригласить пользователя</DialogTitle>
          <DialogDescription>
            Пользователь самостоятельно задаст пароль и подключит MFA. Права на отдел оформляются
            после активации отдельным назначением.
          </DialogDescription>
        </DialogHeader>
        <form className="access-form" onSubmit={submit}>
          <label>
            Логин нового пользователя
            <Input
              aria-label="Логин нового пользователя"
              required
              minLength={3}
              maxLength={80}
              pattern="[a-z0-9][a-z0-9_.-]+"
              autoComplete="off"
              value={username}
              onChange={(e) => setUsername(e.target.value.toLowerCase())}
            />
          </label>
          <label>
            Имя пользователя
            <Input
              aria-label="Имя пользователя"
              required
              minLength={3}
              maxLength={160}
              value={display}
              onChange={(e) => setDisplay(e.target.value)}
            />
          </label>
          <label>
            Основание приглашения
            <Input
              aria-label="Основание приглашения"
              required
              minLength={5}
              maxLength={240}
              value={basis}
              onChange={(e) => setBasis(e.target.value)}
            />
          </label>
          <label>
            Срок приглашения
            <select
              aria-label="Срок приглашения"
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
            >
              <option value={24}>24 часа</option>
              <option value={48}>48 часов</option>
              <option value={72}>72 часа</option>
            </select>
          </label>
          <Feedback command={command} />
          <Button disabled={command.pending}>Создать приглашение</Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function InvitationActions({
  item,
  refresh,
  localDelivery,
}: {
  item: S['InvitationView']
  refresh: () => void
  localDelivery: boolean
}) {
  const [token, setToken] = useState(''),
    [note, setNote] = useState(''),
    [cancelling, setCancelling] = useState(false)
  const command = useCommand()
  return (
    <div className="access-row-actions">
      {item.state === 'pending' && (
        <>
          <Button
            variant="outline"
            size="sm"
            disabled={!localDelivery || command.pending}
            onClick={() =>
              void command.run(
                async () => {
                  setToken('')
                  const value = await result(
                    client.POST('/api/v1/access/invitations/{invitation_id}/delivery', {
                      params: { path: { invitation_id: item.id } },
                    }),
                  )
                  setToken(value.token)
                },
                () => {},
              )
            }
          >
            Получить код приглашения
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={command.pending}
            onClick={() => setCancelling((v) => !v)}
          >
            Отменить приглашение
          </Button>
        </>
      )}
      {cancelling && item.state === 'pending' && (
        <form
          className="access-inline-form"
          onSubmit={(e) => {
            e.preventDefault()
            const body = { expected_version: item.version, note }
            void command.run(
              () =>
                result(
                  client.POST('/api/v1/access/invitations/{invitation_id}/cancel', {
                    body,
                    params: {
                      path: { invitation_id: item.id },
                      header: command.headers('cancel-invite', body),
                    },
                  }),
                ),
              refresh,
            )
          }}
        >
          <label>
            Причина отмены
            <Input
              aria-label={`Причина отмены ${item.username}`}
              required
              minLength={5}
              maxLength={240}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <Button disabled={command.pending}>Подтвердить отмену</Button>
        </form>
      )}
      {token && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) setToken('')
          }}
        >
          <DialogContent className="access-dialog">
            <DialogHeader>
              <DialogTitle>Учебная доставка приглашения</DialogTitle>
              <DialogDescription>
                Код для {item.display_name}, логин {item.username}. Действует до{' '}
                {date(item.expires_at)}. Передайте его владельцу аккаунта вручную. На странице входа
                он выбирает «У меня есть приглашение».
              </DialogDescription>
            </DialogHeader>
            <label>
              Код приглашения
              <Input aria-label="Код приглашения" autoComplete="off" readOnly value={token} />
            </label>
            <p className="muted small">
              Код даёт возможность создать личную учётную запись. Доступ к отделам требует
              отдельного подтверждения.
            </p>
            <Button onClick={() => setToken('')}>Закрыть код</Button>
          </DialogContent>
        </Dialog>
      )}
      <Feedback command={command} />
    </div>
  )
}

function Invitations({
  refresh,
  revision,
  localDelivery,
}: {
  refresh: () => void
  revision: number
  localDelivery: boolean
}) {
  const [offset, setOffset] = useState(0)
  const remote = useRemote(`access-invites:${offset}:${revision}`, (signal) =>
    result(
      client.GET('/api/v1/access/invitations', {
        params: { query: { offset, limit: 20 } },
        signal,
      }),
    ),
  )
  return (
    <>
      <p className="muted">
        Приглашение действует ограниченное время. После активации пользователь появится во вкладке
        «Пользователи».
      </p>
      <LoadState {...remote} onRetry={refresh} />
      {remote.data && (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Пользователь</TableHead>
                <TableHead>Состояние и срок</TableHead>
                <TableHead>Доставка и действия</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {remote.data.items.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>
                    <strong>{item.display_name}</strong>
                    <p>{item.username}</p>
                    <p className="small muted">{item.basis}</p>
                  </TableCell>
                  <TableCell>
                    {states[item.state]}
                    <p className="small muted">До {date(item.expires_at)}</p>
                  </TableCell>
                  <TableCell>
                    <InvitationActions
                      key={`${item.id}:${item.version}`}
                      item={item}
                      refresh={refresh}
                      localDelivery={localDelivery}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!remote.data.total && (
            <p className="access-empty">Приглашений пока нет. Нажмите «Пригласить пользователя».</p>
          )}
          <Pagination {...remote.data} onChange={setOffset} />
        </>
      )}
    </>
  )
}

function PermissionForm({
  person,
  departments,
  close,
  done,
}: {
  person: Person
  departments: S['AccessOptions']['departments']
  close: () => void
  done: () => void
}) {
  const [role, setRole] = useState<Role>('department'),
    [department, setDepartment] = useState(departments[0]?.id ?? '')
  const [start, setStart] = useState(localTime(new Date())),
    [end, setEnd] = useState(localTime(new Date(Date.now() + 30 * 86400000))),
    [basis, setBasis] = useState('')
  const command = useCommand()
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const body = {
      user_id: person.id,
      role,
      department_id: role === 'access_admin' ? null : department,
      valid_from: new Date(start).toISOString(),
      valid_until: new Date(end).toISOString(),
      basis,
    }
    void command.run(
      () =>
        result(
          client.POST('/api/v1/access/requests', {
            body,
            params: { header: command.headers('permission', body) },
          }),
        ),
      done,
    )
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !command.pending) close()
      }}
    >
      <DialogContent className="access-dialog">
        <DialogHeader>
          <DialogTitle>Запросить назначение роли</DialogTitle>
          <DialogDescription>
            {person.display_name} · {person.username}. Назначение вступит в силу после подтверждения
            другим руководителем в указанной области. После назначения потребуется новый вход
            пользователя.
          </DialogDescription>
        </DialogHeader>
        <form className="access-form" onSubmit={submit}>
          <label>
            Роль
            <select
              aria-label="Роль"
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
            >
              {Object.entries(roleNames).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {role !== 'access_admin' ? (
            <label>
              Область доступа
              <select
                aria-label="Область доступа"
                required
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
              >
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.label}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p className="muted small">
              Администратор доступа работает с учётными записями и правами. Для подтверждения нужен
              другой руководитель с областью всей организации. Бизнес-роли с этим назначением не
              совмещаются.
            </p>
          )}
          <div className="access-fields">
            <label>
              Начало действия
              <Input
                type="datetime-local"
                aria-label="Начало действия"
                required
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label>
              Окончание действия
              <Input
                type="datetime-local"
                aria-label="Окончание действия"
                required
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
          </div>
          <label>
            Основание назначения
            <textarea
              aria-label="Основание назначения"
              required
              minLength={5}
              maxLength={240}
              value={basis}
              onChange={(e) => setBasis(e.target.value)}
            />
          </label>
          <p className="small muted">
            Срок — не более года. Если начало уже прошло, доступ начнётся с момента подтверждения.
            Для изменения роли или области отзовите прежнее назначение и создайте новый запрос.
          </p>
          <Feedback command={command} />
          <Button disabled={command.pending || (!department && role !== 'access_admin')}>
            Направить на подтверждение
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function PersonDetail({
  id,
  user,
  options,
  back,
  refresh,
  revision,
}: {
  id: string
  user: User
  options: S['AccessOptions']
  back: () => void
  refresh: () => void
  revision: number
}) {
  const remote = useRemote(`access-user:${id}:${revision}`, (signal) =>
    result(
      client.GET('/api/v1/access/users/{user_id}', { params: { path: { user_id: id } }, signal }),
    ),
  )
  const [requesting, setRequesting] = useState(false),
    [operation, setOperation] = useState<{
      action: 'block' | 'unblock' | 'revoke-sessions' | 'revoke'
      grant?: string
    } | null>(null),
    [note, setNote] = useState(''),
    [sent, setSent] = useState(false)
  const command = useCommand()
  const data = remote.data
  function submit(e: FormEvent) {
    e.preventDefault()
    if (!data || !operation) return
    const body = { expected_version: data.version, note }
    void command.run(
      () =>
        operation.action === 'revoke'
          ? result(
              client.POST('/api/v1/access/grants/{assignment_id}/revoke', {
                body,
                params: {
                  path: { assignment_id: operation.grant! },
                  header: command.headers(operation.grant!, body),
                },
              }),
            )
          : result(
              client.POST('/api/v1/access/users/{user_id}/{action}', {
                body,
                params: {
                  path: { user_id: id, action: operation.action },
                  header: command.headers(operation.action, body),
                },
              }),
            ),
      () => {
        setOperation(null)
        setNote('')
        refresh()
      },
    )
  }
  return (
    <>
      <Button variant="ghost" onClick={back}>
        <ArrowLeft size={16} />
        Все пользователи
      </Button>
      <LoadState {...remote} onRetry={refresh} />
      {data && (
        <>
          <div className="access-heading">
            <div>
              <h2>{data.display_name}</h2>
              <p className="muted">
                {data.username} · {data.active ? 'Активен' : 'Заблокирован'} · MFA{' '}
                {data.mfa_configured ? 'подключена' : 'ещё не подключена'}
              </p>
            </div>
            <Button variant="outline" onClick={refresh}>
              <RefreshCw size={16} />
              Обновить сведения
            </Button>
          </div>
          {id !== user.id && (
            <div className="access-actions">
              <Button disabled={!data.active} onClick={() => setRequesting(true)}>
                <Plus size={16} />
                Запросить назначение роли
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setNote('')
                  setOperation({ action: data.active ? 'block' : 'unblock' })
                }}
              >
                {data.active ? 'Заблокировать аккаунт' : 'Разблокировать аккаунт'}
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setNote('')
                  setOperation({ action: 'revoke-sessions' })
                }}
              >
                Завершить все сессии
              </Button>
            </div>
          )}
          {id === user.id && (
            <p className="muted">
              Собственные назначения здесь доступны для чтения. Управление своим входом находится в
              «Моё пространство».
            </p>
          )}
          {operation && (
            <form className="access-form access-operation" onSubmit={submit}>
              <h3>
                {operation.action === 'revoke'
                  ? 'Отзыв назначения'
                  : operation.action === 'block'
                    ? 'Блокировка аккаунта'
                    : operation.action === 'unblock'
                      ? 'Разблокировка аккаунта'
                      : 'Завершение сессий'}
              </h3>
              <label>
                Основание действия
                <textarea
                  aria-label="Основание действия"
                  minLength={5}
                  maxLength={240}
                  required
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              <p className="small muted">
                Текущие сессии пользователя будут завершены. Пароль, MFA и история сохраняются.
              </p>
              <Feedback command={command} />
              <div className="access-actions">
                <Button disabled={command.pending}>Подтвердить действие</Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={command.pending}
                  onClick={() => setOperation(null)}
                >
                  Закрыть действие
                </Button>
              </div>
            </form>
          )}
          <h3>Назначения и история прав</h3>
          <Table className="access-grants-table">
            <TableHeader>
              <TableRow>
                <TableHead>Роль и область</TableHead>
                <TableHead>Период и состояние</TableHead>
                <TableHead>Основание</TableHead>
                <TableHead>Действие</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.grants.map((grant) => (
                <TableRow key={grant.id}>
                  <TableCell data-label="Роль и область">
                    <strong>{roleNames[grant.role] ?? grant.role}</strong>
                    <p>{grant.department}</p>
                  </TableCell>
                  <TableCell data-label="Период и состояние">
                    {states[grant.state]}
                    <p className="small muted">
                      С {date(grant.valid_from)}
                      <br />
                      До {date(grant.valid_until)}
                    </p>
                  </TableCell>
                  <TableCell data-label="Основание">
                    {grant.basis}
                    {grant.revocation_basis && <p>Отзыв: {grant.revocation_basis}</p>}
                  </TableCell>
                  <TableCell data-label="Действие">
                    {id !== user.id && ['active', 'scheduled'].includes(grant.state) && (
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Отозвать ${roleNames[grant.role]} ${grant.department}`}
                        onClick={() => {
                          setNote('')
                          setOperation({ action: 'revoke', grant: grant.id })
                        }}
                      >
                        Отозвать
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!data.grants.length && (
            <p className="access-empty">
              Назначений нет. Создайте запрос и дождитесь подтверждения руководителем.
            </p>
          )}
          {sent && (
            <p role="status" className="success-box">
              Запрос отправлен. Следите за решением во вкладке «Назначения на подтверждении».
            </p>
          )}
          {requesting && (
            <PermissionForm
              person={data}
              departments={options.departments}
              close={() => setRequesting(false)}
              done={() => {
                setRequesting(false)
                setSent(true)
                refresh()
              }}
            />
          )}
        </>
      )}
    </>
  )
}

function Users({
  user,
  options,
  revision,
  refresh,
}: {
  user: User
  options: S['AccessOptions']
  revision: number
  refresh: () => void
}) {
  const [selected, setSelected] = useState(''),
    [q, setQ] = useState(''),
    [status, setStatus] = useState('all'),
    [offset, setOffset] = useState(0)
  const remote = useRemote(`access-users:${q}:${status}:${offset}:${revision}`, (signal) =>
    result(
      client.GET('/api/v1/access/users', {
        params: {
          query: {
            q,
            active: status === 'all' ? undefined : status === 'active',
            offset,
            limit: 20,
          },
        },
        signal,
      }),
    ),
  )
  if (selected)
    return (
      <PersonDetail
        key={selected}
        id={selected}
        user={user}
        options={options}
        revision={revision}
        refresh={refresh}
        back={() => {
          setSelected('')
          refresh()
        }}
      />
    )
  return (
    <>
      <div className="filter-bar">
        <SearchField
          label="Поиск пользователей"
          value={q}
          onChange={(v) => {
            setQ(v)
            setOffset(0)
          }}
        />
        <select
          aria-label="Состояние аккаунта"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value)
            setOffset(0)
          }}
        >
          <option value="all">Все аккаунты</option>
          <option value="active">Активные</option>
          <option value="blocked">Заблокированные</option>
        </select>
      </div>
      <LoadState {...remote} onRetry={refresh} />
      {remote.data && (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Пользователь</TableHead>
                <TableHead>Логин</TableHead>
                <TableHead>Состояние</TableHead>
                <TableHead>MFA</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {remote.data.items.map((person) => (
                <TableRow key={person.id}>
                  <TableCell>
                    <button
                      type="button"
                      className="access-person"
                      onClick={() => setSelected(person.id)}
                    >
                      {person.display_name}
                    </button>
                  </TableCell>
                  <TableCell>{person.username}</TableCell>
                  <TableCell>{person.active ? 'Активен' : 'Заблокирован'}</TableCell>
                  <TableCell>
                    {person.mfa_configured ? 'Подключена' : 'Ожидает настройки'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!remote.data.total && (
            <p className="access-empty">Пользователей по этому запросу нет.</p>
          )}
          <Pagination {...remote.data} onChange={setOffset} />
        </>
      )}
    </>
  )
}

function RequestActions({ item, refresh }: { item: Permission; refresh: () => void }) {
  const [note, setNote] = useState(''),
    [outcome, setOutcome] = useState<'approve' | 'reject'>('approve'),
    [ack, setAck] = useState(false)
  const command = useCommand()
  return (
    <>
      <Feedback command={command} />
      {item.can_decide && (
        <form
          className="access-form"
          onSubmit={(e) => {
            e.preventDefault()
            const body = { expected_version: item.version, note, outcome, checked: ack }
            void command.run(
              () =>
                result(
                  client.POST('/api/v1/access/requests/{request_id}/decision', {
                    body,
                    params: {
                      path: { request_id: item.id },
                      header: command.headers('decision:' + item.id, body),
                    },
                  }),
                ),
              refresh,
            )
          }}
        >
          <label>
            Решение по правам
            <select
              aria-label="Решение по правам"
              value={outcome}
              onChange={(e) => setOutcome(e.target.value as 'approve' | 'reject')}
            >
              <option value="approve">Подтвердить назначение</option>
              <option value="reject">Отклонить запрос</option>
            </select>
          </label>
          <label>
            Обоснование решения
            <textarea
              aria-label="Обоснование решения"
              required
              minLength={5}
              maxLength={240}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <label className="access-check">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            Проверены личность получателя, необходимость роли, отдел и срок
          </label>
          <Button disabled={command.pending || (outcome === 'approve' && !ack)}>
            Сохранить решение по правам
          </Button>
        </form>
      )}
      {item.can_cancel && (
        <form
          className="access-form"
          onSubmit={(e) => {
            e.preventDefault()
            const body = { expected_version: item.version, note }
            void command.run(
              () =>
                result(
                  client.POST('/api/v1/access/requests/{request_id}/cancel', {
                    body,
                    params: {
                      path: { request_id: item.id },
                      header: command.headers('cancel:' + item.id, body),
                    },
                  }),
                ),
              refresh,
            )
          }}
        >
          <label>
            Причина отмены запроса
            <Input
              aria-label="Причина отмены запроса"
              required
              minLength={5}
              maxLength={240}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <Button variant="outline" disabled={command.pending}>
            Отменить запрос назначения
          </Button>
        </form>
      )}
    </>
  )
}

function Requests({ revision, refresh }: { revision: number; refresh: () => void }) {
  const [offset, setOffset] = useState(0),
    [status, setStatus] = useState('pending'),
    [selected, setSelected] = useState('')
  const remote = useRemote(`access-requests:${offset}:${status}:${revision}`, (signal) =>
    result(
      client.GET('/api/v1/access/requests', {
        params: { query: { state: status || undefined, offset, limit: 20 } },
        signal,
      }),
    ),
  )
  const selectedItem = remote.data?.items.find((i) => i.id === selected)
  return (
    <>
      <div className="filter-bar">
        <select
          aria-label="Состояние запроса прав"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value)
            setOffset(0)
            setSelected('')
          }}
        >
          <option value="">Все запросы</option>
          <option value="pending">Ожидают подтверждения</option>
          <option value="approved">Назначено</option>
          <option value="rejected">Отклонено</option>
          <option value="cancelled">Отменено</option>
        </select>
      </div>
      <p className="muted">
        Руководитель подтверждает назначения в своей области. Автор запроса и получатель прав не
        могут подтвердить это назначение.
      </p>
      <LoadState {...remote} onRetry={refresh} />
      {remote.data && (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Получатель</TableHead>
                <TableHead>Роль и область</TableHead>
                <TableHead>Период</TableHead>
                <TableHead>Состояние</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {remote.data.items.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>
                    <button
                      type="button"
                      className="access-person"
                      onClick={() => setSelected(item.id)}
                    >
                      {item.display_name}
                    </button>
                    <p className="small muted">{item.username}</p>
                  </TableCell>
                  <TableCell>
                    {roleNames[item.role]}
                    <p>{item.department}</p>
                  </TableCell>
                  <TableCell>
                    <p className="small">
                      {date(item.valid_from)}
                      <br />
                      {date(item.valid_until)}
                    </p>
                  </TableCell>
                  <TableCell>{states[item.state]}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!remote.data.total && (
            <p className="access-empty">Запросов в выбранном состоянии нет.</p>
          )}
          <Pagination {...remote.data} onChange={setOffset} />
        </>
      )}
      {selectedItem && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) setSelected('')
          }}
        >
          <DialogContent className="access-dialog">
            <DialogHeader>
              <DialogTitle>Назначение: {selectedItem.display_name}</DialogTitle>
              <DialogDescription>
                {roleNames[selectedItem.role]} · {selectedItem.department} ·{' '}
                {states[selectedItem.state]}
              </DialogDescription>
            </DialogHeader>
            <p>
              Период: {date(selectedItem.valid_from)} — {date(selectedItem.valid_until)}
            </p>
            <p>Инициатор: {selectedItem.creator}</p>
            <p className="access-text">Основание: {selectedItem.basis}</p>
            {selectedItem.decider && (
              <p className="access-text">
                Решение: {selectedItem.decider}. {selectedItem.decision_note}
              </p>
            )}
            <RequestActions
              key={`${selectedItem.id}:${selectedItem.version}`}
              item={selectedItem}
              refresh={() => {
                setSelected('')
                refresh()
              }}
            />
          </DialogContent>
        </Dialog>
      )}
    </>
  )
}

function AccessHistory({ revision, refresh }: { revision: number; refresh: () => void }) {
  const [offset, setOffset] = useState(0)
  const remote = useRemote(`access-history:${offset}:${revision}`, (signal) =>
    result(
      client.GET('/api/v1/access/events', { params: { query: { offset, limit: 25 } }, signal }),
    ),
  )
  return (
    <>
      <LoadState {...remote} onRetry={refresh} />
      {remote.data && (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Действие</TableHead>
                <TableHead>Кто и когда</TableHead>
                <TableHead>Основание</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {remote.data.items.map((event) => (
                <TableRow key={event.id}>
                  <TableCell>{auditLabel(event.kind)}</TableCell>
                  <TableCell>
                    {event.actor}
                    <p className="small muted">{date(event.occurred_at)}</p>
                  </TableCell>
                  <TableCell className="access-text">{event.note || '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Pagination {...remote.data} onChange={setOffset} />
        </>
      )}
    </>
  )
}

export function AccessWorkspace({ user }: { user: User }) {
  const [revision, setRevision] = useState(0),
    [creating, setCreating] = useState(false)
  const [optionsRetry, setOptionsRetry] = useState(0)
  const [tab, setTab] = useState(
    user.roles.some((r) => r.role === 'access_admin') ? 'users' : 'requests',
  )
  const refresh = () => setRevision((v) => v + 1)
  const remote = useRemote(`access-options:${user.id}:${optionsRetry}`, (signal) =>
    result(client.GET('/api/v1/access/options', { signal })),
  )
  if (!remote.data)
    return (
      <section className="content-panel">
        <LoadState {...remote} onRetry={() => setOptionsRetry((v) => v + 1)} />
      </section>
    )
  const options = remote.data
  return (
    <section className="content-panel access-workspace">
      <div className="access-heading">
        <div>
          <h2>{options.is_admin ? 'Пользователи и права' : 'Подтверждение назначений'}</h2>
          <p className="muted">Личные учётные записи, ограниченные области и история решений</p>
        </div>
        <div className="access-actions">
          <Button variant="outline" onClick={refresh}>
            <RefreshCw size={16} />
            Обновить
          </Button>
          {options.is_admin && (
            <Button onClick={() => setCreating(true)}>
              <Plus size={16} />
              Пригласить пользователя
            </Button>
          )}
        </div>
      </div>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="access-tabs">
          {options.is_admin && (
            <>
              <TabsTrigger value="users">Пользователи</TabsTrigger>
              <TabsTrigger value="invitations">Приглашения</TabsTrigger>
            </>
          )}
          <TabsTrigger value="requests">Назначения на подтверждении</TabsTrigger>
          {options.is_admin && <TabsTrigger value="history">История доступа</TabsTrigger>}
        </TabsList>
        {options.is_admin && (
          <>
            <TabsContent value="users">
              <Users user={user} options={options} revision={revision} refresh={refresh} />
            </TabsContent>
            <TabsContent value="invitations">
              <Invitations
                revision={revision}
                refresh={refresh}
                localDelivery={options.local_delivery}
              />
            </TabsContent>
          </>
        )}
        <TabsContent value="requests">
          <Requests revision={revision} refresh={refresh} />
        </TabsContent>
        {options.is_admin && (
          <TabsContent value="history">
            <AccessHistory revision={revision} refresh={refresh} />
          </TabsContent>
        )}
      </Tabs>
      {creating && options.is_admin && (
        <InviteForm
          close={() => setCreating(false)}
          done={() => {
            setCreating(false)
            setTab('invitations')
            refresh()
          }}
        />
      )}
    </section>
  )
}
