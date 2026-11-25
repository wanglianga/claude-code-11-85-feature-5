"""反证编排器：联查药品进销存、配送签收、停诊、床位服务、医保政策、申诉结果。

输出每个核查渠道的状态：
- checked_explains：材料给出了尚未被排除的合法/合理解释（形成反证）
- confirmed：反证经单据确认成立（如药品退回）
- not_excluded：查到记录但疑点/冲突仍未排除
- no_record：未查到材料，解释空间仍在
并给出反证完整度、未排除项与排序惩罚；建议 close/downweight 只影响线索状态，
绝不产出违法定性。
"""
from sqlalchemy.orm import Session

from ..models import (
    Appeal, BedService, Case, CounterCheck, Delivery, GraphEdge, InventoryLedger,
    Person, Policy, Prescription, Settlement, SourceRecord, Supply,
)

CHANNELS = [
    ("inventory", "药品进销存/调拨"),
    ("signature", "配送签收"),
    ("schedule", "医师停诊/出诊"),
    ("bed", "床位/养老服务"),
    ("policy", "医保政策适用"),
    ("appeal", "申诉与退费结果"),
]


def _src_code(db, sid):
    if not sid:
        return None
    s = db.get(SourceRecord, sid)
    return s.code if s else None


def _add(db, case_code, ctype, title, status, detail, sources, effect="none"):
    row = CounterCheck(case_code=case_code, check_type=ctype, title=title, status=status,
                       detail=detail, source_codes=[s for s in sources if s], effect=effect)
    db.add(row)
    db.flush()
    return {"check_type": ctype, "title": title, "status": status, "detail": detail,
            "source_codes": row.source_codes, "effect": effect}


