import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query'

import { api } from './client'
import type {
  AuditEvent,
  AuthState,
  BackupFile,
  Capabilities,
  StackVersion,
  Certificate,
  Dashboard,
  Deployment,
  HostKeyScan,
  Invitation,
  InvitationRequest,
  IperfEndpoint,
  IperfInvitation,
  IperfInvitationRequest,
  IssuedInvitation,
  IssuedIperfInvitation,
  JobAccepted,
  JobDetail,
  JobEvent,
  JobSummary,
  NatsAccount,
  ObservedState,
  Overlay,
  OverlayMode,
  ProbeDetail,
  ProbeSummary,
  ProvisionEndpointRequest,
  ReconciliationPlan,
  RegisterEndpointRequest,
  RevealedAccessKey,
  SensorDetail,
  SensorProfile,
  SensorProfileDetail,
  SensorProfileFile,
  SensorSummary,
  SystemStatus,
  WatchAvailability,
  WatchDevice,
  WatchDeviceRequest,
  WatchOutage,
  WatchOverview,
  WebUser,
  WirelessInterface,
} from './types'

/** One place that names every cache entry, so invalidation cannot go stale. */
export const keys = {
  auth: ['auth'] as const,
  capabilities: ['capabilities'] as const,
  dashboard: ['dashboard'] as const,
  system: ['system'] as const,
  stackVersion: ['system', 'update'] as const,
  probes: ['probes'] as const,
  watch: ['watch'] as const,
  watchOverview: (labels: string[]) => ['watch', 'overview', ...labels] as const,
  watchAvailability: (id: string, days: number) =>
    ['watch', 'availability', id, days] as const,
  watchOutages: (days: number, labels: string[]) =>
    ['watch', 'outages', days, ...labels] as const,
  probe: (id: string) => ['probes', id] as const,
  probePlan: (id: string) => ['probes', id, 'plan'] as const,
  probeInterfaces: (id: string) => ['probes', id, 'wireless-interfaces'] as const,
  sensors: ['sensors'] as const,
  sensor: (name: string) => ['sensors', name] as const,
  sensorProfiles: (name: string) => ['sensors', name, 'profiles'] as const,
  sensorProfile: (name: string, profile: string) =>
    ['sensors', name, 'profiles', profile] as const,
  deployments: ['deployments'] as const,
  deployment: (id: string) => ['deployments', id] as const,
  jobs: (filters?: Record<string, unknown>) => ['jobs', filters ?? {}] as const,
  job: (id: string) => ['jobs', id] as const,
  jobLog: (id: string) => ['jobs', id, 'log'] as const,
  audit: (filters?: Record<string, unknown>) => ['audit', filters ?? {}] as const,
  certificates: ['certificates'] as const,
  backups: ['system', 'backups'] as const,
  credentials: ['credentials'] as const,
  invitations: ['probes', 'invitations'] as const,
  invitation: (id: string) => ['probes', 'invitations', id] as const,
  iperf: ['iperf'] as const,
  iperfInvitations: ['iperf-invitations'] as const,
  overlay: ['overlay'] as const,
  users: ['users'] as const,
}

// Jobs move; a dashboard that only updates on reload is a dashboard nobody
// trusts. Everything else is invalidated by the mutation that changed it.
const LIVE_REFETCH_MS = 15_000

export function useAuthState() {
  return useQuery({
    queryKey: keys.auth,
    queryFn: () => api.get<AuthState>('/auth/state'),
    staleTime: 30_000,
    retry: false,
  })
}

export function useCapabilities() {
  return useQuery({
    queryKey: keys.capabilities,
    queryFn: () => api.get<Capabilities>('/system/capabilities'),
    staleTime: 5 * 60_000,
  })
}

/**
 * Which version is installed and whether the branch has moved on.
 *
 * Polled rather than live: the answer comes from a cache the background check
 * refreshes hourly, so anything faster asks the same question of the same row.
 */
