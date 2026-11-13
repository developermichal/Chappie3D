"""Comprehensive unit and integration tests for .chappie context management,
project file scanning, manifest extraction, and context lifecycle tracking.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chappie.chappie_context import (
    _extract_manifest_details,
    append_change_to_chappie_file,
    build_initial_project_context,
    ensure_chappie_context,
    extract_profile_id_from_chappie,
    read_chappie_file,
    scan_project_files,
    update_chappie_profile,
    write_chappie_file,
)


class TestScanProjectFiles:
    """Tests for scan_project_files filtering, depth limits, and security exclusions."""

    def test_scan_ignores_third_party_and_cache_directories(self, tmp_path: Path) -> None:
        # Create standard source files
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
        (tmp_path / "src" / "utils.py").write_text("def helper(): pass", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Project Docs", encoding="utf-8")

        # Create ignored third-party, cache, and build directories
        ignored_names = [
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
            "sample.egg-info",
        ]

        for ign_dir in ignored_names:
            dir_path = tmp_path / ign_dir
            dir_path.mkdir(parents=True, exist_ok=True)
            (dir_path / "ignored_file.py").write_text("# ignored content", encoding="utf-8")
            (dir_path / "bundle.js").write_text("// ignored js", encoding="utf-8")

        results = scan_project_files(tmp_path)
        rel_paths = [r[0] for r in results]

        # Only legitimate files should be present
        assert "src/main.py" in rel_paths
        assert "src/utils.py" in rel_paths
        assert "README.md" in rel_paths

        # Ensure none of the ignored directory files leaked through
        for ign_dir in ignored_names:
            assert not any(r.startswith(f"{ign_dir}/") for r in rel_paths)
            assert not any(ign_dir in r for r in rel_paths)

    def test_scan_ignores_binary_and_non_source_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "valid.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "src" / "app.ts").write_text("const x = 1;", encoding="utf-8")
        (tmp_path / "config.yaml").write_text("key: value", encoding="utf-8")

        # Create files with ignored extensions
        binary_extensions = [
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
        ]

        for ext in binary_extensions:
            (tmp_path / f"test_binary{ext}").write_bytes(b"\x00\x01\x02\x03")

        results = scan_project_files(tmp_path)
        rel_paths = [r[0] for r in results]

        assert "src/valid.py" in rel_paths
        assert "src/app.ts" in rel_paths
        assert "config.yaml" in rel_paths

        for ext in binary_extensions:
            assert f"test_binary{ext}" not in rel_paths

    def test_scan_ignores_hidden_files_except_chappie(self, tmp_path: Path) -> None:
        (tmp_path / ".chappie").write_text("# Context", encoding="utf-8")
        (tmp_path / ".gitignore").write_text("*.log", encoding="utf-8")
        (tmp_path / ".hidden_file.txt").write_text("secret", encoding="utf-8")
        (tmp_path / ".DS_Store").write_bytes(b"\x00")
        (tmp_path / "normal.txt").write_text("hello", encoding="utf-8")

        results = scan_project_files(tmp_path)
        rel_paths = [r[0] for r in results]

        assert ".chappie" in rel_paths
        assert "normal.txt" in rel_paths
        assert ".gitignore" not in rel_paths
        assert ".hidden_file.txt" not in rel_paths
        assert ".DS_Store" not in rel_paths

    def test_scan_skips_files_larger_than_size_limit(self, tmp_path: Path) -> None:
        # Create normal file
        (tmp_path / "normal.py").write_text("print('ok')", encoding="utf-8")

        # Create file exceeding 1.5MB limit
        large_file = tmp_path / "large_dump.sql"
        large_file.write_bytes(b"A" * 1_600_000)

        results = scan_project_files(tmp_path)
        rel_paths = [r[0] for r in results]

        assert "normal.py" in rel_paths
        assert "large_dump.sql" not in rel_paths

    def test_scan_respects_max_files(self, tmp_path: Path) -> None:
        for i in range(25):
            (tmp_path / f"file_{i:02d}.py").write_text(f"val = {i}", encoding="utf-8")

        results = scan_project_files(tmp_path, max_files=7)
        assert len(results) == 7

    def test_scan_respects_max_depth(self, tmp_path: Path) -> None:
        current = tmp_path
        for level in range(6):
            current = current / f"level_{level}"
            current.mkdir(parents=True)
            (current / f"file_at_level_{level}.py").write_text(f"# lvl {level}", encoding="utf-8")

        # max_depth=3 means rel_dir parts < 3 (depth 0, 1, 2)
        results = scan_project_files(tmp_path, max_depth=3)
        rel_paths = [r[0] for r in results]

        assert "level_0/file_at_level_0.py" in rel_paths
        assert "level_0/level_1/file_at_level_1.py" in rel_paths
        # Beyond max_depth=3 (level_2 is at depth 3 relative to root)
        assert not any("level_2" in p for p in rel_paths)

    def test_scan_non_existent_or_file_returns_empty(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "does_not_exist"
        assert scan_project_files(non_existent) == []

        a_file = tmp_path / "single_file.py"
        a_file.write_text("x = 1", encoding="utf-8")
        assert scan_project_files(a_file) == []


class TestManifestExtraction:
    """Tests for extracting metadata, purpose, and dependencies from project manifests."""

    def test_extract_manifest_pyproject_toml(self, tmp_path: Path) -> None:
        pyproject_content = """[project]
