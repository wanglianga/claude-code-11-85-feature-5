"""案件投影：只呈现核查所需的最小子图、金额范围与证据出处。

投影内容在生成判断时冻结进 CaseJudgment.projection，之后图如何变化都不影响
旧案件材料；接口层再按用户数据权限决定是否可见。
"""
from sqlalchemy.orm import Session

from ..models import GraphEdge, GraphNode
from .expansion import edge_to_dict, node_to_dict


def build_projection(db: Session, case, rule_result: dict, counter_result: dict) -> dict:
    uid_order = []
    for u in rule_result.get("support_chain", []):
        if u not in uid_order:
            uid_order.append(u)
    for p in rule_result.get("trace_paths", []):
        for u in p.get("uids", []):
            if u not in uid_order:
                uid_order.append(u)

    edges = {e.uid: e for e in db.query(GraphEdge).filter(GraphEdge.uid.in_(uid_order)).all()}
    chosen = dict(edges)

    # 补入与链上端点直接相连的反驳边（反证出处），保持“最小但可解释”
    endpoint_uids = set()
    for e in edges.values():
        endpoint_uids.add(e.src_uid)
        endpoint_uids.add(e.dst_uid)
    if endpoint_uids:
        for e in db.query(GraphEdge).filter(GraphEdge.stance == "refute").all():
            if e.src_uid in endpoint_uids or e.dst_uid in endpoint_uids:
                chosen[e.uid] = e
                endpoint_uids.add(e.src_uid)
                endpoint_uids.add(e.dst_uid)

    node_uids = set()
    for e in chosen.values():
        node_uids.add(e.src_uid)
        node_uids.add(e.dst_uid)
    nodes = {n.uid: n for n in db.query(GraphNode).filter(GraphNode.uid.in_(node_uids)).all()}

    missing_edges = [u for u in uid_order if u not in edges]
    return {
        "case_code": case.code,
        "minimal": True,
        "scope_note": "仅包含该线索支持链、直接反证边及其端点的最小子图；不沿弱身份边或已否定边扩展。",
        "center_person": f"person:{case.primary_person_code}",
        "nodes": [node_to_dict(n) for n in nodes.values()],
        "edges": [edge_to_dict(e) for e in chosen.values()],
        "ordered_chain_uids": [u for u in uid_order if u in edges],
        "missing_chain_uids": missing_edges,
        "amount_range": rule_result.get("amount", {}),
        "evidence_hint": "点击边可查看 basis 与 source_codes，来源账中可查撤销/更正链。",
    }
