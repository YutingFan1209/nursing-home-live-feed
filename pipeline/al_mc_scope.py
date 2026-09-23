"""
Assisted living / memory care scope filter.
Rahul confirmed project scope is strictly SNF (skilled nursing facilities) —
see the 2026-07-23 AL/MC cleanup. This module is the reusable version of
that one-off audit query, for flagging future deals during ingestion.

Two tiers of operator, because "operator match alone" isn't safe for
everyone:
  - AL_MC_ONLY_OPERATORS: pure-play AL/MC companies. Any deal naming them
    is out of scope regardless of facility_names.
  - AL_MC_MIXED_OPERATORS: companies that run both SNF and AL/MC (e.g. LTC
    Properties, The Pennant Group). Blanket-flagging these would catch their
    legitimate SNF deals too, so they only count when facility_names itself
    contains an AL/MC term.
"""

AL_MC_TERMS = {
    "assisted living", "memory care", "independent living",
    "senior living community", "life plan community", "ccrc", "retirement",
}

AL_MC_ONLY_OPERATORS = {
    "Sunrise Senior Living",
    "Benchmark Senior Living",
    "Avanti Senior Living",
}

AL_MC_MIXED_OPERATORS = {
    "The Pennant Group",
    "Pennant Group",
    "LTC Properties",
}


def _has_al_mc_term(facility_names: list[str]) -> bool:
    return any(
        term in fn.lower()
        for fn in (facility_names or [])
        for term in AL_MC_TERMS
    )


def is_out_of_scope(deal: dict) -> bool:
    """True if this deal looks like assisted living / memory care, not SNF."""
    facility_names = deal.get("facility_names") or []
    names_to_check = list(deal.get("operator_names") or [])
    if deal.get("acquiring_entity"):
        names_to_check.append(deal["acquiring_entity"])

    if _has_al_mc_term(facility_names):
        return True

    for name in names_to_check:
        name_lower = name.lower()
        if any(op.lower() in name_lower for op in AL_MC_ONLY_OPERATORS):
            return True
        if any(op.lower() in name_lower for op in AL_MC_MIXED_OPERATORS):
            if _has_al_mc_term(facility_names):
                return True

    return False