name = "chappie-core"
description = "Autonomous AI coding companion"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn>=0.28.0",
    "pydantic>=2.6.0"
]
"""
        (tmp_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")

        details = _extract_manifest_details(tmp_path)
        assert "pyproject.toml" in details["manifests_found"]
        assert details["project_name"] == "chappie-core"
        assert details["purpose"] == "Autonomous AI coding companion"
        assert any("fastapi" in dep for dep in details["dependencies"])
        assert any("pydantic" in dep for dep in details["dependencies"])

    def test_extract_manifest_package_json(self, tmp_path: Path) -> None:
        pkg_json = {
            "name": "frontend-dashboard",
            "description": "Vue 3 and Vite Admin Dashboard",
            "dependencies": {
                "vue": "^3.4.0",
                "pinia": "^2.1.0",
            },
            "devDependencies": {
                "vite": "^5.1.0",
                "typescript": "^5.3.0",
            },
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg_json), encoding="utf-8")

        details = _extract_manifest_details(tmp_path)
        assert "package.json" in details["manifests_found"]
        assert details["project_name"] == "frontend-dashboard"
        assert details["purpose"] == "Vue 3 and Vite Admin Dashboard"
        assert "vue" in details["dependencies"]
        assert "pinia" in details["dependencies"]
        assert "vite (dev)" in details["dependencies"]

    def test_extract_manifest_cargo_toml(self, tmp_path: Path) -> None:
        cargo_content = """[package]
name = "rusty_engine"
version = "0.1.0"
edition = "2021"
"""
        (tmp_path / "Cargo.toml").write_text(cargo_content, encoding="utf-8")

        details = _extract_manifest_details(tmp_path)
        assert "Cargo.toml" in details["manifests_found"]
        assert details["project_name"] == "rusty_engine"

    def test_extract_manifest_go_mod(self, tmp_path: Path) -> None:
        go_mod_content = """module github.com/user/microservice-api

go 1.22
"""
        (tmp_path / "go.mod").write_text(go_mod_content, encoding="utf-8")

        details = _extract_manifest_details(tmp_path)
        assert "go.mod" in details["manifests_found"]
        assert details["project_name"] == "github.com/user/microservice-api"

    def test_extract_manifest_readme_purpose_fallback(self, tmp_path: Path) -> None:
        readme_content = """# My Awesome Tool

A high-performance command line tool for automating firmware builds.

