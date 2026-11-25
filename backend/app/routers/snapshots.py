"""快照列表/复演/diff，以及关系裁定之外的两类变更触发：政策有效日、退费更正。"""
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Case, GraphSnapshot, Policy, Settlement, SourceRecord
from ..security import current_user, require_admin
from ..services import counterevidence as ce
from ..services import graph_builder, patterns, snapshot
from ..services.rejudge import CUTOFF, rejudge_case

router = APIRouter(prefix="/api/snapshots", tags=["snapshots"],
                   dependencies=[Depends(current_user)])


@router.get("")
def list_snapshots(db: Session = Depends(get_db)):
    rows = db.query(GraphSnapshot).order_by(GraphSnapshot.snapshot_no.desc()).all()
    return {"items": [snapshot.snapshot_summary(s) for s in rows]}


@router.get("/diff")
def diff(from_no: int, to_no: int, db: Session = Depends(get_db)):
    try:
        return snapshot.diff_snapshots(db, from_no, to_no)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{no}")
def get_snapshot(no: int, db: Session = Depends(get_db)):
    s = db.query(GraphSnapshot).filter_by(snapshot_no=no).first()
    if not s:
        raise HTTPException(404, "快照不存在")
    return snapshot.snapshot_summary(s) | {"graph": s.graph}


class PolicyChangeIn(BaseModel):
    policy_code: str = "POL-CHRONIC"
    new_version: str = "v2026.3"
    effective_from: str = "2026-03-15"  # 有效日更正到截止日之前，便于复演
    title: str | None = None
    note: str | None = None


@router.post("/policy-change")
def policy_change(body: PolicyChangeIn, db: Session = Depends(get_db),
                   user=Depends(current_user)):
    new_from = date.fromisoformat(body.effective_from)
    old = db.query(Policy).filter_by(code=body.policy_code) \
        .order_by(Policy.effective_from.desc()).first()
    if not old:
        raise HTTPException(404, "政策不存在")
    old.effective_to = new_from - timedelta(days=1)
    p = Policy(code=body.policy_code, version=body.new_version,
               title=body.title or old.title, effective_from=new_from,
               region=old.region, clauses=old.clauses,
               citation=(body.note or old.citation))
    db.add(p)
    db.commit()

    # 找出判断材料中引用该政策的案件
    affected = []
    for c in db.query(Case).all():
        j = db.query(__import__("app.models", fromlist=["CaseJudgment"]).CaseJudgment) \
            .filter_by(id=c.current_judgment_id).first() if c.current_judgment_id else None
        if j and body.policy_code in json.dumps(j.counter_result, ensure_ascii=False) \
                + json.dumps(j.rule_result, ensure_ascii=False):
            affected.append(c.code)
    snap = snapshot.create_snapshot(
        db, reason=f"政策有效日/版本变化：{body.policy_code} {body.new_version} "
                   f"生效 {body.effective_from}（{user.username}）",
        edge_scope={"profile": "default", "trigger": "policy_change",
                    "policy_code": body.policy_code, "new_version": body.new_version},
        cutoff=CUTOFF, affected_case_codes=affected)
    rejudged = []
    for code in affected:
        c = db.query(Case).filter_by(code=code).first()
        j = rejudge_case(db, c, author=user.username, snapshot_no=snap.snapshot_no,
                         note=f"政策换版 {body.new_version} 后复判（旧判断版本保留）")
        rejudged.append({"code": code, "new_version": j.version})
    return {"ok": True, "snapshot_no": snap.snapshot_no,
            "affected_cases": affected, "rejudged": rejudged}


class RefundIn(BaseModel):
    settlement_code: str = "ST12"
    refund_amount: float = 168.0
    reason: str = "演示：定点药店退费更正，医保基金追回"


@router.post("/refund-correction")
def refund_correction(body: RefundIn, db: Session = Depends(get_db),
                      user=Depends(current_user)):
    st = db.query(Settlement).filter_by(code=body.settlement_code).first()
    if not st:
        raise HTTPException(404, "结算不存在")
    src_old = db.query(SourceRecord).filter_by(code=f"SRC-{st.code}").first()
    n_corr = db.query(SourceRecord).filter(
        SourceRecord.code.like(f"SRC-{st.code}-C%")).count() + 1
    cs = SourceRecord(
        code=f"SRC-{st.code}-C{n_corr}", source_system="医保结算系统",
        record_type="refund_correction", business_key=st.settle_no,
        valid_from=datetime.utcnow(), received_at=datetime.utcnow(),
        status="current", correction_reason=body.reason,
        supersedes_id=src_old.id if src_old else None,
        citation=f"退费/更正单 TG-{st.code}-{n_corr}",
        payload={"refund_amount": body.refund_amount, "operator": user.username})
    db.add(cs)
    total_refund = float(st.refund_amount or 0) + body.refund_amount
    st.refund_amount = total_refund
    st.status = "refunded" if total_refund >= float(st.total_amount) - 0.01 else "partial_refund"
    db.commit()

    graph_builder.rebuild_graph(db)
    patterns.upsert_derived_edges(db, CUTOFF)
    ce.apply_edge_negations(db)

    case = None
    for c in db.query(Case).all():
        if st.code in (c.anchors or {}).get("settlement_codes", []):
            case = c
            break
    affected = [case.code] if case else []
    snap = snapshot.create_snapshot(
        db, reason=f"退费更正：{st.code} 退费 ¥{body.refund_amount:.2f}（{user.username}）",
        edge_scope={"profile": "default", "trigger": "refund_correction",
                    "settlement_code": st.code},
        cutoff=CUTOFF, affected_case_codes=affected)
    out = None
    if case:
        j = rejudge_case(db, case, author=user.username, snapshot_no=snap.snapshot_no,
                         note=f"结算 {st.code} 退费更正后复判（旧判断版本保留）")
        out = {"code": case.code, "new_judgment_version": j.version}
    return {"ok": True, "snapshot_no": snap.snapshot_no,
            "affected_cases": affected, "rejudged": out,
            "settlement_status": st.status}
