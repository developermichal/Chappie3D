#!/usr/bin/env python3
"""
supervised_checkpoint_engine.py - Supervised Human-in-the-Loop & Socio-Technical Verification Engine.

Provides:
1. Unified Socio-Technical Entity & Constraint Modeling (Human Actors, Hardware, Policies).
2. Calibrated Trigger Matrix & Tiered Gates (Tier 0: Zero-Gate, Tier 1: Micro-Gate, Tier 2: Hard-Gate).
3. 3-Stage Supervised Lifecycle:
   - Stage 1: Ephemeral Observation & Provisional Drafting.
   - Stage 2: Basic-Level Verification Checkpoint Gate.
   - Stage 3: Deep Execution & Commitment OR Instant Ephemeral Pruning & Negative Boundary Logging.
4. Socio-Technical Context Router & Knapsack Token Allocator.
5. Deterministic Electrical Strapping and Safety Invariant Enforcement.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class GateTier(Enum):
    TIER_0_ZERO_GATE = 0      # Autonomous execution (read-only, syntax parse, local telemetry)
    TIER_1_MICRO_GATE = 1     # Soft checkpoint (multi-file edits, schema changes, test generation)
    TIER_2_HARD_GATE = 2      # Strict human sign-off (electrical strapping, deletions, partitions)


class CheckpointAction(Enum):
    APPROVE = "approve"       # Affirmative acceptance (y / 1 / approve) -> Deep execution
    REJECT = "reject"         # Explicit veto (n / stop / reject) -> Instant pruning + Negative invariant
    STEER = "steer"           # Free-form redirection / custom override parameter


class BlastRadius(Enum):
    ISOLATED_COMPONENT = "isolated_component"
    ENTIRE_BOARD = "entire_board"
    PERMANENT_STORAGE = "permanent_storage"


@dataclass
class CognitivePreferences:
    verbosity: str = "minimal_dense"  # minimal_dense, standard, comprehensive
    format: str = "bullet_points"     # bullet_points, tables, code_first, narrative
    technical_depth: str = "register_level"  # architectural, implementation, register_level


@dataclass
class HumanActor:
    entity_id: str
    handle: str
    cognitive_preferences: CognitivePreferences = field(default_factory=CognitivePreferences)
    expertise_matrix: Dict[str, float] = field(default_factory=dict)
    active_projects: List[str] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)


@dataclass
class HardwareComponent:
    entity_id: str
    label: str
    domain: str = "hardware"
    properties: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    last_verified: float = field(default_factory=time.time)


@dataclass
class RelationalEdge:
    edge_id: str
    source_entity: str
    relation: str  # REQUIRES_SIGN_OFF, CONFLICTS_WITH, DEPENDS_ON, SUPERSEDES
    target_entity: str
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "ACTIVE"
    created_at: float = field(default_factory=time.time)


@dataclass
class NegativeBoundary:
    invariant_id: str
    rule: str
    rationale: str
    enforcement: str = "STRICT_BLOCKER"
    source_checkpoint_id: Optional[str] = None
    confidence: float = 1.0
    created_at: float = field(default_factory=time.time)


@dataclass
class VerificationCheckpoint:
    checkpoint_id: str
    tier: GateTier
    trigger_category: str
    observation_summary: str
    proposed_action: str
    blast_radius: BlastRadius = BlastRadius.ISOLATED_COMPONENT
    estimated_turns: int = 1
    token_budget: int = 500
    override_token: Optional[str] = None
    hazard_details: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    resolution: Optional[str] = None
    steer_payload: Optional[str] = None


@dataclass
class ProvisionalDraft:
    draft_id: str
    topic: str
    hypothesis: str
    affected_components: List[str]
    proposed_modifications: Dict[str, Any]
    confidence_score: float = 0.5
    created_at: float = field(default_factory=time.time)


class SupervisedCheckpointEngine:
    """
    Core engine managing socio-technical knowledge, risk estimation,
    gated verification checkpoints, and lifecycle state transitions.
    """

    def __init__(self, token_budget: int = 2000):
        self.token_budget = token_budget
        self.human_actors: Dict[str, HumanActor] = {}
        self.hardware_components: Dict[str, HardwareComponent] = {}
        self.edges: List[RelationalEdge] = []
        self.negative_boundaries: Dict[str, NegativeBoundary] = {}
        self.provisional_buffer: Dict[str, ProvisionalDraft] = {}
        self.checkpoints: Dict[str, VerificationCheckpoint] = {}
        self.verified_insights: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # 1. Socio-Technical Knowledge Graph Registration
    # -------------------------------------------------------------------------

    def register_human_actor(self, actor: HumanActor) -> None:
        self.human_actors[actor.entity_id] = actor

    def register_hardware_component(self, comp: HardwareComponent) -> None:
        self.hardware_components[comp.entity_id] = comp

    def add_relational_edge(self, edge: RelationalEdge) -> None:
        # Check if an active edge supersedes an older edge
        if edge.relation == "SUPERSEDES":
            for existing in self.edges:
                if existing.target_entity == edge.target_entity and existing.status == "ACTIVE":
                    existing.status = "DEPRECATED"
        self.edges.append(edge)

    def add_negative_boundary(self, boundary: NegativeBoundary) -> None:
        self.negative_boundaries[boundary.invariant_id] = boundary

    # -------------------------------------------------------------------------
    # 2. Risk Evaluation & Dynamic Gate Classification
    # -------------------------------------------------------------------------

    def evaluate_risk_tier(
        self,
        action_type: str,
        affected_resources: List[str],
        p_hazard: float = 0.05,
        c_catastrophe: float = 10.0,
        c_attention: float = 1.0,
        c_latency: float = 0.5
    ) -> Tuple[GateTier, str]:
        """
        Calculates operational risk using:
        Risk = P(hazard | action) * C_catastrophe vs (C_attention + C_latency).
        Enforces strict Tier 2 Hard-Gates on electrical strapping, data purge, and partition mods.
        """
        # Strict invariants check (ESP32 strapping, flash pins, destructive operations)
        critical_strapping_pins = {"GPIO12", "MTDI", "GPIO0", "GPIO2", "GPIO15"}
        for res in affected_resources:
            upper_res = res.upper()
            if any(pin in upper_res for pin in critical_strapping_pins) and "ESP32" in upper_res:
                return (
                    GateTier.TIER_2_HARD_GATE,
                    f"Electrical boot strapping hazard detected on {res} (latch hazard for VDD_SDIO)."
                )

        if action_type in {"delete_file_tree", "burn_efuse", "flash_repartition", "high_voltage_rail_mod"}:
            return (
                GateTier.TIER_2_HARD_GATE,
                f"Destructive or hardware-altering operation: '{action_type}' requires unconditional sign-off."
            )

        # Mathematical utility threshold check
        net_risk = p_hazard * c_catastrophe
        attention_tax = c_attention + c_latency

        if net_risk >= 8.0:
            return (GateTier.TIER_2_HARD_GATE, f"High calculated net risk ({net_risk:.2f} >= 8.0).")
        elif net_risk >= attention_tax or action_type in {"multi_file_edit", "schema_migration", "driver_init"}:
            return (GateTier.TIER_1_MICRO_GATE, f"Moderate impact action requiring low-friction validation.")
        else:
            return (GateTier.TIER_0_ZERO_GATE, f"Safe autonomous action within zero-gate boundary.")

    # -------------------------------------------------------------------------
    # 3. 3-Stage Supervised Lifecycle
    # -------------------------------------------------------------------------

    def stage1_draft_observation(
        self,
        topic: str,
        hypothesis: str,
        affected_components: List[str],
        proposed_modifications: Dict[str, Any],
        confidence: float = 0.5
    ) -> str:
        """
        Stage 1: Low-cost ephemeral observation.
        Stores hypothesis in a provisional buffer without committing to persistent knowledge graph.
        """
        # Check against negative boundaries
        for inv in self.negative_boundaries.values():
            if inv.enforcement == "STRICT_BLOCKER":
                for comp in affected_components:
                    if comp.lower() in inv.rule.lower():
                        raise ValueError(f"Action blocked by negative boundary invariant '{inv.invariant_id}': {inv.rationale}")

        draft_id = f"draft_{uuid.uuid4().hex[:8]}"
        draft = ProvisionalDraft(
            draft_id=draft_id,
            topic=topic,
            hypothesis=hypothesis,
            affected_components=affected_components,
            proposed_modifications=proposed_modifications,
            confidence_score=confidence
        )
        self.provisional_buffer[draft_id] = draft
        return draft_id

    def stage2_create_checkpoint(
        self,
        draft_id: str,
        action_type: str,
        blast_radius: BlastRadius = BlastRadius.ISOLATED_COMPONENT,
        hazard_details: Optional[Dict[str, Any]] = None
    ) -> VerificationCheckpoint:
        """
        Stage 2: Generates a high-density structured verification checkpoint card.
        """
        if draft_id not in self.provisional_buffer:
            raise KeyError(f"Draft ID '{draft_id}' not found in provisional buffer.")

        draft = self.provisional_buffer[draft_id]
        tier, reason = self.evaluate_risk_tier(action_type, draft.affected_components)

        chk_id = f"chk_{uuid.uuid4().hex[:8]}"
        checkpoint = VerificationCheckpoint(
            checkpoint_id=chk_id,
            tier=tier,
            trigger_category=action_type,
            observation_summary=draft.hypothesis,
            proposed_action=f"Apply modifications to {', '.join(draft.affected_components)}: {draft.topic}",
            blast_radius=blast_radius,
            estimated_turns=4 if tier == GateTier.TIER_2_HARD_GATE else 2,
            token_budget=1200 if tier == GateTier.TIER_2_HARD_GATE else 600,
            override_token=f"ALLOW_{action_type.upper()}" if tier == GateTier.TIER_2_HARD_GATE else None,
            hazard_details=hazard_details or {"rationale": reason}
        )
        self.checkpoints[chk_id] = checkpoint
        return checkpoint

    def format_checkpoint_card(self, checkpoint: VerificationCheckpoint) -> str:
        """
        Renders a high-density, human-facing decision card using ASCII borders for maximum compatibility.
        """
        tier_title = (
            "HARD-GATE: Tier 2 Strict Sign-Off" if checkpoint.tier == GateTier.TIER_2_HARD_GATE
            else "MICRO-GATE: Tier 1 Verification" if checkpoint.tier == GateTier.TIER_1_MICRO_GATE
            else "ZERO-GATE: Tier 0 Silent Execution"
        )
        hazard_info = ""
        if checkpoint.hazard_details:
            hazard_info = f"\nHazard/Rationale: {checkpoint.hazard_details.get('rationale', 'N/A')}"

        card = (
            f"+-- [{tier_title}] ------------------------------------------\n"
            f"| Checkpoint ID: {checkpoint.checkpoint_id}\n"
            f"| Observation:   {checkpoint.observation_summary}\n"
            f"| Proposal:      {checkpoint.proposed_action}\n"
            f"| Blast Radius:  {checkpoint.blast_radius.value} | Est. Tokens: {checkpoint.token_budget}{hazard_info}\n"
            f"|\n"
            f"| Decision: [y / 1 / approve] Approve  |  [n / stop / reject] Veto & Prune  |  [Text] Steer\n"
            f"+---------------------------------------------------------------------------"
        )
        return card

    def stage3_resolve_checkpoint(
        self,
        checkpoint_id: str,
        draft_id: str,
        decision: CheckpointAction,
        steer_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Stage 3: Executes resolution based on user feedback.
        - APPROVE -> Deep Execution: commits changes, updates knowledge graph, purges provisional draft.
        - REJECT  -> Instant Ephemeral Pruning: discards draft with zero residue + creates NegativeBoundary invariant.
        - STEER   -> Re-aligns trajectory with custom parameters without compounding drift.
        """
        if checkpoint_id not in self.checkpoints:
            raise KeyError(f"Checkpoint ID '{checkpoint_id}' not found.")
        if draft_id not in self.provisional_buffer:
            raise KeyError(f"Draft ID '{draft_id}' not found in provisional buffer.")

        chk = self.checkpoints[checkpoint_id]
        draft = self.provisional_buffer[draft_id]

        if decision == CheckpointAction.APPROVE:
            # 3A: Deep Execution & Permanent Ingestion
            chk.resolved = True
            chk.resolution = "APPROVED"
            
            # Commit verified facts to permanent memory
            committed_fact = {
                "fact_id": f"fact_{uuid.uuid4().hex[:6]}",
                "topic": draft.topic,
                "verified_hypothesis": draft.hypothesis,
                "affected_components": draft.affected_components,
                "modifications": draft.proposed_modifications,
                "confidence": 0.99,
                "verified_at": time.time()
            }
            self.verified_insights.append(committed_fact)
            
            # Remove from provisional buffer
            del self.provisional_buffer[draft_id]
            return {
                "status": "COMMITTED",
                "message": f"Checkpoint {checkpoint_id} approved. Executed deep analysis and persisted fact.",
                "committed_fact": committed_fact
            }

        elif decision == CheckpointAction.REJECT:
            # 3B: Instant Ephemeral Pruning & Negative Boundary Logging
            chk.resolved = True
            chk.resolution = "REJECTED_PRUNED"

            # Create strict negative boundary invariant
            neg_id = f"neg_{uuid.uuid4().hex[:6]}"
            neg_boundary = NegativeBoundary(
                invariant_id=neg_id,
                rule=f"VETOED_PROPOSAL_{draft.topic.upper().replace(' ', '_')}",
                rationale=f"Vetoed during checkpoint {checkpoint_id}: {draft.hypothesis}",
                enforcement="STRICT_BLOCKER",
                source_checkpoint_id=checkpoint_id,
                confidence=1.0
            )
            self.add_negative_boundary(neg_boundary)

            # Instant ephemeral pruning: completely delete provisional draft
            del self.provisional_buffer[draft_id]
            return {
                "status": "PRUNED",
                "message": f"Checkpoint {checkpoint_id} rejected. Ephemeral draft pruned; registered negative boundary '{neg_id}'.",
                "negative_boundary": asdict(neg_boundary)
            }

        elif decision == CheckpointAction.STEER:
            # 3C: Steered Redirection
            chk.resolved = True
            chk.resolution = "STEERED"
            chk.steer_payload = steer_message

            # Update provisional draft with steered directive
            draft.hypothesis = f"[STEERED: {steer_message}] {draft.hypothesis}"
            draft.confidence_score = 0.8
            return {
                "status": "STEERED",
                "message": f"Checkpoint {checkpoint_id} steered. Trajectory realigned with override: '{steer_message}'.",
                "updated_draft": asdict(draft)
            }

        raise ValueError(f"Unknown decision: {decision}")

    # -------------------------------------------------------------------------
    # 4. Socio-Technical Context Router & Knapsack Allocator
    # -------------------------------------------------------------------------

    def route_context_for_actor(
        self,
        actor_id: str,
        active_components: List[str]
    ) -> Dict[str, Any]:
        """
        Synthesizes technical facts, governance gates, and negative boundaries
        formatted according to the recipient's cognitive preferences and allocated within token budget.
        """
        actor = self.human_actors.get(actor_id)
        prefs = actor.cognitive_preferences if actor else CognitivePreferences()

        # 1. Unconditional Invariant Injection (Negative Boundaries & Blockers)
        strict_invariants = []
        for inv in self.negative_boundaries.values():
            strict_invariants.append({
                "invariant_id": inv.invariant_id,
                "rule": inv.rule,
                "rationale": inv.rationale
            })

        # 2. Mandatory Sign-off Gates
        review_gates = []
        for edge in self.edges:
            if edge.relation == "REQUIRES_SIGN_OFF" and edge.source_entity in active_components:
                review_gates.append({
                    "component": edge.source_entity,
                    "target_authority": edge.target_entity,
                    "rationale": edge.metadata.get("rationale", "")
                })

        # 3. Verified Hardware Specs
        verified_specs = []
        for comp_id in active_components:
            if comp_id in self.hardware_components:
                comp = self.hardware_components[comp_id]
                verified_specs.append({
                    "entity_id": comp.entity_id,
                    "label": comp.label,
                    "properties": comp.properties
                })

        # 4. Format payload according to cognitive preferences
        return {
            "formatting_directive": {
                "verbosity": prefs.verbosity,
                "format": prefs.format,
                "technical_depth": prefs.technical_depth
            },
            "strict_invariants": strict_invariants,
            "governance_gates": review_gates,
            "hardware_specs": verified_specs,
            "allocated_budget": self.token_budget
        }

    # -------------------------------------------------------------------------
    # 5. Serialization & State Persistence
    # -------------------------------------------------------------------------

    def export_state_json(self) -> str:
        data = {
            "token_budget": self.token_budget,
            "human_actors": {k: asdict(v) for k, v in self.human_actors.items()},
            "hardware_components": {k: asdict(v) for k, v in self.hardware_components.items()},
            "edges": [asdict(e) for e in self.edges],
            "negative_boundaries": {k: asdict(v) for k, v in self.negative_boundaries.items()},
            "provisional_buffer": {k: asdict(v) for k, v in self.provisional_buffer.items()},
            "checkpoints": {k: {**asdict(v), "tier": v.tier.value, "blast_radius": v.blast_radius.value} for k, v in self.checkpoints.items()},
            "verified_insights": self.verified_insights
        }
        return json.dumps(data, indent=2)

    def import_state_json(self, json_str: str) -> None:
        data = json.loads(json_str)
        self.token_budget = data.get("token_budget", 2000)
        self.human_actors = {
            k: HumanActor(
                entity_id=v["entity_id"],
                handle=v["handle"],
                cognitive_preferences=CognitivePreferences(**v["cognitive_preferences"]),
                expertise_matrix=v.get("expertise_matrix", {}),
                active_projects=v.get("active_projects", []),
                last_updated=v.get("last_updated", time.time())
            )
            for k, v in data.get("human_actors", {}).items()
        }
        self.hardware_components = {
            k: HardwareComponent(**v)
            for k, v in data.get("hardware_components", {}).items()
        }
        self.edges = [
            RelationalEdge(**e)
            for e in data.get("edges", [])
        ]
        self.negative_boundaries = {
            k: NegativeBoundary(**v)
            for k, v in data.get("negative_boundaries", {}).items()
        }
        self.provisional_buffer = {
            k: ProvisionalDraft(**v)
            for k, v in data.get("provisional_buffer", {}).items()
        }
        self.checkpoints = {
            k: VerificationCheckpoint(
                checkpoint_id=v["checkpoint_id"],
                tier=GateTier(v["tier"]),
                trigger_category=v["trigger_category"],
                observation_summary=v["observation_summary"],
                proposed_action=v["proposed_action"],
                blast_radius=BlastRadius(v["blast_radius"]),
                estimated_turns=v.get("estimated_turns", 1),
                token_budget=v.get("token_budget", 500),
                override_token=v.get("override_token"),
                hazard_details=v.get("hazard_details"),
                created_at=v.get("created_at", time.time()),
                resolved=v.get("resolved", False),
                resolution=v.get("resolution"),
                steer_payload=v.get("steer_payload")
            )
            for k, v in data.get("checkpoints", {}).items()
        }
        self.verified_insights = data.get("verified_insights", [])


