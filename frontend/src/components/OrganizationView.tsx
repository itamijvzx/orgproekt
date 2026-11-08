import { useCallback, useEffect, useMemo, useState } from 'react'
import { useReducedMotion } from 'motion/react'
import {
  Background,
  Controls,
  Handle,
  Position as HandlePosition,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Node,
  type NodeProps,
  type Edge,
  type NodeChange,
  applyNodeChanges,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Building2,
  ChevronDown,
  ChevronRight,
  Crosshair,
  GitBranch,
  List,
  UserRound,
  UsersRound,
} from 'lucide-react'
import { Button } from './ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet'
import { Inspector } from './Inspector'
import { Status } from './Status'
import { DateField, OrganizationDirectory } from './WorkspaceLists'
import { SearchField, LoadState } from './ListControls'
import { client, result } from '@/lib/client'
import { useRemote } from '@/lib/useRemote'
import { useUI } from '@/lib/store'
import type { Coverage, Snapshot, User } from '@/lib/types'
import type { components } from '@/lib/schema.generated'
import type { PersonnelTarget } from './PersonnelDialog'

type OrgNode = Node<
  {
    label: string
    code: string
    kind: 'department' | 'position' | 'seat' | 'employee'
    status?: Coverage
    detail: string
    expanded?: boolean
    toggle?: () => void
    employeeId?: string
  },
  'org'