## Features
- Fast
- Reliable
"""
        (tmp_path / "README.md").write_text(readme_content, encoding="utf-8")

        details = _extract_manifest_details(tmp_path)
        assert "README.md" in details["manifests_found"]
        assert details["purpose"] == "A high-performance command line tool for automating firmware builds."

    def test_extract_manifest_empty_directory(self, tmp_path: Path) -> None:
        details = _extract_manifest_details(tmp_path)
        assert details["manifests_found"] == []
        assert details["dependencies"] == []
        assert details["purpose"] == ""
        assert details["project_name"] == ""


class TestBuildInitialProjectContext:
    """Tests for synthesizing complete .chappie architecture context document."""

    def test_build_context_full_python_project(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            'name = "embedded-hub"\ndescription = "IoT Firmware Hub"\n', encoding="utf-8"
        )
        (tmp_path / "README.md").write_text("# Embedded Hub", encoding="utf-8")

        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "app.py").write_text(
            "class HubController:\n    def start_loop(self):\n        pass\n\ndef main():\n    pass\n",
            encoding="utf-8",
        )
        (src_dir / "models.py").write_text(
            "class DevicePayload:\n    pass\n",
            encoding="utf-8",
        )

        doc = build_initial_project_context(tmp_path)

        assert "# Chappie Project Context — embedded-hub" in doc
        assert "## 1. Project Overview & Purpose" in doc
        assert "IoT Firmware Hub" in doc
        assert "## 2. Tech Stack & Dependencies" in doc
        assert "Python" in doc
        assert "pyproject.toml" in doc
        assert "## 3. Directory Structure & Key Files" in doc
        assert "src/app.py" in doc
        assert "src/models.py" in doc
        assert "## 4. Architecture & Key Components" in doc
        assert "HubController" in doc or "main" in doc
        assert "DevicePayload" in doc
        assert "## 5. Development Conventions & Guidelines" in doc
        assert "Sandboxing:" in doc
        assert "## 6. History of Changes & Context Turns" in doc
        assert "Initial Project Indexing" in doc

    def test_build_context_multi_language_distribution(self, tmp_path: Path) -> None:
        (tmp_path / "backend.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "frontend.ts").write_text("export const a = 2;", encoding="utf-8")
        (tmp_path / "lib.rs").write_text("pub fn compute() {}", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main", encoding="utf-8")
        (tmp_path / "style.css").write_text("body { margin: 0; }", encoding="utf-8")

        doc = build_initial_project_context(tmp_path, project_name="PolyglotRepo")

        assert "# Chappie Project Context — PolyglotRepo" in doc
        assert "Python" in doc
        assert "TypeScript" in doc
        assert "Rust" in doc
        assert "Go" in doc
        assert "CSS" in doc

    def test_build_context_empty_directory_handling(self, tmp_path: Path) -> None:
        doc = build_initial_project_context(tmp_path)
        assert "## 3. Directory Structure & Key Files (0 project files indexed)" in doc
        assert "(Project is currently empty or contains only non-source files)" in doc
        assert "Plain Text / Mixed" in doc

    def test_build_context_extracts_frameworks_and_run_instructions(self, tmp_path: Path) -> None:
        pkg_json = {
            "name": "super-vue-app",
            "description": "Interactive Web Platform",
            "dependencies": {"vue": "^3.5.0"},
            "devDependencies": {"vite": "^6.0.0", "typescript": "^5.0.0"},
            "scripts": {
                "dev": "vite",
                "build": "vue-tsc -b && vite build",
            },
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg_json), encoding="utf-8")
        (tmp_path / "compose.yaml").write_text("services:\n  app:\n    build: .\n", encoding="utf-8")
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "main.ts").write_text("console.log('App started');", encoding="utf-8")

        doc = build_initial_project_context(tmp_path)

        assert "# Chappie Project Context — super-vue-app" in doc
        assert "Interactive Web Platform" in doc
        assert "How to Run, Build & Use:" in doc
        assert "`npm run dev` — vite" in doc
        assert "`npm run build` — vue-tsc -b && vite build" in doc
        assert "docker compose -f compose.yaml up -d" in doc
        assert "Vue 3" in doc
        assert "Vite" in doc
        assert "TypeScript" in doc
        assert "Docker Compose" in doc
        assert "In-place operation only" in doc

    def test_build_context_file_tree_truncation(self, tmp_path: Path) -> None:
        for i in range(75):
            (tmp_path / f"mod_{i:03d}.py").write_text(f"# module {i}", encoding="utf-8")

        doc = build_initial_project_context(tmp_path)
        assert "75 project files indexed" in doc
        assert "... and 15 more files" in doc


class TestChappieFileOperations:
    """Tests for reading and writing .chappie files."""

    def test_read_and_write_chappie_file(self, tmp_path: Path) -> None:
        # File does not exist yet
        assert read_chappie_file(tmp_path) is None

        content = "# Custom Chappie Document\n\nSome custom context."
        written_path = write_chappie_file(tmp_path, content)
        assert written_path.exists()
        assert written_path.name == ".chappie"

        read_back = read_chappie_file(tmp_path)
        assert read_back is not None
        assert "# Custom Chappie Document" in read_back
        assert "Some custom context." in read_back

    def test_read_empty_chappie_file_returns_none(self, tmp_path: Path) -> None:
        chappie_path = tmp_path / ".chappie"
        chappie_path.write_text("   \n\n  ", encoding="utf-8")
        assert read_chappie_file(tmp_path) is None


class TestEnsureChappieContext:
    """Tests for ensure_chappie_context idempotency and creation behavior."""

    def test_ensure_chappie_context_creates_new(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("print('test')", encoding="utf-8")
        chappie_file = tmp_path / ".chappie"
        assert not chappie_file.exists()

        content, created_new = ensure_chappie_context(tmp_path, project_name="FreshProject")

        assert created_new is True
        assert chappie_file.exists()
        assert "# Chappie Project Context — FreshProject" in content
        assert "app.py" in content

    def test_ensure_chappie_context_reads_existing(self, tmp_path: Path) -> None:
        chappie_file = tmp_path / ".chappie"
        chappie_file.write_text("# Existing Custom Context\nDo not overwrite me.", encoding="utf-8")

        content, created_new = ensure_chappie_context(tmp_path, project_name="IgnoredName")

        assert created_new is False
        assert content == "# Existing Custom Context\nDo not overwrite me."
        assert chappie_file.read_text(encoding="utf-8").strip() == "# Existing Custom Context\nDo not overwrite me."

    def test_ensure_chappie_context_invalid_directory_raises(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "missing_dir"
        with pytest.raises(ValueError, match="does not exist or is not a directory"):
            ensure_chappie_context(non_existent)


class TestAppendChangeToChappieFile:
    """Tests for appending changes and turn summaries to .chappie context."""

    def test_append_change_standard_context(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        initial_content, _ = ensure_chappie_context(tmp_path, project_name="AppProject")

        updated = append_change_to_chappie_file(
            root_path=tmp_path,
            task="Add authentication router",
            answer_summary="Implemented JWT bearer token authentication and user verification.",
            generated_files=["src/auth.py", "src/models.py"],
        )

        assert "Add authentication router" in updated
        assert "Implemented JWT bearer token authentication" in updated
        assert "src/auth.py, src/models.py" in updated
        assert read_chappie_file(tmp_path) == updated

    def test_append_change_creates_context_if_missing(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('auto')", encoding="utf-8")
        chappie_file = tmp_path / ".chappie"
        assert not chappie_file.exists()

        updated = append_change_to_chappie_file(
            root_path=tmp_path,
            task="Initial setup turn",
            answer_summary="Auto created project context and initialized turn.",
            generated_files=["main.py"],
        )

        assert chappie_file.exists()
        assert "Initial setup turn" in updated
        assert "Auto created project context" in updated

    def test_append_change_truncates_long_task_and_summary(self, tmp_path: Path) -> None:
        long_task = "Task " + "x" * 300
        long_summary = "Summary " + "y" * 600

        updated = append_change_to_chappie_file(
            root_path=tmp_path,
            task=long_task,
            answer_summary=long_summary,
            generated_files=["output.txt"],
        )

        assert len(long_task) > 200
        assert "..." in updated
        assert read_chappie_file(tmp_path) == updated.strip()

    def test_append_change_without_history_marker(self, tmp_path: Path) -> None:
        custom_header = "# Bare Minimal Context Document"
        write_chappie_file(tmp_path, custom_header)

        updated = append_change_to_chappie_file(
            root_path=tmp_path,
            task="Custom turn",
            answer_summary="Turn on custom formatted file",
        )

        assert "## 6. History of Changes & Context Turns" in updated
        assert "Custom turn" in updated
        assert "Turn on custom formatted file" in updated

    def test_append_multiple_turns_orders_newest_first(self, tmp_path: Path) -> None:
        (tmp_path / "calc.py").write_text("def add(a, b): return a + b", encoding="utf-8")
        ensure_chappie_context(tmp_path, project_name="Calc")

        append_change_to_chappie_file(
            root_path=tmp_path,
            task="First Turn",
            answer_summary="Added add function",
            generated_files=["calc.py"],
        )

        updated2 = append_change_to_chappie_file(
            root_path=tmp_path,
            task="Second Turn",
            answer_summary="Added subtract function",
            generated_files=["calc.py"],
        )

        idx_second = updated2.find("Second Turn")
        idx_first = updated2.find("First Turn")
        idx_initial = updated2.find("Initial Project Indexing")

        # Second turn should appear before first turn, and first before initial indexing
        assert idx_second < idx_first
        assert idx_first < idx_initial


class TestGitignoreAndEmbeddedSystemContext:
    """Tests verifying .gitignore parsing, embedded systems manifests and hardware peripheral indexing."""

    def test_scan_respects_gitignore_patterns(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text(
            "*.log\nbuild/\ntemp_*\n!important.log\n", encoding="utf-8"
        )
        (tmp_path / "app.py").write_text("print(1)", encoding="utf-8")
        (tmp_path / "debug.log").write_text("logs", encoding="utf-8")
        (tmp_path / "important.log").write_text("keep this", encoding="utf-8")
        (tmp_path / "temp_cache.txt").write_text("temp", encoding="utf-8")

        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "output.o").write_text("bin", encoding="utf-8")
        (build_dir / "main.c").write_text("int main() {}", encoding="utf-8")

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.c").write_text("int main() {}", encoding="utf-8")

        results = scan_project_files(tmp_path)
        rel_paths = [r[0] for r in results]

        assert "app.py" in rel_paths
        assert "src/main.c" in rel_paths
        assert "important.log" in rel_paths
        assert "debug.log" not in rel_paths
        assert "temp_cache.txt" not in rel_paths
        assert not any("build/" in p for p in rel_paths)

    def test_extract_embedded_rp2040_and_cmake_manifest(self, tmp_path: Path) -> None:
        cmake_content = """cmake_minimum_required(VERSION 3.13)
