from __future__ import annotations

import functools
import logging
import math
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import tiktoken

logger = logging.getLogger(__name__)

_DIGIT_RUN_PATTERN = re.compile(r"\d{2,}")


class MemoryStatus(BaseModel):
    """Represents current RAM metrics and disk spill activation status."""

    total_mb: float = Field(default=0.0, ge=0.0)
    available_mb: float = Field(default=0.0, ge=0.0)
    used_mb: float = Field(default=0.0, ge=0.0)
    percent_used: float = Field(default=0.0, ge=0.0, le=100.0)
    spill_active: bool = False


def _get_windows_memory() -> tuple[float, float, float, float]:
    """Retrieve system memory on Windows using GlobalMemoryStatusEx via ctypes."""
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        raise RuntimeError("GlobalMemoryStatusEx failed")

    total_mb = float(stat.ullTotalPhys) / (1024.0 * 1024.0)
    avail_mb = float(stat.ullAvailPhys) / (1024.0 * 1024.0)
    used_mb = max(0.0, total_mb - avail_mb)
    pct = (used_mb / total_mb * 100.0) if total_mb > 0 else float(stat.dwMemoryLoad)
    return total_mb, avail_mb, used_mb, min(100.0, max(0.0, pct))


def _get_cgroup_memory() -> tuple[float, float, float, float] | None:
    """Retrieve memory usage from Linux cgroup limits (Docker / container), if present."""
    # cgroup v2
    cg2_current = Path("/sys/fs/cgroup/memory.current")
    cg2_max = Path("/sys/fs/cgroup/memory.max")
    if cg2_current.exists() and cg2_max.exists():
        try:
            curr_str = cg2_current.read_text(encoding="utf-8").strip()
            max_str = cg2_max.read_text(encoding="utf-8").strip()
            if max_str != "max":
                used_bytes = int(curr_str)
                total_bytes = int(max_str)
                if total_bytes > 0:
                    total_mb = total_bytes / (1024.0 * 1024.0)
                    used_mb = used_bytes / (1024.0 * 1024.0)
                    avail_mb = max(0.0, total_mb - used_mb)
                    pct = used_mb / total_mb * 100.0
                    return total_mb, avail_mb, used_mb, min(100.0, max(0.0, pct))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed reading cgroup v2 memory: %s", exc)

    # cgroup v1
    cg1_usage = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    cg1_limit = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if cg1_usage.exists() and cg1_limit.exists():
        try:
            used_bytes = int(cg1_usage.read_text(encoding="utf-8").strip())
            limit_bytes = int(cg1_limit.read_text(encoding="utf-8").strip())
            if 0 < limit_bytes < (1 << 60):
                total_mb = limit_bytes / (1024.0 * 1024.0)
                used_mb = used_bytes / (1024.0 * 1024.0)
                avail_mb = max(0.0, total_mb - used_mb)
                pct = used_mb / total_mb * 100.0
                return total_mb, avail_mb, used_mb, min(100.0, max(0.0, pct))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed reading cgroup v1 memory: %s", exc)

    return None


def _get_linux_proc_meminfo() -> tuple[float, float, float, float]:
    """Retrieve memory usage from /proc/meminfo on Linux."""
    mem_info: dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val_parts = parts[1].strip().split()
                if val_parts:
                    mem_info[key] = int(val_parts[0])

    total_kb = mem_info.get("MemTotal", 0)
    avail_kb = mem_info.get("MemAvailable")
    if avail_kb is None:
        free_kb = mem_info.get("MemFree", 0)
        buffers_kb = mem_info.get("Buffers", 0)
        cached_kb = mem_info.get("Cached", 0)
        avail_kb = free_kb + buffers_kb + cached_kb

    total_mb = total_kb / 1024.0
    avail_mb = avail_kb / 1024.0
    used_mb = max(0.0, total_mb - avail_mb)
    pct = (used_mb / total_mb * 100.0) if total_mb > 0 else 0.0
    return total_mb, avail_mb, used_mb, min(100.0, max(0.0, pct))


def _get_psutil_memory() -> tuple[float, float, float, float] | None:
    """Retrieve memory usage using psutil if available."""
    try:
        import psutil  # type: ignore[import-untyped]

        vm = psutil.virtual_memory()
        total_mb = vm.total / (1024.0 * 1024.0)
        avail_mb = vm.available / (1024.0 * 1024.0)
        used_mb = vm.used / (1024.0 * 1024.0)
        pct = float(vm.percent)
        return total_mb, avail_mb, used_mb, min(100.0, max(0.0, pct))
    except (ImportError, Exception):  # noqa: BLE001
        return None


