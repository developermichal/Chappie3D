from __future__ import annotations

import time

import pytest

from chappie.circuit_sim import (
    CircuitRL,
    CircuitRLAnalyzer,
    CircuitSecurityError,
    Generator,
    SinusoidalGenerator,
    SquareWaveGenerator,
    _scale_si_unit,
    ensure_electric_sim_shim,
    get_circuit_sim_guide,
    is_electronic_circuit_task,
    parse_simulation_metrics,
    run_circuit_simulation,
    validate_circuit_code,
)
from chappie.models import CircuitSimulationResult


class TestCircuitTaskDetection:
    """Tests for zero-latency regex classifier with negative guards, SI units, and electronics domain terms."""

    @pytest.mark.parametrize(
        "task",
        [
            "Calculate voltage across a load given current",
            "Simulate an RL circuit with a sinusoidal generator",
            "Design an RLC low-pass filter with 1kHz cutoff",
            "Calculate the voltage divider with a 10k resistor and 5k resistor",
            "Explain Kirchhoff's current law and Kirchhoff's voltage law",
            "Explain Kirchhoff’s rules for loop analysis",
            "Kirchhoff rules in multi-loop networks",
            "Find current using Ohm's law with 12V and 4 ohm resistance",
            "State ohms law and calculate resistance",
            "Verify Ohm’s law with variable voltage",
            "Write an ElectricCircuitSim script for AC/DC converter",
            "Square wave generator driving an inductor and capacitor circuit",
            "Calculate resonant frequency and impedance of an LC circuit",
            "Analyze transient response of an RC circuit",
            "Op-amp inverting amplifier electronic circuit design",
            "Design a transistor amplifier with a BJT",
            "Build a 5V power supply with a diode bridge",
            "Design a series RC filter for noise reduction",
            "Low-pass filter with cutoff at 500 Hz",
            "High-pass filter circuit simulation",
            "Calculate gate voltage for MOSFET switch",
            "Find impedance of circuit with 2 mH inductor and 100 uF capacitor",
        ],
    )
    def test_positive_circuit_tasks(self, task: str) -> None:
        assert is_electronic_circuit_task(task) is True

    @pytest.mark.parametrize(
        "task",
        [
            "Use git mv to rename a file",
            "How do I use the mv command in bash",
            "Best pizza in Boston MA",
            "Use the pf firewall on macOS",
            "Build a quantum circuit in Qiskit",
            "Formula 1 circuit lap times",
            "AC/DC greatest hits",
            "Rock band AC/DC concert tour dates",
            "Implement a circuit breaker with resistor-like backoff",
            "Explain short-circuit evaluation in Python",
            "How does short-circuit boolean logic work in JavaScript?",
            "Remove Norton antivirus from Windows",
            "Norton security license renewal",
            "Write a fast sorting algorithm in Python",
            "How to reverse a linked list in C++",
            "Create a web scraper using beautifulsoup",
            "Optimize a PostgreSQL indexing query",
            "Implement a binary search tree",
            "Build a REST API endpoint with FastAPI",
            "Hello world in Python",
            "",
            "   ",
        ],
    )
    def test_negative_circuit_tasks(self, task: str) -> None:
        assert is_electronic_circuit_task(task) is False

    def test_performance_sub_millisecond(self) -> None:
        task = "Simulate an RLC circuit with resistor, capacitor, and inductor."
        start = time.perf_counter()
        for _ in range(100):
            is_electronic_circuit_task(task)
        elapsed_per_call_ms = ((time.perf_counter() - start) / 100) * 1000
        # Must execute in < 2ms (typically < 0.05ms)
        assert elapsed_per_call_ms < 2.0


class TestCircuitSimGuide:
    """Tests for prompt context documentation generator."""

    def test_guide_content(self) -> None:
        guide = get_circuit_sim_guide()
        assert isinstance(guide, str)
        assert "ElectricCircuitSim" in guide
        assert "CircuitRL" in guide
        assert "SinusoidalGenerator" in guide
        assert "SquareWaveGenerator" in guide
        assert "CircuitRLAnalyzer" in guide
        assert "calculateVoltage" in guide
        assert "CHAPPIE_METRICS" in guide


