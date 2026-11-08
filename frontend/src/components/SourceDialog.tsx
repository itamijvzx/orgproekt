import { useEffect, useRef, useState } from 'react'
import { FileText, ShieldCheck } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { dateLabel } from '@/lib/api'
import { client, result, errorMessage } from '@/lib/client'
import type { Source } from '@/lib/types'

export function SourceDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [source, setSource] = useState<Source | null>(null)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const highlighted = useRef<HTMLElement>(null)
  useEffect(() => {
    highlighted.current?.scrollIntoView({ block: 'center' })
  }, [source])
  useEffect(() => {
    const controller = new AbortController()
    setSource(null)
    setError('')
    if (id)
      result(
        client.GET('/api/v1/sources/{anchor_id}', {
          params: { path: { anchor_id: id } },
          signal: controller.signal,
        }),
      )
        .then((s) => {
          if (!controller.signal.aborted) setSource(s)
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(errorMessage(e))
        })
    return () => controller.abort()
  }, [id, retry])
  return (
    <Dialog
      open={!!id}
      onOpenChange={(v) => {
        if (!v) onClose()
      }}
    >
      <DialogContent className="source-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileText size={19} />
            Документальное основание
          </DialogTitle>
          <DialogDescription>Сохранённая редакция и точная выдержка из источника</DialogDescription>
        </DialogHeader>
        {error ? (
          <div className="error-box" role="alert">
            {error}
            <Button variant="outline" onClick={() => setRetry((v) => v + 1)}>
              Повторить
            </Button>
          </div>
        ) : !source ? (
          <p role="status">Загружаем источник…</p>
        ) : (
          <>
            <article className="source-paper">
              <div className="eyebrow">
                {source.synthetic ? 'СИНТЕТИЧЕСКИЙ ДОКУМЕНТ' : 'ЗАРЕГИСТРИРОВАННЫЙ ИСТОЧНИК'}
              </div>
              <p className="small muted">{source.organization}</p>
              <h2>{source.title}</h2>
              <div className="source-meta">
                <span>Редакция {source.revision}</span>
                <span>
                  Действует с:{' '}
                  {source.effective_from ? dateLabel(source.effective_from) : 'не установлено'}
                </span>
              </div>
              <div className="source-quote">
                <span className="source-locator">{source.locator}</span>
                {source.context ? (
                  <blockquote className="extracted-context">
                    {source.context.text.slice(0, source.context.start)}
                    <mark ref={highlighted}>
                      {source.context.text.slice(source.context.start, source.context.end)}
                    </mark>
                    {source.context.text.slice(source.context.end)}
                  </blockquote>
                ) : (
                  <blockquote>{source.quote}</blockquote>
                )}
              </div>
              <p className="small muted">
                {source.status === 'registered_excerpt'
                  ? 'Выдержка зарегистрирована уполномоченным пользователем. Подлинность и полнота оригинала системой не удостоверены.'
                  : source.context
                    ? 'Текст извлечён из сохранённого оригинала. Выделен точный фрагмент; полноту и применимость необходимо проверить по файлу.'
                    : 'Учебная запись реестра. Текст и сотрудники вымышлены.'}
              </p>
            </article>
            <div className="source-integrity">
              <ShieldCheck size={16} />
              <span>
                Неизменяемая редакция · SHA-256 <code>{source.sha256.slice(0, 16)}…</code>
              </span>
            </div>
          </>
        )}
        <Button variant="outline" onClick={onClose}>
          Закрыть источник
        </Button>
      </DialogContent>
    </Dialog>
  )
}
