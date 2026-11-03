"""
tinycore/differ.py
Computes structured diffs between Block lists:
- Exact hash match -> unchanged
- Similarity >= 0.6 in same heading_path -> changed (with numeric_delta extraction)
- Unmatched -> added / removed
- Returns Diff with is_empty=True if all changes were transient noise
"""

import re
import difflib
from dataclasses import dataclass, field
from tinycore.normalize import Block

CURRENCY_UNITS = [r"\$", r"₹", r"€", r"£", r"¥"]
RATE_UNITS = [r"/mo", r"/month", r"/yr", r"/year", r"/day", r"/hr", r"/sec", r"/min", r"per\s+1[Mm]\s+tokens?", r"req(?:uests)?/s(?:ec)?", r"%"]

# Regex for numbers with optional currency and rate units
NUMERIC_REGEX = re.compile(
    r"(?P<curr>[\$₹€£¥])?\s*(?P<val>\d+(?:[\.,]\d+)?)\s*(?P<curr_post>[\$₹€£¥])?\s*(?P<rate>/mo|/month|/yr|/year|/day|/hr|/sec|/min|per\s+1[Mm]\s+tokens?|req(?:uests)?/s(?:ec)?|%)?",
    re.IGNORECASE
)


@dataclass
class NumericDelta:
    old: float
    new: float
    unit: str


@dataclass
class ChangedBlock:
    before: str
    after: str
    heading_path: str
    similarity: float
    numeric_delta: list[dict] = field(default_factory=list)


@dataclass
class Diff:
    added: list[Block] = field(default_factory=list)
    removed: list[Block] = field(default_factory=list)
    changed: list[ChangedBlock] = field(default_factory=list)
    is_empty: bool = True

    def to_dict(self) -> dict:
        return {
            "added": [{"id": b.id, "heading_path": b.heading_path, "text": b.text} for b in self.added],
            "removed": [{"id": b.id, "heading_path": b.heading_path, "text": b.text} for b in self.removed],
            "changed": [
                {
                    "heading_path": c.heading_path,
                    "before": c.before,
                    "after": c.after,
                    "similarity": round(c.similarity, 2),
                    "numeric_delta": c.numeric_delta
                }
                for c in self.changed
            ],
            "is_empty": self.is_empty
        }


def extract_numbers_with_units(text: str) -> list[tuple[float, str]]:
    """Extracts numeric values alongside their currencies/units."""
    found = []
    for m in NUMERIC_REGEX.finditer(text):
        raw_val = m.group("val").replace(",", "")
        try:
            val = float(raw_val)
        except ValueError:
            continue
        curr = (m.group("curr") or m.group("curr_post") or "").strip()
        rate = (m.group("rate") or "").strip().lower()
        unit = f"{curr}{rate}" if curr and rate else (curr or rate or "count")
        found.append((val, unit))
    return found


def compute_numeric_deltas(before_text: str, after_text: str) -> list[dict]:
    """Finds numeric shifts between before and after text strings."""
    nums_before = extract_numbers_with_units(before_text)
    nums_after = extract_numbers_with_units(after_text)
    deltas = []
    
    # Match by unit or order
    for (v_old, u_old), (v_new, u_new) in zip(nums_before, nums_after):
        if v_old != v_new:
            deltas.append({
                "old": v_old,
                "new": v_new,
                "unit": u_new if u_new != "count" else u_old
            })
    return deltas


def diff_blocks(old: list[Block], new: list[Block]) -> Diff:
    """Computes a structured diff between old and new blocks."""
    # 1. Exact match on norm_hash
    unmatched_old = []
    new_by_hash: dict[str, list[Block]] = {}
    for b in new:
        new_by_hash.setdefault(b.norm_hash, []).append(b)

    matched_new_ids = set()

    for o_block in old:
        candidates = new_by_hash.get(o_block.norm_hash, [])
        matched = False
        for c in candidates:
            if c.id not in matched_new_ids and c.heading_path == o_block.heading_path:
                matched_new_ids.add(c.id)
                matched = True
                break
        if not matched:
            unmatched_old.append(o_block)

    unmatched_new = [b for b in new if b.id not in matched_new_ids]

    added: list[Block] = []
    removed: list[Block] = []
    changed: list[ChangedBlock] = []

    # 2. Match remaining blocks within same heading_path by similarity >= 0.6
    used_new_in_changed = set()

    for o_block in unmatched_old:
        best_sim = 0.0
        best_match = None
        for n_block in unmatched_new:
            if n_block.id in used_new_in_changed:
                continue
            if n_block.heading_path == o_block.heading_path:
                sim = difflib.SequenceMatcher(None, o_block.text, n_block.text).ratio()
                if sim > best_sim:
                    best_sim = sim
                    best_match = n_block

        if best_match and best_sim >= 0.6:
            used_new_in_changed.add(best_match.id)
            deltas = compute_numeric_deltas(o_block.text, best_match.text)
            changed.append(ChangedBlock(
                before=o_block.text,
                after=best_match.text,
                heading_path=o_block.heading_path,
                similarity=best_sim,
                numeric_delta=deltas
            ))
        else:
            removed.append(o_block)

    for n_block in unmatched_new:
        if n_block.id not in used_new_in_changed:
            added.append(n_block)

    is_empty = (len(added) == 0 and len(removed) == 0 and len(changed) == 0)

    return Diff(
        added=added,
        removed=removed,
        changed=changed,
        is_empty=is_empty
    )
