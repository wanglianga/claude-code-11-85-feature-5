"""图构建器：从来源账与业务表重建“当前结构图”。

每条边都带：具体依据 basis、可引用来源 source_codes、支持/反驳方向 stance、
业务日期 event_date 与 active/negated 状态。
另建若干粗粒度业务边（prescription_edge / settlement_edge），使“参保人起点
两跳”可覆盖处方—结算—供药主链。
派生模式边（overlap/split/...）由 patterns.upsert_derived_edges 维护。
"""
from sqlalchemy.orm import Session

from ..models import (
    Appeal, BedService, Delivery, GraphEdge, GraphNode, IdentityEvidence,
    InventoryLedger, Org, Person, Practitioner, Prescription, Referral,
    Schedule, Settlement, SourceRecord, StockTransfer, Supply, Visit,
)


def nuid(t, code):
    return f"{t}:{code}"


def eid(t, a, b):
    return f"edge:{t}:{a}__{b}"


class Builder:
    def __init__(self, db: Session):
        self.db = db
        self.src_code = {s.id: s.code for s in db.query(SourceRecord).all()}
        self.nodes = {}
        self.edges = {}

    def srcs(self, *ids):
        return [self.src_code.get(i) for i in ids if i and self.src_code.get(i)]

    def node(self, t, code, label, ntype=None, region=None, org=None, **props):
        uid = nuid(t, code)
        if uid not in self.nodes:
            self.nodes[uid] = GraphNode(
                uid=uid, node_type=ntype or t, ref_code=code, label=label,
                region=region, org_code=org, props=props)
        return uid

    def edge(self, etype, src, dst, basis, sources=None, stance="neutral",
             date=None, status="active", uid=None, **props):
        uid = uid or eid(etype, src.split(":")[-1], dst.split(":")[-1])
        if uid in self.edges:
            return uid
        e = GraphEdge(uid=uid, edge_type=etype, src_uid=src, dst_uid=dst,
                      basis=basis, source_codes=sources or [], stance=stance,
                      event_date=date, status=status, props=props)
        self.edges[uid] = e
        return uid

    def flush(self):
        self.db.add_all(list(self.nodes.values()))
        self.db.flush()
        self.db.add_all(list(self.edges.values()))
        self.db.flush()


