"""Electronics & Mechatronics Engineering standards and OpenSCAD 3D Enclosure rules.

This module provides formal textbook-level engineering guidance for electronics and
mechatronics, and generates targeted prompt context for circuit analysis and parametric OpenSCAD
enclosure modeling for the Bambu Lab A1 Mini platform.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ELECTRONICS_PROFILE_ID = "profile-elektronik"
ELECTRONICS_PROFILE_NAME = "Elektronik / Mechatronik (OpenSCAD & 3D Standard)"
ELECTRONICS_PROFILE_DESCRIPTION = (
    "Profil inżyniera elektronika i mechatronika: książkowe obliczenia obwodów, "
    "prawa Kirchhoffa i Ohma, dobór komponentów, mikrokontrolery (RP2040/STM32/ESP32) oraz "
    "projektowanie obudów urządzeń w OpenSCAD zgodnie ze standardem 3D (Bambu Lab A1 Mini / Zero Supports)."
)


def is_electronics_profile(profile_name: str | None, profile_id: str | None = None) -> bool:
    """Check if the given profile identifier or name corresponds to the electronics engineer profile."""
    if profile_id == ELECTRONICS_PROFILE_ID:
        return True
    if not profile_name:
        return False
    p_lower = profile_name.strip().lower()
    return any(
        kw in p_lower
        for kw in [
            "elektronik",
            "electronics",
            "mechatronik",
            "mechatronics",
            "openscad",
            "3d_standard",
            "obudow",
        ]
    )


def get_electronics_mechatronics_guide() -> str:
    """Return formal textbook engineering guidelines for electronics and mechatronics."""
    return """### Podręcznikowe Standardy Inżynierii Elektronicznej i Mechatronicznej (Textbook Rigor):
1. **Ścisłe Obliczenia Teoretyczne i Wzory**:
   - Prawa Kirchhoffa (I prawo - prądowe KCL, II prawo - napięciowe KVL).
   - Prawo Ohma ($U = I \\cdot R$), moc wydzielana ($P = U \\cdot I = I^2 \\cdot R = \\frac{U^2}{R}$).
   - Dzielniki napięcia i prądu: $U_{out} = U_{in} \\cdot \\frac{R_2}{R_1 + R_2}$.
   - Obwody prądu przemiennego (AC), reaktancje ($X_L = 2\\pi f L$, $X_C = \\frac{1}{2\\pi f C}$), impedancja zespolona ($Z = R + j(X_L - X_C)$).
   - Częstotliwości graniczne filtrów RC/RL: $f_c = \\frac{1}{2\\pi R C}$, $f_c = \\frac{R}{2\\pi L}$.
   - Stany nieustalone (odpowiedź skokowa, stałe czasowe $\\tau = RC$, $\\tau = L/R$).

2. **Półprzewodniki, Sterowanie i Zasilanie**:
   - Tranzystory BJT: punkt pracy, prąd bazy $I_B = \\frac{I_C}{\\beta}$, nasycenie ($I_B \\ge 2..5 \\cdot \\frac{I_C}{\\beta}$).
   - Tranzystory MOSFET: $V_{GS(th)}$, $R_{DS(on)}$, pojemność bramki $C_{iss}$, rezystor bramkowy tłumiący oscylacje ($R_G = 10..100\\ \\Omega$), rezystor pull-down ($10..100\\text{ k}\\Omega$).
   - Diody zabezpieczające flyback (Schottky / impulsowe) przy obciążeniach indukcyjnych (cewki, przekaźniki, silniki).
   - Stabilizatory napięcia LDO i przetwornice impulsowe Buck/Boost (bilans sprawności, tętnienia, dobór cewek i kondensatorów filtrujących low-ESR).
   - Bilans mocy i odprowadzanie ciepła ($T_j = T_a + P_D \\cdot (R_{\\theta JC} + R_{\\theta CS} + R_{\\theta SA})$).

3. **Mechatronika, Sensory i Mikrokontrolery**:
   - Mikrokontrolery: Raspberry Pi Pico (RP2040), STM32, ESP32, Arduino.
   - Peryferia magistralowe: I2C (rezystory podciągające pull-up $2.2\\text{ k}\\Omega..4.7\\text{ k}\\Omega$), SPI (CS, SCK, MOSI, MISO), UART, PWM, przetworniki ADC z dzielnikami dopasowującymi poziomy napięć ($3.3\\text{V} \\leftrightarrow 5\\text{V}$).
   - Aktuatory: Serwonapędy (SG90, MG90S - sterowanie PWM 50 Hz, wypełnienie 1.0-2.0 ms), silniki krokowe (A4988, DRV8825, TMC2209), silniki DC (mostek H L298N / TB6612FNG).
   - Odsprzęganie zasilania: kondensatory ceramiczne $100\\text{ nF}$ blisko każdego pinu VDD mikrokontrolera oraz elektrolityczne/tantalowe $10..100\\ \\mu\\text{F}$ na wejściu zasilania modułu.
   - Schematy połączeń: zawsze podawaj czytelną tabelę pinoutu (Pinout Mapping Table) oraz schemat blokowy i logiczny połączeń."""


def get_openscad_3d_enclosure_guide() -> str:
    """Return parametric OpenSCAD enclosure design guidelines."""
    return """### Kanoniczny Standard Projektowania Obudów 3D w OpenSCAD (Bambu Lab A1 Mini / Code-CAD SSOT):
