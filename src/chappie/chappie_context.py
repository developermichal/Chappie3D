"""Module for indexing local project directories, managing the .chappie context file,
and tracking project changes for the coding agent.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Third-party packages, build artifacts, environments, and cache directories to ignore
IGNORED_DIRECTORIES = {
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
    "dist",
    "build",
    "target",
    "out",
    "bin",
    "obj",
    "vendor",
    "packages",
    "site-packages",
    "bower_components",
    ".idea",
    ".vscode",
    ".tox",
    "tmp",
    "temp",
    ".cache",
    "CMakeFiles",
    ".pio",
    ".platformio",
    ".pioenvs",
}

IGNORED_EXTENSIONS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".pyc",
    ".pyo",
    ".pyd",
    ".wasm",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
    ".lock",
    ".sqlite",
    ".db",
    ".pkl",
    ".parquet",
    ".weights",
    ".onnx",
    ".pt",
    ".pth",
    ".o",
    ".a",
    ".obj",
    ".elf",
    ".hex",
    ".uf2",
    ".map",
    ".d",
    ".gch",
}

LANGUAGE_EXTENSION_MAP = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (React/Vue)",
    ".js": "JavaScript",
    ".jsx": "JavaScript (React)",
    ".vue": "Vue",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".c": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".s": "Assembly",
    ".asm": "Assembly",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".sql": "SQL",
    ".sh": "Shell Script",
    ".bash": "Bash Script",
    ".ps1": "PowerShell",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".md": "Markdown",
    ".scad": "OpenSCAD",
    ".cmake": "CMake",
    ".ini": "Config/PlatformIO",
}


def infer_file_role(rel_path: str, abs_path: Path | None = None) -> str:
    """Infers the architectural and semantic role of a file in the project.

    Returns a concise summary string explaining what the file does and its subsystem.
    """
    norm_path = rel_path.replace("\\", "/").lower()
    filename = norm_path.split("/")[-1]

    # Specific manifests, configurations and build files
    if filename == "pyproject.toml":
        return "Python project manifest, dependencies & tool configuration"
    if filename == "package.json":
        return "Node.js dependencies and script definitions"
    if filename in {"compose.yaml", "docker-compose.yml", "docker-compose.yaml"}:
        return "Docker Compose multi-service architecture"
    if filename.startswith("dockerfile"):
        return "Docker container build recipe"
    if filename == "cmakelists.txt":
        return "CMake build configuration and compilation targets"
    if filename == "makefile":
        return "Make build rules and targets"
    if filename in {"alembic.ini", "env.py"}:
        return "Database migration configuration"
    if filename == "tsconfig.json":
        return "TypeScript compiler configuration & strict type checks"
    if filename in {"vite.config.ts", "vite.config.js"}:
        return "Vite bundler and dev server configuration"
    if filename in {"tailwind.config.js", "tailwind.config.ts"}:
        return "TailwindCSS styling and theme tokens"
    if filename in {"readme.md", "state.md", "michals_ways.md", "agents.md", "api.md"}:
        return "System documentation and engineering guidelines"

    # Directory/module-based heuristics
    if "routes/" in norm_path or "api/" in norm_path or filename.endswith("api.py"):
        return "FastAPI REST endpoints and routing"
    if "models" in norm_path or filename.endswith("model.py") or filename.endswith("models.py"):
        return "Pydantic data models & type schemas"
    if "agent" in norm_path:
        return "AI agent logic, prompts and orchestration"
    if "vision" in norm_path or "camera" in norm_path:
        return "Vision processing, camera stream and OCR"
    if "redis" in norm_path or "cache" in norm_path:
        return "Redis cache layer and context synchronization"
    if "terminal" in norm_path:
        return "Terminal session & process management"
    if "projects" in norm_path or "chappie_context" in norm_path:
        return "Project context, SSOT metadata and storage"
    if "volume" in norm_path:
        return "Volume management & sandbox boundaries"
    if "tests/" in norm_path or filename.startswith("test_") or filename.endswith("_test.py"):
        return "Automated test suite and regression checks"
    if filename.endswith(".vue"):
        return "Vue 3 reactive UI component"
    if "components/" in norm_path:
        return "Reusable frontend UI component"
    if filename.endswith(".css") or filename.endswith(".scss"):
        return "Visual stylesheets and presentation rules"
    if filename.endswith(".c") or filename.endswith(".cpp") or filename.endswith(".h") or filename.endswith(".hpp"):
        if any(term in norm_path for term in ["driver", "gpio", "spi", "i2c", "uart", "hal", "bsp", "pico", "hardware"]):
            return "Hardware peripheral controller / driver"
        return "Native C/C++ core module"
    if filename.endswith(".py"):
        return "Python module implementation"
    if filename.endswith(".ts") or filename.endswith(".js"):
        return "TypeScript/JavaScript module logic"
    if filename.endswith(".md"):
        return "Documentation guide"
    if filename.endswith(".json") or filename.endswith(".yaml") or filename.endswith(".yml"):
        return "Configuration parameters"
    return "Source code file"


def load_gitignore_patterns(root: Path) -> list[tuple[bool, str, bool]]:
    """Loads and parses ignore patterns from .gitignore in the given root directory.

    Returns a list of tuples: (is_negation, normalized_pattern, is_dir_only).
    """
    gitignore_file = root / ".gitignore"
    if not gitignore_file.exists() or not gitignore_file.is_file():
        return []

    patterns: list[tuple[bool, str, bool]] = []
    try:
        content = gitignore_file.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            is_neg = False
            if line.startswith("!"):
                is_neg = True
                line = line[1:].strip()

            is_dir_only = line.endswith("/")
            pat = line.rstrip("/").lstrip("/")
            if pat:
                patterns.append((is_neg, pat, is_dir_only))
    except Exception as exc:
        logger.warning("Could not read .gitignore at %s: %s", gitignore_file, exc)

    return patterns


def is_path_ignored_by_gitignore(
    rel_path_posix: str,
    is_dir: bool,
    patterns: list[tuple[bool, str, bool]],
) -> bool:
    """Evaluates whether a relative POSIX path matches any .gitignore rule."""
    if not patterns:
        return False

    ignored = False
    path_parts = rel_path_posix.strip("/").split("/")

    for is_neg, pat, is_dir_only in patterns:
        if is_dir_only and not is_dir:
            matched = False
            if "/" in pat:
                for i in range(1, len(path_parts)):
                    sub_path = "/".join(path_parts[:i])
                    if fnmatch.fnmatch(sub_path, pat) or fnmatch.fnmatch(sub_path, f"*/{pat}"):
                        matched = True
                        break
            else:
                for part in path_parts[:-1]:
                    if fnmatch.fnmatch(part, pat):
                        matched = True
                        break
        else:
            if "/" in pat:
                matched = (
                    fnmatch.fnmatch(rel_path_posix, pat)
                    or fnmatch.fnmatch(rel_path_posix, f"*/{pat}")
                    or fnmatch.fnmatch(rel_path_posix, f"{pat}/*")
                )
            else:
                matched = any(fnmatch.fnmatch(p, pat) for p in path_parts)

        if matched:
            ignored = not is_neg

    return ignored


def scan_project_files(
    root_path: Path | str, max_files: int = 500, max_depth: int = 12
) -> list[tuple[str, Path]]:
    """Recursively scans a project directory and returns safe, non-third-party files.
    Respects .gitignore rules and built-in exclusions for .venv, node_modules, build artifacts, etc.

    Returns a list of tuples: (relative_path_posix, absolute_path).
    """
    root = Path(root_path).resolve()
    if not root.exists() or not root.is_dir():
        return []

    gitignore_patterns = load_gitignore_patterns(root)
    collected: list[tuple[str, Path]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        depth = 0 if rel_dir == "." else len(Path(rel_dir).parts)
        if depth >= max_depth:
            dirnames.clear()
            continue

        # Filter out ignored third-party and cache directories in-place
        filtered_dirs: list[str] = []
        for d in dirnames:
            d_lower = d.lower()
            if d_lower in IGNORED_DIRECTORIES:
                continue
            if d.startswith(".") and d != ".":
                continue
            if d.endswith(".egg-info") or d.endswith(".dist-info"):
                continue
            rel_subdir = (
                d
                if rel_dir == "."
                else f"{str(Path(rel_dir)).replace(os.sep, '/')}/{d}"
            )
            if is_path_ignored_by_gitignore(rel_subdir, is_dir=True, patterns=gitignore_patterns):
                continue
            filtered_dirs.append(d)

        dirnames[:] = filtered_dirs

        for fname in sorted(filenames):
            if len(collected) >= max_files:
                break
            # Skip hidden files except .chappie if needed, and skip ignored extensions
            if fname.startswith(".") and fname != ".chappie":
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in IGNORED_EXTENSIONS:
                continue

            abs_file = Path(dirpath) / fname
            rel_file = str(abs_file.relative_to(root)).replace("\\", "/")

            if is_path_ignored_by_gitignore(rel_file, is_dir=False, patterns=gitignore_patterns):
                continue

            try:
                # Skip files larger than 1.5MB to protect agent context
                if abs_file.stat().st_size > 1_500_000:
                    continue
            except OSError:
                continue

            collected.append((rel_file, abs_file))

        if len(collected) >= max_files:
            break

    return collected


def read_chappie_file(root_path: Path | str) -> str | None:
    """Reads the existing .chappie file from the project root if it exists."""
    chappie_file = Path(root_path).resolve() / ".chappie"
    if chappie_file.is_symlink():
        raise ValueError(f"Project context '{chappie_file}' must not be a symlink.")
    if chappie_file.exists() and chappie_file.is_file():
        try:
            content = chappie_file.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                return content
        except OSError as exc:
            logger.warning("Failed to read .chappie file at %s: %s", chappie_file, exc)
    return None


def write_chappie_file(root_path: Path | str, content: str) -> Path:
    """Writes or overwrites the .chappie file in the project root."""
    chappie_file = Path(root_path).resolve() / ".chappie"
    if chappie_file.is_symlink():
        raise ValueError(f"Project context '{chappie_file}' must not be a symlink.")
    chappie_file.write_text(content.strip() + "\n", encoding="utf-8")
    return chappie_file


def _extract_manifest_details(root: Path) -> dict[str, Any]:
    """Inspects manifest/config files in the project to extract dependencies, frameworks,
    run/build instructions, and primary purpose."""
    details: dict[str, Any] = {
        "manifests_found": [],
        "dependencies": [],
        "purpose": "",
        "project_name": "",
        "frameworks": [],
        "run_instructions": [],
    }

    # 1. Python pyproject.toml
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        details["manifests_found"].append("pyproject.toml")
        try:
            txt = pyproject.read_text(encoding="utf-8", errors="replace")
            # Simple regex search for name and dependencies
            name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', txt)
            if name_match:
                details["project_name"] = name_match.group(1)
            desc_match = re.search(r'description\s*=\s*["\']([^"\']+)["\']', txt)
            if desc_match:
                details["purpose"] = desc_match.group(1)
            deps = re.findall(r'["\']([a-zA-Z0-9_\-\[\]]+(?:>=|<=|==|~=|>|<)[^"\']*)["\']', txt)
            if deps:
                details["dependencies"].extend(deps[:15])

            # Detect Python frameworks and tools
            lower_txt = txt.lower()
            if "fastapi" in lower_txt:
                details["frameworks"].append("FastAPI")
                details["run_instructions"].append("Start API backend: `uv run uvicorn src.chappie.api:application --host 0.0.0.0 --port 8000` (or `uv run python -m ...`)")
            if "pydantic" in lower_txt:
                details["frameworks"].append("Pydantic v2")
            if "langchain" in lower_txt or "langgraph" in lower_txt:
                details["frameworks"].append("LangGraph / LangChain")
            if "chromadb" in lower_txt:
                details["frameworks"].append("ChromaDB Vector Store")
            if "pytest" in lower_txt or (root / "tests").exists():
                details["run_instructions"].append("Run test suite: `uv run pytest`")
            if "uv" in lower_txt or (root / "uv.lock").exists():
                details["frameworks"].append("uv (Python Package Manager)")
        except Exception:
            pass

    # 2. Node.js package.json
    package_json = root / "package.json"
    if package_json.exists():
        details["manifests_found"].append("package.json")
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
            if not details["project_name"] and data.get("name"):
                details["project_name"] = str(data.get("name"))
            if not details["purpose"] and data.get("description"):
                details["purpose"] = str(data.get("description"))
            deps = list(data.get("dependencies", {}).keys())
            dev_deps = list(data.get("devDependencies", {}).keys())
            details["dependencies"].extend(deps[:10])
            if dev_deps:
                details["dependencies"].extend([f"{d} (dev)" for d in dev_deps[:10]])

            all_deps_str = " ".join(deps + dev_deps).lower()
            if "vue" in all_deps_str:
                details["frameworks"].append("Vue 3")
            if "vite" in all_deps_str:
                details["frameworks"].append("Vite")
            if "typescript" in all_deps_str or (root / "tsconfig.json").exists():
                details["frameworks"].append("TypeScript")
            if "tailwindcss" in all_deps_str:
                details["frameworks"].append("TailwindCSS")
            if "react" in all_deps_str:
                details["frameworks"].append("React")

            scripts = data.get("scripts", {})
            if isinstance(scripts, dict):
                for script_name, cmd in scripts.items():
                    details["run_instructions"].append(f"`npm run {script_name}` — {cmd}")
        except Exception:
            pass

    # 3. Docker Compose & Dockerfile
    for compose_name in ["compose.yaml", "docker-compose.yml", "docker-compose.yaml"]:
        compose_file = root / compose_name
        if compose_file.exists():
            details["manifests_found"].append(compose_name)
            details["frameworks"].append("Docker Compose")
            details["run_instructions"].append(f"Launch multi-container services: `docker compose -f {compose_name} up -d`")
            break

    dockerfile = root / "Dockerfile"
    if dockerfile.exists() and "Dockerfile" not in details["manifests_found"]:
        details["manifests_found"].append("Dockerfile")
        details["frameworks"].append("Docker")

    # 4. Embedded Systems & C/C++ Manifests (RP2040 Pico SDK, PlatformIO, CMake, Makefile)
    cmakelists = root / "CMakeLists.txt"
    if cmakelists.exists():
        details["manifests_found"].append("CMakeLists.txt")
        details["frameworks"].append("CMake Build System")
        details["run_instructions"].append("Build: `mkdir -p build && cd build && cmake .. && make`")
        try:
            txt = cmakelists.read_text(encoding="utf-8", errors="replace")
            proj_match = re.search(r"project\s*\(\s*([a-zA-Z0-9_\-]+)", txt, re.IGNORECASE)
            if proj_match and not details["project_name"]:
                details["project_name"] = proj_match.group(1)
            lower_cmake = txt.lower()
            if "pico_sdk" in lower_cmake or (root / "pico_sdk_import.cmake").exists():
                details["frameworks"].append("Raspberry Pi Pico SDK (RP2040 / RP2350)")
                if not details["purpose"]:
                    details["purpose"] = "Raspberry Pi Pico (RP2040 / ARM Cortex-M0+) embedded firmware and hardware controller."
            if "freertos" in lower_cmake:
                details["frameworks"].append("FreeRTOS Real-Time Kernel")
        except Exception:
            pass

    if (root / "pico_sdk_import.cmake").exists() and "pico_sdk_import.cmake" not in details["manifests_found"]:
        details["manifests_found"].append("pico_sdk_import.cmake")
        if "Raspberry Pi Pico SDK (RP2040 / RP2350)" not in details["frameworks"]:
            details["frameworks"].append("Raspberry Pi Pico SDK (RP2040 / RP2350)")

    platformio_ini = root / "platformio.ini"
    if platformio_ini.exists():
        details["manifests_found"].append("platformio.ini")
        details["frameworks"].append("PlatformIO Embedded")
        details["run_instructions"].append("Build and upload: `pio run` / `pio run -t upload`")
        try:
            txt = platformio_ini.read_text(encoding="utf-8", errors="replace")
            envs = re.findall(r"\[env:([^\]]+)\]", txt)
            if envs:
                details["frameworks"].append(f"PlatformIO Env ({', '.join(envs[:3])})")
            board_match = re.search(r"board\s*=\s*([^\r\n]+)", txt)
            if board_match:
                details["frameworks"].append(f"Board: {board_match.group(1).strip()}")
        except Exception:
            pass

    for make_name in ["Makefile", "makefile", "GNUmakefile"]:
        make_file = root / make_name
        if make_file.exists():
            details["manifests_found"].append(make_name)
            if "CMake Build System" not in details["frameworks"]:
                details["frameworks"].append("GNU Make")
                details["run_instructions"].append("Build: `make`")
            break

    # 5. Rust Cargo.toml
    cargo_toml = root / "Cargo.toml"
    if cargo_toml.exists():
        details["manifests_found"].append("Cargo.toml")
        details["frameworks"].append("Rust Cargo")
        details["run_instructions"].append("Build and run: `cargo build` / `cargo run` / `cargo test`")
        try:
            txt = cargo_toml.read_text(encoding="utf-8", errors="replace")
            name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', txt)
            if name_match and not details["project_name"]:
                details["project_name"] = name_match.group(1)
        except Exception:
            pass

    # 6. Go go.mod
    go_mod = root / "go.mod"
    if go_mod.exists():
        details["manifests_found"].append("go.mod")
        details["frameworks"].append("Go Module")
        details["run_instructions"].append("Build and test: `go run .` / `go test ./...`")
        try:
            txt = go_mod.read_text(encoding="utf-8", errors="replace")
            mod_match = re.search(r"^module\s+([^\s]+)", txt, re.MULTILINE)
            if mod_match and not details["project_name"]:
                details["project_name"] = mod_match.group(1)
        except Exception:
            pass

    # 7. README.md & Documentation
    for readme_name in ["README.md", "README.txt", "README.rst", "readme.md"]:
        readme_file = root / readme_name
        if readme_file.exists():
            details["manifests_found"].append(readme_name)
            try:
                txt = readme_file.read_text(encoding="utf-8", errors="replace").strip()
                lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
                if lines and not details["purpose"]:
                    # Take first paragraph or non-heading line
                    for line in lines[:5]:
                        if not line.startswith("#") and len(line) > 10:
                            details["purpose"] = line[:300]
                            break
            except Exception:
                pass
            break

    return details


def build_initial_project_context(
    root_path: Path | str, project_name: str | None = None, profile_id: str | None = None
) -> str:
    """Reads all project files (except third-party packages) and synthesizes the baseline
    project architecture, purpose, tech stack, and conventions into a .chappie context document.
    """
    root = Path(root_path).resolve()
    now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    manifest_info = _extract_manifest_details(root)
    proj_name = (
        project_name
        or manifest_info.get("project_name")
        or root.name
        or "Project"
    )

    files_list = scan_project_files(root, max_files=400)
    total_files = len(files_list)

    # Detect languages and file distributions
    lang_counts: dict[str, int] = {}
    key_source_files: list[str] = []
    file_tree_lines: list[str] = []

    for rel_path, abs_path in files_list:
        if rel_path == ".chappie":
            continue
        ext = abs_path.suffix.lower()
        lang = LANGUAGE_EXTENSION_MAP.get(ext, "Other")
        lang_counts[lang] = lang_counts.get(lang, 0) + 1

        # Track file tree lines with semantic role (first 60 files)
        if len(file_tree_lines) < 60:
            role = infer_file_role(rel_path, abs_path)
            try:
                size_kb = round(abs_path.stat().st_size / 1024, 1)
                file_tree_lines.append(f"- `{rel_path}` ({size_kb} KB) [{lang}]: {role}")
            except OSError:
                file_tree_lines.append(f"- `{rel_path}` [{lang}]: {role}")

        # Identify key source files for architectural inspection
        if (
            ext in {".py", ".ts", ".js", ".vue", ".rs", ".go", ".c", ".cpp", ".cc", ".h", ".hpp", ".s", ".asm"}
            and len(key_source_files) < 20
        ):
            key_source_files.append(rel_path)

    languages_str = (
        ", ".join(
            f"{lang} ({cnt} files)"
            for lang, cnt in sorted(lang_counts.items(), key=lambda x: x[1], reverse=True)
        )
        if lang_counts
        else "Plain Text / Mixed"
    )

    dependencies_str = (
        ", ".join(manifest_info["dependencies"][:12])
        if manifest_info["dependencies"]
        else "Standard library / No explicit external package manifest"
    )

    manifests_str = (
        ", ".join(manifest_info["manifests_found"])
        if manifest_info["manifests_found"]
        else "None detected"
    )

    purpose_str = (
        manifest_info.get("purpose")
        or f"Source code repository and component implementation for {proj_name}."
    )

    # Sample snippet summaries of key files to understand entry points
    architectural_highlights: list[str] = []
    for rel_path in key_source_files[:12]:
        abs_p = root / rel_path
        try:
            code_text = abs_p.read_text(encoding="utf-8", errors="replace")
            # Extract top docstring, class names, or function defs
            defs = re.findall(
                r"^\s*(?:def|class|function|interface|type|struct|enum|export|void|int|bool|uint8_t|uint16_t|uint32_t|float|double|static|inline)\s+([a-zA-Z0-9_]+)\b",
                code_text,
                re.MULTILINE,
            )
            # Filter out common C keywords from matched identifiers
            c_keywords = {"if", "for", "while", "switch", "return", "sizeof", "volatile"}
            clean_defs = [d for d in defs if d not in c_keywords]

            hardware_mentions: list[str] = []
            lower_code = code_text.lower()
            if "gpio" in lower_code:
                hardware_mentions.append("GPIO")
            if "i2c" in lower_code or "wire" in lower_code:
                hardware_mentions.append("I2C")
            if "spi" in lower_code:
                hardware_mentions.append("SPI")
            if "uart" in lower_code or "serial" in lower_code:
                hardware_mentions.append("UART/Serial")
            if "pwm" in lower_code:
                hardware_mentions.append("PWM")
            if "adc" in lower_code:
                hardware_mentions.append("ADC")
            if "pio" in lower_code:
                hardware_mentions.append("PIO")
            if "dma" in lower_code:
                hardware_mentions.append("DMA")
            if "freertos" in lower_code or "xtaskcreate" in lower_code:
                hardware_mentions.append("FreeRTOS Task")

            defs_preview = (
                f" (Defines: {', '.join(clean_defs[:6])})" if clean_defs else ""
            )
            hw_preview = (
                f" [Peripherals: {', '.join(hardware_mentions)}]" if hardware_mentions else ""
            )
            architectural_highlights.append(f"- `{rel_path}`{defs_preview}{hw_preview}")
        except Exception:
            architectural_highlights.append(f"- `{rel_path}`")

    arch_summary_text = (
        "\n".join(architectural_highlights)
        if architectural_highlights
        else "- Baseline repository structure loaded."
    )

    file_tree_text = (
        "\n".join(file_tree_lines)
        if file_tree_lines
        else "(Project is currently empty or contains only non-source files)"
    )
    if total_files > len(file_tree_lines):
        file_tree_text += f"\n... and {total_files - len(file_tree_lines)} more files"

    frameworks_list = manifest_info.get("frameworks", [])
    frameworks_str = (
        ", ".join(frameworks_list)
        if frameworks_list
        else "Standard project architecture"
    )

    run_instructions = manifest_info.get("run_instructions", [])
    if run_instructions:
        run_instructions_str = "\n" + "\n".join(f"- {cmd}" for cmd in run_instructions)
    else:
        run_instructions_str = "\n- Consult project entry points or standard language toolchains for execution."

    profile_line = f"> **Knowledge Profile:** `{profile_id}`  \n" if profile_id else ""

    context_document = f"""# Chappie Project Context — {proj_name}

