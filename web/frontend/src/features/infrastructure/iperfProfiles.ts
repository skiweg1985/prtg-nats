import type { IperfEndpoint } from '@/api/types'

/**
 * How many registered endpoints each probe holds.
 *
 * The number is the whole rule. At one, that probe also carries the "default"
 * profile alias: the sensor reads address, port and user out of it, and a
 * sensor object in PRTG needs no connection parameter at all. From two on the
 * alias is removed, and every object there has to name its own profile.
 *
 * Which makes crossing that threshold the moment worth warning about, and this
 * the function that sees it coming.
 */
export function endpointsHeldByProbe(
  endpoints: IperfEndpoint[] | undefined,
): Map<string, number> {
  const held = new Map<string, number>()
  for (const endpoint of endpoints ?? []) {
    for (const holder of endpoint.holders) {
      // Taken from the holder rather than counted here: the server works the
      // same number out for the alias, and two answers could drift.
      held.set(holder.probe, holder.endpoints_held)
    }
  }
  return held
}
