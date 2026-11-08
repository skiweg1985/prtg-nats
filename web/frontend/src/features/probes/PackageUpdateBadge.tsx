import { useTranslation } from 'react-i18next'

import { Badge } from '@/components/ui/primitives'

/**
 * A newer probe package than the installed one, as far as the fleet knows.
 *
 * Accent rather than warn: an older package still measures. It is news for
 * whoever plans the next upgrade, not a fault on the probe.
 */
export function PackageUpdateBadge({ version }: { version?: string | null }) {
  const { t } = useTranslation()
  if (!version) return null
  return <Badge tone="accent">{t('probes.mppUpgrade.badge', { version })}</Badge>
}
