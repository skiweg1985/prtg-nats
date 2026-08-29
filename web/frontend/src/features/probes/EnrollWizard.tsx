import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import {
  useCreateInvitation,
  useInvitation,
  useInvitations,
  useJob,
  useJobLog,
  useNatsAccounts,
  useProbes,
  useRetryJob,
  useRevokeInvitation,
} from '@/api/hooks'
import type {
  Invitation,
  InvitationRequest,
  IssuedInvitation,
  NatsAccount,
} from '@/api/types'
import { useAuth } from '@/app/providers'
import { CopyBlock, useCountdown } from '@/components/ui/CopyBlock'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { JobSteps, LiveLog } from '@/components/ui/JobProgress'
import {
  Badge,
  Banner,
  Button,
  Card,
  DetailRow,
  EmptyState,
  Field,
  Input,
  Label,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { JobStatusBadge } from '@/components/ui/status'

/**
 * Adding a probe, from the operator's side.
 *
 * Nothing here reaches the probe. The platform hands out a command; a person
 * runs it on the host; the host reports back and the platform takes over. So
 * the wizard is three states, not three forms: describe the probe, show the
 * command, watch what happens.
 *
 * The waiting step polls the invitation rather than the probe. There is
 * nothing to ask the probe - it has no management access until the command has
 * run, which is the whole point of doing it this way.
 */
export function EnrollWizard() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { can, isLoading } = useAuth()

  const accounts = useNatsAccounts()
  // For the address check below. The server refuses a second entry for an
  // address that is already enrolled; knowing the list here means saying so
  // while the operator is still typing.
  const probes = useProbes()
  const open = useInvitations(can('probe.create'))
  const createInvitation = useCreateInvitation()
  const revokeInvitation = useRevokeInvitation()

  const [issued, setIssued] = useState<IssuedInvitation | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  // The last request survives the invitation: an expired one used to mean
  // typing account, name and address a second time.
  const [lastRequest, setLastRequest] = useState<InvitationRequest | null>(null)

  // Only while an invitation is out and has not started a job yet. Once the
  // job exists there is nothing left to ask about the invitation.
  const invitation = useInvitation(issued && !jobId ? issued.id : null, {
    refetchInterval: 3000,
  })

  // Redeeming the invitation is what writes the job id, so the job id
  // appearing is the host reporting in. Watched on the invitation itself and
  // not on the open list: redemption takes it out of that list in the same
  // request, which would drop the record exactly when it becomes interesting.
  useEffect(() => {
    const started = invitation.data?.job_id
    if (started) setJobId(started)
  }, [invitation.data])

  if (accounts.error) {
    return <ErrorDetails error={accounts.error} onRetry={() => void accounts.refetch()} />
  }

  // The route is reachable by URL; the buttons that lead here are gated. A
  // full form that fails on submit with a 403 is the worse version of no.
  if (isLoading) return <Skeleton className="h-64" />
  if (!can('probe.create')) {
    return (
      <Card>
        <EmptyState
          title={t('probes.enroll.noPermissionTitle')}
          hint={t('probes.enroll.noPermission')}
        />
      </Card>
    )
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4">
      <header>
        {/* The only page under /probes without one. From step two on, the
            browser's own button was the way out. */}
        <Link to="/probes" className="text-ink-3 text-xs">
          ← {t('probes.title')}
        </Link>
        <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-lg">{t('probes.enroll.title')}</h1>
          <p className="text-ink-3 text-sm">{t('probes.enroll.subtitle')}</p>
        </div>
      </header>

      {!issued && (
        <>
          <OpenInvitations
            invitations={open.data ?? []}
            onReissue={(request) => {
              setLastRequest(request)
              createInvitation.mutate(request, { onSuccess: setIssued })
            }}
          />
          <InvitationForm
            accounts={accounts.data ?? []}
            enrolledHosts={(probes.data ?? []).map((probe) => ({
              host: probe.host,
              username: probe.nats_username,
            }))}
            initial={lastRequest}
            pending={createInvitation.isPending}
            error={createInvitation.error}
            onSubmit={(request) => {
              setLastRequest(request)
              createInvitation.mutate(request, { onSuccess: setIssued })
            }}
            onCancel={() => navigate('/probes')}
          />
        </>
      )}

      {issued && !jobId && (
        <CommandStep
          invitation={issued}
          revoked={invitation.data?.revoked_at != null}
          revokeError={revokeInvitation.error}
          onCancel={() =>
            // Back to the form only once the server confirms - jumping back
            // regardless would claim an invitation is dead that is not.
            revokeInvitation.mutate(issued.id, {
              onSuccess: () => setIssued(null),
            })
          }
          onRestart={() => setIssued(null)}
        />
      )}

      {issued && jobId && (
        <ProgressStep jobId={jobId} invitation={issued} reported={invitation.data} />
      )}
    </div>
  )
}