>
function GraphNode({ data, selected }: NodeProps<OrgNode>) {
  return (
    <div className={`org-node ${data.kind}-node ${selected ? 'is-selected' : ''}`}>
      <Handle type="target" position={HandlePosition.Top} />
      <div className="node-overline">
        <span>
          {data.kind === 'department' ? (
            <Building2 size={15} />
          ) : data.kind === 'position' ? (
            <UsersRound size={15} />
          ) : (
            <UserRound size={15} />
          )}{' '}
          {data.code}
        </span>
        {data.kind === 'department' && (
          <button
            className="nodrag"
            aria-label={`${data.expanded ? 'Свернуть' : 'Раскрыть'} ${data.label}`}
            onClick={(e) => {
              e.stopPropagation()
              data.toggle?.()
            }}
          >
            {data.expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
          </button>
        )}
      </div>
      <strong>{data.label}</strong>
      <span className="node-detail">{data.detail}</span>
      {data.status && (
        <div className="node-bottom">
          <Status status={data.status} compact />
        </div>
      )}
      <Handle type="source" position={HandlePosition.Bottom} />
    </div>
  )
}
const nodeTypes = { org: GraphNode }
function strongest(values: Coverage[]): Coverage {
  return values.includes('uncovered')
    ? 'uncovered'
    : values.includes('unknown') || !values.length
      ? 'unknown'
      : values.every((v) => v === 'abolished')
        ? 'abolished'
        : 'covered'
}
type DirectoryEmployee = components['schemas']['DirectoryEmployee']

async function loadGraphEmployees(asOf: string, signal: AbortSignal, query?: string) {
  const employees: DirectoryEmployee[] = []
  const ids = new Set<string>()
  let expectedTotal: number | null = null
  let offset = 0
  do {
    const page = await result(
      client.GET('/api/v1/directory/employees', {
        params: { query: { as_of: asOf, q: query || undefined, limit: 100, offset } },
        signal,
      }),
    )
    if (page.total > 1000) {
      throw new Error(
        'Схема поддерживает до 1000 сотрудников. Используйте таблицу и фильтры для просмотра полного состава.',
      )
    }
    if ((expectedTotal !== null && page.total !== expectedTotal) || page.offset !== offset) {
      throw new Error(
        'Состав изменился во время загрузки. Повторите запрос, чтобы получить полный каталог.',
      )
    }
    expectedTotal = page.total
    for (const employee of page.items) {
      if (ids.has(employee.id))
        throw new Error('Состав изменился во время загрузки. Повторите запрос.')
      ids.add(employee.id)
      employees.push(employee)
    }
    offset += page.items.length
    if (offset > page.total || (offset < page.total && page.items.length === 0)) {
      throw new Error('Сервер вернул неполный каталог сотрудников. Повторите запрос.')
    }
  } while (offset < expectedTotal)
  return employees
}

function Canvas({
  data,
  revision,
  onEmployee,
}: {
  data: Snapshot
  revision: number
  onEmployee: (id: string) => void
}) {
  const { selection, expanded, positions, viewport, query, asOf, set } = useUI()
  const { fitView } = useReactFlow()
  const reduced = useReducedMotion()
  const [nodes, setNodes] = useState<OrgNode[]>([])
  const [retry, setRetry] = useState(0)
  const directory = useRemote(`graph-directory:${asOf}:${revision}:${retry}`, (signal) =>
    loadGraphEmployees(asOf, signal),
  )
  const search = useRemote(`graph-search:${query}:${asOf}:${revision}:${retry}`, (signal) =>
    query.trim() ? loadGraphEmployees(asOf, signal, query.trim()) : Promise.resolve([]),
  )
  useEffect(() => {
    if (query.trim() && search.data) {
      const ids = [...new Set(search.data.map((e) => e.department_id))]
      set({ expanded: ids })
    }
  }, [query, search.data, set])
  const built = useMemo(() => {
    const nodes: OrgNode[] = []
    const edges: Edge[] = []
    const root = data.departments.find((d) => !d.parent_id)
    const leaves = data.departments.filter((d) => d.id !== root?.id)
    const unassigned = (departmentId: string) =>
      (directory.data ?? []).filter(
        (employee) =>
          employee.department_id === departmentId &&
          !employee.appointments.some((appointment) => appointment.active),
      )
    const ownWidth = (departmentId: string) =>
      data.positions
        .filter((position) => position.department_id === departmentId)
        .reduce(
          (width, position) =>
            width +
            Math.max(
              270,
              data.seats.filter((seat) => seat.position_id === position.id).length * 270,
            ),
          0,
        ) +
      unassigned(departmentId).length * 270
    const widths = new Map(
      data.departments.map((d) => [
        d.id,
        expanded.includes(d.id) ? Math.max(280, ownWidth(d.id)) : 280,
      ]),
    )
    const leavesWidth = leaves.reduce((sum, d) => sum + widths.get(d.id)!, 0)
    const total = Math.max(leavesWidth, root ? widths.get(root.id)! : 0)
    let cursor = (total - leavesWidth) / 2
    const rootSeats = root
      ? data.seats.filter((seat) =>
          data.positions.some(
            (position) => position.id === seat.position_id && position.department_id === root.id,
          ),
        )
      : []
    const rootActiveRows = Math.max(
      0,
      ...rootSeats.map(
        (seat) => seat.appointments.filter((appointment) => appointment.active).length,
      ),
    )
    // The directorate's own positions occupy a separate vertical band before child departments.
    const childDepartmentY =
      root && expanded.includes(root.id)
        ? rootSeats.length
          ? 650 + rootActiveRows * 180
          : 440
        : root
          ? 210
          : 0
    const connect = (source: string, target: string, membership = false) =>
      edges.push({
        id: `${source}:${target}`,
        source,
        target,
        type: 'smoothstep',
        style: {
          stroke: '#a9a297',
          strokeWidth: 1.3,
          ...(membership ? { strokeDasharray: '4 4' } : {}),
        },
        ...(membership ? { label: 'Состав отдела' } : {}),
      })
    for (const d of [...(root ? [root] : []), ...leaves]) {
      const isRoot = d.id === root?.id
      const branchStart = isRoot ? Math.max(0, (total - widths.get(d.id)!) / 2) : cursor
      const width = widths.get(d.id)!
      const x = branchStart + (width - 240) / 2
      const y = isRoot ? 0 : childDepartmentY
      if (!isRoot) cursor += width
      nodes.push({
        id: d.id,
        type: 'org',
        position: positions[d.id] ?? { x, y },
        selected: selection?.id === d.id,
        data: {
          label: d.name,
          code: d.code,
          kind: 'department',
          detail: `${d.employee_count ?? 0} с действующим назначением · ${d.position_count ?? 0} должностей`,
          expanded: expanded.includes(d.id),
          toggle: () =>
            set({
              expanded: expanded.includes(d.id)
                ? expanded.filter((id) => id !== d.id)
                : [...expanded, d.id],
            }),
        },
      })
      if (d.parent_id && data.departments.some((p) => p.id === d.parent_id))
        connect(d.parent_id, d.id)
      if (!expanded.includes(d.id)) continue
      let positionStart = branchStart
      const pos = data.positions.filter((p) => p.department_id === d.id)
      for (const p of pos) {
        const seats = data.seats.filter((s) => s.position_id === p.id)
        const pWidth = Math.max(270, seats.length * 270)
        const px = positionStart + (pWidth - 240) / 2
        const py = y + 200
        nodes.push({
          id: p.id,
          type: 'org',
          position: positions[p.id] ?? { x: px, y: py },
          selected: selection?.id === p.id,
          data: {
            label: p.title,
            code: 'Должность',
            kind: 'position',
            detail: `${seats.length} штатных единиц`,
          },
        })
        connect(d.id, p.id)
        seats.forEach((s, si) => {
          const sx = positionStart + si * 270
          const sy = py + 170
          const active = s.appointments.filter((a) => a.active)
          const fs = data.functions.filter((f) =>
            data.assignments.some((a) => a.function_id === f.id && a.position_id === p.id),
          )
          nodes.push({
            id: s.id,
            type: 'org',
            position: positions[s.id] ?? { x: sx, y: sy },
            selected: selection?.id === s.id,
            data: {
              label: active.length ? 'Назначение действует' : 'Вакансия',
              code: s.code,
              kind: 'seat',
              detail: active.some((a) => a.kind === 'temporary')
                ? 'Оформлено временное замещение'
                : active.length
                  ? 'Постоянное назначение'
                  : 'На выбранную дату нет назначения',
              status: strongest(fs.map((f) => f.status)),
            },
          })
          connect(p.id, s.id)
          active.forEach((a, ai) => {
            const id = `employee:${a.id}`
            nodes.push({
              id,
              type: 'org',
              position: positions[id] ?? { x: sx, y: sy + 200 + ai * 180 },
              data: {
                label: a.employee_name,
                code: 'Сотрудник',
                kind: 'employee',
                detail: p.title,
                employeeId: a.employee_id,
              },
            })
            connect(s.id, id)
          })
        })
        positionStart += pWidth
      }
      unassigned(d.id).forEach((employee, index) => {
        const id = `employee-unassigned:${employee.id}`
        nodes.push({
          id,
          type: 'org',
          draggable: false,
          position: { x: positionStart + index * 270, y: y + 200 },
          data: {
            label: employee.display_name,
            code: 'Без назначения на дату',
            kind: 'employee',
            detail:
              employee.status === 'former'
                ? 'Назначение завершено · история в карточке'
                : 'Действующее назначение не зарегистрировано',
            employeeId: employee.id,
          },
        })
        // Membership in the department does not imply an appointment to a vacant seat.
        connect(d.id, id, true)
      })
    }
    return { nodes, edges }
  }, [data, directory.data, expanded, positions, selection, set])
  useEffect(() => setNodes(built.nodes), [built])
  const changes = useCallback(
    (changes: NodeChange<OrgNode>[]) => setNodes((n) => applyNodeChanges(changes, n)),
    [],
  )
  useEffect(() => {
    if (selection) {
      const timer = setTimeout(() => {
        void fitView({
          nodes: [{ id: selection.id }],
          duration: reduced ? 0 : 160,
          maxZoom: 1,
          padding: 0.6,
        })
      }, 80)
      return () => clearTimeout(timer)
    }
  }, [selection?.id, fitView, reduced, expanded])
  return (
    <>
      <div className="filter-bar">
        <SearchField
          label="Поиск по структуре"
          value={query}
          onChange={(value) => set({ query: value })}
        />
        <span className="muted small">Перемещение меняет только расположение</span>
      </div>
      {query && (
        <>
          <LoadState {...search} onRetry={() => setRetry((v) => v + 1)} />
          {search.data && (
            <div className="graph-search-results">
              <p role="status">
                {search.data.length
                  ? `Найдено сотрудников: ${search.data.length}`
                  : 'Совпадений нет'}
              </p>
              {!!search.data.length && (
                <div className="graph-search-buttons">
                  {search.data.map((employee) => (
                    <Button
                      key={employee.id}
                      variant="outline"
                      size="sm"
                      aria-label={`Открыть карточку: ${employee.display_name}`}
                      onClick={() => onEmployee(employee.id)}
                    >
                      <UserRound size={15} />
                      <span>
                        {employee.display_name}
                        <small>
                          {employee.department_name} ·{' '}
                          {employee.status === 'active'
                            ? 'Есть назначение'
                            : 'Без назначения на дату'}
                        </small>
                      </span>
                    </Button>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
      <LoadState {...directory} onRetry={() => setRetry((value) => value + 1)} />
      {directory.data && (
        <>
          <div className="graph-stage">
            <ReactFlow<OrgNode>
              nodes={nodes}
              edges={built.edges}
              onNodesChange={changes}
              nodeTypes={nodeTypes}
              fitView={!viewport}
              defaultViewport={viewport ?? undefined}
              minZoom={0.1}
              maxZoom={1.5}
              fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
              nodesConnectable={false}
              edgesReconnectable={false}
              onNodeClick={(_e, node) => {
                if (node.data.kind === 'employee') {
                  onEmployee(node.data.employeeId!)
                } else set({ selection: { kind: node.data.kind, id: node.id } })
              }}
              onNodeDragStop={(_e, node) =>
                set({ positions: { ...positions, [node.id]: node.position } })
              }
              onMoveEnd={(_e, value) => set({ viewport: value })}
              aria-label="Организационная структура"
            >
              <Background color="#d5d0c6" gap={24} size={1} />
              <Controls showInteractive={false} />
            </ReactFlow>
            <Button
              className="fit-button"
              variant="outline"
              size="sm"
              onClick={() => {
                void fitView({ duration: reduced ? 0 : 160, padding: 0.12, maxZoom: 1 })
                set({ selection: null })
              }}
            >
              <Crosshair size={16} />
              Вся структура
            </Button>
          </div>
        </>
      )}
    </>
  )
}

export function OrganizationView({
  data,
  user,
  revision,
  onSource,
  onPersonnel,
  onEmployee,
}: {
  data: Snapshot
  user: User
  revision: number
  onSource: (id: string) => void
  onPersonnel: (target: PersonnelTarget) => void
  onEmployee: (id: string) => void
}) {
  const { view, selection, set } = useUI()
  const [narrow, setNarrow] = useState(window.innerWidth < 1200)
  useEffect(() => {
    const match = window.matchMedia('(max-width:1199px)')
    const change = () => setNarrow(match.matches)
    match.addEventListener('change', change)
    return () => match.removeEventListener('change', change)
  }, [])
  const inspector = selection ? (
    <Inspector data={data} user={user} onSource={onSource} onPersonnel={onPersonnel} />
  ) : null
  return (
    <div className="organization-workspace">
      <div className="workspace-toolbar">
        <div className="view-switch" aria-label="Представление">
          <Button
            variant={view === 'table' ? 'secondary' : 'ghost'}
            aria-pressed={view === 'table'}
            onClick={() => set({ view: 'table' })}
          >
            <List size={16} />
            Таблица
          </Button>
          <Button
            variant={view === 'graph' ? 'secondary' : 'ghost'}
            aria-pressed={view === 'graph'}
            onClick={() => set({ view: 'graph' })}
          >
            <GitBranch size={16} />
            Схема
          </Button>
        </div>
        <DateField />
      </div>
      <div
        className={`workspace-body ${selection && !narrow ? 'with-inspector' : ''} ${view === 'graph' ? 'graph-mode' : ''}`}
      >
        <section className="structure-canvas" aria-label="Структура организации">
          {view === 'table' ? (
            <OrganizationDirectory
              data={data}
              revision={revision}
              onEmployee={onEmployee}
              onDepartment={(id) => set({ selection: { kind: 'department', id } })}
            />
          ) : (
            <ReactFlowProvider>
              <Canvas data={data} revision={revision} onEmployee={onEmployee} />
            </ReactFlowProvider>
          )}
        </section>
        {!narrow && inspector}
      </div>
      {narrow && (
        <Sheet
          open={!!selection}
          onOpenChange={(open) => {
            if (!open) set({ selection: null })
          }}
        >
          <SheetContent className="inspector-sheet" showCloseButton={false}>
            <SheetHeader className="sr-only">
              <SheetTitle>Инспектор структуры</SheetTitle>
              <SheetDescription>
                Назначения, функции и основания выбранного объекта
              </SheetDescription>
            </SheetHeader>
            {inspector}
          </SheetContent>
        </Sheet>
      )}
    </div>
  )
}