class TestElectricSimEngine:
    """Tests for Python implementation of ElectricCircuitSim components."""

    def test_sinusoidal_generator(self) -> None:
        gen = SinusoidalGenerator(frequency=50.0, amplitude=10.0, phase=0.0)
        assert gen.calculateVoltage(0.0) == pytest.approx(0.0)
        # At t = 1/(4*f) = 1/200 = 0.005s, sin(pi/2) = 1.0 -> 10.0V
        assert gen.calculateVoltage(0.005) == pytest.approx(10.0)

    def test_square_wave_generator(self) -> None:
        gen = SquareWaveGenerator(frequency=10.0, amplitude=5.0)
        # Period = 0.1s. First half (0 to 0.05s) -> +5.0V, second half -> -5.0V
        assert gen.calculateVoltage(0.02) == 5.0
        assert gen.calculateVoltage(0.07) == -5.0

    def test_square_wave_zero_freq(self) -> None:
        gen = SquareWaveGenerator(frequency=0.0, amplitude=3.3)
        assert gen.calculateVoltage(1.0) == 3.3

    def test_circuit_rl_simulation(self) -> None:
        square_gen = SquareWaveGenerator(frequency=0.5, amplitude=0.5)
        circuit = CircuitRL(resistance=1.0, inductance=5.0)
        result = circuit.simulate(generator=square_gen, t_end=0.2, dt=0.1)

        assert len(result) == 3
        # t=0.0, V=0.5, I=0.01
        assert result[0][0] == pytest.approx(0.0)
        assert result[0][1] == pytest.approx(0.5)
        assert result[0][2] == pytest.approx(0.01)

        # t=0.1, V=0.5, I=0.0198
        assert result[1][0] == pytest.approx(0.1)
        assert result[1][1] == pytest.approx(0.5)
        assert result[1][2] == pytest.approx(0.0198)

    def test_circuit_rl_analyzer(self) -> None:
        gen1 = SinusoidalGenerator(frequency=1.0, amplitude=5.0)
        gen2 = SquareWaveGenerator(frequency=2.0, amplitude=3.0)
        circuit = CircuitRL(resistance=2.0, inductance=1.0)
        analyzer = CircuitRLAnalyzer(generators={"sin": gen1, "sq": gen2}, circuit=circuit)

        analyzer.analyze(end_time=0.1, step=0.01)
        assert "sin" in analyzer.results
        assert "sq" in analyzer.results
        assert len(analyzer.results["sin"]) == 11
        assert len(analyzer.results["sq"]) == 11

    def test_circuit_rl_invalid_params(self) -> None:
        gen = Generator()
        circuit = CircuitRL(resistance=1.0, inductance=1.0)
        with pytest.raises(ValueError, match="dt must be positive"):
            circuit.simulate(gen, t_end=1.0, dt=-0.1)

        zero_l_circuit = CircuitRL(resistance=1.0, inductance=0.0)
        with pytest.raises(ValueError, match="Inductance L cannot be zero"):
            zero_l_circuit.simulate(gen, t_end=1.0, dt=0.1)

        with pytest.raises(ValueError, match="t_end must be non-negative"):
            circuit.simulate(gen, t_end=-1.0, dt=0.1)

    def test_ensure_electric_sim_shim(self) -> None:
        provenance = ensure_electric_sim_shim()
        assert provenance in ("native", "chappie-compat-0.0.2")


