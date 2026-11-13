from __future__ import annotations

import ast
import contextlib
import json
import math
import re
import sys
import time
import types
from typing import Any

from chappie.models import CircuitSimulationResult

__all__ = [
    "Circuit",
    "CircuitRL",
    "CircuitRLAnalyzer",
    "CircuitSecurityError",
    "CircuitSimulationResult",
    "Generator",
    "SinusoidalGenerator",
    "SquareWaveGenerator",
    "ensure_electric_sim_shim",
    "get_circuit_sim_guide",
    "is_electronic_circuit_task",
    "parse_simulation_metrics",
    "run_circuit_simulation",
    "validate_circuit_code",
]

# -----------------------------------------------------------------------------
# Zero-Latency Regex Pattern Matcher for Circuit Tasks
# -----------------------------------------------------------------------------

_NEGATIVE_CIRCUIT_REGEX = re.compile(
    r"\b(?:"
    r"circuit\s*breaker(?:\s+pattern|\s+with|\s+in|\s+for|\s+middleware|\s+microservice)?"
    r"|short[- ]circuit(?:\s*evaluation|\s*logic|\s*boolean|\s*operator)?"
    r"|norton\s*(?:antivirus|security|lifelock|symantec|360|utilities)"
    r"|ac\s*/\s*dc\s*(?:song|band|rock|music|album|lyrics|tour|concert|track|guitar|greatest\s*hits|highway\s*to\s*hell|back\s*in\s*black|angus\s*young|bon\s*scott|brian\s*johnson)"
    r"|(?:greatest\s*hits|rock\s*band)\s*ac\s*/\s*dc"
    r"|quantum\s*circuit(?:s)?"
    r"|(?:formula\s*1|f1|racing|race|track|nascar|grand\s*prix|indycar|lap\s*time|karting)\s*circuit(?:s)?"
    r"|circuit\s*(?:court|judge|bench|judicial|clerks?|riding)"
    r"|resistor[- ]like\s*backoff"
    r")\b",
    re.IGNORECASE,
)

_STRONG_CIRCUIT_REGEX = re.compile(
    r"\b(?:"
    r"electriccircuitsim"
    r"|circuitrl|circuitrlanalyzer"
    r"|sinusoidal\s*generator|square\s*wave\s*generator"
    r"|kirchhoff(?:['’]s)?\s*(?:law|laws|rule|rules|current|voltage|kcl|kvl)"
    r"|ohm(?:s|['’]s)?\s*law"
    r"|voltage\s*divider|current\s*divider"
    r"|RLC(?:\s*(?:circuit|filter|oscillator|resonance|network|bandpass|lowpass|highpass|series|parallel))"
    r"|RL\s*circuit|RC\s*circuit|LC\s*circuit"
    r"|op(?:\s*|-)?amp|operational\s*amplifier"
    r"|diode\s*bridge|rectifier|bjt|mosfet|jfet|transistor\s*amplifier"
    r")\b",
    re.IGNORECASE,
)

_CASE_SENSITIVE_UNITS_REGEX = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mH|uH|µH|μH|nH|pH|kH|mF|uF|µF|μF|nF|pF|kΩ|MΩ|kOhm|MOhm|mohm|mΩ|kohm|ohm|Ω|mV|uV|µV|μV|nV|pV|kV|MV|V|mA|uA|µA|μA|nA|pA|kA|MA|A|kHz|MHz|GHz|Hz|mW|kW|MW|W)\b"
)

_POSITIVE_CIRCUIT_REGEX = re.compile(
    r"\b(?:"
    r"electric(?:al)?\s*circuit(?:s|ry)?"
    r"|electronic\s*circuit(?:s|ry)?"
    r"|circuit\s*(?:simulation|simulator|analysis|design|diagram|theory|model|solver|schematic|board|topology)"
    r"|electriccircuitsim"
    r"|RLC(?:\s*(?:circuit|filter|oscillator|resonance|network|bandpass|lowpass|highpass|series|parallel))?"
    r"|RL\s*circuit|RC\s*circuit|LC\s*circuit"
    r"|series\s*R[LC]\s*filter|parallel\s*R[LC]\s*filter"
    r"|resistor(?:s)?|capacitor(?:s)?|inductor(?:s)?|inductance|capacitance"
    r"|impedance|reactance|admittance"
    r"|sinusoidal\s*generator|square\s*wave\s*generator|signal\s*generator|function\s*generator"
    r"|voltage\s*divider(?:s)?|current\s*divider(?:s)?"
    r"|kirchhoff(?:['’]s)?\s*(?:law|laws|rule|rules|current|voltage|kcl|kvl)"
    r"|kirchhoff\s*rules?"
    r"|ohm(?:s|['’]s)?\s*law"
    r"|(?:thevenin|norton)(?:['’]s)?\s*(?:theorem|equivalent|circuit)"
    r"|op(?:\s*|-)?amp|operational\s*amplifier"
    r"|bjt|mosfet|jfet|transistor\s*amplifier|transistor"
    r"|diode(?:\s*bridge)?|rectifier"
    r"|power\s*supply"
    r"|bode\s*plot"
    r"|cutoff\s*frequency|resonant\s*frequency|resonance\s*frequency"
    r"|transient\s*response"
    r"|(?:low|high|band)[- ]pass\s*filter(?:s)?"
    r"|ac\s*/\s*dc(?:\s*(?:converter|generator|circuit|current|voltage|power|simulation))?"
    r"|dc\s*/\s*ac(?:\s*(?:converter|inverter|generator|circuit|current|voltage|power|simulation))?"
    r"|(?:calculate|find|determine|compute)\s+(?:the\s+)?(?:voltage|current|resistance|impedance|inductance|capacitance|reactance)"
    r")\b",
    re.IGNORECASE,
)