> **Context Mode:** Local Project Directory (Strict In-Place Sandbox)  
> **Root Directory:** `{root}`  
> **Initialized At:** {now_iso}  
> **Status:** Active & Indexed  
{profile_line}
---

## 1. Project Overview & Purpose
{purpose_str}

### How to Run, Build & Use:
{run_instructions_str}

## 2. Tech Stack & Dependencies
- **Languages:** {languages_str}
- **Frameworks & Tools:** {frameworks_str}
- **Project Manifests:** {manifests_str}
- **Key Dependencies:** {dependencies_str}

## 3. Directory Structure & Key Files ({total_files} project files indexed)
{file_tree_text}

## 4. Architecture & Key Components
{arch_summary_text}

## 5. Development Conventions & Guidelines
- **Sandboxing:** Read and write operations are strictly scoped to `{root}`. In-place operation only — do NOT copy, upload, or duplicate source files to auxiliary folders or volumes. Analyze and modify files directly in-place.
- **Third-Party Exclusions:** Third-party package code (e.g. node_modules, .venv) is excluded from agent context modifications.
- **Consistency:** Maintain existing style, import patterns, error handling, and file naming conventions.
- **Zero Regressions:** Preserve existing functionality and interfaces when implementing new changes.

---

## 6. History of Changes & Context Turns
- **[{now_iso}] Initial Project Indexing:**
  - Scanned project structure and learned architecture from {total_files} files.
  - Synthesized initial baseline knowledge into `.chappie`.
