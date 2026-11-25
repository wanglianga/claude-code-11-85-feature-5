"""图快照：冻结分析截止日、身份簇版本、边范围与政策版本。

关系裁定、政策有效日变化、退费更正后创建新快照并标出受影响案件；
旧 CaseJudgment 仍指向旧快照号，材料与原判断来源不被覆盖。
"""
from datetime import date

from sqlalchemy.orm import Session

from ..models import Case, GraphEdge, GraphNode, GraphSnapshot, Policy
from . import identity
from .expansion import edge_to_dict, node_to_dict


def policy_versions_at(db: Session, cutoff: date) -> list[dict]:
    out = []
    for p in db.query(Policy).order_by(Policy.code, Policy.effective_from).all():
        if p.effective_from <= cutoff and (p.effective_to is None or p.effective_to >= cutoff):
            out.append({"code": p.code, "version": p.version,
                        "effective_from": p.effective_from.isoformat(),
                        "title": p.title, "citation": p.citation})
    return out


def create_snapshot(db: Session, reason: str, edge_scope: dict, cutoff: date,
                    affected_case_codes: list[str] | None = None,
                    mark_affected: bool = True, label: str | None = None) -> GraphSnapshot:
    last = db.query(GraphSnapshot).order_by(GraphSnapshot.snapshot_no.desc()).first()
    no = 1 if last is None else last.snapshot_no + 1
    iv = identity.latest_version(db)
    nodes = [node_to_dict(n) for n in db.query(GraphNode).order_by(GraphNode.uid).all()]
    edges = [edge_to_dict(e) for e in db.query(GraphEdge).order_by(GraphEdge.uid).all()]
    affected = affected_case_codes or []
    snap = GraphSnapshot(
        snapshot_no=no, label=label or f"快照 #{no}", cutoff_date=cutoff,
        identity_version=iv.version if iv else 0, edge_scope=edge_scope,
        policy_versions=policy_versions_at(db, cutoff), trigger_reason=reason,
        graph={"nodes": nodes, "edges": edges,
               "counts": {"nodes": len(nodes), "edges": len(edges),
                          "active_edges": sum(1 for e in edges if e["status"] == "active"),
                          "negated_edges": sum(1 for e in edges if e["status"] == "negated"),
                          "support_edges": sum(1 for e in edges if e["stance"] == "support"),
                          "refute_edges": sum(1 for e in edges if e["stance"] == "refute")}},
        affected_case_codes=affected)
    db.add(snap)
    db.flush()
    if mark_affected and affected:
        for c in db.query(Case).filter(Case.code.in_(affected)).all():
            c.affected = True
            c.affected_reason = reason
    db.commit()
    db.refresh(snap)
    return snap


def snapshot_summary(s: GraphSnapshot) -> dict:
    return {"id": s.id, "snapshot_no": s.snapshot_no, "label": s.label,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "cutoff_date": s.cutoff_date.isoformat(),
            "identity_version": s.identity_version, "edge_scope": s.edge_scope,
            "policy_versions": s.policy_versions, "trigger_reason": s.trigger_reason,
            "affected_case_codes": s.affected_case_codes or [],
            "counts": (s.graph or {}).get("counts", {})}


def diff_snapshots(db: Session, from_no: int, to_no: int) -> dict:
    a = db.query(GraphSnapshot).filter_by(snapshot_no=from_no).first()
    b = db.query(GraphSnapshot).filter_by(snapshot_no=to_no).first()
    if not a or not b:
        raise ValueError("快照不存在")
    ea = {e["uid"]: e for e in a.graph.get("edges", [])}
    eb = {e["uid"]: e for e in b.graph.get("edges", [])}
    added = [u for u in eb if u not in ea]
    removed = [u for u in ea if u not in eb]
    status_changed = []
    for u in ea.keys() & eb.keys():
        if ea[u]["status"] != eb[u]["status"] or ea[u]["stance"] != eb[u]["stance"]:
            status_changed.append({"uid": u, "from_status": ea[u]["status"],
                                   "to_status": eb[u]["status"],
                                   "from_stance": ea[u]["stance"],
                                   "to_stance": eb[u]["stance"]})
    return {"from": from_no, "to": to_no,
            "added": added, "removed": removed, "status_changed": status_changed,
            "affected": b.affected_case_codes or []}
