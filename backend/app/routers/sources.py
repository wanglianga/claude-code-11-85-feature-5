"""来源账查询。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import SourceRecord
from ..security import current_user

router = APIRouter(prefix="/api/sources", tags=["sources"],
                   dependencies=[Depends(current_user)])


def _dict(s: SourceRecord):
    return {"id": s.id, "code": s.code, "source_system": s.source_system,
            "record_type": s.record_type, "business_key": s.business_key,
            "valid_from": s.valid_from.isoformat() if s.valid_from else None,
            "valid_to": s.valid_to.isoformat() if s.valid_to else None,
            "received_at": s.received_at.isoformat() if s.received_at else None,
            "status": s.status, "correction_reason": s.correction_reason,
            "supersedes_id": s.supersedes_id, "citation": s.citation,
            "payload": s.payload or {}}


@router.get("")
def list_sources(source_system: str | None = None, record_type: str | None = None,
                 status: str | None = None, q: str | None = None,
                 limit: int = Query(200, le=500), db: Session = Depends(get_db)):
    query = db.query(SourceRecord)
    if source_system:
        query = query.filter_by(source_system=source_system)
    if record_type:
        query = query.filter_by(record_type=record_type)
    if status:
        query = query.filter_by(status=status)
    if q:
        like = f"%{q}%"
        query = query.filter((SourceRecord.code.ilike(like))
                             | (SourceRecord.business_key.ilike(like))
                             | (SourceRecord.citation.ilike(like)))
    rows = query.order_by(SourceRecord.id).limit(limit).all()
    systems = [r[0] for r in db.query(SourceRecord.source_system).distinct().all()]
    types = [r[0] for r in db.query(SourceRecord.record_type).distinct().all()]
    return {"items": [_dict(s) for s in rows], "systems": systems, "types": types}


@router.get("/{code}")
def source_detail(code: str, db: Session = Depends(get_db)):
    s = db.query(SourceRecord).filter_by(code=code).first()
    if not s:
        raise HTTPException(404, "来源记录不存在")
    data = _dict(s)
    if s.supersedes_id:
        old = db.get(SourceRecord, s.supersedes_id)
        data["supersedes"] = _dict(old) if old else None
    nxt = db.query(SourceRecord).filter_by(supersedes_id=s.id).first()
    if nxt:
        data["superseded_by"] = _dict(nxt)
    return data
