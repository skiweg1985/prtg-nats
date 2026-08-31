import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  useOverlay,
  useOverlayAttach,
  useOverlayDetach,
  useOverlayDisable,
  useOverlayEnable,
  useOverlayMode,
  useOverlayRefresh,
  useProbes,
} from '@/api/hooks'
import type { OverlayMode, OverlayPeer, ProbeSummary } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable } from '@/components/ui/DataTable'
import type { Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { ProbeLink } from '@/components/ui/ProbeLink'
import {
  Badge,
  Banner,
  Button,
  Card,
  DetailRow,
  Dialog,
  EmptyState,
  Field,
  Input,
  Mono,
} from '@/components/ui/primitives'

import { OverlayModeChoice, pathTone } from './overlayMode'

/**
 * The tunnel between this installation and the probes.
 *
 * The list answers the question the overlay exists for, which is not "who is a
 * peer" but "which path is each probe actually on right now". A probe in auto
 * that is on the tunnel means somebody's ordinary route is down - it looks
 * healthy everywhere else, so it is called out here.
 */
export function OverlayPage() {
  const { t } = useTranslation()
  const { data, isLoading, error, refetch } = useOverlay()
  const probes = useProbes()
  const attach = useOverlayAttach()
  const mode = useOverlayMode()
  const detach = useOverlayDetach()
  const refresh = useOverlayRefresh()
  const enable = useOverlayEnable()
  const disable = useOverlayDisable()

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [dialog, setDialog] = useState<
    'add' | 'mode' | 'remove' | 'enable' | 'disable' | null
  >(null)
  const [endpoint, setEndpoint] = useState('')
  const [chosenMode, setChosenMode] = useState<OverlayMode>('auto')

  const idFor = useMemo(() => {
    const byName = new Map<string, string>()
    for (const probe of probes.data ?? []) byName.set(probe.nats_username, probe.id)
    return byName
  }, [probes.data])

  const offOverlay = useMemo(() => {
    const peers = new Set((data?.peers ?? []).map((peer) => peer.nats_username))
    return (probes.data ?? []).filter(
      (probe: ProbeSummary) => !peers.has(probe.nats_username),
    )
  }, [data?.peers, probes.data])

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const selectedIds = () =>
    [...selected].map((name) => idFor.get(name)).filter((id): id is string => !!id)

  const close = () => {
    setDialog(null)
    setSelected(new Set())
    enable.reset()
  }

  const columns: Column<OverlayPeer>[] = [
    {
      key: 'probe',
      header: t('infrastructure.overlay.columns.probe'),
      sortValue: (row) => row.nats_username,
      searchValue: (row) => `${row.nats_username} ${row.address}`,
      cell: (row) => <ProbeLink username={row.nats_username} />,
    },
    {
      key: 'address',
      header: t('infrastructure.overlay.columns.address'),
      sortValue: (row) => row.address,
      cell: (row) => <Mono>{row.address}</Mono>,
    },
    {
      key: 'mode',
      header: t('infrastructure.overlay.columns.mode'),
      sortValue: (row) => row.mode,
      cell: (row) => (
        <Badge tone={row.mode === 'off' ? 'neutral' : 'accent'}>
          {t(`infrastructure.overlay.modes.${row.mode}.name`)}
        </Badge>
      ),
    },
    {
      key: 'path',
      header: t('infrastructure.overlay.columns.path'),
      sortValue: (row) => row.last_state ?? '',
      cell: (row) => {
        const state = row.last_state ?? 'unknown'
        return (
          <Badge tone={pathTone(row.mode, state)}>
            {t(`infrastructure.overlay.paths.${state}`, {
              defaultValue: state,
            })}
          </Badge>
        )
      },
    },
  ]

  return (
    <div className="space-y-4">
      {data && !data.enabled && (
        <Banner
          tone="warn"
          title={t('infrastructure.overlay.disabled.title')}
          action={
            <PermissionGate permission="overlay.enable">
              <Button size="sm" onClick={() => setDialog('enable')}>
                {t('infrastructure.overlay.enable.action')}
              </Button>
            </PermissionGate>
          }
        >
          {t('infrastructure.overlay.disabled.body')}
        </Banner>
      )}
      {data?.enabled && !data.interface_up && (
        <Banner tone="danger" title={t('infrastructure.overlay.hubDown.title')}>
          {t('infrastructure.overlay.hubDown.body')}
        </Banner>
      )}

      <Card
        title={t('infrastructure.overlay.hub')}
        action={
          data?.enabled ? (
            <PermissionGate permission="overlay.enable">
              <Button size="sm" variant="ghost" onClick={() => setDialog('disable')}>
                {t('infrastructure.overlay.disable.action')}
              </Button>
            </PermissionGate>
          ) : undefined
        }
      >
        <dl className="grid gap-x-8 gap-y-1 sm:grid-cols-2">
          <DetailRow label={t('infrastructure.overlay.endpoint')}>
            {data?.endpoint ? <Mono>{data.endpoint}</Mono> : '—'}
          </DetailRow>
          <DetailRow label={t('infrastructure.overlay.hubAddress')}>
            <Mono>{data?.hub_address ?? '—'}</Mono>
          </DetailRow>
          <DetailRow label={t('infrastructure.overlay.subnet')}>
            <Mono>{data?.subnet ?? '—'}</Mono>
          </DetailRow>
          <DetailRow label={t('infrastructure.overlay.defaultMode')}>
            {data ? t(`infrastructure.overlay.modes.${data.default_mode}.name`) : '—'}
          </DetailRow>
        </dl>
      </Card>

      <DataTable
        rows={data?.peers}
        columns={columns}
        rowKey={(row) => row.nats_username}
        isLoading={isLoading}
        emptyTitle={t('infrastructure.overlay.empty.title')}
        emptyHint={t('infrastructure.overlay.empty.hint')}
        searchPlaceholder={t('infrastructure.overlay.search')}
        selection={{
          selected,
          onChange: setSelected,
          actions: (
            <PermissionGate permission="overlay.manage">
              <Button size="sm" onClick={() => setDialog('mode')}>
                {t('infrastructure.overlay.actions.mode')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void refresh.mutate({ probe_ids: selectedIds() })}
              >
                {t('infrastructure.overlay.actions.refresh')}
              </Button>
              <Button size="sm" variant="danger" onClick={() => setDialog('remove')}>
                {t('infrastructure.overlay.actions.remove')}
              </Button>
            </PermissionGate>
          ),
        }}
      />

      <PermissionGate permission="overlay.manage">
        <Card title={t('infrastructure.overlay.add.title')}>
          {offOverlay.length === 0 ? (
            <EmptyState
              title={t('infrastructure.overlay.add.allOn')}
              hint={t('infrastructure.overlay.add.allOnHint')}
            />
          ) : (
            <>
              <p className="text-muted mb-3 text-sm">
                {t('infrastructure.overlay.add.body', {
                  count: offOverlay.length,
                })}
              </p>
              <Button onClick={() => setDialog('add')} disabled={!data?.enabled}>
                {t('infrastructure.overlay.add.action')}
              </Button>
            </>
          )}
        </Card>
      </PermissionGate>

      {dialog === 'add' && (
        <Dialog onClose={close} title={t('infrastructure.overlay.add.title')}>
          <p className="text-muted mb-3 text-sm">
            {t('infrastructure.overlay.add.dialog')}
          </p>
          <div className="mb-4 space-y-2">
            {offOverlay.map((probe) => (
              <label key={probe.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={selected.has(probe.nats_username)}
                  onChange={(event) => {
                    const next = new Set(selected)
                    if (event.target.checked) next.add(probe.nats_username)
                    else next.delete(probe.nats_username)
                    setSelected(next)
                  }}
                />
                <span>{probe.nats_username}</span>
              </label>
            ))}
          </div>
          <OverlayModeChoice value={chosenMode} onChange={setChosenMode} />
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              {t('common.cancel')}
            </Button>
            <Button
              disabled={selected.size === 0 || attach.isPending}
              onClick={() => {
                attach.mutate({ probe_ids: selectedIds(), mode: chosenMode })
                close()
              }}
            >
              {t('infrastructure.overlay.add.confirm')}
            </Button>
          </div>
        </Dialog>
      )}

      {dialog === 'mode' && (
        <Dialog onClose={close} title={t('infrastructure.overlay.actions.mode')}>
          <OverlayModeChoice value={chosenMode} onChange={setChosenMode} />
          {chosenMode === 'off' && (
            <div className="mt-3">
              <Banner tone="warn">{t('infrastructure.overlay.offWarning')}</Banner>
            </div>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              {t('common.cancel')}
            </Button>
            <Button
              disabled={mode.isPending}
              onClick={() => {
                mode.mutate({ probe_ids: selectedIds(), mode: chosenMode })
                close()
              }}
            >
              {t('common.apply')}
            </Button>
          </div>
        </Dialog>
      )}

      {dialog === 'remove' && (
        <Dialog onClose={close} title={t('infrastructure.overlay.actions.remove')}>
          <p className="text-sm">
            {t('infrastructure.overlay.removeBody', { count: selected.size })}
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              disabled={detach.isPending}
              onClick={() => {
                detach.mutate({ probe_ids: selectedIds() })
                close()
              }}
            >
              {t('infrastructure.overlay.actions.remove')}
            </Button>
          </div>
        </Dialog>
      )}

      {dialog === 'enable' && (
        <Dialog onClose={close} title={t('infrastructure.overlay.enable.title')}>
          <p className="text-muted mb-3 text-sm">
            {t('infrastructure.overlay.enable.body')}
          </p>
          <Field
            label={t('infrastructure.overlay.endpoint')}
            hint={t('infrastructure.overlay.enable.endpointHint')}
            error={enable.error ? String(enable.error.message) : undefined}
          >
            <Input
              value={endpoint}
              onChange={(event) => setEndpoint(event.target.value)}
              placeholder="nats.example.com"
            />
          </Field>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              {t('common.cancel')}
            </Button>
            <Button
              disabled={!endpoint.trim() || enable.isPending}
              onClick={() =>
                enable.mutate({ endpoint_host: endpoint.trim() }, { onSuccess: close })
              }
            >
              {t('infrastructure.overlay.enable.confirm')}
            </Button>
          </div>
        </Dialog>
      )}

      {dialog === 'disable' && (
        <Dialog onClose={close} title={t('infrastructure.overlay.disable.title')}>
          <p className="text-sm">{t('infrastructure.overlay.disable.body')}</p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              disabled={disable.isPending}
              onClick={() => disable.mutate(undefined, { onSuccess: close })}
            >
              {t('infrastructure.overlay.disable.action')}
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  )
}
