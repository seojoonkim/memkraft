"""The pure execution projection and the single transition table (plan §4.2, §4.6, §4.7).

``project`` is a pure function of the log records and the injected ``now``: no
wall clock, no environment, no filesystem. Records are ordered by
``(event_seq, id)`` — timestamps are data, never sort keys — so a shuffled file
projects identically to an ordered one.

Every state machine lives in ``_TRANSITIONS``, one dict keyed by
``(entity_kind, from_status, to_status)``. This is a hard exit criterion of the
slice: expressing the machines as branching instead of data is what makes later
guards drift apart, so a test rejects any ``if``/``elif`` chain over status
literals in this module.

Two counters are never merged (§4.7). ``skipped`` counts IO-layer damage —
corrupt lines the store could not parse — and leaves the projection consistent.
``rejected_transitions`` counts semantic damage — a transition against an
undeclared id, or a triple absent from ``_TRANSITIONS`` — and sets
``consistent: false``.

Zero dependencies — stdlib only.
"""
from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from .execution_protocol import canonical_timestamp, digest

__all__ = ["project"]

EXECUTION_SCHEMA = 1


class _Rule(NamedTuple):
    """One declared transition.

    ``requires`` names the record fields the *apply* path must find present
    before it appends the transition (§4.6). The projection itself never reads
    them: a record already in the log is history, and history is replayed, not
    re-validated.
    """

    requires: Tuple[str, ...] = ()


#: The state machines of §4.6, as data. Any triple absent from this dict is
#: rejected, which is what makes ``waived`` absorbing and every unlisted pair
#: fail closed without a single branch.
_TRANSITIONS: Dict[Tuple[str, str, str], _Rule] = {
    ("goal", "open", "satisfied"): _Rule(),
    ("goal", "open", "abandoned"): _Rule(("reason",)),

    ("gate", "pending", "passed"): _Rule(),
    ("gate", "pending", "failed"): _Rule(),
    ("gate", "pending", "waived"): _Rule(),
    ("gate", "passed", "pending"): _Rule(("reopen_reason",)),
    ("gate", "failed", "pending"): _Rule(("reopen_reason",)),
    ("gate", "failed", "passed"): _Rule(("reopen_reason",)),
    ("gate", "failed", "waived"): _Rule(),

    ("handoff", "offered", "accepted"): _Rule(),
    ("handoff", "accepted", "completed"): _Rule(),
}

# Record type → (entity kind, identity field, initial status).
_DECLARED_KINDS = {
    "goal_declared": ("goal", None, "open"),
    "gate_declared": ("gate", "gate_id", "pending"),
    "handoff_declared": ("handoff", "handoff_id", "offered"),
}

# Record type → (entity kind, identity field).
_CHANGE_KINDS = {
    "goal_transition": ("goal", None),
    "gate_transition": ("gate", "gate_id"),
    "handoff_transition": ("handoff", "handoff_id"),
}

# Attributes each declaration contributes to its projected entity.
_DECLARED_ATTRIBUTES = {
    "gate": ("required", "scope_key"),
    "handoff": ("to_actor",),
}


def _ordered(records, goal_id: str) -> List[Dict[str, Any]]:
    """Return this goal's records ordered by ``(event_seq, id)``."""
    scoped = [
        record for record in records
        if isinstance(record, dict) and record.get("goal_id") == goal_id
    ]
    return sorted(scoped, key=lambda r: (r.get("event_seq") or 0, r.get("id") or ""))


def project(records, now, goal_id: str, skipped: int = 0) -> Dict[str, Any]:
    """Fold ``records`` into the deterministic projection of ``goal_id``.

    ``skipped`` is the corrupt-line count reported by the store; it is carried
    through rather than recomputed, because the damage is at the IO layer and
    the projection never sees those lines.
    """
    entities: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
    rejected: List[Dict[str, Any]] = []
    execution_seq = 0

    for record in _ordered(records, goal_id):
        execution_seq = max(execution_seq, record.get("event_seq") or 0)
        record_type = record.get("record_type")

        declaration = _DECLARED_KINDS.get(record_type)
        if declaration is not None:
            kind, identity_field, initial = declaration
            identity = None if identity_field is None else record.get(identity_field)
            entity = {"status": initial}
            for name in _DECLARED_ATTRIBUTES.get(kind, ()):
                entity[name] = record.get(name)
            entities.setdefault((kind, identity), entity)
            continue

        change = _CHANGE_KINDS.get(record_type)
        if change is None:
            continue  # inert record types (receipts, assessments, lease events)

        kind, identity_field = change
        identity = None if identity_field is None else record.get(identity_field)
        entity = entities.get((kind, identity))
        if entity is None:
            rejected.append({"record_id": record.get("id"),
                             "reason": "undeclared_%s" % kind})
            continue

        key = (kind, entity["status"], record.get("to_status"))
        if key not in _TRANSITIONS:
            rejected.append({"record_id": record.get("id"),
                             "reason": "forbidden_transition"})
            continue
        entity["status"] = key[2]

    goal = entities.get(("goal", None))
    projection = {
        "execution_schema": EXECUTION_SCHEMA,
        "goal_id": goal_id,
        "goal_status": None if goal is None else goal["status"],
        "gates": [
            {"gate_id": identity, "status": entity["status"],
             "required": entity["required"], "scope_key": entity["scope_key"]}
            for (kind, identity), entity in sorted(entities.items(), key=_identity_sort)
            if kind == "gate"
        ],
        "handoffs": [
            {"handoff_id": identity, "status": entity["status"],
             "to_actor": entity["to_actor"]}
            for (kind, identity), entity in sorted(entities.items(), key=_identity_sort)
            if kind == "handoff"
        ],
        "execution_seq": execution_seq,
        "skipped": skipped,
        "rejected_transitions": rejected,
        "consistent": not rejected,
    }
    projection["evaluated_at"] = canonical_timestamp(now)
    projection["digest"] = digest(
        {key: value for key, value in projection.items() if key != "evaluated_at"}
    )
    return projection


def _identity_sort(item):
    (kind, identity), _entity = item
    return (kind, identity or "")