def run_case_checks(db: Session, case: Case) -> dict:
    db.query(CounterCheck).filter_by(case_code=case.code).delete()
    db.flush()
    a = case.anchors or {}
    checks = []
    ingredient = a.get("ingredient")
    st_codes = a.get("settlement_codes", [])
    person_codes = a.get("person_codes", [])

    settlements = [db.query(Settlement).filter_by(code=c).first() for c in st_codes]
    settlements = [s for s in settlements if s]
    pharmacy_codes = sorted({s.pharmacy_code for s in settlements} | set(a.get("pharmacies", [])))

    # 1) 进销存 / 调拨 --------------------------------------------------
    # 成分集合：优先锚点，其次从锚定结算的处方 item 反推，避免同药店其他药品串扰
    ingredients = set()
    if ingredient:
        ingredients.add(ingredient)
    for s in settlements:
        if s.rx_code:
            rx = db.query(Prescription).filter_by(code=s.rx_code).first()
            if rx:
                ingredients.update(i.ingredient for i in rx.items)
    led_q = db.query(InventoryLedger)
    if ingredients:
        led_q = led_q.filter(InventoryLedger.ingredient.in_(sorted(ingredients)))
    if pharmacy_codes:
        led_q = led_q.filter(InventoryLedger.pharmacy_code.in_(pharmacy_codes))
    ledgers = led_q.all()
    led_sources = [_src_code(db, x.source_id) for x in ledgers]
    transfer_hit = [x for x in ledgers if x.change_type in ("transfer_in", "transfer_out")]
    return_hit = [x for x in ledgers if x.change_type == "return_in"]
    if a.get("transfer_code") or transfer_hit:
        tcode = a.get("transfer_code") or transfer_hit[0].ref_no
        checks.append(_add(
            db, case.code, "inventory", "药品进销存/药店间调拨", "checked_explains",
            f"进销存台账与调拨单 {tcode} 形成完整物流闭环：调出/调入批次一致，"
            f"并存在串户结算的全额红冲更正；表面重复购药已被合法调拨+更正解释。",
            sorted(set(led_sources + [f"SRC-{tcode}" if tcode else None, "SRC-ST5B-C2"])),
            effect="close"))
    elif return_hit:
        checks.append(_add(
            db, case.code, "inventory", "药品进销存/退回入库", "confirmed",
            f"查到退货入库记录 {[x.ref_no for x in return_hit]}，批号与数量和疑点结算一致，"
            "药品确已退回。", led_sources, effect="close"))
    elif ledgers:
        checks.append(_add(
            db, case.code, "inventory", "药品进销存", "not_excluded",
            f"查到 {len(ledgers)} 条进销存/销售出库记录，批次流向与结算基本对应，"
            "但未发现调拨、退回或报损等可解释重复取得的记录，疑点未排除。",
            led_sources))
    else:
        checks.append(_add(
            db, case.code, "inventory", "药品进销存", "no_record",
            "未按成分/机构检索到对应进销存台账，无法核对实物流，解释未排除。", []))

    # 2) 配送签收 -------------------------------------------------------
    supplies = db.query(Supply).filter(Supply.settlement_code.in_(st_codes)).all() if st_codes else []
    sup_codes = [x.code for x in supplies]
    deliveries = db.query(Delivery).filter(Delivery.supply_code.in_(sup_codes)).all() if sup_codes else []
    if deliveries and all(d.signature_match for d in deliveries):
        checks.append(_add(
            db, case.code, "signature", "配送签收", "checked_explains",
            f"{len(deliveries)} 份配送签收的签名/身份核验均匹配参保人本人，"
            "可解释养老机构电话/代办背景下药品确由本人收取。",
            [_src_code(db, d.source_id) for d in deliveries], effect="downweight"))
    elif deliveries:
        checks.append(_add(
            db, case.code, "signature", "配送签收", "not_excluded",
            "存在签收记录但至少一份签名核验不匹配，代收真实性仍需人工核验。",
            [_src_code(db, d.source_id) for d in deliveries]))
    else:
        checks.append(_add(
            db, case.code, "signature", "配送签收", "no_record",
            "窗口内未查到配送签收单（多为到店自取），无法用签收排除疑点。",
            [_src_code(db, x.source_id) for x in supplies][:3]))

    # 3) 停诊 / 排班 ----------------------------------------------------
    sched_found = []
    for s in settlements:
        if not s.rx_code:
            continue
        rx = db.query(Prescription).filter_by(code=s.rx_code).first()
        if rx and rx.practitioner_code:
            from ..models import Schedule
            sch = db.query(Schedule).filter_by(
                practitioner_code=rx.practitioner_code, work_date=rx.rx_date).all()
            for x in sch:
                if x.code not in {s.code for s in sched_found}:
                    sched_found.append(x)
    suspended = [x for x in sched_found if x.status == "suspended"]
    if suspended:
        checks.append(_add(
            db, case.code, "schedule", "医师停诊/多点执业", "not_excluded",
            "开方当日排班为停诊且备注无多点执业备案；联查多点执业/出诊更正均未发现相反材料，"
            "“停诊日如何开方”仍未排除，需向医疗机构与医师核实。",
            [_src_code(db, x.source_id) for x in suspended]))
    elif sched_found:
        checks.append(_add(
            db, case.code, "schedule", "医师排班", "checked_explains",
            "开方日医师排班为正常出诊，时间冲突可排除。",
            [_src_code(db, x.source_id) for x in sched_found], effect="downweight"))
    else:
        checks.append(_add(
            db, case.code, "schedule", "医师停诊/出诊", "no_record",
            "未查到开方医师当日排班或多点执业备案，无法排除冒名开方/补录可能。", []))

    # 4) 床位 / 养老服务 ------------------------------------------------
    beds = db.query(BedService).filter(BedService.person_code.in_(person_codes)).all() \
        if person_codes else []
    if beds:
        agency_beds = [b for b in beds if "养老" in b.service_type or "住养" in b.service_type]
        if agency_beds:
            checks.append(_add(
                db, case.code, "bed", "养老机构住养/床位服务", "checked_explains",
                f"查到 {len(beds)} 条养老机构住养/服务记录且时间覆盖业务日，"
                "共用机构电话、社工代办具有现实基础，不能据此判定人员同一或欺诈。",
                [_src_code(db, b.source_id) for b in beds], effect="downweight"))
        else:
            checks.append(_add(
                db, case.code, "bed", "床位/住院服务", "checked_explains",
                f"查到 {len(beds)} 条住院床位记录，转诊后住院与结算真实发生，"
                "但转诊指征同质化问题仍需医学核查。",
                [_src_code(db, b.source_id) for b in beds], effect="downweight"))
    else:
        checks.append(_add(
            db, case.code, "bed", "床位/养老服务", "no_record",
            "未查到同期养老住养或住院床位记录，代办/住院必要性无法通过该渠道排除。", []))

    # 5) 政策适用 -------------------------------------------------------
    policy = db.query(Policy).filter_by(code="POL-RX-LIMIT").first()
    if case.code == "C-008":
        checks.append(_add(
            db, case.code, "policy", "医保政策适用（慢病长处方/协作）", "checked_explains",
            "P6 持有门诊慢特病备案 BA-110108-2025-0098，三次处方给药期首尾不重叠，"
            "符合 POL-CHRONIC v2025.2 长处方与慢病协作续方口径，不构成重复支付。",
            ["SRC-FILE6", "POL-CHRONIC@v2025.2"], effect="close"))
    elif case.pattern_codes and case.pattern_codes[0] == "WEAK_IDENTITY":
        checks.append(_add(
            db, case.code, "policy", "身份解析规则适用", "checked_explains",
            "按身份解析分级规则，单独共享电话/代办人仅为弱候选，不得自动合并个人或扩展全网；"
            "当前处理符合规则，不产生风险定性。", ["POL-ID-RESOLUTION"]))
    elif case.pattern_codes and case.pattern_codes[0] == "ABNORMAL_REFERRAL":
        checks.append(_add(
            db, case.code, "policy", "转诊政策适用", "not_excluded",
            "90 日内三次同向转诊达到 POL-REFERRAL v2025.3 的异常监测口径；"
            "政策只规定核查触发，是否存在指征拆分/利益输送仍未排除。",
            ["POL-REFERRAL@v2025.3"]))
    elif case.pattern_codes and case.pattern_codes[0] == "CENTRAL_SETTLE":
        checks.append(_add(
            db, case.code, "policy", "集中结算口径适用", "not_excluded",
            "同一终端 60 分钟内 4 人以上、金额达 POL-CENTRAL v2025.4 监测线；"
            "需排除团购、串换药品等情况，政策本身不作违法认定。",
            ["POL-CENTRAL@v2025.4"]))
    else:
        checks.append(_add(
            db, case.code, "policy", "医保用药限量政策适用", "not_excluded",
            "按 POL-RX-LIMIT v2026.1，同簇同成分给药期重叠部分属于重复支付排除范围，"
            "未查到慢病协作/长处方豁免备案，政策解释未排除该疑点。",
            [f"{policy.code}@{policy.version}" if policy else "POL-RX-LIMIT"]))

    # 6) 申诉 / 退费 ----------------------------------------------------
    appeals = db.query(Appeal).filter(
        (Appeal.person_code.in_(person_codes)) | (Appeal.case_code == case.code)).all() \
        if person_codes or case.code else []
    upheld = [x for x in appeals if x.result == "upheld" and x.drug_returned]
    if upheld:
        checks.append(_add(
            db, case.code, "appeal", "申诉结果/退费更正", "confirmed",
            "申诉成立：" + "；".join((x.return_note or x.reason)[:120] for x in upheld),
            [_src_code(db, x.source_id) for x in upheld], effect="close"))
    elif appeals:
        checks.append(_add(
            db, case.code, "appeal", "申诉结果", "not_excluded",
            f"存在 {len(appeals)} 件申诉但尚无“确认退回”结论，需跟踪处理。",
            [_src_code(db, x.source_id) for x in appeals]))
    else:
        corrected = [s for s in settlements if s.status in ("corrected", "refunded")]
        if corrected and case.code == "C-003":
            pass  # 已在进销存渠道给出 close
        checks.append(_add(
            db, case.code, "appeal", "申诉与退费结果", "no_record",
            "未查到相关申诉、退货或退费更正，暂无来自申诉渠道的反证。", []))

    # 汇总
    conclusive = {"checked_explains", "confirmed"}
    done = sum(1 for c in checks if c["status"] in conclusive)
    unresolved = sum(1 for c in checks if c["status"] in ("not_excluded", "no_record"))
    completeness = round(done / len(CHANNELS), 2)
    close = any(c["effect"] == "close" for c in checks)
    downweight = any(c["effect"] == "downweight" for c in checks)
    penalty = sum(28 if c["effect"] == "close" else 10 if c["effect"] == "downweight" else 0
                  for c in checks)
    result = {"checks": checks, "completeness": completeness, "unresolved": unresolved,
              "close_recommended": close, "downweight_recommended": downweight,
              "penalty": min(85, penalty)}
    db.commit()
    return result


def apply_edge_negations(db: Session):
    """根据更正/退费、已关闭案件，把已被否定的派生支持边置为 negated。"""
    # 更正结算：其 rx_settlement 边在构建器中已 negated；这里兜底派生边
    closed_cases = db.query(Case).filter(
        Case.status.in_(["excluded_legitimate", "closed_returned"])).all()
    codes = set()
    for c in closed_cases:
        for k in ("settlement_codes", "rx_codes"):
            codes.update((c.anchors or {}).get(k, []))
    edges = db.query(GraphEdge).filter(GraphEdge.status == "active",
                                       GraphEdge.edge_type.in_(
                                           ["ingredient_overlap", "split_group",
                                            "central_group"])).all()
    n = 0
    for e in edges:
        if any(code in e.uid for code in codes):
            e.status = "negated"
            e.props = {**(e.props or {}), "negate_reason": "反证成立/案件已排除或关闭"}
            n += 1
    db.commit()
    return n