include(pico_sdk_import.cmake)
project(rp_embedded_firmware C CXX ASM)
pico_sdk_init()
add_executable(rp_embedded_firmware main.c)
target_link_libraries(rp_embedded_firmware pico_stdlib hardware_gpio hardware_i2c hardware_spi)
"""
        (tmp_path / "CMakeLists.txt").write_text(cmake_content, encoding="utf-8")
        (tmp_path / "pico_sdk_import.cmake").write_text("# Pico SDK import", encoding="utf-8")
        (tmp_path / "main.c").write_text(
            """#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"

void init_hardware() {
    gpio_init(25);
    i2c_init(i2c0, 400 * 1000);
}

int main() {
    stdio_init_all();
    init_hardware();
    return 0;
}
""",
            encoding="utf-8",
        )

        doc = build_initial_project_context(tmp_path)

        assert "# Chappie Project Context — rp_embedded_firmware" in doc
        assert "Raspberry Pi Pico SDK (RP2040 / RP2350)" in doc
        assert "CMake Build System" in doc
        assert "main.c" in doc
        assert "GPIO" in doc
        assert "I2C" in doc
        assert "In-place operation only — do NOT copy, upload, or duplicate" in doc

    def test_extract_platformio_embedded_manifest(self, tmp_path: Path) -> None:
        pio_ini = """[env:pico]
