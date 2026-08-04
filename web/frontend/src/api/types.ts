/**
 * The shapes the API returns.
 *
 * Hand-written rather than generated for now: the surface is small, and a
 * generated client would still need this layer to give the enums real union
 * types. The OpenAPI schema at /api/openapi.json is the reference.
 */

export type ProbeStatus =
  | 'pending'
  | 'enrolled'
  | 'healthy'
  | 'degraded'
  | 'unreachable'
  | 'retired'

export type SensorInstallationStatus =
  | 'absent'
  | 'current'
  | 'outdated'
  | 'drifted'
  | 'failed'
  | 'unmanaged'

export type JobStatus =
  | 'queued'
  | 'running'
  | 'successful'
  | 'failed'
  | 'cancelled'
  | 'partially_successful'

export type JobStepStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'
export type LogLevel = 'debug' | 'info' | 'warning' | 'error'
export type ServiceState = 'active' | 'inactive' | 'unknown'
export type CaState = 'ok' | 'missing' | 'mismatched' | 'unknown'
export type NatsConnectionState = 'connected' | 'disconnected' | 'unknown'
export type CertificateStatus =
  | 'valid'
  | 'expiring_soon'
  | 'expired'
  | 'mismatched'
  | 'missing'
export type AuditResult = 'success' | 'failure' | 'denied'
export type AlertSeverity = 'info' | 'warning' | 'critical'
export type DeviationSeverity = 'info' | 'warning' | 'critical'

/** The error envelope. `message_key` and `params` are translated here; `details` never is. */
export interface ApiErrorBody {
  code: string
  message_key: string
  params: Record<string, unknown>
  fields: string[]
  details: string | null
  correlation_id: string | null
  retryable: boolean
}

export interface Principal {
  user_id: string
  username: string
  display_name: string
  roles: string[]
  permissions: string[]
  locale: string
  is_development: boolean
  must_change_password: boolean
}

export interface AuthState {
  authenticated: boolean
  setup_required: boolean
  dev_auth: boolean
  principal: Principal | null
}

export interface ProbeSummary {
  id: string
  nats_username: string
  display_name: string | null
  host: string
  probe_name: string | null
  status: ProbeStatus
  service: ServiceState
  package_version: string | null
  ca_state: CaState
  nats_connection: NatsConnectionState
  sensor_count: number
  deviation_count: number
  observed_at: string | null
  stale: boolean
  running_job_id: string | null
  error_code: string | null
  helper_version: number | null
  helper_outdated: boolean
}

export interface SensorState {
  name: string
  status: SensorInstallationStatus
  desired_version: string | null
  installed_version: string | null
  installed_sha256: string | null
  expected_sha256: string | null
  interfaces: string[]
  helper_state: string | null
}

export interface Deviation {
  kind: string
  severity: DeviationSeverity
  object_type: string
  object_ref: string
  expected: string | null
  actual: string | null
  remediation: string | null
  params: Record<string, string>
}

export interface ObservedState {
  observed_at: string
  reachable: boolean
  service: ServiceState
  package_version: string | null
  hostname: string | null
  ca_sha256: string | null
  config_path: string | null
  probe_id: string | null
  probe_name: string | null
  helper_version: number | null
  helper_sha256: string | null
  helper_outdated: boolean
  error_code: string | null
  error_details: string | null
}

export interface ProbeInventory {
  ssh_host: string
  ssh_port: number
  probe_id: string | null
  probe_name: string | null
  access_key_present: boolean
  pending_transaction: string | null
  assigned_sensors: string[]
  known_iperf_endpoints: string[]
}

/** Only ever the answer of the audited reveal endpoint, never part of a list. */
export interface RevealedAccessKey {
  nats_username: string
  access_key: string
}

export interface ProbeDetail {
  summary: ProbeSummary
  inventory: ProbeInventory
  observed: ObservedState | null
  sensors: SensorState[]
  deviations: Deviation[]
  notes: string | null
  labels: Record<string, string>
}

export interface PlannedAction {
  kind: string
  target: string
  description_key: string
  params: Record<string, string>
  restarts_service: boolean
  risk_key: string | null
}

export interface ReconciliationPlan {
  probe_username: string
  deviations: Deviation[]
  actions: PlannedAction[]
  restarts_service: boolean
  is_empty: boolean
}

export interface SensorSummary {
  name: string
  version: string
  description: string
  needs_interface: boolean
  requires_privileged_helper: boolean
  iperf_kind: string | null
  has_parameter_schema: boolean
  installed_on: number
  outdated_on: number
}

export interface SensorFile {
  slot: string
  relative_path: string
  size_bytes: number
  sha256: string
}

export interface ParameterField {
  name: string
  type: 'string' | 'integer' | 'boolean' | 'choice'
  required?: boolean
  default?: unknown
  choices?: string[]
  label_key?: string
  description_key?: string
  sensitive?: boolean
  group?: string
  minimum?: number
  maximum?: number
  placeholder?: string
}

export interface ParameterSchema {
  fields: ParameterField[]
}

export interface SensorDetail extends Omit<SensorSummary, 'has_parameter_schema'> {
  files: SensorFile[]
  parameter_schema: ParameterSchema | null
  readme: string | null
  profile_template: string | null
  probes: string[]
}

