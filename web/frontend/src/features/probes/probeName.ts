import type { ProbeSummary } from '@/api/types'

/**
 * The one name a probe is shown under.
 *
 * A display name chosen here wins, because somebody chose it. After that the
 * name PRTG uses today, because PRTG is where probes are named - and only
 * when the probe is linked and the name could be read. The name stored at
 * enrollment is the fallback for an unlinked probe, the NATS account the
 * last resort. One rule for the page title and the list, so a probe is not
 * called one thing in one place and another in the next.
 */
export function probeName(
  probe: Pick<ProbeSummary, 'display_name' | 'probe_name' | 'nats_username'>,
  nameInPrtg?: string | null,
): string {
  return probe.display_name ?? nameInPrtg ?? probe.probe_name ?? probe.nats_username
}
