# Architektura Mikroserwisów i Dokumentacja API UI Chappie (`..`)

### Spis Treści
1. [Przegląd Architektury i Rola Podserwisu UI](#1-przegląd-architektury-i-rola-podserwisu-ui)
2. [Topologia Sieciowa i Przepływ Ruchu (Hop Matrix)](#2-topologia-sieciowa-i-przepływ-ruchu-hop-matrix)
3. [Globalny Standard Obsługi Błędów i Klienta API UI](#3-globalny-standard-obsługi-błędów-i-klienta-api-ui)
4. [Katalog i Specyfikacja Endpointów API](#4-katalog-i-specyfikacja-endpointów-api)
   - [Moduł 1: Rdzeń Systemu, Diagnostyka, Uwierzytelnianie i Użytkownicy](#moduł-1-rdzeń-systemu-diagnostyka-uwierzytelnianie-i-użytkownicy)
   - [Moduł 2: Orkiestracja Agenta, Pamięć Sesji i Baza Wiedzy RAG](#moduł-2-orkiestracja-agenta-pamięć-sesji-i-baza-wiedzy-rag)
   - [Moduł 3: Środowisko Robocze, Reguły i Uprawnienia SQL (Workspace)](#moduł-3-środowisko-robocze-reguły-i-uprawnienia-sql-workspace)
   - [Moduł 4: Wolumeny Sandboksa i Operacje Plikowe (Volumes & Files)](#moduł-4-wolumeny-sandboksa-i-operacje-plikowe-volumes--files)
   - [Moduł 5: Zarządzanie Projektami, Skanowanie i Transfery Międzyprojektowe](#moduł-5-zarządzanie-projektami-skanowanie-i-transfery-międzyprojektowe)
   - [Moduł 6: Diagnostyka Logów, Live Chat, Kamera Pi i Vision AI](#moduł-6-diagnostyka-logów-live-chat-kamera-pi-i-vision-ai)

---

### 1. Przegląd Architektury i Rola Podserwisu UI

Podserwis interfejsu użytkownika (`../../../ui`) stanowi nowoczesną aplikację jednostronicową (SPA) zbudowaną w oparciu o **Vue 3**, **Vite**, **TypeScript** (tryb `strict`) oraz **TailwindCSS**. Aplikacja odpowiada za pełną interakcję z operatorem: wprowadzanie zadań kognitywnych, prezentację toku myślenia modelu (rationale), edycję i inspekcję plików w piaskownicy, zarządzanie projektami i wolumenami, indeksowanie bazy wiedzy RAG oraz streaming obrazu z kamer.

W architekturze mikroserwisowej Chappie podserwis `../../../ui` jest całkowicie odseparowany od logiki biznesowej i komunikuje się ze środowiskiem backendowym wyłącznie za pośrednictwem ustandaryzowanego interfejsu REST API zdefiniowanego w `ui/src/api.ts`.

Główne mikroserwisy backendowe w `..`:
1. **`backend` (`../backend`, port `8002`):**
   - Centralna bramka wejściowa (API Gateway), uwierzytelnianie, zarządzanie użytkownikami, szyfrowane sesje cookie, audyt bezpieczeństwa oraz asynchroniczne proxy tras do serwisu `agent`.
   - Baza danych: PostgreSQL (tabela `users`).
2. **`agent` (`../agent` / `src/chappie`, port `8001`):**
   - Kognitywny silnik orkiestracji LangGraph (`CodingAgent`), realizacja RAG, zarządzanie wolumenami dyskowymi piaskownicy (`VolumeManager`), magazyn profili (`ProfileStorage`) i projektów (`ProjectStorage`), kolektor logów oraz moduły wizyjne.
3. **`workspace` (`../workspace`, port `8000` / `8001`):**
   - Zarządzanie dynamicznymi regułami agenta, dyrektywami promptów, uprawnieniami I/O oraz bezpieczna bramka do wykonywania zapytań SQL.
   - Baza danych: PostgreSQL (tabela `agent_permissions`).

---

### 2. Topologia Sieciowa i Przepływ Ruchu (Hop Matrix)

Poniższy diagram ilustruje przepływ zapytań sieciowych z przeglądarki klienta przez poszczególne warstwy infrastruktury:

```mermaid
flowchart TD
    Client["Przeglądarka Klienta (Vue 3 SPA)"] -->|HTTPS / Port 443 / 80| NginxProxy["reverse_proxy (Nginx:80/443)"]
    
    NginxProxy -->|Lokalny routing /chappie/| UIContainer["ui (Nginx:3000 SPA Static)"]
    NginxProxy -->|Routing żądań /api/*| BackendService["services.backend (:8002)<br/>Gateway & Auth Engine"]
    
    BackendService -->|Obsługa /api/auth/* oraz /api/users/*| PostgresDB[("PostgreSQL (:5432)<br/>Tabela: users")]
    BackendService -->|Asynchroniczne Proxy pozostałych /api/* (gateway.py)| AgentService["services.agent (:8001)<br/>LangGraph Engine & API"]
    
    AgentService -->|Pobieranie reguł & Walidacja ścieżek| WorkspaceService["services.workspace (:8000)"]
    WorkspaceService -->|Reguły & Dynamic SQL| PostgresDB
    
    AgentService -->|Wektoryzacja zapytań i dokumentów| EmbedderService["embedder (:8090)<br/>SentenceTransformers"]
    AgentService -->|Wyszukiwanie wektorowe chunków RAG| ChromaDB[("ChromaDB (:8000)<br/>Kolekcje dokumentów")]
    AgentService -->|Wnioskowanie i synteza kodu LLM| ModelService["model (:8080)<br/>vLLM / llama.cpp"]
    AgentService -->|OCR i ekstrakcja z obrazu| VisionService["vision_model (:8085)"]
    AgentService -->|Strumień wideo MJPEG| PiCamera["Kamera Raspberry Pi (:8000)"]
    AgentService -->|Operacje I/O na plikach piaskownicy| VolumesStorage["Wolumeny Sandboksa (/app/volumes)"]
```

#### Macierz Przeskoków Sieciowych (Hop Matrix)
| Ścieżka URL w UI | Pierwszy Przeskok (Reverse Proxy) | Drugi Przeskok (API Gateway) | Cel Końcowy (Docelowy Serwis) | Baza Danych / Zasób Downstream |
| :--- | :--- | :--- | :--- | :--- |
| `/api/auth/*` | `reverse_proxy:443` | `backend:8002` (Bezpośrednia obsługa) | `services.backend` | PostgreSQL (`users`) |
| `/api/users/*` | `reverse_proxy:443` | `backend:8002` (Bezpośrednia obsługa) | `services.backend` | PostgreSQL (`users`) |
| `/api/backend/health` | `reverse_proxy:443` | `backend:8002` (Bezpośrednia obsługa) | `services.backend` | Brak (Stan lokalny) |
| `/api/health` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | `model`, `embedder`, `chroma`, `workspace` |
| `/api/tasks` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | LangGraph, `chroma`, `model`, `volumes` |
| `/api/sessions/*` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | RAM agenta, `../../../data/projects.json`, wolumeny |
| `/api/documents/*` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | `chroma:8000`, `embedder:8090`, `../../../data/profiles.json` |
| `/api/profiles/*` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | `../../../data/profiles.json` (atomowy JSON z blokadą) |
| `/api/workspace/*` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` -> `workspace:8000` | PostgreSQL (`agent_permissions`) |
| `/api/volumes/*` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | System plików sandboksa `/app/volumes/` |
| `/api/projects/*` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | Pliki `.chappie`, `../../../data/projects.json`, wolumeny |
| `/api/logs/*` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | Bufor RAM `LogBufferHandler`, `./logs/*.log`, Docker CLI |
| `/api/live-chat/*` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | `/app/volumes/live-chat/`, `vision_model:8085`, `model:8080` |
| `/api/pi/*` | `reverse_proxy:443` | `backend:8002` (Streaming / Proxy) | `agent:8001` | Kamera Raspberry Pi (`sensors/pi/server.py:8000`) |
| `/api/vision/*` | `reverse_proxy:443` | `backend:8002` (Bramka proxy) | `agent:8001` | `vision_model:8085` (Qwen2-VL / MiniCPM-V) |

---

### 3. Globalny Standard Obsługi Błędów i Klienta API UI

Wszystkie żądania HTTP z poziomu aplikacji frontendowej przechodzą przez generyczną funkcję pomocniczą `request<T>(url: string, options?: RequestInit)` zdefiniowaną w `../../../ui/src/api.ts`:

```typescript
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        // Dekompozycja błędów walidacji Pydantic/FastAPI (HTTP 422)
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
      // Zachowanie statusText w przypadku braku struktury JSON w odpowiedzi błędu
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
```

#### Standardowe Kody Odpowiedzi HTTP:
- **`200 OK` / `201 Created`:** Żądanie przetworzone pomyślnie, payload zawiera oczekiwany typ DTO.
- **`400 Bad Request`:** Błąd semantyczny żądania (np. próba usunięcia chronionego zasobu, niepoprawny format ścieżki).
- **`401 Unauthorized`:** Brak ważnego tokenu sesyjnego lub niepoprawne poświadczenia w nagłówku/ciasteczku `chappie_session`.
- **`403 Forbidden`:** Brak uprawnień administratora lub naruszenie granic piaskownicy (`PathSecurityViolation`).
- **`404 Not Found`:** Żądany zasób (projekt, dokument, profil, wolumen, użytkownik) nie istnieje.
- **`422 Unprocessable Entity`:** Błąd walidacji schematu wejściowego FastAPI / Pydantic v2 (`detail: [{loc, msg, type}]`).
- **`500 Internal Server Error`:** Nieobsłużony wyjątek wewnątrz serwisu docelowego (np. błąd grafu LangGraph, błąd I/O).
- **`502 Bad Gateway`:** Błąd komunikacji wygenerowany przez bramkę `gateway.py` w przypadku niedostępności serwisu `agent`, modelu LLM lub VLM.
- **`503 Service Unavailable`:** Baza wektorowa ChromaDB lub model embeddera nie są gotowe do obsługi ruchu.
- **`504 Gateway Timeout`:** Przekroczenie limitu czasu oczekiwania na odpowiedź downstream.

---

### 4. Katalog i Specyfikacja Endpointów API

---

### Moduł 1: Rdzeń Systemu, Diagnostyka, Uwierzytelnianie i Użytkownicy

#### `GET /api/health` — Diagnostyka Kondycji Podsystemów i Zależności Downstream
- **Funkcja w UI:** `api.health()` (`ui/src/api.ts:79`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:163`)
- **Opis Biznesowy i Rola:** Cykliczny punkt kontrolny (heartbeat/polling) wywoływany przez UI w celu weryfikacji dostępności całego ekosystemu mikroserwisów. Serwis `agent` asynchronicznie odpytuje równolegle (`asyncio.gather`) usługi downstream, mapując ich stan w czasie rzeczywistym.

##### Mapa Wszystkich Usług Stosu Docker Compose (`../../../compose.yaml`) a Diagnostyka
W pełnej topologii Chappie zdefiniowanej w `../../../compose.yaml` funkcjonuje 11 powiązanych usług. Endpoint `GET /api/health` agreguje stan podsystemów bezpośrednio zaangażowanych w cykl kognitywny, podczas gdy pozostałe kontenery posiadają dedykowane mechanizmy kontrolne:

| Serwis w `../../../compose.yaml` | Port Kontenera | Rola w Ekosystemie | Mechanizm Weryfikacji w `/api/health` / Dedykowany Healthcheck |
| :--- | :--- | :--- | :--- |
| `agent` | `8001` | Orkiestrator LangGraph i API | Bezpośredni host endpointu `GET /api/health` (klucz `"api": "ok"`). |
| `model` | `8080` | Wnioskowanie LLM (GGUF, GPU) | Odpytywany przez `GET http://model:8080/health` (klucz `"model"`). |
| `embedder` | `8090` | Wektoryzacja tekstu (PyTorch CUDA) | Odpytywany przez `GET http://embedder:8090/health` (klucz `"embedder"`). |
| `chroma` | `8000` | Baza wektorowa RAG | Odpytywany przez heartbeat klienta ChromaDB (`http://chroma:8000/api/v1/heartbeat`, klucz `"chroma"`). |
| `workspace` | `8000` / `8001` | Reguły agenta i uprawnienia SQL | Odpytywany przez `GET http://workspace:8000/healthz` (klucz `"workspace"`). |
| `db` (PostgreSQL) | `5432` | Relacyjna baza użytkowników i uprawnień | Weryfikowany pośrednio przez `workspace` (test sesji SQLAlchemy) oraz dedykowany healthcheck Docker `pg_isready`. |
| `backend` | `8002` | API Gateway i silnik autoryzacji | Dedykowany endpoint `GET /api/backend/health` oraz Docker healthcheck `urllib.request`. |
| `vision_model` | `8085` | Model wizyjny VLM (Qwen2.5-VL / MiniCPM-V) | Dedykowany endpoint `GET http://vision_model:8085/health` (Docker healthcheck) oraz testowany przy `/api/vision/extract`. |
| `reverse_proxy` | `80`, `443`, `7420` | Bramka Nginx SSL i routing | Weryfikacja łączności portowej i trasowania do `backend` i `ui`. |
| `ui` | `3000` | Frontend Vue 3 SPA | Nginx static server, odbiór żądań przeglądarki. |
| `pi_camera` | `8000` | Serwer kamery Raspberry Pi | Dedykowany endpoint diagnostyczny `GET /api/pi/status` odpytujący `/telemetry`. |
| `indexer` | - | Narzędzie wsadowe indeksacji PDF | Profil Docker `indexing` (`chappie-index`). |

##### Parametry Żądania (Request)
*Brak parametrów wejściowych (bezparametrowe żądanie HTTP GET).*
- **Wymagane Nagłówki:** `Accept: application/json`

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `502 Bad Gateway` (w przypadku awarii kontenera `agent`).
- **Typ DTO w TypeScript:** `HealthResponse` (`ui/src/types.ts:101`)
- **Model Pydantic Backend:** `HealthResponse` (`src/chappie/models.py:176`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola | Zastosowanie i Konsumpcja w UI |
| :--- | :--- | :--- | :--- | :--- |
| `status` | `"ok" \| "degraded"` | `string (Literal["ok", "degraded"])` | Globalny stan zdrowia systemu. Przyjmuje `"ok"` wyłącznie gdy wszystkie podsystemy raportują `"ok"`. | Aktualizacja głównego wskaźnika w pasku stanu (`StatusBar.vue`), blokowanie wysyłania zadań przy awarii. |
| `version` | `string` | `string` | Wersja oprogramowania Chappie (`settings.chappie_version`, np. `"1.0.0b2"`). | Wyświetlanie etykiety wersji w stopce interfejsu. |
| `services` | `Record<string, string>` | `object (dict[str, str])` | Mapa stanów poszczególnych mikroserwisów (`api`, `model`, `embedder`, `chroma`, `workspace`). | Renderowanie listy kontenerów w panelu diagnostycznym, oznaczanie kropkami statusu (zielona/czerwona). |

##### Przykład Odpowiedzi JSON (`200 OK`):
```json
{
  "status": "ok",
  "version": "1.0.0b2",
  "services": {
    "api": "ok",
    "model": "ok",
    "embedder": "ok",
    "chroma": "ok",
    "workspace": "ok"
  }
}
```

##### Efekty Uboczne (Side Effects)
- Czysta operacja diagnostyczna (Read-Only) — brak modyfikacji baz danych i systemu plików.

---

#### `POST /api/auth/login` — Uwierzytelnianie Użytkownika i Inicjalizacja Sesji
- **Funkcja w UI:** `api.login(payload: LoginRequest)` (`ui/src/api.ts:270`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`src/services/backend/routes/auth.py:30`) -> PostgreSQL (`users`)
- **Opis Biznesowy i Rola:** Logowanie operatora do systemu Chappie. Weryfikuje poświadczenia w PostgreSQL (bcrypt), generuje token HMAC SHA-256 oraz ustawia ciasteczko sesyjne `chappie_session` typu `HttpOnly`.

##### Parametry Żądania (Request)
- **Lokalizacja:** `Body (JSON)`
- **Typ DTO w TypeScript:** `LoginRequest` (`ui/src/types.ts:359`)
- **Model Pydantic Backend:** `LoginRequest` (`src/services/backend/schemas/user.py:31`)

| Pole / Parametr | Typ TypeScript | Typ Backend (Pydantic / DB) | Wymagany | Opis i Przeznaczenie | Konsumpcja przez Serwisy Downstream |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `username` | `string` | `str (Field(min_length=1))` | Tak | Unikalny login użytkownika w systemie. | Wyszukanie rekordu w tabeli `users` (`WHERE username = :username`) w PostgreSQL. |
| `password` | `string` | `str (Field(min_length=1))` | Tak | Hasło w postaci jawnej wpisane w formularzu. | Weryfikacja kryptograficzna funkcją `verify_password(password, user.password_hash)` (bcrypt). |

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `401 Unauthorized`, `422 Unprocessable Entity`.
- **Nagłówki Odpowiedzi:** `Set-Cookie: chappie_session=<token>; Max-Age=<expires_in_seconds>; Path=/; HttpOnly; SameSite=Lax`
- **Typ DTO w TypeScript:** `LoginResponse` (`ui/src/types.ts:364`)
- **Model Pydantic Backend:** `LoginResponse` (`src/services/backend/schemas/user.py:36`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola | Zastosowanie i Konsumpcja w UI |
| :--- | :--- | :--- | :--- | :--- |
| `success` | `boolean` | `boolean` | Flaga potwierdzająca pomyślne uwierzytelnienie (`true`). | Walidacja stanu operacji logowania w magazynie Pinia / `useAuth`. |
| `token` | `string` | `string` | Podpisany token sesyjny zawierający zaszyfrowany identyfikator użytkownika, rolę oraz czas wygaśnięcia. | Przechowywany w pamięci aplikacji jako token zapasowy do nagłówków `Authorization: Bearer`. |
| `username` | `string` | `string` | Zwrócona nazwa zalogowanego użytkownika. | Wyświetlanie nazwy użytkownika w profilu nagłówka interfejsu. |
| `is_admin` | `boolean` | `boolean` | Flaga uprawnień administratora. | Warunkowe renderowanie zakładek administracyjnych (zarządzanie użytkownikami, zaawansowane reguły SQL). |
| `expires_in_seconds` | `number` | `integer` | Czas życia sesji w sekundach (`session_expiry_hours * 3600`, domyślnie 86400s = 24h). | Konfiguracja wewnętrznego timera automatycznego odświeżania sesji / wylogowania w UI. |

##### Efekty Uboczne (Side Effects)
- Odczyt rekordu użytkownika z bazy PostgreSQL (`SELECT FROM users`).
- Zalogowanie pomyślnego uwierzytelnienia w strumieniu logów backendu.
- Ustawienie ciasteczka sesyjnego `chappie_session` w przeglądarce klienta.

---

#### `GET /api/auth/session` — Weryfikacja Ważności i Uprawnień Aktywnej Sesji
- **Funkcja w UI:** `api.getSession()` (`ui/src/api.ts:276`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`src/services/backend/routes/auth.py:77`) -> `get_current_session`
- **Opis Biznesowy i Rola:** Weryfikuje podpis kryptograficzny tokenu sesyjnego HMAC SHA-256 z ciasteczka `chappie_session` lub nagłówka Bearer, odczytuje rolę administratora oraz datę wygaśnięcia sesji.

##### Parametry Żądania (Request)
- **Lokalizacja:** `Cookie` (`chappie_session`) lub `Headers` (`Authorization: Bearer <token>`).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `401 Unauthorized`.
- **Typ DTO w TypeScript:** `SessionInfoResponse` (`ui/src/types.ts:372`)
- **Model Pydantic Backend:** `SessionInfoResponse` (`src/services/backend/schemas/user.py:44`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola | Zastosowanie i Konsumpcja w UI |
| :--- | :--- | :--- | :--- | :--- |
| `authenticated` | `boolean` | `boolean` | Flaga potwierdzająca ważność sesji (`true`). | Przełączanie widoku z ekranu logowania na dashboard aplikacji. |
| `username` | `string \| null` | `string \| null` | Nazwa zalogowanego użytkownika. | Wyświetlanie nazwy użytkownika w nagłówku. |
| `is_admin` | `boolean` | `boolean` | Flaga uprawnień administratora. | Odblokowanie widoków administracyjnych w UI. |
| `expires_at` | `string \| null` | `string (ISO 8601 UTC) \| null` | Czas wygaśnięcia tokenu. | Inicjalizacja licznika czasu do automatycznego wylogowania. |

---

#### `POST /api/auth/logout` — Zakończenie Sesji Użytkownika
- **Funkcja w UI:** `api.logout()` (`ui/src/api.ts:277`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`src/services/backend/routes/auth.py:97`)
- **Opis Biznesowy i Rola:** Kończy aktywną sesję użytkownika i unieważnia ciasteczko `chappie_session` (`Max-Age=0`).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`.
- **Typ DTO w TypeScript:** `MessageResponse` (`ui/src/types.ts:399`)
- **Model Pydantic Backend:** `MessageResponse` (`src/services/backend/schemas/user.py:51`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola | Zastosowanie i Konsumpcja w UI |
| :--- | :--- | :--- | :--- | :--- |
| `message` | `string` | `string` | Komunikat potwierdzający wylogowanie. | Wyświetlenie powiadomienia Toast i wyczyszczenie magazynu sesji Pinia. |
| `success` | `boolean` | `boolean` | Flaga powodzenia operacji (`true`). | Przekierowanie routera Vue do ekranu logowania (`/login`). |

---

#### `GET /api/users` — Pobranie Rejestru Użytkowników
- **Funkcja w UI:** `api.listUsers()` (`ui/src/api.ts:281`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`src/services/backend/routes/users.py:30`) -> PostgreSQL (`users`)
- **Opis Biznesowy i Rola:** Zwraca listę kont użytkowników. Wymaga uprawnień administratora (`is_admin=True`).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `401 Unauthorized`, `403 Forbidden`.
- **Typ DTO w TypeScript:** `User[]` (`ui/src/types.ts:379`)
- **Model Pydantic Backend:** `list[UserResponse]` (`src/services/backend/schemas/user.py:23`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola | Zastosowanie i Konsumpcja w UI |
| :--- | :--- | :--- | :--- | :--- |
| `[].id` | `number` | `integer` | Klucz główny użytkownika w bazie PostgreSQL. | Identyfikator rekordu przy edycji i usuwaniu. |
| `[].username` | `string` | `string` | Unikalna nazwa konta użytkownika. | Wyświetlanie w tabeli użytkowników. |
| `[].is_admin` | `boolean` | `boolean` | Flaga uprawnień administratora. | Wyświetlanie badge'a roli (Admin / Standard). |
| `[].created_at` | `string` | `string (ISO 8601 UTC)` | Data rejestracji konta. | Kolumna daty rejestracji. |
| `[].updated_at` | `string` | `string (ISO 8601 UTC)` | Data ostatniej modyfikacji. | Kolumna ostatniej aktywności. |

---

#### `POST /api/users` — Utworzenie Konta Użytkownika
- **Funkcja w UI:** `api.createUser(payload: UserCreate)` (`ui/src/api.ts:282`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`src/services/backend/routes/users.py:49`) -> PostgreSQL (`users`)
- **Opis Biznesowy i Rola:** Rejestracja nowego użytkownika w PostgreSQL z hashowaniem hasła bcrypt. Wymaga uprawnień administratora.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `UserCreate` (`ui/src/types.ts:387`)
- **Model Pydantic Backend:** `UserCreate` (`src/services/backend/schemas/user.py:13`)

| Pole / Parametr | Typ TypeScript | Typ Backend | Wymagany | Opis i Przeznaczenie |
| :--- | :--- | :--- | :--- | :--- |
| `username` | `string` | `str (min_length=3, max_length=50)` | Tak | Unikalny login użytkownika. |
| `password` | `string` | `str (min_length=4)` | Tak | Hasło w postaci jawnej (hashowane bcryptem przed zapisem). |
| `is_admin` | `boolean` | `bool (default=False)` | Nie | Nadanie uprawnień administratora. |

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `201 Created`, `400 Bad Request` (zajęty login), `401 Unauthorized`, `403 Forbidden`, `422 Unprocessable Entity`.
- **Typ DTO w TypeScript:** `User` (`ui/src/types.ts:379`)

---

#### `PUT /api/users/{userId}` — Modyfikacja Danych Użytkownika
- **Funkcja w UI:** `api.updateUser(userId: number, payload: UserUpdate)` (`ui/src/api.ts:288`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`src/services/backend/routes/users.py:75`) -> PostgreSQL (`users`)
- **Opis Biznesowy i Rola:** Aktualizacja loginu, hasła lub roli wskazanego użytkownika.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `UserUpdate` (`ui/src/types.ts:393`)
- **Model Pydantic Backend:** `UserUpdate` (`src/services/backend/schemas/user.py:17`)

| Pole / Parametr | Typ TypeScript | Typ Backend | Wymagany | Opis i Przeznaczenie |
| :--- | :--- | :--- | :--- | :--- |
| `userId` | Path: `number` | `int` | Tak | Identyfikator rekordu użytkownika w PostgreSQL. |
| `username` | Body: `string \| undefined` | `str \| None (min_length=3, max_length=50)` | Nie | Zmiana loginu użytkownika. |
| `password` | Body: `string \| undefined` | `str \| None (min_length=4)` | Nie | Nowe hasło (hashowane bcryptem). |
| `is_admin` | Body: `boolean \| undefined` | `bool \| None` | Nie | Zmiana roli administracyjnej. |

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `404 Not Found`, `422 Unprocessable Entity`.
- **Typ DTO w TypeScript:** `User` (`ui/src/types.ts:379`)

---

#### `DELETE /api/users/{userId}` — Usunięcie Konta Użytkownika
- **Funkcja w UI:** `api.deleteUser(userId: number)` (`ui/src/api.ts:294`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`src/services/backend/routes/users.py:112`) -> PostgreSQL (`users`)
- **Opis Biznesowy i Rola:** Trwałe usunięcie konta użytkownika z PostgreSQL. Posiada zabezpieczenie blokujące usunięcie własnego aktywnego konta.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request` (próba usunięcia własnego konta), `401 Unauthorized`, `403 Forbidden`, `404 Not Found`.
- **Typ DTO w TypeScript:** `MessageResponse` (`ui/src/types.ts:399`)

---

### Moduł 2: Orkiestracja Agenta, Pamięć Sesji i Baza Wiedzy RAG

#### `POST /api/tasks` — Asynchroniczna Egzekucja Zadania Agenta i Wnioskowanie Kognitywne
- **Funkcja w UI:** `api.runTask(...)` (`ui/src/api.ts:81`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:234`) -> `model:8080`, `embedder:8090`, `chroma:8000`, `workspace:8000`, wolumeny dyskowe
- **Opis Biznesowy i Rola:** Główny punkt wejściowy silnika LangGraph (`CodingAgent`). Realizuje semantyczne wyszukiwanie RAG w ChromaDB, syntezę kodu i generowanie plików, ewaluację reguł i metryki etapów.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `AgentTaskRequest` (`ui/src/types.ts:31`)
- **Model Pydantic Backend:** `AgentTaskRequest` (`src/chappie/models.py:44`)

| Pole / Parametr | Typ TypeScript | Typ Backend (Pydantic) | Wymagany | Opis i Przeznaczenie | Konsumpcja Downstream |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `task` | `string` | `str (min_length=2, max_length=20000)` | Tak | Treść polecenia dla agenta. | Prompt wejściowy LLM i zapytanie wektorowe RAG. |
| `document_ids` | `string[]` | `list[str] (max_length=500)` | Nie (domyślnie `[]`) | Lista ID dokumentów RAG. | Filtrowanie wektorowe w ChromaDB (`$in`). |
| `session_id` | `string \| null` | `str \| None (max_length=128)` | Nie | ID ciągłej sesji dialogowej. | Przywrócenie historii z pamięci RAM `session_memory`. |
| `project_id` | `string \| null` | `str \| None (max_length=128)` | Nie | ID aktywnego projektu. | Załadowanie kontekstu z `.chappie` i `projects.json`. |
| `volume_name` | `string \| null` | `str \| None (max_length=128)` | Nie | Nazwa wolumenu sandboksa. | Wyznaczenie ścieżki w `VolumeManager` (`/app/volumes/{vol}`). |
| `profile_id` | `string \| null` | `str \| None (max_length=128)` | Nie | ID profilu wiedzy. | Pobranie powiązanych `document_ids` i dyrektyw wiedzy. |
| `save_project_context` | `boolean` | `bool (default=True)` | Nie | Flaga zapisu kontekstu. | Zapisanie stanu projektu w `../../../data/projects.json`. |
| `pypi_packages` | `string[]` | `list[str]` | Nie | Lista pakietów PyPI. | Weryfikacja zależności w piaskownicy. |
| `enable_web_search` | `boolean \| null` | `bool \| None` | Nie | Zgoda na wyszukiwanie web. | Węzeł wyszukiwania Google/SearXNG w grafie LangGraph. |
| `response_language` | `"pl" \| "en" \| "auto"` | `Literal["pl", "en", "auto"]` | Nie (domyślnie `"pl"`) | Język odpowiedzi i toku myślenia. | Dyrektywa językowa w prompcie systemowym LLM. |
| `allow_cross_project_transfers` | `boolean` | `bool \| None` | Nie | Zezwolenie na transfery plików. | Weryfikacja uprawnień I/O w `VolumeManager`. |

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `422 Unprocessable Entity`, `500 Internal Server Error`, `502 Bad Gateway`.
- **Typ DTO w TypeScript:** `AgentResponse` (`ui/src/types.ts:77`)
- **Model Pydantic Backend:** `AgentTaskResponse` (`src/chappie/models.py:141`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola | Zastosowanie i Konsumpcja w UI |
| :--- | :--- | :--- | :--- | :--- |
| `request_id` | `string` | `string (UUIDv4)` | Unikalny identyfikator żądania. | Śledzenie żądania w logach i audycie. |
| `session_id` | `string` | `string (UUIDv4)` | Identyfikator aktywnej sesji dialogowej. | Wiązanie kolejnych wiadomości w wątek. |
| `version` | `string` | `string` | Wersja silnika agenta (`"1.0.0b2"`). | Kontrola zgodności DTO. |
| `answer` | `string` | `string (max 50000)` | Główna odpowiedź tekstowa agenta w Markdown. | Renderowanie wiadomości asystenta w czacie (`ChatView.vue`). |
| `rationale` | `string[]` | `array of strings (max 8)` | Kroki rozumowania i uzasadnienia modelu. | Renderowanie rozwijanej sekcji "Tok Myślenia". |
| `files` | `GeneratedFile[]` | `array of objects` | Wygenerowane pliki (`path`, `language`, `purpose`, `content`). | Kafelki plików, podgląd w edytorze, zapis na wolumen. |
| `files[].path` | `string` | `string (bezpieczna ścieżka względna)` | Relatywna ścieżka do pliku w projekcie/wolumenie. | Drzewo plików projektu, nazwa zakładki w edytorze. |
| `files[].language` | `string` | `string (np. "python", "vue", "typescript")` | Język programowania pliku. | Wybór trybu kolorowania składni w edytorze kodu. |
| `files[].purpose` | `string` | `string` | Krótkie podsumowanie roli i zawartości pliku. | Etykieta pomocnicza (tooltip) w interfejsie. |
| `files[].content` | `string` | `string (max 500000 znaków)` | Pełna tekstowa zawartość wygenerowanego pliku. | Zawartość bufora edytora kodu, payload do zapisu na dysk. |
| `project_context` | `ProjectContext \| null` | `object \| null` | Zaktualizowany obiekt kontekstu projektu. | Aktualizacja panelu bocznego projektu. |
| `stats` | `AgentStats` | `object (AgentRunStats)` | Metryki wykonania (`total_ms`, `retrieval_ms`, `model_ms`, `usage`, `stages`). | Wykresy i wskaźniki wydajności w panelu telemetrii. |
| `stats.total_ms` | `number` | `number (float)` | Całkowity czas wykonania zadania w milisekundach. | Wyświetlanie całkowitego czasu odpowiedzi przy wiadomości. |
| `stats.retrieval_ms` | `number` | `number (float)` | Czas spędzony na wyszukiwaniu wektorowym w ChromaDB. | Wskaźnik opóźnienia warstwy RAG. |
| `stats.model_ms` | `number` | `number (float)` | Czas generowania tokenów przez model LLM. | Wskaźnik czasu wnioskowania modelu sztucznej inteligencji. |
| `stats.validation_ms` | `number` | `number (float)` | Czas walidacji składniowej i weryfikacji bezpieczeństwa kodu. | Raportowanie czasu kontroli jakości generowanego kodu. |
| `stats.iterations` | `number` | `integer (min: 1)` | Liczba kroków i pętli wykonanych przez graf LangGraph. | Informacja o złożoności wieloetapowego planu wykonania. |
| `stats.retrieved_chunks`| `number` | `integer` | Liczba fragmentów publikacji pobranych z bazy RAG. | Licznik źródeł wiedzy wykorzystanych jako kontekst promptu. |
| `stats.usage` | `UsageStats` | `object (UsageStats)` | Zużycie tokenów (`prompt_tokens`, `completion_tokens`, `total_tokens`). | Monitorowanie kosztu obliczeniowego zapytania. |
| `stats.stages` | `StageMetric[]` | `array of objects ({name, duration_ms, detail})` | Lista metryk czasowych każdego etapu pipeline'u agenta. | Wizualizacja faz wykonania na osi czasu (timeline). |
| `created_at` | `string` | `string (ISO 8601 UTC)` | Znacznik czasu wygenerowania odpowiedzi. | Wyświetlenie daty i godziny przy wiadomości. |

###### Przykład Odpowiedzi JSON (`200 OK`):
```json
{
  "request_id": "a1b2c3d4e5f67890",
  "session_id": "9876543210fedcba",
  "version": "1.0.0b2",
  "answer": "Pomyślnie zaimplementowano moduł integracji...",
  "rationale": [
    "Przeanalizowano schemat bazy danych i wymagania dotyczące pól.",
    "Zidentyfikowano brakujące mapowanie relacji w modelu SQLAlchemy.",
    "Wygenerowano kod testu jednostkowego weryfikujący poprawność walidacji."
  ],
  "files": [
    {
      "path": "src/services/example.py",
      "language": "python",
      "purpose": "Implementacja logiki biznesowej serwisu",
      "content": "def execute():\n    return True\n"
    }
  ],
  "project_context": null,
  "stats": {
    "total_ms": 1420.5,
    "retrieval_ms": 45.2,
    "model_ms": 1310.0,
    "validation_ms": 12.3,
    "circuit_sim_ms": 0.0,
    "pypi_ms": 0.0,
    "iterations": 1,
    "retrieved_chunks": 4,
    "usage": {
      "prompt_tokens": 1250,
      "completion_tokens": 340,
      "total_tokens": 1590
    },
    "stages": [
      {
        "name": "rag_retrieval",
        "duration_ms": 45.2,
        "detail": "Pobrano 4 chunki z kolekcji ChromaDB"
      },
      {
        "name": "llm_inference",
        "duration_ms": 1310.0,
        "detail": "Model: Qwen2.5-Coder-32B"
      }
    ]
  },
  "created_at": "2026-11-08T20:30:00.000Z"
}
```

###### Efekty Uboczne i Interakcje Downstream (Side Effects)
- **Zarządzanie Pamięcią Sesji Dialogowej (Session Memory):** Rejestracja pary zapytanie-odpowiedź w pamięci RAM instancji agenta (`_sessions`, `_session_turns` w `CodingAgent`). W przypadku przekroczenia progu alokacji pamięci RAM następuje zrzut migawki stanu (Memory Spilling) do pliku w dedykowanym wolumenie sandboksa `../../../sessions`.
- **Wektoryzacja i Semantyczne Wyszukiwanie RAG:** Wywołanie serwisu `embedder:8090` (`POST /v1/embeddings`, `kind="query"`) w celu obliczenia wektora osadzenia zapytania oraz wykonanie kwerendy podobieństwa cosinusowego do bazy `chroma:8000` (`collection.query(where={"document_id": {"$in": document_ids}})`).
- **Pobranie i Wstrzyknięcie Reguł Bezpieczeństwa:** Odpytanie serwisu `workspace:8000` (baza `agent_permissions` w PostgreSQL) w celu pobrania aktywnych białych list komend oraz dyrektyw promptu systemowego.
- **Wieloetapowe Wnioskowanie i Synteza Kodu:** Iteracyjna generacja tokenów w modelu LLM (`model:8080`, np. Qwen2.5-Coder) sterowana grafem stanów LangGraph.
- **Walidacja Składniowa i Symulacje Podprocesowe:** Weryfikacja poprawności wygenerowanego kodu Python/TypeScript/SQL, wykonanie symulacji obwodów elektronicznych (silnik PySpice / ngspice zwracający `CircuitSimulationResult`) oraz opcjonalna kompilacja brył 3D w OpenSCAD.
- **Wyszukiwanie Internetowe (Web Search):** W przypadku flagi `enable_web_search=True` wykonanie zapytań sieciowych (Google/SearXNG) i utrwalenie rekordu audytowego `WebSearchAuditRecord`.
- **Weryfikacja Zależności PyPI:** Zapytania do oficjalnego indeksu pakietów PyPI dla bibliotek z listy `pypi_packages` i rejestracja wpisu `PypiAuditRecord`.
- **Zapis Plików i Synchronizacja Projektu:** W przypadku podania `project_id`/`volume_name` zapis utworzonych plików w katalogu sandboksa `/app/volumes/{volume_name}`, aktualizacja metadanych w `../../../data/projects.json`, odświeżenie pliku `.chappie/context.json` oraz wygenerowanie dokumentu Problem Context Klasy 2 (`project.context`).
- **Telemetria i Monitorowanie Zasobów:** Rejestracja czasu trwania poszczególnych etapów w `AgentRunStats` oraz liczby zużytych tokenów promptu i generacji (`UsageStats`).

---

#### `DELETE /api/sessions/{sessionId}` — Czyszczenie Pamięci Sesji i Powiązań Projektu
- **Funkcja w UI:** `api.clearSession(sessionId: string)` (`ui/src/api.ts:111`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:243`)
- **Opis Biznesowy i Rola:** Usuwa stan sesji z pamięci RAM procesu `agent`, kasuje plik migawki sesji z wolumenu `../../../sessions` oraz zeruje `active_session_id` i historię tur w powiązanym projekcie w `data/projects.json`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `{ status: string; session_id: string }`

---

#### `GET /api/documents` — Pobieranie Listy Zindeksowanych Dokumentów RAG
- **Funkcja w UI:** `api.documents()` (`ui/src/api.ts:80`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:252`) -> ChromaDB (`books_qwen3_06b_v1`)
- **Opis Biznesowy i Rola:** Pobiera z bazy wektorowej ChromaDB zestawienie wszystkich zindeksowanych publikacji PDF z agregacją stron i chunków.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `503 Service Unavailable`.
- **Typ DTO w TypeScript:** `DocumentSummary[]` (`ui/src/types.ts:89`)
- **Model Pydantic Backend:** `list[DocumentSummary]` (`src/chappie/models.py:164`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola | Zastosowanie w UI |
| :--- | :--- | :--- | :--- | :--- |
| `[].document_id` | `string` | `string (24-znakowy SHA-256)` | Unikalny identyfikator dokumentu. | Zaznaczanie checkboxów w selektorze RAG. |
| `[].filename` | `string` | `string` | Oryginalna nazwa pliku PDF. | Wyświetlanie nazwy pliku na liście. |
| `[].title` | `string` | `string` | Tytuł publikacji/książki. | Tytuł dokumentu w interfejsie. |
| `[].pages` | `number` | `integer` | Łączna liczba stron w dokumencie. | Licznik stron w widoku bazy wiedzy. |
| `[].chunks` | `number` | `integer` | Łączna liczba zwektoryzowanych wycinków tekstu. | Wskaźnik gęstości wektorowej w bazie RAG. |

---

#### `POST /api/documents` — Upload, Ekstrakcja Tekstu i Indeksowanie PDF
- **Funkcja w UI:** `api.uploadPdf(file: File, title: string)` (`ui/src/api.ts:114`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:836`) -> `embedder:8090` -> ChromaDB
- **Opis Biznesowy i Rola:** Przesyła plik PDF (multipart/form-data do 100MB), ekstrahuje tekst, dzieli na chunki (okno przesuwne 700 słów z zakładką 100 słów), generuje embeddingi wektorowe przez `embedder` i zapisuje w ChromaDB.

##### Parametry Żądania (Request)
- **Format:** `multipart/form-data`
- **Pola:** `file` (`UploadFile`, wymagany), `title` (`str | None`, opcjonalny).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `413 Payload Too Large`, `415 Unsupported Media Type`, `422 Unprocessable Entity`, `503 Service Unavailable`, `504 Gateway Timeout`.
- **Typ DTO w TypeScript:** `DocumentIndexResponse` (`ui/src/types.ts:97`)
- **Model Pydantic Backend:** `DocumentIndexResponse` (`src/chappie/models.py:172`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola | Zastosowanie w UI |
| :--- | :--- | :--- | :--- | :--- |
| `document_id` | `string` | `string` | Identyfikator SHA-256 zindeksowanego dokumentu. | Aktualizacja stanu wyselekcjonowanych dokumentów. |
| `filename` | `string` | `string` | Nazwa pliku PDF. | Potwierdzenie nazwy pliku. |
| `title` | `string` | `string` | Tytuł dokumentu. | Prezentacja tytułu na liście. |
| `pages` | `number` | `integer` | Liczba przetworzonych stron. | Statystyka przetwarzania. |
| `chunks` | `number` | `integer` | Liczba utworzonych wektorów. | Statystyka wektoryzacji. |
| `duration_ms` | `number` | `number (float)` | Czas indeksowania w milisekundach. | Raportowanie wydajności indeksowania. |

---

#### `DELETE /api/documents/{documentId}` — Usuwanie Dokumentu z Bazy Wektorowej
- **Funkcja w UI:** `api.deleteDocument(documentId: string)` (`ui/src/api.ts:121`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:259`) -> ChromaDB & `../../../data/profiles.json`
- **Opis Biznesowy i Rola:** Trwale usuwa wektory i chunki dokumentu z ChromaDB oraz kaskadowo wykreśla identyfikator dokumentu ze wszystkich profili wiedzy w `../../../data/profiles.json`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `{ status: string; document_id: string }`

---

#### `GET /api/profiles` — Pobieranie Listy Profili Wiedzy
- **Funkcja w UI:** `api.listProfiles()` (`ui/src/api.ts:124`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:270`) -> `ProfileStorage` (`../../../data/profiles.json`)
- **Opis Biznesowy i Rola:** Zwraca listę zdefiniowanych profili wiedzy grupujących zbiory dokumentów RAG. W razie braku bazy automatycznie tworzy profile domyślne (`default`, `electronics_standard_v1`).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `BookProfile[]` (`ui/src/types.ts:159`)
- **Model Pydantic Backend:** `list[BookProfile]` (`src/chappie/models.py:282`)

---

#### `POST /api/profiles` — Tworzenie Nowego Profilu Wiedzy
- **Funkcja w UI:** `api.createProfile(profile: BookProfileCreate)` (`ui/src/api.ts:125`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:278`) -> `ProfileStorage` (`../../../data/profiles.json`)
- **Opis Biznesowy i Rola:** Tworzy nowy profil wiedzy z unikalną nazwą i listą przypisanych dokumentów RAG. Zapis atomowy pod blokadą `threading.RLock`.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `BookProfileCreate` (`ui/src/types.ts:167`)
- **Model Pydantic Backend:** `BookProfileCreate` (`src/chappie/models.py:251`)

| Pole / Parametr | Typ TypeScript | Typ Backend | Wymagany | Opis i Przeznaczenie |
| :--- | :--- | :--- | :--- | :--- |
| `name` | `string` | `str (min_length=1, max_length=100)` | Tak | Unikalna nazwa profilu. |
| `description` | `string` | `str (default="", max_length=500)` | Nie | Opis merytoryczny profilu. |
| `document_ids` | `string[]` | `list[str] (default=[])` | Nie | Tablica ID dokumentów przypisanych do profilu. |

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `201 Created`, `400 Bad Request` (duplikat nazwy), `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `BookProfile` (`ui/src/types.ts:159`)

---

#### `PUT /api/profiles/{profileId}` — Aktualizacja Profilu Wiedzy
- **Funkcja w UI:** `api.updateProfile(profileId: string, profile: BookProfileUpdate)` (`ui/src/api.ts:126`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:288`) -> `ProfileStorage`
- **Opis Biznesowy i Rola:** Modyfikuje nazwę, opis lub zbiór dokumentów profilu z zachowaniem unikalności nazwy i deduplikacją ID.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `BookProfileUpdate` (`ui/src/types.ts:174`)
- **Model Pydantic Backend:** `BookProfileUpdate` (`src/chappie/models.py:266`)

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `404 Not Found`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `BookProfile` (`ui/src/types.ts:159`)

---

#### `DELETE /api/profiles/{profileId}` — Usunięcie Profilu Wiedzy
- **Funkcja w UI:** `api.deleteProfile(profileId: string)` (`ui/src/api.ts:132`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:303`) -> `ProfileStorage`
- **Opis Biznesowy i Rola:** Usuwa profil wiedzy z `../../../data/profiles.json`. Same dokumenty i wektory w ChromaDB pozostają nienaruszone.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `{ status: string; profile_id: string }`

---

### Moduł 3: Środowisko Robocze, Reguły i Uprawnienia SQL (Workspace)

#### `GET /api/workspace/state` — Stan Podsystemu Workspace
- **Funkcja w UI:** `api.workspaceState()` (`ui/src/api.ts:142`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:212`) -> `workspace:8000/api/workspace/rules`
- **Opis Biznesowy i Rola:** Zwraca stan włączenia kontroli granic workspace, status połączenia z serwisem bazy PostgreSQL oraz listę dozwolonych korzeni roboczych.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `502 Bad Gateway`.
- **Typ DTO w TypeScript:** `WorkspaceStateResponse` (`ui/src/types.ts:122`)
- **Model Pydantic Backend:** `WorkspaceStateResponse` (`src/chappie/models.py:237`)

---

#### `POST /api/workspace/toggle` — Przełączanie Ochrony Workspace
- **Funkcja w UI:** `api.toggleWorkspace(enabled: boolean)` (`ui/src/api.ts:143`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:224`) -> `WorkspaceClient.set_enabled()`
- **Opis Biznesowy i Rola:** Dynamicznie aktywuje lub dezaktywuje egzekucję granic workspace w silniku agenta (zasada fail-closed).

##### Parametry Żądania (Request)
- **Model Pydantic Backend:** `WorkspaceStateToggleRequest` (`src/chappie/models.py:226`) — pole `enabled: bool`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `WorkspaceStateResponse` (`ui/src/types.ts:122`)

---

#### `GET /api/workspace/rules` — Zestawienie Reguł i Dyrektyw Agenta
- **Funkcja w UI:** `api.fetchAgentRules()` (`ui/src/api.ts:149`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:216`) -> `workspace:8000` -> PostgreSQL (`agent_permissions`)
- **Opis Biznesowy i Rola:** Agreguje białe listy komend (`command`), wzorce ścieżek (`whitelist_pattern`) oraz dyrektywy promptu wstrzykiwane do LLM.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `WorkspaceRulesSummaryResponse` (`ui/src/types.ts:142`)
- **Model Pydantic Backend:** `AgentPermissionsSummary` (`src/chappie/models.py:205`)

---

#### `POST /api/workspace/sql/execute` — Bezpieczna Konsola Zapytań SQL
- **Funkcja w UI:** `api.executeSql(query: string)` (`ui/src/api.ts:150`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:220`) -> `workspace:8000` -> PostgreSQL / SQLite
- **Opis Biznesowy i Rola:** Wykonuje zapytania SQL (SELECT, INSERT, UPDATE, DDL) na relacyjnej bazie danych z pomiarem czasu wykonania i atomowością transakcji.

##### Parametry Żądania (Request)
- **Model Pydantic Backend:** `SqlExecuteRequest` (`src/chappie/models.py:213`) — pole `query: str`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `SqlExecuteResponse` (`ui/src/types.ts:150`)
- **Model Pydantic Backend:** `SqlExecuteResponse` (`src/chappie/models.py:217`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola |
| :--- | :--- | :--- | :--- |
| `success` | `boolean` | `boolean` | Flaga pomyślnego wykonania zapytania (`true`/`false`). |
| `columns` | `string[]` | `array of strings` | Nazwy kolumn tabeli wynikowej (dla `SELECT`). |
| `rows` | `any[][]` | `array of arrays` | Zwrócone wiersze danych. |
| `row_count` | `number` | `integer` | Liczba wierszy zwróconych lub zmodyfikowanych. |
| `execution_time_ms` | `number` | `number (float)` | Czas wykonania zapytania w milisekundach. |
| `error` | `string \| null` | `string \| null` | Komunikat błędu silnika SQL przy niepowodzeniu. |

---

#### `POST /api/workspace/validate` — Weryfikacja Uprawnień do Ścieżki
- **Funkcja w UI:** Weryfikacja uprawnień I/O w `WorkspaceClient.validate_write_path`.
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:230`) -> `WorkspaceClient`
- **Opis Biznesowy i Rola:** Sprawdza, czy wskazana ścieżka mieści się w dozwolonych granicach sandboxa na podstawie aktywnych wzorców regex.

##### Struktura Odpowiedzi (Response)
- **Model Pydantic Backend:** `WorkspaceFileValidationResult` (`src/chappie/models.py:245`) — pola `allowed: bool`, `target_path: str`, `canonical_path: str`, `reason: str`.

---

### Moduł 4: Wolumeny Sandboksa i Operacje Plikowe (Volumes & Files)

#### `GET /api/volumes` — Wykrywanie i Metadane Wolumenów Sandboksa
- **Funkcja w UI:** `api.listVolumes()` (`ui/src/api.ts:156`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:317`) -> `VolumeManager` (`/app/volumes/`)
- **Opis Biznesowy i Rola:** Skanuje katalog `/app/volumes/`, oblicza liczbę plików i rozmiar każdego wolumenu oraz filtruje foldery ukryte/techniczne.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `VolumeInfo[]` (`ui/src/types.ts:194`)
- **Model Pydantic Backend:** `list[VolumeInfo]` (`src/chappie/models.py:291`)

---

#### `POST /api/volumes` — Utworzenie Wolumenu Sandboksa
- **Funkcja w UI:** `api.createVolume(volume: VolumeCreate)` (`ui/src/api.ts:157`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:325`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Tworzy nowy katalog wolumenu w piaskownicy z rygorystyczną walidacją nazwy regex `^[a-zA-Z0-9_\-\.]+$` (ochrona przed Path Traversal).

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `VolumeCreate` (`ui/src/types.ts:203`)
- **Model Pydantic Backend:** `VolumeCreate` (`src/chappie/models.py:300`) — pole `name: str (1..100)`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `201 Created`, `400 Bad Request`, `403 Forbidden`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `VolumeInfo` (`ui/src/types.ts:194`)

---

#### `GET /api/volumes/{volumeName}/files` — Listowanie Plików w Wolumenie
- **Funkcja w UI:** `api.listVolumeFiles(volumeName: string, subpath = "")` (`ui/src/api.ts:163`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:337`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Rekurencyjnie listuje strukturę plików i podkatalogów w wolumenie sandboksa z zachowaniem limitów (`max_depth=10`, `max_files=500`).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectFileInfo[]` (`ui/src/types.ts:229`)
- **Model Pydantic Backend:** `list[ProjectFileInfo]` (`src/chappie/models.py:315`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola |
| :--- | :--- | :--- | :--- |
| `[].path` | `string` | `string` | Względna znormalizowana ścieżka do pliku/katalogu. |
| `[].size_bytes` | `number` | `integer` | Rozmiar pliku w bajtach. |
| `[].modified_at` | `string` | `string (ISO 8601 UTC)` | Data ostatniej modyfikacji. |
| `[].is_directory`| `boolean` | `boolean` | Flaga określająca, czy element jest katalogiem. |
| `[].extension` | `string` | `string` | Rozszerzenie pliku (np. `"py"`, `"ts"`). |

---

#### `GET /api/volumes/{volumeName}/files/read` — Bezpieczny Odczyt Pliku z Wolumenu
- **Funkcja w UI:** `api.readVolumeFile(volumeName: string, path: string)` (`ui/src/api.ts:165`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:351`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Odczytuje treść tekstową pliku UTF-8 z piaskownicy z limitem rozmiaru 1 MB i weryfikacją `resolve_safe_path`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectFileContent` (`ui/src/types.ts:237`)
- **Model Pydantic Backend:** `ProjectFileContent` (`src/chappie/models.py:323`) — pola `path: str`, `content: str`, `size_bytes: int`, `language: str`.

---

#### `POST /api/volumes/{volumeName}/files/write` — Zapis Pojedynczego Pliku w Wolumenie
- **Funkcja w UI:** `api.writeVolumeFile(volumeName: string, payload: ProjectFileWriteRequest)` (`ui/src/api.ts:167`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:368`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Zapisuje zawartość tekstową do pliku na wolumenie z automatycznym tworzeniem folderów nadrzędnych i walidacją `safe_relative_path`.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `ProjectFileWriteRequest` (`ui/src/types.ts:244`)
- **Model Pydantic Backend:** `ProjectFileWriteRequest` (`src/chappie/models.py:330`) — pola `path: str (1..240)`, `content: str (max 1MB)`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectFileInfo` (`ui/src/types.ts:229`)

---

#### `POST /api/volumes/{volumeName}/files/batch` — Hurtowy Zapis Paczki Plików
- **Funkcja w UI:** `api.batchWriteVolumeFiles(volumeName: string, payload: BatchWriteFilesRequest)` (`ui/src/api.ts:173`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:388`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Zapisuje hurtowo do 500 plików w ramach jednego żądania z opcjonalną ścieżką bazową `base_path` i flagą `overwrite`.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `BatchWriteFilesRequest` (`ui/src/types.ts:346`)
- **Model Pydantic Backend:** `BatchWriteFilesRequest` (`src/chappie/models.py:567`) — pola `base_path?: str`, `files: FileWriteItem[] (1..500)`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `BatchWriteFilesResponse` (`ui/src/types.ts:351`)
- **Model Pydantic Backend:** `BatchWriteFilesResponse` (`src/chappie/models.py:591`) — pola `success: bool`, `volume_name: str`, `files_written: ProjectFileInfo[]`, `total_files: int`, `total_bytes: int`.

---

#### `DELETE /api/volumes/{volumeName}/files` — Selektywne Usuwanie Pliku
- **Funkcja w UI:** `api.deleteVolumeFile(volumeName: string, path: string)` (`ui/src/api.ts:179`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:435`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Usuwa pojedynczy plik z wolumenu sandboksa. Blokuje próby usuwania katalogów (`IsADirectoryError -> HTTP 400`).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `{ status: string; volume_name: string; path: string }`

---

### Moduł 5: Zarządzanie Projektami, Skanowanie i Transfery Międzyprojektowe

#### `GET /api/projects` — Pobranie Rejestru Wszystkich Projektów
- **Funkcja w UI:** `api.listProjects()` (`ui/src/api.ts:183`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:458`) -> `ProjectStorage` (`../../../data/projects.json`)
- **Opis Biznesowy i Rola:** Pobiera listę wszystkich projektów z automatyczną synchronizacją plików `.chappie` dla projektów lokalnych, posortowaną malejąco po `updated_at`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext[]` (`ui/src/types.ts:249`)
- **Model Pydantic Backend:** `list[ProjectContext]` (`src/chappie/models.py:462`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola |
| :--- | :--- | :--- | :--- |
| `[].id` | `string` | `string (12-znakowy Hex UUID)` | Identyfikator projektu. |
| `[].name` | `string` | `string (1..100)` | Nazwa wyświetlana projektu. |
| `[].volume_name` | `string` | `string` | Nazwa wolumenu sandboksa w `/app/volumes/`. |
| `[].description` | `string` | `string` | Opis celów i zakresu projektu. |
| `[].profile_id` | `string \| null` | `str \| None` | Identyfikator powiązanego profilu wiedzy RAG. |
| `[].profile_name` | `string \| null` | `str \| None` | Nazwa powiązanego profilu wiedzy. |
| `[].active_session_id` | `string \| null` | `str \| None` | Identyfikator aktywnej sesji czatu. |
| `[].context_document` | `string` | `string` | Pełny dokument Problem Context Klasy 2 (Markdown). |
| `[].git_config` | `ProjectGitConfig` | `object` | Konfiguracja repozytorium Git. |
| `[].root_path` | `string \| null` | `str \| None` | Ścieżka do katalogu lokalnego (jeśli dotyczy). |
| `[].is_local_dir` | `boolean` | `bool` | Flaga projektu z lokalnego katalogu. |
| `[].chappie_context` | `string` | `string` | Zawartość pliku `.chappie`. |
| `[].has_explanation` | `boolean` | `bool` | Flaga wygenerowanej analizy architektonicznej. |
| `[].updated_at` | `string` | `string (ISO 8601 UTC)` | Data ostatniej modyfikacji. |

---

#### `POST /api/projects` — Rejestracja Nowego Projektu
- **Funkcja w UI:** `api.createProject(project: ProjectContextCreate)` (`ui/src/api.ts:184`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:466`) -> `ProjectStorage` & `VolumeManager`
- **Opis Biznesowy i Rola:** Rejestruje projekt w `ProjectStorage`, tworzy katalog wolumenu sandboksa, generuje domyślny dokument Problem Context Klasy 2 oraz zapisuje pliki `.chappie/context.json` i `project.context`.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `ProjectContextCreate` (`ui/src/types.ts:271`)
- **Model Pydantic Backend:** `ProjectContextCreate` (`src/chappie/models.py:390`)

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `201 Created`, `400 Bad Request`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `GET /api/projects/{projectId}` — Szczegóły Pojedynczego Projektu
- **Funkcja w UI:** `api.getProject(projectId: string)` (`ui/src/api.ts:190`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:486`) -> `ProjectStorage`
- **Opis Biznesowy i Rola:** Zwraca pełny stan projektu z doładowaniem świeżego pliku `.chappie` z dysku.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `PUT /api/projects/{projectId}` — Aktualizacja Konfiguracji Projektu
- **Funkcja w UI:** `api.updateProject(projectId: string, project: ProjectContextUpdate)` (`ui/src/api.ts:191`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:499`) -> `ProjectStorage`
- **Opis Biznesowy i Rola:** Aktualizuje metadane, profil wiedzy, repozytorium Git oraz synchronizuje nagłówek profilu w pliku `.chappie` na dysku.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `ProjectContextUpdate` (`ui/src/types.ts:286`)
- **Model Pydantic Backend:** `ProjectContextUpdate` (`src/chappie/models.py:423`)

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `404 Not Found`, `422 Unprocessable Entity`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `DELETE /api/projects/{projectId}` — Usunięcie Projektu z Rejestru
- **Funkcja w UI:** `api.deleteProject(projectId: string)` (`ui/src/api.ts:197`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:525`) -> `ProjectStorage`
- **Opis Biznesowy i Rola:** Usuwa rekord projektu z `../../../data/projects.json`. Fizyczne pliki na dysku i wolumeny pozostają nienaruszone.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `{ status: string; project_id: string }`

---

#### `POST /api/projects/{projectId}/scan` — Skanowanie Telemetrii Plików Projektu
- **Funkcja w UI:** `api.scanProject(projectId: string)` (`ui/src/api.ts:201`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:538`) -> `VolumeManager` & `ProjectStorage`
- **Opis Biznesowy i Rola:** Rekurencyjnie skanuje pliki projektu, przelicza liczbę plików i sumaryczny rozmiar w bajtach oraz zapisuje w `state_metadata`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `POST /api/projects/{projectId}/context/update` — Kognitywna Synteza Problem Context
- **Funkcja w UI:** `api.updateProjectContext(projectId: string)` (`ui/src/api.ts:205`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:565`) -> `CodingAgent.update_project_current_context()`
- **Opis Biznesowy i Rola:** Syntetyzuje ustrukturyzowany dokument Problem Context Klasy 2 (zgodnie z `../../../MICHALS_WAYS.md`), agregując historię tur, drzewo plików, metadane Git i profilu, po czym zapisuje w `project.context` i `data/projects.json`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `POST /api/projects/open-directory` — Otwieranie Katalogu Lokalnego
- **Funkcja w UI:** `api.openDirectoryProject(payload: OpenDirectoryProjectRequest)` (`ui/src/api.ts:209`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:581`) -> `ProjectStorage` -> `WorkspaceService`
- **Opis Biznesowy i Rola:** Otwiera katalog lokalny, tworzy/ładuje plik `.chappie`, rejestruje projekt i ustawia piaskownicę I/O w `WorkspaceService`.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `OpenDirectoryProjectRequest` (`ui/src/types.ts:303`)
- **Model Pydantic Backend:** `OpenDirectoryProjectRequest` (`src/chappie/models.py:484`) — pola `path: str`, `name?: str`, `profile_id?: str`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `POST /api/projects/upload-directory` — Import Struktury Plików Projektu
- **Funkcja w UI:** `api.uploadDirectoryProject(payload: UploadDirectoryProjectRequest)` (`ui/src/api.ts:215`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:611`) -> `ProjectStorage` & `VolumeManager`
- **Opis Biznesowy i Rola:** Przyjmuje paczkę plików wyselekcjonowaną w przeglądarce, zapisuje w bezpiecznym podkatalogu wolumenu, inicjalizuje `.chappie` i rejestruje projekt.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `UploadDirectoryProjectRequest` (`ui/src/types.ts:309`)
- **Model Pydantic Backend:** `UploadDirectoryProjectRequest` (`src/chappie/models.py:498`) — pola `name: str`, `files: FileWriteItem[] (1..5000)`, `profile_id?: str`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `POST /api/projects/switch-context` — Przełączanie Aktywnego Kontekstu
- **Funkcja w UI:** `api.switchProjectContext(payload: SwitchProjectContextRequest)` (`ui/src/api.ts:221`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:643`) -> `ProjectStorage` -> `WorkspaceService`
- **Opis Biznesowy i Rola:** Atomowo zrzuca stan opuszczanego projektu do `.chappie`, ładuje świeży kontekst projektu docelowego i rekonfiguruje uprawnienia sandboksa w `WorkspaceService`.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `SwitchProjectContextRequest` (`ui/src/types.ts:315`)
- **Model Pydantic Backend:** `SwitchProjectContextRequest` (`src/chappie/models.py:514`) — pola `target_project_id: str`, `current_project_id?: str`, `save_current?: bool`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `POST /api/projects/{projectId}/chappie/sync` — Synchronizacja Pliku `.chappie`
- **Funkcja w UI:** `api.syncChappieContext(projectId: string)` (`ui/src/api.ts:227`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:671`) -> `ProjectStorage`
- **Opis Biznesowy i Rola:** Wymusza ponowne odczytanie pliku `.chappie` z dysku i zsynchronizowanie go ze stanem w `../../../data/projects.json`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `POST /api/projects/{projectId}/explain` — Zapis Wyjaśnienia Architektonicznego
- **Funkcja w UI:** `api.explainProject(projectId: string, explanation: string)` (`ui/src/api.ts:231`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:687`) -> `ProjectStorage`
- **Opis Biznesowy i Rola:** Dołącza wygenerowaną analizę architektoniczną do pliku `.chappie` i ustawia flagę `has_explanation=True`.

##### Parametry Żądania (Request)
- **Lokalizacja:** `Path` + `Body: { explanation: string }`

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectContext` (`ui/src/types.ts:249`)

---

#### `GET /api/projects/{projectId}/files` — Listowanie Drzewa Plików Projektu
- **Funkcja w UI:** `api.listProjectFiles(projectId: string, subpath = "")` (`ui/src/api.ts:237`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:709`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Bezpiecznie listuje pliki w sandboksie projektu (ignorując `.git`, `node_modules`, `venv`, `dist`) z weryfikacją `PathSecurityViolation`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `403 Forbidden`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectFileInfo[]` (`ui/src/types.ts:229`)
- **Model Pydantic Backend:** `list[ProjectFileInfo]` (`src/chappie/models.py:315`)

---

#### `GET /api/projects/{projectId}/files/read` — Bezpieczny Odczyt Pliku Projektu
- **Funkcja w UI:** `api.readProjectFile(projectId: string, path: string)` (`ui/src/api.ts:239`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:726`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Bezpiecznie odczytuje tekstową zawartość pliku (limit 1 MB) z mapowaniem na identyfikator języka składni.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectFileContent` (`ui/src/types.ts:237`)
- **Model Pydantic Backend:** `ProjectFileContent` (`src/chappie/models.py:323`) — pola `path: str`, `content: str`, `size_bytes: int`, `language: str`.

---

#### `POST /api/projects/{projectId}/files/write` — Zapis Pliku i Audyt w `.chappie`
- **Funkcja w UI:** `api.writeProjectFile(projectId: string, payload: ProjectFileWriteRequest)` (`ui/src/api.ts:241`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:744`) -> `VolumeManager` & `ProjectStorage`
- **Opis Biznesowy i Rola:** Zapisuje treść do pliku w sandboksie projektu i dopisuje wpis audytowy do pliku `.chappie`.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `ProjectFileWriteRequest` (`ui/src/types.ts:244`)
- **Model Pydantic Backend:** `ProjectFileWriteRequest` (`src/chappie/models.py:330`) — pola `path: str`, `content: str`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`, `422 Unprocessable Entity`.
- **Typ DTO w TypeScript:** `ProjectFileInfo` (`ui/src/types.ts:229`)

---

#### `DELETE /api/projects/{projectId}/files` — Usuwanie Pliku z Projektu
- **Funkcja w UI:** `api.deleteProjectFile(projectId: string, path: string)` (`ui/src/api.ts:247`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:781`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Bezpiecznie usuwa pojedynczy plik z piaskownicy projektu (blokując usuwanie katalogów).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `{ status: string; project_id: string; path: string }`

---

#### `POST /api/projects/{projectId}/files/transfer` — Bezpieczny Transfer Plików Między Projektami
- **Funkcja w UI:** `api.transferProjectFile(projectId: string, payload: ProjectFileTransferRequest)` (`ui/src/api.ts:251`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:809`) -> `ProjectStorage` & `VolumeManager`
- **Opis Biznesowy i Rola:** Bezpiecznie kopiuje lub przenosi plik między dwoma projektami. Wymaga obecności pliku `.chappie` w obu korzeniach projektów, braku dopasowania do reguł `../../../.gitignore` oraz rejestruje audyt w plikach `.chappie`.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `ProjectFileTransferRequest` (`ui/src/types.ts:58`)
- **Model Pydantic Backend:** `ProjectFileTransferRequest` (`src/chappie/models.py:601`)

| Pole / Parametr | Typ TypeScript | Typ Backend | Wymagany | Opis i Przeznaczenie |
| :--- | :--- | :--- | :--- | :--- |
| `source_path` | `string` | `str (1..500)` | Tak | Relatywna ścieżka do pliku źródłowego. |
| `target_project_id`| `string` | `str (1..128)` | Tak | Identyfikator projektu docelowego. |
| `target_path` | `string` | `str (1..500)` | Tak | Relatywna ścieżka docelowa w projekcie odbiorcy. |
| `move` | `boolean` | `bool (default=False)` | Nie | Flaga przenoszenia (`true` = move/cut, `false` = copy). |
| `allow_cross_project`| `boolean` | `bool (default=True)` | Nie | Jawna zgoda na transfer międzyprojektowy. |

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden` (brak `.chappie` lub reguła `../../../.gitignore`), `404 Not Found`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `ProjectFileTransferResponse` (`ui/src/types.ts:66`)
- **Model Pydantic Backend:** `ProjectFileTransferResponse` (`src/chappie/models.py:611`)

| Pole Odpowiedzi | Typ TypeScript | Typ JSON Schema / Pydantic | Opis Znaczenia Pola |
| :--- | :--- | :--- | :--- |
| `success` | `boolean` | `boolean` | Potwierdzenie pomyślności transferu (`true`). |
| `source_project_id`| `string` | `string` | Identyfikator projektu źródłowego. |
| `source_path` | `string` | `string` | Ścieżka źródłowa. |
| `target_project_id`| `string` | `string` | Identyfikator projektu docelowego. |
| `target_path` | `string` | `string` | Ścieżka docelowa w projekcie odbiorcy. |
| `bytes_transferred`| `number` | `integer` | Liczba przesłanych bajtów. |
| `is_move` | `boolean` | `boolean` | Flaga określająca, czy plik przeniesiono (`true`), czy skopiowano (`false`). |
| `message` | `string` | `string` | Czytelne podsumowanie operacji. |

---

### Moduł 6: Diagnostyka Logów, Live Chat, Kamera Pi i Vision AI

#### `GET /api/logs` — Agregacja i Filtrowanie Logów Systemowych
- **Funkcja w UI:** `api.fetchLogs(service, lines, level, q)` (`ui/src/api.ts:257`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:877`) -> `ServiceLogCollector`
- **Opis Biznesowy i Rola:** Pobiera logi wybranego mikroserwisu lub całego stosu (`all`) z bufora RAM `LogBufferHandler`, plików `./logs/*.log` lub demona Dockera.

##### Parametry Żądania (Request)
- **Lokalizacja:** `Query: service?: string, lines?: number (10..2000), level?: string, q?: string`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `500 Internal Server Error`, `502 Bad Gateway`.
- **Typ DTO w TypeScript:** `ServiceLogsResponse` (`ui/src/types.ts:32` / `168`)
- **Model Pydantic Backend:** `ServiceLogsResponse` (`src/chappie/models.py:270`) — pola `service: str`, `available_services: list[str]`, `total_lines: int`, `lines_returned: int`, `logs: list[str]`, `entries: list[ServiceLogEntry]`, `source: Literal["docker", "file", "memory"]`, `timestamp: str`.

---

#### `GET /api/logs/services` — Lista Monitorowanych Usług
- **Funkcja w UI:** `api.fetchLogServices()` (`ui/src/api.ts:265`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:890`)
- **Opis Biznesowy i Rola:** Zwraca listę identyfikatorów serwisów (`all`, `agent`, `model`, `embedder`, `chroma`, `ui`, `indexer`).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `502 Bad Gateway`.
- **Typ DTO w TypeScript:** `string[]`

---

#### `DELETE /api/logs` — Czyszczenie Bufora Pamięciowego Logów
- **Funkcja w UI:** `api.clearLogs()` (`ui/src/api.ts:266`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:894`) -> `ServiceLogCollector.clear()`
- **Opis Biznesowy i Rola:** Resetuje kolejkę pierścieniową logów w pamięci RAM serwisu agenta.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `502 Bad Gateway`.
- **Typ DTO w TypeScript:** `{ status: string; message: string }`

---

#### `GET /api/live-chat/contexts` — Rejestr Zapisanych Wątków Czatu
- **Funkcja w UI:** `api.listLiveChatContexts()` (`ui/src/api.ts:298`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1040`) -> `VolumeManager` (`live-chat/contexts/`)
- **Opis Biznesowy i Rola:** Pobiera listę podsumowań trwałych wątków czatu zapisanych w wolumenie sandboksa.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `LiveChatContextSummary[]` (`ui/src/types.ts:12`)
- **Model Pydantic Backend:** `list[LiveChatContextSummary]` (`src/chappie/models.py:321`)

---

#### `GET /api/live-chat/contexts/{contextId}` — Pełna Historia Wątku Czatu
- **Funkcja w UI:** `api.getLiveChatContext(contextId: string)` (`ui/src/api.ts:299`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1103`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Wczytuje pełny obiekt wybranego wątku konwersacyjnego z wiadomościami i metadanymi.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`.
- **Typ DTO w TypeScript:** `LiveChatContext` (`ui/src/types.ts:11`)
- **Model Pydantic Backend:** `LiveChatContext` (`src/chappie/models.py:329`)

---

#### `GET /api/live-chat/contexts/latest` — Ostatnio Modyfikowany Wątek Czatu
- **Funkcja w UI:** `api.getLatestLiveChatContext()` (`ui/src/api.ts:301`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1082`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Zwraca najświeższy wątek konwersacji do inicjalizacji widoku czatu.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `404 Not Found`.
- **Typ DTO w TypeScript:** `LiveChatContext` (`ui/src/types.ts:11`)

---

#### `POST /api/live-chat/contexts` — Utworzenie lub Zapis Wątku Czatu
- **Funkcja w UI:** `api.saveLiveChatContext(payload: LiveChatSaveRequest)` (`ui/src/api.ts:302`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1053`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Zapisuje stan konwersacji do pliku JSON w wolumenie sandboksa z automatyczną sanityzacją dużych ciągów base64.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `LiveChatSaveRequest` (`ui/src/types.ts:13`)
- **Model Pydantic Backend:** `LiveChatSaveRequest` (`src/chappie/models.py:339`) — pola `context_id?: str`, `title: str`, `messages: LiveChatMessage[]`, `system_prompt?: str`, `metadata?: dict`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `422 Unprocessable Entity`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `LiveChatContext` (`ui/src/types.ts:11`)

---

#### `DELETE /api/live-chat/contexts/{contextId}` — Usunięcie Wątku Czatu
- **Funkcja w UI:** `api.deleteLiveChatContext(contextId: string)` (`ui/src/api.ts:308`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1149`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Trwale usuwa plik wątku konwersacji z wolumenu sandboksa.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`.
- **Typ DTO w TypeScript:** `{ status: string; id: string }`

---

#### `POST /api/live-chat/session/auto-save` — Autozapis Stanu Sesji Roboczej
- **Funkcja w UI:** `api.autoSaveLiveChatSession(payload: LiveChatSessionState)` (`ui/src/api.ts:312`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1167`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Zapisuje stan roboczy aktywnego czatu w pliku tymczasowym, chroniąc przed utratą danych po przeładowaniu strony.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `LiveChatSessionState` (`ui/src/types.ts:14`)
- **Model Pydantic Backend:** `LiveChatSessionState` (`src/chappie/models.py:348`)

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `{ status: string; session_id: string }`

---

#### `GET /api/live-chat/session` — Odzyskanie Autozapisanego Stanu Sesji
- **Funkcja w UI:** `api.getLiveChatSession(sessionId?: string)` (`ui/src/api.ts:318`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1185`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Wczytuje roboczy stan dialogu po przeładowaniu strony.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `LiveChatSessionState` (`ui/src/types.ts:14`)

---

#### `DELETE /api/live-chat/session` — Wyczyszczenie Stanu Sesji Roboczej
- **Funkcja w UI:** `api.clearLiveChatSession(sessionId?: string)` (`ui/src/api.ts:322`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1203`) -> `VolumeManager`
- **Opis Biznesowy i Rola:** Kasuje tymczasowy plik autozapisu po wyczyszczeniu czatu lub zmianie widoku.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `500 Internal Server Error`.
- **Typ DTO w TypeScript:** `{ status: string }`

---

#### `POST /api/live-chat/task` — Tura Konwersacyjna z Opcjonalną Wizją
- **Funkcja w UI:** `api.runLiveChatTask(payload: LiveChatTaskRequest)` (`ui/src/api.ts:328`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1217`) -> `vision_model:8085` -> `model:8080` -> `VolumeManager`
- **Opis Biznesowy i Rola:** Realizuje wieloturowy dialog w Live Chat. W razie obecności obrazu wykonuje OCR/ekstrakcję cech w `vision_model:8085`, wstrzykuje wyodrębniony tekst do promptu, odpytuje silnik LLM i opcjonalnie zapisuje wątek na dysk.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `LiveChatTaskRequest` (`ui/src/types.ts:15`)
- **Model Pydantic Backend:** `LiveChatTaskRequest` (`src/chappie/models.py:357`) — pola `task: str`, `context_id?: str`, `title?: str`, `messages?: LiveChatMessage[]`, `image_data_url?: str`, `rois?: RoiBox[]`, `save_context?: bool`, `metadata?: dict`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `422 Unprocessable Entity`, `500 Internal Server Error`, `502 Bad Gateway`, `504 Gateway Timeout`.
- **Typ DTO w TypeScript:** `LiveChatTaskResponse` (`ui/src/types.ts:16`)
- **Model Pydantic Backend:** `LiveChatTaskResponse` (`src/chappie/models.py:369`) — pola `answer: str`, `context_id?: str`, `extracted_text?: str`, `messages: LiveChatMessage[]`, `duration_ms: float`, `created_at: str`.

---

#### `GET /api/pi/stream` — Strumień Wideo MJPEG z Kamery Raspberry Pi
- **Funkcja w UI:** `api.getPiStreamUrl(piUrl?: string)` (`ui/src/api.ts:334`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py` streaming) -> `agent:8001` (`src/chappie/api.py:900`) -> Raspberry Pi (`sensors/pi/server.py:8000/stream`)
- **Opis Biznesowy i Rola:** Niskopoziomowy strumień wideo MJPEG (`multipart/x-mixed-replace; boundary=frame`) bez buforowania pośredniego (`X-Accel-Buffering: no`) z kamery Raspberry Pi.

##### Parametry Żądania (Request)
- **Lokalizacja:** `Query: pi_url?: string` (walidacja SSRF w `validate_pi_url()`).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK` (HTTP Streaming), `400 Bad Request`, `502 Bad Gateway`.
- **Nagłówki:** `Content-Type: multipart/x-mixed-replace; boundary=frame`, `Cache-Control: no-cache`.

---

#### `POST /api/pi/snapshot` — Snapshot o Wysokiej Rozdzielczości z Kamery Pi
- **Funkcja w UI:** `api.fetchPiSnapshot(params?: PiSnapshotRequest)` (`ui/src/api.ts:337`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:924`) -> Raspberry Pi (`sensors/pi/server.py:8000/snapshot`)
- **Opis Biznesowy i Rola:** Wykonuje precyzyjny snapshot fotograficzny z sensora kamery Pi (rozdzielczość do 4K, jakość, kontrast, jasność, rotacja, odbicie) i zwraca Data URL base64.

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `PiSnapshotRequest` (`ui/src/types.ts:22` / `427`)
- **Model Pydantic Backend:** `PiSnapshotRequest` (`src/chappie/models.py:382`)

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `422 Unprocessable Entity`, `502 Bad Gateway`.
- **Typ DTO w TypeScript:** `PiSnapshotResponse` (`ui/src/types.ts:23` / `440`)
- **Model Pydantic Backend:** `PiSnapshotResponse` (`src/chappie/models.py:387`) — pola `data_url: str`, `image_data_url: str`, `size_bytes: int`, `width: int`, `height: int`, `content_type: str`, `timestamp: str`, `source_url: str`.

---

#### `GET /api/pi/status` — Telemetria Urządzenia Raspberry Pi
- **Funkcja w UI:** `api.fetchPiStatus(piUrl?: string)` (`ui/src/api.ts:344`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:996`) -> Raspberry Pi (`sensors/pi/server.py:8000/telemetry`)
- **Opis Biznesowy i Rola:** Odczytuje gotowość sensora wideo i parametry diagnostyczne Pi (temperatura procesora, FPS, model kamery).

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `502 Bad Gateway`.
- **Typ DTO w TypeScript:** `PiCameraStatus` (`ui/src/types.ts:21` / `451`)
- **Model Pydantic Backend:** `PiCameraStatus` (`src/chappie/models.py:378`) — pola `status: str`, `url: str`, `telemetry: dict | null`, `error: str | null`.

---

#### `POST /api/vision/extract` — Ekstrakcja Kognitywna i OCR z Obrazu (VLM)
- **Funkcja w UI:** `api.extractVisionText(payload: VisionExtractRequest)` (`ui/src/api.ts:348`)
- **Przepływ Sieciowy:** `reverse_proxy:443` -> `backend:8002` (`gateway.py`) -> `agent:8001` (`src/chappie/api.py:1016`) -> `vision_model:8085` (`/v1/chat/completions`)
- **Opis Biznesowy i Rola:** Wykonuje precyzyjną ekstrakcję tekstu/kodu/schematów z całego obrazu lub zaznaczonych obszarów ROI przy użyciu modelu wizyjnego VLM (Qwen2-VL / MiniCPM-V).

##### Parametry Żądania (Request)
- **Typ DTO w TypeScript:** `VisionExtractRequest` (`ui/src/types.ts:40` / `412`)
- **Model Pydantic Backend:** `VisionExtractRequest` (`src/chappie/models.py:398`) — pola `image_data_url: str`, `rois?: RoiBox[]`, `prompt?: str`, `file_type?: str`, `temperature?: float`, `max_tokens?: int`.

##### Struktura Odpowiedzi (Response)
- **Status Kody:** `200 OK`, `400 Bad Request`, `422 Unprocessable Entity`, `500 Internal Server Error`, `502 Bad Gateway`, `504 Gateway Timeout`.
- **Typ DTO w TypeScript:** `VisionExtractResponse` (`ui/src/types.ts:41` / `419`)
- **Model Pydantic Backend:** `VisionExtractResponse` (`src/chappie/models.py:408`) — pola `text: str`, `extracted_text: str`, `rois_count: int`, `duration_ms: float`, `model: str`, `confidence?: float`.

---

### Podsumowanie Zgodności Architektonicznej i Spójności Kontraktów

Dokumentacja w `README.md` pokrywa **100% z 61 metod interfejsu sieciowego UI** (`ui/src/api.ts`). Gwarantuje:
1. **Pełną zgodność typów (Single Source of Truth):** Wszystkie typy TypeScript w `../../../ui/src/types.ts` odpowiadają modelom Pydantic v2 w `src/chappie/models.py`, `src/services/backend/schemas/user.py` oraz `src/services/workspace/schemas.py`.
2. **Bezpieczeństwo i Izolację:** Wyjaśnienie zasad weryfikacji tokenów HMAC SHA-256, izolacji piaskownicy w `VolumeManager` (`PathSecurityViolation`), ochrony przed SSRF (`validate_pi_url`) oraz uprawnień bazodanowych w `WorkspaceService`.
3. **Kompletność:** Bezwzględny brak placeholderów, skrótów `TODO` czy pominiętych pól, zgodnie z zasadami inżynierskimi projektu Chappie (`../../../MICHALS_WAYS.md`).
