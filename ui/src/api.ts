import type {
  AgentResponse,
  BatchWriteFilesRequest,
  BatchWriteFilesResponse,
  BookProfile,
  BookProfileCreate,
  BookProfileUpdate,
  DocumentIndexResponse,
  DocumentSummary,
  HealthResponse,
  LoginRequest,
  LoginResponse,
  MessageResponse,
  OpenDirectoryProjectRequest,
  ProjectContext,
  ProjectContextCreate,
  ProjectContextUpdate,
  ProjectFileContent,
  ProjectFileInfo,
  ProjectFileTransferRequest,
  ProjectFileTransferResponse,
  ProjectFileWriteRequest,
  ProjectThreadResponse,
  ProjectThreadSelection,
  ProjectThreadsResponse,
  ServiceLogsResponse,
  SessionInfoResponse,
  SqlExecuteResponse,
  SwitchProjectContextRequest,
  UploadDirectoryProjectRequest,
  User,
  UserCreate,
  UserUpdate,
  VolumeCreate,
  VolumeInfo,
  WorkspaceRulesSummaryResponse,
  WorkspaceStateResponse,
} from "./types";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail
          .map((d: any) => {
            if (typeof d === "string") return d;
            const loc = Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "";
            const msg = d.msg || JSON.stringify(d);
            return loc ? `${loc}: ${msg}` : msg;
          })
          .join("; ");
      } else if (payload.detail && typeof payload.detail === "object") {
        message = payload.detail.msg || payload.detail.message || JSON.stringify(payload.detail);
      } else if (payload.message && typeof payload.message === "string") {
        message = payload.message;
      }
    } catch {
      // Keep the HTTP status text.
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  documents: () => request<DocumentSummary[]>("/api/documents"),
  runTask: (
    task: string,
    documentIds: string[],
    sessionId?: string | null,
    projectId?: string | null,
    volumeName?: string | null,
    profileId?: string | null,
    enableWebSearch?: boolean | null,
    responseLanguage?: "pl" | "en" | "auto",
    saveProjectContext?: boolean,
    allowCrossProjectTransfers?: boolean
  ) =>
    request<AgentResponse>("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task,
        document_ids: documentIds,
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(projectId ? { project_id: projectId } : {}),
        ...(volumeName ? { volume_name: volumeName } : {}),
        ...(profileId ? { profile_id: profileId } : {}),
        ...(enableWebSearch !== undefined ? { enable_web_search: enableWebSearch } : {}),
        ...(responseLanguage ? { response_language: responseLanguage } : {}),
        ...(saveProjectContext !== undefined ? { save_project_context: saveProjectContext } : {}),
        ...(allowCrossProjectTransfers !== undefined
          ? { allow_cross_project_transfers: allowCrossProjectTransfers }
          : {}),
      }),
    }),
  clearSession: (sessionId: string) =>
    request<{ status: string; session_id: string }>(`/api/sessions/${sessionId}`, {
      method: "DELETE",
    }),
  deleteDocument: (documentId: string) =>
    request<{ status: string; document_id: string }>(`/api/documents/${documentId}`, {
      method: "DELETE",
    }),
  listProfiles: () => request<BookProfile[]>("/api/profiles"),
  createProfile: (profile: BookProfileCreate) =>
    request<BookProfile>("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    }),
  updateProfile: (profileId: string, profile: BookProfileUpdate) =>
    request<BookProfile>(`/api/profiles/${profileId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    }),
  deleteProfile: (profileId: string) =>
    request<{ status: string; profile_id: string }>(`/api/profiles/${profileId}`, {
      method: "DELETE",
    }),
  uploadPdf: (file: File, title: string) => {
    const body = new FormData();
    body.append("file", file);
    if (title.trim()) body.append("title", title.trim());
    return request<DocumentIndexResponse>("/api/documents", { method: "POST", body });
  },
  workspaceState: () => request<WorkspaceStateResponse>("/api/workspace/state"),
  toggleWorkspace: (enabled: boolean) =>
    request<WorkspaceStateResponse>("/api/workspace/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }),
  fetchAgentRules: () => request<WorkspaceRulesSummaryResponse>("/api/workspace/rules"),
  executeSql: (query: string) =>
    request<SqlExecuteResponse>("/api/workspace/sql/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    }),
  listVolumes: () => request<VolumeInfo[]>("/api/volumes"),
  createVolume: (volume: VolumeCreate) =>
    request<VolumeInfo>("/api/volumes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(volume),
    }),
  listVolumeFiles: (volumeName: string, subpath = "") =>
    request<ProjectFileInfo[]>(`/api/volumes/${encodeURIComponent(volumeName)}/files?subpath=${encodeURIComponent(subpath)}`),
  readVolumeFile: (volumeName: string, path: string) =>
    request<ProjectFileContent>(`/api/volumes/${encodeURIComponent(volumeName)}/files/read?path=${encodeURIComponent(path)}`),
  writeVolumeFile: (volumeName: string, payload: ProjectFileWriteRequest) =>
    request<ProjectFileInfo>(`/api/volumes/${encodeURIComponent(volumeName)}/files/write`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  batchWriteVolumeFiles: (volumeName: string, payload: BatchWriteFilesRequest) =>
    request<BatchWriteFilesResponse>(`/api/volumes/${encodeURIComponent(volumeName)}/files/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteVolumeFile: (volumeName: string, path: string) =>
    request<{ status: string; volume_name: string; path: string }>(`/api/volumes/${encodeURIComponent(volumeName)}/files?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    }),
  listProjects: () => request<ProjectContext[]>("/api/projects"),
  createProject: (project: ProjectContextCreate) =>
    request<ProjectContext>("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(project),
    }),
  getProject: (projectId: string) => request<ProjectContext>(`/api/projects/${encodeURIComponent(projectId)}`),
  updateProject: (projectId: string, project: ProjectContextUpdate) =>
    request<ProjectContext>(`/api/projects/${encodeURIComponent(projectId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(project),
    }),
  deleteProject: (projectId: string) =>
    request<{ status: string; project_id: string }>(`/api/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    }),
  listProjectThreads: (projectId: string) =>
    request<ProjectThreadsResponse>(`/api/projects/${encodeURIComponent(projectId)}/threads`),
  createProjectThread: (projectId: string) =>
    request<ProjectThreadSelection>(`/api/projects/${encodeURIComponent(projectId)}/threads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }),
  getProjectThread: (projectId: string, sessionId: string) =>
    request<ProjectThreadResponse>(`/api/projects/${encodeURIComponent(projectId)}/threads/${encodeURIComponent(sessionId)}`),
  selectProjectThread: (projectId: string, sessionId: string) =>
    request<ProjectThreadSelection>(`/api/projects/${encodeURIComponent(projectId)}/threads/${encodeURIComponent(sessionId)}/active`, {
      method: "PATCH",
    }),
  deleteProjectThread: (projectId: string, sessionId: string) =>
    request<void>(`/api/projects/${encodeURIComponent(projectId)}/threads/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }),
  scanProject: (projectId: string) =>
    request<ProjectContext>(`/api/projects/${encodeURIComponent(projectId)}/scan`, {
      method: "POST",
    }),
  updateProjectContext: (projectId: string) =>
    request<ProjectContext>(`/api/projects/${encodeURIComponent(projectId)}/context/update`, {
      method: "POST",
    }),
  openDirectoryProject: (payload: OpenDirectoryProjectRequest) =>
    request<ProjectContext>("/api/projects/open-directory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  uploadDirectoryProject: (payload: UploadDirectoryProjectRequest) =>
    request<ProjectContext>("/api/projects/upload-directory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  switchProjectContext: (payload: SwitchProjectContextRequest) =>
    request<ProjectContext>("/api/projects/switch-context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  syncChappieContext: (projectId: string) =>
    request<ProjectContext>(`/api/projects/${encodeURIComponent(projectId)}/chappie/sync`, {
      method: "POST",
    }),
  explainProject: (projectId: string, explanation: string) =>
    request<ProjectContext>(`/api/projects/${encodeURIComponent(projectId)}/explain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ explanation }),
    }),
  listProjectFiles: (projectId: string, subpath = "") =>
    request<ProjectFileInfo[]>(`/api/projects/${encodeURIComponent(projectId)}/files?subpath=${encodeURIComponent(subpath)}`),
  readProjectFile: (projectId: string, path: string) =>
    request<ProjectFileContent>(`/api/projects/${encodeURIComponent(projectId)}/files/read?path=${encodeURIComponent(path)}`),
  writeProjectFile: (projectId: string, payload: ProjectFileWriteRequest) =>
    request<ProjectFileInfo>(`/api/projects/${encodeURIComponent(projectId)}/files/write`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteProjectFile: (projectId: string, path: string) =>
    request<{ status: string; project_id: string; path: string }>(`/api/projects/${encodeURIComponent(projectId)}/files?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    }),
  transferProjectFile: (projectId: string, payload: ProjectFileTransferRequest) =>
    request<ProjectFileTransferResponse>(`/api/projects/${encodeURIComponent(projectId)}/files/transfer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  fetchLogs: (service = "all", lines = 200, level?: string, q?: string) => {
    const params = new URLSearchParams();
    if (service) params.set("service", service);
    if (lines) params.set("lines", String(lines));
    if (level && level !== "ALL") params.set("level", level);
    if (q && q.trim()) params.set("q", q.trim());
    return request<ServiceLogsResponse>(`/api/logs?${params.toString()}`);
  },
  fetchLogServices: () => request<string[]>("/api/logs/services"),
  clearLogs: () =>
    request<{ status: string; message: string }>("/api/logs", {
      method: "DELETE",
    }),
  login: (payload: LoginRequest) =>
    request<LoginResponse>("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getSession: () => request<SessionInfoResponse>("/api/auth/session"),
  logout: () =>
    request<MessageResponse>("/api/auth/logout", {
      method: "POST",
    }),
  listUsers: () => request<User[]>("/api/users"),
  createUser: (payload: UserCreate) =>
    request<User>("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  updateUser: (userId: number, payload: UserUpdate) =>
    request<User>(`/api/users/${userId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteUser: (userId: number) =>
    request<MessageResponse>(`/api/users/${userId}`, {
      method: "DELETE",
    }),
};