"""
    return context_document


def extract_profile_id_from_chappie(content: str) -> str | None:
    """Extracts knowledge profile id from .chappie header if present."""
    if not content:
        return None
    match = re.search(r">\s*\*\*Knowledge Profile:\*\*\s*`?([a-zA-Z0-9_\-]+)`?", content)
    if match:
        return match.group(1).strip()
    return None


def update_chappie_profile(root_path: Path | str, profile_id: str | None) -> str | None:
    """Updates the Knowledge Profile header line in .chappie file if present."""
    root = Path(root_path).resolve()
    content = read_chappie_file(root)
    if not content:
        return None
    profile_tag = f"> **Knowledge Profile:** `{profile_id}`  \n" if profile_id else ""
    if re.search(r">\s*\*\*Knowledge Profile:\*\*.*", content):
        if profile_id:
            updated = re.sub(
                r">\s*\*\*Knowledge Profile:\*\*.*",
                f"> **Knowledge Profile:** `{profile_id}`  ",
                content,
            )
        else:
            updated = re.sub(r">\s*\*\*Knowledge Profile:\*\*.*\n?", "", content)
    else:
        if profile_id:
            updated = re.sub(
                r"(>\s*\*\*Status:\*\*.*\n)",
                r"\1" + profile_tag,
                content,
            )
        else:
            updated = content
    write_chappie_file(root, updated)
    return updated


def ensure_chappie_context(
    root_path: Path | str,
    project_name: str | None = None,
    profile_id: str | None = None,
) -> tuple[str, bool]:
    """Ensures that a .chappie context file exists in the project root.

    If .chappie exists, reads and returns it (created_new = False).
    If .chappie does not exist, builds the full initial context from project files,
    writes it to .chappie in the root, and returns it (created_new = True).
    """
    root = Path(root_path).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Path '{root_path}' does not exist or is not a directory.")

    existing_content = read_chappie_file(root)
    if existing_content:
        if profile_id and not extract_profile_id_from_chappie(existing_content):
            updated = update_chappie_profile(root, profile_id)
            if updated:
                return updated, False
        return existing_content, False

    # Build initial context and write .chappie
    new_context = build_initial_project_context(
        root, project_name=project_name, profile_id=profile_id
    )
    write_chappie_file(root, new_context)
    return new_context, True


def has_chappie_explanation(content: str | None) -> bool:
    """Checks whether the .chappie context contains a generated project explanation."""
    if not content:
        return False
    return (
        "## Detailed Project Explanation & Architecture Analysis" in content
        or "## Project Explanation" in content
        or "> **Status:** Explained" in content
    )


def update_chappie_explanation(root_path: Path | str, explanation: str) -> str:
    """Saves or updates the project explanation section in the project's .chappie file."""
    root = Path(root_path).resolve()
    existing_content = read_chappie_file(root)
    if not existing_content:
        existing_content, _ = ensure_chappie_context(root)

    now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    clean_explanation = explanation.strip()

    section_header = "## Detailed Project Explanation & Architecture Analysis"
    section_content = (
        f"{section_header}\n"
        f"> **Analyzed & Explained:** {now_iso}  \n"
        f"> **Status:** Explained  \n\n"
        f"{clean_explanation}\n\n"
    )

    if section_header in existing_content:
        # Replace existing explanation section
        pattern = re.compile(
            rf"{re.escape(section_header)}.*?(?=\n## |\Z)",
            re.DOTALL,
        )
        updated_content = pattern.sub(section_content.rstrip() + "\n\n", existing_content)
    elif "## 2. Tech Stack & Dependencies" in existing_content:
        parts = existing_content.split("## 2. Tech Stack & Dependencies", 1)
        updated_content = f"{parts[0]}{section_content}## 2. Tech Stack & Dependencies{parts[1]}"
    else:
        updated_content = f"{existing_content}\n\n{section_content}"

    write_chappie_file(root, updated_content)
    return updated_content


