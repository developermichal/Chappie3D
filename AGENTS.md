# AGENTS.md — Instrukcje i Wytyczne dla Agentów AI w Projekcie Chappie

## ⚠️ 1. KRYTYCZNA ZASADA #1: BEZWZGLĘDNA INTEGRALNOŚĆ KOMPILACJI UI I ZAKAZ NIEUŻYWANEGO KODU (TS6133 / DOCKER BUILD GATE)

> **ZASADA ZEROWEJ TOLERANCJI DLA NIEUŻYWANYCH DEKLARACJI W `ui/`:**  
> Każdy agent modyfikujący kod w katalogu `ui/` (`*.vue`, `*.ts`, `package.json`, konfiguracje) ma bezwzględny obowiązek zagwarantować, że kod kompiluje się w 100% czysto bez jakichkolwiek błędów TypeScript i martwego kodu.

### Przyczyna i Mechanizm Awarii
W pliku konfiguracyjnym `ui/tsconfig.json` włączone są restrykcyjne reguły weryfikacji typów:
- `"noUnusedLocals": true`
- `"noUnusedParameters": true`
- `"strict": true`

Etap budowania obrazu produkcyjnego w `ui/Dockerfile` (krok `RUN npm run build`) wykonuje skrypt:
```json
"build": "vue-tsc -b && vite build"
```
Pozostawienie **jakiejkolwiek** nieużywanej zmiennej, nieużywanej funkcji (np. błąd `TS6133: '<name>' is declared but its value is never read`), wiszącego importu lub niespójności typów powoduje natychmiastowe przerwanie budowania kontenera Docker z kodem wyjścia `1`:
```text
Dockerfile:6
--------------------
   4 |     RUN npm install
   5 |     COPY . .
   6 | >>> RUN npm run build
   7 |     
   8 |     FROM nginx:1.27-alpine
--------------------
target ui: failed to solve: process "/bin/sh -c npm run build" did not complete successfully: exit code: 1
```

### Obowiązkowa Procedura dla Każdego Agenta
1. **Zero Dead Code:** Przed zakończeniem pracy bezwzględnie usuń wszystkie nieużywane zmienne, parametry, funkcje pomocnicze i importy z `ui/src/`.
2. **Obowiązkowa Walidacja Kompilacji:** Po każdej edycji w `ui/` wykonaj lokalny build testowy (np. `npm run build` / `vue-tsc -b`) lub weryfikację obrazu `docker build -t chappie-ui ./ui`.
3. **Zakaz Maskowania Błędów:** Nie wolno wyłączać flag `noUnusedLocals`/`strict` w `tsconfig.json` ani stosować `as any` w celu obejścia weryfikacji.

---

## 2. Nadrzędne Zasady Inżynierskie (`MICHALS_WAYS.md`)

Dokument `MICHALS_WAYS.md` w katalogu głównym projektu stanowi **Single Source of Truth (SSOT)** dla wszelkich zasad operacyjnych, inżynierskich i architektonicznych w projekcie `chappie`.

### Kluczowe Zasady Operacyjne
- **Zasada 0 (Protokół Startowy):** Każda sesja robocza rozpoczyna się od weryfikacji stanu repozytorium i załadowania kontekstu.
- **Zasady 1 & 2 (Pamięć i Kontekst):** Zarządzanie stanem sesji w `sessions/` (klasa 1: `general.context`, klasa 2: `<nazwa>.context` i `current.context`).
- **Zasada 3 (Język):** Komunikacja z Michałem w języku polskim, kod i komentarze techniczne w języku angielskim.
- **Zasada 4 (Partnerstwo Senior-to-Senior):** Prawo i obowiązek konstruktywnej krytyki, zgłaszania ryzyk i propozycji optymalizacji.
- **Zasada 5 (Reużywalność):** Wykorzystywanie istniejącego kodu i oficjalnych bibliotek zamiast tworzenia zbędnych duplikatów.
- **Zasada 8 (Dyscyplina Środowiskowa):** Środowisko Windows, PowerShell, `uv run`, Docker Compose.
- **Zasada 9 (Zero Regresji):** Ochrona działających funkcji i stabilności całego systemu.
- **Zasada 12 (Kompletność Implementacji):** Brak placeholderów, TODO i niedokończonych fragmentów kodu.
- **Zasada 13 (Frontend Build Gate):** Uszczegółowienie Zasady #1 zdefiniowanej na początku niniejszego dokumentu.

---

## 3. Standardy Wykonawcze i Narzędziowe

- **Python & Backend:** Python 3.12, FastAPI, Pydantic v2, `uv` do zarządzania pakietami i uruchamiania środowiska (`uv run ...`).
- **Frontend:** Vue 3, Vite, TypeScript (`strict`), TailwindCSS, Docker multi-stage build z Nginx.
- **Baza Wektorowa i Pamięć:** ChromaDB, LangGraph, Sentence-Transformers.
- **Terminal i Środowisko:** Windows 11 PowerShell (ścieżki z backslashami `\`, cudzysłowy pojedyncze w argumentach narzędzi natywnych).

---

## 4. Architektura Dwóch Projektów (Chappie Server vs Terminal Client)

- **Repozytorium Serwera (Główne):** `C:\development\chappie` (FastAPI, orkiestrator LangGraph, endpoint `/ws/terminal`, `TerminalManager`, Docker Compose, UI).
- **Repozytorium Klienta Terminala:** `C:\development\terminal` (`../terminal`) — niezależna aplikacja CLI, REPL, instalator, sandboxing ścieżek, budowanie samodzielnych plików binarnych PyInstaller (`chappie.exe`).
- **Kontrakt WebSocket:** Endpoint `/ws/terminal` (port `7420` / `8002`) obsługuje komunikację z klientem na bazie modeli Pydantic (`TerminalHandshakeMessage`, `TerminalQueryMessage`, `TerminalResponseMessage`, `TerminalFileUpdate`).
- **Nienaruszalność Kontraktu:** Przy wszelkich zmianach po stronie serwera `/ws/terminal` bezwzględnie należy zachować wsteczną kompatybilność i uruchomić testy integracyjne po obu stronach (`tests/integration/test_terminal_endpoint_integration.py` w Chappie oraz `tests/integration/test_terminal_client_integration.py` w Terminalu).
