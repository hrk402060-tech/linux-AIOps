from src.services.credential_risk_policy import assess


def test_policy_detects_common_and_strong_passwords_without_returning_secret() -> None:
    weak = assess("root123", "root")
    strong = assess("Correct-Horse-47!Battery", "operator")
    assert weak["status"] == "at_risk"
    assert "common_password" in weak["violations"]
    assert strong["status"] == "passed"
    assert "root123" not in str(weak)