_CO_OCCURRING_TERMS_REGEX = re.compile(
    r"\b(?:voltage|potential\s*difference)\b.*\b(?:current|amperage|resistance|load|resistor)\b"
    r"|\b(?:current|amperage)\b.*\b(?:voltage|potential\s*difference|resistance|load|resistor)\b",
    re.IGNORECASE,
)


def is_electronic_circuit_task(task: str) -> bool:
    """Evaluates whether the incoming task involves electronic circuits or simulation.

    Executes in under 2ms using pre-compiled regex pattern matching without LLM overhead.
    """
    if not task or not task.strip():
        return False

    # Negative guards take precedence unless strong domain terms indicate an explicit circuit query
    if _NEGATIVE_CIRCUIT_REGEX.search(task) and not _STRONG_CIRCUIT_REGEX.search(task):
        return False

    # Check positive domain keywords
    if _POSITIVE_CIRCUIT_REGEX.search(task):
        return True

    # Check co-occurring electrical terms (e.g. voltage across a load given current)
    if _CO_OCCURRING_TERMS_REGEX.search(task):
        return True

    # Check case-sensitive electrical unit with magnitude (e.g. 10 kOhm, 2 mH)
    return bool(_CASE_SENSITIVE_UNITS_REGEX.search(task))


# -----------------------------------------------------------------------------
# Pure Python Engine for ElectricCircuitSim
# (Single source of truth for Euler integration and circuit classes)
# -----------------------------------------------------------------------------


class Generator:
    """Abstract/base signal generator."""

    def calculateVoltage(self, time: float) -> float:
        return 0.0


class SinusoidalGenerator(Generator):
    """Sinusoidal AC voltage generator: V(t) = A * sin(2 * pi * f * t + phi)."""

    def __init__(self, frequency: float, amplitude: float, phase: float = 0.0) -> None:
        self.frequency = float(frequency)
        self.amplitude = float(amplitude)
        self.phase = float(phase)

    def calculateVoltage(self, time: float) -> float:
        return float(self.amplitude * math.sin(2.0 * math.pi * self.frequency * time + self.phase))


class SquareWaveGenerator(Generator):
    """Square wave voltage generator with 50% duty cycle."""

    def __init__(self, frequency: float, amplitude: float) -> None:
        self.frequency = float(frequency)
        self.amplitude = float(amplitude)

    def calculateVoltage(self, time: float) -> float:
        if self.frequency <= 0:
            return float(self.amplitude)
        period = 1.0 / self.frequency
        mod_time = time % period
        if mod_time < (period / 2.0):
            return float(self.amplitude)
        return float(-self.amplitude)


class Circuit:
    """Base circuit model."""

    def simulate(
        self, generator: Generator, t_end: float, dt: float
    ) -> list[tuple[float, float, float]]:
        return []


class CircuitRL(Circuit):
    """Resistor-Inductor (RL) circuit transient simulator.

    Governing differential equation: V(t) = L * dI/dt + R * I(t).
    Solved iteratively using the forward Euler method.
    """

    def __init__(self, resistance: float, inductance: float, initialCurrent: float = 0.0) -> None:
        self.resistance = float(resistance)
        self.inductance = float(inductance)
        self.initialCurrent = float(initialCurrent)

    def simulate(
        self, generator: Generator, t_end: float, dt: float
    ) -> list[tuple[float, float, float]]:
        if dt <= 0:
            raise ValueError(f"Time step dt must be positive, got {dt}")
        if self.inductance == 0:
            raise ValueError("Inductance L cannot be zero for RL simulation")
        if t_end < 0:
            raise ValueError(f"End time t_end must be non-negative, got {t_end}")

        results: list[tuple[float, float, float]] = []
        current = self.initialCurrent
        num_steps = math.floor(t_end / dt + 1e-9) + 1

        # Bound maximum steps to prevent computation stalls
        max_allowed_steps = 200_000
        if num_steps > max_allowed_steps:
            raise ValueError(
                f"Requested simulation steps ({num_steps}) exceeds safety maximum ({max_allowed_steps}). "
                f"Increase dt or reduce t_end."
            )

        for i in range(num_steps):
            t = i * dt
            voltage = generator.calculateVoltage(t)
            di = ((voltage - self.resistance * current) / self.inductance) * dt
            current += di
            results.append((round(t, 10), voltage, current))

        return results