export function useStackVersion() {
  return useQuery({
    queryKey: keys.stackVersion,
    queryFn: () => api.get<StackVersion>('/system/update'),
    staleTime: 60_000,
  })
}

/** Ask the repository right now instead of waiting for the next pass. */
export function useCheckForUpdate() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<StackVersion>('/system/update/check', {}),
    onSuccess: (data) => {
      client.setQueryData(keys.stackVersion, data)
      void client.invalidateQueries({ queryKey: keys.capabilities })
    },
  })
}

/**
 * Start the update, or a rebuild of what the checkout already holds.
 *
 * The second is for the state a `git pull` on the host leaves behind: the code
 * is there, the image is not built from it. Same job, two steps fewer.
 */
export function useStartUpdate() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (mode: 'update' | 'rebuild' = 'update') =>
      api.post<JobAccepted>('/system/update', { mode }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.jobs() })
    },
  })
}

export function useDashboard() {
  return useQuery({
    queryKey: keys.dashboard,
    queryFn: () => api.get<Dashboard>('/dashboard'),
    refetchInterval: LIVE_REFETCH_MS,
  })
}

export function useSystemStatus() {
  return useQuery({
    queryKey: keys.system,
    queryFn: () => api.get<SystemStatus>('/system'),
    refetchInterval: LIVE_REFETCH_MS,
  })
}

export function useProbes() {
  return useQuery({
    queryKey: keys.probes,
    queryFn: () => api.get<ProbeSummary[]>('/probes'),
    refetchInterval: LIVE_REFETCH_MS,
  })
}

/**
 * How often a probe's detail reloads: only while a job holds it.
 *
 * A page open on a probe nothing is happening to has nothing to poll for - the
 * state it shows is as current as the last observation and says so. One open
 * on a probe being worked on used to freeze until the window lost the focus
 * and got it back, which is the whole reason this exists.
 */
export function probeRefetchInterval(detail: ProbeDetail | undefined) {
  return detail?.summary.running_job_id ? LIVE_REFETCH_MS : (false as const)
}

export function useProbe(id: string | undefined) {
  return useQuery({
    queryKey: keys.probe(id ?? ''),
    queryFn: () => api.get<ProbeDetail>(`/probes/${id}`),
    enabled: Boolean(id),
    refetchInterval: (query) => probeRefetchInterval(query.state.data),
  })
}

export function useProbePlan(id: string | undefined, enabled = false) {
  return useQuery({
    queryKey: keys.probePlan(id ?? ''),
    queryFn: () => api.post<ReconciliationPlan>(`/probes/${id}/reconcile?dry_run=true`),
    enabled: Boolean(id) && enabled,
  })
}

export function useRefreshProbe() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post<ObservedState>(`/probes/${id}/refresh`),
    onSuccess: (_, id) => {
      void client.invalidateQueries({ queryKey: keys.probe(id) })
      void client.invalidateQueries({ queryKey: keys.probes })
    },
  })
}

export function useUpdateProbe() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
      api.patch<ProbeDetail>(`/probes/${id}`, body),
    onSuccess: (_, variables) => {
      void client.invalidateQueries({ queryKey: keys.probe(variables.id) })
      void client.invalidateQueries({ queryKey: keys.probes })
    },
  })
}

export function useProbeAction(action: 'install-ca' | 'validate' | 'helper-update') {
  const client = useQueryClient()
  return useMutation({
    // The fleet route with a one-probe selection: the per-probe twins did the
    // same thing under a second path, and both had to be kept working.
    mutationFn: (id: string) =>
      api.post<JobAccepted>(`/probes/actions/${action}`, { probe_ids: [id] }),
    onSuccess: (_, id) => {
      void client.invalidateQueries({ queryKey: ['jobs'] })
      // The probe now holds a job, and its detail decides by that whether to
      // keep itself current. Without this the page would wait for the next
      // load to find out that anything started.
      void client.invalidateQueries({ queryKey: keys.probe(id) })
    },
  })
}

