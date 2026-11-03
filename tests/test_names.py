"""
tests/test_names.py
Tests name normalization, fuzzy scoring, initial expansion, and ambiguity resolution.
"""

from tinycore.names import normalize_name, score, resolve


def test_normalize_name():
    assert normalize_name("Dr. Prit Patel") == "prit patel"
    assert normalize_name("Shri Narendra Modi, PM") == "narendra modi pm"
    assert normalize_name("Prof. CA. Jane Doe, Ph.D.") == "jane doe ph d"
    assert normalize_name("Renée O'Connor") == "renee o connor"


def test_score_and_initial_expansion():
    # Exact match
    assert score("Prit Patel", "Prit Patel") == 1.0

    # Initial expansion (P. Patel ~ Prit Patel)
    s_init = score("P. Patel", "Prit Patel")
    assert s_init >= 0.97

    # Disparate names
    s_diff = score("Alice Smith", "Bob Jones")
    assert s_diff < 0.50


def test_resolve_exact_identifier():
    candidates = [
        {"name": "DeepMind Limited", "licence_number": "12345"},
        {"name": "DeepMind AI Technologies", "licence_number": "99999"}
    ]
    res = resolve("DeepMind", candidates, query_identifier="99999")
    assert res.outcome == "MATCH"
    assert res.top_candidate["licence_number"] == "99999"
    assert res.top_score == 1.0


def test_resolve_high_confidence_match():
    candidates = [
        {"name": "Google DeepMind UK Limited"},
        {"name": "Acme Widgets Corp"}
    ]
    res = resolve("Google DeepMind UK Limited", candidates)
    assert res.outcome == "MATCH"
    assert res.top_candidate["name"] == "Google DeepMind UK Limited"
    assert res.top_score >= 0.97


def test_resolve_ambiguous_multiple():
    candidates = [
        {"name": "Pritesh Patel"},
        {"name": "Pritam Patel"}
    ]
    # "Prit Patel" is close to both -> ambiguous
    res = resolve("Prit Patel", candidates)
    assert res.outcome == "MULTIPLE"
    assert res.top_candidate is None  # Never pick arbitrarily
    assert len(res.ranked_candidates) == 2


def test_resolve_no_match():
    candidates = [
        {"name": "Completely Unrelated Firm Ltd"}
    ]
    res = resolve("DeepMind Robotics", candidates)
    assert res.outcome == "NO_MATCH"
    assert res.top_candidate is None
