import clsx from 'clsx'
import { NavLink, Outlet } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useCapabilities, useLogout } from '@/api/hooks'
import { useAuth, useTheme } from '@/app/providers'
import { Badge, Button } from '@/components/ui/primitives'
import {
  LANGUAGE_LABELS,
  SUPPORTED_LANGUAGES,
  changeLanguage,
  currentLanguage,
  type Language,
} from '@/i18n'

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
    to: '/infrastructure/credentials',
    labelKey: 'nav.credentials',
    permission: 'credential.read',
  },
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
  const { principal, can } = useAuth()
  const { data: capabilities } = useCapabilities()
  const logout = useLogout()

  const visible = (entries: NavEntry[]) =>
    entries.filter((entry) => !entry.permission || can(entry.permission))

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
        </nav>

        {capabilities && !capabilities.docker && (
          <p className="text-ink-3 border-rule border-t px-4 py-2.5 text-xs">
            {t('settings.dockerUnavailable')}
          </p>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-rule bg-surface sticky top-0 z-(--z-header) flex items-center gap-3 border-b px-4 py-2">
          <nav className="flex gap-1 md:hidden">
            {visible(PRIMARY).map((entry) => (
              <NavItem key={entry.to} entry={entry} compact />
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <LanguageSwitcher />
            <ThemeSwitcher />
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

function NavItem({ entry, compact }: { entry: NavEntry; compact?: boolean }) {
  const { t } = useTranslation()
  return (
    <NavLink
      to={entry.to}
      end={entry.to === '/'}
      className={({ isActive }) =>
        clsx(
          'rounded-control block px-2.5 py-1.5 text-sm transition-colors duration-100',
          compact && 'px-2 py-1 text-xs',
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

function LanguageSwitcher() {
  const { t } = useTranslation()
  return (
    <select
      value={currentLanguage()}
      onChange={(event) => changeLanguage(event.target.value as Language)}
      aria-label={t('settings.language')}
      className="rounded-control border-rule-2 bg-surface text-ink-2 border px-2 py-1 text-xs"
    >
      {SUPPORTED_LANGUAGES.map((language) => (
        <option key={language} value={language}>
          {LANGUAGE_LABELS[language]}
        </option>
      ))}
    </select>
  )
}

function ThemeSwitcher() {
  const { t } = useTranslation()
  const { choice, setChoice } = useTheme()
  return (
    <select
      value={choice}
      onChange={(event) => setChoice(event.target.value as 'light' | 'dark' | 'system')}
      aria-label={t('settings.theme')}
      className="rounded-control border-rule-2 bg-surface text-ink-2 border px-2 py-1 text-xs"
    >
      <option value="system">{t('settings.themeSystem')}</option>
      <option value="light">{t('settings.themeLight')}</option>
      <option value="dark">{t('settings.themeDark')}</option>
    </select>
  )
}
