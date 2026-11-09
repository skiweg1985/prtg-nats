import { ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { Alert, Dashboard } from '@/api/types'
import { AttentionTitle, Card, Dot } from '@/components/ui/primitives'
import { useProbeNames } from '@/features/probes/probeName'

/** More names than this and the row stops being readable at a glance. */
const NAMES_SHOWN = 3

export interface AttentionItem {
  key: string
  tone: 'danger' | 'warn'
  text: string
  /** Who it is about, when there is more than one and they are known. */
  names: string[]
  /** Where the rows behind it are. Absent only for a kind of warning this
      interface does not know a page for. */
  to?: string
}

/**
 * Everything on the dashboard that wants somebody to act, one row per kind.
 *
 * Counts come from the live status, the same numbers the probe list shows.
 * The stored warnings only add names to a row, and only when they agree with
 * the count: they are written by the inventory sync and can trail a probe
 * that has just recovered or just dropped out, and a row naming the wrong
 * probe is worse than one naming none.
 */
export function useAttentionItems(dashboard: Dashboard | undefined): AttentionItem[] {
  const { t } = useTranslation()
  const { nameOf, probeOf } = useProbeNames()
  if (!dashboard) return []

  const alertsOf = (kind: string) => dashboard.alerts.filter((alert) => alert.kind === kind)
  const probeLink = (alert: Alert) => {
    const probe = probeOf(alert.object_ref)
    return probe ? `/probes/${probe.id}` : undefined
  }

  const items: AttentionItem[] = []

  // One probe, one named alert: the alert's own sentence and the probe's own
  // page. Several: a count, the names, and the filtered list.
  function probeGroup(
    key: string,
    tone: AttentionItem['tone'],
    kind: string,
    count: number,
    listLink: string,
  ) {
    if (count === 0) return
    const alerts = alertsOf(kind)
    const named = alerts.length === count
    const only = named && count === 1 ? alerts[0] : undefined
    if (only) {
      const probe = nameOf(only.object_ref)
      items.push({
        key,
        tone,
        text: t(`alerts.${kind}`, { ...only.params, probe }),
        names: [],
        to: probeLink(only) ?? listLink,
      })
      return
    }
    items.push({
      key,
      tone,
      text: t(`dashboard.attention.${key}`, { count }),
      names: named ? alerts.map((alert) => nameOf(alert.object_ref)) : [],
      to: listLink,
    })
  }

  probeGroup(
    'unreachable',
    'danger',
    'probe.unreachable',
    dashboard.probe_unreachable,
    '/probes?status=unreachable',
  )

  const certificates = '/infrastructure/certificates'
  const expired = dashboard.expiring_certificates.filter((c) => c.status === 'expired')
  const expiring = dashboard.expiring_certificates.filter((c) => c.status !== 'expired')
  const mismatched = alertsOf('certificate.mismatched')
  if (expired.length > 0) {
    items.push({
      key: 'certificatesExpired',
      tone: 'danger',
      text: t('dashboard.attention.certificatesExpired', { count: expired.length }),
      names: expired.map((c) => c.subject ?? c.kind),
      to: certificates,
    })
  }
  if (mismatched.length > 0) {
    items.push({
      key: 'certificatesMismatched',
      tone: 'danger',
      text: t('dashboard.attention.certificatesMismatched', { count: mismatched.length }),
      names: mismatched.map((alert) => alert.object_label),
      to: certificates,
    })
  }

  if (dashboard.failed_jobs_24h > 0) {
    items.push({
      key: 'failedJobs',
      tone: 'danger',
      text: t('dashboard.attention.failedJobs', { count: dashboard.failed_jobs_24h }),
      names: [],
      to: '/jobs?status=failed',
    })
  }

  probeGroup(
    'degraded',
    'warn',
    'probe.degraded',
    dashboard.probe_degraded,
    '/probes?status=degraded',
  )

  // No live count to measure against: the warning is the only source.
  const silent = alertsOf('probe.channel_silent')
  probeGroup('channelSilent', 'warn', 'probe.channel_silent', silent.length, '/probes')

  if (expiring.length > 0) {
    items.push({
      key: 'certificatesExpiring',
      tone: 'warn',
      text: t('dashboard.attention.certificatesExpiring', { count: expiring.length }),
      names: expiring.map((c) => c.subject ?? c.kind),
      to: certificates,
    })
  }

  if (dashboard.probe_pending > 0) {
    items.push({
      key: 'pending',
      tone: 'warn',
      text: t('dashboard.attention.pending', { count: dashboard.probe_pending }),
      names: [],
      to: '/probes?status=pending&status=enrolled',
    })
  }
  if (dashboard.probe_prtg_missing > 0) {
    items.push({
      key: 'prtgMissing',
      tone: 'warn',
      text: t('dashboard.attention.prtgMissing', { count: dashboard.probe_prtg_missing }),
      names: [],
      to: '/probes?filter=prtg',
    })
  }

  // A kind this interface has no row for still deserves to be seen; it
  // keeps its own sentence and goes nowhere rather than somewhere wrong.
  const known = new Set([
    'probe.unreachable',
    'probe.degraded',
    'probe.channel_silent',
    'certificate.expired',
    'certificate.expiring_soon',
    'certificate.mismatched',
  ])
  for (const alert of dashboard.alerts) {
    if (known.has(alert.kind)) continue
    const label = alert.object_type === 'probe' ? nameOf(alert.object_ref) : alert.object_label
    items.push({
      key: `alert-${alert.id}`,
      tone: alert.severity === 'critical' ? 'danger' : 'warn',
      text: t(`alerts.${alert.kind}`, {
        ...alert.params,
        probe: label,
        certificate: label,
        defaultValue: alert.kind,
      }),
      names: [],
    })
  }

  // Faults before warnings. The rows above are pushed in that order already;
  // the sort catches the warnings of unknown kinds, which arrive last with
  // either severity. Stable, so the order within a severity stays.
  return items.sort((a, b) => Number(a.tone === 'warn') - Number(b.tone === 'warn'))
}

export function AttentionList({ items }: { items: AttentionItem[] }) {
  const { t } = useTranslation()
  const tone = items.some((item) => item.tone === 'danger') ? 'danger' : 'warn'
  return (
    <Card
      title={
        <AttentionTitle count={items.length} tone={tone}>
          {t('dashboard.attention.title')}
        </AttentionTitle>
      }
      dense
    >
      <ul className="divide-rule divide-y">
        {items.map((item) => (
          <li key={item.key}>
            <AttentionRow item={item} />
          </li>
        ))}
      </ul>
    </Card>
  )
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const { t } = useTranslation()
  const shown = item.names.slice(0, NAMES_SHOWN)
  const hidden = item.names.length - shown.length
  const body = (
    <>
      <Dot tone={item.tone} />
      {/* The names beside the sentence, not under it: one line per row
          keeps the list as short as the problems are few. */}
      <span className="min-w-0 flex-1 text-sm sm:flex sm:items-baseline sm:gap-2">
        <span className="text-ink shrink-0">{item.text}</span>
        {shown.length > 0 && (
          <span className="text-ink-3 block min-w-0 truncate text-xs sm:text-sm">
            {shown.join(', ')}
            {hidden > 0 && ` ${t('dashboard.attention.more', { count: hidden })}`}
          </span>
        )}
      </span>
      {item.to && (
        <ChevronRight
          aria-hidden
          size={16}
          strokeWidth={1.75}
          className="text-ink-3 group-hover:text-ink-2 shrink-0"
        />
      )}
    </>
  )
  const row = 'flex min-h-10 items-center gap-3 px-4 py-2'
  return item.to ? (
    <Link
      to={item.to}
      className={`group ${row} hover:bg-hover transition-colors duration-(--dur-instant)`}
    >
      {body}
    </Link>
  ) : (
    <div className={row}>{body}</div>
  )
}