/** The actions a selection of probes can be asked for in one go. */
export type FleetAction =
  | 'refresh'
  | 'validate'
  | 'install-ca'
  | 'helper-update'
  | 'configure'
  | 'reconcile'

/**
 * One action over a selection: one job, one lock per probe.
 *
 * The same endpoints the detail page uses, addressed by selection rather than
 * by probe. A single id is a legitimate selection - the caller does not have
 * to decide between two shapes of request at twelve probes and at one.
 *
 * One mutation for all six actions rather than one hook per action: the bar
 * that calls this runs one at a time, so one pending flag and one error are
 * exactly what it has to render.
 */
export function useFleetAction() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ action, probeIds }: { action: FleetAction; probeIds: string[] }) =>
      api.post<JobAccepted>(`/probes/actions/${action}`, { probe_ids: probeIds }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['jobs'] })
      void client.invalidateQueries({ queryKey: keys.probes })
    },
  })
}

export function useSensors() {
  return useQuery({
    queryKey: keys.sensors,
    queryFn: () => api.get<SensorSummary[]>('/sensors'),
  })
}

export function useSensor(name: string | undefined) {
  return useQuery({
    queryKey: keys.sensor(name ?? ''),
    queryFn: () => api.get<SensorDetail>(`/sensors/${name}`),
    enabled: Boolean(name),
  })
}

export function useRenderParameters(name: string) {
  return useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post<{ parameters: string }>(`/sensors/${name}/render-parameters`, { values }),
  })
}

export function useSensorProfiles(name: string | undefined, enabled = true) {
  return useQuery({
    queryKey: keys.sensorProfiles(name ?? ''),
    queryFn: () => api.get<SensorProfile[]>(`/sensors/${name}/profiles`),
    enabled: Boolean(name) && enabled,
  })
}

export function useSensorProfile(
  name: string | undefined,
  profile: string | undefined,
) {
  return useQuery({
    queryKey: keys.sensorProfile(name ?? '', profile ?? ''),
    queryFn: () =>
      api.get<SensorProfileDetail>(`/sensors/${name}/profiles/${profile}`),
    enabled: Boolean(name && profile),
  })
}

/**
 * Store a variant and hand it to the probes it is meant to be on.
 *
 * Files go first: the profile carries their paths, and the sensor checks that
 * a path it is given exists. Uploading afterwards would deploy a profile that
 * points at a file the probe has not seen yet.
 */
