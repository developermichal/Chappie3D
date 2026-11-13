from __future__ import annotations

from unittest.mock import mock_open, patch

import pytest

from chappie.memory import (
    MemoryStatus,
    _estimate_tokens_fallback,
    _get_bpe_encoding,
    _get_cgroup_memory,
    _get_linux_proc_meminfo,
    _get_psutil_memory,
    estimate_tokens,
    get_system_memory_status,
    get_system_memory_usage_pct,
    is_memory_spill_required,
)


def test_memory_status_model() -> None:
    status = MemoryStatus(
        total_mb=16384.0,
        available_mb=8192.0,
        used_mb=8192.0,
        percent_used=50.0,
        spill_active=False,
    )
    assert status.total_mb == 16384.0
    assert status.percent_used == 50.0
    assert not status.spill_active


def test_real_system_memory_retrieval() -> None:
    status = get_system_memory_status()
    assert isinstance(status, MemoryStatus)
    assert status.total_mb >= 0.0
    assert status.available_mb >= 0.0
    assert 0.0 <= status.percent_used <= 100.0

    pct = get_system_memory_usage_pct()
    assert 0.0 <= pct <= 100.0


def test_memory_spill_threshold_boundaries() -> None:
    with patch(
        "chappie.memory.get_system_memory_status",
        return_value=MemoryStatus(
            total_mb=1000.0, available_mb=250.0, used_mb=750.0, percent_used=75.0, spill_active=False
        ),
    ):
        assert not is_memory_spill_required(threshold_pct=80.0)

    with patch(
        "chappie.memory.get_system_memory_status",
        return_value=MemoryStatus(
            total_mb=1000.0, available_mb=200.0, used_mb=800.0, percent_used=80.0, spill_active=False
        ),
    ):
        assert not is_memory_spill_required(threshold_pct=80.0)

    with patch(
        "chappie.memory.get_system_memory_status",
        return_value=MemoryStatus(
            total_mb=1000.0, available_mb=199.0, used_mb=801.0, percent_used=80.1, spill_active=True
        ),
    ):
        assert is_memory_spill_required(threshold_pct=80.0)

    with patch(
        "chappie.memory.get_system_memory_status",
        return_value=MemoryStatus(
            total_mb=1000.0, available_mb=100.0, used_mb=900.0, percent_used=90.0, spill_active=True
        ),
    ):
        assert is_memory_spill_required(threshold_pct=80.0)


def test_linux_proc_meminfo_parsing() -> None:
    meminfo_content = """MemTotal:       16384000 kB
MemFree:         4096000 kB
MemAvailable:    8192000 kB
Buffers:          512000 kB
Cached:          3584000 kB
"""
    m = mock_open(read_data=meminfo_content)
    with patch("builtins.open", m):
        total_mb, avail_mb, used_mb, pct = _get_linux_proc_meminfo()

        assert total_mb == pytest.approx(16000.0, rel=1e-2)
        assert avail_mb == pytest.approx(8000.0, rel=1e-2)
        assert used_mb == pytest.approx(8000.0, rel=1e-2)
        assert pct == pytest.approx(50.0, rel=1e-2)


def test_cgroup_v2_parsing(tmp_path) -> None:
    cg2_curr = tmp_path / "memory.current"
    cg2_max = tmp_path / "memory.max"

    cg2_curr.write_text(str(8 * 1024 * 1024 * 1024), encoding="utf-8")
    cg2_max.write_text(str(10 * 1024 * 1024 * 1024), encoding="utf-8")

    with patch("chappie.memory.Path") as mock_path:

        def path_side_effect(p_str: str):
            if "memory.current" in str(p_str):
                return cg2_curr
            if "memory.max" in str(p_str):
                return cg2_max
            return tmp_path / "non_existent"

        mock_path.side_effect = path_side_effect

        res = _get_cgroup_memory()
        assert res is not None
        total_mb, _avail_mb, used_mb, pct = res
        assert total_mb == pytest.approx(10240.0, rel=1e-2)
        assert used_mb == pytest.approx(8192.0, rel=1e-2)
        assert pct == pytest.approx(80.0, rel=1e-2)


def test_psutil_fallback_when_unavailable() -> None:
    with patch.dict("sys.modules", {"psutil": None}):
        res = _get_psutil_memory()
        assert res is None


def test_estimate_tokens_empty_and_edge_types() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0  # type: ignore[arg-type]
    assert estimate_tokens("   ") >= 1
    assert estimate_tokens("a") == 1
    assert estimate_tokens(12345) >= 5  # type: ignore[arg-type]


