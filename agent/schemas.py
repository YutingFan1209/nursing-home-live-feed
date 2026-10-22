"""Verdict schema, shared by the loop (as the terminal tool) and verify.py."""

# Same vocabulary as pipeline/ucc_category.py, plus two outcomes the rules
# can't express: "not a nursing home deal" and "couldn't tell".
CATEGORIES = [
    "acquisition_financing",   # loan to a buyer around closing
    "refinancing",             # owner already in place well before the filing
    "operations_transfer",     # secured party is itself an operator (sale signal)
    "financing",               # a nursing home loan, but timing says nothing
    "not_a_nursing_home_deal", # equipment/vendor/consumer lien, wrong industry
    "unresolved",              # can't tell from the data -- send to a person
]

CONFIDENCE = ["high", "medium", "low"]
LINK_BASIS = ["direct", "name_similarity", "none"]

VERDICT_TOOL = {
    "name": "submit_verdict",
    "description": (
        "Submit your final answer for this filing. Call exactly once, last. "
        "Every claim in `evidence` must cite the step_id of a tool result "
        "that shows it. Leave facility_name/ccn/owner_entity null unless a "
        "tool result names them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORIES},
            "facility_name": {"type": ["string", "null"], "description": "CMS provider name, as a tool returned it."},
            "ccn": {"type": ["string", "null"], "description": "CMS Certification Number, as a tool returned it."},
            "owner_entity": {"type": ["string", "null"], "description": "Legal entity that owns/operates the facility, as a tool or the filing names it."},
            "link_basis": {
                "type": "string", "enum": LINK_BASIS,
                "description": (
                    "How the DEBTOR is tied to the facility you named. direct: a tool result itself shows it "
                    "(the debtor, or an entity the result names as its parent/affiliate, is the facility's CMS "
                    "owner, operator or licensed company). name_similarity: only shared name words, the same "
                    "lender, or records that sit side by side without saying they are related. none: no tie "
                    "(use when you name no facility)."),
            },
            "confidence": {"type": "string", "enum": CONFIDENCE},
            "needs_human_review": {"type": "boolean"},
            "reasoning": {"type": "string", "description": "2-4 sentences a researcher can read."},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string"},
                        "step_id": {"type": "string", "description": "step_id from the tool result that supports the claim"},
                    },
                    "required": ["claim", "step_id"],
                },
            },
        },
        "required": ["category", "link_basis", "confidence", "needs_human_review", "reasoning", "evidence"],
    },
}
