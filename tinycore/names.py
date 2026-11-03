"""
tinycore/names.py
Entity name normalization, fuzzy scoring, and ambiguity resolution for TinyVerify.
Deterministic logic:
- NFKD normalization, honorific stripping, initial expansion
- Rapidfuzz token_sort_ratio & Jaro-Winkler
- Resolution: MATCH (>=0.97 with margin, or identifier exact match), MULTIPLE (>=0.80), NO_MATCH (<0.80)
"""

import re
import unicodedata
from dataclasses import dataclass
from rapidfuzz import fuzz, distance

HONORIFICS = [
    r"\bdr\.?\b", r"\bmr\.?\b", r"\bmrs\.?\b", r"\bms\.?\b",
    r"\bshri\.?\b", r"\bsmt\.?\b", r"\bprof\.?\b", r"\bca\.?\b",
    r"\badv\.?\b", r"\bsir\b", r"\ber\.?\b"
]
COMPILED_HONORIFICS = [re.compile(h, re.IGNORECASE) for h in HONORIFICS]


@dataclass
class Resolution:
    outcome: str  # "MATCH", "MULTIPLE", "NO_MATCH"
    top_candidate: dict | None
    ranked_candidates: list[dict]
    top_score: float
    margin: float = 0.0


def normalize_name(s: str) -> str:
    """Normalizes names by NFKD, stripping diacritics, casefolding, dropping honorifics & punctuation."""
    if not s:
        return ""
    # NFKD and remove diacritics
    nfkd = unicodedata.normalize("NFKD", s)
    no_diacritics = "".join([c for c in nfkd if not unicodedata.combining(c)])
    
    # Casefold
    clean = no_diacritics.casefold()

    # Drop honorifics
    for h in COMPILED_HONORIFICS:
        clean = h.sub(" ", clean)

    # Drop punctuation except alphanumeric and spaces
    clean = re.sub(r"[^\w\s]", " ", clean)

    # Collapse whitespace
    return re.sub(r"\s+", " ", clean).strip()


def _is_initial_expansion(tokens_a: list[str], tokens_b: list[str]) -> bool:
    """Checks if one name is an initial expansion of another (e.g. ['p', 'patel'] ~ ['prit', 'patel'])."""
    if len(tokens_a) != len(tokens_b) or not tokens_a:
        return False
    # Direct order
    if all(
        t_a == t_b or (len(t_a) == 1 and t_b.startswith(t_a)) or (len(t_b) == 1 and t_a.startswith(t_b))
        for t_a, t_b in zip(tokens_a, tokens_b)
    ):
        return True
    # Inverted order (e.g. "Patel, P." vs "Prit Patel")
    if all(
        t_a == t_b or (len(t_a) == 1 and t_b.startswith(t_a)) or (len(t_b) == 1 and t_a.startswith(t_b))
        for t_a, t_b in zip(reversed(tokens_a), tokens_b)
    ):
        return True
    return False


def score(a: str, b: str) -> float:
    """Calculates name similarity (0.0 to 1.0) using rapidfuzz and initial expansion handling."""
    norm_a = normalize_name(a)
    norm_b = normalize_name(b)

    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0

    tokens_a = norm_a.split()
    tokens_b = norm_b.split()

    # Check initial expansion
    if _is_initial_expansion(tokens_a, tokens_b):
        return 0.98

    # Rapidfuzz token sort and Jaro-Winkler
    ts_ratio = fuzz.token_sort_ratio(norm_a, norm_b) / 100.0
    jw_sim = distance.JaroWinkler.similarity(norm_a, norm_b)

    return max(ts_ratio, jw_sim)


def resolve(
    query_name: str,
    candidates: list[dict],
    query_identifier: str | None = None
) -> Resolution:
    """
    Resolves name matching against candidate registry records.
    - Identifier exact match always wins -> MATCH (score 1.0)
    - MATCH if top >= 0.97 AND (top - second >= 0.05 or only 1 candidate)
    - MULTIPLE if >=2 candidates >= 0.80 within 0.05, or top in [0.80, 0.97)
    - NO_MATCH if top < 0.80 or empty
    """
    if not candidates:
        return Resolution(outcome="NO_MATCH", top_candidate=None, ranked_candidates=[], top_score=0.0)

    # 1. Identifier exact match wins immediately
    if query_identifier:
        clean_qid = query_identifier.strip().lower()
        for cand in candidates:
            cand_id = str(cand.get("identifier") or cand.get("company_number") or cand.get("licence_number") or "").strip().lower()
            if cand_id and cand_id == clean_qid:
                cand_copy = dict(cand)
                cand_copy["score"] = 1.0
                return Resolution(
                    outcome="MATCH",
                    top_candidate=cand_copy,
                    ranked_candidates=[cand_copy],
                    top_score=1.0,
                    margin=1.0
                )

    # 2. Score candidates by name
    scored = []
    for cand in candidates:
        name_val = cand.get("name") or cand.get("company_name") or ""
        s = score(query_name, name_val)
        c_copy = dict(cand)
        c_copy["score"] = round(s, 4)
        scored.append(c_copy)

    scored.sort(key=lambda x: x["score"], reverse=True)

    top = scored[0]["score"]
    second = scored[1]["score"] if len(scored) > 1 else 0.0
    margin = round(top - second, 4)

    # Threshold checks
    if top >= 0.97 and (len(scored) == 1 or margin >= 0.05):
        return Resolution(
            outcome="MATCH",
            top_candidate=scored[0],
            ranked_candidates=scored,
            top_score=top,
            margin=margin
        )

    if top >= 0.80:
        return Resolution(
            outcome="MULTIPLE",
            top_candidate=None,  # Spec rule: NEVER pick arbitrarily on ambiguous MULTIPLE
            ranked_candidates=scored,
            top_score=top,
            margin=margin
        )

    return Resolution(
        outcome="NO_MATCH",
        top_candidate=None,
        ranked_candidates=scored,
        top_score=top,
        margin=margin
    )
