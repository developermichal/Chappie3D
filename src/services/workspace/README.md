# Workspace Integration & Permissions Microservice (`services.workspace`)

### Overview
The `workspace` microservice manages dynamic agent capabilities, system prompt directives, execution constraints, and administrator SQL query execution. It allows live runtime reconfiguration of what tools, commands, and security policies apply to the Chappie AI Agent.

- **Package Path:** `src/services/workspace/` (`services.workspace`)
- **Default Port:** `8001` (or `8000` when running standalone)
- **Internal Host:** `0.0.0.0`
- **Primary Consumers:** `agent` (`WorkspaceClient`), System Administrators
- **Downstream Dependencies:** `db` (PostgreSQL 16) / SQLite

---

### Core Architecture & Responsibilities

1. **Unified Agent Permissions & Directives:**
   - Single-table schema (`agent_permissions`) managing capabilities (`rule_type: 'command'`), runtime system prompt directives (`rule_type: 'prompt_directive'`), and path/command whitelist patterns (`rule_type: 'whitelist_pattern'`).
   - Dynamic activation and deactivation toggle (`/api/workspace/permissions/{id}/toggle`).
   - Enforces policy directives (e.g. `external_download_policy`, `web_search_permission`).

2. **SQL Administration Gateway:**
   - Provides safe SQL execution endpoint (`/api/workspace/sql/execute`) with timing, row serialization, and exception trapping.

3. **Database Migration Pipeline:**
   - Dedicated Alembic configuration and migration versions (`0001_initial` through `0004_add_external_download_policy`).

---

### Configuration & Environment Variables

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `WORKSPACE_DATABASE_URL` | string | `postgresql://chappie:chappie_secret@postgres:5432/chappie_workspace` | Database connection string |
| `WORKSPACE_SERVICE_HOST` | string | `0.0.0.0` | Binding interface for workspace server |
| `WORKSPACE_SERVICE_PORT` | integer | `8001` | Binding port for workspace server |
| `WORKSPACE_ENABLED` | boolean | `true` | Feature flag to enable workspace enforcement |
| `WORKSPACE_APP_VERSION` | string | `1.0.0b2` | Semantic application version |

---

### REST API Endpoints

#### 1. Health Checks
- **`GET /healthz`** & **`GET /api/health`**
  - **Response (200 OK):**
    ```json
    {
      "status": "ok",
      "version": "1.0.0b2",
      "database": "connected"
    }
    ```

#### 2. Rules & Permissions Management (`/api/workspace/permissions`)
- **`GET /api/workspace/rules/summary`** — Retrieve consolidated active rules, prompt directives, and allowed commands.
- **`GET /api/workspace/permissions?rule_type={type}&is_active={bool}`** — Filter and list permission rules.
- **`POST /api/workspace/permissions`** — Create a new rule.
  - **Request Body:**
    ```json
    {
      "rule_type": "command",
      "name": "git_commit",
      "prompt_directive": "Allowed to stage and commit repository changes.",
      "target_pattern": ".*",
      "constraints_json": "{\"allow_force\": false}",
      "is_active": true
    }
    ```
- **`PUT /api/workspace/permissions/{permission_id}`** — Update an existing rule.
- **`DELETE /api/workspace/permissions/{permission_id}`** — Delete a rule (204 No Content).
- **`POST /api/workspace/permissions/{permission_id}/toggle`** — Toggle active state.

#### 3. SQL Execution (`/api/workspace/sql/execute`)
- **`POST /api/workspace/sql/execute`**
  - **Request Body:**
    ```json
    {
      "query": "SELECT id, name, is_active FROM agent_permissions LIMIT 5;"
    }
    ```
  - **Response (200 OK):**
    ```json
    {
      "success": true,
      "columns": ["id", "name", "is_active"],
      "rows": [[1, "system_baseline_instruction", true]],
      "row_count": 1,
      "execution_time_ms": 1.25,
      "error": null
    }
    ```

---

### Running the Service

```bash
# Using CLI entrypoint
workspace-api

# Or via Uvicorn directly
uv run uvicorn services.workspace.main:app --host 0.0.0.0 --port 8001
```
