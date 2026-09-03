import pytest
from rca_engine.remediation import generate_remediation_plan
from backend.app.models.event import RemediationPlan


def test_generate_remediation_ec2():
    plan = generate_remediation_plan("inc_001", "EC2")
    assert isinstance(plan, RemediationPlan)
    assert plan.target_service == "EC2"
    assert plan.action_type == "SCALE_OUT"
    assert len(plan.commands) > 0
    assert "autoscaling" in plan.commands[0]


def test_generate_remediation_dynamodb():
    plan = generate_remediation_plan("inc_002", "DynamoDB")
    assert plan.target_service == "DynamoDB"
    assert plan.action_type == "AUTO_HEAL"
    assert "PAY_PER_REQUEST" in plan.commands[0]


def test_generate_remediation_fallback():
    plan = generate_remediation_plan("inc_003", "CustomThirdPartySvc")
    assert plan.target_service == "CustomThirdPartySvc"
    assert plan.action_type == "MANUAL_CHECK"
