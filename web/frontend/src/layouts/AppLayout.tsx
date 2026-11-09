import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ArrowUpRight,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  type LucideIcon,
} from 'lucide-react'

import { useLogout } from '@/api/hooks'
import { OBJECT_ICONS } from '@/app/icons'
import { useLastSearch, useNavigationTracking } from '@/app/navigation'
import { useAuth } from '@/app/providers'
import { Badge, Button, IconButton } from '@/components/ui/primitives'
import { guideHref } from '@/utils/guide'

/**
 * The frame. A single left rail with the object types, a thin header for
 * identity and preferences, and everything else in the content area.
 *
 * Two levels of navigation, no more: an administrator looking for the probe
 * list should not have to remember which submenu it lives under.
 */

interface NavEntry {
  to: string
  labelKey: string
  icon: LucideIcon
  permission?: string
}

const PRIMARY: NavEntry[] = [
  { to: '/', labelKey: 'nav.dashboard', icon: OBJECT_ICONS.dashboard, permission: 'system.read' },
  { to: '/probes', labelKey: 'nav.probes', icon: OBJECT_ICONS.probe, permission: 'probe.read' },
  { to: '/sensors', labelKey: 'nav.sensors', icon: OBJECT_ICONS.sensor, permission: 'sensor.read' },
  { to: '/jobs', labelKey: 'nav.jobs', icon: OBJECT_ICONS.job, permission: 'job.read' },
  {
    to: '/sensor-logs',
    labelKey: 'nav.sensorLogs',
    icon: OBJECT_ICONS.sensorLog,
    permission: 'sensor_log.read',
  },
]

const INFRASTRUCTURE: NavEntry[] = [
  {
    to: '/infrastructure/nats',
    labelKey: 'nav.nats',
    icon: OBJECT_ICONS.nats,
    permission: 'system.read',
  },
  {
    to: '/infrastructure/certificates',
    labelKey: 'nav.certificates',
    icon: OBJECT_ICONS.certificate,
    permission: 'certificate.read',
  },
  {
    to: '/infrastructure/iperf',
    labelKey: 'nav.iperf',
    icon: OBJECT_ICONS.iperf,
    permission: 'iperf.read',
  },
  {
    to: '/infrastructure/overlay',
    labelKey: 'nav.overlay',
    icon: OBJECT_ICONS.overlay,
    permission: 'overlay.read',
  },
  // Reached from the NATS page before, and from nowhere else: an account
  // nobody could find was an account nobody cleaned up.
  {
    to: '/infrastructure/credentials',
    labelKey: 'nav.credentials',
    icon: OBJECT_ICONS.credential,
    permission: 'credential.read',
  },
  {
    to: '/infrastructure/maintenance',
    labelKey: 'nav.system',
    icon: OBJECT_ICONS.system,
    permission: 'system.read',
  },
]

const SECONDARY: NavEntry[] = [
  { to: '/audit', labelKey: 'nav.audit', icon: OBJECT_ICONS.audit, permission: 'audit.read' },
  // Visible to anyone who may read the system: knowing which version is
  // installed is not a privileged question. Only the button behind it is.
  {
    to: '/updates',
    labelKey: 'nav.updates',
    icon: OBJECT_ICONS.update,
    permission: 'system.read',
  },
  { to: '/settings', labelKey: 'nav.settings', icon: OBJECT_ICONS.settings },
]

// The pages the sidebar opens, whose last address it restores.
const NAV_PATHS = new Set([...PRIMARY, ...INFRASTRUCTURE, ...SECONDARY].map((entry) => entry.to))
const isNavPath = (pathname: string) => NAV_PATHS.has(pathname)

// Per browser, not per tab: how wide someone likes the rail is a habit of
// theirs, not a state of one visit.
const COLLAPSED_KEY = 'prtg-nats.sidebar-collapsed'

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

function useCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(readCollapsed)
  function toggle() {
    const next = !collapsed
    setCollapsed(next)
    try {
      window.localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0')
    } catch {
      // Blocked storage: the rail still folds, it just forgets on reload.
    }
  }
  return [collapsed, toggle]
}

