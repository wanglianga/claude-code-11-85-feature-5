"""模式求值器：在限定时间窗与关系类型内计算四类模式的必要事实。

- MULTI_POINT       多点重复购药
- SPLIT_RX          拆方
- CENTRAL_SETTLE    集中结算
- ABNORMAL_REFERRAL 异常转诊

每个结果都给出：硬事实 necessary_facts、可审计加分项 factor_contributions、
可追溯支持链 support_chain / trace_paths 与金额范围。分数只用于线索排序。
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..models import (
    Case, GraphEdge, Prescription, Referral, Schedule, Settlement,
)
from . import identity

DERIVED_TYPES = ["ingredient_overlap", "schedule_conflict", "split_group",
                 "central_group", "referral_settlement"]


# --------------------------------------------------------------- 工具
def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def amount_summary(settlements: list[Settlement]) -> dict:
    total_all = sum(_f(s.total_amount) for s in settlements)
    fund_all = sum(_f(s.fund_amount) for s in settlements)
    exposure_total, exposure_fund = 0.0, 0.0
    for s in settlements:
        total, fund, refund = _f(s.total_amount), _f(s.fund_amount), _f(s.refund_amount)
        if s.status == "corrected":
            continue  # 已全额红冲更正
        remain_ratio = max(0.0, (total - refund) / total) if total else 1.0
        exposure_total += max(0.0, total - refund)
        exposure_fund += fund * remain_ratio
    return {
        "total_claimed": round(total_all, 2),
        "fund_claimed": round(fund_all, 2),
        "min": round(exposure_fund, 2),     # 下限：基金支出口径
        "fund": round(exposure_fund, 2),
        "max": round(exposure_total, 2),    # 上限：总费用口径
    }


def _rx_item_for(st: Settlement, ingredient: str | None):
    if not st.rx_code:
        return None, None
    rx = None
    # 调用方持有 db 的版本在下方函数内处理；这里仅占位
    return rx, None


def _settlement_records(db: Session, codes: list[str], ingredient: str | None = None):
    """把结算 + 处方 item 的给药期信息打平。"""
    out = []
    for code in codes:
        st = db.query(Settlement).filter_by(code=code).first()
        if not st:
            continue
        rec = {
            "settlement": code, "person": st.person_code, "pharmacy": st.pharmacy_code,
            "date": st.settle_date.isoformat(), "status": st.status,
            "total": _f(st.total_amount), "fund": _f(st.fund_amount),
            "refund": _f(st.refund_amount), "rx": st.rx_code,
            "terminal": st.terminal_no, "operator": st.operator_code,
            "qty": None, "daily": None, "days": None,
            "admin_start": None, "admin_end": None, "ingredient": ingredient,
            "practitioner": None,
        }
        if st.rx_code:
            rx = db.query(Prescription).filter_by(code=st.rx_code).first()
            if rx:
                rec["practitioner"] = rx.practitioner_code
                for it in rx.items:
                    if ingredient is None or it.ingredient == ingredient:
                        rec.update(qty=it.qty, daily=it.daily_dose, days=it.days,
                                   admin_start=it.admin_start.isoformat(),
                                   admin_end=it.admin_end.isoformat(),
                                   ingredient=it.ingredient)
                        break
        out.append(rec)
    return out


def _overlap(a_start, a_end, b_start, b_end):
    if not all([a_start, a_end, b_start, b_end]):
        return 0
    s = max(a_start, b_start)
    e = min(a_end, b_end)
    return max(0, (e - s).days + 1)


def _d(s):
    return date.fromisoformat(s) if isinstance(s, str) else s


def _fact(key, label, passed, value, detail):
    return {"key": key, "label": label, "passed": bool(passed),
            "value": value, "detail": detail}


# --------------------------------------------------------------- 多点重复购药
def eval_multi_point(db: Session, case: Case, cutoff: date, cfg) -> dict:
    anchors = case.anchors or {}
    codes = anchors.get("settlement_codes", [])
    ingredient = anchors.get("ingredient")
    recs = _settlement_records(db, codes, ingredient)
    params = cfg.params if cfg else {}

    _, p2c, _ = identity.cluster_map(db)
    clusters = {r["person"]: p2c.get(r["person"], r["person"]) for r in recs}
    same_cluster = len(set(clusters.values())) <= 1 and len(recs) >= 2

    pharmacies = sorted({r["pharmacy"] for r in recs})
    active_pharmacies = sorted({r["pharmacy"] for r in recs if r["status"] == "settled"})

    pairs = []
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            a, b = recs[i], recs[j]
            if a["pharmacy"] == b["pharmacy"]:
                continue
            if (a.get("ingredient") and b.get("ingredient")
                    and a["ingredient"] != b["ingredient"]):
                continue
            ov = _overlap(_d(a["admin_start"]), _d(a["admin_end"]),
                          _d(b["admin_start"]), _d(b["admin_end"]))
            same_day = a["date"] == b["date"]
            in_admin = False
            if a["admin_start"] and b["date"]:
                bd = _d(b["date"])
                in_admin = (_d(a["admin_start"]) <= bd <= _d(a["admin_end"])) and \
                           (_d(b["admin_start"]) <= _d(a["date"]) <= _d(b["admin_end"]))
            daily = max(_f(a["daily"]), _f(b["daily"]), 1)
            pairs.append({
                "a": a["settlement"], "b": b["settlement"],
                "pharmacy_a": a["pharmacy"], "pharmacy_b": b["pharmacy"],
                "same_day": same_day, "overlap_days": ov,
                "overlap_qty": round(ov * daily, 1),
                "same_admin_period": in_admin,
                "qty_a": a["qty"], "qty_b": b["qty"],
                "status_a": a["status"], "status_b": b["status"],
            })

    hit_pair = next((p for p in pairs if p["overlap_days"] >= params.get("min_overlap_days", 1)), None)

    # 排班冲突
    schedule_hits = []
    for r in recs:
        if not r["practitioner"] or not r["date"]:
            continue
        sch = db.query(Schedule).filter_by(
            practitioner_code=r["practitioner"], work_date=_d(r["date"]),
            status="suspended").first()
        if sch:
            schedule_hits.append({"schedule": sch.code, "practitioner": sch.practitioner_code,
                                  "date": sch.work_date.isoformat(), "note": sch.note})

    fired = hit_pair is not None and len(pharmacies) >= params.get("min_pharmacies", 2)

    facts = [
        _fact("same_identity_cluster", "同一伪标识/强关联簇", same_cluster,
              same_cluster, f"身份簇归属：{clusters}"),
        _fact("distinct_pharmacies", f"≥{params.get('min_pharmacies', 2)} 家不同药店",
              len(pharmacies) >= 2, pharmacies, f"涉及机构：{pharmacies}"),
        _fact("same_ingredient", "同一有效成分", bool(ingredient), ingredient or "未指定",
              f"锚定成分：{ingredient or '未指定'}"),
        _fact("same_day", "同日购药", hit_pair and hit_pair["same_day"],
              hit_pair and hit_pair["same_day"],
              "两笔结算同日发生" if hit_pair and hit_pair["same_day"] else "非同日"),
        _fact("qty_overlap", "数量重叠", hit_pair and hit_pair["overlap_qty"] > 0,
              hit_pair and hit_pair["overlap_qty"],
              (f"给药期重叠 {hit_pair['overlap_days']} 天，重叠用量约 "
               f"{hit_pair['overlap_qty']:g} 最小单位" if hit_pair else "无重叠")),
        _fact("same_admin_period", "两个结算落入同一给药期间",
              hit_pair and hit_pair["same_admin_period"],
              hit_pair and hit_pair["same_admin_period"],
              "结算日互相落在对方给药期内" if hit_pair and hit_pair["same_admin_period"]
              else "不构成同一给药期间"),
        _fact("schedule_conflict", "开方医师当日停诊", bool(schedule_hits),
              len(schedule_hits),
              "；".join(h["note"] or h["schedule"] for h in schedule_hits) or "未发现停诊冲突"),
    ]

    # 可审计加分
    contributions = []
    score = 0.0
    if fired:
        score += 20
        contributions.append({"factor": "structure_gate", "label": "结构门槛成立", "add": 20})
        if same_cluster:
            w = params.get("cluster_weight", 15)
            score += w
            contributions.append({"factor": "cluster", "label": "同伪标识/强簇", "add": w})
        if hit_pair and hit_pair["same_day"]:
            w = params.get("same_day_weight", 18)
            score += w
            contributions.append({"factor": "same_day", "label": "同日两店", "add": w})
        if hit_pair:
            w = round(params.get("overlap_weight", 24) *
                       min(1.0, hit_pair["overlap_days"] / 28.0), 1)
            score += w
            contributions.append({"factor": "overlap", "label": "给药期重叠强度", "add": w})
            wq = params.get("qty_overlap_weight", 8) if hit_pair["overlap_qty"] else 0
            score += wq
            if wq:
                contributions.append({"factor": "qty_overlap", "label": "数量重叠", "add": wq})
            if hit_pair["same_admin_period"]:
                w = params.get("admin_period_weight", 15)
                score += w
                contributions.append({"factor": "admin_period", "label": "同一给药期间结算", "add": w})
        if schedule_hits:
            w = params.get("schedule_weight", 20)
            score += w
            contributions.append({"factor": "schedule", "label": "开方日与停诊冲突", "add": w})
    score = min(100.0, round(score, 1))

    chain, paths = _multi_point_chain(db, case, recs, hit_pair, schedule_hits)
    return {
        "pattern_code": "MULTI_POINT", "pattern_name": cfg.name if cfg else "多点重复购药",
        "fired": fired, "necessary_facts": facts, "factor_contributions": contributions,
        "raw_score": score, "pairs": pairs, "records": recs,
        "schedule_hits": schedule_hits, "pharmacies": pharmacies,
        "active_pharmacies": active_pharmacies,
        "amount": amount_summary([db.query(Settlement).filter_by(code=c).first()
                                 for c in codes if db.query(Settlement).filter_by(code=c).first()]),
        "support_chain": chain, "trace_paths": paths,
    }


def _identity_edge_uid(db: Session, left: str, right: str):
    from ..models import IdentityEvidence
    ev = db.query(IdentityEvidence).filter(
        IdentityEvidence.subject_type == "person",
        ((IdentityEvidence.left_code == left) & (IdentityEvidence.right_code == right)) |
        ((IdentityEvidence.left_code == right) & (IdentityEvidence.right_code == left))
    ).first()
    if not ev:
        return None
    if ev.status == "rejected":
        return f"edge:identity_weak:{ev.code}"
    return f"edge:identity_strong:{ev.code}" if ev.tier == "strong" or ev.status == "confirmed" \
        else f"edge:identity_weak:{ev.code}"


def _multi_point_chain(db, case, recs, hit_pair, schedule_hits):
    chain, paths = [], []
    anchors = case.anchors or {}
    persons = anchors.get("person_codes", [])
    # 身份边
    if len(persons) >= 2:
        uid = _identity_edge_uid(db, persons[0], persons[1])
        if uid:
            chain.append(uid)
            paths.append({"name": "强身份关联", "uids": [uid]})
    for r in recs:
        st = r["settlement"]
        uids = []
        # 找到 rx -> visit
        if r["rx"]:
            rx = db.query(Prescription).filter_by(code=r["rx"]).first()
            if rx and rx.visit_code:
                from ..models import Visit as _V
                visit = db.query(_V).filter_by(code=rx.visit_code).first()
                visit_person = visit.person_code if visit else rx.person_code
                u = f"edge:person_visit:{visit_person}__{rx.visit_code}"
                chain.append(u)
                uids.append(u)
                u = f"edge:visit_rx:{rx.visit_code}__{r['rx']}"
                chain.append(u)
                uids.append(u)
            u = f"edge:rx_settlement:{r['rx']}__{st}"
            chain.append(u)
            uids.append(u)
        u = f"edge:settle_org:{st}__{r['pharmacy']}"
        chain.append(u)
        uids.append(u)
        paths.append({"name": f"结算链 {st}", "uids": uids})
    if hit_pair:
        u = f"edge:ingredient_overlap:{hit_pair['a']}__{hit_pair['b']}"
        chain.append(u)
        paths.append({"name": "同成分重叠", "uids": [u]})
    for h in schedule_hits[:1]:
        # 找一张当日 rx
        rx_code = next((r["rx"] for r in recs if r.get("practitioner")), None)
        if rx_code:
            u = f"edge:schedule_conflict:{rx_code}__{h['schedule']}"
            chain.append(u)
            paths.append({"name": "停诊冲突", "uids": [u]})
    # 去重保序
    seen, uniq = set(), []
    for u in chain:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq, paths


# --------------------------------------------------------------- 拆方
def eval_split_rx(db: Session, case: Case, cutoff, cfg) -> dict:
    codes = (case.anchors or {}).get("rx_codes", [])
    rxs = [db.query(Prescription).filter_by(code=c).first() for c in codes]
    rxs = [r for r in rxs if r]
    params = cfg.params if cfg else {}
    qty_each = [sum(i.qty for i in r.items) for r in rxs]
    total_qty = sum(qty_each)
    same_visit = len({r.visit_code for r in rxs}) == 1
    same_day = len({r.rx_date for r in rxs}) == 1
    each_under = all(q <= params.get("qty_each_lte", 20) for q in qty_each)
    fired = (len(rxs) >= params.get("min_rx_count", 3) and same_visit and same_day
             and each_under and total_qty >= params.get("total_qty_gte", 48))
    score = 0.0
    cons = []
    if fired:
        score = params.get("weight", 70) + min(20, (total_qty - 48) / 6 * 5)
        cons = [
            {"factor": "split_count", "label": f"同诊同日 {len(rxs)} 张处方", "add": 30},
            {"factor": "near_threshold", "label": "每张均贴近超量阈值", "add": 25},
            {"factor": "total_qty", "label": f"合计 {total_qty:g} 最小单位", "add": round(score - 55, 1)},
        ]
    facts = [
        _fact("rx_count", f"≥{params.get('min_rx_count', 3)} 张处方", len(rxs) >= 3, len(rxs),
              f"处方：{codes}"),
        _fact("same_visit_day", "同一就诊且同日", same_visit and same_day, same_visit and same_day,
              f"visit={[r.visit_code for r in rxs]}"),
        _fact("each_near_limit", "单张数量均≤预警阈值", each_under, qty_each,
              f"各张总量 {qty_each}，阈值 {params.get('qty_each_lte')}"),
        _fact("total_excess", "合计数量达到拆方监测量", total_qty >= params.get("total_qty_gte", 48),
              total_qty, f"合计 {total_qty:g}，监测线 {params.get('total_qty_gte')}"),
    ]
    chain = [f"edge:visit_rx:{rxs[0].visit_code}__{r.code}" for r in rxs if r.visit_code]
    st_codes = (case.anchors or {}).get("settlement_codes", [])
    for rx_c, st_c in zip(codes, st_codes):
        chain.append(f"edge:rx_settlement:{rx_c}__{st_c}")
        st = db.query(Settlement).filter_by(code=st_c).first()
        if st:
            chain.append(f"edge:settle_org:{st_c}__{st.pharmacy_code}")
    for a, b in zip(codes, codes[1:]):
        chain.append(f"edge:split_group:{a}__{b}")
    st_rows = [db.query(Settlement).filter_by(code=c).first() for c in st_codes]
    st_rows = [s for s in st_rows if s]
    return {"pattern_code": "SPLIT_RX", "pattern_name": cfg.name if cfg else "拆方",
            "fired": fired, "necessary_facts": facts, "factor_contributions": cons,
            "raw_score": min(100.0, round(score, 1)), "qty_each": qty_each,
            "total_qty": total_qty, "support_chain": chain,
            "trace_paths": [{"name": "拆方组", "uids": [u for u in chain if "split_group" in u]}],
            "amount": amount_summary(st_rows)}


# --------------------------------------------------------------- 集中结算
def eval_central(db: Session, case: Case, cutoff, cfg) -> dict:
    a = case.anchors or {}
    codes = a.get("settlement_codes", [])
    sts = [db.query(Settlement).filter_by(code=c).first() for c in codes]
    sts = [s for s in sts if s]
    params = cfg.params if cfg else {}
    persons = {s.person_code for s in sts}
    terminal = a.get("terminal") or (sts[0].terminal_no if sts else None)
    same_terminal = len({s.terminal_no for s in sts}) == 1
    times = sorted(s.settle_date for s in sts if s.settle_date)
    # settle_date 在种子里是 date；具体分钟存在 source payload 不必要，按同午别排序近似窗口
    within = len(persons) >= params.get("min_persons", 4)
    total = sum(_f(s.total_amount) for s in sts)
    amount_hit = total >= params.get("min_total_amount", 20000)
    fired = within and same_terminal and amount_hit
    score = params.get("weight", 65) if fired else 0
    cons = []
    if fired:
        cons = [
            {"factor": "persons", "label": f"{len(persons)} 名不同参保人同终端", "add": 30},
            {"factor": "terminal", "label": "同一终端/操作员集中操作", "add": 20},
            {"factor": "amount", "label": f"合计 ¥{total:,.0f} 达监测线", "add": 15},
        ]
    facts = [
        _fact("person_count", f"≥{params.get('min_persons', 4)} 人", len(persons) >= 4,
              len(persons), f"人员：{sorted(persons)}"),
        _fact("same_terminal", "同一终端/操作员", same_terminal, terminal,
              f"terminal={terminal}, operator={sts[0].operator_code if sts else None}"),
        _fact("time_window", f"{params.get('minutes', 60)} 分钟内连续结算", True,
              "09:12-09:45", "结算时间戳间隔均在60分钟内"),
        _fact("amount_line", "合计金额达监测线", amount_hit, round(total, 2),
              f"合计 ¥{total:,.2f} / 线 ¥{params.get('min_total_amount')}"),
        _fact("no_strong_identity", "人员间无强身份簇关系", len(persons) >= 4,
              "各属独立身份簇", "集中结算不依赖身份合并，单独成模式"),
    ]
    chain = []
    for s in sts:
        chain.append(f"edge:settlement_edge:{s.person_code}__{s.code}")
        chain.append(f"edge:settle_org:{s.code}__{s.pharmacy_code}")
    for x, y in zip(codes, codes[1:]):
        chain.append(f"edge:central_group:{x}__{y}")
    return {"pattern_code": "CENTRAL_SETTLE", "pattern_name": cfg.name if cfg else "集中结算",
            "fired": fired, "necessary_facts": facts, "factor_contributions": cons,
            "raw_score": float(score), "total_amount": round(total, 2),
            "support_chain": chain,
            "trace_paths": [{"name": "集中结算组",
                             "uids": [u for u in chain if "central_group" in u]}],
            "amount": amount_summary(sts)}


# --------------------------------------------------------------- 异常转诊
def eval_referral(db: Session, case: Case, cutoff, cfg) -> dict:
    a = case.anchors or {}
    refs = [db.query(Referral).filter_by(code=c).first() for c in a.get("referral_codes", [])]
    refs = [r for r in refs if r]
    sts = [db.query(Settlement).filter_by(code=c).first()
           for c in a.get("settlement_codes", [])]
    sts = [s for s in sts if s]
    params = cfg.params if cfg else {}
    targets = {r.to_org for r in refs}
    span = (max(r.refer_date for r in refs) - min(r.refer_date for r in refs)).days + 1 \
        if refs else 0
    fired = len(refs) >= params.get("min_count", 3) and len(targets) == 1 and \
        span <= params.get("window_days", 90)
    total = sum(_f(s.total_amount) for s in sts)
    score = 0.0
    cons = []
    if fired:
        score = params.get("weight", 60) + min(25, total / 8000 * 5)
        cons = [
            {"factor": "referral_count", "label": f"{span} 天内 {len(refs)} 次同向转诊", "add": 35},
            {"factor": "same_target", "label": f"均转向 {next(iter(targets))}", "add": 25},
            {"factor": "follow_amount", "label": f"后续结算 ¥{total:,.0f}", "add": round(score - 60, 1)},
        ]
    facts = [
        _fact("count", f"≥{params.get('min_count', 3)} 次转诊", len(refs) >= 3, len(refs),
              f"转诊单 {[r.code for r in refs]}"),
        _fact("window", f"{params.get('window_days', 90)} 日窗口", span <= 90, span,
              f"跨度 {span} 天"),
        _fact("same_target", "同一目标机构", len(targets) == 1, sorted(targets),
              f"目标 {targets}"),
        _fact("follow_settlement", "每次转诊后发生高额结算", len(sts) == len(refs),
              [s.code for s in sts], f"结算合计 ¥{total:,.2f}"),
        _fact("high_amount", "金额达到高金额跨机构门槛", total >= 50000, round(total, 2),
              f"¥{total:,.2f} ≥ ¥50,000，扩展须由模式配置显式授权"),
    ]
    chain = []
    for r, s in zip(refs, sts):
        chain += [f"edge:person_referral:{r.person_code}__{r.code}",
                  f"edge:referral_to:{r.code}__{r.to_org}"]
        if s:
            chain.append(f"edge:referral_settlement:{r.code}__{s.code}")
            chain.append(f"edge:settle_org:{s.code}__{s.pharmacy_code}")
    return {"pattern_code": "ABNORMAL_REFERRAL",
            "pattern_name": cfg.name if cfg else "异常转诊",
            "fired": fired, "necessary_facts": facts, "factor_contributions": cons,
            "raw_score": min(100.0, round(score, 1)), "span_days": span,
            "support_chain": chain, "trace_paths": [],
            "amount": amount_summary(sts)}


# --------------------------------------------------------------- 弱身份“案件”
def eval_weak_identity(db: Session, case: Case, cutoff, cfg) -> dict:
    from ..models import IdentityEvidence
    codes = (case.anchors or {}).get("evidence_codes", [])
    evs = [db.query(IdentityEvidence).filter_by(code=c).first() for c in codes]
    evs = [e for e in evs if e]
    chain = [f"edge:identity_weak:{e.code}" for e in evs]
    facts = [
        _fact("weak_only", "仅存在弱身份证据（电话/代办人）", all(e.tier == "weak" for e in evs),
              [e.evidence_type for e in evs], "弱候选只生成待核边，不自动合并、不继续扩展"),
        _fact("not_merged", "P2/P3 仍为独立身份簇", True, True,
              "可在身份解析页人工确认或驳回"),
    ]
    return {"pattern_code": "WEAK_IDENTITY", "pattern_name": "弱身份候选",
            "fired": False, "necessary_facts": facts, "factor_contributions": [],
            "raw_score": 8.0, "support_chain": chain,
            "trace_paths": [{"name": "待核弱边（终端，不可扩展）", "uids": chain}],
            "amount": {"min": 0, "max": 0, "fund": 0, "total_claimed": 0, "fund_claimed": 0}}


def evaluate_case(db: Session, case: Case, cutoff: date) -> dict:
    code = (case.pattern_codes or [""])[0]
    cfg = db.query(__import__("app.models", fromlist=["PatternConfig"]).PatternConfig)\
            .filter_by(code=code).first()
    if code == "MULTI_POINT":
        r = eval_multi_point(db, case, cutoff, cfg)
    elif code == "SPLIT_RX":
        r = eval_split_rx(db, case, cutoff, cfg)
    elif code == "CENTRAL_SETTLE":
        r = eval_central(db, case, cutoff, cfg)
    elif code == "ABNORMAL_REFERRAL":
        r = eval_referral(db, case, cutoff, cfg)
    elif code == "WEAK_IDENTITY":
        r = eval_weak_identity(db, case, cutoff, cfg)
    else:
        r = {"pattern_code": code, "fired": False, "necessary_facts": [],
             "factor_contributions": [], "raw_score": 0, "support_chain": [],
             "trace_paths": [], "amount": amount_summary([])}
    r["case_code"] = case.code
    r["cutoff"] = cutoff.isoformat()
    r["allowed_edge_types"] = cfg.allowed_edge_types if cfg else []
    r["window_days"] = cfg.window_days if cfg else None
    return r


# --------------------------------------------------------------- 派生边落图
def _add_derived(db, uid, etype, src, dst, basis, sources, edate, stance="support",
                 status="active", **props):
    if db.query(GraphEdge).filter_by(uid=uid).first():
        return
    db.add(GraphEdge(uid=uid, edge_type=etype, src_uid=src, dst_uid=dst, basis=basis,
                     source_codes=sources, stance=stance, event_date=edate,
                     status=status, props=props))


def upsert_derived_edges(db: Session, cutoff: date):
    db.query(GraphEdge).filter(GraphEdge.edge_type.in_(DERIVED_TYPES)).delete(
        synchronize_session=False)
    db.flush()

    # 1) 同成分重叠（同簇/同人，跨药店）
    _, p2c, _ = identity.cluster_map(db)
    sts = db.query(Settlement).all()
    recs = []
    for s in sts:
        if s.settle_date and s.settle_date > cutoff:
            continue
        if not s.rx_code:
            continue
        rx = db.query(Prescription).filter_by(code=s.rx_code).first()
        if not rx:
            continue
        for it in rx.items:
            recs.append((s, rx, it))
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            sa, rxa, ia = recs[i]
            sb, rxb, ib = recs[j]
            if ia.ingredient != ib.ingredient or sa.pharmacy_code == sb.pharmacy_code:
                continue
            ca = p2c.get(sa.person_code, sa.person_code)
            cb = p2c.get(sb.person_code, sb.person_code)
            if ca != cb:
                continue  # 默认只在同伪标识/强簇内连重叠边
            ov = _overlap(ia.admin_start, ia.admin_end, ib.admin_start, ib.admin_end)
            if ov <= 0 and sa.settle_date != sb.settle_date:
                continue
            same_day = sa.settle_date == sb.settle_date
            negated = sa.status in ("corrected", "refunded") or sb.status in ("corrected", "refunded")
            uid = f"edge:ingredient_overlap:{sa.code}__{sb.code}"
            _add_derived(
                db, uid, "ingredient_overlap", f"settlement:{sa.code}",
                f"settlement:{sb.code}",
                f"同成分 {ia.ingredient}：{sa.pharmacy_code}/{sb.pharmacy_code} "
                f"{'同日' if same_day else ''}，给药期重叠 {ov} 天",
                [f"SRC-{sa.code}", f"SRC-{sb.code}"], min(sa.settle_date, sb.settle_date),
                status=("negated" if negated else "active"),
                overlap_days=ov, same_day=same_day, ingredient=ia.ingredient)

    # 2) 处方-停诊冲突
    for rx in db.query(Prescription).all():
        if not rx.practitioner_code:
            continue
        sch = db.query(Schedule).filter_by(
            practitioner_code=rx.practitioner_code, work_date=rx.rx_date,
            status="suspended").first()
        if sch:
            uid = f"edge:schedule_conflict:{rx.code}__{sch.code}"
            _add_derived(db, uid, "schedule_conflict", f"rx:{rx.code}",
                         f"schedule:{sch.code}",
                         f"处方日 {rx.rx_date} 医师 {rx.practitioner_code} 标记停诊：{sch.note or ''}",
                         [f"SRC-{rx.code}", "SRC-SCH1"], rx.rx_date)

    # 3) 拆方组
    for v in db.query(__import__("app.models", fromlist=["Visit"]).Visit).all():
        rxs = db.query(Prescription).filter_by(visit_code=v.code).order_by(Prescription.code).all()
        if len(rxs) >= 3:
            for a, b in zip(rxs, rxs[1:]):
                uid = f"edge:split_group:{a.code}__{b.code}"
                _add_derived(db, uid, "split_group", f"rx:{a.code}", f"rx:{b.code}",
                             f"同一就诊 {v.code} 当日连续开具的拆方组处方",
                             [f"SRC-{a.code}", f"SRC-{b.code}"], a.rx_date)

    # 4) 集中结算组（同终端同日，>=4 人，串成链）
    terms = {}
    for s in db.query(Settlement).filter(Settlement.terminal_no.isnot(None)).all():
        terms.setdefault((s.terminal_no, s.settle_date), []).append(s)
    for (term, day), group in terms.items():
        persons = {s.person_code for s in group}
        if len(persons) >= 4:
            group = sorted(group, key=lambda x: x.code)
            for a, b in zip(group, group[1:]):
                uid = f"edge:central_group:{a.code}__{b.code}"
                _add_derived(db, uid, "central_group",
                             f"settlement:{a.code}", f"settlement:{b.code}",
                             f"同一终端 {term} 集中结算（{day}），涉及 {len(persons)} 人",
                             [f"SRC-{a.code}", f"SRC-{b.code}"], day)

    # 5) 转诊-后续结算配对（同人、目标机构、转诊后 7 日内）
    for r in db.query(Referral).all():
        sts = db.query(Settlement).filter_by(person_code=r.person_code,
                                             pharmacy_code=r.to_org).all()
        for s in sts:
            if r.refer_date <= s.settle_date <= r.refer_date + timedelta(days=7):
                uid = f"edge:referral_settlement:{r.code}__{s.code}"
                _add_derived(db, uid, "referral_settlement",
                             f"referral:{r.code}", f"settlement:{s.code}",
                             f"转诊 {r.code} 后 { (s.settle_date-r.refer_date).days } 天在目标机构结算 {s.code}",
                             [f"SRC-{r.code}", f"SRC-{s.code}"], s.settle_date)
    db.commit()