def append_change_to_chappie_file(
    root_path: Path | str,
    task: str,
    answer_summary: str,
    generated_files: list[str] | None = None,
    rationale: str | None = None,
    project_id: str | None = None,
) -> str:
    """Appends a new turn / change record to the project's .chappie file and returns
    the updated context string. Also updates Redis cache if project_id is provided.
    """
    root = Path(root_path).resolve()
    existing_content = read_chappie_file(root)
    if not existing_content:
        existing_content, _ = ensure_chappie_context(root)

    now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    clean_task = task.strip().replace("\n", " ")
    if len(clean_task) > 200:
        clean_task = clean_task[:200] + "..."

    clean_summary = answer_summary.strip()
    if len(clean_summary) > 400:
        clean_summary = clean_summary[:400] + "..."

    files_list = generated_files or []
    files_str = (
        f"\n  - **Files Modified / Generated:** {', '.join(files_list)}"
        if files_list
        else ""
    )

    rationale_str = ""
    if rationale and rationale.strip():
        clean_rationale = rationale.strip()
        if len(clean_rationale) > 400:
            clean_rationale = clean_rationale[:400] + "..."
        rationale_str = f"\n  - **Rationale / Why:** {clean_rationale}"

    new_turn_entry = (
        f"- **[{now_iso}] Task:** {clean_task}\n"
        f"  - **Summary:** {clean_summary}"
        f"{rationale_str}"
        f"{files_str}\n"
    )

    history_marker = "## 6. History of Changes & Context Turns"
    if history_marker in existing_content:
        parts = existing_content.split(history_marker, 1)
        updated_content = f"{parts[0]}{history_marker}\n{new_turn_entry}{parts[1].lstrip()}"
    else:
        updated_content = f"{existing_content}\n\n## 6. History of Changes & Context Turns\n{new_turn_entry}"

    write_chappie_file(root, updated_content)

    if project_id:
        try:
            from chappie.redis_cache import get_redis_cache
            get_redis_cache().set_project_context(project_id, updated_content)
        except Exception:
            pass

    return updated_content