platform = raspberrypi
board = pico
framework = arduino
lib_deps = Wire, SPI
"""
        (tmp_path / "platformio.ini").write_text(pio_ini, encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.cpp").write_text(
            """#include <Arduino.h>
#include <SPI.h>

void setup() {
    Serial.begin(115200);
}

void loop() {}
""",
            encoding="utf-8",
        )

        doc = build_initial_project_context(tmp_path)

        assert "platformio.ini" in doc
        assert "PlatformIO Embedded" in doc
        assert "Board: pico" in doc
        assert "src/main.cpp" in doc
        assert "UART/Serial" in doc or "SPI" in doc


class TestChappieProfileIntegration:
    """Tests for embedding, extracting, and updating Knowledge Profile in .chappie."""

    def test_build_and_extract_profile_id(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('test')", encoding="utf-8")
        doc = build_initial_project_context(tmp_path, profile_id="profile-elektronik")
        assert "> **Knowledge Profile:** `profile-elektronik`" in doc

        extracted = extract_profile_id_from_chappie(doc)
        assert extracted == "profile-elektronik"

    def test_update_chappie_profile(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('test')", encoding="utf-8")
        ensure_chappie_context(tmp_path, profile_id="default")

        content = read_chappie_file(tmp_path)
        assert content is not None
        assert extract_profile_id_from_chappie(content) == "default"

        # Update profile to electronics
        updated = update_chappie_profile(tmp_path, "profile-elektronik")
        assert updated is not None
        assert extract_profile_id_from_chappie(updated) == "profile-elektronik"

        # Read from disk to confirm persistence
        persisted = read_chappie_file(tmp_path)
        assert persisted is not None
        assert extract_profile_id_from_chappie(persisted) == "profile-elektronik"


class TestChappieExplanationIntegration:
    """Tests for saving and verifying project explanations in .chappie."""

    def test_save_and_has_chappie_explanation(self, tmp_path: Path) -> None:
        from chappie.chappie_context import has_chappie_explanation, update_chappie_explanation

        (tmp_path / "main.py").write_text("print('test')", encoding="utf-8")
        initial_doc, _ = ensure_chappie_context(tmp_path)
        assert not has_chappie_explanation(initial_doc)

        explanation = "Ten projekt to firmware dla mikrokontrolera RP2040 realizujący sterowanie PWM i komunikację I2C."
        updated = update_chappie_explanation(tmp_path, explanation)

        assert has_chappie_explanation(updated)
        assert "## Detailed Project Explanation & Architecture Analysis" in updated
        assert "firmware dla mikrokontrolera RP2040" in updated

        # Verify disk persistence
        from_disk = read_chappie_file(tmp_path)
        assert from_disk is not None
        assert has_chappie_explanation(from_disk)
        assert "firmware dla mikrokontrolera RP2040" in from_disk

        # Verify re-explaining updates the content cleanly
        reexplanation = "Zaktualizowane wyjaśnienie architektury: dodano moduł FreeRTOS."
        reupdated = update_chappie_explanation(tmp_path, reexplanation)
        assert has_chappie_explanation(reupdated)
        assert "Zaktualizowane wyjaśnienie architektury: dodano moduł FreeRTOS." in reupdated
        assert reupdated.count("## Detailed Project Explanation & Architecture Analysis") == 1
