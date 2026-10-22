"""Versioned clinical-concept registry loader.

The bundled registry is an engineering-only vocabulary. It deliberately carries null clinical sources,
unmapped external identifiers, and unreviewed status until qualified reviewers provide those records.
"""
import json
from functools import lru_cache

from ..config import get_settings


def _validate(data: dict) -> None:
    for key in ("catalog_version", "status", "terminology", "review", "concepts", "protocol_version", "protocol_source", "rule_ids"):
        if key not in data:
            raise ValueError(f"knowledge catalog missing '{key}'")
    ids = [c.get("concept_id") for c in data["concepts"]]
    if not all(isinstance(i, str) and i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("knowledge catalog concept identifiers must be unique and nonempty")
    for obj in [data["review"], data["protocol_source"], *[c for c in data["concepts"]]]:
        approved = bool(obj.get("clinically_approved"))
        if approved and (not obj.get("source_organisation") or not obj.get("source_reference") or not obj.get("effective_date")):
            raise ValueError("approved knowledge needs a source organisation, reference, and effective date")


@lru_cache(maxsize=4)
def _load(path: str, modified: float) -> dict:
    data = json.loads(open(path, encoding="utf-8").read())
    _validate(data)
    return data


def active_catalog() -> dict:
    path = get_settings().knowledge_path
    return _load(str(path), path.stat().st_mtime)


def catalog_status(ruleset: dict) -> dict:
    data = active_catalog()
    return {"version": data["catalog_version"], "status": data["status"], "purpose": data["purpose"],
        "clinically_approved": data["review"]["clinically_approved"], "review_status": data["review"]["review_status"],
        "source_organisation": data["review"]["source_organisation"], "source_reference": data["review"]["source_reference"],
        "effective_date": data["review"]["effective_date"], "terminology": data["terminology"],
        "concept_count": len(data["concepts"]), "external_mappings": sum(1 for c in data["concepts"] if c["external_terminology"]),
        "protocol_version": data["protocol_version"], "ruleset_version": ruleset["version"],
        "matches_active_ruleset": data["protocol_version"] == ruleset["version"],
        "rule_ids_match": set(data["rule_ids"]) == {r["id"] for r in ruleset["urgent_rules"]}, "note": data["review"]["note"]}
