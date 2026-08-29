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
  /**
   * Still going, but not in the process that started it. A stack update
   * replaces the API container, so the work carries on in a container that
   * outlives it. Not terminal - the outcome is recorded on the way back up.
   */
  | 'detached'
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
  installed_helper_sha256: string | null
  expected_helper_sha256: string | null
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

export interface WirelessInterface {
  name: string
  reserved_by: string | null
  carries_default_route: boolean
  operstate: string | null
  nm_state: string | null
  connection: string | null
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
  supports_profiles: boolean
  installed_on: number
  outdated_on: number
}

export interface SensorFile {
  slot: string
  relative_path: string
  size_bytes: number
  sha256: string
}

export type FieldType = 'string' | 'integer' | 'boolean' | 'choice'

interface FieldBase {
  name: string
  required?: boolean
  description?: string
  label_key?: string
  description_key?: string
  group?: string
}

/** One option of the sensor, as the PRTG parameter line takes it. */
export interface ParameterField extends FieldBase {
  type: FieldType
  default?: unknown
  choices?: string[]
  minimum?: number
  maximum?: number
  placeholder?: string
  /** argparse action="append": the flag is repeated, not given a list. */
  repeatable?: boolean
  /** 'prtg' means PRTG substitutes the value - show the placeholder, do not ask. */
  source?: 'manual' | 'prtg'
  prtg_placeholder?: string | null
}

/** One KEY=VALUE line of a variant - a setting or a credential. */
export interface ProfileField extends FieldBase {
  type: FieldType
  default?: unknown
  choices?: string[]
  /** Never sent back by the API; an empty input means "leave as it is". */
  sensitive?: boolean
  /** The parameter this key stands in for, so PRTG can leave it out. */
  maps_to?: string | null
}

/** A certificate or key that travels with a variant. */
export interface FileField extends FieldBase {
  kind?: string
  secret?: boolean
  max_bytes: number
  extension: string
  maps_to?: string | null
}

export interface ParameterSchema {
  parameters: ParameterField[]
  settings: ProfileField[]
  credentials: ProfileField[]
  files: FileField[]
  supports_profiles: boolean
  default_parameter_line: string
}

/** One probe that reports the sensor installed, and whether it is current. */
export interface SensorInstallation {
  probe: string
  version: string
  current: boolean
}

export interface SensorDetail extends Omit<SensorSummary, 'has_parameter_schema'> {
  files: SensorFile[]
  parameter_schema: ParameterSchema | null
  readme: string | null
  profile_template: string | null
  installations: SensorInstallation[]
}

/** An uploaded certificate or key. Described, never handed back. */
export interface SensorProfileFile {
  key: string
  filename: string
  size_bytes: number
  sha256: string
  /** Where it sits on the probe, which is also what stands in the profile. */
  probe_path: string
}

/** One variant of a sensor: one SSID, one endpoint, one site. */
export interface SensorProfile {
  sensor: string
  name: string
  updated_at: string | null
  probes: string[]
  files: SensorProfileFile[]
  /** The line that selects this variant in PRTG. */
  parameter_line: string
}

export interface SensorProfileDetail extends SensorProfile {
  /** Settings only - a credential is never sent back. */
  values: Record<string, string>
  /** Names of the credentials that are stored, so the form can say so. */
  secrets_set: string[]
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
  /** Whether this installation can update itself from its own checkout. */
  stack_update: boolean
}

export interface StackCommit {
  sha: string
  subject: string
  date: string
}

/**
 * Three commits rather than one version, because they diverge in ways an
 * operator has to be able to tell apart. The most common divergence is a
 * checkout that was pulled but never rebuilt, which a single version number
 * would report as up to date.
 */
export interface StackVersion {
  running_commit: string
  /** What `git describe` called it, when the repository has tags. */
  running_version: string
  checkout_commit: string
  checkout_dirty: boolean
  remote_commit: string
  branch: string
  state:
    | 'current'
    | 'update_available'
    | 'rebuild_pending'
    | 'unreachable'
    | 'unknown'
  reachable: boolean
  error: string
  commits: StackCommit[]
  checked_at: string | null
  /** When this installation was last updated from here, and to what. */
  last_update_at: string | null
  last_update_commit: string
  /** The job that ran it, so its log can be reached after the reload. */
  last_update_job_id: string
  checkout_dir: string | null
  available: boolean
  unavailable_reason: string | null
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

/**
 * One probe holding an endpoint's credentials, and what PRTG needs there.
 *
 * The parameter line belongs to the pair, not to the endpoint. A probe that
 * holds this endpoint alone also carries the "default" profile alias, and a
 * sensor object there needs no connection parameter at all - it reads address,
 * port and user out of that profile. From the second endpoint on the alias is
 * gone and every object has to name its own.
 */
export interface IperfHolder {
  probe: string
  /** Registered endpoints this probe holds in total. One is the threshold the
   *  alias hangs on, which is what makes a warning before crossing it
   *  possible. */
  endpoints_held: number
  uses_default_alias: boolean
  /** Empty exactly when the alias applies: nothing to paste is the answer. */
  parameter_line: string
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
  holders: IperfHolder[]
}

export interface HostKeyOffer {
  line: string
  algorithm: string
  fingerprint: string
}

export interface HostKeyScan {
  host: string
  ssh_port: number
  keys: HostKeyOffer[]
  already_pinned: boolean
}

/** The one-time sign-in. Held in component state for the length of one form
 *  and never stored anywhere - the server does not echo it back either. */
export interface AdminSignIn {
  username: string
  password?: string
  private_key?: string
  key_passphrase?: string
  sudo_password?: string
}

export interface ProvisionEndpointRequest {
  name: string
  host: string
  ssh_port?: number
  iperf_port?: number
  username?: string
  ssh_source_cidr?: string | null
  host_keys: string[]
  admin: AdminSignIn
}

export interface RegisterEndpointRequest {
  name: string
  host: string
  port?: number
  username?: string
  password?: string
  public_key_pem?: string | null
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

/** An open invitation for an iperf endpoint to enrol itself. */
export interface IperfInvitation {
  id: string
  kind: string
  name: string | null
  expected_host: string | null
  iperf_port: number | null
  username: string | null
  ssh_source_cidr: string | null
  expires_at: string
  created_by_name: string | null
  redeemed_at: string | null
  revoked_at: string | null
  source_ip: string | null
  job_id: string | null
}

export interface IssuedIperfInvitation extends IperfInvitation {
  token: string
  command: string
  ca_sha256: string
}

export interface IperfInvitationRequest {
  name: string
  expected_host?: string | null
  iperf_port?: number
  username?: string
  ssh_source_cidr?: string | null
  ttl_minutes?: number
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