/**
 * The invitations that are out. They lived only in the tab that created them:
 * a reload lost the command for good while the invitation stayed usable for
 * up to a day - unfindable, unrevokable, and free to be redeemed.
 */
function OpenInvitations({
  invitations,
  onReissue,
}: {
  invitations: Invitation[]
  onReissue: (request: InvitationRequest) => void
}) {
  const { t } = useTranslation()
  const revoke = useRevokeInvitation()

  if (invitations.length === 0) return null

  return (
    <Card title={t('probes.enroll.open.title')} dense>
      <ul className="divide-rule divide-y">
        {invitations.map((entry) => (
          <li
            key={entry.id}
            className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm"
          >
            <div className="min-w-0 flex-1">
              <Mono>{entry.nats_username ?? '—'}</Mono>
              <p className="text-ink-3 text-xs">
                {t('probes.enroll.open.meta', {
                  host: entry.expected_host ?? '—',
                  by: entry.created_by_name ?? '—',
                })}{' '}
                · <InvitationCountdown deadline={entry.expires_at} />
              </p>
              {/* The command was only ever shown at creation; there is no way
                  to show it again, and pretending otherwise would be worse. */}
              <p className="text-ink-3 text-xs">
                {t('probes.enroll.open.commandGone')}
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="ghost"
                disabled={revoke.isPending}
                onClick={() => revoke.mutate(entry.id)}
              >
                {t('probes.enroll.open.revoke')}
              </Button>
              <Button
                size="sm"
                disabled={revoke.isPending}
                onClick={() =>
                  // A fresh command for the same probe: the old invitation is
                  // withdrawn first, then the same request goes out again.
                  revoke.mutate(entry.id, {
                    onSuccess: () =>
                      onReissue({
                        nats_username: entry.nats_username ?? '',
                        probe_name: entry.probe_name,
                        expected_host: entry.expected_host,
                      }),
                  })
                }
              >
                {t('probes.enroll.open.reissue')}
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {revoke.error != null && (
        <div className="px-4 py-2">
          <ErrorDetails error={revoke.error} />
        </div>
      )}
    </Card>
  )
}

function InvitationCountdown({ deadline }: { deadline: string }) {
  const { t } = useTranslation()
  const remaining = useCountdown(deadline)
  return (
    <span>
      {remaining
        ? `${t('probes.enroll.step2.expires')} ${remaining}`
        : t('probes.enroll.step2.expired')}
    </span>
  )
}

// --- Step 1: what the probe should become -----------------------------------

const TTL_CHOICES = [15, 60, 240, 1440] as const

function InvitationForm({
  accounts,
  enrolledHosts,
  initial,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  accounts: NatsAccount[]
  enrolledHosts: { host: string; username: string }[]
  /** The previous request, so an expired invitation costs a click, not a form. */
  initial: InvitationRequest | null
  pending: boolean
  error: ApiError | Error | null
  onSubmit: (request: InvitationRequest) => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [username, setUsername] = useState(initial?.nats_username ?? '')
  const [probeName, setProbeName] = useState(initial?.probe_name ?? '')
  const [host, setHost] = useState(initial?.expected_host ?? '')
  const [installPackage, setInstallPackage] = useState(
    initial?.install_package ?? true,
  )
  const [ttl, setTtl] = useState(initial?.ttl_minutes ?? 60)

  const existing = accounts.find((account) => account.username === username)
  // An account that belongs to an enrolled probe can be enrolled again - the
  // server keeps probe id and access key so PRTG sees the same probe. Only an
  // account that belongs to something else stays refused. The interface used
  // to recommend re-enrolment in one message and refuse it in this form.
  const reenrolling = existing !== undefined && existing.probe_enrolled
  const taken = existing !== undefined && !existing.probe_enrolled
  // Mirrors NATS_USERNAME_PATTERN and PROBE_NAME_PATTERN on the server. The
  // server refuses these too; saying so here saves a round trip and a
  // half-filled form.
  const usernameOk = /^[a-z0-9][a-z0-9-]{1,63}$/.test(username)
  const probeNameOk = probeName === '' || /^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$/.test(probeName)

  // A second entry for one address would share the management access that
  // lives on the host, and retiring either would revoke it for both. The
  // server refuses it; saying so here saves the walk to a console.
  const claimedBy = enrolledHosts.find(
    (entry) =>
      entry.host.trim().toLowerCase() === host.trim().toLowerCase() &&
      entry.username !== username,
  )
  const hostOk = host === '' || claimedBy === undefined
  const canSubmit = usernameOk && !taken && probeNameOk && hostOk && !pending

  return (
    <Card title={t('probes.enroll.step1.title')}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (!canSubmit) return
          onSubmit({
            nats_username: username,
            probe_name: probeName || null,
            expected_host: host || null,
            install_package: installPackage,
            ttl_minutes: ttl,
          })
        }}
      >
        <p className="text-ink-2 text-sm">{t('probes.enroll.step1.intro')}</p>

        <Field
          label={t('probes.enroll.fields.account')}
          hint={t('probes.enroll.fields.accountHint')}
          error={
            taken
              ? t('probes.enroll.errors.accountTaken')
              : username && !usernameOk
                ? t('probes.enroll.errors.accountShape')
                : undefined
          }
        >
          <Input
            value={username}
            onChange={(event) => setUsername(event.target.value.trim())}
            placeholder="mpp-berlin-01"
            autoFocus
            spellCheck={false}
            autoComplete="off"
          />
        </Field>

        <Field
          label={t('probes.enroll.fields.probeName')}
          hint={t('probes.enroll.fields.probeNameHint')}
          error={probeNameOk ? undefined : t('probes.enroll.errors.probeNameShape')}
        >
          <Input
            value={probeName}
            onChange={(event) => setProbeName(event.target.value)}
            placeholder="multi-platform-probe@berlin"
            spellCheck={false}
            autoComplete="off"
          />
        </Field>

        <Field
          label={t('probes.enroll.fields.host')}
          hint={t('probes.enroll.fields.hostHint')}
          error={
            claimedBy
              ? t('probes.enroll.errors.hostTaken', { probe: claimedBy.username })
              : undefined
          }
        >
          <Input
            value={host}
            onChange={(event) => setHost(event.target.value.trim())}
            placeholder="probe.example.com"
            spellCheck={false}
            autoComplete="off"
          />
        </Field>

        {reenrolling && (
          <Banner tone="warn" title={t('probes.enroll.reenroll.title')}>
            {t('probes.enroll.reenroll.body')}
          </Banner>
        )}

        <Field
          label={t('probes.enroll.fields.ttl')}
          hint={t('probes.enroll.fields.ttlHint')}
        >
          <select
            value={ttl}
            onChange={(event) => setTtl(Number(event.target.value))}
            className="rounded-control border-rule-2 bg-surface text-ink border px-2.5 py-1.5 text-sm"
          >
            {TTL_CHOICES.map((minutes) => (
              <option key={minutes} value={minutes}>
                {minutes < 60
                  ? t('probes.enroll.fields.ttlMinutes', { count: minutes })
                  : t('probes.enroll.fields.ttlHours', { count: minutes / 60 })}
              </option>
            ))}
          </select>
        </Field>

        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={installPackage}
            onChange={(event) => setInstallPackage(event.target.checked)}
          />
          <span>
            <span className="text-ink">{t('probes.enroll.fields.installPackage')}</span>
            <span className="text-ink-3 block text-xs">
              {t('probes.enroll.fields.installPackageHint')}
            </span>
          </span>
        </label>

        {error != null && <ErrorDetails error={error} />}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" variant="primary" disabled={!canSubmit}>
            {pending
              ? t('probes.enroll.step1.creating')
              : t('probes.enroll.step1.submit')}
          </Button>
        </div>
      </form>
    </Card>
  )
}

// --- Step 2: the command, and waiting ---------------------------------------

function CommandStep({
  invitation,
  revoked,
  revokeError,
  onCancel,
  onRestart,
}: {
  invitation: IssuedInvitation
  revoked: boolean
  revokeError: ApiError | Error | null
  onCancel: () => void
  onRestart: () => void
}) {
  const { t } = useTranslation()
  const remaining = useCountdown(invitation.expires_at)
  // Expired here, revoked elsewhere - either way the command on screen would
  // be refused, and waiting for a host that can no longer report in is the
  // wrong thing to show.
  const dead = remaining === null || revoked

  return (
    <div className="space-y-4">
      <Card title={t('probes.enroll.step2.title')}>
        <div className="space-y-3">
          <p className="text-ink-2 text-sm">{t('probes.enroll.step2.intro')}</p>

          <CopyBlock value={invitation.command} label={t('probes.enroll.step2.copy')} />

          <div className="border-rule space-y-1 border-t pt-3">
            <DetailRow label={t('probes.enroll.step2.account')}>
              <Mono>{invitation.nats_username}</Mono>
            </DetailRow>
            <DetailRow label={t('probes.enroll.step2.caFingerprint')}>
              <Mono truncate>{invitation.ca_sha256}</Mono>
            </DetailRow>
            <DetailRow label={t('probes.enroll.step2.expires')}>
              {remaining === null ? (
                <Badge tone="danger">{t('probes.enroll.step2.expired')}</Badge>
              ) : (
                <span className="text-sm">{remaining}</span>
              )}
            </DetailRow>
          </div>

          {/* The command carries the invitation. Anyone who can read it can
              enrol a host as this account until it is used or expires. */}
          <Banner tone="warn" title={t('probes.enroll.step2.secretTitle')}>
            {t('probes.enroll.step2.secretBody')}
          </Banner>
        </div>
      </Card>

      {dead ? (
        <Banner tone="warn" title={t('probes.enroll.step2.deadTitle')}>
          <div className="space-y-2">
            <p>{t('probes.enroll.step2.deadBody')}</p>
            <Button variant="primary" size="sm" onClick={onRestart}>
              {t('probes.enroll.step2.restart')}
            </Button>
          </div>
        </Banner>
      ) : (
        <Card>
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span
                className="border-accent size-4 animate-spin rounded-full border-2 border-t-transparent"
                aria-hidden
              />
              <div>
                <p className="text-ink text-sm font-medium">
                  {t('probes.enroll.step2.waiting')}
                </p>
                <p className="text-ink-3 text-xs">
                  {t('probes.enroll.step2.waitingHint')}
                </p>
              </div>
            </div>
            <Button variant="ghost" onClick={onCancel}>
              {t('probes.enroll.step2.revoke')}
            </Button>
          </div>
        </Card>
      )}

      {/* A failed withdrawal used to vanish: the form came back as if the
          invitation were dead, while it was still out there. */}
      {revokeError != null && <ErrorDetails error={revokeError} />}
    </div>
  )
}

// --- Step 3: what the platform did ------------------------------------------

function ProgressStep({
  jobId,
  invitation,
  reported,
}: {
  jobId: string
  invitation: IssuedInvitation
  reported: Invitation | undefined
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const retry = useRetryJob()
  const job = useJob(jobId, {
    refetchInterval: (query) =>
      query.state.data?.status === 'running' || query.state.data?.status === 'queued'
        ? 2000
        : false,
  })
  const log = useJobLog(jobId)
  const live = job.data?.status === 'running' || job.data?.status === 'queued'
  // The enrolment job carries the NATS account as its target, not the probe
  // id, so the link to the probe's own page has to be looked up. Until the
  // list has caught up, the list itself stays the destination.
  const probes = useProbes()
  const probeId = probes.data?.find(
    (probe) => probe.nats_username === invitation.nats_username,
  )?.id

  return (
    <div className="space-y-4">
      <Card
        title={t('probes.enroll.step3.title')}
        action={job.data && <JobStatusBadge status={job.data.status} />}
      >
        <div className="space-y-3">
          <p className="text-ink-2 text-sm">{t('probes.enroll.step3.intro')}</p>
          {job.data && <JobSteps job={job.data} />}
        </div>
      </Card>

      {job.data?.status === 'successful' && (
        <Banner tone="ok" title={t('probes.enroll.step3.doneTitle')}>
          <div className="space-y-2">
            <p>{t('probes.enroll.step3.doneBody')}</p>
            {/* The two steps the platform cannot take: they happen in the PRTG
                core by hand, and until they have, the probe is not there at
                all. Saying so here is the only place it can be said in time. */}
            <p>{t('probes.enroll.step3.donePrtg')}</p>
            <div className="flex flex-wrap gap-2">
              {/* The body promises "sensors deploy like on any other probe";
                  this is the button that starts exactly that. */}
              {probeId && (
                <Link to={`/probes/${probeId}?tab=sensors`}>
                  <Button variant="primary" size="sm">
                    {t('probes.enroll.step3.toDeploy')}
                  </Button>
                </Link>
              )}
              <Link to={probeId ? `/probes/${probeId}` : '/probes'}>
                <Button size="sm">
                  {probeId
                    ? t('probes.enroll.step3.toProbe')
                    : t('probes.enroll.step3.toProbes')}
                </Button>
              </Link>
              <Link to={`/jobs/${jobId}`}>
                <Button size="sm">{t('probes.enroll.step3.toJob')}</Button>
              </Link>
            </div>
          </div>
        </Banner>
      )}

      {job.data?.status === 'failed' && (
        <Banner tone="danger" title={t('probes.enroll.step3.failedTitle')}>
          <div className="space-y-2">
            {/* The invitation is spent either way. Retrying the job is the
                right move when the cause was transient; a new invitation is
                needed only if the host itself has to run again. */}
            <p>{t('probes.enroll.step3.failedBody')}</p>
            <Link to={`/jobs/${jobId}`}>
              <Button size="sm">{t('probes.enroll.step3.toJob')}</Button>
            </Link>
          </div>
        </Banner>
      )}

      {/* The cause and the recommended action are already written - showing
          only a generic banner here meant reading the thinnest version of the
          answer at the moment it mattered most. */}
      {job.data?.status === 'failed' && job.data.error_code && (
        <ErrorDetails
          error={
            new ApiError(
              {
                code: job.data.error_code,
                message_key: `errors.${job.data.error_code}`,
                params: job.data.error_params ?? {},
                fields: [],
                details: job.data.error_details,
                correlation_id: null,
                retryable: true,
              },
              500,
            )
          }
          step={job.data.current_step}
          onRetry={() =>
            retry.mutate(jobId, {
              onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
            })
          }
        />
      )}

      <Card title={t('jobs.log')} dense>
        <LiveLog jobId={jobId} initialEvents={log.data ?? []} live={live} />
      </Card>

      <p className="text-ink-3 text-xs">
        <Label>{t('probes.enroll.step3.reportedBy')}</Label>{' '}
        {/* The issued snapshot only knows what was typed; when the address
            field stayed empty - the documented normal case - the server took
            the caller's own address, and that is the one that matters for
            NAT and firewall rules. */}
        <Mono>{reported?.source_ip ?? invitation.expected_host ?? '—'}</Mono>
      </p>
    </div>
  )
}

// --- Bits --------------------------------------------------------------------

