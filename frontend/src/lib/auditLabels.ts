const labels: Record<string, string> = {
  'access.invited': 'Создано приглашение пользователя',
  'access.invitation_delivered': 'Выдан код приглашения',
  'access.invitation_accepted': 'Приглашение активировано',
  'access.invitation_cancelled': 'Приглашение отменено',
  'access.permission_requested': 'Запрошено назначение роли',
  'access.permission_approved': 'Назначение роли подтверждено',
  'access.permission_rejected': 'Назначение роли отклонено',
  'access.permission_cancelled': 'Запрос назначения отменён',
  'access.permission_revoked': 'Назначение роли отозвано',
  'access.account_block': 'Учётная запись заблокирована',
  'access.account_unblock': 'Учётная запись разблокирована',
  'access.account_revoke-sessions': 'Сессии пользователя завершены',
  'access.management_denied': 'Отказ в управлении доступом',
  'report.created': 'Подготовлен черновик отчёта',
  'report.saved': 'Сохранена редакция отчёта',
  'report.submitted': 'Отчёт направлен рецензенту',
  'report.returned': 'Отчёт возвращён на доработку',
  'report.reviewed': 'Рецензент проверил отчёт',
  'report.released': 'Выпущен неизменяемый отчёт',
  'report.exported': 'Выдан файл отчёта',
  'change.created': 'Создано предложение изменений',
  'change.version_created': 'Создана новая версия предложения',
  'change.sent': 'Предложение направлено на согласование',
  'change.message': 'Сообщение по предложению',
  'change.private_note': 'Сохранена закрытая заметка',
  'change.reviewed': 'Ответ согласующего',
  'change.decided': 'Управленческое решение',
  'change.execution_recorded': 'Зарегистрировано исполнение действия',
  'change.verified': 'Аудитор проверил исполнение',
  'change.cancelled': 'Предложение отменено с сохранением истории',
  'change.reminder': 'Напоминание о рассмотрении',
  'change.escalation': 'Вопрос передан руководителю',
  'change.notice_read': 'Уведомление о предложении прочитано',
  'access.workflow_demo_created': 'Созданы учебные аккаунты согласования',
  'personnel.terminate': 'Назначение прекращено',
  'personnel.appoint': 'Зарегистрировано назначение',
  'personnel.correct_termination': 'Исправлена дата прекращения',
  'registry.seeded': 'Создан учебный реестр',
  'seed.expanded': 'Дополнен учебный состав',
  'organization.name_changed': 'Изменено название организации',
  'document.uploaded': 'Загружена редакция документа',
  'document.downloaded': 'Скачан оригинал документа',
  'document.processing_ready': 'Извлечён текст документа',
  'document.processing_partial': 'Текст извлечён частично',
  'document.processing_failed': 'Ошибка обработки документа',
  'document.processing_needs_ocr': 'Документу требуется распознавание',
  'document.processing_access_revoked': 'Доступ к обработке отозван',
  'document.cancelled': 'Обработка документа отменена',
  'document.retried': 'Повторена обработка документа',
  'corpus.created': 'Зафиксирован комплект документов',
  'comparison.created': 'Запущено сравнение документов',
  'comparison.reviewed': 'Записано рассмотрение результата',
  'analysis.completed': 'Анализ завершён',
  'analysis.failed': 'Ошибка анализа',
  'analysis.cancel': 'Анализ отменён',
  'analysis.retry': 'Анализ поставлен на повтор',
  'search.index_requested': 'Запрошена индексация документов',
  'search.maintenance_indexed': 'Обновлён поисковый индекс',
  'access.denied': 'Отказ в доступе',
  'audit.viewed': 'Открыт журнал действий',
  'auth.password_verified': 'Проверен пароль',
  'auth.login_failed': 'Неудачная попытка входа',
  'auth.login_succeeded': 'Вход подтверждён',
  'auth.enrollment_started': 'Начато подключение MFA',
  'auth.mfa_enrolled': 'Подключена двухфакторная защита',
  'auth.mfa_failed': 'Неудачная проверка MFA',
  'auth.reauthenticated': 'Вход подтверждён повторно',
  'auth.local_demo_login_succeeded': 'Вход по паролю в локальном учебном режиме',
  'auth.reauthentication_failed': 'Повторный вход не подтверждён',
  'auth.logout': 'Завершена сессия',
  'auth.sessions_revoked': 'Отозваны другие сессии',
  'auth.password_reset': 'Изменён пароль',
  'auth.recovery_issued': 'Выдано восстановление доступа',
  'auth.mfa_replaced': 'Заменён аутентификатор',
  'auth.mfa_replacement_started': 'Начата замена аутентификатора',
  'auth.mfa_replacement_failed': 'Замена аутентификатора не подтверждена',
}
export const auditLabel = (kind: string) => labels[kind] ?? kind

// Describe only outcomes encoded by an actual event, never infer business approval.
export function auditOutcome(kind: string, payload: Record<string, unknown>) {
  if (kind.includes('failed')) return 'Ошибка'
  if (kind.includes('denied') || kind.includes('access_revoked')) return 'Доступ запрещён'
  if (kind.includes('cancel')) return 'Отменено'
  if (kind.includes('needs_ocr')) return 'Нужна проверка'
  if (kind.includes('partial')) return 'Частично'
  if (kind === 'comparison.reviewed') {
    const decisions: Record<string, string> = {
      confirmed: 'Подтверждено',
      dismissed: 'Отклонено',
      needs_evidence: 'Нужны основания',
    }
    return decisions[String(payload.decision)] ?? 'Рассмотрено'
  }
  if (kind === 'change.reviewed' || kind === 'change.decided') {
    const outcomes: Record<string, string> = {
      agree: 'Согласовано',
      approve: 'Утверждено',
      return: 'Возвращено',
      reject: 'Отклонено',
      request_info: 'Запрошены сведения',
      accept_risk: 'Принят риск',
    }
    return outcomes[String(payload.outcome)] ?? 'Решение сохранено'
  }
  if (kind.endsWith('created') || kind.endsWith('requested') || kind.endsWith('retry'))
    return 'Зарегистрировано'
  return 'Зафиксировано'
}
