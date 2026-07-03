"""Executable checks for the P0-05 safety policy artifact."""

import json
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).resolve().parents[3] / "packages/eval/policy/safety_policy.json"

REQUIRED_REFUSAL_CATEGORIES = {
    "diagnosis",
    "treatment_instructions",
    "medication_supplement_dosing",
    "emergency_medical_triage",
    "injury_rehab_protocols",
    "sexual_dysfunction_dx_tx",
    "trend_proves_condition",
}


def _load_policy() -> dict[str, Any]:
    with POLICY_PATH.open(encoding="utf-8") as policy_file:
        data = json.load(policy_file)
    assert isinstance(data, dict)
    return data


def test_safety_policy_schema_validates() -> None:
    policy = _load_policy()

    assert isinstance(policy.get("policy_version"), str)
    assert policy["policy_version"]
    assert isinstance(policy.get("schema_version"), str)
    assert policy["schema_version"] == "1.0"

    product_boundary = policy.get("product_boundary")
    assert isinstance(product_boundary, dict)
    assert product_boundary.get("positioning") == "wellness_decision_support"
    assert product_boundary.get("not_medical_advice") is True
    assert isinstance(product_boundary.get("forbidden_claims"), list)
    assert product_boundary["forbidden_claims"]
    assert all(isinstance(claim, str) and claim for claim in product_boundary["forbidden_claims"])
    assert isinstance(product_boundary.get("default_disclaimer"), str)
    assert product_boundary["default_disclaimer"]

    allowed_behaviors = policy.get("allowed_behaviors")
    assert isinstance(allowed_behaviors, list)
    assert allowed_behaviors
    for behavior in allowed_behaviors:
        assert isinstance(behavior, dict)
        assert isinstance(behavior.get("id"), str)
        assert behavior["id"]
        assert isinstance(behavior.get("description"), str)
        assert behavior["description"]

    disclaimers = policy.get("required_disclaimers")
    assert isinstance(disclaimers, dict)
    assert {"wellness_boundary", "clinician_consult", "emergency_help"} <= set(disclaimers)
    assert all(isinstance(value, str) and value for value in disclaimers.values())

    confidence_policy_refs = policy.get("confidence_policy_refs")
    assert isinstance(confidence_policy_refs, dict)
    assert confidence_policy_refs == {
        "source_doc": "docs/safety/confidence-policy.md",
        "confidence_reduction_prd_ref": "19.7",
        "conservative_recommendation_prd_ref": "19.7",
    }

    categories = policy.get("refusal_categories")
    assert isinstance(categories, list)
    assert categories

    for category in categories:
        assert isinstance(category, dict)
        assert isinstance(category.get("id"), str)
        assert category["id"]
        assert category.get("prd_ref") == "19.6"
        assert category.get("action") in {"refuse", "redirect"}
        assert isinstance(category.get("description"), str)
        assert category["description"]
        assert isinstance(category.get("trigger_patterns"), list)
        assert category["trigger_patterns"]
        assert all(isinstance(pattern, str) and pattern for pattern in category["trigger_patterns"])
        assert isinstance(category.get("safe_redirect"), str)
        assert category["safe_redirect"]
        assert isinstance(category.get("escalation"), str)
        assert category["escalation"]
        assert isinstance(category.get("required_disclaimers"), list)
        assert category["required_disclaimers"]
        assert set(category["required_disclaimers"]) <= set(disclaimers)


def test_all_prd_19_6_refusal_categories_are_represented() -> None:
    policy = _load_policy()
    category_ids = {category["id"] for category in policy["refusal_categories"]}

    assert category_ids >= REQUIRED_REFUSAL_CATEGORIES

    for category in policy["refusal_categories"]:
        if category["id"] in REQUIRED_REFUSAL_CATEGORIES:
            assert category["trigger_patterns"]
            assert category["safe_redirect"] or category["escalation"]
