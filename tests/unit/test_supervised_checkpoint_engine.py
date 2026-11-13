#!/usr/bin/env python3
"""
test_supervised_checkpoint_engine.py - Test suite for Supervised Checkpoint Engine.

Verifies:
- Risk calculation and Gate Tier classification (Tier 0, Tier 1, Tier 2 Hard-Gates).
- 3-Stage Supervised Lifecycle transitions (Draft -> Checkpoint -> Resolution).
- Instant ephemeral pruning and negative boundary invariant enforcement.
- Socio-technical routing and cognitive formatting rules.
- State export/import roundtrip serialization parity.
"""

import unittest
from utils.supervised_checkpoint_engine import (
    SupervisedCheckpointEngine,
    GateTier,
    CheckpointAction,
    BlastRadius,
    HumanActor,
    HardwareComponent,
    CognitivePreferences,
    RelationalEdge
)


class TestSupervisedCheckpointEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SupervisedCheckpointEngine(token_budget=1500)
        self.actor = HumanActor(
            entity_id="person:lead_eng",
            handle="lead_architect",
            cognitive_preferences=CognitivePreferences(
                verbosity="minimal_dense",
                format="tables",
                technical_depth="register_level"
            ),
            expertise_matrix={"embedded_c": 0.95, "pcb_design": 0.88}
        )
        self.esp32 = HardwareComponent(
            entity_id="mcu:esp32_wroom_32",
            label="ESP32-WROOM-32",
            properties={"vcc": 3.3, "flash_voltage_v": 3.3, "strapping_pins": ["GPIO12", "GPIO0"]}
        )
        self.engine.register_human_actor(self.actor)
        self.engine.register_hardware_component(self.esp32)

    def test_tier_0_zero_gate_autonomous_execution(self):
        tier, reason = self.engine.evaluate_risk_tier(
            action_type="read_symbol_table",
            affected_resources=["src/components/wifi/esp8266ex.ino"],
            p_hazard=0.01,
            c_catastrophe=1.0
        )
        self.assertEqual(tier, GateTier.TIER_0_ZERO_GATE)
        self.assertIn("Safe autonomous action", reason)

    def test_tier_1_micro_gate_trigger(self):
        tier, reason = self.engine.evaluate_risk_tier(
            action_type="multi_file_edit",
            affected_resources=["src/components/displays/tft_3_5_st7796/ST7796.md"]
        )
        self.assertEqual(tier, GateTier.TIER_1_MICRO_GATE)
        self.assertIn("Moderate impact", reason)

    def test_tier_2_hard_gate_strapping_hazard(self):
        tier, reason = self.engine.evaluate_risk_tier(
            action_type="pin_reassignment",
            affected_resources=["mcu:esp32_wroom_32:GPIO12"]
        )
        self.assertEqual(tier, GateTier.TIER_2_HARD_GATE)
        self.assertIn("Electrical boot strapping hazard", reason)

    def test_tier_2_hard_gate_destructive_action(self):
        tier, reason = self.engine.evaluate_risk_tier(
            action_type="delete_file_tree",
            affected_resources=["legacy_source_tree/"]
        )
        self.assertEqual(tier, GateTier.TIER_2_HARD_GATE)
        self.assertIn("Destructive or hardware-altering operation", reason)

    def test_stage_1_drafting_and_provisional_isolation(self):
        draft_id = self.engine.stage1_draft_observation(
            topic="SPI Clock Optimization",
            hypothesis="Raise SPI frequency to 40MHz on ST7796",
            affected_components=["mcu:esp32_wroom_32"],
            proposed_modifications={"spi_freq_mhz": 40}
        )
        self.assertIn(draft_id, self.engine.provisional_buffer)
        self.assertEqual(len(self.engine.verified_insights), 0)

    def test_stage_2_checkpoint_generation(self):
        draft_id = self.engine.stage1_draft_observation(
            topic="SPI Clock Optimization",
            hypothesis="Raise SPI frequency to 40MHz on ST7796",
            affected_components=["mcu:esp32_wroom_32"],
            proposed_modifications={"spi_freq_mhz": 40}
        )
        chk = self.engine.stage2_create_checkpoint(
            draft_id=draft_id,
            action_type="driver_init",
            blast_radius=BlastRadius.ISOLATED_COMPONENT
        )
        self.assertIn(chk.checkpoint_id, self.engine.checkpoints)
        self.assertEqual(chk.tier, GateTier.TIER_1_MICRO_GATE)
        formatted_card = self.engine.format_checkpoint_card(chk)
        self.assertIn("MICRO-GATE", formatted_card)
        self.assertIn("SPI Clock Optimization", formatted_card)

    def test_stage_3a_approval_and_permanent_commitment(self):
        draft_id = self.engine.stage1_draft_observation(
            topic="SPI Clock Optimization",
            hypothesis="Raise SPI frequency to 40MHz on ST7796",
            affected_components=["mcu:esp32_wroom_32"],
            proposed_modifications={"spi_freq_mhz": 40}
        )
        chk = self.engine.stage2_create_checkpoint(draft_id, "driver_init")
        res = self.engine.stage3_resolve_checkpoint(chk.checkpoint_id, draft_id, CheckpointAction.APPROVE)

        self.assertEqual(res["status"], "COMMITTED")
        self.assertEqual(len(self.engine.provisional_buffer), 0)
        self.assertEqual(len(self.engine.verified_insights), 1)
        self.assertEqual(self.engine.verified_insights[0]["topic"], "SPI Clock Optimization")

    def test_stage_3b_rejection_ephemeral_pruning_and_negative_boundary(self):
        draft_id = self.engine.stage1_draft_observation(
            topic="Use GPIO12 for TFT_MISO",
            hypothesis="Connect display MISO to ESP32 pin GPIO12",
            affected_components=["mcu:esp32_wroom_32:GPIO12"],
            proposed_modifications={"miso_pin": "GPIO12"}
        )
        chk = self.engine.stage2_create_checkpoint(draft_id, "pin_reassignment")
        res = self.engine.stage3_resolve_checkpoint(chk.checkpoint_id, draft_id, CheckpointAction.REJECT)

        self.assertEqual(res["status"], "PRUNED")
        self.assertEqual(len(self.engine.provisional_buffer), 0)
        self.assertEqual(len(self.engine.verified_insights), 0)
        self.assertEqual(len(self.engine.negative_boundaries), 1)

        # Confirm negative boundary prevents recurring proposal
        with self.assertRaises(ValueError) as ctx:
            self.engine.stage1_draft_observation(
                topic="Retry GPIO12 connection",
                hypothesis="Attempting again to connect GPIO12",
                affected_components=["mcu:esp32_wroom_32:GPIO12", "USE_GPIO12_FOR_TFT_MISO"],
                proposed_modifications={"miso_pin": "GPIO12"}
            )
        self.assertIn("blocked by negative boundary", str(ctx.exception))

    def test_stage_3c_steer_and_trajectory_realignment(self):
        draft_id = self.engine.stage1_draft_observation(
            topic="Display Pinout Setup",
            hypothesis="Assign TFT_MISO to GPIO12",
            affected_components=["mcu:esp32_wroom_32"],
            proposed_modifications={"miso_pin": "GPIO12"}
        )
        chk = self.engine.stage2_create_checkpoint(draft_id, "pin_reassignment")
        res = self.engine.stage3_resolve_checkpoint(
            chk.checkpoint_id,
            draft_id,
            CheckpointAction.STEER,
            steer_message="Use GPIO19 instead of GPIO12"
        )
        self.assertEqual(res["status"], "STEERED")
        self.assertIn("GPIO19", self.engine.provisional_buffer[draft_id].hypothesis)

    def test_relational_edge_supersedes_behavior(self):
        edge1 = RelationalEdge(
            edge_id="e1",
            source_entity="comp:st7796",
            relation="CONFIGURES",
            target_entity="spi_bus_default"
        )
        edge2 = RelationalEdge(
            edge_id="e2",
            source_entity="comp:st7796_v2",
            relation="SUPERSEDES",
            target_entity="spi_bus_default"
        )
        self.engine.add_relational_edge(edge1)
        self.assertEqual(edge1.status, "ACTIVE")
        self.engine.add_relational_edge(edge2)
        self.assertEqual(edge1.status, "DEPRECATED")

    def test_socio_technical_context_routing_and_preferences(self):
        gate_edge = RelationalEdge(
            edge_id="gate_esp32_strapping",
            source_entity="mcu:esp32_wroom_32",
            relation="REQUIRES_SIGN_OFF",
            target_entity="person:lead_eng",
            metadata={"rationale": "High-risk strapping pin change"}
        )
        self.engine.add_relational_edge(gate_edge)
        context = self.engine.route_context_for_actor("person:lead_eng", ["mcu:esp32_wroom_32"])

        self.assertEqual(context["formatting_directive"]["format"], "tables")
        self.assertEqual(context["formatting_directive"]["technical_depth"], "register_level")
        self.assertEqual(len(context["governance_gates"]), 1)
        self.assertEqual(context["governance_gates"][0]["target_authority"], "person:lead_eng")
        self.assertEqual(len(context["hardware_specs"]), 1)

    def test_json_export_and_import_state_parity(self):
        draft_id = self.engine.stage1_draft_observation(
            topic="Test Draft",
            hypothesis="Hypothesis test",
            affected_components=["mcu:esp32_wroom_32"],
            proposed_modifications={}
        )
        chk = self.engine.stage2_create_checkpoint(draft_id, "driver_init")
        json_dump = self.engine.export_state_json()

        new_engine = SupervisedCheckpointEngine()
        new_engine.import_state_json(json_dump)

        self.assertIn("person:lead_eng", new_engine.human_actors)
        self.assertIn("mcu:esp32_wroom_32", new_engine.hardware_components)
        self.assertIn(draft_id, new_engine.provisional_buffer)
        self.assertIn(chk.checkpoint_id, new_engine.checkpoints)


if __name__ == "__main__":
    unittest.main()
