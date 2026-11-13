<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "./api";
import type { BookProfile, DocumentSummary, GeneratedFile, ProjectContext, ProjectFileInfo, ServiceLogsResponse, SqlExecuteResponse } from "./types";

type View = "tasks" | "projects" | "cad" | "terminal" | "books" | "database" | "logs";
type Turn = { id: string; task: string; answer: string; files: GeneratedFile[]; filePaths: string[]; durationMs?: number };
type TerminalMessage = { type?: string; status?: string; query_id?: string; answer?: string; token?: string; error_message?: string; files?: GeneratedFile[] };

const navigation: { id: View; label: string; icon: string }[] = [
  { id: "tasks", label: "Zadania i czat", icon: "◈" },
  { id: "projects", label: "Projekty", icon: "▦" },
  { id: "cad", label: "CAD / OpenSCAD", icon: "◇" },
  { id: "terminal", label: "Terminal Web", icon: ">_" },
  { id: "books", label: "Książki / RAG", icon: "▤" },
  { id: "database", label: "Baza SQL", icon: "▥" },
  { id: "logs", label: "Logi", icon: "≡" },
];
const view = ref<View>("tasks");
const loading = ref(false);
const error = ref("");
const notice = ref("");
const authenticated = ref(false);
const checkingAuth = ref(true);
const username = ref("");
const password = ref("");
const user = ref("");
const projects = ref<ProjectContext[]>([]);
const projectId = ref("");
const project = computed(() => projects.value.find(item => item.id === projectId.value));
const projectName = ref("");
const projectPath = ref("");
const projectDescription = ref("");
const editedDescription = ref("");
const directory = ref("");
const files = ref<ProjectFileInfo[]>([]);
const selectedPath = ref("");
const source = ref("");
const savedSource = ref("");
const generated = ref<GeneratedFile[]>([]);
const task = ref("");
const turns = ref<Turn[]>([]);
const sessionId = ref<string | null>(null);
const threads = ref<string[]>([]);
const documents = ref<DocumentSummary[]>([]);
const documentIds = ref<string[]>([]);
const profiles = ref<BookProfile[]>([]);
const profileId = ref("");
const newProfileName = ref("");
const uploading = ref(false);
const sql = ref("SELECT name FROM sqlite_master WHERE type = 'table' LIMIT 20");
const sqlResult = ref<SqlExecuteResponse | null>(null);
const logs = ref<ServiceLogsResponse | null>(null);
const logService = ref("all");
const logQuery = ref("");
const terminalPrompt = ref("");
const terminalOutput = ref<{ kind: "input" | "output" | "error"; text: string }[]>([]);
const terminalStatus = ref("Rozłączono");
const terminalBusy = ref(false);
let socket: WebSocket | null = null;
let socketSession = "";
let pendingQuery = "";

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

async function action(work: () => Promise<void>): Promise<void> {
  error.value = "";
  notice.value = "";
  loading.value = true;
  try { await work(); }
  catch (cause) { error.value = messageOf(cause); }
  finally { loading.value = false; }
}

async function loadProjects(): Promise<void> {
  projects.value = await api.listProjects();
  if (projectId.value && !projects.value.some(item => item.id === projectId.value)) projectId.value = "";
  if (!projectId.value && projects.value.length) projectId.value = projects.value[0]!.id;
}

async function loadKnowledge(): Promise<void> {
  const [bookList, profileList] = await Promise.all([api.documents(), api.listProfiles()]);
  documents.value = bookList;
  profiles.value = profileList;
}

async function loadWorkspace(): Promise<void> {
  await Promise.all([loadProjects(), loadKnowledge()]);
  editedDescription.value = project.value?.description || "";
  if (projectId.value) await Promise.all([refreshFiles(), loadThreads(projectId.value)]);
}

async function loadThread(id: string, threadId: string): Promise<void> {
  const detail = await api.getProjectThread(id, threadId);
  if (projectId.value !== id || sessionId.value !== threadId) return;
  turns.value = detail.turns.map(turn => ({
    id: turn.turn_id,
    task: turn.user_task,
    answer: turn.assistant_answer,
    files: [],
    filePaths: turn.generated_files,
  }));
  generated.value = [];
}

async function loadThreads(id: string): Promise<void> {
  const listing = await api.listProjectThreads(id);
  if (projectId.value !== id) return;
  threads.value = listing.threads;
  sessionId.value = listing.active_session_id;
  turns.value = [];
  generated.value = [];
  if (listing.active_session_id) await loadThread(id, listing.active_session_id);
}