export interface JobStep {
  name: string
  position: number
  status: JobStepStatus
  started_at: string | null
  finished_at: string | null
  target_label: string | null
}

export interface JobEvent {
  sequence: number
  ts: string
  level: LogLevel
  step: string | null
  target: string | null
  code: string
  params: Record<string, unknown>
  raw: string | null
}

export interface JobSummary {
  id: string
  type: string
  status: JobStatus
  target_type: string | null
  target_id: string | null
  target_label: string | null
  progress: number
  current_step: string | null
  requested_by_name: string | null
  trigger: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_seconds: number | null
  blocked_reason_key: string | null
  blocked_by_job_id: string | null
  error_code: string | null
}

export interface JobDetail extends JobSummary {
  steps: JobStep[]
  payload: Record<string, unknown>
  result: Record<string, unknown> | null
  error_params: Record<string, unknown> | null
  error_details: string | null
  retry_of_job_id: string | null
}

export interface DeploymentTarget {
  probe_id: string
  probe_label: string
  status: JobStatus
  previous_version: string | null
  error_code: string | null
  error_details: string | null
  finished_at: string | null
}

export interface Deployment {
  id: string
  sensor_name: string
  sensor_version: string
  status: JobStatus
  job_id: string | null
  dry_run: boolean
  requested_by_name: string | null
  created_at: string
  targets: DeploymentTarget[]
}

export interface AuditEvent {
  id: string
  ts: string
  actor_name: string
  source_ip: string | null
  action: string
  object_type: string
  object_id: string | null
  object_label: string | null
  result: AuditResult
  error_code: string | null
  job_id: string | null
  before_state: Record<string, unknown> | null
  after_state: Record<string, unknown> | null
  comment: string | null
}

export interface Certificate {
  kind: 'ca' | 'server'
  path: string
  status: CertificateStatus
  subject: string | null
  issuer: string | null
  not_after: string | null
  days_remaining: number | null
  sha256: string | null
  subject_alt_names: string[]
  key_matches: boolean | null
}

export interface Capabilities {
  docker: boolean
  runtime_state: 'missing' | 'partial' | 'complete'
  dev_auth: boolean
}

export interface NatsAccount {
  username: string
  is_shared: boolean
  has_auth_entry: boolean
  probe_enrolled: boolean
}

export interface SiteSettings {
  nats_fqdn: string | null
  nats_port: number
  nats_host_ip: string | null
  ca_http_port: number
  ca_organization: string
  prtg_core_ip: string | null
  nats_endpoint: string | null
  is_configured: boolean
}

export interface JetStreamState {
  enabled: boolean
  streams: number
  consumers: number
  messages: number
  bytes_used: number
  store_used: number
  store_limit: number
  store_usage_ratio: number | null
}

export interface NatsState {
  available: boolean
  healthy: boolean
  server_name: string | null
  version: string | null
  uptime: string | null
  connections: number
  slow_consumers: number
  jetstream: JetStreamState | null
  connected_user_count: number
  error_details: string | null
}

export interface ContainerState {
  name: string
  exists: boolean
  running: boolean
  status: string | null
  health: string | null
  image: string | null
  restart_count: number
}

export interface SystemStatus {
  site: SiteSettings
  nats: NatsState
  containers: ContainerState[]
  certificates: Certificate[]
  capabilities: Capabilities
  runtime_missing: string[]
}

export interface Alert {
  id: string
  kind: string
  severity: AlertSeverity
  object_type: string
  object_ref: string
  object_label: string
  params: Record<string, string>
  first_seen_at: string
  last_seen_at: string
  acknowledged_at: string | null
}

export interface Dashboard {
  system: SystemStatus
  probe_total: number
  probe_healthy: number
  probe_degraded: number
  probe_unreachable: number
  probes_with_deviations: number
  failed_jobs_24h: number
  running_jobs: number
  expiring_certificates: Certificate[]
  alerts: Alert[]
  recent_jobs: JobSummary[]
  recent_audit: AuditEvent[]
}

export interface IperfEndpoint {
  name: string
  host: string
  port: number
  username: string
  kind: string
  updated_at: string | null
  has_public_key: boolean
  /** False for an endpoint somebody else operates: its password is not ours
   *  to rotate, and removing it here takes nothing off that host. */
  managed: boolean
  deployed_to: string[]
}

export interface JobAccepted {
  job_id: string
  status: JobStatus
  events_url: string
}

/** An open invitation for one host to enrol itself. Never carries the token. */
export interface Invitation {
  id: string
  kind: string
  nats_username: string | null
  probe_name: string | null
  expected_host: string | null
  expires_at: string
  created_by_name: string | null
  redeemed_at: string | null
  revoked_at: string | null
  source_ip: string | null
  job_id: string | null
}

/**
 * What creating an invitation returns. The token is in here once and is never
 * retrievable again - it is not stored in the clear, only its hash is.
 */
export interface IssuedInvitation extends Invitation {
  token: string
  command: string
  ca_sha256: string
}

export interface InvitationRequest {
  nats_username: string
  probe_name?: string | null
  expected_host?: string | null
  install_package?: boolean
  ttl_minutes?: number
}

export interface WebUser {
  id: string
  username: string
  display_name: string
  email: string | null
  roles: string[]
  is_active: boolean
  locale: string
  last_login_at: string | null
  locked_until: string | null
  created_at: string
}
