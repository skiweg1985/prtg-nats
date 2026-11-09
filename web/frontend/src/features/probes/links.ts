/** One sensor on one probe: its deployment and its runs. */
export function probeSensorHref(probeId: string, sensor: string): string {
  return `/probes/${probeId}/sensors/${encodeURIComponent(sensor)}`
}
