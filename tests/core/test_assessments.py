"""
Tests for ATP assessment models and client methods.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atp.core.client import ATPClient
from atp.core.config import ATPConfig
from atp.core.models import (
    NEUTRAL_BY_DIMENSION,
    AssessmentDimension,
    AssessmentRecord,
    AssessmentsResponse,
    ComplianceAssessment,
    IntegrityAssessment,
    QualityAssessment,
)

# ---------------------------------------------------------------------------
# Model unit tests — no I/O, always fast
# ---------------------------------------------------------------------------


def test_assessment_dimension_values():
    assert AssessmentDimension.INTEGRITY == "integrity"
    assert AssessmentDimension.QUALITY == "quality"
    assert AssessmentDimension.COMPLIANCE == "compliance"


def test_integrity_assessment_values():
    assert IntegrityAssessment.VERIFIED == "verified"
    assert IntegrityAssessment.COMPROMISED == "compromised"


def test_quality_assessment_values():
    assert QualityAssessment.PASSED == "passed"
    assert QualityAssessment.FAILED == "failed"


def test_compliance_assessment_values():
    assert ComplianceAssessment.COMPLIANT == "compliant"
    assert ComplianceAssessment.NON_COMPLIANT == "non-compliant"


def test_neutral_by_dimension():
    assert NEUTRAL_BY_DIMENSION[AssessmentDimension.INTEGRITY] == "unverified"
    assert NEUTRAL_BY_DIMENSION[AssessmentDimension.QUALITY] == "unevaluated"
    assert NEUTRAL_BY_DIMENSION[AssessmentDimension.COMPLIANCE] == "unassessed"


def test_assessment_record_from_api_response():
    now = datetime.now(UTC)
    data = {
        "assessment_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "assessor_system_id": "sys_assessor",
        "assessor_task_id": None,
        "assessed_system_id": "sys_assessed",
        "assessed_task_id": "11111111-2222-3333-4444-555555555555",
        "dimension": "integrity",
        "assessment": "verified",
        "assessed_at": now.isoformat(),
        "created_at": now.isoformat(),
    }
    record = AssessmentRecord.from_api_response(data)
    assert record.assessment_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert record.dimension == AssessmentDimension.INTEGRITY
    assert record.assessment == "verified"
    assert record.assessor_task_id is None


def test_assessments_response_empty():
    data = {"system_id": "sys_x", "task_id": "task_y", "assessments": []}
    resp = AssessmentsResponse.from_api_response(data)
    assert resp.system_id == "sys_x"
    assert resp.assessments == []


def test_assessments_response_multiple():
    now = datetime.now(UTC).isoformat()
    data = {
        "system_id": "sys_x",
        "task_id": "task_y",
        "assessments": [
            {
                "assessment_id": "id-1",
                "assessor_system_id": "sys_a",
                "assessed_system_id": "sys_x",
                "assessed_task_id": "task_y",
                "dimension": "integrity",
                "assessment": "verified",
                "assessed_at": now,
                "created_at": now,
            },
            {
                "assessment_id": "id-2",
                "assessor_system_id": "sys_b",
                "assessed_system_id": "sys_x",
                "assessed_task_id": "task_y",
                "dimension": "quality",
                "assessment": "passed",
                "assessed_at": now,
                "created_at": now,
            },
        ],
    }
    resp = AssessmentsResponse.from_api_response(data)
    assert len(resp.assessments) == 2
    assert resp.assessments[0].dimension == AssessmentDimension.INTEGRITY
    assert resp.assessments[1].dimension == AssessmentDimension.QUALITY


# ---------------------------------------------------------------------------
# Client method tests — mock _get_client() to avoid real HTTP and bypass the
# event-loop detection inside ATPClient that recreates the client each time.
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return ATPConfig(api_key="test-key", exchange_url="http://localhost:8080")


@pytest.fixture
def client_with_system(config):
    c = ATPClient(config=config)
    c._registered_systems = {"my-agent": "sys_abc123"}
    c._system_uris = {"sys_abc123": "http://localhost:8080/systems/sys_abc123"}
    return c


def _make_mock_http(status_code: int, json_data: dict | None = None) -> MagicMock:
    """Build a mock httpx client whose post/get methods return the given response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = "ok" if status_code < 400 else "error"
    if json_data is not None:
        mock_response.json.return_value = json_data

    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.get = AsyncMock(return_value=mock_response)
    return mock_http


