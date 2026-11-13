# Chappie Coding Agent Microservice (`services.agent`)

### Overview
The `agent` microservice is the core cognitive, reasoning, RAG, and execution engine of the Chappie platform. It hosts the LangGraph coding agent workflow, sandboxed volume operations, project management, profile configuration, terminal execution processes, in-memory log collection, and WebSocket streaming.

- **Package Path:** `src/services/agent/` (`services.agent`)
- **Host Port:** `8003`
- **Internal Host:** `0.0.0.0`
- **Primary Consumers:** `backend` (via Gateway Proxy), UI WebSocket clients
- **Downstream Dependencies:** host-published model (:8080), embedder (:8090), Chroma (:8000), workspace (:8005), local project storage

---

### Core Architecture & Responsibilities

1. **LangGraph Agent Workflow:**
   - Multi-stage cyclic graph executing Plan, Retrieve, Draft, Validate, and Review stages.
   - Integration with local OpenAI-compatible LLM endpoint (`llama.cpp` CUDA GGUF).

2. **RAG & Knowledge Retrieval:**
   - Vector similarity query against ChromaDB collections (`books_qwen3_06b_v1`).
   - Dense embeddings generation via SentenceTransformers CUDA embedder service.

3. **Sandboxed Filesystem & Volume Management:**
   - Strict path traversal safety (`VolumeManager`, `PathSecurityViolation`).
   - Project directory isolation, batch file write, and cross-project transfer policy checks.

4. **Terminal Process Management:**
   - Asynchronous terminal process execution, command constraints, and timeout protection.

5. **Real-time Logging & WebSocket Streams:**
   - Ring-buffer memory logging collector (`ServiceLogCollector`) and WebSocket `/api/ws/service-logs`.

---

### Configuration & Environment Variables

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `MODEL_BASE_URL` | string | `http://localhost:8080/v1` | URL for host-published LLM chat completion API |
| `MODEL_NAME` | string | `chappie-qwen3-coder` | Name of the LLM model |
| `CHROMA_HOST` | string | `localhost` | Host-published ChromaDB vector storage host |
| `CHROMA_PORT` | string | `8000` | ChromaDB vector storage port |
| `CHROMA_COLLECTION` | string | `books_qwen3_06b_v1` | Default collection for PDF chunks |
| `EMBEDDER_URL` | string | `http://localhost:8090` | Host-published embedder URL |
| `VOLUMES_BASE_PATH` | string | `volumes` | Legacy base directory for sandboxed volumes (project migration follows) |
| `PROFILES_FILE_PATH` | string | `data/profiles.json` | JSON database for book profiles |
| `PROJECTS_FILE_PATH` | string | `data/projects.json` | JSON database for project metadata |

---

### REST & WebSocket API Endpoints

#### 1. Core Agent Task Execution
- **`POST /api/tasks`** — Execute autonomous coding/retrieval task via LangGraph.
  - **Request Schema:** `AgentTaskRequest` (task, document_ids, volume_name, project_id, etc.)
  - **Response Schema:** `AgentTaskResponse` (answer, generated files, execution rationale, stage stats)

#### 2. System Health & Logs
- **`GET /api/health`** — Verify dependencies (model, embedder, chroma, storage).
- **`GET /api/service-logs`** — Retrieve circular log buffer entries.
- **`WS /api/ws/service-logs`** — Real-time WebSocket log streaming.

#### 3. Sandboxed Volumes & File Management
- **`GET /api/volumes`** & **`POST /api/volumes`** — List and create sandboxed volumes.
- **`POST /api/volumes/batch-write-files`** — Atomic batch file writing with path validation.
- **`POST /api/projects/transfers`** — Inter-project file transfers with security verification.

#### 4. Document Indexing & Knowledge
- **`GET /api/documents`** — List indexed PDF books and chunk statistics.
- **`POST /api/documents/index`** — Upload and index PDF documentation into ChromaDB.
- **`DELETE /api/documents/{document_id}`** — Remove document chunks from vector index.

---

### Running the Service

```powershell
# From the repository root on Windows, after starting Docker Compose services:
uv sync
uv run uvicorn services.agent.main:app --host 0.0.0.0 --port 8003
```

Backend and Nginx use `host.docker.internal:8003` to reach this process. Publish model, Chroma and embedder ports to localhost and set their URLs in `.env`; Docker DNS service names cannot be resolved by a Windows process. See the repository README and `docs/host-migration.md` before switching an existing installation.

Standalone terminal clients previously configured for the Compose port `7421` must set `server_port` to `8003` for direct access to `/ws/terminal` on the host agent, or `3001` for the Nginx proxy. Set `server_host` to the Windows host address (or `localhost` for a local client). No separate terminal port is published by Compose anymore.
