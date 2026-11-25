"""受限图扩展：范围由“起始索赔/受益人 + 允许边类型”定义。

默认策略（题目硬性要求）：
- 仅在同一伪标识或强关联身份簇内；
- 沿处方/结算/供药等边，最多两跳、180 天；
- 弱身份候选只生成一条待核边，对端标记 terminal_candidate，绝不继续扩展；
- negated 边默认不参与扩展（不沿用已否定子图）；
- 高金额跨机构扩展必须携带模式配置中的 extended_edge_types 与截止条件。
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Case, GraphEdge, GraphNode, PatternConfig
from . import identity

DEFAULT_CUTOFF = date.fromisoformat(settings.DEFAULT_CUTOFF)

# 默认允许的边类型（处方/结算/供药主链 + 强身份 + 关键事实边）
DEFAULT_ALLOWED = [
    "identity_strong", "prescription_edge", "settlement_edge",
    "person_visit", "visit_rx", "rx_person", "rx_settlement",
    "settle_org", "settle_supply", "supply_org",
    "ingredient_overlap", "schedule_conflict", "rx_doctor",
]


def node_to_dict(n: GraphNode) -> dict:
    return {"uid": n.uid, "node_type": n.node_type, "ref_code": n.ref_code,
            "label": n.label, "region": n.region, "org_code": n.org_code,
            "props": n.props or {}}


def edge_to_dict(e: GraphEdge) -> dict:
    return {"uid": e.uid, "edge_type": e.edge_type,
            "source": e.src_uid, "target": e.dst_uid, "stance": e.stance,
            "basis": e.basis, "source_codes": e.source_codes or [],
            "event_date": e.event_date.isoformat() if e.event_date else None,
            "status": e.status, "props": e.props or {}}


def _adjacency(db: Session):
    edges = db.query(GraphEdge).all()
    adj = {}
    for e in edges:
        adj.setdefault(e.src_uid, []).append(e)
        adj.setdefault(e.dst_uid, []).append(e)
    return edges, adj


def expand(db: Session, start_type: str, start_code: str,
           cutoff: date | None = None, window_days: int = 180, max_hops: int = 2,
           allowed_edge_types: list[str] | None = None, include_weak: bool = True,
           include_negated: bool = False) -> dict:
    cutoff = cutoff or DEFAULT_CUTOFF
    allowed = set(allowed_edge_types or DEFAULT_ALLOWED)
    lo = cutoff - timedelta(days=window_days)
    _, adj = _adjacency(db)

    # 起点与身份簇根
    roots = []
    start_uid = None
    if start_type == "beneficiary":
        start_uid = f"person:{start_code}"
        members = identity.cluster_members_of(db, start_code)
        roots = [f"person:{m}" for m in members]
    elif start_type == "claim":
        start_uid = f"settlement:{start_code}"
        roots = [start_uid]
        from ..models import Settlement
        st = db.query(Settlement).filter_by(code=start_code).first()
        if st:
            for m in identity.cluster_members_of(db, st.person_code):
                roots.append(f"person:{m}")
    else:
        start_uid = f"{start_type}:{start_code}"
        roots = [start_uid]

    depth = {u: 0 for u in roots}
    queue = list(roots)
    used_edges = {}
    terminal_candidates = []
    notes = []

    def date_ok(e: GraphEdge):
        return not e.event_date or lo <= e.event_date <= cutoff

    while queue:
        u = queue.pop(0)
        if depth[u] >= max_hops:
            continue
        for e in adj.get(u, []):
            if e.status == "negated" and not include_negated:
                continue
            if not date_ok(e):
                continue
            other = e.dst_uid if e.src_uid == u else e.src_uid

            # 弱身份边：只呈现待核边，对端为终端候选，不入队继续扩展
            if e.edge_type == "identity_weak":
                if not include_weak:
                    continue
                used_edges[e.uid] = e
                if e.status != "negated":
                    if other not in depth:
                        depth[other] = depth[u] + 1
                        terminal_candidates.append(other)
                        notes.append(f"弱候选 {other} 仅生成待核边 {e.uid}，不继续扩展")
                continue

            if e.edge_type not in allowed:
                continue
            used_edges[e.uid] = e
            if other not in depth:
                depth[other] = depth[u] + 1
                if depth[other] < max_hops:
                    queue.append(other)

    # 补入两端都已在子图中的允许边（如同层重叠边）
    for e in db.query(GraphEdge).all():
        if e.uid in used_edges:
            continue
        if e.status == "negated" and not include_negated:
            continue
        if e.src_uid in depth and e.dst_uid in depth and date_ok(e):
            if e.edge_type in allowed or (e.edge_type == "identity_weak" and include_weak):
                used_edges[e.uid] = e

    node_rows = db.query(GraphNode).filter(GraphNode.uid.in_(list(depth))).all() if depth else []
    missing = [u for u in depth if not any(n.uid == u for n in node_rows)]
    return {
        "start": {"type": start_type, "code": start_code, "uid": start_uid},
        "params": {"cutoff": cutoff.isoformat(), "window_days": window_days,
                   "max_hops": max_hops,
                   "allowed_edge_types": sorted(allowed),
                   "include_weak": include_weak, "include_negated": include_negated,
                   "cluster_scoped": True},
        "roots": roots,
        "nodes": [node_to_dict(n) for n in node_rows],
        "edges": [edge_to_dict(e) for e in used_edges.values()],
        "depths": depth,
        "terminal_candidates": terminal_candidates,
        "missing_uids": missing,
        "notes": notes,
    }


def high_amount_extension(db: Session, case_code: str, freeze: bool = False,
                          user_name: str = "system") -> dict:
    """高金额跨机构扩展：必须由模式配置显式增加边与截止条件。"""
    case = db.query(Case).filter_by(code=case_code).first()
    if not case:
        raise ValueError("案件不存在")
    pattern_code = (case.pattern_codes or [None])[0]
    cfg = db.query(PatternConfig).filter_by(code=pattern_code).first() if pattern_code else None
    threshold = float(cfg.high_amount_threshold) if cfg else 50000
    amount = float(case.amount_max or 0)
    if amount < threshold:
        return {"authorized": False,
                "reason": f"案件金额上限 ¥{amount:,.2f} 未达高金额门槛 ¥{threshold:,.0f}，"
                          f"不得启用跨机构扩展；维持默认两跳/180日范围。"}
    if not cfg or not cfg.extended_edge_types:
        return {"authorized": False,
                "reason": f"模式 {pattern_code} 未配置 extended_edge_types，"
                          "不能自行扩大边范围。"}
    allowed = list(set(DEFAULT_ALLOWED + cfg.allowed_edge_types + cfg.extended_edge_types))
    result = expand(db, "beneficiary", case.primary_person_code,
                    cutoff=DEFAULT_CUTOFF, window_days=210, max_hops=3,
                    allowed_edge_types=allowed, include_negated=False)
    result["authorized"] = True
    result["extension"] = {
        "pattern_code": pattern_code,
        "added_edge_types": cfg.extended_edge_types,
        "extension_cutoff": cfg.extension_cutoff,
        "threshold": threshold, "amount_max": amount,
        "window_days": 210, "max_hops": 3,
    }
    snapshot_no = None
    if freeze:
        from .snapshot import create_snapshot
        snap = create_snapshot(
            db, reason=f"高金额跨机构扩展冻结：{case.code} / {pattern_code}",
            edge_scope={"profile": "cross_org_high_amount", "case_code": case.code,
                        "pattern_code": pattern_code, "max_hops": 3, "window_days": 210,
                        "allowed_edge_types": allowed,
                        "extension_cutoff": cfg.extension_cutoff},
            cutoff=DEFAULT_CUTOFF, affected_case_codes=[case.code], mark_affected=True)
        snapshot_no = snap.snapshot_no
    result["frozen_snapshot_no"] = snapshot_no
    return result
