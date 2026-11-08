import { useMemo } from 'react'

import { useProbes, usePrtgProbeNames } from '@/api/hooks'
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

/**
 * The shown name for a probe known only by its NATS account.
 *
 * Sensor logs, overlay peers, jobs and alerts reference a probe by that
 * account, because it is what the runtime files carry. Shown as is, the same
 * probe was "mpp-…" on one page and its display name on the next. This looks
 * the account up in the fleet and applies the same rule as the list; an
 * account it cannot place keeps its own name, since that is all there is.
 */
export function useProbeNames() {
  const { data: probes } = useProbes()
  // Without a PRTG token the answer is empty and the rule falls through to
  // the enrollment name, exactly as in the list.
  const { data: prtgNames } = usePrtgProbeNames()
  return useMemo(() => {
    const prtgNameOf = new Map(
      (prtgNames?.names ?? []).map((entry) => [entry.nats_username, entry.name_in_prtg]),
    )
    const byAccount = new Map((probes ?? []).map((probe) => [probe.nats_username, probe]))
    const nameOfProbe = (probe: ProbeSummary) =>
      probeName(probe, prtgNameOf.get(probe.nats_username))
    return {
      probes: probes ?? [],
      probeOf: (account: string) => byAccount.get(account),
      nameOfProbe,
      nameOf: (account: string) => {
        const probe = byAccount.get(account)
        return probe ? nameOfProbe(probe) : account
      },
    }
  }, [probes, prtgNames])
}