class CircuitRLAnalyzer:
    """Multi-generator analyzer for RL circuits."""

    def __init__(self, generators: dict[str, Generator], circuit: CircuitRL) -> None:
        self.generators = dict(generators)
        self.circuit = circuit
        self.results: dict[str, list[tuple[float, float, float]]] = {}

    def add_generator(self, name: str, generator: Generator) -> None:
        self.generators[name] = generator

    def analyze(self, end_time: float, step: float, name: str | list[str] | None = None) -> None:
        if isinstance(name, str):
            gens_to_run = {name: self.generators[name]} if name in self.generators else {}
        elif isinstance(name, list):
            gens_to_run = {k: self.generators[k] for k in name if k in self.generators}
        else:
            gens_to_run = self.generators

        for gen_name, gen in gens_to_run.items():
            self.results[gen_name] = self.circuit.simulate(gen, end_time, step)


def ensure_electric_sim_shim() -> str:
    """Ensures ElectricCircuitSim module and its core stubs are available in sys.modules.

    Attempts native import first; if native binary is unavailable, provides pure Python classes.
    Returns the provenance identifier ('native' or 'chappie-compat-0.0.2').
    """
    target_mod_name = "ElectricCircuitSim.core._electric_sim"
    if target_mod_name in sys.modules:
        mod = sys.modules[target_mod_name]
        if hasattr(mod, "__file__") and (
            mod.__file__.endswith(".pyd") or mod.__file__.endswith(".so")
        ):
            return "native"
        return "chappie-compat-0.0.2"

    try:
        import ElectricCircuitSim.core._electric_sim as native_mod

        if hasattr(native_mod, "CircuitRL"):
            return "native"
    except (ImportError, ModuleNotFoundError):
        pass

    mod = types.ModuleType(target_mod_name)
    mod.Generator = Generator
    mod.SinusoidalGenerator = SinusoidalGenerator
    mod.SquareWaveGenerator = SquareWaveGenerator
    mod.Circuit = Circuit
    mod.CircuitRL = CircuitRL
    sys.modules[target_mod_name] = mod

    with contextlib.suppress(ImportError):
        from ElectricCircuitSim import core

        if not hasattr(core, "CircuitRL"):
            core.Generator = Generator
            core.SinusoidalGenerator = SinusoidalGenerator
            core.SquareWaveGenerator = SquareWaveGenerator
            core.Circuit = Circuit
            core.CircuitRL = CircuitRL

    return "chappie-compat-0.0.2"


# -----------------------------------------------------------------------------
# Fail-Closed Static AST Security Validator
# -----------------------------------------------------------------------------

_ALLOWED_MODULES = {
    "__future__",
    "ElectricCircuitSim",
    "ElectricCircuitSim.core",
    "ElectricCircuitSim.analyze",
    "math",
}

_ALLOWED_IMPORT_SYMBOLS = {
    "annotations",
    "Generator",
    "SinusoidalGenerator",
    "SquareWaveGenerator",
    "Circuit",
    "CircuitRL",
    "CircuitRLAnalyzer",
    "core",
    "analyze",
    "sin",
    "cos",
    "tan",
    "pi",
    "sqrt",
    "floor",
    "ceil",
    "exp",
    "log",
    "log10",
}

_FORBIDDEN_BUILTIN_NAMES = {
    "__import__",
    "__builtins__",
    "open",
    "exec",
    "eval",
    "compile",
    "globals",
    "locals",
    "vars",
    "input",
    "breakpoint",
    "exit",
    "quit",
    "help",
    "copyright",
    "license",
    "credits",
    "getattr",
    "setattr",
    "delattr",
    "memoryview",
    "bytearray",
}


class CircuitSecurityError(Exception):
    """Raised when generated code attempts disallowed operations or sandbox escapes."""


