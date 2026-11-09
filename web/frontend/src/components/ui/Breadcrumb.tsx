import { Fragment } from 'react'
import { Link, matchPath } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronRight } from 'lucide-react'

import { useOriginChain, type CrumbState, type Visit } from '@/app/navigation'
import { useProbeNames } from '@/features/probes/probeName'

/** One level above the current page: where it leads and what it is called. */
export interface Crumb {
  to: string
  label: string
}

interface ResolvedCrumb {
  to: string
  label: string
  state: CrumbState
}

/**
 * The way back from a detail page, every level a link.
 *
 * `trail` is the page's place in the hierarchy, from the top down, without
 * the page itself. Which way is shown depends on how the page was reached:
 *
 * - Opened from a page of that hierarchy, the hierarchy is shown, and every
 *   level the user passed through leads back to it as they left it - with
 *   its filters, its tab, its time window.
 * - Opened from somewhere else - the sensor log, the dashboard, a job - the
 *   breadcrumb names that page and leads back to it as it was.
 * - Opened without a known origin, from a link or a reload of one, it is the
 *   plain hierarchy.
 *
 * `current` names the page itself at the end, where the heading does not:
 * a run is headed by its sensor and network, so "Run 12:04" says which one.
 * Where the heading is the name, repeating it is noise.
 */
export function Breadcrumb({ trail, current }: { trail: Crumb[]; current?: string }) {
  const { t } = useTranslation()
  const chain = useOriginChain()
  const labelOf = useVisitLabel()
  const crumbs = resolveCrumbs(trail, chain, labelOf)

  return (
    <nav aria-label={t('common.breadcrumb')}>
      <ol className="text-ink-3 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-xs">
        {crumbs.map((crumb, index) => (
          <Fragment key={`${crumb.to}|${crumb.label}`}>
            {index > 0 && <Separator />}
            <li className="min-w-0">
              <Link
                to={crumb.to}
                state={crumb.state}
                className="hover:text-ink block truncate transition-colors duration-(--dur-instant)"
              >
                {crumb.label}
              </Link>
            </li>
          </Fragment>
        ))}
        {current && (
          <>
            <Separator />
            <li aria-current="page" className="text-ink-2 min-w-0 truncate">
              {current}
            </li>
          </>
        )}
      </ol>
    </nav>
  )
}

function Separator() {
  return (
    <li aria-hidden className="text-ink-3 flex">
      <ChevronRight size={12} strokeWidth={1.75} />
    </li>
  )
}

function href(visit: Visit): string {
  return `${visit.pathname}${visit.search}`
}

function pathOf(to: string): string {
  return to.split(/[?#]/)[0] ?? to
}

/** The crumbs to show; pure, so the three cases can be tested on their own. */
export function resolveCrumbs(
  trail: Crumb[],
  chain: Visit[],
  labelOf: (visit: Visit) => string,
): ResolvedCrumb[] {
  const origin = chain[0]
  const inTrail = origin && trail.some((crumb) => pathOf(crumb.to) === origin.pathname)

  if (origin && !inTrail) {
    return [{ to: href(origin), label: labelOf(origin), state: { crumb: origin.key } }]
  }

  return trail.map((crumb) => {
    // The level as the user left it, if they passed through it.
    const visited = chain.find((visit) => visit.pathname === pathOf(crumb.to))
    return visited
      ? { to: href(visited), label: crumb.label, state: { crumb: visited.key } }
      : { to: crumb.to, label: crumb.label, state: { crumb: null } }
  })
}

// List pages are named after their sidebar entry.
const PAGE_LABELS: Record<string, string> = {
  '/': 'nav.dashboard',
  '/probes': 'nav.probes',
  '/sensors': 'nav.sensors',
  '/jobs': 'nav.jobs',
  '/sensor-logs': 'nav.sensorLogs',
  '/infrastructure/nats': 'nav.nats',
  '/infrastructure/certificates': 'nav.certificates',
  '/infrastructure/iperf': 'nav.iperf',
  '/infrastructure/overlay': 'nav.overlay',
  '/infrastructure/credentials': 'nav.credentials',
  '/infrastructure/maintenance': 'nav.system',
  '/audit': 'nav.audit',
  '/updates': 'nav.updates',
  '/settings': 'nav.settings',
}

/** What an origin is called in a breadcrumb. */
function useVisitLabel(): (visit: Visit) => string {
  const { t } = useTranslation()
  const { probes, nameOfProbe } = useProbeNames()

  return (visit) => {
    const page = PAGE_LABELS[visit.pathname]
    if (page) return t(page)
    const probeSensor = matchPath('/probes/:probeId/sensors/:sensor', visit.pathname)
    if (probeSensor) return probeSensor.params.sensor ?? visit.pathname
    const probe = matchPath('/probes/:probeId', visit.pathname)
    if (probe) {
      const summary = probes.find((entry) => entry.id === probe.params.probeId)
      return summary ? nameOfProbe(summary) : t('nav.probes')
    }
    if (matchPath('/sensor-logs/:recordId', visit.pathname)) return t('breadcrumb.run')
    if (matchPath('/jobs/:jobId', visit.pathname)) return t('breadcrumb.job')
    const named =
      matchPath('/sensors/:name', visit.pathname) ??
      matchPath('/infrastructure/iperf/:name', visit.pathname)
    if (named) return named.params.name ?? visit.pathname
    return visit.pathname
  }
}
