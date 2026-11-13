#!/usr/bin/env python3
"""Workspace Permissions CLI and Seeding Utility for Unified Agent Permissions."""

import argparse
import os
import sys

# Ensure project root is available on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import Session

from services.workspace.database import Base, SessionLocal, engine
from services.workspace.models import AgentPermission


def get_db_session() -> Session:
    """Create a new database session, ensuring tables exist."""
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def seed_defaults(db: Session, project_root_path: str | None = None):
    """Seed initial universal agent permissions."""
    print("[*] Checking/Seeding universal agent permissions...")

    defaults = [
        {
            "rule_type": "prompt_directive",
            "name": "system_baseline_instruction",
            "prompt_directive": "You are Chappie, an AI engineering assistant operating strictly within operator-approved boundaries.",
            "target_pattern": None,
            "constraints_json": None,
            "is_active": True,
        },
        {
            "rule_type": "prompt_directive",
            "name": "code_quality_guidelines",
            "prompt_directive": "Follow clean code practices, maintain high test coverage, and uphold system safety principles.",
            "target_pattern": None,
            "constraints_json": None,
            "is_active": True,
        },
        {
            "rule_type": "command",
            "name": "view_workspace",
            "prompt_directive": "Operator has granted capability to inspect authorized workspace directories.",
            "target_pattern": ".*",
            "constraints_json": '{"read_only": true}',
            "is_active": False,
        },
        {
            "rule_type": "whitelist_pattern",
            "name": "data_directory_access",
            "prompt_directive": None,
            "target_pattern": "^data/.*",
            "constraints_json": None,
            "is_active": False,
        },
    ]

    for item in defaults:
        existing = db.query(AgentPermission).filter(AgentPermission.name == item["name"]).first()
        if not existing:
            perm = AgentPermission(**item)
            db.add(perm)
            db.commit()
            print(f"[+] Created AgentPermission [{item['rule_type']}] '{item['name']}' (active={item['is_active']})")
        else:
            print(f"[=] Existing AgentPermission found: '{item['name']}'")

    print("[+] Seeding completed successfully.")


def list_permissions(db: Session):
    """Print all agent permissions."""
    rules = db.query(AgentPermission).order_by(AgentPermission.id.asc()).all()
    print("=" * 80)
    print("                 CHAPPIE AGENT PERMISSIONS REGISTRY                   ")
    print("=" * 80)
    if not rules:
        print("[i] No agent permissions configured.")
        return

    for r in rules:
        status_str = "ACTIVE" if r.is_active else "INACTIVE"
        print(f"\n[ID: {r.id}] [{r.rule_type.upper()}] {r.name} [{status_str}]")
        print(f"  Created: {r.created_at} | Updated: {r.updated_at}")
        if r.prompt_directive:
            print(f"  Directive: {r.prompt_directive}")
        if r.target_pattern:
            print(f"  Pattern:   {r.target_pattern}")
        if r.constraints_json:
            print(f"  Constraints: {r.constraints_json}")
    print("\n" + "=" * 80)


def add_rule(
    db: Session,
    rule_type: str,
    name: str,
    prompt_directive: str | None = None,
    target_pattern: str | None = None,
    constraints_json: str | None = None,
    is_active: bool = True,
):
    """Add a new agent permission rule."""
    existing = db.query(AgentPermission).filter(AgentPermission.name == name).first()
    if existing:
        print(f"[!] Rule already exists (ID={existing.id}): {name}")
        return existing
    rule = AgentPermission(
        rule_type=rule_type,
        name=name,
        prompt_directive=prompt_directive,
        target_pattern=target_pattern,
        constraints_json=constraints_json,
        is_active=is_active,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    print(f"[+] Added AgentPermission (ID={rule.id}): [{rule.rule_type}] {rule.name}")
    return rule


def remove_rule(db: Session, identifier: str):
    """Remove a rule by ID or name."""
    if identifier.isdigit():
        rule = db.query(AgentPermission).filter(AgentPermission.id == int(identifier)).first()
    else:
        rule = db.query(AgentPermission).filter(AgentPermission.name == identifier).first()

    if not rule:
        print(f"[!] Rule not found: {identifier}")
        return False

    rule_id = rule.id
    rule_name = rule.name
    db.delete(rule)
    db.commit()
    print(f"[+] Deleted AgentPermission (ID={rule_id}): {rule_name}")
    return True


def toggle_rule(db: Session, identifier: str, active: bool):
    """Activate or deactivate a rule."""
    if identifier.isdigit():
        rule = db.query(AgentPermission).filter(AgentPermission.id == int(identifier)).first()
    else:
        rule = db.query(AgentPermission).filter(AgentPermission.name == identifier).first()

    if not rule:
        print(f"[!] Rule not found: {identifier}")
        return False

    rule.is_active = active
    db.commit()
    status_str = "ACTIVE" if active else "INACTIVE"
    print(f"[+] Updated AgentPermission (ID={rule.id}) status to: {status_str}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Chappie Workspace Permissions Management CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # List command
    subparsers.add_parser("list", help="List all agent permissions")

    # Seed command
    subparsers.add_parser("seed", help="Seed default agent permissions")

    # Add rule command
    add_p = subparsers.add_parser("add-rule", help="Add an agent permission rule")
    add_p.add_argument("rule_type", choices=["command", "prompt_directive", "whitelist_pattern"], help="Rule type")
    add_p.add_argument("name", type=str, help="Descriptive name")
    add_p.add_argument("--prompt-directive", type=str, default=None, help="Prompt directive text")
    add_p.add_argument("--target-pattern", type=str, default=None, help="Target regex or glob pattern")
    add_p.add_argument("--constraints", type=str, default=None, help="JSON constraints")
    add_p.add_argument("--inactive", action="store_true", help="Set rule as inactive")

    # Remove rule command
    rm_p = subparsers.add_parser("remove-rule", help="Remove an agent permission rule by ID or name")
    rm_p.add_argument("identifier", type=str, help="Rule ID or name")

    # Activate / Deactivate rule
    act_p = subparsers.add_parser("activate-rule", help="Activate a rule")
    act_p.add_argument("identifier", type=str, help="Rule ID or name")

    deact_p = subparsers.add_parser("deactivate-rule", help="Deactivate a rule")
    deact_p.add_argument("identifier", type=str, help="Rule ID or name")

    args = parser.parse_args()

    if not args.command or args.command == "list":
        db = get_db_session()
        try:
            list_permissions(db)
        finally:
            db.close()
        return

    db = get_db_session()
    try:
        if args.command == "seed":
            seed_defaults(db)
        elif args.command == "add-rule":
            add_rule(
                db,
                rule_type=args.rule_type,
                name=args.name,
                prompt_directive=args.prompt_directive,
                target_pattern=args.target_pattern,
                constraints_json=args.constraints,
                is_active=not args.inactive,
            )
        elif args.command == "remove-rule":
            remove_rule(db, args.identifier)
        elif args.command == "activate-rule":
            toggle_rule(db, args.identifier, active=True)
        elif args.command == "deactivate-rule":
            toggle_rule(db, args.identifier, active=False)
    finally:
        db.close()


if __name__ == "__main__":
    main()
