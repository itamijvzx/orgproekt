import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CalendarClock, FileText, TriangleAlert } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { localToday } from '@/lib/api'
import { client, result, errorMessage } from '@/lib/client'
import type { Appointment, Seat, Snapshot } from '@/lib/types'

export interface PersonnelTarget {
  seat: Seat
  action: 'terminate' | 'appoint' | 'correct_termination'
  appointment?: Appointment
  corrects_event_id?: string
}
export function PersonnelDialog({
  target,
  data,
  onClose,
  onSaved,
}: {
  target: PersonnelTarget | null
  data: Snapshot
  onClose: () => void
  onSaved: () => void
}) {
  const [date, setDate] = useState(localToday())
  const [end, setEnd] = useState('')
  const [kind, setKind] = useState<'permanent' | 'temporary'>('permanent')
  const [employee, setEmployee] = useState('')
  const [people, setPeople] = useState<{ id: string; display_name: string }[]>([])
  const [title, setTitle] = useState('')
  const [quote, setQuote] = useState('')
  const [locator, setLocator] = useState('Пункт 1')
  const [documentDate, setDocumentDate] = useState(localToday())
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const lastRequest = useRef<{ payload: string; key: string } | null>(null)
  const position = data.positions.find((p) => p.id === target?.seat.position_id)
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    setAck(false)
    setTitle('')
    setQuote('')
    lastRequest.current = null
    setEmployee('')
    setPeople([])
    setDate(localToday())
    setEnd('')
    setKind('permanent')
    setDocumentDate(localToday())
    if (target?.action === 'appoint' && position)
      result(
        client.GET('/api/v1/employees', {
          params: { query: { department_id: position.department_id } },
          signal: controller.signal,
        }),
      )
        .then((value) => {
          if (!controller.signal.aborted) setPeople(value)
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(errorMessage(e))
        })
    return () => controller.abort()
  }, [target, position])
  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!target) return
    setError('')
    setBusy(true)
    try {
      const body = {
        action: target.action,
        seat_id: target.seat.id,
        appointment_id: target.appointment?.id ?? null,
        employee_id: employee || null,
        effective_date: date,
        end_date: target.action === 'appoint' && kind === 'temporary' ? end || null : null,
        kind,
        expected_version: target.seat.version,
        corrects_event_id: target.corrects_event_id ?? null,
        basis: { title, locator, quote, document_date: documentDate },
      }
      const payload = JSON.stringify(body)
      if (lastRequest.current?.payload !== payload)
        lastRequest.current = { payload, key: crypto.randomUUID() }
      await result(
        client.POST('/api/v1/personnel/events', {
          body,
          params: { header: { 'idempotency-key': lastRequest.current.key } },
        }),
      )
      onSaved()
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  const appoint = target?.action === 'appoint'
  return (
    <Dialog
      open={!!target}
      onOpenChange={(v) => {
        if (!v && !busy) onClose()
      }}
    >
      <DialogContent className="personnel-dialog">
        <DialogHeader>
          <DialogTitle>
            {appoint
              ? 'Зарегистрировать назначение'
              : target?.action === 'correct_termination'
                ? 'Исправить дату прекращения'
                : 'Прекратить назначение'}
          </DialogTitle>
          <DialogDescription>
            {position?.title} · {target?.seat.code}. Официальное событие с документальным
            основанием.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="event-form">
          {!appoint && (
            <div className="notice-strip">
              <CalendarClock size={18} />
              <span>
                {target?.appointment?.employee_name}. Должность и документированные функции
                сохранятся.
              </span>
            </div>
          )}
          {appoint && (
            <>
              <div className="field">
                <Label htmlFor="employee">Сотрудник</Label>
                <select
                  id="employee"
                  required
                  value={employee}
                  onChange={(e) => setEmployee(e.target.value)}
                >
                  <option value="">Выберите сотрудника</option>
                  {people.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <Label htmlFor="kind">Вид назначения</Label>
                <select
                  id="kind"
                  value={kind}
                  onChange={(e) =>
                    setKind(e.target.value === 'temporary' ? 'temporary' : 'permanent')
                  }
                >
                  <option value="permanent">Постоянное</option>
                  <option value="temporary">Временное замещение</option>
                </select>
              </div>
            </>
          )}
          <div className="field-row">
            <div className="field">
              <Label htmlFor="effective-date">
                {appoint ? 'Начало назначения' : 'Первый день без назначения'}
              </Label>
              <Input
                id="effective-date"
                type="date"
                required
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
            {appoint && kind === 'temporary' && (
              <div className="field">
                <Label htmlFor="end-date">Первый день после замещения</Label>
                <Input
                  id="end-date"
                  type="date"
                  required
                  min={date}
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </div>
            )}
          </div>
          <p className="small muted">
            Дата окончания не входит в период. Будущее событие изменит состояние на указанную дату.
          </p>
          <div className="form-section-title">
            <FileText size={16} />
            Документ-основание
          </div>
          <div className="field">
            <Label htmlFor="basis-title">Название и номер документа</Label>
            <Input
              id="basis-title"
              required
              minLength={5}
              maxLength={240}
              placeholder="Приказ № 12 о кадровом изменении"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="field-row">
            <div className="field">
              <Label htmlFor="basis-date">Дата документа</Label>
              <Input
                id="basis-date"
                type="date"
                required
                max={date}
                value={documentDate}
                onChange={(e) => setDocumentDate(e.target.value)}
              />
            </div>
            <div className="field">
              <Label htmlFor="basis-locator">Пункт / раздел</Label>
              <Input
                id="basis-locator"
                required
                maxLength={120}
                value={locator}
                onChange={(e) => setLocator(e.target.value)}
              />
            </div>
          </div>
          <div className="field">
            <Label htmlFor="basis-quote">Точная выдержка</Label>
            <textarea
              id="basis-quote"
              required
              minLength={10}
              maxLength={4000}
              value={quote}
              onChange={(e) => setQuote(e.target.value)}
              placeholder="Текст пункта, подтверждающего изменение и дату"
              rows={3}
            />
          </div>
          <label className="check-label">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            <span>
              Подтверждаю полномочия и соответствие введённых данных официальному основанию.
            </span>
          </label>
          {error && (
            <div className="error-box" role="alert">
              <TriangleAlert size={16} />
              {error}
            </div>
          )}
          <div className="dialog-actions">
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              Отмена
            </Button>
            <Button type="submit" disabled={!ack || busy}>
              {busy ? 'Сохраняем…' : 'Зарегистрировать событие'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