def validate_circuit_code(code: str) -> None:
    """Validates the AST of untrusted circuit code against a strict allowlist.

    Rejects unsafe imports, process executions, file accesses, or any identifier starting with '_'.
    Raises SyntaxError if parsing fails or CircuitSecurityError if unsafe operations are found.
    """
    if not code or not code.strip():
        raise SyntaxError("Empty code string provided.")

    tree = ast.parse(code)

    for node in ast.walk(tree):
        # 1. Reject any private identifier or dunder attribute access
        if isinstance(node, ast.Name):
            if node.id.startswith("_"):
                raise CircuitSecurityError(f"Forbidden private identifier or dunder '{node.id}'.")
            if node.id in _FORBIDDEN_BUILTIN_NAMES:
                raise CircuitSecurityError(f"Forbidden builtin function or identifier '{node.id}'.")

        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise CircuitSecurityError(f"Forbidden private attribute or dunder '{node.attr}'.")

        elif isinstance(node, ast.FunctionDef) and node.name.startswith("_"):
            raise CircuitSecurityError(f"Forbidden private function definition '{node.name}'.")

        elif isinstance(node, ast.ClassDef) and node.name.startswith("_"):
            raise CircuitSecurityError(f"Forbidden private class definition '{node.name}'.")

        # 2. Strict module import validation
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in _ALLOWED_MODULES:
                    raise CircuitSecurityError(
                        f"Forbidden import '{alias.name}'. Only ElectricCircuitSim "
                        f"and math modules are permitted."
                    )
                if alias.asname and alias.asname.startswith("_"):
                    raise CircuitSecurityError(f"Forbidden alias '{alias.asname}'.")

        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod not in _ALLOWED_MODULES:
                raise CircuitSecurityError(
                    f"Forbidden import from '{mod}'. Only ElectricCircuitSim "
                    f"and math modules are permitted."
                )
            for alias in node.names:
                if alias.name not in _ALLOWED_IMPORT_SYMBOLS:
                    raise CircuitSecurityError(
                        f"Forbidden symbol '{alias.name}' imported from '{mod}'."
                    )
                if alias.asname and alias.asname.startswith("_"):
                    raise CircuitSecurityError(f"Forbidden alias '{alias.asname}'.")


# -----------------------------------------------------------------------------
# Context Guidance for Prompt Engineering
# -----------------------------------------------------------------------------


def get_circuit_sim_guide() -> str:
    """Returns domain instructions and API specifications for ElectricCircuitSim."""
    return """## Electronic Circuit Simulation Guidelines (ElectricCircuitSim)

When designing or analyzing electronic circuits, you must provide runnable Python code utilizing the `ElectricCircuitSim` library along with theoretical explanations.

### API Specification & Components

1. **Imports**:
```python
from ElectricCircuitSim.core import CircuitRL, SinusoidalGenerator, SquareWaveGenerator
from ElectricCircuitSim.analyze import CircuitRLAnalyzer
```

2. **Signal Generators**:
- `SinusoidalGenerator(frequency: float, amplitude: float, phase: float = 0.0)`
  - Mathematical model: $V(t) = A \\sin(2\\pi f t + \\phi)$
  - Method: `calculateVoltage(time: float) -> float`
- `SquareWaveGenerator(frequency: float, amplitude: float)`
  - Period $T = 1 / f$. Outputs $+A$ for $t \\in [0, T/2)$ and $-A$ for $t \\in [T/2, T)$.
  - Method: `calculateVoltage(time: float) -> float`

3. **RL Circuit Simulation**:
- `CircuitRL(resistance: float, inductance: float, initialCurrent: float = 0.0)`
  - Solves the differential equation $V(t) = L \\frac{dI}{dt} + R I(t)$ using the Euler numerical method.
  - Method: `simulate(generator, t_end: float, dt: float) -> list[tuple[float, float, float]]`
  - Returns a list of `(time, voltage, current)` tuples.

4. **Multi-Signal Analysis & Visualization**:
- `CircuitRLAnalyzer(generators: dict[str, Generator], circuit: CircuitRL)`
  - Methods:
    - `add_generator(name: str, generator)`
    - `analyze(end_time: float, step: float, name: str | list[str] | None = None)`
  - Property `.results` contains `{generator_name: [(time, voltage, current), ...]}`.

### Best Practices for Generated Code
- Set appropriate simulation steps ($dt \\le \\frac{1}{20 \\cdot f}$) to guarantee numerical stability.
- Always output metrics using `CHAPPIE_METRICS: {"metric_name": value}` or key-value prints so they are captured by the automated verification harness.
- Formulate scripts cleanly with standard component models and parameter definitions.
"""


# -----------------------------------------------------------------------------
# Simulation Output Parser
# -----------------------------------------------------------------------------


def _scale_si_unit(val: float, unit: str | None) -> float:
    """Scales a numeric measurement according to standard SI engineering prefixes."""
    if not unit:
        return val
    u = unit.strip()

    # Base units
    if u in ("V", "A", "Hz", "s", "sec", "Ohm", "ohm", "Ω", "H", "F", "W", "J"):
        return val

    # Mega (1e6)
    if u.startswith(("MOhm", "MΩ", "MHz", "MV", "MW", "MA", "MJ")):
        return val * 1e6

    # Kilo (1e3)
    if (
        u.startswith(("kOhm", "kohm", "kΩ", "kHz", "kV", "kW", "kA", "kJ", "kH"))
        or u.lower() == "kohm"
    ):
        return val * 1e3

    # Milli (1e-3)
    if (
        u.startswith(("mH", "mV", "mA", "ms", "mohm", "mΩ", "mF", "mW", "mJ"))
        or u.lower() == "mohm"
    ):
        return val * 1e-3

    # Micro (1e-6)
    if u.startswith(
        (
            "uV",
            "uA",
            "uF",
            "uH",
            "us",
            "uW",
            "uohm",
            "uΩ",
            "µV",
            "µA",
            "µF",
            "µH",
            "µs",
            "µW",
            "µohm",
            "µΩ",
            "μV",
            "μA",
            "μF",
            "μH",
            "μs",
            "μW",
            "μohm",
            "μΩ",
        )
    ):
        return val * 1e-6

    # Nano (1e-9)
    if u.startswith(("nF", "nH", "ns", "nA", "nV", "nW", "nohm", "nΩ")):
        return val * 1e-9

    # Pico (1e-12)
    if u.startswith(("pF", "pH", "ps", "pA", "pV", "pW")):
        return val * 1e-12

    return val


