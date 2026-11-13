# STATE.md — Architektura, Topografia i Cykl Życia Kontekstu w Systemie Chappie

> **Wersja Dokumentu:** 1.0.0 (SSOT Context Architecture & Simulation)  
> **Data Utworzenia:** 2026-11-11  
> **Status:** Obowiązujący standard inżynierski projektu Chappie  
> **Główne Komponenty:** `src/chappie/agent.py`, `src/chappie/memory.py`, `src/chappie/projects.py`, `src/chappie/chappie_context.py`, `src/chappie/vision.py`, `src/chappie/terminal_manager.py`, `src/chappie/volume_manager.py`, `ui/src/App.vue`

---

## 1. Wstęp i Architektura Pamięci Kontekstowej Chappie

System Chappie implementuje **adaptacyjny, czterowarstwowy model zarządzania kontekstem i pamięcią**. Kontekst nie jest monolitycznym obiektem, lecz dynamicznym zbiorem struktur danych rozproszonych pomiędzy pamięć operacyjną (RAM), szybką warstwę **Redis Cache**, bazę wektorową (ChromaDB), bazę relacyjną (PostgreSQL/SQLite), dyskowe wolumeny trwałe (`volumes/`) oraz pamięć reaktywną frontendu Vue 3 / Pinia.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                WARSTWA FRONTENDU (UI / BROWSER)                         │
│  - App.vue (Reaktywne refs: task, result, liveChatMessages, capturedFrames, project)   │
│  - WebRTC / Screen Capture Buffer (HTMLVideoElement, MediaStream, base64 data URLs)    │
│  - LocalStorage / Cookie (Ustawienia motywu, tokeny sesji, język, aktywne widoki)      │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ REST API (JSON) / WebSocket (/ws/terminal)
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                    WARSTWA USŁUG DOMENOWYCH & ROUTINGU (FASTAPI)                       │
│  ├─ 1. LiveChatService: Strumieniowanie wideo, bufor klatek, OCR, dedykowany general ctx│
│  ├─ 2. ProjectService: .chappie SSOT, file mapping, rationale, terminal WebSocket     │
│  └─ 3. GeneralChatService: Czat sesyjny, pamięć tury, RAG ChromaDB, profile wiedzy     │
└──────────────────────┬────────────────────┬────────────────────┬───────────────────────┘
                       │                    │                    │
                       ▼                    ▼                    ▼
