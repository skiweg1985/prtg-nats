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
  Capabilities,
  Certificate,
  Dashboard,
  Deployment,
  Invitation,
  InvitationRequest,
  IperfEndpoint,
  IssuedInvitation,
  JobAccepted,
  JobDetail,
  JobEvent,
  JobSummary,
  NatsAccount,
  ObservedState,
  ParameterSchema,
  ProbeDetail,
  ProbeSummary,
  ReconciliationPlan,
  RevealedAccessKey,
  SensorDetail,
  SensorSummary,
  SystemStatus,
  WebUser,
} from './types'

/** One place that names every cache entry, so invalidation cannot go stale. */
export const keys = {
  auth: ['auth'] as const,
  capabilities: ['capabilities'] as const,
  dashboard: ['dashboard'] as const,
  system: ['system'] as const,
  probes: ['probes'] as const,
  probe: (id: string) => ['probes', id] as const,
  probePlan: (id: string) => ['probes', id, 'plan'] as const,
  sensors: ['sensors'] as const,
  sensor: (name: string) => ['sensors', name] as const,
  sensorSchema: (name: string) => ['sensors', name, 'schema'] as const,
  deployments: ['deployments'] as const,
  deployment: (id: string) => ['deployments', id] as const,
  jobs: (filters?: Record<string, unknown>) => ['jobs', filters ?? {}] as const,
  job: (id: string) => ['jobs', id] as const,
  jobLog: (id: string) => ['jobs', id, 'log'] as const,
  audit: (filters?: Record<string, unknown>) => ['audit', filters ?? {}] as const,
  certificates: ['certificates'] as const,
  credentials: ['credentials'] as const,
  invitations: ['probes', 'invitations'] as const,
  invitation: (id: string) => ['probes', 'invitations', id] as const,
  iperf: ['iperf'] as const,
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

export function useProbe(id: string | undefined) {
  return useQuery({
    queryKey: keys.probe(id ?? ''),
    queryFn: () => api.get<ProbeDetail>(`/probes/${id}`),
    enabled: Boolean(id),
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
    mutationFn: (id: string) => api.post<JobAccepted>(`/probes/${id}/${action}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['jobs'] })
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

export function useSensorParameterSchema(name: string | undefined) {
  return useQuery({
    queryKey: keys.sensorSchema(name ?? ''),
    queryFn: () => api.get<ParameterSchema>(`/sensors/${name}/parameter-schema`),
    enabled: Boolean(name),
  })
}

export function useRenderParameters(name: string) {
  return useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post<{ parameters: string }>(`/sensors/${name}/render-parameters`, { values }),
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

export function useIperfEndpoints() {
  return useQuery({
    queryKey: keys.iperf,
    queryFn: () => api.get<IperfEndpoint[]>('/iperf-endpoints'),
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
    mutationFn: (id: string) => api.post<JobAccepted>(`/probes/${id}/configure`),
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
