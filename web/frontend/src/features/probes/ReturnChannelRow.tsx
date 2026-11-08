import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useRepairReturnChannel } from '@/api/hooks'
import type { ReturnChannel } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Badge, Button, DetailRow } from '@/components/ui/primitives'
import { formatPercent, formatRelative } from '@/utils/format'

/**
 * The probe's return channel in one row: when it last delivered, how full its
 * buffer is, the gaps it reported, and a warning once it has gone silent.
 *
 * Left out for a probe without a channel, the way the overlay row is: a line
 * saying 'none' on every probe enrolled before the channel existed would be
 * noise. The repair is the job the platform also starts on its own.
 */
export function ReturnChannelRow({
  probeId,
  channel,
}: {
  probeId: string
  channel: ReturnChannel | null | undefined
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const repair = useRepairReturnChannel()

  if (!channel) return null

  const fill =
    channel.buffer_bytes != null && channel.buffer_limit
      ? formatPercent(channel.buffer_bytes / channel.buffer_limit)
      : null

  return (
    <DetailRow label={t('probes.returnChannel.label')}>
      <span className='block'>
        <span className='flex flex-wrap items-center gap-2'>
          <span>
            {channel.last_contact_at
              ? t('probes.returnChannel.lastReport', {
                  time: formatRelative(channel.last_contact_at),
                })
              : t('probes.returnChannel.nothingYet')}
          </span>
          {channel.silent && (
            <Badge tone='warn'>{t('probes.returnChannel.silent')}</Badge>
          )}
          <PermissionGate permission='probe.update'>
            <Button
              size='sm'
              variant='ghost'
              onClick={() =>
                repair.mutate(probeId, {
                  onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                })
              }
              disabled={repair.isPending}
            >
              {t('probes.returnChannel.repair')}
            </Button>
          </PermissionGate>
        </span>
        {channel.last_contact_at && (
          <span className='text-ink-3 block text-xs'>
            {fill != null && (
              <>{t('probes.returnChannel.buffer', { percent: fill })} · </>
            )}
            {channel.gap_count > 0 && channel.last_gap_at
              ? t('probes.returnChannel.gaps', {
                  count: channel.gap_count,
                  time: formatRelative(channel.last_gap_at),
                })
              : t('probes.returnChannel.noGaps')}
          </span>
        )}
        {repair.error && <ErrorDetails error={repair.error} />}
      </span>
    </DetailRow>
  )
}
