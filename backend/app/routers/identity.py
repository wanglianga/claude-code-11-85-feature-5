"""身份与实体证据、簇版本、人工裁定。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Case, IdentityClusterVersion, IdentityEvidence, Person
from ..security import current_user
from ..services import counterevidence as ce
from ..services import graph_builder, identity, patterns, snapshot

router = APIRouter(prefix="/api/identity", tags=["identity"],
                   dependencies=[Depends(current_user)])


@router.get("/evidence")
def evidence_list(db: Session = Depends(get_db)):
    evs = db.query(IdentityEvidence).order_by(IdentityEvidence.code).all()
    persons = {p.code: p for p in db.query(Person).all()}

    def name_of(code, stype):
        if stype == "person" and code in persons:
            return persons[code].name
        return code

    return {"items": [{
        "code": e.code, "subject_type": e.subject_type,
        "left_code": e.left_code, "right_code": e.right_code,
        "left_name": name_of(e.left_code, e.subject_type),
        "right_name": name_of(e.right_code, e.subject_type),
        "evidence_type": e.evidence_type, "evidence_value": e.evidence_value,
        "tier": e.tier, "status": e.status,
        "valid_from": e.valid_from.isoformat() if e.valid_from else None,
        "valid_to": e.valid_to.isoformat() if e.valid_to else None,
        "source_id": e.source_id,
        "decided_by": e.decided_by,
        "decided_at": e.decided_at.isoformat() if e.decided_at else None,
        "decision_note": e.decision_note,
    } for e in evs]}


@router.get("/clusters")
def clusters(db: Session = Depends(get_db)):
    rows = db.query(IdentityClusterVersion).order_by(IdentityClusterVersion.version.desc()).all()
    return {"items": [{"version": r.version, "created_at": r.created_at.isoformat(),
                       "reason": r.reason, "clusters": r.clusters} for r in rows]}


class AdjudicateIn(BaseModel):
    decision: str  # confirmed / rejected
    note: str | None = None


@router.post("/evidence/{code}/adjudicate")
def adjudicate(code: str, body: AdjudicateIn, db: Session = Depends(get_db),
               user=Depends(current_user)):
    if body.decision not in ("confirmed", "rejected"):
        raise HTTPException(400, "decision 必须为 confirmed/rejected")
    ev = db.query(IdentityEvidence).filter_by(code=code).first()
    if not ev:
        raise HTTPException(404, "证据不存在")
    ev.status = body.decision
    ev.decided_by = user.username
    ev.decided_at = datetime.utcnow()
    ev.decision_note = body.note
    db.commit()

    # 重算簇版本 + 图 + 派生边 + 否定状态
    identity.rebuild_clusters(
        db, reason=f"人工裁定 {code} → {body.decision}（{user.username}）")
    db.commit()
    graph_builder.rebuild_graph(db)
    patterns.upsert_derived_edges(db, __import__("datetime").date(2026, 3, 31))
    ce.apply_edge_negations(db)

    # 受影响案件：anchors.person_codes 含任一主体
    affected = []
    for c in db.query(Case).all():
        pc = (c.anchors or {}).get("person_codes", [])
        if ev.left_code in pc or ev.right_code in pc:
            affected.append(c.code)
    snap = snapshot.create_snapshot(
        db, reason=f"身份裁定 {code}={body.decision}，重算身份簇与图",
        edge_scope={"profile": "default", "trigger": "identity_adjudication",
                    "evidence_code": code, "decision": body.decision},
        cutoff=__import__("datetime").date(2026, 3, 31),
        affected_case_codes=affected)
    return {"ok": True, "snapshot_no": snap.snapshot_no,
            "identity_version": snap.identity_version, "affected_cases": affected}