export function useWriteSensorProfile(name: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({
      profile,
      values,
      probeIds,
      files,
    }: {
      profile: string
      values: Record<string, string>
      probeIds: string[]
      files: { key: string; contentBase64: string }[]
    }) => {
      for (const file of files) {
        await api.put<SensorProfileFile>(
          `/sensors/${name}/profiles/${profile}/files/${file.key}`,
          { content_base64: file.contentBase64 },
        )
      }
      return api.put<JobAccepted>(`/sensors/${name}/profiles/${profile}`, {
        values,
        probes: probeIds,
      })
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.sensorProfiles(name) })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useDeleteSensorProfile(name: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (profile: string) =>
      api.delete<JobAccepted>(`/sensors/${name}/profiles/${profile}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.sensorProfiles(name) })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useDeployments() {
  return useQuery({
    queryKey: keys.deployments,
    queryFn: () => api.get<Deployment[]>('/deployments'),
    refetchInterval: LIVE_REFETCH_MS,
  })
}

export function useDeployment(id: string | undefined) {
  return useQuery({
    queryKey: keys.deployment(id ?? ''),
    queryFn: () => api.get<Deployment>(`/deployments/${id}`),
    enabled: Boolean(id),
  })
}

export function useCreateDeployment() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { sensor: string; probe_ids: string[]; dry_run: boolean }) =>
      api.post<Deployment>('/deployments', body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.deployments })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useJobs(filters: Record<string, string | undefined> = {}) {
  return useQuery({
    queryKey: keys.jobs(filters),
    queryFn: () => api.get<JobSummary[]>('/jobs', filters),
    refetchInterval: LIVE_REFETCH_MS,
  })
}

export function useJob(
  id: string | undefined,
  options?: Partial<UseQueryOptions<JobDetail>>,
) {
  return useQuery({
    queryKey: keys.job(id ?? ''),
    queryFn: () => api.get<JobDetail>(`/jobs/${id}`),
    enabled: Boolean(id),
    ...options,
  })
}

export function useJobLog(id: string | undefined) {
  return useQuery({
    queryKey: keys.jobLog(id ?? ''),
    queryFn: () => api.get<JobEvent[]>(`/jobs/${id}/log`),
    enabled: Boolean(id),
  })
}

export function useRetryJob() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post<JobAccepted>(`/jobs/${id}/retry`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

export function useCancelJob() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post<JobSummary>(`/jobs/${id}/cancel`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

export function useAuditEvents(filters: Record<string, string | undefined> = {}) {
  return useQuery({
    queryKey: keys.audit(filters),
    queryFn: () => api.get<AuditEvent[]>('/audit-events', filters),
  })
}

export function useCertificates() {
  return useQuery({
    queryKey: keys.certificates,
    queryFn: () => api.get<Certificate[]>('/certificates'),
  })
}

export function useOverlay() {
  return useQuery({
    queryKey: keys.overlay,
    queryFn: () => api.get<Overlay>('/overlay'),
  })
}

/**
 * The three probe-facing overlay actions.
 *
 * All of them take a selection rather than one probe: moving a site onto the
 * tunnel is the realistic operation, and doing it one detail page at a time is
 * how half a site ends up in a different mode than the rest of it.
 */
/**
 * Turning the overlay on and off.
 *
 * Synchronous rather than a job: it is a settings form, and its mistakes - an
 * endpoint that is the NATS address, a subnet probes already hold addresses
 * from - are worth refusing in front of the person typing.
 */
export function useOverlayEnable() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (request: {
      endpoint_host: string
      port?: number
      subnet?: string
      default_mode?: OverlayMode
    }) => api.post<Overlay>('/overlay/enable', request),
    onSuccess: () => invalidateOverlay(client),
  })
}

export function useOverlayDisable() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<Overlay>('/overlay/disable', {}),
    onSuccess: () => invalidateOverlay(client),
  })
}

export function useOverlayAttach() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (request: { probe_ids: string[]; mode?: OverlayMode }) =>
      api.post<JobAccepted>('/overlay/peers', request),
    onSuccess: () => invalidateOverlay(client),
  })
}

export function useOverlayMode() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (request: {
      probe_ids: string[]
      mode: OverlayMode
      force?: boolean
    }) => api.post<JobAccepted>('/overlay/peers/mode', request),
    onSuccess: () => invalidateOverlay(client),
  })
}

export function useOverlayDetach() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (request: { probe_ids: string[]; force?: boolean }) =>
      api.post<JobAccepted>('/overlay/peers/remove', request),
    onSuccess: () => invalidateOverlay(client),
  })
}

export function useOverlayRefresh() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (request: { probe_ids: string[] }) =>
      api.post<JobAccepted>('/overlay/peers/refresh', request),
    onSuccess: () => invalidateOverlay(client),
  })
}

function invalidateOverlay(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: keys.overlay })
  void client.invalidateQueries({ queryKey: keys.probes })
  void client.invalidateQueries({ queryKey: ['jobs'] })
}

export function useIperfEndpoints(enabled = true) {
  return useQuery({
    queryKey: keys.iperf,
    queryFn: () => api.get<IperfEndpoint[]>('/iperf-endpoints'),
    // The argument is for callers that only sometimes need the list - the
    // rollout dialog asks only for a sensor that measures against an endpoint,
    // and only when the reader may see them at all.
    enabled,
  })
}

export function useIperfEndpoint(name: string | undefined) {
  return useQuery({
    queryKey: [...keys.iperf, name],
    queryFn: () => api.get<IperfEndpoint>(`/iperf-endpoints/${name}`),
    enabled: Boolean(name),
  })
}

/** Read a host's SSH keys without signing in, so a person can accept them
 *  before any administrator credential travels to that address. */
export function useScanHostKeys() {
  return useMutation({
    mutationFn: (body: { host: string; ssh_port?: number }) =>
      api.post<HostKeyScan>('/iperf-endpoints/host-keys', body),
  })
}

export function useProvisionEndpoint() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (request: ProvisionEndpointRequest) =>
      api.post<JobAccepted>('/iperf-endpoints', request),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.iperf })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useRegisterEndpoint() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (request: RegisterEndpointRequest) =>
      api.post<IperfEndpoint>('/iperf-endpoints/register', request),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.iperf }),
  })
}

export function useRotateEndpoint() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (name: string) =>
      api.post<JobAccepted>(`/iperf-endpoints/${name}/rotate`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.iperf })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useUpdateForeignEndpointCredentials() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ name, password }: { name: string; password: string }) =>
      api.put<JobAccepted>(`/iperf-endpoints/${name}/credentials`, { password }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.iperf })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/**
 * Which probes hold one endpoint's credentials.
 *
 * The list showed a "deployed to" count that nothing on this page could
 * change: widening it meant rolling the whole sensor out again, narrowing it
 * meant a terminal. Both directions are the same shape, so they are one hook
 * with the verb as an argument.
 */
export function useEndpointDeployment(action: 'deploy' | 'revoke') {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ name, probes }: { name: string; probes: string[] }) =>
      api.post<JobAccepted>(`/iperf-endpoints/${name}/${action}`, { probes }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.iperf })
      void client.invalidateQueries({ queryKey: keys.probes })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useRemoveEndpoint() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ name, keepService }: { name: string; keepService: boolean }) =>
      api.delete<JobAccepted>(
        `/iperf-endpoints/${name}${keepService ? '?keep_service=true' : ''}`,
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.iperf })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

// --- Maintenance -------------------------------------------------------------

export function useBackups(enabled = true) {
  return useQuery({
    queryKey: keys.backups,
    queryFn: () => api.get<BackupFile[]>('/system/backups'),
    enabled,
  })
}

export function useVerifySystem() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<JobAccepted>('/system/verify'),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

export function useCreateBackup() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<JobAccepted>('/system/backup'),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.backups })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useExportRuntime() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<JobAccepted>('/system/export'),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.backups })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useRestartNats() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<JobAccepted>('/system/restart'),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

export function useRenewServerCertificate() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<JobAccepted>('/certificates/server/renew'),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.certificates })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useUsers() {
  return useQuery({
    queryKey: keys.users,
    queryFn: () => api.get<WebUser[]>('/users'),
  })
}

export function useLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { username: string; password: string }) =>
      api.post<AuthState>('/auth/login', body),
    onSuccess: (state) => {
      client.setQueryData(keys.auth, state)
      void client.invalidateQueries()
    },
  })
}

export function useSetup() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { username: string; password: string; display_name: string }) =>
      api.post<AuthState>('/auth/setup', body),
    onSuccess: (state) => client.setQueryData(keys.auth, state),
  })
}

export function useLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<void>('/auth/logout'),
    onSuccess: () => {
      client.clear()
    },
  })
}

/**
 * One invitation, polled while the wizard waits for a host to report in.
 *
 * By id and not through the list above: the list holds open invitations, and
 * redeeming an invitation is what writes the job id the wizard is waiting for
 * - so following the list would lose the record at exactly the wrong moment.
 * There is nothing to push here, the host talks to the platform, not to the
 * browser.
 */
export function useInvitation(
  id: string | null,
  options?: { refetchInterval?: number | false },
) {
  return useQuery({
    queryKey: keys.invitation(id ?? ''),
    queryFn: () => api.get<Invitation>(`/probes/enrollment/tokens/${id}`),
    enabled: id !== null,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

/**
 * Every invitation that could still be used. The cache key existed since the
 * beginning - two mutations invalidated it and nothing ever read it, which
 * left an open invitation invisible the moment its tab closed.
 */
export function useInvitations(enabled = true) {
  return useQuery({
    queryKey: keys.invitations,
    queryFn: () => api.get<Invitation[]>('/probes/enrollment/tokens'),
    enabled,
  })
}

export function useIperfInvitations(enabled = true) {
  return useQuery({
    queryKey: keys.iperfInvitations,
    queryFn: () => api.get<IperfInvitation[]>('/iperf-endpoints/enrollment/tokens'),
    enabled,
  })
}

/** Watched by id while the dialog waits - redemption removes the record from
 *  the open list in the same request that gives it its job. */
export function useIperfInvitation(
  id: string | null,
  options?: { refetchInterval?: number | false },
) {
  return useQuery({
    queryKey: [...keys.iperfInvitations, id],
    queryFn: () => api.get<IperfInvitation>(`/iperf-endpoints/enrollment/tokens/${id}`),
    enabled: id !== null,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

export function useCreateIperfInvitation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (request: IperfInvitationRequest) =>
      api.post<IssuedIperfInvitation>('/iperf-endpoints/enrollment/tokens', request),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: keys.iperfInvitations }),
  })
}

export function useRevokeIperfInvitation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.delete<void>(`/iperf-endpoints/enrollment/tokens/${id}`),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: keys.iperfInvitations }),
  })
}

export function useCreateInvitation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (request: InvitationRequest) =>
      api.post<IssuedInvitation>('/probes/enrollment/tokens', request),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.invitations }),
  })
}

export function useRevokeInvitation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.delete<void>(`/probes/enrollment/tokens/${id}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.invitations }),
  })
}

