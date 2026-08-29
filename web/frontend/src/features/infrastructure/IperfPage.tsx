import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useIperfEndpoints,
  useIperfInvitations,
  useProvisionEndpoint,
  useRegisterEndpoint,
  useRevokeIperfInvitation,
  useScanHostKeys,
} from '@/api/hooks'
import type { HostKeyScan, IperfEndpoint } from '@/api/types'
import { DataTable } from '@/components/ui/DataTable'
import type { Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Banner,
  Button,
  Card,
  DetailRow,
  Dialog,
  Field,
  Input,
  Mono,
} from '@/components/ui/primitives'

import { InviteDialog, RemoveDialog, RotateDialog } from './IperfEndpointDialogs'
import { IperfProbesDialog } from './IperfProbesDialog'
import { endpointsHeldByProbe } from './iperfProfiles'
import { PermissionGate, useAuth } from '@/app/providers'
import { formatRelative } from '@/utils/format'

/**
 * The measurement endpoints the probes measure against.
 *
 * Three ways to get one onto this list, and the page leads with the one that
 * fits the usual topology: an endpoint on a public address can rarely reach
 * this installation, but it always has to answer an SSH connection from it -
 * otherwise there would be no management channel at all.
 */
export function IperfPage() {
  const { t } = useTranslation()
  const { data, isLoading, error, refetch } = useIperfEndpoints()
  const [dialog, setDialog] = useState<'provision' | 'register' | 'invite' | null>(
    null,
  )
  const [removing, setRemoving] = useState<IperfEndpoint | null>(null)
  const [rotating, setRotating] = useState<IperfEndpoint | null>(null)
  const [assigning, setAssigning] = useState<IperfEndpoint | null>(null)

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const columns: Column<IperfEndpoint>[] = [
    {
      key: 'name',
      header: t('infrastructure.iperf.columns.name'),
      sortValue: (row) => row.name,
      searchValue: (row) => `${row.name} ${row.host}`,
      cell: (row) => (
        <span className="flex items-center gap-2">
          <span className="text-ink font-medium">{row.name}</span>
          {!row.managed && (
            <Badge tone="neutral">{t('infrastructure.iperf.foreign')}</Badge>
          )}
          {row.holders.length === 0 && (
            <Badge tone="warn">{t('infrastructure.iperf.notDeployed')}</Badge>
          )}
        </span>
      ),
    },
    {
      key: 'endpoint',
      header: t('infrastructure.iperf.columns.address'),
      cell: (row) => (
        <Mono>
          {row.host}:{row.port}
        </Mono>
      ),
    },
    {
      key: 'user',
      header: t('infrastructure.iperf.columns.user'),
      cell: (row) => <Mono>{row.username || '—'}</Mono>,
    },
    {
      key: 'deployed',
      header: t('infrastructure.iperf.columns.deployedTo'),
      align: 'right',
      sortValue: (row) => row.holders.length,
      // The count is the way in, not a read-out: it is the question somebody
      // has when they look at this column, and the answer is the probe list.
      // The row leads to the endpoint; this cell keeps the click that opens
      // the assignment straight away.
      cell: (row) => (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            setAssigning(row)
          }}
          title={row.holders.map((holder) => holder.probe).join(', ') || undefined}
          className="text-ink hover:text-accent text-sm underline underline-offset-2"
        >
          {row.holders.length}
        </button>
      ),
    },
    {
      key: 'updated',
      header: t('common.updated'),
      cell: (row) => (
        <span className="text-ink-3 text-xs">{formatRelative(row.updated_at)}</span>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (row) => (
        <PermissionGate permission="iperf.manage">
          <span
            className="flex justify-end gap-2"
            onClick={(event) => event.stopPropagation()}
          >
            <RotateButton endpoint={row} onStart={() => setRotating(row)} />
            <Button size="sm" variant="ghost" onClick={() => setRemoving(row)}>
              {t('common.remove')}
            </Button>
          </span>
        </PermissionGate>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h1 className="text-lg">{t('infrastructure.iperfTitle')}</h1>
          <p className="text-ink-3 text-sm">{t('infrastructure.iperfSubtitle')}</p>
        </div>
        <PermissionGate permission="iperf.manage">
          <span className="flex gap-2">
            <Button size="sm" onClick={() => setDialog('register')}>
              {t('infrastructure.iperf.register')}
            </Button>
            {/* The third way in, for the host this platform cannot reach:
                the endpoint runs a command and reports back, like a probe. */}
            <Button size="sm" onClick={() => setDialog('invite')}>
              {t('infrastructure.iperf.invite')}
            </Button>
            <Button size="sm" variant="primary" onClick={() => setDialog('provision')}>
              {t('infrastructure.iperf.add')}
            </Button>
          </span>
        </PermissionGate>
      </header>

      <DataTable
        rows={data ?? []}
        columns={columns}
        rowKey={(row) => row.name}
        isLoading={isLoading}
        emptyTitle={t('infrastructure.iperfEmpty')}
        rowHref={(row) => `/infrastructure/iperf/${row.name}`}
      />

      <OpenIperfInvitations />

      {dialog === 'provision' && <ProvisionDialog onClose={() => setDialog(null)} />}
      {dialog === 'register' && <RegisterDialog onClose={() => setDialog(null)} />}
      {dialog === 'invite' && <InviteDialog onClose={() => setDialog(null)} />}
      {rotating && (
        <RotateDialog endpoint={rotating} onClose={() => setRotating(null)} />
      )}
      {assigning && (
        <IperfProbesDialog
          endpoint={assigning}
          heldByProbe={endpointsHeldByProbe(data)}
          onClose={() => setAssigning(null)}
        />
      )}
      {removing && (
        <RemoveDialog endpoint={removing} onClose={() => setRemoving(null)} />
      )}
    </div>
  )
}

/**
 * The iperf invitations that are out. Same reasoning as the probe wizard's
 * card: the command lives only in the tab that created it, the invitation
 * lives on the server.
 */
function OpenIperfInvitations() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const { data } = useIperfInvitations(can('iperf.manage'))
  const revoke = useRevokeIperfInvitation()

  if (!data || data.length === 0) return null

  return (
    <Card title={t('infrastructure.iperf.inviteOpenTitle')} dense>
      <ul className="divide-rule divide-y">
        {data.map((entry) => (
          <li
            key={entry.id}
            className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm"
          >
            <div className="min-w-0 flex-1">
              <Mono>{entry.name ?? '—'}</Mono>
              <p className="text-ink-3 text-xs">
                {t('probes.enroll.open.meta', {
                  host: entry.expected_host ?? '—',
                  by: entry.created_by_name ?? '—',
                })}
              </p>
            </div>
            <Button
              size="sm"
              variant="ghost"
              disabled={revoke.isPending}
              onClick={() => revoke.mutate(entry.id)}
            >
              {t('probes.enroll.open.revoke')}
            </Button>
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

// --- Setting one up ----------------------------------------------------------

/**
 * Two steps, and the split is the point.
 *
 * The host's SSH keys are read first and shown, because the sign-in in step two
 * carries an administrator credential and it has to go to the host somebody
 * looked at - not to whatever answered the address.
 *
 * Both steps are named from the start, though. Withholding the fields is the
 * design; withholding that they are coming only left people wondering where a
 * sign-in was ever going to be asked for.
 */
function ProvisionDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const scan = useScanHostKeys()
  const provision = useProvisionEndpoint()

  const [name, setName] = useState('')
  const [host, setHost] = useState('')
  const [sshPort, setSshPort] = useState('22')
  const [iperfPort, setIperfPort] = useState('5201')
  const [username, setUsername] = useState('prtg-probe')
  const [sourceCidr, setSourceCidr] = useState('')
  const [adminUser, setAdminUser] = useState('root')
  const [adminPassword, setAdminPassword] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [accepted, setAccepted] = useState<HostKeyScan | null>(null)
  const [started, setStarted] = useState<string | null>(null)

  const nameOk = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name)
  const canScan = nameOk && host.trim() !== '' && !scan.isPending
  const canSubmit =
    accepted !== null &&
    (adminPassword !== '' || privateKey !== '') &&
    adminUser !== '' &&
    !provision.isPending

  return (
    <Dialog title={t('infrastructure.iperf.addTitle')} onClose={onClose}>
      {started ? (
        <Banner tone="ok" title={t('infrastructure.iperf.startedTitle')}>
          <div className="space-y-2">
            <p>{t('infrastructure.iperf.startedBody')}</p>
            {/* No link to the endpoint page: the record is written by the job,
                so until it finishes there is nothing there to look at. */}
            <p>{t('infrastructure.iperf.startedNext')}</p>
            <Link to={`/jobs/${started}`}>
              <Button size="sm" variant="primary">
                {t('probes.enroll.step3.toJob')}
              </Button>
            </Link>
          </div>
        </Banner>
      ) : (
        <div className="space-y-4">
          <p className="text-ink-2 text-sm">{t('infrastructure.iperf.addIntro')}</p>

          <div className="flex items-center gap-2">
            <h3 className="text-ink text-sm font-semibold">
              {t('infrastructure.iperf.addStep1')}
            </h3>
            {accepted && (
              <Badge tone="ok">{t('infrastructure.iperf.addStep1Done')}</Badge>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label={t('infrastructure.iperf.columns.name')}
              hint={t('infrastructure.iperf.nameHint')}
              error={name && !nameOk ? t('infrastructure.iperf.nameShape') : undefined}
            >
              <Input
                value={name}
                onChange={(event) => setName(event.target.value.trim())}
                placeholder="berlin"
                autoFocus
                spellCheck={false}
              />
            </Field>
            <Field
              label={t('infrastructure.iperf.host')}
              hint={t('infrastructure.iperf.hostHint')}
            >
              <Input
                value={host}
                onChange={(event) => setHost(event.target.value.trim())}
                placeholder="iperf.example.com"
                spellCheck={false}
              />
            </Field>
            <Field label={t('infrastructure.iperf.sshPort')}>
              <Input
                value={sshPort}
                onChange={(event) => setSshPort(event.target.value.trim())}
                inputMode="numeric"
              />
            </Field>
            <Field label={t('infrastructure.iperf.iperfPort')}>
              <Input
                value={iperfPort}
                onChange={(event) => setIperfPort(event.target.value.trim())}
                inputMode="numeric"
              />
            </Field>
          </div>

          {/* Nothing that authenticates has been typed yet. */}
          {!accepted && (
            <div className="space-y-3">
              <Button
                variant="primary"
                disabled={!canScan}
                onClick={() =>
                  scan.mutate(
                    { host, ssh_port: Number(sshPort) || 22 },
                    { onSuccess: setAccepted },
                  )
                }
              >
                {scan.isPending
                  ? t('infrastructure.iperf.scanning')
                  : t('infrastructure.iperf.scan')}
              </Button>
              {scan.error != null && <ErrorDetails error={scan.error} />}
            </div>
          )}

          {/* The heading stands whether or not the step is reachable. Without
              it the dialog is four fields and a button, and where the sign-in
              happens is something you find out by pressing it. */}
          <h3 className="text-ink text-sm font-semibold">
            {t('infrastructure.iperf.addStep2')}
          </h3>
          {!accepted && (
            <p className="text-ink-3 text-sm">
              {t('infrastructure.iperf.addStep2Preview')}
            </p>
          )}

          {accepted && (
            <>
              <Card title={t('infrastructure.iperf.hostKeys')} dense>
                <div className="space-y-2">
                  <p className="text-ink-2 text-sm">
                    {accepted.already_pinned
                      ? t('infrastructure.iperf.keysKnown')
                      : t('infrastructure.iperf.keysNew')}
                  </p>
                  <dl>
                    {accepted.keys.map((key) => (
                      <DetailRow key={key.line} label={key.algorithm}>
                        <Mono truncate>{key.fingerprint}</Mono>
                      </DetailRow>
                    ))}
                  </dl>
                </div>
              </Card>

              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  label={t('infrastructure.iperf.adminUser')}
                  hint={t('infrastructure.iperf.adminHint')}
                >
                  <Input
                    value={adminUser}
                    onChange={(event) => setAdminUser(event.target.value.trim())}
                    spellCheck={false}
                    autoComplete="off"
                  />
                </Field>
                <Field label={t('auth.password')}>
                  <Input
                    type="password"
                    value={adminPassword}
                    onChange={(event) => setAdminPassword(event.target.value)}
                    autoComplete="off"
                  />
                </Field>
              </div>

              <Field
                label={t('infrastructure.iperf.privateKey')}
                hint={t('infrastructure.iperf.privateKeyHint')}
              >
                <textarea
                  value={privateKey}
                  onChange={(event) => setPrivateKey(event.target.value)}
                  rows={3}
                  spellCheck={false}
                  className="rounded-control border-rule-2 bg-surface text-ink w-full border px-2.5 py-1.5 font-mono text-xs"
                  placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                />
              </Field>

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

              <Field label={t('infrastructure.iperf.measureUser')}>
                <Input
                  value={username}
                  onChange={(event) => setUsername(event.target.value.trim())}
                  spellCheck={false}
                />
              </Field>

              {/* The credential is in this form and in one SSH connection. It
                  is not stored, not logged, and not written into the job. */}
              <Banner tone="warn" title={t('infrastructure.iperf.credentialTitle')}>
                {t('infrastructure.iperf.credentialBody')}
              </Banner>

              {provision.error != null && <ErrorDetails error={provision.error} />}
            </>
          )}

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            {accepted && (
              <Button
                variant="primary"
                disabled={!canSubmit}
                onClick={() =>
                  provision.mutate(
                    {
                      name,
                      host,
                      ssh_port: Number(sshPort) || 22,
                      iperf_port: Number(iperfPort) || 5201,
                      username,
                      ssh_source_cidr: sourceCidr || null,
                      host_keys: accepted.keys.map((key) => key.line),
                      admin: {
                        username: adminUser,
                        password: adminPassword || undefined,
                        private_key: privateKey || undefined,
                      },
                    },
                    { onSuccess: (job) => setStarted(job.job_id) },
                  )
                }
              >
                {provision.isPending
                  ? t('infrastructure.iperf.settingUp')
                  : t('infrastructure.iperf.setUp')}
              </Button>
            )}
          </div>
        </div>
      )}
    </Dialog>
  )
}

// --- Registering one somebody else operates ----------------------------------

function RegisterDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const register = useRegisterEndpoint()

  const [name, setName] = useState('')
  const [host, setHost] = useState('')
  const [port, setPort] = useState('5201')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [publicKey, setPublicKey] = useState('')

  const nameOk = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name)
  // All or nothing: a user name without its password and the endpoint's public
  // key is a sensor that fails on every single run.
  const credentialsComplete =
    username === '' || (password !== '' && publicKey.includes('BEGIN PUBLIC KEY'))
  const canSubmit = nameOk && host !== '' && credentialsComplete && !register.isPending

  return (
    <Dialog title={t('infrastructure.iperf.registerTitle')} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-ink-2 text-sm">{t('infrastructure.iperf.registerIntro')}</p>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('infrastructure.iperf.columns.name')}>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value.trim())}
              placeholder="provider"
              autoFocus
              spellCheck={false}
            />
          </Field>
          <Field label={t('infrastructure.iperf.host')}>
            <Input
              value={host}
              onChange={(event) => setHost(event.target.value.trim())}
              placeholder="iperf.provider.example"
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
          <Field
            label={t('infrastructure.iperf.columns.user')}
            hint={t('infrastructure.iperf.foreignUserHint')}
          >
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value.trim())}
              spellCheck={false}
              autoComplete="off"
            />
          </Field>
        </div>

        {username !== '' && (
          <>
            <Field label={t('auth.password')}>
              <Input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="off"
              />
            </Field>
            <Field
              label={t('infrastructure.iperf.publicKey')}
              hint={t('infrastructure.iperf.publicKeyHint')}
            >
              <textarea
                value={publicKey}
                onChange={(event) => setPublicKey(event.target.value)}
                rows={4}
                spellCheck={false}
                className="rounded-control border-rule-2 bg-surface text-ink w-full border px-2.5 py-1.5 font-mono text-xs"
                placeholder="-----BEGIN PUBLIC KEY-----"
              />
            </Field>
          </>
        )}

        {register.error != null && <ErrorDetails error={register.error} />}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            disabled={!canSubmit}
            onClick={() =>
              register.mutate(
                {
                  name,
                  host,
                  port: Number(port) || 5201,
                  username,
                  password,
                  public_key_pem: publicKey || null,
                },
                {
                  // The record exists the moment this returns, and the page it
                  // lands on is the one that says what is still missing.
                  onSuccess: (endpoint) => {
                    onClose()
                    navigate(`/infrastructure/iperf/${endpoint.name}`)
                  },
                },
              )
            }
          >
            {t('infrastructure.iperf.register')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

// --- Rotating and removing ---------------------------------------------------

function RotateButton({
  endpoint,
  onStart,
}: {
  endpoint: IperfEndpoint
  onStart: () => void
}) {
  const { t } = useTranslation()

  if (!endpoint.managed) return null
  return (
    <Button size="sm" onClick={onStart}>
      {t('infrastructure.iperf.rotate')}
    </Button>
  )
}

// --- Bits --------------------------------------------------------------------