Dla obudów urządzeń elektronicznych generuj czysty, parametryczny kod OpenSCAD (.scad):

1. **Płaszczyzna Styku i Pierwsza Warstwa (100% Flat PEI Contact)**:
   - Lita, ciągła podłoga o grubości nominalnej `SOLID_FLOOR_T = 1.30 mm` na Z=0.
   - Bezwzględny zakaz otworów na wylot rozbijających 1. warstwę na wysepki (Through-cut prohibition).
   - Faza dolna `BOTTOM_CHAMFER = 0.40 mm x 45°` na dolnej krawędzi obwodowej (kompensacja Elephant Foot).

2. **Ścianki Nośne i Narożniki**:
   - Grubość ścianek nośnych bazy `WALL_T = 2.20 mm`.
   - Zaokrąglenie narożników zewnętrznych `OUTER_R = 3.00 mm`, wewnętrznych `INNER_R = 2.00 mm`.
   - Faza górna pokrywy `LID_CHAMFER = 0.80 mm x 45°`.

3. **Luz Montażowy i Podcięcia PCB (Zero Deburring)**:
   - Luz montażowy FDM na stronę dla PCB: `PCB_CLEARANCE = 0.35 mm`.
   - Podcięcia narożne Dogbone `DOGBONE_R = 0.60 mm` we wszystkich narożnikach 90° kieszeni PCB (kompensacja promienia dyszy 0.40 mm).
   - Komora ulgowa pod luty SMD na spodzie: głębokość `SMD_DEPTH = 1.20 mm`, półka obwodowa `SHELF_W = 1.20 mm`.

4. **Słupki Montażowe i Śruby**:
   - Słupki główne obudowy DIN 7991 (M2.5/M3): średnica zewnętrzna 7.00 mm, otwór rdzeniowy 2.80 mm (M3) lub 2.20 mm (M2.5), głębokość 12.00 mm.
   - Słupki montażowe mikrokontrolera (RPi 3A+ / Pico / Zero): prześwit podłogowy 3.00 mm, średnica 6.00 mm, otwór 2.20 mm (wkręt M2.5).

5. **Druk w 100% bez podpór (Zero Supports <= 45°)**:
   - Wszystkie nawisy, żaluzje wentylacyjne i fazy portów (USB, HDMI, zasilanie) muszą mieć kąt samonośny `<= 45°`.
   - Żaluzje wentylacyjne pod kątem 45°.

6. **Pasowanie Pokrywy z Bazą (Interlocking Lip & Groove)**:
   - Wysokość kołnierza pozycjonującego: `LIP_H = 1.50 mm`.
   - Luz pasowania na stronę: `LIP_CLEARANCE = 0.25 mm`.

7. **Parametryzacja i Struktura Pliku OpenSCAD**:
   - Używaj `$fn = 60;` dla gładkich otworów i zaokrągleń.
   - Generuj osobne moduły `base_enclosure()` oraz `top_lid()` z możliwością selektywnego renderowania (`part = "both"; // ["base", "lid", "both"]`).
   - Zapisuj pliki modeli bezpośrednio w strukturze wolumenu roboczego."""


def get_electronics_agent_prompt_context() -> str:
    """Generate combined context for the Electronics & Mechatronics Engineer profile."""
    elec_guide = get_electronics_mechatronics_guide()
    cad_guide = get_openscad_3d_enclosure_guide()
    return (
        f"{elec_guide}\n\n"
        f"{cad_guide}\n\n"
        "### Zasady pracy Profilu Inżyniera Elektronika i Mechatronika:\n"
        "- Rozwiązuj zadania z pełną ścisłością inżynierską (książkowe podejście, wzory, obliczenia prądowo-napięciowe, bilans cieplny).\n"
        "- Projektując obudowy urządzeń, dostarczaj kompletny, parametryczny kod OpenSCAD (.scad) i/lub CadQuery (.py).\n"
        "- Wszystkie wygenerowane pliki modeli 3D, skryptów i dokumentacji zapisuj w odpowiednich relatywnych ścieżkach w strukturze projektu/wolumenu."
    )
