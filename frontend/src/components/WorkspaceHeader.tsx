import { useState } from 'react'
import { DropdownMenu } from 'radix-ui'
import {
  ArrowLeftRight,
  Bell,
  BookOpen,
  Building2,
  ChevronDown,
  CircleHelp,
  FileClock,
  FileDiff,
  FileText,
  History,
  House,
  LogOut,
  Menu,
  MoreHorizontal,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import type { User } from '@/lib/types'

const sections = [
  { id: 'access', label: 'Доступ', icon: ShieldCheck },
  { id: 'overview', label: 'Обзор', icon: House },
  { id: 'organization', label: 'Организация', icon: Building2 },
  { id: 'functions', label: 'Реестр функций', icon: BookOpen },
  { id: 'documents', label: 'Документы', icon: FileText },
  { id: 'changes', label: 'Предложения', icon: ArrowLeftRight },
  { id: 'reports', label: 'Отчёты', icon: FileText },
  { id: 'comparisons', label: 'Комплекты и сравнение', icon: FileDiff },
  { id: 'search', label: 'Поиск по содержанию', icon: Search },
  { id: 'history', label: 'Кадровые события', icon: FileClock },
  { id: 'notifications', label: 'Уведомления', icon: Bell },
  { id: 'audit', label: 'Журнал действий', icon: History },
  { id: 'settings', label: 'Моё пространство', icon: Settings2 },
  { id: 'about', label: 'О проекте', icon: CircleHelp },
]
const roles: Record<string, string> = {
  auditor: 'Внутренний аудитор',
  hr: 'Кадровый специалист',
  department: 'Представитель отдела',
  access_admin: 'Администратор доступа',
  manager: 'Руководитель',
  reviewer: 'Рецензент аудита',
}

export function WorkspaceHeader({
  name,
  page,
  user,
  onNavigate,
  onRefresh,
  onLogout,
}: {
  name: string
  page: string
  user: User
  onNavigate: (page: string) => void
  onRefresh: () => void
  onLogout: () => void
}) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const admin = user.roles.some((r) => r.role === 'access_admin')
  const managesAccess = admin || user.roles.some((r) => r.role === 'manager')
  const visibleSections = sections.filter((s) =>
    s.id === 'access'
      ? managesAccess
      : !admin || ['audit', 'settings', 'about', 'notifications'].includes(s.id),
  )
  function navigate(id: string) {
    setMobileOpen(false)
    onNavigate(id)
  }
  const item = (id: string, menu = false) => {
    const entry = sections.find((s) => s.id === id)!
    const contents = (
      <>
        <entry.icon size={18} />
        <span>{entry.label}</span>
      </>
    )
    return menu ? (
      <DropdownMenu.Item
        key={id}
        className="nav-menu-item"
        onSelect={() => navigate(id)}
        aria-current={page === id ? 'page' : undefined}
      >
        {contents}
      </DropdownMenu.Item>
    ) : (
      <button
        key={id}
        type="button"
        className={`top-nav-item ${page === id ? 'active' : ''}`}
        aria-current={page === id ? 'page' : undefined}
        onClick={() => navigate(id)}
      >
        {contents}
      </button>
    )
  }
  const group = (label: string, ids: string[], Icon: typeof ArrowLeftRight) => (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" className={`top-nav-item ${ids.includes(page) ? 'active' : ''}`}>
          <Icon size={18} />
          <span>{label}</span>
          <ChevronDown size={13} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className="nav-dropdown"
          sideOffset={8}
          align="start"
          collisionPadding={12}
        >
          {ids.map((id) => item(id, true))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <button
          type="button"
          className="wordmark"
          title={name}
          onClick={() => navigate('overview')}
        >
          {name}
        </button>
        <nav className="top-nav" aria-label="Основная навигация">
          {admin ? (
            item('access')
          ) : (
            <>
              {item('overview')}
              {item('organization')}
              {item('documents')}
              {group('Изменения', ['changes', 'comparisons', 'history', 'reports'], ArrowLeftRight)}
            </>
          )}
          {item('audit')}
          {group(
            'Ещё',
            admin
              ? ['about']
              : ['functions', 'search', ...(managesAccess ? ['access'] : []), 'about'],
            MoreHorizontal,
          )}
        </nav>
        <div className="topbar-actions">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Открыть уведомления"
            aria-current={page === 'notifications' ? 'page' : undefined}
            onClick={() => navigate('notifications')}
          >
            <Bell size={20} />
          </Button>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button className="user-menu" type="button" aria-label="Меню пользователя">
                <span className="user-avatar">
                  {user.display_name
                    .split(' ')
                    .map((p) => p[0])
                    .slice(0, 2)
                    .join('')}
                </span>
                <span className="user-menu-copy">
                  <strong>{user.display_name}</strong>
                  <small>{roles[user.roles[0]?.role] ?? 'Область не назначена'}</small>
                </span>
                <ChevronDown size={15} />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                className="nav-dropdown"
                sideOffset={10}
                align="end"
                collisionPadding={12}
              >
                <DropdownMenu.Label className="nav-menu-label">
                  {user.display_name}
                  <small>{roles[user.roles[0]?.role] ?? 'Область не назначена'}</small>
                </DropdownMenu.Label>
                <DropdownMenu.Separator className="nav-menu-separator" />
                {item('settings', true)}
                <DropdownMenu.Item className="nav-menu-item" onSelect={onRefresh}>
                  <RefreshCw size={17} />
                  Обновить данные
                </DropdownMenu.Item>
                <DropdownMenu.Separator className="nav-menu-separator" />
                <DropdownMenu.Item className="nav-menu-item" onSelect={onLogout}>
                  <LogOut size={17} />
                  Выйти
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
          <Button
            className="mobile-menu-toggle"
            variant="ghost"
            size="icon"
            aria-label="Открыть меню"
            onClick={() => setMobileOpen(true)}
          >
            <Menu />
          </Button>
        </div>
      </div>
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent className="mobile-navigation">
          <SheetHeader>
            <SheetTitle>{name}</SheetTitle>
            <SheetDescription>Разделы рабочего пространства</SheetDescription>
          </SheetHeader>
          <nav aria-label="Разделы приложения">{visibleSections.map((s) => item(s.id))}</nav>
        </SheetContent>
      </Sheet>
    </header>
  )
}