def extract_file_map_from_chappie(content: str) -> dict[str, str]:
    """Extracts relative file paths and their semantic roles from .chappie content."""
    if not content:
        return {}
    file_map: dict[str, str] = {}
    lines = content.splitlines()
    in_section = False
    for line in lines:
        if line.startswith("## 3. Directory Structure & Key Files") or line.startswith("## 3."):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.strip().startswith("- `"):
            # Format: - `rel_path` (size bytes) [Lang]: Role
            match = re.search(r"- `([^`]+)`(?:\s*\([^)]*\))?\s*(?:\[[^\]]*\])?:\s*(.+)$", line.strip())
            if match:
                file_path = match.group(1).strip()
                role = match.group(2).strip()
                file_map[file_path] = role
            else:
                m_simple = re.search(r"- `([^`]+)`", line.strip())
                if m_simple:
                    file_map[m_simple.group(1).strip()] = "Source code file"
    return file_map


def extract_history_from_chappie(content: str) -> list[dict[str, Any]]:
    """Extracts historical turn summaries and rationale from .chappie content."""
    if not content:
        return []
    history: list[dict[str, Any]] = []
    history_marker = "## 6. History of Changes & Context Turns"
    if history_marker not in content:
        return []

    section_text = content.split(history_marker, 1)[1]
    turn_blocks = re.split(r"(?=- \*\*\[)", section_text)

    for block in turn_blocks:
        block = block.strip()
        if not block.startswith("- **["):
            continue

        item: dict[str, Any] = {}
        task_match = re.search(r"- \*\*\[([^\]]+)\]\s*Task:\*\*\s*(.+)", block)
        if task_match:
            item["timestamp"] = task_match.group(1).strip()
            item["task"] = task_match.group(2).strip()

        summary_match = re.search(r"\n\s*-\s*\*\*Summary:\*\*\s*([^\n]+)", block)
        if summary_match:
            item["summary"] = summary_match.group(1).strip()

        rationale_match = re.search(r"\n\s*-\s*\*\*Rationale / Why:\*\*\s*([^\n]+)", block)
        if rationale_match:
            item["rationale"] = rationale_match.group(1).strip()

        files_match = re.search(r"\n\s*-\s*\*\*Files Modified / Generated:\*\*\s*([^\n]+)", block)
        if files_match:
            item["files"] = [f.strip() for f in files_match.group(1).split(",") if f.strip()]

        if item.get("task") or item.get("summary"):
            history.append(item)

    return history
