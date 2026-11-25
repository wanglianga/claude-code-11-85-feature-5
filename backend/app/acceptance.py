"""虚构网络验收断言（只读）。

对应题目验收剧本：
P1 双药店同日同成分重叠 + 停诊 + 同一给药期；规则与图模型同链；
P2/P3 弱身份不合并、不扩展；P4 调拨解释合法供药；P5 申诉退回关闭。
"""
from datetime import date

from sqlalchemy.orm import Session

from .models import (
    Appeal, Case, CaseJudgment, GraphEdge, IdentityEvidence, SourceRecord,
)
from .services import identity
from .services.expansion import expand, high_amount_extension
from .services.ranking import rank_cases


def _chk(key, name, passed, expected, actual, detail=""):
    return {"key": key, "name": name, "passed": bool(passed),
            "expected": expected, "actual": actual, "detail": detail}


def run_acceptance(db: Session) -> dict:
    checks = []

    # 1) 来源账 ----------------------------------------------------------
    src_total = db.query(SourceRecord).count()
    no_citation = db.query(SourceRecord).filter(
        (SourceRecord.citation.is_(None)) | (SourceRecord.citation == "")).count()
    old = db.query(SourceRecord).filter_by(code="SRC-ST5B").first()
    corr = db.query(SourceRecord).filter_by(code="SRC-ST5B-C2").first()
    checks.append(_chk(
        "source_ledger", "来源账：记录可引用且更正链完整",
        src_total >= 50 and no_citation == 0 and old and old.status == "corrected"
        and corr and corr.supersedes_id == old.id,
        ">=50 条来源、0 条缺引用、SRC-ST5B 被更正单接续",
        {"total": src_total, "missing_citation": no_citation,
         "old_status": old.status if old else None,
         "correction_linked": bool(corr and old and corr.supersedes_id == old.id)}))

    # 2) 身份解析分级 -----------------------------------------------------
    ie01 = db.query(IdentityEvidence).filter_by(code="IE-01").first()
    ie03 = db.query(IdentityEvidence).filter_by(code="IE-03").first()
    v, p2c, c2m = identity.cluster_map(db)
    p1_same = p2c.get("P1") == p2c.get("P1B")
    p23_sep = p2c.get("P2") != p2c.get("P3")
    checks.append(_chk(
        "identity_grading", "强证据成簇、弱电话候选不合并",
        ie01 and ie01.tier == "strong" and ie01.status == "auto" and p1_same
        and ie03 and ie03.tier == "weak" and ie03.status == "pending" and p23_sep,
        "P1/P1B 同强簇；P2/P3 异簇且 IE-03 pending",
        {"identity_version": v.version if v else None,
         "P1_cluster": p2c.get("P1"), "P1B_cluster": p2c.get("P1B"),
         "P2_cluster": p2c.get("P2"), "P3_cluster": p2c.get("P3"),
         "IE-03_status": ie03.status if ie03 else None}))

    # 3) P1 必要事实 ------------------------------------------------------
    c1 = db.query(Case).filter_by(code="C-001").first()
    j1 = db.query(CaseJudgment).filter_by(id=c1.current_judgment_id).first()
    rr1 = j1.rule_result
    facts = {f["key"]: f for f in rr1["necessary_facts"]}
    chain1 = rr1["support_chain"]
    required_uids = [
        "edge:identity_strong:IE-01",
        "edge:rx_settlement:RX1__ST1", "edge:rx_settlement:RX2__ST2",
        "edge:settle_org:ST1__PH1", "edge:settle_org:ST2__PH2",
        "edge:ingredient_overlap:ST1__ST2",
        "edge:schedule_conflict:RX1__SCH1",
    ]
    missing = [u for u in required_uids if u not in chain1]
    p1_facts_ok = (
        rr1["fired"]
        and facts["distinct_pharmacies"]["passed"]
        and facts["same_day"]["passed"]
        and facts["qty_overlap"]["value"] >= 28
        and facts["same_admin_period"]["passed"]
        and facts["schedule_conflict"]["passed"]
        and not missing
    )
    checks.append(_chk(
        "p1_chain", "P1：两药店同日同成分数量重叠+停诊+同一给药期，支持链完整",
        p1_facts_ok, "全部必要事实成立且 7 条核心边在链",
        {"fired": rr1["fired"],
         "overlap_qty": facts["qty_overlap"]["value"],
         "same_admin_period": facts["same_admin_period"]["passed"],
         "schedule": facts["schedule_conflict"]["passed"],
         "missing_uids": missing, "chain": chain1}))

    # 4) 规则与图模型同链 + 同目标排序 P1 居首 ------------------------------
    mr1 = j1.model_result or {}
    same_chain = mr1.get("trace_chain_uids") == chain1
    open_rows = [r for r in rank_cases(db, "MULTI_POINT", "model")
                 if r["status"] in ("pending", "reviewing", "new")]
    rule_rows = [r for r in rank_cases(db, "MULTI_POINT", "amount_completeness")
                 if r["status"] in ("pending", "reviewing", "new")]
    top_model = open_rows[0]["code"] if open_rows else None
    top_rule = rule_rows[0]["code"] if rule_rows else None
    checks.append(_chk(
        "same_chain_and_rank", "规则与图模型在 P1 上展示相同支持链，且同目标排序居首",
        same_chain and top_model == "C-001" and top_rule == "C-001"
        and (mr1.get("calibrated_score") or 0) >= 55,
        "trace_chain_uids == support_chain；两种排序开放线索第一均为 C-001；模型分≥55",
        {"same_chain": same_chain, "top_model": top_model, "top_rule": top_rule,
         "model_score": mr1.get("calibrated_score"),
         "rule_score": j1.rule_result.get("raw_score")}))

    # 5) P2/P3 弱身份：只给待核边，不扩展 --------------------------------
    exp23 = expand(db, "beneficiary", "P2")
    uids23 = {e["uid"] for e in exp23["edges"]}
    nodes23 = {n["uid"] for n in exp23["nodes"]}
    weak_ok = ("edge:identity_weak:IE-03" in uids23
               and "person:P3" in exp23["terminal_candidates"]
               and "settlement:ST4" not in nodes23
               and "rx:RX4" not in nodes23)
    checks.append(_chk(
        "p23_weak_no_expand", "P2/P3：弱电话候选只生成待核边，不沿 P3 继续扩展",
        weak_ok, "IE-03 在图；P3 为 terminal；ST4/RX4 不可达",
        {"weak_edge_present": "edge:identity_weak:IE-03" in uids23,
         "terminal": exp23["terminal_candidates"],
         "ST4_reached": "settlement:ST4" in nodes23,
         "RX4_reached": "rx:RX4" in nodes23}))

    # 6) P4 调拨解释 + 否定边不扩展 --------------------------------------
    c3 = db.query(Case).filter_by(code="C-003").first()
    j3 = db.query(CaseJudgment).filter_by(id=c3.current_judgment_id).first()
    inv = next((c for c in j3.counter_result.get("checks", [])
                if c["check_type"] == "inventory"), None)
    ov = db.query(GraphEdge).filter(
        GraphEdge.edge_type == "ingredient_overlap",
        GraphEdge.uid.like("%ST5A%ST5B%")).first()
    exp4 = expand(db, "claim", "ST5A")
    exp4_uids = {e["uid"] for e in exp4["edges"]}
    p4_ok = (c3.status == "excluded_legitimate" and inv
             and inv["status"] == "checked_explains" and "TR4" in inv["detail"]
             and ov and ov.status == "negated"
             and "edge:ingredient_overlap:ST5A__ST5B" not in exp4_uids)
    checks.append(_chk(
        "p4_transfer", "P4：调拨+红冲解释表面重复，重叠边否定且不再扩展",
        p4_ok, "C-003 excluded_legitimate；inventory=checked_explains(TR4)；overlap negated 且扩展不可见",
        {"status": c3.status, "inventory": inv and inv["status"],
         "overlap_status": ov and ov.status,
         "overlap_in_expansion": "edge:ingredient_overlap:ST5A__ST5B" in exp4_uids}))

    # 7) P5 申诉退回 -----------------------------------------------------
    app1 = db.query(Appeal).filter_by(code="APP1").first()
    c4 = db.query(Case).filter_by(code="C-004").first()
    j4 = db.query(CaseJudgment).filter_by(id=c4.current_judgment_id).first()
    ap = next((c for c in j4.counter_result.get("checks", [])
               if c["check_type"] == "appeal"), None)
    checks.append(_chk(
        "p5_appeal_return", "P5：申诉成立且确认药品退回，线索关闭",
        app1 and app1.result == "upheld" and app1.drug_returned
        and c4.status == "closed_returned" and ap and ap["status"] == "confirmed"
        and ap["effect"] == "close",
        "APP1 upheld/returned；C-004 closed_returned；appeal check confirmed/close",
        {"appeal": app1.result if app1 else None,
         "returned": app1.drug_returned if app1 else None,
         "case_status": c4.status, "check": ap and ap["status"]}))

    # 8) 默认受限扩展 ----------------------------------------------------
    exp1 = expand(db, "beneficiary", "P1")
    cutoff = date(2026, 3, 31)
    bad_dates = [e["uid"] for e in exp1["edges"] if e["event_date"]
                 and not (date.fromisoformat("2025-10-03")
                          <= date.fromisoformat(e["event_date"]) <= cutoff)]
    n1 = {n["uid"] for n in exp1["nodes"]}
    bounds_ok = (exp1["params"]["max_hops"] == 2 and exp1["params"]["window_days"] == 180
                 and not bad_dates and "schedule:SCH1" in n1
                 and "org:PH1" in n1 and "org:PH2" in n1)
    checks.append(_chk(
        "restricted_expansion", "默认扩展：同强簇、两跳、180 日，主链节点可达且无越窗边",
        bounds_ok, "hops=2/window=180；SCH1/PH1/PH2 可达；无越窗边",
        {"node_count": len(exp1["nodes"]), "edge_count": len(exp1["edges"]),
         "out_of_window": bad_dates}))

    # 9) 高金额跨机构扩展门槛 --------------------------------------------
    low = high_amount_extension(db, "C-001", freeze=False)
    high = high_amount_extension(db, "C-007", freeze=False)
    bed_reached = any(n["node_type"] == "bed" for n in high.get("nodes", []))
    checks.append(_chk(
        "high_amount_gate", "高金额跨机构扩展须模式显式授权：P1 被拒、C-007 授权并加入床位边",
        low["authorized"] is False and high["authorized"] is True and bed_reached,
        "C-001 金额不足拒绝；C-007 授权且 bed 节点可达",
        {"c001": low.get("reason"), "c007_authorized": high.get("authorized"),
         "added": high.get("extension", {}).get("added_edge_types"),
         "bed_reached": bed_reached}))

    # 10) 快照与不可变判断 ------------------------------------------------
    from .models import GraphSnapshot
    snap1 = db.query(GraphSnapshot).filter_by(snapshot_no=1).first()
    snap_ok = (snap1 and snap1.cutoff_date.isoformat() == "2026-03-31"
               and snap1.identity_version >= 1 and snap1.policy_versions
               and snap1.graph.get("nodes") and j1.version == 1
               and j1.snapshot_no == 1 and j1.disclaimer)
    checks.append(_chk(
        "snapshot_immutable", "快照 #1 冻结四要素，首份判断不可变且带免责声明",
        snap_ok, "cutoff/identity_version/edge_scope/policy_versions 齐全；judgment v1@snap1",
        {"cutoff": snap1.cutoff_date.isoformat() if snap1 else None,
         "identity_version": snap1.identity_version if snap1 else None,
         "policies": len(snap1.policy_versions) if snap1 else 0,
         "judgment_version": j1.version, "snapshot_no": j1.snapshot_no}))

    checks.extend(_hitrate_checks(db))
    passed = sum(1 for c in checks if c["passed"])
    return {"overall": passed == len(checks),
            "passed": passed, "total": len(checks), "checks": checks}


