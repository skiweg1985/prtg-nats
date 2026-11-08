import { Link } from 'react-router-dom'

import { useProbeNames } from '@/features/probes/probeName'

import { Mono } from './primitives'

/**
 * One probe, referenced by its NATS account, shown by its name.
 *
 * Everything that references a probe from the outside - a sensor, an iperf
 * endpoint, the runtime sidecars - knows it by that account, while the route
 * wants a record id and the reader wants the name the list shows. The fleet
 * listing supplies both.
 *
 * `account` adds the NATS account as a second, quieter line where a page is
 * about accounts (the overlay peers); elsewhere the name alone is the probe.
 *
 * A name it cannot place stays plain text rather than becoming a link to
 * nothing: a probe unenrolled since the reference was written is a real state,
 * and a dead link would hide it.
 */
export function ProbeLink({ username, account = false }: { username: string; account?: boolean }) {
  const { probeOf, nameOf } = useProbeNames()
  const probe = probeOf(username)
  if (!probe) return <Mono>{username}</Mono>
  const name = nameOf(username)
  return (
    <span className="inline-flex min-w-0 flex-col">
      <Link to={`/probes/${probe.id}`} className="text-ink truncate hover:underline">
        {name}
      </Link>
      {account && name !== username && <Mono className="text-ink-3">{username}</Mono>}
    </span>
  )
}
