"""Tests for Electronics & Mechatronics Profile, OpenSCAD rules, and Agent integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chappie.agent import CodingAgent
from chappie.config import Settings
from chappie.electronics_standard import (
    ELECTRONICS_PROFILE_DESCRIPTION,
    ELECTRONICS_PROFILE_ID,
    ELECTRONICS_PROFILE_NAME,
    get_electronics_agent_prompt_context,
    get_electronics_mechatronics_guide,
    get_openscad_3d_enclosure_guide,
    is_electronics_profile,
)
from chappie.models import AgentTaskRequest, BookProfile
from chappie.profiles import ProfileStorage
from chappie.workspace_client import LocalPermissionsStorage


def test_is_electronics_profile():
    """Test identification of electronics profile by id, name, or keywords."""
    assert is_electronics_profile(None, ELECTRONICS_PROFILE_ID) is True
    assert is_electronics_profile("Elektronik / Mechatronik (OpenSCAD & 3D Standard)") is True
    assert is_electronics_profile("Inżynieria elektronika i mechatroniki") is True
    assert is_electronics_profile("Projekt obudowy OpenSCAD") is True
    assert is_electronics_profile("Frontend React Developer") is False
    assert is_electronics_profile(None, None) is False


def test_get_guides():
    """Verify textbook guide and OpenSCAD 3D guide generation."""
    elec_guide = get_electronics_mechatronics_guide()
    assert "Kirchhoff" in elec_guide
    assert "Ohm" in elec_guide
    assert "MOSFET" in elec_guide
    assert "RP2040" in elec_guide
    assert "SG90" in elec_guide

    cad_guide = get_openscad_3d_enclosure_guide()
    assert "SOLID_FLOOR_T = 1.30 mm" in cad_guide
    assert "BOTTOM_CHAMFER = 0.40 mm" in cad_guide
    assert "WALL_T = 2.20 mm" in cad_guide
    assert "PCB_CLEARANCE = 0.35 mm" in cad_guide
    assert "DOGBONE_R = 0.60 mm" in cad_guide
    assert "Zero Supports" in cad_guide

    combined = get_electronics_agent_prompt_context()
    assert "Inżyniera Elektronika i Mechatronika" in combined
    assert "OpenSCAD" in combined


def test_profile_storage_seeds_electronics_profile(tmp_path):
    """Verify that ProfileStorage supports getting and seeding the electronics profile."""
    profiles_path = tmp_path / "profiles.json"
    storage = ProfileStorage(profiles_path)
    # Built-in profile can be retrieved directly
    elec = storage.get_profile(ELECTRONICS_PROFILE_ID)
    assert elec is not None
    assert elec.name == ELECTRONICS_PROFILE_NAME
    assert elec.description == ELECTRONICS_PROFILE_DESCRIPTION

    # And can be seeded into persistent storage
    storage.ensure_default_profiles()
    profiles = storage.list_profiles()
    assert any(p.id == ELECTRONICS_PROFILE_ID for p in profiles)


def test_workspace_seeds_openscad_permissions(tmp_path):
    """Verify that LocalPermissionsStorage seeds openscad permissions."""
    db_path = tmp_path / "permissions.db"
    storage = LocalPermissionsStorage(db_path)
    summary = storage.get_active_rules()
    assert (
        "Allow OpenSCAD 3D CAD modeling, rendering and export for enclosures"
        in summary.prompt_directives
    )
    assert "openscad" in summary.allowed_commands
    assert "openscad_runner" in summary.allowed_commands


class FakeElectronicsModel:
    def __init__(self):
        self.captured_prompt = None

    async def ainvoke(self, messages):
        for m in messages:
            if hasattr(m, "content") and "User task:" in str(m.content):
                self.captured_prompt = str(m.content)
        mock_msg = MagicMock()
        mock_msg.content = '{"answer": "Projekt obudowy i obliczenia", "rationale": ["Zgodność ze standardem"], "files": [{"path": "enclosure.scad", "content": "cube([10,10,10]);", "language": "scad", "purpose": "obudowa"}]}'
        return mock_msg


@pytest.mark.asyncio
async def test_agent_with_electronics_profile_injects_context():
    """Verify agent injects electronics textbook and OpenSCAD 3D rules when electronics profile is active."""
    settings = Settings(
        enable_circuit_simulation=True,
    )
    knowledge = MagicMock()
    knowledge.search = AsyncMock(return_value=[])

    profiles_mock = MagicMock()
    profiles_mock.get_profile = MagicMock(
        return_value=BookProfile(
            id=ELECTRONICS_PROFILE_ID,
            name=ELECTRONICS_PROFILE_NAME,
            description=ELECTRONICS_PROFILE_DESCRIPTION,
            document_ids=[],
            created_at="2026-10-25T00:00:00Z",
            updated_at="2026-10-25T00:00:00Z",
        )
    )

    agent = CodingAgent(
        settings=settings,
        knowledge=knowledge,
        profiles=profiles_mock,
    )

    fake_model = FakeElectronicsModel()
    agent.llm = fake_model

    req = AgentTaskRequest(
        task="Zaprojektuj obudowę dla Raspberry Pi Pico z czujnikiem i przekaźnikiem.",
        profile_id=ELECTRONICS_PROFILE_ID,
    )

    response = await agent.run(req)
    assert response is not None
    assert fake_model.captured_prompt is not None
    assert (
        "Active Specialized Engineering Profile: Elektronik / Mechatronik"
        in fake_model.captured_prompt
    )
    assert "Kirchhoff" in fake_model.captured_prompt
    assert "SOLID_FLOOR_T = 1.30 mm" in fake_model.captured_prompt
    assert "OpenSCAD" in fake_model.captured_prompt