export function useNatsAccounts() {
  return useQuery({
    queryKey: keys.credentials,
    queryFn: () => api.get<NatsAccount[]>('/credentials'),
  })
}

export function useCreateNatsAccount() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (username: string) =>
      api.post<NatsAccount>('/credentials', { username }),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.credentials }),
  })
}

export function useRotateNatsAccount() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (username: string) =>
      api.post<JobAccepted>(`/credentials/${username}/rotate`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.credentials })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useDeleteNatsAccount() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (username: string) => api.delete<void>(`/credentials/${username}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.credentials }),
  })
}

export function useRevealNatsPassword() {
  return useMutation({
    mutationFn: (username: string) =>
      api.get<{ username: string; password: string }>(
        `/credentials/${username}/reveal`,
      ),
  })
}

export function useRevealAccessKey() {
  return useMutation({
    mutationFn: (id: string) => api.get<RevealedAccessKey>(`/probes/${id}/access-key`),
  })
}

export function useConfigureProbe() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.post<JobAccepted>('/probes/actions/configure', { probe_ids: [id] }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

export function useExecuteReconcile() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.post<JobAccepted>(`/probes/${id}/reconcile?dry_run=false`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

export type UnenrollOptions = {
  removeSensors?: boolean
  uninstallMpp?: boolean
  deleteAccount?: boolean
}

export function useUnenrollProbe() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...options }: UnenrollOptions & { id: string }) => {
      const query = new URLSearchParams({
        remove_sensors: String(options.removeSensors ?? false),
        uninstall_mpp: String(options.uninstallMpp ?? false),
        delete_account: String(options.deleteAccount ?? false),
      })
      return api.delete<JobAccepted>(`/probes/${id}?${query.toString()}`)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.probes })
      void client.invalidateQueries({ queryKey: keys.credentials })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useRemoveSensorFromProbe() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ probeId, sensor }: { probeId: string; sensor: string }) =>
      api.post<JobAccepted>(`/probes/${probeId}/sensors/${sensor}/remove`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

export function useWirelessInterfaces(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: keys.probeInterfaces(id ?? ''),
    queryFn: () => api.get<WirelessInterface[]>(`/probes/${id}/wireless-interfaces`),
    enabled: Boolean(id) && enabled,
    // Asked live rather than served from the observed-state cache: the answer
    // decides which interface somebody hands over, and a stale one would offer
    // an interface that has since been given a connection.
    staleTime: 0,
  })
}

export function useReserveInterface() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      probeId,
      sensor,
      iface,
    }: {
      probeId: string
      sensor: string
      iface: string
    }) =>
      api.post<JobAccepted>(
        `/probes/${probeId}/sensors/${sensor}/interfaces/${iface}/reserve`,
      ),
    onSuccess: (_result, variables) => {
      void client.invalidateQueries({ queryKey: keys.probeInterfaces(variables.probeId) })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useReleaseInterface() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      probeId,
      sensor,
      iface,
    }: {
      probeId: string
      sensor: string
      iface: string
    }) =>
      api.post<JobAccepted>(
        `/probes/${probeId}/sensors/${sensor}/interfaces/${iface}/release`,
      ),
    onSuccess: (_result, variables) => {
      void client.invalidateQueries({ queryKey: keys.probeInterfaces(variables.probeId) })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useSetupStack() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<JobAccepted>('/system/setup'),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.capabilities })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useCreateUser() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      username: string
      password: string
      display_name: string
      roles: string[]
      must_change_password: boolean
    }) => api.post<WebUser>('/users', body),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.users }),
  })
}

export function useUpdateUser() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
      api.patch<WebUser>(`/users/${id}`, body),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.users }),
  })
}

export function useDeleteUser() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/users/${id}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.users }),
  })
}


// --- Availability monitoring -------------------------------------------------

/** ``?label=team:support&label=site:hamburg`` - repeated, not comma-joined,
    because a label value may contain a comma and a key never a colon. */
function labelQuery(labels: string[]): string {
  return labels.map((label) => `label=${encodeURIComponent(label)}`).join('&')
}

/**
 * The dashboard, in one request and on a live refresh.
 *
 * This is a page somebody leaves open on a wall display, which is the whole
 * reason the server answers it as one document: a device per request would
 * make a support desk's own monitoring the busiest client of the API.
 */
export function useWatchOverview(labels: string[] = []) {
  return useQuery({
    queryKey: keys.watchOverview(labels),
    queryFn: () =>
      api.get<WatchOverview>(
        `/watch/overview${labels.length ? `?${labelQuery(labels)}` : ''}`,
      ),
    refetchInterval: LIVE_REFETCH_MS,
  })
}

export function useWatchAvailability(deviceId: string | undefined, days: number) {
  return useQuery({
    queryKey: keys.watchAvailability(deviceId ?? '', days),
    queryFn: () =>
      api.get<WatchAvailability>(`/watch/devices/${deviceId}/availability`, {
        days,
      }),
    enabled: Boolean(deviceId),
  })
}

export function useWatchOutages(days: number, labels: string[] = []) {
  return useQuery({
    queryKey: keys.watchOutages(days, labels),
    queryFn: () =>
      api.get<WatchOutage[]>(
        `/watch/outages?days=${days}${labels.length ? `&${labelQuery(labels)}` : ''}`,
      ),
    refetchInterval: LIVE_REFETCH_MS,
  })
}

export function useCreateWatchDevice() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: WatchDeviceRequest) =>
      api.post<WatchDevice>('/watch/devices', body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.watch })
    },
  })
}

export function useUpdateWatchDevice() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<WatchDeviceRequest> & { id: string }) =>
      api.patch<WatchDevice>(`/watch/devices/${id}`, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.watch })
    },
  })
}

export function useDeleteWatchDevice() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/watch/devices/${id}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.watch })
    },
  })
}