async function initialize(): Promise<void> {
  checkingAuth.value = true;
  try {
    const session = await api.getSession();
    authenticated.value = session.authenticated && session.is_admin;
    user.value = authenticated.value ? session.username || "admin" : "";
    if (authenticated.value) await action(loadWorkspace);
  } catch (cause) { authenticated.value = false; error.value = messageOf(cause); }
  finally { checkingAuth.value = false; }
}

async function login(): Promise<void> {
  await action(async () => {
    const response = await api.login({ username: username.value.trim(), password: password.value });
    if (!response.success || !response.is_admin) throw new Error("Dostęp wymaga konta administratora.");
    authenticated.value = true;
    user.value = response.username;
    password.value = "";
    await loadWorkspace();
  });
}

async function logout(): Promise<void> {
  await action(async () => {
    disconnect();
    await api.logout();
    authenticated.value = false;
    user.value = "";
    turns.value = [];
    sessionId.value = null;
    threads.value = [];
  });
}

async function selectProject(id: string): Promise<void> {
  if (id === projectId.value) return;
  disconnect();
  projectId.value = id;
  editedDescription.value = project.value?.description || "";
  sessionId.value = null;
  threads.value = [];
  turns.value = [];
  generated.value = [];
  selectedPath.value = "";
  source.value = "";
  directory.value = "";
  files.value = [];
  if (id) await action(async () => { await Promise.all([refreshFiles(), loadThreads(id)]); });
}

async function createProject(): Promise<void> {
  if (!projectName.value.trim()) return;
  await action(async () => {
    const openingDirectory = Boolean(projectPath.value.trim());
    const created = openingDirectory
      ? await api.openDirectoryProject({ path: projectPath.value.trim(), name: projectName.value.trim() })
      : await api.createProject({ name: projectName.value.trim(), description: projectDescription.value.trim() });
    await loadProjects();
    if (projectId.value === created.id) {
      projectId.value = "";
    }
    await selectProject(created.id);
    projectName.value = "";
    projectPath.value = "";
    projectDescription.value = "";
    notice.value = openingDirectory ? `Otworzono katalog projektu ${created.name}.` : `Utworzono projekt ${created.name}.`;
  });
}

async function saveProject(): Promise<void> {
  if (!project.value) return;
  await action(async () => {
    await api.updateProject(project.value!.id, { description: editedDescription.value });
    await loadProjects();
    notice.value = "Opis projektu zapisany.";
  });
}

async function refreshFiles(): Promise<void> {
  files.value = projectId.value ? await api.listProjectFiles(projectId.value, directory.value) : [];
}

async function enterDirectory(path: string): Promise<void> {
  directory.value = path;
  await action(refreshFiles);
}

async function openFile(path: string): Promise<void> {
  if (!projectId.value) return;
  await action(async () => {
    const file = await api.readProjectFile(projectId.value, path);
    selectedPath.value = file.path;
    source.value = file.content;
    savedSource.value = file.content;
    view.value = "cad";
  });
}

function selectGenerated(file: GeneratedFile): void {
  selectedPath.value = file.path;
  source.value = file.content;
  savedSource.value = file.content;
  view.value = "cad";
}

async function saveFile(): Promise<void> {
  if (!projectId.value || !selectedPath.value.trim()) return;
  await action(async () => {
    await api.writeProjectFile(projectId.value, { path: selectedPath.value.trim(), content: source.value });
    savedSource.value = source.value;
    await refreshFiles();
    notice.value = `Zapisano ${selectedPath.value}.`;
  });
}

