"""Interpret every engine validation channel; an empty violations array is not enough."""
from __future__ import annotations


def validation_issues(result: dict) -> list:
    issues = list(result.get("violations") or [])
    issues.extend(check for check in result.get("schema_checks", [])
                  if check.get("status") != "satisfied")
    # Functional constraints use open-world bodies: unknown is not a found
    # violation. A successful body/binding is evidence of a violated constraint.
    issues.extend(check for check in result.get("constraint_checks", [])
                  if check.get("status") not in {"unknown", "false"})
    return issues
