"""总览统计、验收运行、重置演示数据。"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..acceptance import run_acceptance
from ..database import get_db
from ..models import (
    Case, GraphEdge, GraphNode, GraphSnapshot, IdentityEvidence, Person,
    SourceRecord, User,
)
from ..security import can_access_case, current_user, require_admin

router = APIRouter(prefix="/api", tags=["tools"])


@router.get("/meta/stats")
def stats(db: Session = Depends(get_db), user=Depends(current_user)):
    # 案件类统计同样按数据权限收敛，避免向片区稽核员泄露其他片区数量
    all_cases = db.query(Case).all()
    visible = [c for c in all_cases
               if can_access_case(user, c.region or "", c.org_codes or [])]
    by_status: dict = {}
    for c in visible:
        by_status[c.status] = by_status.get(c.status, 0) + 1
    weak_pending = db.query(IdentityEvidence).filter_by(tier="weak", status="pending").count()
    return {
        "persons": db.query(Person).count(),
        "source_records": db.query(SourceRecord).count(),
        "nodes": db.query(GraphNode).count(),
        "edges": db.query(GraphEdge).count(),
        "active_edges": db.query(GraphEdge).filter_by(status="active").count(),
        "negated_edges": db.query(GraphEdge).filter_by(status="negated").count(),
        "cases": len(visible),
        "cases_total_scoped": len(all_cases) if user.role == "admin" else None,
        "cases_by_status": by_status,
        "snapshots": db.query(GraphSnapshot).count(),
        "weak_pending": weak_pending,
    }


@router.get("/acceptance/run", dependencies=[Depends(current_user)])
def acceptance_run(db: Session = Depends(get_db)):
    return run_acceptance(db)


@router.post("/meta/reset")
def reset(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    # 关键：先结束请求会话事务并归还连接，否则它持有 users/auth_tokens 的共享锁，
    # 会与 run_seed 的 drop_all 形成自死锁。
    db.rollback()
    db.close()
    from ..seed import run_seed
    run_seed(reset=True)
    return {"ok": True, "message": "演示数据已重置并重新冻结快照 #1"}