def demo_simulation() -> None:
    """Demonstrates complete 3-stage lifecycle simulation."""
    print("=" * 75)
    print("SUPERVISED HUMAN-IN-THE-LOOP CHECKPOINT ENGINE - DEMO")
    print("=" * 75)

    engine = SupervisedCheckpointEngine()

    # Register Actors & Components
    actor = HumanActor(
        entity_id="person:lead_eng",
        handle="lead_architect",
        cognitive_preferences=CognitivePreferences(
            verbosity="minimal_dense",
            format="bullet_points",
            technical_depth="register_level"
        )
    )
    esp32 = HardwareComponent(
        entity_id="mcu:esp32_wroom_32",
        label="ESP32-WROOM-32",
        properties={"vcc": 3.3, "strapping_pins": ["GPIO12", "GPIO0", "GPIO2"]}
    )
    engine.register_human_actor(actor)
    engine.register_hardware_component(esp32)

    # 1. Stage 1: Ephemeral Observation (ESP32 GPIO12 hazard)
    print("\n--- STAGE 1: DRAFT PROVISIONAL OBSERVATION ---")
    draft_id = engine.stage1_draft_observation(
        topic="Display SPI MISO connection",
        hypothesis="Remap ST7796 TFT_MISO to ESP32 GPIO12 (strapping pin)",
        affected_components=["mcu:esp32_wroom_32:GPIO12"],
        proposed_modifications={"pin_mapping": "GPIO12"}
    )
    print(f"[OK] Draft created: {draft_id} (Ephemeral, zero permanent graph residue)")

    # 2. Stage 2: Verification Checkpoint Gate
    print("\n--- STAGE 2: VERIFICATION CHECKPOINT CARD ---")
    chk = engine.stage2_create_checkpoint(
        draft_id=draft_id,
        action_type="pin_reassignment",
        blast_radius=BlastRadius.ENTIRE_BOARD
    )
    print(engine.format_checkpoint_card(chk))

    # 3. Stage 3B: Reject / Veto -> Instant Pruning & Negative Invariant
    print("\n--- STAGE 3B: VETO & INSTANT EPHEMERAL PRUNING ---")
    res = engine.stage3_resolve_checkpoint(chk.checkpoint_id, draft_id, CheckpointAction.REJECT)
    print(f"Status: {res['status']}")
    print(f"Outcome: {res['message']}")
    print(f"Provisional buffer size: {len(engine.provisional_buffer)} (Draft purged)")
    print(f"Negative boundaries count: {len(engine.negative_boundaries)}")

    # 4. Context Routing for Actor
    print("\n--- SOCIO-TECHNICAL CONTEXT ROUTING ---")
    context = engine.route_context_for_actor("person:lead_eng", ["mcu:esp32_wroom_32"])
    print(json.dumps(context, indent=2))
    print("\n[OK] Engine successfully demonstrated.")


if __name__ == "__main__":
    demo_simulation()