export function AppLayout() {
  const { t } = useTranslation()
  const { principal } = useAuth()
  const logout = useLogout()
  const [menuOpen, setMenuOpen] = useState(false)
  const [collapsed, toggleCollapsed] = useCollapsed()
  const location = useLocation()
  useNavigationTracking(isNavPath)

  // Arriving somewhere is the end of navigating there.
  useEffect(() => setMenuOpen(false), [location.pathname])

  useEffect(() => {
    if (!menuOpen) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [menuOpen])

  return (
    <div className="bg-paper flex min-h-screen">
      <aside
        className={clsx(
          'border-rule bg-surface sticky top-0 hidden h-screen shrink-0 flex-col border-r md:flex',
          collapsed ? 'w-14' : 'w-56',
        )}
      >
        <div
          className={clsx(
            'border-rule flex h-12 shrink-0 items-center border-b',
            collapsed ? 'justify-center' : 'justify-between gap-2 pr-2 pl-4',
          )}
        >
          {!collapsed && (
            <div className="min-w-0 leading-tight">
              <p className="font-display text-ink truncate text-sm font-semibold tracking-(--tracking-display)">
                {t('app.name')}
              </p>
              <p className="text-ink-3 truncate text-xs">{t('app.tagline')}</p>
            </div>
          )}
          <IconButton
            icon={collapsed ? PanelLeftOpen : PanelLeftClose}
            label={collapsed ? t('nav.expand') : t('nav.collapse')}
            onClick={toggleCollapsed}
            aria-expanded={!collapsed}
          />
        </div>

        {/* Folded, the rail is short enough to need no scrolling, and must
            not clip the labels that float out of it. */}
        <nav
          aria-label={t('nav.label')}
          className={clsx('flex-1 py-3', collapsed ? 'px-2' : 'overflow-y-auto px-2')}
        >
          <NavSections collapsed={collapsed} />
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-rule bg-surface sticky top-0 z-(--z-header) flex h-12 shrink-0 items-center gap-3 border-b px-4">
          {/* Everything below md: reaches its destinations through here. The
              rail is hidden at that width, and the row of primary entries this
              replaces left infrastructure, the audit trail, updates and the
              settings reachable only by typing the address. */}
          <Button
            size="sm"
            variant="ghost"
            icon={Menu}
            className="-ml-2 md:hidden"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-expanded={menuOpen}
            aria-controls="app-menu"
          >
            {t('nav.menu')}
          </Button>

          <div className="ml-auto flex items-center gap-2">
            {principal && (
              <>
                <span className="text-ink-3 hidden text-sm sm:inline">
                  {principal.display_name}
                </span>
                {principal.is_development && <Badge tone="warn">dev</Badge>}
                <Button
                  size="sm"
                  variant="ghost"
                  icon={LogOut}
                  className="-mr-2"
                  onClick={() => logout.mutate()}
                  disabled={principal.is_development}
                >
                  {t('auth.signOut')}
                </Button>
              </>
            )}
          </div>
        </header>

        {menuOpen && (
          <nav
            id="app-menu"
            aria-label={t('nav.label')}
            className="border-rule bg-surface border-b px-2 py-3 md:hidden"
          >
            <NavSections collapsed={false} />
          </nav>
        )}

        {principal?.is_development && (
          <p className="bg-warn-soft text-warn border-warn/25 border-b px-4 py-1.5 text-xs">
            {t('auth.developmentBanner')}
          </p>
        )}

        <main className="min-w-0 flex-1 px-4 py-5 lg:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

/**
 * The whole of the navigation, rendered by the rail and by the narrow-screen
 * menu alike. Two copies of this list is how one of them ends up missing half
 * the destinations, which is exactly what happened.
 */
function NavSections({ collapsed }: { collapsed: boolean }) {
  const { t } = useTranslation()
  const { can } = useAuth()
  const visible = (entries: NavEntry[]) =>
    entries.filter((entry) => !entry.permission || can(entry.permission))

  return (
    <>
      <NavGroup entries={visible(PRIMARY)} collapsed={collapsed} />

      {visible(INFRASTRUCTURE).length > 0 && (
        <>
          {/* Folded, the heading has no room; a rule keeps the grouping. */}
          {collapsed ? (
            <hr className="border-rule mx-1.5 my-3" />
          ) : (
            <p className="text-ink-3 mt-5 mb-1 px-2.5 text-xs font-medium">
              {t('nav.infrastructure')}
            </p>
          )}
          <NavGroup entries={visible(INFRASTRUCTURE)} collapsed={collapsed} />
        </>
      )}

      <div className={clsx('border-rule border-t', collapsed ? 'mx-1.5 mt-3 pt-3' : 'mt-5 pt-3')}>
        <NavGroup entries={visible(SECONDARY)} collapsed={collapsed} />
        <GuideLink collapsed={collapsed} />
      </div>
    </>
  )
}

const NAV_ITEM =
  'group rounded-control relative flex h-10 items-center gap-2.5 md:h-8 text-sm transition-colors duration-(--dur-instant) ease-(--ease-out)'

/** The entry's name beside a folded rail, on hover and keyboard focus. The
    accessible name is the label inside the link; this copy is for the eye. */
function NavTooltip({ label }: { label: string }) {
  return (
    <span
      aria-hidden
      className={clsx(
        'border-rule bg-surface text-ink rounded-control pointer-events-none absolute top-1/2 left-full z-(--z-popover) ml-2 -translate-y-1/2',
        'border px-2 py-1 text-xs font-medium whitespace-nowrap opacity-0 shadow-md',
        'transition-opacity duration-(--dur-instant) group-hover:opacity-100 group-focus-visible:opacity-100',
      )}
    >
      {label}
    </span>
  )
}

/**
 * The guide is a separate set of static pages, not a route of this
 * application, so it is a plain link in a new tab rather than a NavLink: the
 * reader keeps the page they were on and reads beside it.
 */
function GuideLink({ collapsed }: { collapsed: boolean }) {
  const { t, i18n } = useTranslation()
  const location = useLocation()
  const Icon = OBJECT_ICONS.guide
  return (
    <a
      href={guideHref(location.pathname, i18n.resolvedLanguage)}
      target="_blank"
      rel="noopener"
      className={clsx(
        NAV_ITEM,
        'text-ink-2 hover:bg-hover hover:text-ink mt-0.5',
        collapsed ? 'justify-center' : 'px-2.5',
      )}
    >
      <Icon aria-hidden size={16} strokeWidth={1.75} className="text-ink-3 group-hover:text-ink-2 shrink-0" />
      <span className={collapsed ? 'sr-only' : 'min-w-0 flex-1 truncate'}>{t('nav.guide')}</span>
      {!collapsed && (
        <ArrowUpRight aria-hidden size={14} strokeWidth={1.75} className="text-ink-3 shrink-0" />
      )}
      {collapsed && <NavTooltip label={t('nav.guide')} />}
    </a>
  )
}

function NavGroup({ entries, collapsed }: { entries: NavEntry[]; collapsed: boolean }) {
  return (
    <ul className="space-y-0.5">
      {entries.map((entry) => (
        <li key={entry.to}>
          <NavItem entry={entry} collapsed={collapsed} />
        </li>
      ))}
    </ul>
  )
}

function NavItem({ entry, collapsed }: { entry: NavEntry; collapsed: boolean }) {
  const { t } = useTranslation()
  // The page as it was last left in this browser tab: coming back to the
  // probe list after a look at one probe keeps its filter.
  const lastSearch = useLastSearch(entry.to)
  const label = t(entry.labelKey)
  const Icon = entry.icon
  return (
    <NavLink
      to={`${entry.to}${lastSearch}`}
      end={entry.to === '/'}
      className={({ isActive }) =>
        clsx(
          NAV_ITEM,
          collapsed ? 'justify-center' : 'px-2.5',
          // The wash and the blue icon mark where you are; the label stays
          // ink, so the active entry reads as a place, not as a button.
          isActive
            ? 'bg-accent-soft text-ink font-medium'
            : 'text-ink-2 hover:bg-hover hover:text-ink',
        )
      }
    >
      {({ isActive }) => (
        <>
          <Icon
            aria-hidden
            size={16}
            strokeWidth={1.75}
            className={clsx(
              'shrink-0',
              isActive ? 'text-accent' : 'text-ink-3 group-hover:text-ink-2',
            )}
          />
          <span className={collapsed ? 'sr-only' : 'min-w-0 truncate'}>{label}</span>
          {collapsed && <NavTooltip label={label} />}
        </>
      )}
    </NavLink>
  )
}
