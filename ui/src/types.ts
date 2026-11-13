export interface GeneratedFile {
  path: string;
  language: string;
  purpose: string;
  content: string;
}

export interface StageMetric {
  name: string;
  duration_ms: number;
  detail: string;
}

export interface UsageStats {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface AgentStats {
  total_ms: number;
  retrieval_ms: number;
  model_ms: number;
  validation_ms: number;
  iterations: number;
  retrieved_chunks: number;
  usage: UsageStats;
  stages: StageMetric[];
}

export interface AgentTaskRequest {
  task: string;
  document_ids?: string[];
  session_id?: string | null;
  project_id?: string | null;
  volume_name?: string | null;
  profile_id?: string | null;
  save_project_context?: boolean;
  pypi_packages?: string[];
  enable_web_search?: boolean | null;
  response_language?: "pl" | "en" | "auto";
  allow_cross_project_transfers?: boolean;
}

export interface ChappieSettingsBundle {
  responseLanguage: "pl" | "en" | "auto";
  uiScale: string;
  enableWebSearch: boolean;
  saveProjectContext: boolean;
  autoSaveCookie: boolean;
  appTheme: "dark" | "cyberpunk" | "contrast";
  defaultKnowledgeProfileId: string;
  visibilityMode: "all" | "active";
  allowCrossProjectTransfers?: boolean;
}

export interface ProjectFileTransferRequest {
  source_path: string;
  target_project_id: string;
  target_path: string;
  move?: boolean;
  allow_cross_project?: boolean;
}

export interface ProjectFileTransferResponse {
  success: boolean;
  source_project_id: string;
  source_path: string;
  target_project_id: string;
  target_path: string;
  bytes_transferred: number;
  is_move: boolean;
  message?: string;
}

export interface AgentResponse {
  request_id: string;
  session_id: string;
  version: string;
  answer: string;
  rationale: string[];
  files: GeneratedFile[];
  project_context?: ProjectContext | null;
  stats: AgentStats;
  created_at: string;
}

export interface DocumentSummary {
  document_id: string;
  filename: string;
  title: string;
  pages: number;
  chunks: number;
}

export interface DocumentIndexResponse extends DocumentSummary {
  duration_ms: number;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  services: Record<string, string>;
}

export type ServiceStatusType = "online" | "offline" | "starting";

export interface ServiceStateItem {
  name: string;
  status: ServiceStatusType;
  detail: string;
}

export interface WorkspaceRootInfo {
  id: number;
  root_path: string;
  is_active: boolean;
  excluded_files: string[];
}

export interface WorkspaceStateResponse {
  enabled: boolean;
  service_status: "connected" | "unavailable";
  active_roots: string[];
  roots: WorkspaceRootInfo[];
  effective_excluded_paths: string[];
}

export interface AgentRuleItem {
  id: number;
  rule_type: string;
  name: string;
  prompt_directive?: string | null;
  target_pattern?: string | null;
  constraints_json?: string | null;
  is_active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface WorkspaceRulesSummaryResponse {
  total_rules: number;
  active_rules: number;
  allowed_commands: string[];
  prompt_directives: string[];
  rules: AgentRuleItem[];
}

export interface SqlExecuteResponse {
  success: boolean;
  columns: string[];
  rows: any[][];
  row_count: number;
  execution_time_ms: number;
  error?: string | null;
}

export interface BookProfile {
  id: string;
  name: string;
  description: string;
  document_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface BookProfileCreate {
  name: string;
  description?: string;
  document_ids?: string[];
}

export interface BookProfileUpdate {
  name?: string;
  description?: string;
  document_ids?: string[];
}

export type QueueItemStatus = "pending" | "uploading" | "indexing" | "completed" | "error";

export interface BatchQueueItem {
  id: string;
  file: File;
  title: string;
  status: QueueItemStatus;
  progressPercent: number;
  error?: string;
  warnings?: string[];
  hasWarnings?: boolean;
  result?: DocumentSummary;
}

export interface VolumeInfo {
  name: string;
  path: string;
  exists: boolean;
  file_count: number;
  total_size_bytes: number;
  subdirectories: string[];
}

export interface VolumeCreate {
  name: string;
}

export type GitProviderType = "github" | "custom";
export type GitAuthType = "none" | "token" | "ssh_key" | "basic";

export interface ProjectGitConfig {
  provider: GitProviderType;
  repository_url: string;
  branch: string;
  origin_name: string;
  auth_type: GitAuthType;
  auth_token_or_secret?: string | null;
  custom_origin_params?: Record<string, any>;
}

export interface SessionTurnMessage {
  turn_id: string;
  timestamp: string;
  user_task: string;
  assistant_answer: string;
  generated_files: string[];
  total_tokens: number;
}

export interface ProjectThreadsResponse {
  threads: string[];
  active_session_id: string | null;
}

export interface ProjectThreadResponse {
  session_id: string;
  turns: SessionTurnMessage[];
}

export interface ProjectThreadSelection {
  session_id: string;
}

export interface ProjectFileInfo {
  path: string;
  size_bytes: number;
  modified_at: string;
  is_directory: boolean;
  extension: string;
}

export interface ProjectFileContent {
  path: string;
  content: string;
  size_bytes: number;
  language: string;
}

export interface ProjectFileWriteRequest {
  path: string;
  content: string;
}

export interface ProjectContext {
  id: string;
  name: string;
  volume_name: string;
  description: string;
  profile_id?: string | null;
  profile_name?: string | null;
  active_session_id?: string | null;
  summary: string;
  context_document?: string;
  git_config: ProjectGitConfig;
  session_history?: SessionTurnMessage[];
  state_metadata: Record<string, any>;
  recent_files: string[];
  root_path?: string | null;
  is_local_dir?: boolean;
  chappie_context?: string;
  has_explanation?: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProjectContextCreate {
  name: string;
  volume_name?: string;
  description?: string;
  profile_id?: string | null;
  summary?: string;
  context_document?: string;
  git_config?: ProjectGitConfig;
  state_metadata?: Record<string, any>;
  root_path?: string | null;
  is_local_dir?: boolean;
  chappie_context?: string;
  has_explanation?: boolean;
}

export interface ProjectContextUpdate {
  name?: string;
  volume_name?: string;
  description?: string;
  profile_id?: string | null;
  active_session_id?: string | null;
  summary?: string;
  context_document?: string;
  git_config?: ProjectGitConfig;
  state_metadata?: Record<string, any>;
  recent_files?: string[];
  root_path?: string | null;
  is_local_dir?: boolean;
  chappie_context?: string;
  has_explanation?: boolean;
}

export interface OpenDirectoryProjectRequest {
  path: string;
  name?: string;
  profile_id?: string | null;
}

export interface UploadDirectoryProjectRequest {
  name: string;
  files: FileWriteItem[];
  profile_id?: string | null;
}

export interface SwitchProjectContextRequest {
  target_project_id: string;
  current_project_id?: string | null;
  save_current?: boolean;
}

export interface ServiceLogEntry {
  timestamp?: string | null;
  level: string;
  service: string;
  message: string;
  raw: string;
}

export interface ServiceLogsResponse {
  service: string;
  available_services: string[];
  total_lines: number;
  lines_returned: number;
  logs: string[];
  entries: ServiceLogEntry[];
  source: string;
  timestamp: string;
}

export interface FileWriteItem {
  path: string;
  content: string;
  overwrite?: boolean;
}

export interface BatchWriteFilesRequest {
  base_path?: string;
  files: FileWriteItem[];
}

export interface BatchWriteFilesResponse {
  success: boolean;
  volume_name: string;
  files_written: ProjectFileInfo[];
  total_files: number;
  total_bytes: number;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  success: boolean;
  token: string;
  username: string;
  is_admin: boolean;
  expires_in_seconds: number;
}

export interface SessionInfoResponse {
  authenticated: boolean;
  username?: string | null;
  is_admin: boolean;
  expires_at?: string | null;
}

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserCreate {
  username: string;
  password: string;
  is_admin?: boolean;
}

export interface UserUpdate {
  username?: string;
  password?: string;
  is_admin?: boolean;
}

export interface MessageResponse {
  message: string;
  success: boolean;
}