def _hitrate_checks(db: Session) -> list[dict]:
    """冻结排序命中率评估的三项验收断言。"""
    from .models import FreezeBatch, FreezeCandidate
    from .services import hitrate as stats

    out = []
    main = db.query(FreezeBatch).filter_by(code="FB-2026Q1-TOP10PCT").first()
    stress = db.query(FreezeBatch).filter_by(code="FB-STRESS-UNRECORDED").first()
    if not main or not stress:
        return [_chk("hitrate_seed", "命中率评估批次已播种", False,
                     "两个冻结批次存在", "缺失", "")]

    def rows_of(batch):
        return [{
            "region": c.region, "review_source": c.review_source,
            "evidence_label": c.evidence_label,
            "review_probability": c.review_probability,
            "censor_status": c.censor_status,
            "recorded_factors": c.recorded_factors or {},
            "candidate_code": c.candidate_code,
        } for c in db.query(FreezeCandidate).filter_by(batch_id=batch.id)
            .order_by(FreezeCandidate.rank_no).all()]

    main_rows = rows_of(main)
    strata = stats.build_strata(main_rows, ("A", "B"))
    a, b = strata["A"], strata["B"]
    # 11) 冻结候选集与分层核查概率账完全符合题设
    accounting_ok = (
        len(main_rows) == 100 and a["N"] == 50 and b["N"] == 50
        and (a["n_hist"], a["y_hist"]) == (40, 20)
        and (a["M_unreviewed"], a["m_audit"], a["k_audit"]) == (10, 5, 2)
        and a["n_censored"] == 5 and abs(a["pi_hist"] - 0.8) < 1e-9
        and abs(a["pi_audit"] - 0.5) < 1e-9
        and (b["n_hist"], b["y_hist"]) == (10, 3)
        and (b["M_unreviewed"], b["m_audit"], b["k_audit"]) == (40, 10, 2)
        and b["n_censored"] == 30 and abs(b["pi_hist"] - 0.2) < 1e-9
        and abs(b["pi_audit"] - 0.25) < 1e-9
    )
    # 确认标签、删失状态、概率预测分别保存；待核件无标签且不计阴性
    storage_ok = all(
        (r["censor_status"] == "censored") == (r["evidence_label"] is None)
        for r in main_rows
    )
    out.append(_chk(
        "hitrate_frozen_accounting",
        "冻结候选集/核查概率账/确认标签/删失状态与题设一致且分列存储",
        accounting_ok and storage_ok,
        "100 件 A/B 各 50；A 40(20)+未核10 抽5确认2、删失5；B 10(3)+未核40 抽10确认2、删失30；π=0.8/0.2 与 0.5/0.25",
        {"A": {k: a[k] for k in ("N", "n_hist", "y_hist", "M_unreviewed",
                                 "m_audit", "k_audit", "n_censored",
                                 "pi_hist", "pi_audit")},
         "B": {k: b[k] for k in ("N", "n_hist", "y_hist", "M_unreviewed",
                                 "m_audit", "k_audit", "n_censored",
                                 "pi_hist", "pi_audit")},
         "label_censor_separated": storage_ok}))

    # 12) 双路点估计与 95% 区间：35%（超几何）与 40%（IPW），区间重叠
    res = stats.evaluate_hitrate(main_rows, ("A", "B"))
    ra, ip = res["random_audit"], res["ipw"]
    two_path_ok = (
        ra["identifiable"] and ip["identifiable"]
        and ra["point_estimate"] == 35.0
        and ra["estimated_confirmed"] == 35.0
        and ra["ci95"][0] <= 35.0 <= ra["ci95"][1]
        and ip["point_estimate"] == 40.0
        and ip["estimated_confirmed"] == 40.0
        and ip["ci95"][0] <= 40.0 <= ip["ci95"][1]
        and res["comparison"]["overlap"] is True
        and abs(res["naive_adjudicated"]["rate_pp"] - 41.54) < 0.01
    )
    out.append(_chk(
        "hitrate_two_path_estimates",
        "随机复核路 35%（分层超几何区间）、概率校正路 40%（重抽样区间），区间重叠；已办阳性率仅对照",
        two_path_ok,
        "RANDOM 35% 且 CI 覆盖 35；IPW 40% 且 CI 覆盖 40；两区间重叠；naive=41.54% 不作目标量",
        {"random": {"point": ra["point_estimate"], "ci95": ra["ci95"],
                    "method": ra["ci95_method"]},
         "ipw": {"point": ip["point_estimate"], "ci95": ip["ci95"],
                 "method": ip["ci95_method"]},
         "overlap": res["comparison"]["overlap"],
         "naive_adjudicated_rate": res["naive_adjudicated"]["rate_pp"]}))

    # 13) 不可识别护栏：零核查概率/未记录混杂 → 不得外推、不得宣称提升
    sres = stats.evaluate_hitrate(rows_of(stress), ("A", "B"))
    guard_ok = (
        not sres["random_audit"]["identifiable"]
        and not sres["ipw"]["identifiable"]
        and sres["identifiability"]["can_claim_ranking_lift"] is False
        and any(x["stratum"] == "B" for x in sres["random_audit"]["blocked_strata"])
        and any(x["stratum"] == "B" for x in sres["ipw"]["blocked_strata"])
        and len(sres["identifiability"]["unrecorded_confounders"]) > 0
        and res["identifiability"]["can_claim_ranking_lift"] is True
    )
    out.append(_chk(
        "hitrate_identifiability_guardrail",
        "关键层零核查概率/未记录混杂时两路不可识别、不得宣称排序提升；主批次不受影响",
        guard_ok,
        "压力批次 B 层阻断且 can_claim=False；主批次 can_claim=True",
        {"stress_random_blocked": sres["random_audit"]["blocked_strata"],
         "stress_ipw_blocked": sres["ipw"]["blocked_strata"],
         "unrecorded": sres["identifiability"]["unrecorded_confounders"],
         "stress_can_claim": sres["identifiability"]["can_claim_ranking_lift"],
         "main_can_claim": res["identifiability"]["can_claim_ranking_lift"]}))
    return out