def rebuild_graph(db: Session):
    db.query(GraphEdge).delete()
    db.query(GraphNode).delete()
    db.flush()
    b = Builder(db)

    org_name = {o.code: o.name for o in db.query(Org).all()}

    # ---------- 节点 ----------
    for p in db.query(Person).all():
        b.node("person", p.code, f"{p.code} {p.name}", region=p.region,
               pseudo=p.pseudo_id, phone=p.contact_phone, address=p.address, note=p.note)
    for o in db.query(Org).all():
        b.node("org", o.code, o.name, region=o.region, org_type=o.org_type, level=o.level)
    for d in db.query(Practitioner).all():
        org_row = db.get(Org, d.org_id)
        b.node("practitioner", d.code, f"{d.name}（{d.code}）", org=org_row.code if org_row else None,
               credential=d.credential_no, title=d.title,
               valid_to=d.credential_valid_to.isoformat() if d.credential_valid_to else None)
    for v in db.query(Visit).all():
        b.node("visit", v.code, f"就诊 {v.code} {v.visit_date}", date=v.visit_date.isoformat(),
               diagnosis=v.diagnosis)
    for r in db.query(Prescription).all():
        ings = "、".join(sorted({i.ingredient for i in r.items}))
        b.node("rx", r.code, f"处方 {r.rx_no}（{ings}）", date=r.rx_date.isoformat(),
               rx_no=r.rx_no, ingredients=ings)
    for s in db.query(Settlement).all():
        b.node("settlement", s.code, f"结算 {s.settle_no} ¥{float(s.total_amount):.0f}",
               date=s.settle_date.isoformat(), status=s.status,
               total=float(s.total_amount), fund=float(s.fund_amount),
               terminal=s.terminal_no, operator=s.operator_code)
    for sp in db.query(Supply).all():
        b.node("supply", sp.code, f"供药 {sp.ingredient} ×{sp.qty:g}",
               date=sp.supply_date.isoformat(), batch=sp.batch_no, qty=sp.qty)
    for dl in db.query(Delivery).all():
        b.node("delivery", dl.code, f"签收 {dl.receiver_name}",
               date=dl.signed_at.date().isoformat(), match=dl.signature_match)
    for sc in db.query(Schedule).all():
        b.node("schedule", sc.code, f"排班 {sc.work_date} {sc.status}",
               date=sc.work_date.isoformat(), status=sc.status, note=sc.note)
    for rf in db.query(Referral).all():
        b.node("referral", rf.code, f"转诊 {rf.referral_no}",
               date=rf.refer_date.isoformat(), reason=rf.reason)
    for ap in db.query(Appeal).all():
        b.node("appeal", ap.code, f"申诉 {ap.appeal_no}（{ap.result}）",
               date=ap.submitted_at.date().isoformat(), result=ap.result,
               returned=ap.drug_returned)
    for tr in db.query(StockTransfer).all():
        b.node("transfer", tr.code, f"调拨 {tr.transfer_no} {tr.ingredient}×{tr.qty:g}",
               date=tr.receive_date.isoformat(), qty=tr.qty)
    for bed in db.query(BedService).all():
        b.node("bed", bed.code, f"床位 {bed.bed_no}（{bed.service_type}）",
               date=bed.service_from.isoformat(), to=bed.service_to.isoformat())

    # ---------- 身份证据边 ----------
    for ev in db.query(IdentityEvidence).all():
        if ev.subject_type == "practitioner":
            src, dst = nuid("practitioner", ev.left_code), nuid("practitioner", ev.right_code)
        else:
            src, dst = nuid("person", ev.left_code), nuid("person", ev.right_code)
        if ev.status == "confirmed":
            etype, status = "identity_strong", "active"
        elif ev.tier == "strong":
            etype, status = "identity_strong", "active"
        elif ev.status == "rejected":
            etype, status = "identity_weak", "negated"
        else:
            etype, status = "identity_weak", "active"
        b.edge(etype, src, dst,
               f"{ev.get_evidence_type_display() if hasattr(ev, 'get_evidence_type_display') else ev.evidence_type}：{ev.evidence_value}",
               sources=b.srcs(ev.source_id), status=status, uid=f"edge:{etype}:{ev.code}",
               tier=ev.tier, evidence_code=ev.code, review_status=ev.status,
               terminal_candidate=(etype == "identity_weak" and status == "active"))

    # ---------- 就诊/处方链 ----------
    for v in db.query(Visit).all():
        p, vn = nuid("person", v.person_code), nuid("visit", v.code)
        b.edge("person_visit", p, vn, f"{v.person_code} 于 {v.visit_date} 在 {v.org_code} 就诊（{v.diagnosis}）",
               b.srcs(v.source_id), date=v.visit_date)
        b.edge("visit_org", vn, nuid("org", v.org_code), f"就诊机构：{org_name.get(v.org_code, v.org_code)}",
               b.srcs(v.source_id), date=v.visit_date)
        if v.practitioner_code:
            b.edge("visit_doctor", vn, nuid("practitioner", v.practitioner_code),
                   f"接诊医师 {v.practitioner_code}", b.srcs(v.source_id), date=v.visit_date)

    for r in db.query(Prescription).all():
        rn = nuid("rx", r.code)
        b.edge("rx_person", rn, nuid("person", r.person_code),
               f"处方归属参保人 {r.person_code}", b.srcs(r.source_id), date=r.rx_date)
        b.edge("prescription_edge", nuid("person", r.person_code), rn,
               f"{r.rx_date} 处方 {r.rx_no}", b.srcs(r.source_id), date=r.rx_date)
        b.edge("rx_org", rn, nuid("org", r.org_code), f"开方机构 {org_name.get(r.org_code, r.org_code)}",
               b.srcs(r.source_id), date=r.rx_date)
        if r.practitioner_code:
            b.edge("rx_doctor", rn, nuid("practitioner", r.practitioner_code),
                   f"开方医师 {r.practitioner_code}", b.srcs(r.source_id), date=r.rx_date)
        if r.visit_code:
            b.edge("visit_rx", nuid("visit", r.visit_code), rn,
                   f"就诊产生处方 {r.rx_no}", b.srcs(r.source_id), date=r.rx_date)

    # ---------- 结算/供药链 ----------
    for s in db.query(Settlement).all():
        sn = nuid("settlement", s.code)
        b.edge("settlement_edge", nuid("person", s.person_code), sn,
               f"{s.settle_date} 结算 {s.settle_no}，总额 ¥{float(s.total_amount):.2f}，基金 ¥{float(s.fund_amount):.2f}",
               b.srcs(s.source_id), date=s.settle_date,
               amount=float(s.total_amount), fund=float(s.fund_amount), status=s.status)
        b.edge("settle_org", sn, nuid("org", s.pharmacy_code),
               f"结算/供药机构：{org_name.get(s.pharmacy_code, s.pharmacy_code)}",
               b.srcs(s.source_id), date=s.settle_date)
        if s.rx_code:
            b.edge("rx_settlement", nuid("rx", s.rx_code), sn,
                   f"处方 {s.rx_code} 对应结算 {s.settle_no}", b.srcs(s.source_id),
                   date=s.settle_date,
                   status=("negated" if s.status == "corrected" else "active"))
    for sp in db.query(Supply).all():
        supn = nuid("supply", sp.code)
        b.edge("supply_org", supn, nuid("org", sp.pharmacy_code),
               f"供药药店：{org_name.get(sp.pharmacy_code, sp.pharmacy_code)}，批号 {sp.batch_no}",
               b.srcs(sp.source_id), date=sp.supply_date)
        if sp.settlement_code:
            b.edge("settle_supply", nuid("settlement", sp.settlement_code), supn,
                   f"结算 {sp.settlement_code} 供药 {sp.ingredient} ×{sp.qty:g}",
                   b.srcs(sp.source_id), date=sp.supply_date)
    for dl in db.query(Delivery).all():
        b.edge("supply_delivery", nuid("supply", dl.supply_code), nuid("delivery", dl.code),
               f"配送签收：{dl.receiver_name}，签名匹配={dl.signature_match}",
               b.srcs(dl.source_id), stance=("refute" if dl.signature_match else "support"),
               date=dl.signed_at.date())

    # ---------- 排班 ----------
    for sc in db.query(Schedule).all():
        pn = nuid("practitioner", sc.practitioner_code)
        sn = nuid("schedule", sc.code)
        b.edge("practitioner_schedule", pn, sn,
               f"{sc.work_date} 排班状态：{sc.status}。{sc.note or ''}",
               b.srcs(sc.source_id), date=sc.work_date, schedule_status=sc.status)
        b.edge("schedule_org", sn, nuid("org", sc.org_code),
               f"排班机构 {org_name.get(sc.org_code, sc.org_code)}", b.srcs(sc.source_id),
               date=sc.work_date)

    # ---------- 转诊 ----------
    for rf in db.query(Referral).all():
        rn = nuid("referral", rf.code)
        b.edge("person_referral", nuid("person", rf.person_code), rn,
               f"{rf.refer_date} 转诊 {rf.referral_no}：{rf.reason}", b.srcs(rf.source_id),
               date=rf.refer_date)
        b.edge("referral_from", rn, nuid("org", rf.from_org),
               f"转出：{org_name.get(rf.from_org, rf.from_org)}", b.srcs(rf.source_id),
               date=rf.refer_date)
        b.edge("referral_to", rn, nuid("org", rf.to_org),
               f"转入：{org_name.get(rf.to_org, rf.to_org)}", b.srcs(rf.source_id),
               date=rf.refer_date)

    # ---------- 床位 ----------
    for bed in db.query(BedService).all():
        b.edge("person_bed", nuid("person", bed.person_code), nuid("bed", bed.code),
               f"{bed.service_from}~{bed.service_to} {bed.bed_no} {bed.service_type}",
               b.srcs(bed.source_id), stance="refute", date=bed.service_from)
        b.edge("bed_org", nuid("bed", bed.code), nuid("org", bed.agency_code),
               f"床位/服务机构：{org_name.get(bed.agency_code, bed.agency_code)}",
               b.srcs(bed.source_id), stance="refute", date=bed.service_from)

    # ---------- 申诉 ----------
    for ap in db.query(Appeal).all():
        an = nuid("appeal", ap.code)
        b.edge("person_appeal", nuid("person", ap.person_code), an,
               f"申诉 {ap.appeal_no}，结果 {ap.result}，确认退回={ap.drug_returned}",
               b.srcs(ap.source_id),
               stance=("refute" if ap.result == "upheld" else "neutral"),
               date=ap.submitted_at.date())
        if ap.result == "upheld" and ap.drug_returned:
            # 关联同一人被全额退款的结算
            for s in db.query(Settlement).filter_by(person_code=ap.person_code,
                                                    status="refunded").all():
                b.edge("appeal_return_for", an, nuid("settlement", s.code),
                       f"申诉确认药品退回，对应退款结算 {s.code}（¥{float(s.refund_amount):.2f}）",
                       b.srcs(ap.source_id), stance="refute", date=ap.decided_at.date() if ap.decided_at else None)

    # ---------- 调拨 ----------
    for tr in db.query(StockTransfer).all():
        tn = nuid("transfer", tr.code)
        b.edge("transfer_from", tn, nuid("org", tr.from_pharmacy),
               f"调出：{org_name.get(tr.from_pharmacy, tr.from_pharmacy)}",
               b.srcs(tr.source_id), date=tr.ship_date)
        b.edge("transfer_to", tn, nuid("org", tr.to_pharmacy),
               f"调入：{org_name.get(tr.to_pharmacy, tr.to_pharmacy)}",
               b.srcs(tr.source_id), date=tr.receive_date, stance="refute")
        # 调拨解释串户/重复疑点：连到同成分、同窗口的更正结算
        for s in db.query(Settlement).filter(
                Settlement.pharmacy_code.in_([tr.from_pharmacy, tr.to_pharmacy])).all():
            rx = db.query(Prescription).filter_by(code=s.rx_code).first() if s.rx_code else None
            if rx and any(i.ingredient == tr.ingredient for i in rx.items):
                b.edge("explain_transfer", nuid("settlement", s.code), tn,
                       f"调拨单 {tr.transfer_no} 可解释该结算对应的表面重复/串户：{tr.ingredient}×{tr.qty:g}",
                       date=tr.receive_date, stance="refute",
                       uid=f"edge:explain_transfer:{s.code}__{tr.code}")

    b.flush()
    return len(b.nodes), len(b.edges)