@pytest.mark.asyncio
async def test_report_assessment_success(client_with_system):
    now = datetime.now(UTC)
    mock_http = _make_mock_http(
        201,
        {
            "assessment_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "recorded_at": now.isoformat(),
            "status": "recorded",
        },
    )

    with patch.object(client_with_system, "_get_client", return_value=mock_http):
        record = await client_with_system.report_assessment(
            assessed_system_id="sys_target",
            assessed_task_id="11111111-2222-3333-4444-555555555555",
            dimension=AssessmentDimension.INTEGRITY,
            assessment=IntegrityAssessment.VERIFIED,
            assessed_at=now,
        )

    assert record is not None
    assert record.dimension == AssessmentDimension.INTEGRITY
    assert record.assessment == "verified"
    mock_http.post.assert_called_once()
    _, call_kwargs = mock_http.post.call_args
    payload = call_kwargs["json"]
    assert payload["dimension"] == "integrity"
    assert payload["assessment"] == "verified"
    assert payload["assessor_system_id"] == "sys_abc123"


@pytest.mark.asyncio
async def test_report_assessment_409_returns_none(client_with_system):
    """409 Conflict (duplicate) should return None, not raise."""
    mock_http = _make_mock_http(409)

    with patch.object(client_with_system, "_get_client", return_value=mock_http):
        result = await client_with_system.report_assessment(
            assessed_system_id="sys_target",
            assessed_task_id="11111111-2222-3333-4444-555555555555",
            dimension=AssessmentDimension.INTEGRITY,
            assessment=IntegrityAssessment.VERIFIED,
            assessed_at=datetime.now(UTC),
        )

    assert result is None


@pytest.mark.asyncio
async def test_report_assessment_no_system_raises(config):
    c = ATPClient(config=config)
    with pytest.raises(RuntimeError, match="no system registered"):
        await c.report_assessment(
            assessed_system_id="sys_target",
            assessed_task_id="task-1",
            dimension=AssessmentDimension.INTEGRITY,
            assessment="verified",
            assessed_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_get_assessments_success(client_with_system):
    now = datetime.now(UTC).isoformat()
    mock_http = _make_mock_http(
        200,
        {
            "system_id": "sys_target",
            "task_id": "11111111-2222-3333-4444-555555555555",
            "assessments": [
                {
                    "assessment_id": "id-1",
                    "assessor_system_id": "sys_abc123",
                    "assessed_system_id": "sys_target",
                    "assessed_task_id": "11111111-2222-3333-4444-555555555555",
                    "dimension": "integrity",
                    "assessment": "verified",
                    "assessed_at": now,
                    "created_at": now,
                }
            ],
        },
    )

    with patch.object(client_with_system, "_get_client", return_value=mock_http):
        result = await client_with_system.get_assessments(
            system_id="sys_target",
            task_id="11111111-2222-3333-4444-555555555555",
        )

    assert isinstance(result, AssessmentsResponse)
    assert len(result.assessments) == 1
    assert result.assessments[0].dimension == AssessmentDimension.INTEGRITY
    assert result.assessments[0].assessment == "verified"


@pytest.mark.asyncio
async def test_get_assessments_empty(client_with_system):
    mock_http = _make_mock_http(
        200,
        {"system_id": "sys_target", "task_id": "task-1", "assessments": []},
    )

    with patch.object(client_with_system, "_get_client", return_value=mock_http):
        result = await client_with_system.get_assessments("sys_target", "task-1")

    assert result.assessments == []