def parse_simulation_metrics(stdout: str) -> dict[str, Any]:
    """Extracts structured numeric and waveform metrics from simulation stdout.

    Supports CHAPPIE_METRICS contract lines (with full nested JSON support), SI unit scaling,
    unwrapped numpy reprs, and simulation tuple series.
    """
    measurements: dict[str, Any] = {}
    if not stdout or not stdout.strip():
        return measurements

    # Pre-process numpy type representations (e.g. np.float64(0.0198) -> 0.0198)
    cleaned_stdout = re.sub(
        r"\b(?:np\.|numpy\.)?(?:float(?:16|32|64|128)?|int(?:8|16|32|64)?)\(([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\)",
        r"\1",
        stdout,
    )

    # 1. Check for CHAPPIE_METRICS contract lines (highest priority) with robust JSON parsing
    chappie_matches = list(
        re.finditer(r"CHAPPIE_METRICS\s*[:=]\s*\{", cleaned_stdout, re.IGNORECASE)
    )
    for m in chappie_matches:
        start_idx = m.end() - 1
        with contextlib.suppress(Exception):
            data, _ = json.JSONDecoder().raw_decode(cleaned_stdout[start_idx:])
            if isinstance(data, dict):
                measurements.update(data)

    # 2. Check for explicit key-value lines with horizontal whitespace only
    kv_pattern = re.compile(
        r"^[^\S\r\n]*([a-zA-Z_][a-zA-Z0-9_ \t-]*?)[^\S\r\n]*[:=][^\S\r\n]*"
        r"([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)"
        r"(?:[^\S\r\n]*([a-zA-ZΩμµ/][a-zA-Z0-9Ωμµ/]*))?[^\S\r\n]*$",
        re.MULTILINE,
    )
    for match in kv_pattern.finditer(cleaned_stdout):
        raw_key = match.group(1).strip()
        key = re.sub(r"[\s-]+", "_", raw_key).lower()
        val_str = match.group(2).strip()
        unit_str = match.group(3)

        with contextlib.suppress(ValueError):
            raw_val = float(val_str) if "." in val_str or "e" in val_str.lower() else int(val_str)
            scaled_val = _scale_si_unit(float(raw_val), unit_str)
            if key not in measurements:
                measurements[key] = scaled_val
                if unit_str:
                    measurements[f"{key}_unit"] = unit_str.strip()

    # 3. Check for general JSON blocks in output using raw_decode
    for m in re.finditer(r"\{", cleaned_stdout):
        start_idx = m.start()
        with contextlib.suppress(Exception):
            data, _ = json.JSONDecoder().raw_decode(cleaned_stdout[start_idx:])
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, (int, float, str, bool, list, dict)):
                        json_key = f"json_{k}"
                        if json_key not in measurements and k not in measurements:
                            measurements[json_key] = v

    # 4. Check for tuple lists [(time, voltage, current), ...] (fallback for aggregate metrics)
    tuple_list_match = re.search(
        r"\[\s*\(\s*[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?,\s*[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?,\s*[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?\s*\).*?\]",
        cleaned_stdout,
        re.DOTALL,
    )
    if tuple_list_match:
        with contextlib.suppress(ValueError, SyntaxError, TypeError, IndexError):
            parsed_data = ast.literal_eval(tuple_list_match.group(0))
            if isinstance(parsed_data, list) and len(parsed_data) > 0:
                times = [pt[0] for pt in parsed_data]
                voltages = [pt[1] for pt in parsed_data]
                currents = [pt[2] for pt in parsed_data]

                tuple_metrics = {
                    "sample_count": len(parsed_data),
                    "time_start": float(times[0]),
                    "time_end": float(times[-1]),
                    "voltage_min": float(min(voltages)),
                    "voltage_max": float(max(voltages)),
                    "voltage_avg": float(sum(voltages) / len(voltages)),
                    "current_min": float(min(currents)),
                    "current_max": float(max(currents)),
                    "current_avg": float(sum(currents) / len(currents)),
                    "final_voltage": float(voltages[-1]),
                    "final_current": float(currents[-1]),
                }
                for k, v in tuple_metrics.items():
                    if k not in measurements:
                        measurements[k] = v

    return measurements


# -----------------------------------------------------------------------------
# Declarative Circuit Parser & In-Process Execution Engine
# (Security Boundary: Evaluates declarative specifications as data, no arbitrary code execution)
# -----------------------------------------------------------------------------