function downloadFile(): void {
  if (!selectedPath.value) return;
  const url = URL.createObjectURL(new Blob([source.value], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = selectedPath.value.split(/[\\/]/).pop() || "model.scad";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function sendTask(): Promise<void> {
  const prompt = task.value.trim();
  if (!prompt) return;
  await action(async () => {
    const id = projectId.value;
    const response = await api.runTask(prompt, documentIds.value, sessionId.value, id || null, project.value?.volume_name || null, profileId.value || null);
    if (id !== projectId.value) return;
    sessionId.value = response.session_id;
    turns.value.push({ id: response.request_id, task: prompt, answer: response.answer, files: response.files, filePaths: response.files.map(file => file.path), durationMs: response.stats.total_ms });
    generated.value = response.files;
    task.value = "";
    if (response.project_context) await loadProjects();
    if (id && projectId.value === id) threads.value = (await api.listProjectThreads(id)).threads;
  });
}

async function newSession(): Promise<void> {
  await action(async () => {
    if (projectId.value) {
      const id = projectId.value;
      const created = await api.createProjectThread(id);
      await api.selectProjectThread(id, created.session_id);
      if (projectId.value !== id) return;
      sessionId.value = created.session_id;
      threads.value = (await api.listProjectThreads(id)).threads;
    } else sessionId.value = null;
    turns.value = [];
    generated.value = [];
  });
}

async function selectThread(threadId: string): Promise<void> {
  if (!projectId.value || threadId === sessionId.value) return;
  const id = projectId.value;
  await action(async () => {
    await api.selectProjectThread(id, threadId);
    if (projectId.value !== id) return;
    sessionId.value = threadId;
    turns.value = [];
    generated.value = [];
    await loadThread(id, threadId);
  });
}

async function deleteThread(threadId: string): Promise<void> {
  if (!projectId.value || !window.confirm(`Usunąć wątek ${threadId}?`)) return;
  const id = projectId.value;
  await action(async () => {
    await api.deleteProjectThread(id, threadId);
    if (projectId.value !== id) return;
    await loadThreads(id);
    notice.value = "Usunięto wątek projektu.";
  });
}

async function uploadPdf(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const selected = input.files?.[0];
  if (!selected) return;
  uploading.value = true;
  await action(async () => {
    const result = await api.uploadPdf(selected, selected.name.replace(/\.pdf$/i, ""));
    await loadKnowledge();
    documentIds.value.push(result.document_id);
    notice.value = `Zaindeksowano ${result.title}.`;
  });
  uploading.value = false;
  input.value = "";
}

async function createProfile(): Promise<void> {
  if (!newProfileName.value.trim()) return;
  await action(async () => {
    const profile = await api.createProfile({ name: newProfileName.value.trim(), document_ids: documentIds.value });
    await loadKnowledge();
    profileId.value = profile.id;
    newProfileName.value = "";
    notice.value = `Zapisano profil ${profile.name}.`;
  });
}

function chooseProfile(id: string): void {
  profileId.value = id;
  if (id) documentIds.value = [...(profiles.value.find(item => item.id === id)?.document_ids || [])];
}

async function executeSql(): Promise<void> {
  if (!sql.value.trim()) return;
  await action(async () => { sqlResult.value = await api.executeSql(sql.value); });
}

async function refreshLogs(): Promise<void> {
  await action(async () => { logs.value = await api.fetchLogs(logService.value, 200, undefined, logQuery.value); });
}

function disconnect(): void {
  const old = socket;
  socket = null;
  if (old) old.close();
  socketSession = "";
  pendingQuery = "";
  terminalBusy.value = false;
  terminalStatus.value = "Rozłączono";
}

function connect(): void {
  if (!project.value?.root_path) { error.value = "Terminal wymaga projektu z katalogiem root_path."; return; }
  disconnect();
  error.value = "";
  const currentProject = project.value;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const next = new WebSocket(`${protocol}//${location.host}/ws/terminal`);
  socket = next;
  socketSession = crypto.randomUUID();
  terminalStatus.value = "Łączenie…";
  next.onopen = () => next.send(JSON.stringify({ session_id: socketSession, project_name: currentProject.name, project_root: currentProject.root_path, chappie_context: currentProject.chappie_context || "", response_language: "pl" }));
  next.onmessage = event => {
    if (socket !== next) return;
    try {
      const data = JSON.parse(event.data as string) as TerminalMessage;
      if (data.status === "ready") { terminalStatus.value = "Gotowy"; return; }
      if (data.type === "token") return;
      if (data.type === "response") {
        if (data.query_id !== pendingQuery) return;
        pendingQuery = "";
        terminalBusy.value = false;
        terminalOutput.value.push({ kind: data.status === "error" ? "error" : "output", text: data.error_message || data.answer || "(brak odpowiedzi)" });
        if (data.files?.length) generated.value = data.files;
      } else if (data.status === "error") {
        terminalBusy.value = false;
        terminalOutput.value.push({ kind: "error", text: data.error_message || "Błąd połączenia" });
      }
    } catch { terminalOutput.value.push({ kind: "error", text: "Niepoprawna odpowiedź serwera." }); }
  };
  next.onerror = () => { if (socket === next) error.value = "Nie udało się połączyć z terminalem."; };
  next.onclose = () => {
    if (socket !== next) return;
    socket = null;
    terminalStatus.value = "Rozłączono";
    terminalBusy.value = false;
    pendingQuery = "";
  };
}

function sendTerminal(): void {
  const prompt = terminalPrompt.value.trim();
  if (!prompt || !socket || socket.readyState !== WebSocket.OPEN || terminalStatus.value !== "Gotowy" || terminalBusy.value) return;
  pendingQuery = crypto.randomUUID();
  terminalBusy.value = true;
  terminalOutput.value.push({ kind: "input", text: prompt });
  socket.send(JSON.stringify({ type: "query", query_id: pendingQuery, prompt }));
  terminalPrompt.value = "";
}

function changeView(next: View): void {
  view.value = next;
  if (next === "projects" && projectId.value) void action(refreshFiles);
  if (next === "logs") void refreshLogs();
}

onMounted(() => { void initialize(); });
onUnmounted(disconnect);
</script>

<template>
  <div class="workstation">
    <div v-if="checkingAuth" class="auth-card">Sprawdzanie sesji…</div>
    <form v-else-if="!authenticated" class="auth-card" @submit.prevent="login">
      <span class="eyebrow">CHAPPIE / ENGINEERING</span>
      <h1>Stanowisko inżynierskie</h1>
      <p>Zaloguj się na konto administratora.</p>
      <label>Użytkownik<input v-model="username" required autocomplete="username" /></label>
      <label>Hasło<input v-model="password" required type="password" autocomplete="current-password" /></label>
      <button :disabled="loading" type="submit">Zaloguj</button>
      <p v-if="error" class="alert">{{ error }}</p>
    </form>
    <template v-else>
      <aside class="sidebar">
        <div class="brand"><span class="brand-mark">C<span>3</span></span><div><strong>CHAPPIE 3D</strong><small>ENGINEERING WORKSTATION</small></div></div>
        <span class="eyebrow nav-heading">WORKSPACE</span>
        <nav aria-label="Nawigacja">
          <button v-for="item in navigation" :key="item.id" :class="{ active: view === item.id }" @click="changeView(item.id)"><span class="nav-icon">{{ item.icon }}</span>{{ item.label }}</button>
        </nav>
        <div class="sidebar-bottom"><small>AKTYWNY PROJEKT</small><select :value="projectId" @change="selectProject(($event.target as HTMLSelectElement).value)"><option value="">Bez projektu</option><option v-for="item in projects" :key="item.id" :value="item.id">{{ item.name }}</option></select><button class="quiet" @click="logout">Wyloguj · {{ user }}</button></div>
      </aside>
      <main class="main">
        <header class="topbar"><div><span class="eyebrow">CHAPPIE / {{ navigation.find(item => item.id === view)?.label.toUpperCase() }}</span><h1>{{ navigation.find(item => item.id === view)?.label }}</h1></div><div class="top-status"><span class="status-dot"></span>{{ project?.name || "Bez projektu" }}</div></header>
        <div v-if="error" class="alert" role="alert">{{ error }} <button class="quiet" @click="error = ''">×</button></div>
        <div v-if="notice" class="notice" role="status">{{ notice }} <button class="quiet" @click="notice = ''">×</button></div>

        <section v-if="view === 'tasks'" class="content-grid">
          <div class="panel main-panel"><div class="panel-heading"><div><span class="eyebrow">AGENT / TASK</span><h2>Sesja robocza</h2></div><button class="secondary" :disabled="loading" @click="newSession">Nowy wątek</button></div>
            <div v-if="projectId" class="stack"><span class="eyebrow">WĄTKI PROJEKTU</span><div v-if="!threads.length" class="muted">Brak wątków w projekcie.</div><div v-for="threadId in threads" :key="threadId" class="row"><button class="secondary" :class="{ active: sessionId === threadId }" :disabled="loading" :aria-current="sessionId === threadId ? 'true' : undefined" @click="selectThread(threadId)">{{ threadId === sessionId ? '● ' : '' }}{{ threadId }}</button><button class="quiet" :disabled="loading" :aria-label="`Usuń wątek ${threadId}`" @click="deleteThread(threadId)">Usuń</button></div></div>
            <div class="conversation"><div v-if="!turns.length" class="empty">Opisz zadanie techniczne, aby rozpocząć pracę z agentem.</div><article v-for="turn in turns" :key="turn.id" class="turn"><div class="prompt"><span class="eyebrow">TY</span><p>{{ turn.task }}</p></div><div class="reply"><span class="eyebrow">CHAPPIE{{ turn.durationMs === undefined ? '' : ` · ${turn.durationMs} MS` }}</span><p class="prewrap">{{ turn.answer }}</p><div v-if="turn.filePaths.length" class="chips"><template v-if="turn.files.length"><button v-for="file in turn.files" :key="file.path" class="chip" @click="selectGenerated(file)">{{ file.path }} ↗</button></template><span v-for="path in turn.files.length ? [] : turn.filePaths" :key="path" class="chip">{{ path }}</span></div></div></article></div>
            <form class="composer" @submit.prevent="sendTask"><textarea v-model="task" rows="4" placeholder="Np. zaprojektuj parametryczny uchwyt w OpenSCAD…" /><div class="row"><span class="muted">{{ project?.name || 'Bez projektu' }} · {{ documentIds.length }} dokumentów RAG</span><button :disabled="loading || !task.trim()" type="submit">{{ loading ? 'Pracuję…' : 'Wyślij zadanie →' }}</button></div></form>
          </div>
          <div class="panel side-panel"><span class="eyebrow">WYNIKI</span><h2>Pliki wyjściowe</h2><p class="muted">Wybierz wygenerowany plik, aby obejrzeć kod i zapisać go w projekcie.</p><div v-if="!generated.length" class="empty">Brak plików w bieżącym zadaniu.</div><button v-for="file in generated" :key="file.path" class="file-row" @click="selectGenerated(file)"><span>◇</span><span>{{ file.path }}<small>{{ file.language }} · {{ file.purpose }}</small></span><span>↗</span></button></div>
        </section>

        <section v-else-if="view === 'projects'" class="content-grid"><div class="panel main-panel"><div class="panel-heading"><div><span class="eyebrow">WORKSPACE / PROJECTS</span><h2>Projekty</h2></div><button class="secondary" :disabled="loading" @click="action(loadProjects)">Odśwież</button></div>
          <div class="project-list"><button v-for="item in projects" :key="item.id" :class="['project-item', { selected: projectId === item.id }]" @click="selectProject(item.id)"><strong>{{ item.name }}</strong><small>{{ item.root_path || item.volume_name || 'Projekt bez katalogu' }}</small><span>{{ item.description || 'Brak opisu' }}</span></button><div v-if="!projects.length" class="empty">Brak projektów. Utwórz pierwszy projekt.</div></div>
          <h3>Nowy projekt / otwórz katalog</h3><form class="stack" @submit.prevent="createProject"><label>Nazwa<input v-model="projectName" required placeholder="Projekt CAD" /></label><label>Ścieżka katalogu na serwerze (opcjonalnie)<input v-model="projectPath" placeholder="C:\projekty\model" /></label><label>Opis (przy tworzeniu projektu bez katalogu)<input v-model="projectDescription" placeholder="Cel i założenia projektu" /></label><button :disabled="loading || !projectName.trim()" type="submit">{{ projectPath.trim() ? 'Otwórz katalog projektu' : 'Utwórz projekt' }}</button></form>
          <div v-if="project" class="stack project-details"><h3>{{ project.name }}</h3><label>Opis projektu<input v-model="editedDescription" placeholder="Opis" /></label><button class="secondary" :disabled="loading" @click="saveProject">Zapisz opis</button></div>
        </div><div class="panel side-panel"><span class="eyebrow">EXPLORER</span><h2>Pliki projektu</h2><div class="row"><span class="muted">{{ directory || '/' }}</span><button class="quiet" :disabled="!projectId" @click="action(refreshFiles)">↻</button></div><button v-if="directory" class="file-row" @click="enterDirectory(directory.split('/').slice(0, -1).join('/'))">↰ W górę</button><button v-for="file in files" :key="file.path" class="file-row" @click="file.is_directory ? enterDirectory(file.path) : openFile(file.path)"><span>{{ file.is_directory ? '▣' : '◇' }}</span><span>{{ file.path.split('/').pop() }}<small>{{ file.is_directory ? 'Katalog' : file.extension || 'Plik' }}</small></span><span>›</span></button><div v-if="!files.length" class="empty">{{ projectId ? 'Brak plików w tym katalogu.' : 'Wybierz projekt.' }}</div></div></section>

        <section v-else-if="view === 'cad'" class="content-grid"><div class="panel main-panel"><div class="panel-heading"><div><span class="eyebrow">CAD / OPENSCAD</span><h2>Źródło modelu</h2></div><span class="badge">{{ selectedPath.toLowerCase().endsWith('.scad') ? 'OPENSCAD' : 'SOURCE' }}</span></div><label>Ścieżka względem projektu<input v-model="selectedPath" placeholder="models/part.scad" /></label><textarea v-model="source" class="code-editor" spellcheck="false" placeholder="// Wybierz plik z projektu lub wynik zadania, albo napisz kod OpenSCAD…" /><div class="row"><span class="muted">{{ source === savedSource ? 'Bez zmian' : 'Niezapisane zmiany' }}</span><div class="actions"><button class="secondary" :disabled="!selectedPath" @click="downloadFile">Pobierz .scad</button><button :disabled="loading || !projectId || !selectedPath.trim()" @click="saveFile">Zapisz w projekcie</button></div></div></div><div class="panel side-panel"><span class="eyebrow">MODEL / PREVIEW</span><h2>Podgląd kodu</h2><p class="muted">Podgląd źródła OpenSCAD. Renderowanie geometrii wymaga zewnętrznego silnika OpenSCAD; plik możesz pobrać i otworzyć lokalnie.</p><pre class="preview">{{ source || '// Brak wybranego modelu' }}</pre><h3>Wygenerowane pliki</h3><button v-for="file in generated" :key="file.path" class="file-row" @click="selectGenerated(file)">{{ file.path }}</button></div></section>

        <section v-else-if="view === 'terminal'" class="panel full-panel"><div class="panel-heading"><div><span class="eyebrow">WEBSOCKET / AGENT SESSION</span><h2>Terminal Web</h2></div><div class="actions"><span class="badge">{{ terminalStatus }}</span><button v-if="socket" class="secondary" @click="disconnect">Rozłącz</button><button v-else :disabled="!project?.root_path" @click="connect">Połącz</button></div></div><p class="muted">Terminal agentowy, nie powłoka systemowa. Wymaga projektu otwartego z katalogu na serwerze.</p><div class="terminal-screen"><div v-if="!terminalOutput.length" class="terminal-line">$ Połącz i wpisz polecenie dla agenta.</div><div v-for="(line, index) in terminalOutput" :key="index" :class="['terminal-line', line.kind]"><span>{{ line.kind === 'input' ? '$ ' : line.kind === 'error' ? '! ' : '› ' }}</span>{{ line.text }}</div></div><form class="row terminal-input" @submit.prevent="sendTerminal"><input v-model="terminalPrompt" :disabled="terminalStatus !== 'Gotowy' || terminalBusy" placeholder="Polecenie dla agenta…" /><button :disabled="terminalStatus !== 'Gotowy' || terminalBusy || !terminalPrompt.trim()" type="submit">{{ terminalBusy ? 'Czekam…' : 'Wyślij' }}</button></form></section>

        <section v-else-if="view === 'books'" class="content-grid"><div class="panel main-panel"><div class="panel-heading"><div><span class="eyebrow">KNOWLEDGE / RAG</span><h2>Biblioteka PDF</h2></div><button class="secondary" :disabled="loading" @click="action(loadKnowledge)">Odśwież</button></div><label class="upload">Dodaj dokument PDF<input type="file" accept=".pdf,application/pdf" :disabled="uploading" @change="uploadPdf" /></label><p class="muted">Zaznaczone dokumenty zostaną dołączone do kolejnych zadań.</p><label v-for="doc in documents" :key="doc.document_id" class="document-row"><input v-model="documentIds" type="checkbox" :value="doc.document_id" /><span><strong>{{ doc.title }}</strong><small>{{ doc.filename }} · {{ doc.pages }} stron · {{ doc.chunks }} fragmentów</small></span></label><div v-if="!documents.length" class="empty">Brak zaindeksowanych dokumentów.</div></div><div class="panel side-panel"><span class="eyebrow">RAG / PROFILES</span><h2>Profil wiedzy</h2><label>Aktywny profil<select :value="profileId" @change="chooseProfile(($event.target as HTMLSelectElement).value)"><option value="">Bez profilu</option><option v-for="profile in profiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option></select></label><p class="muted">Wybranie profilu ustawia powiązane dokumenty. Nowy profil zapamięta obecny wybór.</p><form class="stack" @submit.prevent="createProfile"><label>Nazwa nowego profilu<input v-model="newProfileName" placeholder="Materiały techniczne" /></label><button :disabled="loading || !newProfileName.trim()" type="submit">Zapisz profil</button></form></div></section>

        <section v-else-if="view === 'database'" class="panel full-panel"><div class="panel-heading"><div><span class="eyebrow">DATABASE / WORKSPACE</span><h2>Konsola SQL</h2></div><span class="badge">SQL</span></div><form class="stack" @submit.prevent="executeSql"><label>Zapytanie<textarea v-model="sql" rows="6" class="code-editor sql-editor" spellcheck="false" /></label><button :disabled="loading || !sql.trim()" type="submit">Wykonaj zapytanie</button></form><div v-if="sqlResult" class="result-area"><p v-if="sqlResult.error" class="alert">{{ sqlResult.error }}</p><p v-else class="muted">{{ sqlResult.row_count }} wierszy · {{ sqlResult.execution_time_ms }} ms</p><div class="table-scroll"><table v-if="sqlResult.columns.length"><thead><tr><th v-for="(column, i) in sqlResult.columns" :key="i">{{ column }}</th></tr></thead><tbody><tr v-for="(row, i) in sqlResult.rows" :key="i"><td v-for="(cell, j) in row" :key="j">{{ cell === null ? 'NULL' : String(cell) }}</td></tr></tbody></table></div></div></section>

        <section v-else-if="view === 'logs'" class="panel full-panel"><div class="panel-heading"><div><span class="eyebrow">SYSTEM / DIAGNOSTICS</span><h2>Logi usług</h2></div><button :disabled="loading" @click="refreshLogs">Odśwież</button></div><form class="row filters" @submit.prevent="refreshLogs"><label>Usługa<select v-model="logService"><option value="all">Wszystkie</option><option v-for="service in logs?.available_services || []" :key="service" :value="service">{{ service }}</option></select></label><label>Wyszukaj<input v-model="logQuery" placeholder="Filtr tekstowy" /></label><button type="submit" :disabled="loading">Filtruj</button></form><div class="log-list"><div v-for="(entry, index) in logs?.entries || []" :key="index" class="log-entry"><time>{{ entry.timestamp || '—' }}</time><span :class="['log-level', entry.level.toLowerCase()]">{{ entry.level }}</span><strong>{{ entry.service }}</strong><span>{{ entry.message }}</span></div><div v-if="!logs?.entries.length" class="empty">Brak logów dla wybranego filtra.</div></div></section>
      </main>
    </template>
  </div>
</template>

<style scoped>
.workstation{--bg:#0d131b;--panel:#151e29;--line:#2a3848;--text:#e9f1f5;--muted:#91a5b4;--accent:#51d5bf;display:flex;min-height:100vh;background:var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif;font-size:14px}
*{box-sizing:border-box}button,input,textarea,select{font:inherit}button{cursor:pointer;background:var(--accent);border:1px solid var(--accent);color:#08221e;border-radius:7px;padding:10px 15px;font-weight:650}button:disabled{opacity:.5;cursor:not-allowed}button:hover:not(:disabled){filter:brightness(1.12)}input,textarea,select{color:var(--text);background:#101923;border:1px solid var(--line);border-radius:7px;padding:10px;max-width:100%;width:100%}textarea{resize:vertical}label{display:block;color:var(--muted);font-size:12px}label input,label textarea,label select{display:block;margin-top:8px}h1,h2,h3,p{margin-top:0}h1{font-size:28px;letter-spacing:-.03em}h2{font-size:19px;letter-spacing:-.02em}h3{font-size:15px;margin-top:25px}p{line-height:1.55}.eyebrow{font-size:10px;letter-spacing:.18em;font-weight:800;color:var(--accent)}.muted,small{color:var(--muted)}small{font-size:11px}.sidebar{width:242px;flex-shrink:0;background:#111b26;border-right:1px solid var(--line);display:flex;flex-direction:column;padding:27px 14px;min-height:100vh}.brand{display:flex;align-items:center;gap:12px;margin:0 6px 53px}.brand strong,.brand small{display:block}.brand strong{letter-spacing:.12em;font-size:13px}.brand small{font-size:9px;letter-spacing:.12em;margin-top:5px}.brand-mark{background:var(--accent);color:#08221e;border-radius:8px;font-size:20px;font-weight:900;padding:8px}.brand-mark span{font-size:11px}.nav-heading{margin:0 14px 14px}.sidebar nav{display:grid;gap:5px}.sidebar nav button{text-align:left;color:#b5c5d0;background:transparent;border:0;padding:12px;font-weight:500;display:flex;gap:13px;align-items:center}.sidebar nav button.active{background:#1b3440;color:var(--accent)}.nav-icon{font-family:monospace;font-size:18px;width:22px;text-align:center}.sidebar-bottom{margin-top:auto;padding:20px 8px 0;border-top:1px solid var(--line)}.sidebar-bottom small{display:block;margin-bottom:8px;letter-spacing:.12em}.sidebar-bottom select{margin-bottom:12px}.main{width:100%;min-width:0;padding:32px clamp(18px,4vw,60px)}.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;gap:12px}.topbar h1{margin:6px 0 0}.top-status{color:var(--muted);border:1px solid var(--line);padding:9px 14px;border-radius:30px;white-space:nowrap}.status-dot{display:inline-block;background:var(--accent);width:7px;height:7px;border-radius:50%;margin-right:9px}.content-grid{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(270px,1fr);gap:18px;align-items:start}.panel{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:22px;min-width:0}.panel-heading,.row{display:flex;align-items:center;justify-content:space-between;gap:12px}.panel-heading{margin-bottom:20px}.panel-heading h2{margin:6px 0 0}.secondary,.chip{background:transparent;color:var(--accent);border-color:var(--line)}.quiet{background:transparent;color:var(--muted);border:0;padding:5px}.conversation{min-height:270px;max-height:52vh;overflow:auto}.empty{color:var(--muted);padding:30px 12px;text-align:center;border:1px dashed var(--line);border-radius:8px;margin:14px 0}.turn{margin-bottom:20px}.prompt,.reply{padding:16px;border-radius:8px;margin-bottom:8px;background:#1a2934}.reply{background:#111b26}.turn p{margin:8px 0}.prewrap,.terminal-line{white-space:pre-wrap;overflow-wrap:anywhere}.composer{border-top:1px solid var(--line);padding-top:17px}.composer textarea{margin-bottom:12px}.chips,.actions{display:flex;gap:8px;flex-wrap:wrap}.chip{font-size:12px;padding:7px}.file-row{display:flex;align-items:center;gap:12px;width:100%;background:transparent;border:0;border-bottom:1px solid var(--line);border-radius:0;text-align:left;color:var(--text);padding:14px 4px;overflow-wrap:anywhere}.file-row span:nth-child(2){flex:1;min-width:0}.file-row small{display:block;margin-top:3px}.project-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.project-item{text-align:left;background:#101923;color:var(--text);border-color:var(--line)}.project-item.selected{border-color:var(--accent)}.project-item strong,.project-item small,.project-item span{display:block;margin:4px 0;overflow-wrap:anywhere}.stack{display:grid;gap:15px}.stack button{justify-self:start}.project-details{border-top:1px solid var(--line);margin-top:25px}.code-editor{font-family:Consolas,monospace;line-height:1.5;min-height:360px;margin:14px 0}.preview{background:#0b141e;min-height:270px;max-height:55vh;overflow:auto;padding:14px;border-radius:7px;color:#b6e8df;font-size:12px;line-height:1.5}.badge{color:var(--accent);border:1px solid var(--line);padding:8px 10px;border-radius:6px;font-size:11px;letter-spacing:.1em}.terminal-screen{height:55vh;overflow:auto;background:#09121b;border:1px solid var(--line);border-radius:8px;padding:16px;font-family:Consolas,monospace}.terminal-line{padding:7px 0;color:#b6e8df}.terminal-line.input{color:var(--accent)}.terminal-line.error,.alert{color:#ffb6a7}.terminal-input{margin-top:14px}.upload{border:1px dashed var(--accent);padding:18px;border-radius:7px;cursor:pointer;margin-bottom:20px}.upload input{margin-top:10px}.document-row{display:flex;align-items:center;gap:14px;border-bottom:1px solid var(--line);padding:14px 0}.document-row input{width:auto}.document-row strong,.document-row small{display:block}.sql-editor{min-height:140px}.result-area{margin-top:24px}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;text-align:left}th,td{border-bottom:1px solid var(--line);padding:11px;white-space:pre-wrap}th{color:var(--accent)}.filters{justify-content:flex-start;align-items:end;margin-bottom:20px;flex-wrap:wrap}.filters label{min-width:180px}.log-list{max-height:65vh;overflow:auto}.log-entry{display:grid;grid-template-columns:170px 70px 100px minmax(0,1fr);gap:10px;padding:10px;border-bottom:1px solid var(--line);font-family:Consolas,monospace;font-size:12px;overflow-wrap:anywhere}.log-entry time{color:var(--muted)}.log-level.error,.log-level.critical{color:#ffb6a7}.log-level.warning{color:#f7ce87}.alert,.notice{padding:12px 16px;border:1px solid currentColor;border-radius:8px;margin-bottom:16px}.notice{color:var(--accent)}.auth-card{width:min(440px,calc(100% - 32px));margin:auto;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:36px}.auth-card label{margin-bottom:18px}.auth-card p{color:var(--muted)}
@media(max-width:1000px){.content-grid{grid-template-columns:1fr}.sidebar{width:200px}.main{padding:22px}}@media(max-width:680px){.workstation{display:block}.sidebar{width:100%;min-height:0;padding:12px}.brand{margin:0 4px 12px}.sidebar nav{display:flex;overflow:auto}.sidebar nav button{white-space:nowrap}.nav-heading,.sidebar-bottom{display:none}.main{padding:16px}.topbar{align-items:flex-start}.top-status{font-size:11px}.panel{padding:15px}.log-entry{grid-template-columns:1fr 1fr}.row{flex-wrap:wrap}}
</style>