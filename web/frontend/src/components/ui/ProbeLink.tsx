import { Link } from 'react-router-dom'

import { useProbes } from '@/api/hooks'

import { Mono } from './primitives'

/**
 * One probe named by its NATS account, as a way to get to it.
 *
 * Everything that references a probe from the outside - a sensor, an iperf
 * endpoint, the runtime sidecars - knows it by that account, while the route
 * wants a record id. The fleet listing supplies the missing half.
 *
 * A name it cannot place stays plain text rather than becoming a link to
 * nothing: a probe unenrolled since the reference was written is a real state,
 * and a dead link would hide it.
 */
export function ProbeLink({ username }: { username: string }) {
  const { data } = useProbes()
  const probe = data?.find((entry) => entry.nats_username === username)
  if (!probe) return <Mono>{username}</Mono>
  return (
    <Link to={`/probes/${probe.id}`} className="hover:underline">
      <Mono>{username}</Mono>
    </Link>
  )
}
