"""
tests/test_llm.py
Tests Groq LLM classification and fallback extraction logic.
Validates Hard Rule 4: non-verbatim extractions are strictly rejected -> UNKNOWN.
"""

from tinycore.llm import extract_fallback, classify_change


def test_extract_fallback_enforces_hard_rule_4(monkeypatch):
    # Simulated page text
    page_text = (
        "Acme Medical Board Directory\n"
        "Licence Holder: Dr. Johnathan Doe\n"
        "Registration Number: MED-98765\n"
        "Status: ACTIVE"
    )

    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "reg_num": {"type": "string"},
            "invented_field": {"type": "string"}
        }
    }

    # Mock _call_groq_json to simulate model response with both real and hallucinated fields
    def mock_call(prompt, model, system_msg):
        return {
            "name": "Dr. Johnathan Doe",
            "reg_num": "MED-98765",
            "invented_field": "Hallucinated Value Not in Page"
        }

    monkeypatch.setattr("tinycore.llm._call_groq_json", mock_call)

    validated = extract_fallback(page_text, schema)
    # Verbatim fields must pass
    assert validated["name"] == "Dr. Johnathan Doe"
    assert validated["reg_num"] == "MED-98765"
    # Hallucinated field not in page text must be rejected -> UNKNOWN
    assert validated["invented_field"] == "UNKNOWN"


def test_classify_change_schema_and_types(monkeypatch):
    diff = {
        "changed": [
            {
                "heading_path": "Pricing",
                "before": "Pro: $20/mo",
                "after": "Pro: $29/mo",
                "numeric_delta": [{"old": 20.0, "new": 29.0, "unit": "$/mo"}]
            }
        ],
        "is_empty": False
    }

    def mock_call(prompt, model, system_msg):
        return {
            "type": "price",
            "materiality": 0.85,
            "summary": "Pro plan price increased from $20 to $29 per month."
        }

    monkeypatch.setattr("tinycore.llm._call_groq_json", mock_call)

    res = classify_change(diff)
    assert res["type"] == "price"
    assert 0.0 <= res["materiality"] <= 1.0
    assert "price increased" in res["summary"]
