import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useEndpointDeployment,
  useIperfEndpoints,
  useProbes,
  useProvisionEndpoint,
  useRegisterEndpoint,
  useRemoveEndpoint,
  useRotateEndpoint,
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
import { PermissionGate } from '@/app/providers'
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
  const [dialog, setDialog] = useState<'provision' | 'register' | null>(null)
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
      sortValue: (row) => row.deployed_to.length,
      // The count is the way in, not a read-out: it is the question somebody
      // has when they look at this column, and the answer is the probe list.
      cell: (row) => (
        <button
          type="button"
          onClick={() => setAssigning(row)}
          title={row.deployed_to.join(', ') || undefined}
          className="text-ink hover:text-accent text-sm underline underline-offset-2"
        >
          {row.deployed_to.length}
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
          <span className="flex justify-end gap-2">
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
      />

      {dialog === 'provision' && <ProvisionDialog onClose={() => setDialog(null)} />}
      {dialog === 'register' && <RegisterDialog onClose={() => setDialog(null)} />}
      {rotating && (
        <RotateDialog endpoint={rotating} onClose={() => setRotating(null)} />
      )}
      {assigning && (
        <ProbesDialog endpoint={assigning} onClose={() => setAssigning(null)} />
      )}
      {removing && (
        <RemoveDialog endpoint={removing} onClose={() => setRemoving(null)} />
      )}
    </div>
  )
}

// --- Setting one up ----------------------------------------------------------

/**
 * Two steps, and the split is the point.
 *
 * The host's SSH keys are read first and shown, because the sign-in in step two
 * carries an administrator credential and it has to go to the host somebody
 * looked at - not to whatever answered the address.
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

          {/* Step one. Nothing that authenticates has been typed yet. */}
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
                { onSuccess: onClose },
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

/**
 * Ask first, then say where it went.
 *
 * The button used to start the job on a single click and report neither its id
 * nor its failure: a new password went out to every probe measuring against
 * this endpoint and nothing on screen changed. What the tooltip said is what
 * this dialog says, at the moment it matters.
 */
function RotateDialog({
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
            count: endpoint.deployed_to.length,
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

function RemoveDialog({
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
            count: endpoint.deployed_to.length,
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

// --- Bits --------------------------------------------------------------------


/**
 * Which probes measure against this endpoint.
 *
 * The list is the assignment, not a snapshot of the last rollout: a sensor
 * deployment reads the same record, so a probe taken out here stays out
 * instead of getting the credentials back with the next deployment.
 *
 * Adding and removing are separate jobs rather than one "save" of the whole
 * set. They are separate operations on the probes - one writes a credential,
 * the other takes one away - and a single button hiding both would report one
 * outcome for two things that can fail independently.
 */
function ProbesDialog({
  endpoint,
  onClose,
}: {
  endpoint: IperfEndpoint
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data: probes, isLoading } = useProbes()
  const deploy = useEndpointDeployment('deploy')
  const revoke = useEndpointDeployment('revoke')
  const [selected, setSelected] = useState<string[]>([])

  const holding = new Set(endpoint.deployed_to)
  const running = deploy.isPending || revoke.isPending
  const chosen = new Set(selected)
  const toAdd = selected.filter((name) => !holding.has(name))
  const toRemove = selected.filter((name) => holding.has(name))

  const toggle = (name: string) =>
    setSelected((current) =>
      current.includes(name)
        ? current.filter((entry) => entry !== name)
        : [...current, name],
    )

  const start = (mutation: typeof deploy, names: string[]) =>
    mutation.mutate(
      { name: endpoint.name, probes: names },
      {
        onSuccess: (accepted) => {
          onClose()
          navigate(`/jobs/${accepted.job_id}`)
        },
      },
    )

  return (
    <Dialog title={t('infrastructure.iperf.probesTitle')} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-ink-2 text-sm">
          {t('infrastructure.iperf.probesBody', { name: endpoint.name })}
        </p>

        {isLoading ? (
          <p className="text-ink-3 text-sm">{t('common.loading')}</p>
        ) : !probes || probes.length === 0 ? (
          <p className="text-ink-3 text-sm">{t('infrastructure.iperf.probesEmpty')}</p>
        ) : (
          <ul className="divide-rule border-rule-2 rounded-control max-h-64 divide-y overflow-y-auto border">
            {probes.map((probe) => (
              <li key={probe.nats_username}>
                <label className="flex cursor-pointer items-center gap-2 px-2.5 py-2 text-sm">
                  <input
                    type="checkbox"
                    checked={chosen.has(probe.nats_username)}
                    onChange={() => toggle(probe.nats_username)}
                  />
                  <span className="text-ink min-w-0 flex-1 truncate">
                    {probe.display_name || probe.nats_username}
                  </span>
                  {holding.has(probe.nats_username) && (
                    <Badge tone="ok">{t('infrastructure.iperf.probeHolds')}</Badge>
                  )}
                </label>
              </li>
            ))}
          </ul>
        )}

        {deploy.error != null && <ErrorDetails error={deploy.error} />}
        {revoke.error != null && <ErrorDetails error={revoke.error} />}

        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          {/* Reading who holds what is part of seeing the endpoint at all;
              changing it writes a credential to a probe, which is the
              deployer's decision rather than the endpoint owner's. */}
          <PermissionGate permission="sensor.deploy">
            <Button
              variant="danger"
              disabled={running || toRemove.length === 0}
              onClick={() => start(revoke, toRemove)}
            >
              {t('infrastructure.iperf.revokeFrom', { count: toRemove.length })}
            </Button>
            <Button
              variant="primary"
              disabled={running || toAdd.length === 0}
              onClick={() => start(deploy, toAdd)}
            >
              {t('infrastructure.iperf.deployTo', { count: toAdd.length })}
            </Button>
          </PermissionGate>
        </div>
      </div>
    </Dialog>
  )
}