class TestMetricParser:
    """Tests for simulation metric extraction, SI scaling, and nested JSON parsing."""

    def test_parse_tuple_list(self) -> None:
        stdout = "Simulation finished: [(0.0, 5.0, 0.0), (0.1, 5.0, 0.05), (0.2, 5.0, 0.09)]"
        metrics = parse_simulation_metrics(stdout)
        assert metrics["sample_count"] == 3
        assert metrics["time_start"] == 0.0
        assert metrics["time_end"] == 0.2
        assert metrics["voltage_max"] == 5.0
        assert metrics["final_current"] == 0.09

    def test_parse_key_values_with_newline_boundary(self) -> None:
        stdout = """Results
final_current: 0.125
frequency = 50.0
"""
        metrics = parse_simulation_metrics(stdout)
        assert "final_current" in metrics
        assert metrics["final_current"] == 0.125
        assert metrics["frequency"] == 50.0

    def test_scale_si_unit_comprehensive(self) -> None:
        assert _scale_si_unit(10.5, "mV") == pytest.approx(0.0105)
        assert _scale_si_unit(20.0, "mA") == pytest.approx(0.02)
        assert _scale_si_unit(2.5, "kHz") == pytest.approx(2500.0)
        assert _scale_si_unit(2.0, "mH") == pytest.approx(0.002)
        assert _scale_si_unit(10.0, "kOhm") == pytest.approx(10000.0)
        assert _scale_si_unit(10.0, "kohm") == pytest.approx(10000.0)
        assert _scale_si_unit(100.0, "uF") == pytest.approx(0.0001)
        assert _scale_si_unit(50.0, "mF") == pytest.approx(0.05)
        assert _scale_si_unit(100.0, "mW") == pytest.approx(0.1)
        assert _scale_si_unit(5.0, "mohm") == pytest.approx(0.005)

    def test_parse_si_unit_scaling(self) -> None:
        stdout = """
Peak Voltage: 10.5 mV
Current: 20 mA
Frequency: 2.5 kHz
Inductance: 2 mH
Resistance: 10 kohm
Capacitance: 100 uF
Power: 100 mW
"""
        metrics = parse_simulation_metrics(stdout)
        assert metrics["peak_voltage"] == pytest.approx(0.0105)
        assert metrics["current"] == pytest.approx(0.02)
        assert metrics["frequency"] == pytest.approx(2500.0)
        assert metrics["inductance"] == pytest.approx(0.002)
        assert metrics["resistance"] == pytest.approx(10000.0)
        assert metrics["capacitance"] == pytest.approx(0.0001)
        assert metrics["power"] == pytest.approx(0.1)

    def test_parse_numpy_float_repr(self) -> None:
        stdout = "final_current: np.float64(0.0198)\npeak_voltage: float32(5.0)"
        metrics = parse_simulation_metrics(stdout)
        assert metrics["final_current"] == pytest.approx(0.0198)
        assert metrics["peak_voltage"] == pytest.approx(5.0)

    def test_parse_chappie_metrics_contract(self) -> None:
        stdout = (
            'CHAPPIE_METRICS: {"custom_gain": 14.2, "settling_time": 0.002, "nested": {"key": 1}}'
        )
        metrics = parse_simulation_metrics(stdout)
        assert metrics["custom_gain"] == 14.2
        assert metrics["settling_time"] == 0.002
        assert metrics["nested"] == {"key": 1}

    def test_empty_stdout(self) -> None:
        assert parse_simulation_metrics("") == {}
        assert parse_simulation_metrics("No numbers here.") == {}


class TestSecurityAndIsolation:
    """Tests for fail-closed AST security validator against all bypass vectors."""

    @pytest.mark.parametrize(
        "malicious_code",
        [
            "import os; os.system('calc')",
            "import sys; sys.exit(0)",
            "import subprocess; subprocess.Popen(['cmd'])",
            "import socket; s = socket.socket()",
            "import urllib.request; urllib.request.urlopen('http://example.com')",
            "import requests",
            "import shutil",
            "import ctypes",
            "from os import path",
            "open('pwn.txt', 'w').write('test')",
            "exec('import os')",
            "eval('__import__(\"os\")')",
            "__builtins__.__import__('os')",
            "__builtins__.__import__('ctypes')",
            "from typing import sys",
            "from json.tool import sys",
            "from pathlib import os",
            "import pathlib; pathlib.os.system('calc')",
            "x = ().__class__.__subclasses__()",
            "y = [].__class__.__bases__",
            "f = lambda: None; f.__globals__['os'] = 1",
            "import memoryview",
            "import bytearray",
        ],
    )
    def test_ast_validator_blocks_unsafe_code(self, malicious_code: str) -> None:
        result = run_circuit_simulation(malicious_code, timeout=2.0)
        assert result.success is False
        assert result.error_message is not None
        assert "Security violation" in result.error_message or "SyntaxError" in result.error_message

    def test_ast_validator_direct(self) -> None:
        with pytest.raises(CircuitSecurityError):
            validate_circuit_code("import socket")

        with pytest.raises(CircuitSecurityError):
            validate_circuit_code("open('test.txt')")

        with pytest.raises(CircuitSecurityError):
            validate_circuit_code("from typing import sys")

        with pytest.raises(CircuitSecurityError):
            validate_circuit_code("__builtins__.__import__('os')")


