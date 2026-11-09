from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from src.database import get_session
from src.models.asset import Asset, AssetEnvironment
from src.auth import AuthPrincipal, authenticated_operator, require_admin, require_system_admin
from src.schemas.vulnerability import (
    Conclusion,
    DispositionFilter,
    Severity,
    VulnerabilityDispositionUpdate,
    VulnerabilityFindingResponse,
    VulnerabilityListResponse,
    VulnerabilitySummary,
)
from src.services import vulnerabilities as service
from src.services import asset_access

router = APIRouter(prefix="/vulnerabilities", tags=["vulnerabilities"])


@router.get("", response_model=VulnerabilityListResponse)
def read_vulnerabilities(
    query: str | None = Query(default=None, max_length=120),
    severity: Severity | None = None,
    conclusion: Conclusion | None = None,
    asset_id: str | None = Query(default=None, min_length=1, max_length=36),
    disposition: DispositionFilter = "active",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> VulnerabilityListResponse:
    visible_asset_ids = asset_access.visible_asset_ids(session, principal)
    records, total = service.list_findings(
        session, query, asset_id, severity, conclusion, disposition, page, page_size,
        visible_asset_ids,
    )
    return VulnerabilityListResponse(
        items=[VulnerabilityFindingResponse.model_validate(item) for item in records],
        total=total,
        summary=VulnerabilitySummary(**service.summarize_findings(
            session, visible_asset_ids
        )),
    )


@router.get("/{finding_id}", response_model=VulnerabilityFindingResponse)
def read_vulnerability(
    finding_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session)
) -> VulnerabilityFindingResponse:
    record = service.get_finding_record(session, finding_id)
    if record is not None:
        asset_access.require_asset(session, principal, record.asset_id)
    finding = service.serialize_finding(session, record) if record else None
    if finding is None:
        raise HTTPException(status_code=404, detail="漏洞发现不存在")
    return VulnerabilityFindingResponse.model_validate(finding)


@router.patch("/{finding_id}/disposition", response_model=VulnerabilityFindingResponse)
def change_disposition(
    finding_id: str, payload: VulnerabilityDispositionUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> VulnerabilityFindingResponse:
    finding = service.get_finding_record(session, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="漏洞发现不存在")
    asset_access.require_asset(session, principal, finding.asset_id)
    try:
        updated = service.update_disposition(
            session, finding,
            payload.model_copy(update={"operator": authenticated_operator(request)}),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return VulnerabilityFindingResponse.model_validate(updated)


@router.delete("/{finding_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_system_admin)])
def delete_finding(
    finding_id: str,
    request: Request,
    purpose: str = Query(pattern="^(test_data_cleanup|compliance_expiry)$"),
    reason: str = Query(min_length=2, max_length=1000),
    session: Session = Depends(get_session),
) -> Response:
    if len(reason.strip()) < 2:
        raise HTTPException(status_code=422, detail="删除原因不能为空")
    if purpose == "compliance_expiry":
        raise HTTPException(status_code=409, detail="尚未配置可验证的合规保留期限，禁止物理删除")
    finding = service.get_finding_record(session, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="漏洞发现不存在")
    asset = session.get(Asset, finding.asset_id)
    if asset is None or asset.environment != AssetEnvironment.TEST:
        raise HTTPException(status_code=409, detail="仅测试环境漏洞证据允许测试清理")
    service.physically_delete(
        session, finding, purpose, reason.strip(), authenticated_operator(request)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