def get_system_memory_status(threshold_pct: float = 80.0) -> MemoryStatus:
    """Retrieve current system memory stats and evaluate if spill threshold is exceeded.

    Tries psutil, Windows ctypes GlobalMemoryStatusEx, Linux cgroups, and /proc/meminfo.
    """
    total_mb, avail_mb, used_mb, pct = 0.0, 0.0, 0.0, 0.0

    psutil_res = _get_psutil_memory()
    if psutil_res is not None:
        total_mb, avail_mb, used_mb, pct = psutil_res
    elif sys.platform == "win32":
        try:
            total_mb, avail_mb, used_mb, pct = _get_windows_memory()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Windows memory query failed: %s", exc)
    else:
        cg_res = _get_cgroup_memory()
        if cg_res is not None:
            total_mb, avail_mb, used_mb, pct = cg_res
        elif os.path.exists("/proc/meminfo"):
            try:
                total_mb, avail_mb, used_mb, pct = _get_linux_proc_meminfo()
            except Exception as exc:  # noqa: BLE001
                logger.warning("/proc/meminfo query failed: %s", exc)

    spill_active = pct > threshold_pct
    return MemoryStatus(
        total_mb=round(total_mb, 2),
        available_mb=round(avail_mb, 2),
        used_mb=round(used_mb, 2),
        percent_used=round(pct, 2),
        spill_active=spill_active,
    )


def get_system_memory_usage_pct() -> float:
    """Return current system RAM usage as a percentage (0.0 to 100.0)."""
    status = get_system_memory_status()
    return status.percent_used


def is_memory_spill_required(threshold_pct: float = 80.0) -> bool:
    """Return True if RAM usage strictly exceeds threshold_pct (e.g. > 80.0%)."""
    status = get_system_memory_status(threshold_pct=threshold_pct)
    return status.spill_active


@functools.lru_cache(maxsize=1)
def _get_bpe_encoding() -> tiktoken.Encoding | None:
    """Retrieve and cache tiktoken cl100k_base BPE encoding instance if available."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Tiktoken encoding cl100k_base unavailable, using conservative byte upper-bound fallback: %s",
            exc,
        )
        return None


def _estimate_tokens_fallback(text: str) -> int:
    """Conservative fallback token estimation when BPE tokenizer is unavailable.

    Uses the UTF-8 byte count of the text as a mathematically guaranteed upper bound.
    In any byte-level BPE tokenizer (e.g. GPT-4/cl100k, Qwen, Llama), each token
    consumes at least 1 UTF-8 byte, making byte count a provable upper bound for all
    Unicode text, Polish diacritics, JSON, numbers, and code.
    """
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
        if not text:
            return 0
    try:
        byte_count = len(text.encode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        byte_count = len(text)
    return max(1, byte_count)


def estimate_tokens(text: str) -> int:
    """Estimates LLM token count for a string with conservative safety margins.

    Uses tiktoken BPE cl100k_base encoding as a fast proxy with adjustments for
    deployed models (e.g., Qwen/Llama):
      1. Digit run correction: Qwen/Llama tokenizers tokenize each digit individually
         (\\p{N}), whereas cl100k_base groups up to 3 digits per token ([0-9]{1,3}).
         We calculate and add the digit deficit for all multi-digit runs.
      2. Safety factor: A conservative +10% margin is added as an empirical proxy to
         mitigate subword tokenization differences across architectures (e.g., Polish
         diacritics, rare code identifiers, JSON indentation/formatting).
      3. Byte upper bound: The estimated token count is capped at the total UTF-8 byte count,
         which serves as the rigorous, provable upper bound for byte-level BPE tokenizers.
      4. Fallback: If tiktoken is unavailable, returns the guaranteed UTF-8 byte upper bound.
    """
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
        if not text:
            return 0

    try:
        byte_count = len(text.encode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        byte_count = len(text)

    encoding = _get_bpe_encoding()
    if encoding is not None:
        try:
            base_tokens = len(encoding.encode_ordinary(text))
            digit_correction = sum(
                len(m.group(0)) - (len(m.group(0)) + 2) // 3
                for m in _DIGIT_RUN_PATTERN.finditer(text)
            )
            adjusted = math.ceil((base_tokens + digit_correction) * 1.10)
            return min(byte_count, max(1, adjusted))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed BPE token estimation via tiktoken, falling back to byte upper bound: %s",
                exc,
            )

    return _estimate_tokens_fallback(text)
