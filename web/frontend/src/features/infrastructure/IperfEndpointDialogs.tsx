import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useCreateIperfInvitation,
  useIperfInvitation,
  useJob,
  useJobLog,
  useRemoveEndpoint,
  useRevokeIperfInvitation,
  useRotateEndpoint,
  useUpdateForeignEndpointCredentials,
} from '@/api/hooks'
import type { IperfEndpoint, IssuedIperfInvitation } from '@/api/types'
import { CopyBlock, useCountdown } from '@/components/ui/CopyBlock'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { JobSteps, LiveLog } from '@/components/ui/JobProgress'
import {
  Banner,
  Button,
  Dialog,
  DetailRow,
  Field,
  Input,
  Mono,
} from '@/components/ui/primitives'
import { JobStatusBadge } from '@/components/ui/status'

/**
 * Ask first, then say where it went.
 *
 * The button used to start the job on a single click and report neither its id
 * nor its failure: a new password went out to every probe measuring against
 * this endpoint and nothing on screen changed. What the tooltip said is what
 * this dialog says, at the moment it matters.
 */
export function RotateDialog({
  endpoint,
  onClose,
}: {
  endpoint: IperfEndpoint
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const rotate = useRotateEndpoint()

  return (
    <Dialog title={t('infrastructure.iperf.rotateTitle')} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-ink-2 text-sm">
          {t('infrastructure.iperf.rotateBody', {
            name: endpoint.name,
            count: endpoint.holders.length,
          })}
        </p>

        {rotate.error != null && <ErrorDetails error={rotate.error} />}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            disabled={rotate.isPending}
            onClick={() =>
              rotate.mutate(endpoint.name, {
                onSuccess: (accepted) => {
                  onClose()
                  navigate(`/jobs/${accepted.job_id}`)
                },
              })
            }
          >
            {rotate.isPending
              ? t('infrastructure.iperf.rotating')
              : t('infrastructure.iperf.rotate')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

/**
 * Store what the foreign operator already changed and repair all holders.
 *
 * Unlike rotation, this never reaches the endpoint. The password lives only
 * in this field until the API hands it to the worker; success goes straight to
 * the job because every probe has its own outcome.
 */
export function ForeignCredentialsDialog({
  endpoint,
  onClose,
}: {
  endpoint: IperfEndpoint
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const update = useUpdateForeignEndpointCredentials()
  const [password, setPassword] = useState('')

  return (
    <Dialog title={t('infrastructure.iperf.updateForeignTitle')} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-ink-2 text-sm">
          {t('infrastructure.iperf.updateForeignBody', {
            name: endpoint.name,
            count: endpoint.holders.length,
          })}
        </p>

        <Field
          label={t('infrastructure.iperf.newPassword')}
          hint={t('infrastructure.iperf.updateForeignHint')}
        >
          <Input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
            autoFocus
          />
        </Field>

        {update.error != null && <ErrorDetails error={update.error} />}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            disabled={password.length === 0 || update.isPending}
            onClick={() =>
              update.mutate(
                { name: endpoint.name, password },
                {
                  onSuccess: (accepted) => {
                    setPassword('')
                    onClose()
                    navigate(`/jobs/${accepted.job_id}`)
                  },
                },
              )
            }
          >
            {update.isPending
              ? t('infrastructure.iperf.updatingForeign')
              : t('infrastructure.iperf.updateForeignSubmit')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

export function RemoveDialog({
  endpoint,
  onClose,
}: {
  endpoint: IperfEndpoint
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const remove = useRemoveEndpoint()
  const [keepService, setKeepService] = useState(false)

  return (
    <Dialog title={t('infrastructure.iperf.removeTitle')} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-ink-2 text-sm">
          {t('infrastructure.iperf.removeBody', {
            name: endpoint.name,
            count: endpoint.holders.length,
          })}
        </p>

        {endpoint.managed ? (
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={keepService}
              onChange={(event) => setKeepService(event.target.checked)}
            />
            <span>
              <span className="text-ink">{t('infrastructure.iperf.keepService')}</span>
              <span className="text-ink-3 block text-xs">
                {t('infrastructure.iperf.keepServiceHint')}
              </span>
            </span>
          </label>
        ) : (
          <Banner tone="warn" title={t('infrastructure.iperf.foreign')}>
            {t('infrastructure.iperf.removeForeign')}
          </Banner>
        )}

        {/* The package is never uninstalled: something else on that host may
            be using it, and this platform did not always put it there. */}
        <p className="text-ink-3 text-xs">{t('infrastructure.iperf.removePackage')}</p>

        {remove.error != null && <ErrorDetails error={remove.error} />}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            disabled={remove.isPending}
            onClick={() =>
              // Taking an endpoint away runs on the host as a job like any
              // other, and the dialog closing was the only sign of it.
              remove.mutate(
                { name: endpoint.name, keepService },
                {
                  onSuccess: (accepted) => {
                    onClose()
                    navigate(`/jobs/${accepted.job_id}`)
                  },
                },
              )
            }
          >
            {t('common.remove')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

// --- The third way in: an invitation the endpoint redeems itself -------------

/**
 * For the host this platform cannot reach - behind NAT, behind a firewall.
 *
 * The SSH way needs a route from here to there; this one only needs the
 * endpoint to reach this platform once. Same shape as enrolling a probe:
 * the platform hands out a command, somebody runs it on the host, the host
 * reports back and a job takes over. The whole backend for it existed and
 * was reachable only with curl.
 */
export function InviteDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const create = useCreateIperfInvitation()
  const revoke = useRevokeIperfInvitation()

  const [issued, setIssued] = useState<IssuedIperfInvitation | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [host, setHost] = useState('')
  const [port, setPort] = useState('5201')
  const [username, setUsername] = useState('prtg-probe')
  const [sourceCidr, setSourceCidr] = useState('')
  const [ttl, setTtl] = useState(60)

  // Redemption writes the job id; the id appearing is the host reporting in.
  const invitation = useIperfInvitation(issued && !jobId ? issued.id : null, {
    refetchInterval: 3000,
  })
  useEffect(() => {
    const started = invitation.data?.job_id
    if (started) setJobId(started)
  }, [invitation.data])

  const nameOk = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name)
  const canSubmit = nameOk && !create.isPending

  return (
    <Dialog title={t('infrastructure.iperf.inviteTitle')} onClose={onClose}>
      {issued && jobId ? (
        <InviteProgress jobId={jobId} name={issued.name ?? name} onClose={onClose} />
      ) : issued ? (
        <InviteWaiting
          issued={issued}
          revoked={invitation.data?.revoked_at != null}
          revokeError={revoke.error}
          onRevoke={() =>
            revoke.mutate(issued.id, { onSuccess: () => setIssued(null) })
          }
          onRestart={() => setIssued(null)}
        />
      ) : (
        <div className="space-y-4">
          <p className="text-ink-2 text-sm">{t('infrastructure.iperf.inviteIntro')}</p>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label={t('infrastructure.iperf.columns.name')}
              hint={t('infrastructure.iperf.nameHint')}
              error={
                name && !nameOk ? t('infrastructure.iperf.nameShape') : undefined
              }
            >
              <Input
                value={name}
                onChange={(event) => setName(event.target.value.trim())}
                placeholder="filiale-sued"
                autoFocus
                spellCheck={false}
              />
            </Field>
            <Field
              label={t('infrastructure.iperf.host')}
              hint={t('infrastructure.iperf.inviteHostHint')}
            >
              <Input
                value={host}
                onChange={(event) => setHost(event.target.value.trim())}
                placeholder="iperf.example.com"
                spellCheck={false}
              />
            </Field>
            <Field label={t('infrastructure.iperf.iperfPort')}>
              <Input
                value={port}
                onChange={(event) => setPort(event.target.value.trim())}
                inputMode="numeric"
              />
            </Field>
            <Field label={t('infrastructure.iperf.measureUser')}>
              <Input
                value={username}
                onChange={(event) => setUsername(event.target.value.trim())}
                spellCheck={false}
              />
            </Field>
          </div>

          <Field
            label={t('infrastructure.iperf.sourceCidr')}
            hint={t('infrastructure.iperf.sourceCidrHint')}
          >
            <Input
              value={sourceCidr}
              onChange={(event) => setSourceCidr(event.target.value.trim())}
              placeholder="203.0.113.7/32"
              spellCheck={false}
            />
          </Field>

          <Field
            label={t('infrastructure.iperf.inviteTtl')}
            hint={t('infrastructure.iperf.inviteTtlHint')}
          >
            <select
              value={ttl}
              onChange={(event) => setTtl(Number(event.target.value))}
              className="rounded-control border-rule-2 bg-surface text-ink border px-2.5 py-1.5 text-sm"
            >
              {[15, 60, 240, 1440].map((minutes) => (
                <option key={minutes} value={minutes}>
                  {minutes < 60 ? `${minutes} min` : `${minutes / 60} h`}
                </option>
              ))}
            </select>
          </Field>

          {create.error != null && <ErrorDetails error={create.error} />}

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="primary"
              disabled={!canSubmit}
              onClick={() =>
                create.mutate(
                  {
                    name,
                    expected_host: host || null,
                    iperf_port: Number(port) || 5201,
                    username,
                    ssh_source_cidr: sourceCidr || null,
                    ttl_minutes: ttl,
                  },
                  { onSuccess: setIssued },
                )
              }
            >
              {create.isPending
                ? t('infrastructure.iperf.inviteCreating')
                : t('infrastructure.iperf.inviteCreate')}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  )
}

function InviteWaiting({
  issued,
  revoked,
  revokeError,
  onRevoke,
  onRestart,
}: {
  issued: IssuedIperfInvitation
  revoked: boolean
  revokeError: Error | null
  onRevoke: () => void
  onRestart: () => void
}) {
  const { t } = useTranslation()
  const remaining = useCountdown(issued.expires_at)
  const dead = remaining === null || revoked

  return (
    <div className="space-y-4">
      {dead ? (
        <Banner tone="warn" title={t('infrastructure.iperf.inviteDeadTitle')}>
          <div className="space-y-2">
            <p>{t('infrastructure.iperf.inviteDeadBody')}</p>
            <Button size="sm" variant="primary" onClick={onRestart}>
              {t('infrastructure.iperf.inviteRestart')}
            </Button>
          </div>
        </Banner>
      ) : (
        <>
          <p className="text-ink-2 text-sm">
            {t('infrastructure.iperf.inviteCommandIntro')}
          </p>
          <CopyBlock value={issued.command} label={t('common.copy')} />
          <dl>
            <DetailRow label={t('infrastructure.iperf.columns.name')}>
              <Mono>{issued.name}</Mono>
            </DetailRow>
            <DetailRow label={t('probes.enroll.step2.caFingerprint')}>
              <Mono truncate>{issued.ca_sha256}</Mono>
            </DetailRow>
            <DetailRow label={t('probes.enroll.step2.expires')}>
              {remaining}
            </DetailRow>
          </dl>
          <Banner tone="warn" title={t('probes.enroll.step2.secretTitle')}>
            {t('infrastructure.iperf.inviteSecretBody')}
          </Banner>
          <div className="flex items-center justify-between gap-2">
            <span className="text-ink-3 flex items-center gap-2 text-sm">
              <span className="border-ink-3 inline-block h-3 w-3 animate-spin rounded-full border border-t-transparent" />
              {t('infrastructure.iperf.inviteWaiting')}
            </span>
            <Button size="sm" variant="ghost" onClick={onRevoke}>
              {t('probes.enroll.step2.revoke')}
            </Button>
          </div>
        </>
      )}
      {revokeError != null && <ErrorDetails error={revokeError} />}
    </div>
  )
}

function InviteProgress({
  jobId,
  name,
  onClose,
}: {
  jobId: string
  name: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  const job = useJob(jobId, {
    refetchInterval: (query) =>
      query.state.data?.status === 'running' || query.state.data?.status === 'queued'
        ? 2000
        : false,
  })
  const log = useJobLog(jobId)
  const live = job.data?.status === 'running' || job.data?.status === 'queued'

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-ink-2 text-sm">
          {t('infrastructure.iperf.inviteProgress')}
        </p>
        {job.data && <JobStatusBadge status={job.data.status} />}
      </div>
      {job.data && <JobSteps job={job.data} />}
      <LiveLog jobId={jobId} initialEvents={log.data ?? []} live={live} />
      {job.data?.status === 'successful' && (
        <Banner
          tone="ok"
          title={t('infrastructure.iperf.inviteDoneTitle')}
          action={
            <Link to={`/infrastructure/iperf/${name}`} onClick={onClose}>
              <Button size="sm" variant="primary">
                {t('infrastructure.iperf.inviteToEndpoint')}
              </Button>
            </Link>
          }
        >
          {t('infrastructure.iperf.startedNext')}
        </Banner>
      )}
      <div className="flex justify-end">
        <Button variant="ghost" onClick={onClose}>
          {t('common.close')}
        </Button>
      </div>
    </div>
  )
}
