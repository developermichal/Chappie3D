# Backend Gateway & Authentication Microservice (`services.backend`)

### Overview
The `backend` microservice acts as the secure entry gateway and identity provider for the Chappie platform. It provides session-based authentication, user account management (CRUD), and an asynchronous reverse gateway proxy that forwards all non-auth API calls to the host-run `agent` service.

- **Package Path:** `src/services/backend/` (`services.backend`)
- **Default Port:** `8002`
- **Internal Host:** `0.0.0.0`
- **Primary Consumers:** `reverse_proxy` (Nginx), `ui` (Vue 3 Web App), External REST clients
- **Downstream Dependencies:** `db` (PostgreSQL 16), host-run agent (`http://localhost:8003` when the backend runs on the host; Compose overrides this with `http://host.docker.internal:8003`)

---

### Core Architecture & Responsibilities

1. **Relational Authentication & Session Management:**
   - Cryptographically signed tamper-proof session tokens using HMAC-SHA256 (`create_session_token`).
   - Secure HTTP-only session cookie management (`chappie_session`) and `Authorization: Bearer <token>` support.
   - Salted PBKDF2-HMAC-SHA256 password hashing.
   - Session duration enforcement (default 3 hours).

2. **User Administration (RBAC):**
   - Administrator-only user account management (listing, retrieval, creation, updates, and deletion).
   - Automated startup database connection pooling and Alembic migration application.
   - Initial administrative user bootstrap/seeding (`admin` / `admin`).

3. **Asynchronous Reverse Gateway Proxy:**
   - Universal proxy router (`/api/{full_path:path}`) delegating all core assistant and task requests downstream to the host-run `agent` service.
   - Hop-by-hop header stripping to avoid proxy conflicts.

---

### Configuration & Environment Variables

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `POSTGRES_USER` | string | `chappie` | PostgreSQL database username |
| `POSTGRES_PASSWORD` | string | `chappie_secret` | PostgreSQL database password |
| `POSTGRES_DB` | string | `chappie_db` | PostgreSQL database name |
| `POSTGRES_HOST` | string | `db` | PostgreSQL database host |
| `POSTGRES_PORT` | integer | `5432` | PostgreSQL database port |
| `DATABASE_URL` | string | `postgresql+psycopg://chappie:chappie_secret@db:5432/chappie_db` | Full SQLAlchemy DB connection string |
| `SECRET_KEY` | string | `chappie_super_secret_session_key_production_2026` | Secret key used for signing session tokens |
| `SESSION_EXPIRY_HOURS` | integer | `3` | Lifetime of authentication sessions in hours |
| `AGENT_SERVICE_URL` | string | `http://localhost:8003` | URL of the host-run agent; Compose sets `http://host.docker.internal:8003` for container-to-host routing |
| `BACKEND_HOST` | string | `0.0.0.0` | Binding interface for backend server |
| `BACKEND_PORT` | integer | `8002` | Binding port for backend server |

---

### REST API Endpoints

#### 1. Health Check
- **`GET /api/backend/health`**
  - **Response (200 OK):**
    ```json
    {
      "status": "ok",
      "service": "chappie-backend"
    }
    ```

#### 2. Authentication Routes (`/api/auth`)
- **`POST /api/auth/login`**
  - **Request Body:**
    ```json
    {
      "username": "admin",
      "password": "admin"
    }
    ```
  - **Response (200 OK):**
    ```json
    {
      "success": true,
      "token": "<base64_payload>.<signature>",
      "username": "admin",
      "is_admin": true,
      "expires_in_seconds": 10800
    }
    ```
- **`GET /api/auth/session`**
  - **Headers:** `Authorization: Bearer <token>` or `Cookie: chappie_session=<token>`
  - **Response (200 OK):**
    ```json
    {
      "authenticated": true,
      "username": "admin",
      "is_admin": true,
      "expires_at": "2026-11-08T13:00:00Z"
    }
    ```
- **`POST /api/auth/logout`**
  - **Response (200 OK):**
    ```json
    {
      "message": "Successfully logged out.",
      "success": true
    }
    ```

#### 3. User Management Routes (`/api/users` - Admin Required)
- **`GET /api/users`** — List all registered users.
- **`GET /api/users/{user_id}`** — Retrieve specific user details by ID.
- **`POST /api/users`** — Create a new user (`{ "username": "dev1", "password": "password123", "is_admin": false }`).
- **`PUT /api/users/{user_id}`** — Update username, password, or admin role.
- **`DELETE /api/users/{user_id}`** — Delete a user account (cannot delete own active admin account).

#### 4. Gateway Proxy (`/api/*`)
- Any request not matching `/api/auth/*`, `/api/users/*`, or `/api/backend/*` is forwarded to `AGENT_SERVICE_URL`.

---

### Running the Service

```bash
# Using CLI entrypoint
chappie-backend

# Or via Uvicorn directly
uv run uvicorn services.backend.main:app --host 0.0.0.0 --port 8002
```
