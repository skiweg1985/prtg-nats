import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useCapabilities, useLogout } from '@/api/hooks'
import { useAuth } from '@/app/providers'
import { Badge, Button } from '@/components/ui/primitives'

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
  permission?: string
}

const PRIMARY: NavEntry[] = [
  { to: '/', labelKey: 'nav.dashboard', permission: 'system.read' },
  // Second, and above the probes: for a viewer account handed to a support
  // desk this is the only page that matters.
  { to: '/availability', labelKey: 'nav.availability', permission: 'watch.read' },
  { to: '/probes', labelKey: 'nav.probes', permission: 'probe.read' },
  { to: '/sensors', labelKey: 'nav.sensors', permission: 'sensor.read' },
  { to: '/deployments', labelKey: 'nav.deployments', permission: 'deployment.read' },
  { to: '/jobs', labelKey: 'nav.jobs', permission: 'job.read' },
]

const INFRASTRUCTURE: NavEntry[] = [
  { to: '/infrastructure/nats', labelKey: 'nav.nats', permission: 'system.read' },
  {
    to: '/infrastructure/certificates',
    labelKey: 'nav.certificates',
    permission: 'certificate.read',
  },
  { to: '/infrastructure/iperf', labelKey: 'nav.iperf', permission: 'iperf.read' },
  {
    to: '/infrastructure/overlay',
    labelKey: 'nav.overlay',
    permission: 'overlay.read',
  },
  {
    to: '/infrastructure/credentials',
    labelKey: 'nav.credentials',
    permission: 'credential.read',
  },
  { to: '/infrastructure/system', labelKey: 'nav.system', permission: 'system.read' },
]

const SECONDARY: NavEntry[] = [
  { to: '/audit', labelKey: 'nav.audit', permission: 'audit.read' },
  // Visible to anyone who may read the system: knowing which version is
  // installed is not a privileged question. Only the button behind it is.
  { to: '/updates', labelKey: 'nav.updates', permission: 'system.read' },
  { to: '/settings', labelKey: 'nav.settings' },
]

export function AppLayout() {
  const { t } = useTranslation()
  const { principal } = useAuth()
  const { data: capabilities } = useCapabilities()
  const logout = useLogout()
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

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
      <aside className="border-rule bg-surface sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r md:flex">
        <div className="border-rule border-b px-4 py-3.5">
          <p className="font-display text-ink text-sm font-semibold tracking-(--tracking-display)">
            {t('app.name')}
          </p>
          <p className="text-ink-3 text-xs">{t('app.tagline')}</p>
        </div>

        <nav className="flex-1 overflow-y-auto px-2 py-3">
          <NavSections />
        </nav>

        {capabilities && !capabilities.docker && (
          <p className="text-ink-3 border-rule border-t px-4 py-2.5 text-xs">
            {t('settings.dockerUnavailable')}
          </p>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-rule bg-surface sticky top-0 z-(--z-header) flex items-center gap-3 border-b px-4 py-2">
          {/* Everything below md: reaches its destinations through here. The
              rail is hidden at that width, and the row of primary entries this
              replaces left infrastructure, the audit trail, updates and the
              settings reachable only by typing the address. */}
          <Button
            size="sm"
            variant="ghost"
            className="md:hidden"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-expanded={menuOpen}
            aria-controls="app-menu"
          >
            <span aria-hidden>☰</span> {t('nav.menu')}
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
            className="border-rule bg-surface border-b px-2 py-3 md:hidden"
          >
            <NavSections />
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
function NavSections() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const visible = (entries: NavEntry[]) =>
    entries.filter((entry) => !entry.permission || can(entry.permission))

  return (
    <>
      <NavGroup entries={visible(PRIMARY)} />

      {visible(INFRASTRUCTURE).length > 0 && (
        <>
          <p className="label-mono mt-5 mb-1.5 px-2">{t('nav.infrastructure')}</p>
          <NavGroup entries={visible(INFRASTRUCTURE)} />
        </>
      )}

      <div className="border-rule mt-5 border-t pt-3">
        <NavGroup entries={visible(SECONDARY)} />
      </div>
    </>
  )
}

function NavGroup({ entries }: { entries: NavEntry[] }) {
  return (
    <ul className="space-y-0.5">
      {entries.map((entry) => (
        <li key={entry.to}>
          <NavItem entry={entry} />
        </li>
      ))}
    </ul>
  )
}

function NavItem({ entry }: { entry: NavEntry }) {
  const { t } = useTranslation()
  return (
    <NavLink
      to={entry.to}
      end={entry.to === '/'}
      className={({ isActive }) =>
        clsx(
          'rounded-control block px-2.5 py-1.5 text-sm transition-colors duration-100',
          isActive
            ? 'bg-accent-soft text-accent font-medium'
            : 'text-ink-2 hover:bg-surface-2 hover:text-ink',
        )
      }
    >
      {t(entry.labelKey)}
    </NavLink>
  )
}