class TestDeclarativeRunner:
    """Tests for declarative execution of ElectricCircuitSim scripts."""

    def test_run_valid_simulation(self) -> None:
        code = """from __future__ import annotations
from ElectricCircuitSim.core import CircuitRL, SinusoidalGenerator

gen = SinusoidalGenerator(frequency=5.0, amplitude=10.0)
circuit = CircuitRL(resistance=2.0, inductance=1.0)
results = circuit.simulate(gen, 0.1, 0.01)
print(results)
"""
        result = run_circuit_simulation(code, timeout=2.0)
        assert isinstance(result, CircuitSimulationResult)
        assert result.success is True
        assert result.duration_ms >= 0
        assert result.measurements.get("sample_count") == 11
        assert result.measurements.get("engine") == "chappie-compat-0.0.2"
        assert result.error_message is None

    def test_run_with_math_expressions(self) -> None:
        code = """from __future__ import annotations
import math
from ElectricCircuitSim.core import CircuitRL, SinusoidalGenerator

freq = 50.0
gen = SinusoidalGenerator(frequency=freq, amplitude=10.0, phase=math.pi / 2)
circuit = CircuitRL(resistance=10.0, inductance=0.05)
results = circuit.simulate(gen, t_end=1.0 / freq, dt=1.0 / (20 * freq))
print(results)
"""
        result = run_circuit_simulation(code, timeout=2.0)
        assert result.success is True
        assert result.measurements.get("sample_count") == 21
        assert result.measurements.get("engine") == "chappie-compat-0.0.2"

    def test_run_with_analyzer(self) -> None:
        code = """from __future__ import annotations
from ElectricCircuitSim.core import CircuitRL, SinusoidalGenerator
from ElectricCircuitSim.analyze import CircuitRLAnalyzer

gen = SinusoidalGenerator(frequency=1.0, amplitude=5.0)
circuit = CircuitRL(resistance=1.0, inductance=2.0)
analyzer = CircuitRLAnalyzer(generators={"sin": gen}, circuit=circuit)
analyzer.analyze(end_time=0.1, step=0.01)
print("peak_voltage: 5.0 V")
print("final_current: 0.02 A")
"""
        result = run_circuit_simulation(code, timeout=2.0)
        assert result.success is True
        assert result.measurements.get("peak_voltage") == 5.0
        assert result.measurements.get("final_current") == 0.02
        assert result.measurements.get("engine") == "chappie-compat-0.0.2"

    def test_run_syntax_error(self) -> None:
        code = "def invalid_syntax(:"
        result = run_circuit_simulation(code, timeout=2.0)
        assert result.success is False
        assert result.error_message is not None
        assert "SyntaxError" in result.error_message

    def test_run_invalid_circuit_parameters(self) -> None:
        code = """from ElectricCircuitSim.core import CircuitRL, SinusoidalGenerator

gen = SinusoidalGenerator(5.0, 10.0)
circuit = CircuitRL(1.0, 0.0)
results = circuit.simulate(gen, 0.1, 0.01)
"""
        result = run_circuit_simulation(code, timeout=2.0)
        assert result.success is False
        assert "Inductance L cannot be zero" in result.error_message

    def test_run_empty_code(self) -> None:
        result = run_circuit_simulation("")
        assert result.success is False
        assert "Empty code" in result.error_message