def test_estimate_tokens_bpe_proxy_and_cross_model_safety() -> None:
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")

    english_text = "Chappie coding assistant with containerized execution."
    code_text = "def solve(n: int) -> list[dict[str, Any]]:\n    return [{'id': i} for i in range(n)]"
    json_text = '{"task": "context_management", "tokens_used": 24000, "status": "success"}'
    polish_sentence = "Zażółć gęślą jaźń, a następnie zoptymalizuj budżetowanie tokenów sesji."

    for sample in [english_text, code_text, json_text, polish_sentence]:
        raw_bpe = len(enc.encode_ordinary(sample))
        estimated = estimate_tokens(sample)
        utf8_bytes = len(sample.encode("utf-8", errors="replace"))

        # Safety: Estimated tokens must never be less than the raw BPE proxy
        assert estimated >= raw_bpe
        # Boundedness: Estimated tokens cannot exceed the total UTF-8 byte count
        assert estimated <= utf8_bytes


def test_estimate_tokens_digit_heavy_inputs_qwen_safety() -> None:
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")

    # In cl100k_base, [0-9]{1,3} groups digits (10 digits -> 4 tokens).
    # In Qwen/Llama, every digit is a separate token (\p{N} -> 10 tokens).
    digit_string = "1234567890"
    raw_bpe = len(enc.encode_ordinary(digit_string))
    assert raw_bpe == 4  # Confirms cl100k would undercount Qwen

    estimated_digits = estimate_tokens(digit_string)
    # Our estimate must account for individual digit tokenization and never underestimate
    assert estimated_digits >= 10

    # Test structured JSON with numeric IDs and timestamps
    num_json = '{"timestamp": 1716309876, "ids": [1001, 1002, 1003], "ratio": 0.987654}'
    raw_json_bpe = len(enc.encode_ordinary(num_json))
    estimated_json = estimate_tokens(num_json)
    assert estimated_json >= raw_json_bpe
    assert estimated_json >= 30


def test_estimate_tokens_special_token_safety() -> None:
    # Text containing raw special tokens must not raise ValueError
    special_text = "System <|im_start|> user\nTask instructions <|endoftext|>"
    tokens = estimate_tokens(special_text)
    assert tokens > 0


def test_estimate_tokens_adversarial_fallback_upper_bound() -> None:
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")

    adversarial_samples = [
        "Polish diacritics: Zażółć gęślą jaźń, chrząszcz brzmi w trzcinie.",
        "Emoji burst: " + ("🚀💡⚡🔥🎉💻📱🤖" * 10),
        "Punctuation heavy code: {{{[[[(((!@#$%^&*()_+{}|:\"<>?~`)))]]]]}}}",
        "Hex dump: 0x7fff5fbff8a0 0x7fff5fbff8a8 0xdeadbeef 0xcafebabe",
        "UUIDs: c3b9d0e2-8f1a-4b7c-9e3d-2a1f0c8e7b6a 7f9d8a1c-3b2e-4f5a-8d7c-1e2f3a4b5c6d",
        "Digits run: " + ("9876543210" * 15),
        "Random Unicode: αβγδεζηθικλμνξοπρστυφχψω äöüß éèêë ñç 漢字 日本語 한국어",
        '{"deep": {"nested": [{"id": 123456789, "val": 0.00012345}, {"active": true}]}}',
        "a" * 500,
        "ą" * 200,
        "   \t\n\r   " * 20,
    ]

    for s in adversarial_samples:
        fb = _estimate_tokens_fallback(s)
        raw_bpe = len(enc.encode_ordinary(s))
        utf8_bytes = len(s.encode("utf-8", errors="replace"))

        # 1. Fallback is strictly equal to the UTF-8 byte count
        assert fb == utf8_bytes
        # 2. Provable property: UTF-8 byte upper bound is NEVER less than BPE token count
        assert fb >= raw_bpe, f"Fallback failed upper bound for: {s[:30]!r}"
        # 3. Main estimate is never less than raw BPE proxy
        assert estimate_tokens(s) >= raw_bpe, f"Main estimate undercounted for: {s[:30]!r}"
        # 4. Main estimate never exceeds UTF-8 byte bound
        assert estimate_tokens(s) <= utf8_bytes


def test_estimate_tokens_uses_fallback_when_bpe_unavailable() -> None:
    with patch("chappie.memory._get_bpe_encoding", return_value=None):
        pl_text = "Zażółć gęślą jaźń"
        fallback_tokens = estimate_tokens(pl_text)
        expected_fallback = _estimate_tokens_fallback(pl_text)
        assert fallback_tokens == expected_fallback
        assert fallback_tokens == len(pl_text.encode("utf-8"))


def test_estimate_tokens_handles_encoding_exception() -> None:
    class BrokenEncoder:
        def encode_ordinary(self, _text: str):
            raise RuntimeError("Corrupted BPE vocabulary")

    with patch("chappie.memory._get_bpe_encoding", return_value=BrokenEncoder()):
        test_text = "Resilient fallback test under unexpected tokenizer errors."
        tokens = estimate_tokens(test_text)
        assert tokens == _estimate_tokens_fallback(test_text)
        assert tokens == len(test_text.encode("utf-8"))


def test_bpe_encoding_is_cached() -> None:
    enc1 = _get_bpe_encoding()
    enc2 = _get_bpe_encoding()
    assert enc1 is enc2