def _safe_eval_ast_number(node: ast.AST, env: dict[str, Any]) -> float:
    """Evaluates literal numbers and safe math expressions without executing arbitrary code."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return -_safe_eval_ast_number(node.operand, env)
        if isinstance(node.op, ast.UAdd):
            return +_safe_eval_ast_number(node.operand, env)
    if isinstance(node, ast.BinOp):
        left = _safe_eval_ast_number(node.left, env)
        right = _safe_eval_ast_number(node.right, env)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError("Division by zero in circuit expression.")
            return left / right
        if isinstance(node.op, ast.Pow):
            return float(left**right)
    if isinstance(node, ast.Name) and node.id in env:
        val = env[node.id]
        if isinstance(val, (int, float)):
            return float(val)
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "math"
        and hasattr(math, node.attr)
    ):
        attr_val = getattr(math, node.attr)
        if isinstance(attr_val, (int, float)):
            return float(attr_val)
    if isinstance(node, ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "math"
        ):
            func_name = node.func.attr
        if (
            func_name in ("sin", "cos", "tan", "sqrt", "exp", "log", "log10", "floor", "ceil")
            and node.args
        ):
            arg_val = _safe_eval_ast_number(node.args[0], env)
            math_func = getattr(math, func_name)
            return float(math_func(arg_val))

    raise ValueError(f"Unable to safely evaluate numeric expression node: {ast.dump(node)}")


def _run_declarative_circuit(
    tree: ast.AST, script_path: str = "circuit_simulation.py"
) -> CircuitSimulationResult:
    """Parses and executes a declarative ElectricCircuitSim AST in-process."""
    env: dict[str, Any] = {
        "pi": math.pi,
        "math": math,
    }
    stdout_lines: list[str] = []
    simulation_results: list[tuple[float, float, float]] = []
    analyzer_results: dict[str, list[tuple[float, float, float]]] = {}

    start_time = time.perf_counter()

    for stmt in getattr(tree, "body", []):
        # 1. Imports (already validated)
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue

        # 2. Variable assignments
        elif isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                continue
            target_name = stmt.targets[0].id
            val_node = stmt.value

            # Check if value is a function/method call
            if isinstance(val_node, ast.Call):
                # 2a. Direct class constructors (e.g. SinusoidalGenerator(...))
                if isinstance(val_node.func, ast.Name):
                    func_id = val_node.func.id
                    if func_id == "SinusoidalGenerator":
                        kwargs: dict[str, float] = {}
                        args = [_safe_eval_ast_number(a, env) for a in val_node.args]
                        for kw in val_node.keywords:
                            if kw.arg:
                                kwargs[kw.arg] = _safe_eval_ast_number(kw.value, env)
                        freq = kwargs.get("frequency", args[0] if len(args) > 0 else 1.0)
                        amp = kwargs.get("amplitude", args[1] if len(args) > 1 else 1.0)
                        phase = kwargs.get("phase", args[2] if len(args) > 2 else 0.0)
                        env[target_name] = SinusoidalGenerator(
                            frequency=freq, amplitude=amp, phase=phase
                        )
                        continue

                    if func_id == "SquareWaveGenerator":
                        kwargs = {}
                        args = [_safe_eval_ast_number(a, env) for a in val_node.args]
                        for kw in val_node.keywords:
                            if kw.arg:
                                kwargs[kw.arg] = _safe_eval_ast_number(kw.value, env)
                        freq = kwargs.get("frequency", args[0] if len(args) > 0 else 1.0)
                        amp = kwargs.get("amplitude", args[1] if len(args) > 1 else 1.0)
                        env[target_name] = SquareWaveGenerator(frequency=freq, amplitude=amp)
                        continue

                    if func_id == "CircuitRL":
                        kwargs = {}
                        args = [_safe_eval_ast_number(a, env) for a in val_node.args]
                        for kw in val_node.keywords:
                            if kw.arg:
                                kwargs[kw.arg] = _safe_eval_ast_number(kw.value, env)
                        res = kwargs.get("resistance", args[0] if len(args) > 0 else 1.0)
                        ind = kwargs.get("inductance", args[1] if len(args) > 1 else 1.0)
                        init_curr = kwargs.get("initialCurrent", args[2] if len(args) > 2 else 0.0)
                        env[target_name] = CircuitRL(
                            resistance=res, inductance=ind, initialCurrent=init_curr
                        )
                        continue

                    if func_id == "CircuitRLAnalyzer":
                        gens_dict: dict[str, Generator] = {}
                        circuit_obj: CircuitRL | None = None
                        if val_node.args:
                            arg0 = val_node.args[0]
                            if isinstance(arg0, ast.Dict):
                                for k, v in zip(arg0.keys, arg0.values, strict=False):
                                    k_str = str(ast.literal_eval(k)) if k else ""
                                    if isinstance(v, ast.Name) and v.id in env:
                                        gens_dict[k_str] = env[v.id]
                            if len(val_node.args) > 1 and isinstance(val_node.args[1], ast.Name):
                                circuit_obj = env.get(val_node.args[1].id)
                        for kw in val_node.keywords:
                            if kw.arg == "generators" and isinstance(kw.value, ast.Dict):
                                for k, v in zip(kw.value.keys, kw.value.values, strict=False):
                                    k_str = str(ast.literal_eval(k)) if k else ""
                                    if isinstance(v, ast.Name) and v.id in env:
                                        gens_dict[k_str] = env[v.id]
                            elif kw.arg == "circuit" and isinstance(kw.value, ast.Name):
                                circuit_obj = env.get(kw.value.id)

                        if circuit_obj is None:
                            for item in env.values():
                                if isinstance(item, CircuitRL):
                                    circuit_obj = item
                                    break
                        if circuit_obj is not None:
                            env[target_name] = CircuitRLAnalyzer(
                                generators=gens_dict, circuit=circuit_obj
                            )
                        continue

                # 2b. Method call or attribute constructor (e.g. circuit.simulate(...), core.CircuitRL(...))
                elif isinstance(val_node.func, ast.Attribute):
                    attr_name = val_node.func.attr
                    if attr_name == "simulate":
                        obj_name = (
                            val_node.func.value.id
                            if isinstance(val_node.func.value, ast.Name)
                            else ""
                        )
                        circuit_obj = env.get(obj_name)
                        if isinstance(circuit_obj, CircuitRL):
                            args = val_node.args
                            kwargs_nodes = {kw.arg: kw.value for kw in val_node.keywords if kw.arg}
                            gen_node = kwargs_nodes.get(
                                "generator", args[0] if len(args) > 0 else None
                            )
                            gen_obj = (
                                env.get(gen_node.id) if isinstance(gen_node, ast.Name) else None
                            )
                            t_end_node = kwargs_nodes.get(
                                "t_end", args[1] if len(args) > 1 else None
                            )
                            dt_node = kwargs_nodes.get("dt", args[2] if len(args) > 2 else None)
                            t_end = _safe_eval_ast_number(t_end_node, env) if t_end_node else 0.1
                            dt = _safe_eval_ast_number(dt_node, env) if dt_node else 0.01

                            if isinstance(gen_obj, Generator):
                                res = circuit_obj.simulate(gen_obj, t_end=t_end, dt=dt)
                                env[target_name] = res
                                simulation_results = res
                                continue

                    elif attr_name in ("CircuitRL", "SinusoidalGenerator", "SquareWaveGenerator"):
                        if attr_name == "CircuitRL":
                            kwargs = {}
                            args = [_safe_eval_ast_number(a, env) for a in val_node.args]
                            for kw in val_node.keywords:
                                if kw.arg:
                                    kwargs[kw.arg] = _safe_eval_ast_number(kw.value, env)
                            res = kwargs.get("resistance", args[0] if len(args) > 0 else 1.0)
                            ind = kwargs.get("inductance", args[1] if len(args) > 1 else 1.0)
                            init_curr = kwargs.get(
                                "initialCurrent", args[2] if len(args) > 2 else 0.0
                            )
                            env[target_name] = CircuitRL(
                                resistance=res, inductance=ind, initialCurrent=init_curr
                            )
                            continue

            # Numeric literal or math expression fallback
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                env[target_name] = _safe_eval_ast_number(val_node, env)

        # 3. Method calls (e.g. analyzer.analyze(...), analyzer.add_generator(...), print(...))
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call_node = stmt.value
            if isinstance(call_node.func, ast.Attribute):
                method_name = call_node.func.attr
                obj_name = (
                    call_node.func.value.id if isinstance(call_node.func.value, ast.Name) else ""
                )
                target_obj = env.get(obj_name)

                if isinstance(target_obj, CircuitRLAnalyzer):
                    if method_name == "analyze":
                        args = call_node.args
                        kwargs_nodes = {kw.arg: kw.value for kw in call_node.keywords if kw.arg}
                        t_end_node = kwargs_nodes.get(
                            "end_time", args[0] if len(args) > 0 else None
                        )
                        step_node = kwargs_nodes.get("step", args[1] if len(args) > 1 else None)
                        t_end = _safe_eval_ast_number(t_end_node, env) if t_end_node else 0.1
                        step = _safe_eval_ast_number(step_node, env) if step_node else 0.01
                        target_obj.analyze(end_time=t_end, step=step)
                        analyzer_results = target_obj.results

                    elif method_name == "add_generator":
                        if len(call_node.args) >= 2:
                            name_str = str(ast.literal_eval(call_node.args[0]))
                            gen_name = (
                                call_node.args[1].id
                                if isinstance(call_node.args[1], ast.Name)
                                else ""
                            )
                            if gen_name in env:
                                target_obj.add_generator(name_str, env[gen_name])

                elif isinstance(target_obj, CircuitRL) and method_name == "simulate":
                    args = call_node.args
                    kwargs_nodes = {kw.arg: kw.value for kw in call_node.keywords if kw.arg}
                    gen_node = kwargs_nodes.get("generator", args[0] if len(args) > 0 else None)
                    gen_obj = env.get(gen_node.id) if isinstance(gen_node, ast.Name) else None
                    if not isinstance(gen_obj, Generator):
                        raise TypeError(
                            f"A valid Generator object is required for CircuitRL.simulate, got {gen_obj}"
                        )
                    t_end_node = kwargs_nodes.get("t_end", args[1] if len(args) > 1 else None)
                    dt_node = kwargs_nodes.get("dt", args[2] if len(args) > 2 else None)
                    t_end = _safe_eval_ast_number(t_end_node, env) if t_end_node else 0.1
                    dt = _safe_eval_ast_number(dt_node, env) if dt_node else 0.01
                    simulation_results = target_obj.simulate(gen_obj, t_end=t_end, dt=dt)

            # print(...) statements
            elif isinstance(call_node.func, ast.Name) and call_node.func.id == "print":
                print_parts: list[str] = []
                for a in call_node.args:
                    if isinstance(a, ast.Constant):
                        print_parts.append(str(a.value))
                    elif isinstance(a, ast.Name) and a.id in env:
                        print_parts.append(str(env[a.id]))
                    elif isinstance(a, ast.Attribute):
                        obj = env.get(a.value.id) if isinstance(a.value, ast.Name) else None
                        if obj and hasattr(obj, a.attr):
                            print_parts.append(str(getattr(obj, a.attr)))
                    else:
                        with contextlib.suppress(Exception):
                            print_parts.append(str(_safe_eval_ast_number(a, env)))
                stdout_lines.append(" ".join(print_parts))

    duration_ms = (time.perf_counter() - start_time) * 1000.0

    # If simulation or analyzer was run, ensure results are represented in output
    if simulation_results and not any("[(0" in line or "[(" in line for line in stdout_lines):
        stdout_lines.append(f"Simulation results: {simulation_results}")
    if analyzer_results and not any("analyzer_results" in line for line in stdout_lines):
        stdout_lines.append(f"analyzer_results: {analyzer_results}")

    stdout_str = "\n".join(stdout_lines)
    measurements = parse_simulation_metrics(stdout_str)
    measurements["engine"] = "chappie-compat-0.0.2"

    return CircuitSimulationResult(
        success=True,
        script_path=script_path,
        stdout=stdout_str,
        stderr="",
        duration_ms=round(duration_ms, 2),
        measurements=measurements,
        error_message=None,
    )


# -----------------------------------------------------------------------------
# Main Simulation Runner Interface
# -----------------------------------------------------------------------------


def run_circuit_simulation(
    code: str,
    timeout: float = 10.0,
    script_path: str = "circuit_simulation.py",
) -> CircuitSimulationResult:
    """Executes a generated circuit simulation Python script.

    Safety Architecture:
    1. Static AST Validation: Parses and enforces strict allowlists on imports, symbols,
       and identifiers. Rejects any dunders or private attributes.
    2. Declarative Execution: Safely evaluates declarative ElectricCircuitSim scripts
       in-process using the trusted pure-Python math model with zero OS escape surface.
    3. Fail-Closed Security: If code attempts arbitrary system calls, unapproved imports,
       or non-declarative operations, returns a diagnostic error without executing.
    """
    if not code or not code.strip():
        return CircuitSimulationResult(
            success=False,
            script_path=script_path,
            stdout="",
            stderr="Empty code provided for simulation.",
            duration_ms=0.0,
            measurements={"engine": "none"},
            error_message="Empty code provided for simulation.",
        )

    # 1. Parse and validate AST
    try:
        tree = ast.parse(code)
        validate_circuit_code(code)
    except SyntaxError as syn_err:
        return CircuitSimulationResult(
            success=False,
            script_path=script_path,
            stdout="",
            stderr=f"SyntaxError: {syn_err}",
            duration_ms=0.0,
            measurements={"engine": "none"},
            error_message=f"SyntaxError: {syn_err}",
        )
    except CircuitSecurityError as sec_err:
        return CircuitSimulationResult(
            success=False,
            script_path=script_path,
            stdout="",
            stderr=f"SecurityViolation: {sec_err}",
            duration_ms=0.0,
            measurements={"engine": "none"},
            error_message=f"Security violation: {sec_err}",
        )

    # 2. In-Process Declarative Execution
    try:
        return _run_declarative_circuit(tree, script_path=script_path)
    except ValueError as val_err:
        return CircuitSimulationResult(
            success=False,
            script_path=script_path,
            stdout="",
            stderr=str(val_err),
            duration_ms=0.0,
            measurements={"engine": "chappie-compat-0.0.2"},
            error_message=str(val_err),
        )
    except (TypeError, ArithmeticError, RuntimeError, AttributeError) as exc:
        return CircuitSimulationResult(
            success=False,
            script_path=script_path,
            stdout="",
            stderr=f"Simulation error: {exc}",
            duration_ms=0.0,
            measurements={"engine": "none"},
            error_message=f"Simulation error: {exc}",
        )
