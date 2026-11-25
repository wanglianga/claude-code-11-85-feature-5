"""案件投影、判断版本与人工核查状态。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Case, CaseJudgment, CaseReviewEvent, CounterCheck
from ..security import can_access_case, current_user

router = APIRouter(prefix="/api/cases", tags=["cases"],
                   dependencies=[Depends(current_user)])

STATUS_LABELS = {
    "new": "新线索", "pending": "待人工核查", "reviewing": "核查中",
    "confirmed_risk_clue": "风险线索成立（转稽核）",
    "excluded_legitimate": "已排除·合法解释",
    "closed_returned": "已关闭·药品退回",
    "closed_weak_identity": "已关闭·弱身份",
    "weak_identity": "弱身份候选待核",
}


def _list_row(c: Case):
    return {"code": c.code, "title": c.title, "pattern_codes": c.pattern_codes,
            "primary_person_code": c.primary_person_code, "region": c.region,
            "org_codes": c.org_codes or [], "status": c.status,
            "status_label": STATUS_LABELS.get(c.status, c.status),
            "rule_score": float(c.rule_score or 0),
            "model_score": float(c.model_score) if c.model_score is not None else None,
            "amount_min": float(c.amount_min or 0), "amount_max": float(c.amount_max or 0),
            "counter_completeness": c.counter_completeness or 0,
            "unresolved_counter": c.unresolved_counter or 0,
            "affected": c.affected, "assignee": c.assignee}


@router.get("")
def list_cases(db: Session = Depends(get_db), user=Depends(current_user)):
    rows = db.query(Case).order_by(Case.code).all()
    rows = [c for c in rows if can_access_case(user, c.region or "", c.org_codes or [])]
    return {"items": [_list_row(c) for c in rows],
            "status_labels": STATUS_LABELS}


@router.get("/{code}")
def case_detail(code: str, db: Session = Depends(get_db), user=Depends(current_user)):
    c = db.query(Case).filter_by(code=code).first()
    if not c:
        raise HTTPException(404, "案件不存在")
    if not can_access_case(user, c.region or "", c.org_codes or []):
        raise HTTPException(403, "您的数据权限不能查看该案件")
    j = db.query(CaseJudgment).filter_by(id=c.current_judgment_id).first()
    versions = db.query(CaseJudgment).filter_by(case_id=c.id) \
        .order_by(CaseJudgment.version.desc()).all()
    events = db.query(CaseReviewEvent).filter_by(case_code=code) \
        .order_by(CaseReviewEvent.id).all()
    checks = db.query(CounterCheck).filter_by(case_code=code).all()
    return {
        "case": {**_list_row(c), "anchors": c.anchors or {},
                 "affected_reason": c.affected_reason,
                 "first_snapshot_id": c.first_snapshot_id,
                 "updated_at": c.updated_at.isoformat() if c.updated_at else None},
        "current_judgment": _judgment(j) if j else None,
        "versions": [{"id": v.id, "version": v.version, "kind": v.kind,
                      "author": v.author, "note": v.note,
                      "snapshot_no": v.snapshot_no,
                      "created_at": v.created_at.isoformat() if v.created_at else None}
                     for v in versions],
        "review_events": [{"at": e.at.isoformat(), "reviewer": e.reviewer,
                           "from_status": e.from_status, "to_status": e.to_status,
                           "note": e.note} for e in events],
        "counter_checks": [{"check_type": x.check_type, "title": x.title,
                            "status": x.status, "detail": x.detail,
                            "source_codes": x.source_codes or [], "effect": x.effect}
                           for x in checks],
        "status_labels": STATUS_LABELS,
    }


def _judgment(j: CaseJudgment):
    return {"id": j.id, "version": j.version, "kind": j.kind, "author": j.author,
            "note": j.note, "snapshot_no": j.snapshot_no,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "rule_result": j.rule_result, "model_result": j.model_result,
            "counter_result": j.counter_result, "projection": j.projection,
            "amount_range": j.amount_range, "disclaimer": j.disclaimer}


@router.get("/{code}/judgments/{version}")
def judgment_version(code: str, version: int, db: Session = Depends(get_db),
                     user=Depends(current_user)):
    c = db.query(Case).filter_by(code=code).first()
    if not c:
        raise HTTPException(404, "案件不存在")
    j = db.query(CaseJudgment).filter_by(case_id=c.id, version=version).first()
    if not j:
        raise HTTPException(404, "判断版本不存在")
    return _judgment(j)


class ReviewIn(BaseModel):
    to_status: str
    note: str | None = None
    assignee: str | None = None


@router.post("/{code}/review")
def review(code: str, body: ReviewIn, db: Session = Depends(get_db),
           user=Depends(current_user)):
    c = db.query(Case).filter_by(code=code).first()
    if not c:
        raise HTTPException(404, "案件不存在")
    if not can_access_case(user, c.region or "", c.org_codes or []):
        raise HTTPException(403, "无权操作该案件")
    allowed = set(STATUS_LABELS)
    if body.to_status not in allowed:
        raise HTTPException(400, f"状态必须为 {sorted(allowed)}")
    old = c.status
    c.status = body.to_status
    if body.assignee is not None:
        c.assignee = body.assignee or user.username
    elif not c.assignee:
        c.assignee = user.username
    db.add(CaseReviewEvent(case_code=code, reviewer=user.username,
                           from_status=old, to_status=body.to_status, note=body.note))
    db.commit()
    # 关闭/排除类状态可能需要否定派生支持边
    if body.to_status in ("excluded_legitimate", "closed_returned", "closed_weak_identity"):
        from ..services.counterevidence import apply_edge_negations
        apply_edge_negations(db)
    return {"ok": True, "status": c.status}