┌──────────────────────────────┐ ┌──────────────────────┐ ┌─────────────────────────────┐
│          REDIS CACHE         │ │   BAZA WEKTOROWA     │ │  WOLUMENY TRWAŁE (DYSK)      │
│  - chappie:project:{id}:ctx  │ │  (ChromaDB:8000)     │ │  - <root_path>/.chappie SSOT │
│    (Tylko aktywny kontekst)  │ │  - Kolekcje profili  │ │  - volumes/live-chat/        │
│  - chappie:livechat:general  │ │  - Embeddings CUDA   │ │  - volumes/sessions/         │
│    (Trwała persystencja AOF) │ │                      │ │  - volumes/projects/         │
│  - chappie:session:{id}:state│ │                      │ │  - data/projects.json        │
└──────────────────────────────┘ └──────────────────────┘ └─────────────────────────────┘
```

### 1.1. Podstawowe Wskaźniki i Stałe Budżetowania Tokenów

1. **Maksymalne Okno Kontekstowe Modelu (`max_context_tokens`):** Domyślnie **28 000 tokenów** (dla okna 32k/128k LLM z zapasem na generację odpowiedzi).
2. **Łączny Budżet Promptu i Systemu:** Maksymalnie **60% okna kontekstowego** (`28000 * 0.60 = 16 800 tokenów`).
3. **Maksymalny Budżet Monitu Systemowego:** `min(raw_cost, 20% okna)` = **5 600 tokenów**.
4. **Bramka Pamięci RAM (`memory_offload_ram_threshold_pct`):** **80.0%**. Ewaluowana jednokrotnie przy starcie sesji (`init_session`):
   - **RAM <= 80%:** Sesja działa w **100% trybie RAM (Zero I/O)**.
   - **RAM > 80%:** Automatyczna aktywacja **Disk Spill** do wolumenu `volumes/sessions/{session_id}.json`.
5. **Tokenizator:** BPE `cl100k_base` (`tiktoken`) z matematycznie bezpiecznym fallbackiem na liczbę bajtów UTF-8 (`len(text.encode('utf-8'))`).

---

## 2. Topografia Przechowywania Kontekstu (Komponent po Komponencie)

| Komponent / Moduł | Lokalizacja Fizyczna | Struktura Danych / Typ | Szacunkowy Rozmiar Pamięci | Trwałość |
| :--- | :--- | :--- | :--- | :--- |
| **Agent Sessions** | Backend RAM (`CodingAgent._sessions`) | `dict[str, list[BaseMessage]]` | ~2–15 KB / sesję | Do restartu API / `clearSession` |
| **Session Turns** | Backend RAM (`CodingAgent._session_turns`) | `dict[str, list[SessionTurnMessage]]` | ~5–50 KB / sesję | Do restartu API / `clearSession` |
| **Disk Spill Snapshots** | Dysk (`volumes/sessions/*.json`) | `SessionSnapshot` (JSON) | ~10–500 KB / plik | Trwała (Dysk/Wolumen) |
| **Project Storage** | Dysk & RAM (`data/projects.json`) | `list[ProjectContext]` (JSON) | ~20–500 KB | Trwała (Dysk) |
| **Project Sandbox SSOT** | Dysk (`<project_root>/.chappie`) | Markdown / Plaintext | ~2–50 KB / projekt | Trwała (Dysk/Git) |
| **Terminal Workers** | Backend RAM (`TerminalManager._workers`) | `mp.Process` + `CodingAgent` (RSS) | ~80–150 MB / worker | Do rozłączenia klienta |
| **Live Chat Contexts** | Dysk (`volumes/contexts/live_chats/*.json`) | `LiveChatContext` (JSON) | ~10–200 KB / czat | Trwała (Dysk/Wolumen) |
| **General Contexts** | Dysk (`volumes/contexts/general/*.json`) | `GeneralContext` (Commits JSON) | ~5–100 KB / problem | Trwała (Dysk/Wolumen) |
| **Wektory ChromaDB** | Dysk & RAM (`volumes/chroma/`) | Parquet / HNSW Index / SQLite | ~50 MB – 2 GB | Trwała (Chroma) |
| **Frontend UI State** | Browser RAM (Vue 3 / `App.vue`) | Reaktywne `ref<T>` / Pinia | ~5–30 MB | Do odświeżenia karty |
| **Video/Screen Buffer** | Browser RAM (`capturedFrames`) | `Array<CapturedFrame>` (base64) | **~1.5–3.5 MB / klatkę** | Do wyczyszczenia / F5 |

---

## 3. Cykl Życia Kontekstu w Poszczególnych Stanach Systemu

### Stan 1: Po Uruchomieniu (Cold Start / Post-Initialization)

**Opis Stanu:** Serwer FastAPI, baza ChromaDB i frontend Nginx zostały uruchomione. Brak aktywnych sesji użytkownika, żaden projekt nie jest wczytany do pamięci roboczej agenta.

```
[Start Serwera] ──> Ładowanie Settings (config.py)
                 ──> Inicjalizacja KnowledgeBase & Chroma Client
                 ──> Odczyt metadanych projektów z data/projects.json (RAM: ~50 KB)
                 ──> Brak aktywnych sesji w CodingAgent._sessions (0 bajtów)
                 ──> Frontend ładuje App.vue (sessionId: null, result: null)
```

1. **Gdzie znajduje się kontekst:**
   - **Backend RAM:** Metadane zarejestrowanych projektów wczytane z `data/projects.json` do słownika `ProjectStorage._load_data()`. Słowniki sesji agenta (`_sessions`, `_session_turns`, `_session_ram_usage`) są puste (`len == 0`).
   - **ChromaDB:** Indeksy HNSW na dysku w `volumes/chroma/`, zainicjalizowany klient połączenia.
   - **Bazy SQLite:** Otwarte pule połączeń do `data/workspace.db` i `data/users.db`.
   - **Frontend Browser:** Zainicjalizowany obiekt `App.vue`, `sessionId.value = null`, `task.value = ""`, `selectedProject.value = null`.
2. **Ile zajmuje kontekst:**
   - **Pamięć RAM Backendu:** ~80–120 MB (narzut interpretera Pythona, FastAPI, PyTorch/Embedder w tle). Sam kontekst użytkownika: **0 bajtów**.
   - **Zużycie Tokenów LLM:** **0 tokenów**.

---

### Stan 2: Czat Ogólny z Agentem (Single / Multi-Turn bez Projektu)

**Opis Stanu:** Użytkownik wysyła zapytania techniczne w widoku `chat` bez wybranego projektu (`project_id = null`).

```
[User Prompt] ──> POST /api/tasks (AgentTaskRequest)
              ──> agent.init_session(session_id) ──> Sprawdzenie RAM (19.2% <= 80% -> RAM Mode)
              ──> LangGraph Graph:
                   1. retrieve: Przeszukanie wiedzy ogólnej ChromaDB (opcjonalnie)
                   2. web_search / pypi_lookup (jeśli zapytanie dotyczy bibliotek)
                   3. generate: Złożenie promptu przez _assemble_budgeted_prompt
                   4. validate: Sprawdzenie formatu JSON Pydantic
              ──> Zapis tury w CodingAgent._session_turns[session_id]
              ──> Odesłanie odpowiedzi do UI (AgentResponse)
```

1. **Gdzie znajduje się kontekst:**
   - **Backend RAM:**
     - `self._sessions[session_id]`: Lista `[HumanMessage, AIMessage, ...]`.
     - `self._session_turns[session_id]`: Lista obiektów `SessionTurnMessage` zawierających `user_task`, `assistant_answer`, `generated_files`, `total_tokens`.
   - **Dysk / Wolumeny:**
     - Jeśli RAM na starcie sesji wynosił `<= 80%`: **0 operacji dyskowych** (czysty RAM).
     - Jeśli RAM wynosił `> 80%`: Każda tura wykonuje zrzut `SessionSnapshot` do `volumes/sessions/{session_id}.json`.
   - **Frontend RAM:** Obiekt `result.value` przechowuje ostatnią odpowiedź, pliki wygenerowane oraz statystyki tokenów.
2. **Ile zajmuje kontekst i zużycie tokenów:**
   - **RAM:** ~4 KB na turę (tekst promptu + odpowiedzi).
   - **Tokeny LLM per Tura:**
     - System Prompt bazowy: **~180 tokenów**.
     - Treść zadania użytkownika: np. **50–300 tokenów**.
     - Historia dialogu (poprzednie tury): Budżetowana dynamicznie (do 16 800 tokenów).
     - Wyniki RAG / WebSearch / PyPI: **0–2 500 tokenów**.
     - Łączne wejście LLM (Prompt Tokens): **~400–4 000 tokenów / zapytanie**.

---

### Stan 3: Po Wgraniu / Wybraniu Projektu (Project Active Ingestion)

**Opis Stanu:** Użytkownik otwiera lub rejestruje katalog roboczy projektu (np. `C:\development\chappie` lub wolumen `projects/my-app`).

```
[Wybór Projektu] ──> projects.open_directory_project(root_path)
                 ──> ensure_chappie_context: Odczyt lub utworzenie .chappie na dysku
                 ──> generate_project_tree_summary: Zbudowanie drzewa katalogów (z filtrem ignore)
                 ──> Utworzenie / synchronizacja wpisu w data/projects.json
[Zadanie Agenta] ──> _assemble_budgeted_prompt:
                      * Wstrzyknięcie Project Sandbox Directives
                      * Wstrzyknięcie zawartości .chappie (architektura, reguły)
                      * Wstrzyknięcie drzewa katalogów projektu
                      * Wstrzyknięcie historii tur sesji powiązanych z tym projektem
                 ──> Auto-Write: Zapis plików wygenerowanych wprost na dysk w root_path
                 ──> projects.append_session_turn_to_project: Aktualizacja .chappie i projects.json
```

1. **Gdzie znajduje się kontekst:**
   - **Dysk (Katalog Projektu):** Plik `.chappie` stanowi **Single Source of Truth (SSOT)** dla architektury, reguł i historii zmian. Wygenerowane pliki trafiają natychmiast do drzewa katalogów projektu na dysku.
   - **Dysk (`data/projects.json`):** Obiekt `ProjectContext` (id, name, root_path, git_config, context_document, summary, session_turns).
   - **Backend RAM:** Aktywny `ProjectContext` przekazywany do stanu grafu `AgentState["project_context"]`.
   - **Frontend RAM:** `selectedProject.value`, `contextDocDraft.value` w `App.vue`.
2. **Ile zajmuje kontekst i budżet tokenów:**
   - **Rozmiar `.chappie`:** Średnio **2–25 KB** (~500–6 000 tokenów).
   - **Rozmiar Drzewa Projektu:** Po odfiltrowaniu `node_modules`, `.venv`, `.git` — średnio **1–8 KB** (~250–2 000 tokenów).
   - **Budżetowanie w Prompcie (`_assemble_budgeted_prompt`):**
     - Projekt otrzymuje dedykowaną część z łącznej puli 16 800 tokenów promptu.
     - Jeśli dokument `.chappie` + drzewo przekraczają dozwolony budżet, następuje inteligentne ucięcie z dopiskiem `[... Project architecture truncated to fit context budget ...]`.

---

### Stan 4: Przed Wgraniem Projektu, ale po Zmianie Ekranu na Video Chat / Multimodal

**Opis Stanu:** Użytkownik przełącza się na zakładkę `live-chat`, aktywuje kamerę Raspberry Pi lub udostępnianie ekranu (WebRTC Screen Capture), ale nie wybrał jeszcze projektu.

```
[Screen Share / Pi Cam] ──> Frontend: Przechwycenie klatki (1920x1080 JPEG)
                         ──> Konwersja do Base64 Data URL (~2.5 MB)
                         ──> capturedFrames.value.push(frame) -> Browser Heap: +2.5 MB / klatkę
[Wysłanie Zapytania]    ──> POST /api/live-chat/tasks (LiveChatTaskRequest)
                         ──> VisionModelClient.extract_text(image_data_url, rois)
                              1. Wycinek ROI / Obraz Full HD -> Wywołanie Modelu VLM
                              2. Ekstrakcja tekstu OCR i opisu sceny
                         ──> Utworzenie LiveChatMessage(extracted_text=OCR_TEXT, has_image=True)
                         ──> UWAGA: Surowy obraz Base64 ZOSTACIE ODCINITY z historii dialogu!
                         ──> LLM otrzymuje wyłącznie czysty tekst OCR + treść zapytania.
```

1. **Gdzie znajduje się kontekst:**
   - **Frontend Browser RAM (KRYTYCZNE ZUŻYCIE):**
     - Obiekty `capturedFrames`: Tablica zawierająca ciągi znaków `data:image/jpeg;base64,...`.
     - Strumień `screenStream` (`MediaStream`) / element `<video>` w pamięci GPU i pamięci karty przeglądarki.
   - **Pamięć Transmisji HTTP (FastAPI):** Obraz przesyłany jednorazowo w ciele żądania POST (`image_data_url`).
   - **Backend RAM:**
     - Obraz istnieje **wyłącznie ulotnie** w czasie trwania metody `VisionModelClient.extract_text()`.
     - Do historii `LiveChatMessage` trafia **wyłącznie wyekstrahowany tekst OCR** (`extracted_text`), a pole `has_image` ustawiane jest na `True`.
   - **Dysk (`volumes/contexts/live_chats/`):** Zapisywany tylko przy `save_context=True`. Funkcja `_clean_data_urls` bezwzględnie wycina wszelkie wiszące ciągi `data:image/` przed zapisem JSON.
2. **Ile zajmuje kontekst i zużycie tokenów:**
   - **Pamięć RAM Przeglądarki:** **~2.5 MB na każdą przechwyconą klatkę** (10 klatek w historii podglądu = ~25 MB RAM).
   - **Pamięć RAM Backendu:** Ulotny piksel-bufor PIL Image (~6–8 MB) zwalniany natychmiast po wykonaniu OCR przez Garbage Collector.
   - **Tokeny LLM dla Wyekstrahowanego Obrazu:** Zamiast dziesiątek tysięcy tokenów multimodalnych surowego obrazu, tekst OCR zajmuje zaledwie **150–1 500 tokenów** w oknie kontekstowym!

---

### Stan 5: Bez Wgranego Projektu i przy Zmianie Ekranu / Zakładek w UI

**Opis Stanu:** Użytkownik nawiguje pomiędzy zakładkami (`chat` -> `live-chat` -> `books` -> `database` -> `logs` -> `settings`) bez wczytanego projektu.

```
[Nawigacja UI: currentNav] ──> Przełączenie widoku (v-if / v-show)
                           ──> Stan komponentu App.vue NIE jest niszczony:
                                * task.value, result.value pozostają w pamięci RAM Vue
                                * liveChatMessages.value pozostają w pamięci RAM Vue
                                * capturedFrames.value pozostają w pamięci RAM Vue
                                * screenStream działa w tle (jeśli nie wyłączono przyciskiem)
                           ──> Backend: Całkowicie bezstanowy wobec nawigacji UI (REST/WS)
```

1. **Gdzie znajduje się kontekst:**
   - **Frontend RAM:** Wszystkie dane formularzy, historia zapytań i podglądy klatek pozostają w pamięci karty przeglądarki.
   - **Backend RAM:** Backend nie rejestruje faktu zmiany zakładki w UI. Sesje w `CodingAgent._sessions` oczekują na kolejne zapytania HTTP/WS.
2. **Ile zajmuje kontekst:**
   - **RAM Przeglądarki:** ~15–40 MB (zależnie od liczby zrzutów ekranu w `capturedFrames`).
   - **RAM Backendu:** Baza spoczynkowa (~120 MB).

---

### Stan 6: Zamknięcie Projektu i Przełączenie na Inny Projekt

**Opis Stanu:** Użytkownik kończy pracę nad projektem A (`Project_A`) i przełącza się w interfejsie lub terminalu na projekt B (`Project_B`).

```
[Przełączenie Projektu] ──> selectProjectForContext(Project_B)
                        ──> Zapis ewentualnych zmian w Project_A (.chappie zsynchronizowany na dysku)
                        ──> selectedProject.value = Project_B
                        ──> contextDocDraft.value = Project_B.context_document
                        ──> Wygenerowanie nowego sessionId (lub powiązanie sesji z Project_B.id)
                        ──> Backend:
                             * _assemble_budgeted_prompt ładuje wyłącznie .chappie z Project_B
                             * Drzewo katalogów generowane jest wyłącznie dla Project_B.root_path
                             * Historia tur filtrowana: target_project_id == Project_B.id
                        ──> 100% Gwarancja Izolacji Sandboksa (Zero wycieku kontekstu)
```

1. **Gdzie znajduje się kontekst:**
   - **Projekt A:** Zrzucany do `data/projects.json` oraz pliku `<Project_A_root>/.chappie`. Żadne dane Projektu A nie są wstrzykiwane do promptu Projektu B.
   - **Projekt B:** Wczytywany z dysku, staje się aktywnym sandboksem agenta.
2. **Mechanizm Zabezpieczenia Przed Wyciekiem Kontekstu:**
   - Agent w węźle `validate` oraz `_assemble_budgeted_prompt` weryfikuje `project_context.id` oraz `project_context.root_path`.
   - Zasada sandboksa: Próba odwołania do ścieżek z poprzedniego projektu podnosi błąd `Security Violation: Proposed file attempts to escape project root sandbox`.

---

### Stan 7: Sesje Terminala WebSocket (`/ws/terminal`)

**Opis Stanu:** Klient CLI `chappie-terminal` (`C:\development\terminal`) łączy się przez protokół WebSocket z serwerem Chappie.

```
[chappie CLI] ──> WebSocket Connection: /ws/terminal (Port 7420 / 8002)
              ──> TerminalHandshakeMessage (session_id, project_name, project_root, chappie_context)
              ──> TerminalManager:
                   * Uruchomienie dedykowanego procesu potomnego: mp.Process(_worker_entrypoint)
                   * Utworzenie kanału IPC (Pipe): parent_conn <──> child_conn
                   * Proces potomny inicjalizuje niezależną instancję CodingAgent w izolowanej pamięci
              ──> Zapytanie CLI (TerminalQueryMessage):
                   * Worker wykonuje agent.run()
                   * Strumieniowanie tokenów rozumowania i odpowiedzi (TerminalTokenStream)
                   * Zwrócenie zmodyfikowanych plików (TerminalFileUpdate) z sandboxingiem validate_safe_path
              ──> Rozłączenie CLI:
                   * TerminalManager.cleanup_session() natychmiast zabija proces potomny (PID)
```

1. **Gdzie znajduje się kontekst:**
   - **Izolowana Pamięć Procesu Potomnego (OS Address Space):** Każda sesja terminala posiada własny proces Pythona o unikalnym PID. Wszelkie obiekty stanu, drzewo projektu i historia sesji żyją w pamięci tego procesu.
   - **Dysk (`<project_root>/.chappie`):** Klient CLI synchronizuje historię zmian bezpośrednio w lokalnym pliku `.chappie`.
2. **Zużycie Zasobów:**
   - **Pamięć RAM Serwera:** **~80–150 MB RSS na każdą aktywną sesję terminala**.
   - **Zwolnienie Pamięci:** Z chwilą zamknięcia połączenia WebSocket proces potomny jest natychmiast terminowany przez `TerminalManager.cleanup_session()`, co zwraca 100% zaalokowanej pamięci RAM do systemu operacyjnego.

---

## 4. Pełne Symulacje Numeryczne i Scenariusze Zużycia Zasobów

Poniższe symulacje przedstawiają zachowanie systemu w warunkach granicznych i stresowych, poparte dokładnymi obliczeniami zużycia pamięci i tokenów.

---

### Symulacja A: Długa Konwersacja Wieloturowa (1 do 120 Tur)

*Parametry:* Sesja czatu kodowania, średnio 150 tokenów prompt użytkownika, 450 tokenów odpowiedź asystenta (łącznie **~600 tokenów na turę**). Okno modelu: 28 000 tokenów.

```
Liczba Tur │ Łączne Surowe Tokeny │ Stan Bufora Dialogu              │ Akcja Algorytmu Budżetowania                  │ RAM / Dysk Mode
───────────┼──────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────┼─────────────────
Tura 1–10  │ ~6 000 tokenów       │ Wszystkie 10 tur w oknie         │ Brak ucinania (mieści się w budżecie 16.8k)   │ Czysty RAM (lub Disk)
Tura 20    │ ~12 000 tokenów      │ Wszystkie 20 tur w oknie         │ Brak ucinania                                 │ Czysty RAM (lub Disk)
Tura 28    │ ~16 800 tokenów      │ Granica budżetu promptu (60%)    │ Punkt krytyczny budżetu historii              │ Czysty RAM (lub Disk)
Tura 30    │ ~18 000 tokenów      │ Przekroczenie budżetu            │ Aktywacja _summarize_archived_turns:          │ Czysty RAM (lub Disk)
           │                      │                                  │ - Tury 1–12: zrzut do streszczenia (~250 tok) │
           │                      │                                  │ - Tury 13–30: pełna treść verbatim (~10k tok)│
Tura 60    │ ~36 000 tokenów      │ 2x przekroczenie okna 28k        │ - Tury 1–48: streszczenie semantyczne (~300 tok)│ Czysty RAM (lub Disk)
           │                      │                                  │ - Tury 49–60: pełna treść verbatim (~7.2k tok)│
Tura 120   │ ~72 000 tokenów      │ 4.3x przekroczenie okna 28k      │ - Tury 1–108: streszczenie semantyczne (~350 tok)│ Czysty RAM (lub Disk)
           │                      │                                  │ - Tury 109–120: pełna treść verbatim (~7.2k tok)│
```

#### Wnioski z Symulacji A:
1. **Stabilność Okna LLM:** Dzięki funkcji `_build_budgeted_messages_from_turns` system **nigdy nie przepełnia okna kontekstowego modelu** (zawsze <= 16 800 tokenów promptu).
2. **Zachowanie Pełnej Historii:** Mimo streszczania w prompcie LLM, pełny transkrypcja 120 tur jest zachowywana w całości w `CodingAgent._session_turns` (w RAM) oraz w `volumes/sessions/{session_id}.json` (na dysku).
3. **Pamięć RAM dla 120 tur:** ~720 KB (tekst struktur Pydantic). Znikome obciążenie dla serwera.

---

### Symulacja B: Wielkie Monorepo (50 000 Plików, 100 MB Kodu)

*Parametry:* Projekt z rozbudowaną strukturą `node_modules`, `.git`, `.venv`, plikami binarnymi i 5 000 plików źródłowych Pythona/TypeScript.

```
Etap Analizy Projektu                      │ Surowy Rozmiar      │ Po Przefiltrowaniu (chappie_context) │ Tokeny w Prompcie LLM
───────────────────────────────────────────┼─────────────────────┼──────────────────────────────────────┼───────────────────────
1. Skanowanie katalogu                     │ 50 000 plików       │ 5 000 plików (odrzucono 90% śmieci) │ -
2. Generowanie ASCII Tree                  │ ~1.2 MB tekstu      │ Skrócone drzewo (max 2 poziomy / lim)│ ~2 500 tokenów
3. Wczytanie pliku .chappie                │ 35 KB Markdown      │ 35 KB (pełna architektura)           │ ~8 500 tokenów
4. Złożenie promptu (_assemble_budgeted)   │ Wymagane: 11 000 tok│ Mieści się w budżecie projektu       │ 11 000 tokenów
```

#### Przypadek Skrajny (Drzewo > 20 000 tokenów):
Jeśli sam plik `.chappie` oraz drzewo katalogów przekraczają 16 800 tokenów:
- Algorytm `_truncate_to_token_budget_with_cost` automatycznie obcina drzewo plików i sekcję architektury do limitu budżetowego.
- Zwracany jest znacznik `[... Project status & notes truncated to fit context budget ...]`.
- **System zapobiega błędowi `InvalidRequestError: Context window exceeded` po stronie API LLM.**

---

### Symulacja C: Intensywny Video Chat i Screen Sharing (100 Klatek Full HD)

*Parametry:* Operator prowadzi sesję analizy interfejsu i logów przez 10 minut. Wykonano 100 zrzutów ekranu w rozdzielczości 1920x1080 (JPEG Base64).

```
Element Systemu                      │ Zużycie Zasobów bez Optymalizacji │ Zużycie Zasobów w Systemie Chappie
─────────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────
Pamięć RAM Przeglądarki (Frontend)   │ 100 * 2.5 MB = 250 MB Heap        │ 250 MB (w capturedFrames w UI)
Payload Transmisji POST /api/live... │ 2.5 MB / żądanie                  │ 2.5 MB / żądanie (pojedynczy frame)
Pamięć RAM Backendu (FastAPI)        │ ~250 MB (gdyby trzymano obrazy)   │ ~8 MB ulotnie (tylko w trakcie OCR)
Historia Czatów na Dysku (JSON)      │ 100 * 2.5 MB = 250 MB JSON        │ **~150 KB JSON (dzięki _clean_data_urls)**
Tokeny Wejściowe do LLM              │ 100 * 1 500 tok = 150 000 tokenów │ **~1 200 tokenów (czysty tekst OCR)**
```

#### Wnioski z Symulacji C:
1. **Ochrona Tokenów i Dysku:** Dzięki filtrowi `_clean_data_urls` oraz zamianie obrazu na tekst OCR przed dodaniem do `LiveChatMessage`, wielomegowe payloady base64 **nie zatruwają historii konwersacji ani wolumenów dyskowych**.
2. **Wąskie Gardło:** Jedynym miejscem akumulacji pamięci jest tablica `capturedFrames` w pamięci RAM przeglądarki (zalecane czyszczenie bufora podglądu w UI po zakończeniu sesji).

---

### Symulacja D: Równoległe Sesje Terminalowe (10 Jednoczesnych Deweloperów)

*Parametry:* 10 niezależnych klientów `chappie-terminal` podłączonych równocześnie do `/ws/terminal`.

```
Metryka Systemowa                    │ Wartość dla 1 Sesji CLI │ Wartość dla 10 Sesji CLI │ Limit / Próg Bezpieczeństwa
─────────────────────────────────────┼─────────────────────────┼──────────────────────────┼─────────────────────────────
Liczba Procesów Potomnych (Workers)  │ 1 proces                │ 10 procesów              │ Zależne od rdzeni CPU
Zużycie Pamięci RAM (RSS)            │ ~110 MB                 │ ~1.1 GB RAM              │ 80% RAM Gate (~52 GB)
Aktywne Uchwyty IPC Pipe             │ 1 para                  │ 10 par                   │ Max File Descriptors OS
Izolacja Sandboksa                   │ 100%                    │ 100% (niezależne PIDy)   │ Brak kolizji pamięci
```

---

## 5. Analiza Punktów Krytycznych, Błędów Estymacji i Ryzyk Przepełnienia

Poniższa sekcja dokumentuje precyzyjnie momenty, w których może dojść do problemów z wyliczeniem pamięci lub tokenów oraz zastosowane mechanizmy obronne.

### 5.1. Błąd Szacowania Tokenów (BPE Tokenizer vs Długość Znakowa)

*Problem:* Naiwne szacowanie tokenów regułą `len(text) // 4` zawodzi dramatycznie dla:
- Tekstów w języku polskim z polskimi znakami diakrytycznymi (`ą, ę, ś, ć, ż, ź, ó, ł, ń` w UTF-8 zajmują 2 bajty).
- Kodu źródłowego, JSON, wcięć tabulacji i ciągów liczb wielocyfrowych (gdzie 1 token = 1-2 znaki).
*Rozwiązanie w Chappie (`src/chappie/memory.py`):*
1. Główny tokenizator: BPE `tiktoken` z modelem `cl100k_base`.
2. Fallback przy braku tiktokena: Funkcja `_estimate_tokens_fallback(text)` wykorzystuje **liczbę bajtów UTF-8** (`len(text.encode('utf-8'))`).
   - W każdym tokenizatorze BPE (GPT-4, Qwen, Llama) 1 token zużywa **co najmniej 1 bajt UTF-8**.
   - Liczba bajtów UTF-8 jest **matematycznie udowodnionym górnym ograniczeniem (Upper Bound)**, co gwarantuje, że model nigdy nie otrzyma więcej tokenów niż oszacowano.

### 5.2. Duplikacja Stanu w Grafie LangGraph (`AgentState`)

*Problem:* W orkiestratorze LangGraph stan `AgentState` jest przekazywany sekwencyjnie pomiędzy węzłami (`retrieve -> web_search -> pypi_lookup -> generate -> validate`).
- Jeśli stan zawiera wielomegowe obiekty (np. surowe dokumenty RAG lub obrazy), kopiowanie słownika stanu w pamięci Pythona prowadzi do gwałtownego wzrostu zużycia RAM.
*Rozwiązanie w Chappie:*
- W węźle `retrieve` pobierane jest ściśle określone `top_k` (domyślnie 4–6 chunków o łącznej objętości < 1 500 tokenów).
- Dane binarne obrazów nigdy nie są umieszczane w `AgentState`.

### 5.3. Wyciek Pamięci w Przeglądarce przy Zrzutach Ekranu (Vue Memory Leak)

*Problem:* W `App.vue` każda przechwycona klatka z kamery lub ekranu dodawana jest do tablicy `capturedFrames.value.push(frame)`.
- Przy długiej sesji (np. 300 klatek) pamięć samej karty przeglądarki osiąga **> 750 MB RAM**, co może doprowadzić do zawieszenia karty (błąd `Out of Memory` przeglądarki).
*Zalecenie Inżynierskie:*
- Stosowanie rotacji bufora FIFO w UI (np. maksymalnie 20 ostatnich klatek w podglądzie) oraz zwalnianie strumienia `screenStream.getTracks().forEach(t => t.stop())` przy opuszczaniu widoku `live-chat`.

### 5.4. Przekroczenie Limitu Payloadu HTTP (FastAPI Request Body)

*Problem:* Przesłanie nieskompresowanego zrzutu ekranu w rozdzielczości 4K w formacie Base64 PNG może przekroczyć **15–20 MB** na pojedyncze żądanie HTTP, blokując pętlę zdarzeń ASGI FastAPI.
*Rozwiązanie w Chappie:*
- Frontend w `App.vue` kompresuje zrzuty do formatu JPEG o jakości 0.85, ograniczając wagę pojedynczej klatki do maksymalnie **1.5–2.5 MB**.
- Moduł `vision.py` weryfikuje nagłówki i rozmiar bufora przed przekazaniem do modelu VLM.

---

## 6. Macierz Podsumowująca Stanów i Pamięci

| Stan Systemu | Główna Lokalizacja Kontekstu | Format Danych | Średni Rozmiar RAM | Średni Rozmiar Dysku | Zużycie Tokenów LLM (Wejście) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Po Uruchomieniu** | Backend RAM / Chroma / SQLite | Obiekty Pythona / SQLite | ~120 MB (baza) | ~50 MB (Chroma) | 0 tokenów |
| **2. Czat Ogólny** | Backend RAM (`_sessions`, `_session_turns`) | `BaseMessage` / Pydantic | +10–100 KB | 0 B (RAM mode) / ~50 KB (Spill) | 400 – 4 000 tokenów |
| **3. Po Wgraniu Projektu** | Dysk (`.chappie`) + `projects.json` | Markdown + JSON | +20–200 KB | +10–100 KB | 2 500 – 16 800 tokenów (capped) |
| **4. Video Chat / Multimodal** | Browser RAM (`capturedFrames`) + OCR | Base64 (UI) / Tekst (API) | +25–200 MB (UI) / ~8 MB (API) | ~100 KB (JSON) | +150 – 1 500 tokenów (OCR) |
| **5. Zmiana Ekranu w UI** | Browser RAM (Vue ref-y) | Obiekty JS / Reactive | B/Z (trzymane w UI) | B/Z | 0 tokenów (brak requestu) |
| **6. Przełączenie Projektu** | Zrzut starego na dysk, ładowanie nowego | `.chappie` + `projects.json` | Zwolnienie starego / +nowy | Trwały zapis obu | Zależne od nowego `.chappie` |
| **7. Sesja Terminala WS** | Pamięć procesu potomnego (`mp.Process`) | Izolowany Python Worker | +80–150 MB / worker | Synchronizacja `.chappie` | Dynamiczne (budżetowane) |

---

## 7. Podsumowanie i Gwarancje Architektoniczne

1. **Gwarancja Nienaruszalności Okna LLM:** Niezależnie od liczby tur dialogu (nawet 1 000+) i wielkości projektu (nawet 100 000 plików), algorytmy budżetowania tokenów (`_assemble_budgeted_prompt`, `_build_budgeted_messages_from_turns`) **zawsze bezwzględnie przycinają monit do zadanego progu (maksymalnie 60% okna, czyli 16 800 tokenów)**.
2. **Gwarancja Integralności Dyskowej:** Dzięki filtrowi `_clean_data_urls` wielomegowe payloady base64 z kamer i zrzutów ekranu nigdy nie trafiają do trwałych transkryptów sesji ani streszczeń.
3. **Gwarancja Bezpieczeństwa Sandboksa:** Kontekst aktywnego projektu jest ściśle ograniczony do `project_root`. Zmiana projektu natychmiast odcina stary kontekst, uniemożliwiając wyciek danych pomiędzy niezależnymi repozytoriami.
4. **Wydajność Hybrydowa (RAM / Disk):** Jednokrotne sprawdzenie obciążenia RAM przy starcie sesji zapewnia maksymalną prędkość (Zero I/O na dysku) przy standardowym obciążeniu oraz niezawodny zrzut na wolumen `sessions/` przy obciążeniu powyżej 80%.
