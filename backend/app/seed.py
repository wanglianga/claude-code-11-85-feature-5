"""幂等种子数据：构建题目要求的虚构验收网络。

四个验收原型：
- P1（伪标识 P1，含跨系统档案 P1B）：两药店同日同成分、数量/给药期重叠，
  开方医师当日停诊，两结算落入同一给药期间。
- P2/P3：共用养老机构电话（另有共同代办人弱证据），各自处方与签收匹配，不并人。
- P4：表面重复购药由药店间调拨 + 串户更正解释，合法供药。
- P5：申诉成立且确认药品退回，反证关闭线索。
另有 P6 慢病协作（给药期不重叠 + 长处方政策）、P9 拆方、PH5 集中结算、
P14 异常转诊（高金额跨机构，触发扩展门槛）。
"""
from datetime import date as D
from datetime import datetime as DT

from .database import Base, SessionLocal, engine
from .models import (
    Appeal,
    BedService,
    Case,
    CounterCheck,
    Delivery,
    FreezeBatch,
    GraphEdge,
    GraphNode,
    IdentityEvidence,
    InventoryLedger,
    Org,
    PatternConfig,
    Person,
    Policy,
    Practitioner,
    Prescription,
    PrescriptionItem,
    Referral,
    Schedule,
    Settlement,
    SourceRecord,
    StockTransfer,
    Supply,
    User,
    Visit,
)
from .security import hash_password

CUTOFF = D(2026, 3, 31)


def dt(y, m, d, h=10, mi=0):
    return DT(y, m, d, h, mi)


# ------------------------------------------------------------ 来源账助手
def src(db, code, rtype, bkey, system, citation, vf, received=None,
        status="current", reason=None, supersedes_code=None, payload=None, vt=None):
    sup_id = None
    if supersedes_code:
        old = db.query(SourceRecord).filter_by(code=supersedes_code).one()
        old.status = "corrected"
        sup_id = old.id
    row = SourceRecord(
        code=code, source_system=system, record_type=rtype, business_key=bkey,
        valid_from=vf if isinstance(vf, DT) else DT.combine(vf, DT.min.time()),
        valid_to=vt, received_at=received or dt(2026, 3, 31, 23, 0),
        status=status, correction_reason=reason, supersedes_id=sup_id,
        citation=citation, payload=payload or {},
    )
    db.add(row)
    db.flush()
    return row


def seed_users(db):
    users = [
        User(username="admin", password_hash=hash_password("admin123"),
             display_name="平台管理员", role="admin",
             region_scope=["*"], org_scope=["*"]),
        User(username="auditor", password_hash=hash_password("audit123"),
             display_name="稽核员·小周（朝阳片区）", role="auditor",
             region_scope=["CY"], org_scope=[]),
    ]
    db.add_all(users)
    db.flush()


def seed_policies(db):
    rows = [
        Policy(code="POL-RX-LIMIT", version="v2026.1",
               title="处方管理与医保用药限量经办规则",
               effective_from=D(2026, 1, 1), region="*",
               citation="京医保发〔2025〕18号 第12/13条",
               clauses={
                   "general_max_days": 30,
                   "same_ingredient_overlap": "同一伪标识/强簇内同一有效成分给药期重叠的重复结算列为疑点",
                   "high_amount_cross_org_threshold": 50000,
                   "split_rx_threshold_tablets": 20,
                   "split_rx_min_count": 3,
               }),
        Policy(code="POL-CHRONIC", version="v2025.2",
               title="门诊慢特病长处方与慢病协作办法",
               effective_from=D(2025, 6, 1), region="*",
               citation="京医保发〔2025〕7号 第5条",
               clauses={"long_rx_days_max": 90,
                        "coordinated_refill": "已备案慢病协作，在给药期不重叠前提下可跨机构续方"}),
        Policy(code="POL-REFERRAL", version="v2025.3",
               title="分级诊疗转诊经办规程",
               effective_from=D(2025, 6, 1), region="*",
               citation="京卫医〔2025〕112号",
               clauses={"same_target_count": 3, "window_days": 90,
                        "note": "90日内同机构向同目标机构转诊3次及以上列为异常转诊线索"}),
        Policy(code="POL-CENTRAL", version="v2025.4",
               title="定点零售药店集中结算监测口径",
               effective_from=D(2025, 9, 1), region="*",
               citation="医保办发〔2025〕29号附件2",
               clauses={"persons_per_60min": 4, "min_total_amount": 20000}),
    ]
    db.add_all(rows)
    db.flush()


def seed_orgs_persons(db):
    orgs = [
        ("H1", "康和医院", "hospital", "CY", "三级综合"),
        ("H2", "仁济社区卫生服务中心", "clinic", "HD", "社区"),
        ("H3", "东城中医医院", "hospital", "DC", "二级中医"),
        ("H4", "华康骨科医院", "hospital", "CY", "专科"),
        ("PH1", "康宁大药房（朝阳店）", "pharmacy", "CY", "定点药店"),
        ("PH2", "百草堂药房（东城店）", "pharmacy", "DC", "定点药店"),
        ("PH3", "济民药房（西城店）", "pharmacy", "XC", "定点药店"),
        ("PH4", "同济堂药房（海淀店）", "pharmacy", "HD", "定点药店"),
        ("PH5", "悦康大药房（朝阳店）", "pharmacy", "CY", "定点药店"),
        ("PH7", "东城中医医院药房", "pharmacy", "DC", "院内药房"),
        ("E1", "夕阳红养老服务有限公司", "agency", "HD", "养老机构"),
    ]
    for code, name, t, region, level in orgs:
        db.add(Org(code=code, name=name, org_type=t, region=region, level=level))
    db.flush()

    persons = [
        # code, name, pseudo, region, phone, address, note
        ("P1", "张建国", "P1", "CY", "13900001111", "朝阳区和平街12号院", "退休职工，高血压"),
        ("P1B", "张建国（康宁建档镜像）", "P1", "CY", "13900001111", "朝阳区和平街12号院",
         "药店系统建档，伪标识与医保平台一致"),
        ("P2", "王秀兰", "P2", "HD", "010-88886666", "海淀区夕阳红路1号", "夕阳红机构住养"),
        ("P3", "王桂芳", "P3", "HD", "010-88886666", "海淀区夕阳红路1号", "夕阳红机构住养"),
        ("P4", "李国强", "P4", "XC", "13900000004", "西城区德胜门外大街", None),
        ("P5", "赵淑珍", "P5", "CY", "13900000005", "朝阳区劲松三区", None),
        ("P6", "刘德海", "P6", "HD", "13900000006", "海淀区中关村南大街", "慢病备案：高血压/糖尿病"),
        ("P7", "陈淑芬", "P7", "CY", "13800007777", "朝阳区幸福里3号楼201", None),
        ("P8", "林晓梅", "P8", "CY", "13800007777", "朝阳区幸福里3号楼201", "P7 之女"),
        ("P9", "吴德发", "P9", "DC", "13900000009", "东城区安定门内大街", None),
        ("P10", "郑传福", "P10", "CY", "13900000010", "朝阳区垡头西里", None),
        ("P11", "冯玉梅", "P11", "CY", "13900000011", "朝阳区垡头西里", None),
        ("P12", "蒋大山", "P12", "CY", "13900000012", "朝阳区十八里店", None),
        ("P13", "沈慧兰", "P13", "CY", "13900000013", "朝阳区十八里店", None),
        ("P14", "许文才", "P14", "HD", "13900000014", "海淀区学院路", "膝骨关节炎"),
    ]
    for code, name, pseudo, region, phone, addr, note in persons:
        db.add(Person(code=code, name=name, pseudo_id=pseudo, region=region,                      contact_phone=phone, address=addr, note=note,
                      gender="男" if code in ("P1", "P1B", "P4", "P6", "P9", "P12", "P14") else "女",
                      birth_year=1945 + int(code[1:].rstrip("B")) % 20 if code[1:].rstrip("B").isdigit() else 1960))

    docs = [
        ("D1", "陈明", "110101197503124411", D(2027, 12, 31), "H1", "内科 副主任医师"),
        ("D1B", "陈明（电子处方平台镜像）", "110101197503124411", D(2027, 12, 31), "H1", "内科 副主任医师"),
        ("D2", "刘静", "110108198207152244", D(2028, 6, 30), "H2", "全科 主治医师"),
        ("D3", "赵立中", "110101196911203317", D(2026, 12, 31), "H3", "中医科 主任医师"),
        ("D4", "钱进", "110105197805056612", D(2027, 3, 31), "H4", "骨科 副主任医师"),
    ]
    for code, name, cred, valid_to, org, title in docs:
        db.add(Practitioner(code=code, name=name, credential_no=cred,
                            credential_valid_to=valid_to,
                            org_id=db.query(Org).filter_by(code=org).one().id, title=title))
    db.flush()


def seed_identity_evidence(db):
    mi = src(db, "SRC-REG-MI", "identity_registry", "MI-PSEUDO-LEDGER",
             "医保身份平台", "医保伪标识签发台账 P1/P2...", dt(2026, 1, 1, 9),
             received=dt(2026, 1, 1, 9, 5), payload={"pseudo_ids": ["P1", "P2"]})
    ej = src(db, "SRC-E1-REG", "agency_registration", "E1-CONTACT-2026",
             "养老机构登记系统", "夕阳红住养人联系电话登记表", dt(2026, 1, 2, 10),
             payload={"phone": "010-88886666", "agent": "小刘"})
    fam = src(db, "SRC-CY-COMM", "community_grid", "GRID-CY-3-201",
              "社区网格台账", "幸福里3-201 住户信息卡", dt(2026, 2, 1, 15))
    cred_src = src(db, "SRC-NHC-CRED", "practitioner_credential", "CRED-1101011975...",
                   "医师执业注册系统", "医师执业证书 110101197503124411", dt(2025, 12, 20))

    ev = [
        ("IE-01", "person", "P1", "P1B", "pseudo_id", "医保伪标识=P1", "strong",
         "auto", mi.id, D(2026, 1, 1), None),
        ("IE-02", "practitioner", "D1", "D1B", "professional_credential",
         "有效期内执业凭证 110101197503124411", "strong", "auto", cred_src.id,
         D(2025, 12, 20), D(2027, 12, 31)),
        ("IE-03", "person", "P2", "P3", "shared_phone", "共用养老机构电话 010-88886666",
         "weak", "pending", ej.id, None, None),
        ("IE-04", "person", "P2", "P3", "shared_agent", "共同代办人：夕阳红社工 小刘",
         "weak", "pending", ej.id, None, None),
        ("IE-05", "person", "P7", "P8", "shared_phone", "共用家庭电话 13800007777",
         "weak", "pending", fam.id, None, None),
        ("IE-06", "person", "P7", "P8", "shared_address", "共用地址 幸福里3号楼201",
         "weak", "pending", fam.id, None, None),
    ]
    for code, stype, l, r, et, val, tier, status, sid, vf, vt in ev:
        db.add(IdentityEvidence(code=code, subject_type=stype, left_code=l, right_code=r,
                                evidence_type=et, evidence_value=val, tier=tier,
                                status=status, source_id=sid, valid_from=vf, valid_to=vt))
    db.flush()


# ------------------------------------------------------------ 业务数据
def add_visit(db, code, person, org, doc, date, diag, system="HIS·康和"):
    s = src(db, f"SRC-{code}", "visit", code, system,
            f"{system} 就诊流水 {code}", dt(date.year, date.month, date.day, 9, 20),
            received=dt(date.year, date.month, date.day, 10, 0))
    v = Visit(code=code, person_code=person, org_code=org, practitioner_code=doc,
              visit_date=date, diagnosis=diag, source_id=s.id)
    db.add(v)
    return v


def add_rx(db, code, no, visit, person, org, doc, date, items):
    s = src(db, f"SRC-{code}", "prescription", no, "电子处方中心",
            f"处方号 {no}", dt(date.year, date.month, date.day, 9, 41),
            received=dt(date.year, date.month, date.day, 9, 42))
    rx = Prescription(code=code, rx_no=no, visit_code=visit, person_code=person,
                      org_code=org, practitioner_code=doc, rx_date=date, source_id=s.id)
    db.add(rx)
    db.flush()
    for i, it in enumerate(items, 1):
        icode = f"{code}-I{i}"
        db.add(PrescriptionItem(
            code=icode, prescription_id=rx.id, source_id=s.id,
            drug_name=it[0], ingredient=it[1], spec=it[2], qty=it[3],
            daily_dose=it[4], days=it[5], admin_start=it[6], admin_end=it[7]))
    return rx


def add_settle(db, code, no, person, rx, pharmacy, date, total, fund, selfpay,
               status="settled", refund=0, terminal=None, operator=None,
               received=None, sstatus="current", reason=None, supersedes=None,
               system="医保结算系统"):
    s = src(db, f"SRC-{code}", "settlement", no, system,
            f"结算单 {no}", dt(date.year, date.month, date.day, date.hour if hasattr(date, 'hour') else 15, 0),
            received=received or dt(date.year, date.month, date.day, 18, 0),
            status=sstatus, reason=reason, supersedes_code=supersedes)
    row = Settlement(code=code, settle_no=no, person_code=person, rx_code=rx,
                     pharmacy_code=pharmacy, settle_date=date, total_amount=total,
                     fund_amount=fund, self_amount=selfpay, status=status,
                     refund_amount=refund, terminal_no=terminal, operator_code=operator,
                     source_id=s.id)
    db.add(row)
    return row


def add_supply(db, code, ph, settle, rxitem, person, ing, batch, qty, date):
    s = src(db, f"SRC-{code}", "supply", code, "药店进销存系统",
            f"供药/出库单 {code}", dt(date.year, date.month, date.day, 16, 30))
    row = Supply(code=code, pharmacy_code=ph, settlement_code=settle,
                 rx_item_code=rxitem, person_code=person, ingredient=ing,
                 batch_no=batch, qty=qty, supply_date=date, source_id=s.id)
    db.add(row)
    return row


def add_ledger(db, code, ph, ing, batch, ctype, qty, ref, date, counterpart=None):
    s = src(db, f"SRC-{code}", "inventory_ledger", code, "药店进销存系统",
            f"台账行 {code}（{ref}）", dt(date.year, date.month, date.day, 20, 0))
    db.add(InventoryLedger(code=code, pharmacy_code=ph, ingredient=ing, batch_no=batch,
                           change_type=ctype, change_qty=qty, ref_no=ref, txn_date=date,
                           counterpart_org_code=counterpart, source_id=s.id))


def seed_p1(db):
    d = D(2026, 3, 10)
    sch_src = src(db, "SRC-SCH1", "schedule", "SCH-D1-0310", "医院排班系统",
                  "H1 门诊排班/停诊单 TP-0309-17", dt(2026, 3, 9, 16),
                  payload={"leave_no": "SP-0309"})
    db.add(Schedule(code="SCH1", practitioner_code="D1", org_code="H1", work_date=d,
                    status="suspended", note="因病停诊（请假单 SP-0309），当日无多点执业备案",
                    source_id=sch_src.id))
    add_visit(db, "V1", "P1", "H1", "D1", d, "高血压2级 随访配药")
    add_rx(db, "RX1", "CF-20260310-1102", "V1", "P1", "H1", "D1", d, [
        ("苯磺酸氨氯地平片", "氨氯地平", "5mg*7片/盒", 30, 1, 30, d, D(2026, 4, 8))])
    add_rx(db, "RX2", "CF-20260310-1109", "V1", "P1B", "H1", "D1", d, [
        ("苯磺酸氨氯地平片", "氨氯地平", "5mg*14片/盒", 28, 1, 28, d, D(2026, 4, 6))])
    add_settle(db, "ST1", "JS-CY-0310-7781", "P1", "RX1", "PH1", d,
               186.50, 142.30, 44.20)
    add_settle(db, "ST2", "JS-DC-0310-2290", "P1B", "RX2", "PH2", d,
               168.00, 126.00, 42.00)
    add_supply(db, "SUP1", "PH1", "ST1", "RX1-I1", "P1", "氨氯地平", "B-AML-260208", 30, d)
    add_supply(db, "SUP2", "PH2", "ST2", "RX2-I1", "P1B", "氨氯地平", "B-AML-260121", 28, d)
    add_ledger(db, "INV1", "PH1", "氨氯地平", "B-AML-260208", "sale_out", -30, "ST1", d)
    add_ledger(db, "INV2", "PH2", "氨氯地平", "B-AML-260121", "sale_out", -28, "ST2", d)


def seed_p23(db):
    # 床位服务
    for code, person, bed in [("BED2", "P2", "A-1203-1"), ("BED3", "P3", "A-1205-2")]:
        s = src(db, f"SRC-{code}", "bed_service", code, "养老机构床位系统",
                f"夕阳红床位/服务记录 {bed}", dt(2026, 1, 1, 11))
        db.add(BedService(code=code, agency_code="E1", person_code=person, bed_no=bed,
                          service_from=D(2026, 1, 1), service_to=D(2026, 6, 30),
                          service_type="机构住养（含代办购药）", source_id=s.id))
    d2, d3 = D(2026, 3, 3), D(2026, 3, 5)
    add_visit(db, "V2", "P2", "H2", "D2", d2, "2型糖尿病随访", "HIS-仁济")
    add_rx(db, "RX3", "CF-RJ-3301", "V2", "P2", "H2", "D2", d2, [
        ("盐酸二甲双胍片", "二甲双胍", "0.5g*20片/盒", 60, 2, 30, D(2026, 3, 4), D(2026, 4, 2))])
    add_settle(db, "ST3", "JS-HD-0304-1102", "P2", "RX3", "PH4", D(2026, 3, 4),
               92.00, 78.20, 13.80)
    add_supply(db, "SUP3", "PH4", "ST3", "RX3-I1", "P2", "二甲双胍", "B-MET-260211", 60, D(2026, 3, 4))
    ds = src(db, "SRC-DL1", "delivery", "DL1", "配送签收系统", "签收单 PS-8801",
             dt(2026, 3, 5, 9), received=dt(2026, 3, 5, 18))
    db.add(Delivery(code="DL1", supply_code="SUP3", person_code="P2", receiver_name="王秀兰",
                    address="海淀区夕阳红路1号 A-1203", signed_at=dt(2026, 3, 5, 10, 22),
                    signature_match=True, source_id=ds.id))

    add_visit(db, "V3", "P3", "H2", "D2", d3, "高血压随访", "HIS-仁济")
    add_rx(db, "RX4", "CF-RJ-3355", "V3", "P3", "H2", "D2", d3, [
        ("格列美脲片", "格列美脲", "2mg*30片/盒", 30, 1, 30, D(2026, 3, 5), D(2026, 4, 3))])
    add_settle(db, "ST4", "JS-HD-0305-1188", "P3", "RX4", "PH4", D(2026, 3, 5),
               76.00, 64.60, 11.40)
    add_supply(db, "SUP4", "PH4", "ST4", "RX4-I1", "P3", "格列美脲", "B-GLM-260130", 30, D(2026, 3, 5))
    ds2 = src(db, "SRC-DL2", "delivery", "DL2", "配送签收系统", "签收单 PS-8830",
              dt(2026, 3, 6, 9))
    db.add(Delivery(code="DL2", supply_code="SUP4", person_code="P3", receiver_name="王桂芳",
                    address="海淀区夕阳红路1号 A-1205", signed_at=dt(2026, 3, 6, 15, 40),
                    signature_match=True, source_id=ds2.id))


def seed_p4(db):
    d = D(2026, 3, 12)
    add_visit(db, "V4", "P4", "H3", "D3", D(2026, 3, 11), "高脂血症", "HIS-东城中医")
    add_rx(db, "RX5", "CF-DZ-7788", "V4", "P4", "H3", "D3", D(2026, 3, 11), [
        ("阿托伐他汀钙片", "阿托伐他汀", "20mg*14片/盒", 28, 1, 28, d, D(2026, 4, 8))])
    # 真实销售：PH3
    add_settle(db, "ST5A", "JS-XC-0312-0901", "P4", "RX5", "PH3", d,
               156.00, 118.00, 38.00)
    add_supply(db, "SUP5A", "PH3", "ST5A", "RX5-I1", "P4", "阿托伐他汀",
               "B-AT-0210", 28, d)
    add_ledger(db, "INV5A", "PH3", "阿托伐他汀", "B-AT-0210", "sale_out", -28, "ST5A", d)
    # 表面重复：PH4 串户结算，后被全额红冲更正
    add_settle(db, "ST5B", "JS-HD-0312-3340", "P4", "RX5", "PH4", d,
               161.00, 122.00, 39.00, status="corrected", refund=161.00,
               sstatus="corrected",
               reason="调拨串户：该笔实为 PH4→PH3 调拨出库，误按医保销售上传，已全额红冲",
               received=dt(2026, 3, 12, 12))
    corr_src = src(db, "SRC-ST5B-C2", "settlement_correction", "JS-HD-0312-3340-C2",
                   "医保结算系统", "红冲更正单 GZ-0313-009", dt(2026, 3, 13, 9, 30),
                   received=dt(2026, 3, 13, 10), reason="调拨串户更正",
                   supersedes_code="SRC-ST5B",
                   payload={"red_offset": 161.0, "ref": "TR4"})
    db.flush()
    # 调拨单
    ts = src(db, "SRC-TR4", "stock_transfer", "TR4", "连锁药店调拨平台",
             "调拨单 DB-20260311-04", dt(2026, 3, 11, 17),
             payload={"batch": "B-AT-0210", "qty": 40})
    db.add(StockTransfer(code="TR4", transfer_no="DB-20260311-04",
                         from_pharmacy="PH4", to_pharmacy="PH3",
                         ingredient="阿托伐他汀", qty=40,
                         ship_date=D(2026, 3, 11), receive_date=d, source_id=ts.id))
    add_ledger(db, "INV5B1", "PH4", "阿托伐他汀", "B-AT-0210", "transfer_out", -40,
               "TR4", D(2026, 3, 11), counterpart="PH3")
    add_ledger(db, "INV5B2", "PH3", "阿托伐他汀", "B-AT-0210", "transfer_in", 40,
               "TR4", d, counterpart="PH4")
    add_ledger(db, "INV5B3", "PH4", "阿托伐他汀", "B-AT-0210", "sale_out", -28,
               "JS-HD-0312-3340", d)
    add_ledger(db, "INV5B4", "PH4", "阿托伐他汀", "B-AT-0210", "return_in", 28,
               "GZ-0313-009", D(2026, 3, 13))


def seed_p5(db):
    d = D(2026, 3, 6)
    add_visit(db, "V5", "P5", "H1", "D1", d, "高脂血症复查")
    add_rx(db, "RX6", "CF-0306-2201", "V5", "P5", "H1", "D1", d, [
        ("瑞舒伐他汀钙片", "瑞舒伐他汀", "10mg*14片/板", 28, 1, 28, d, D(2026, 4, 2))])
    add_rx(db, "RX7", "CF-0306-2230", "V5", "P5", "H1", "D1", d, [
        ("瑞舒伐他汀钙片", "瑞舒伐他汀", "10mg*14片/板", 28, 1, 28, d, D(2026, 4, 2))])
    add_settle(db, "ST6", "JS-CY-0306-5510", "P5", "RX6", "PH1", d, 210.00, 168.00, 42.00)
    add_settle(db, "ST7", "JS-CY-0306-6120", "P5", "RX7", "PH5", d, 205.00, 160.00, 45.00,
               status="refunded", refund=205.00)
    add_supply(db, "SUP6", "PH1", "ST6", "RX6-I1", "P5", "瑞舒伐他汀", "B-RSV-260220", 28, d)
    add_supply(db, "SUP7", "PH5", "ST7", "RX7-I1", "P5", "瑞舒伐他汀", "B-RSV-260218", 28, d)
    add_ledger(db, "INV6", "PH1", "瑞舒伐他汀", "B-RSV-260220", "sale_out", -28, "ST6", d)
    add_ledger(db, "INV7", "PH5", "瑞舒伐他汀", "B-RSV-260218", "sale_out", -28, "ST7", d)
    add_ledger(db, "INV7R", "PH5", "瑞舒伐他汀", "B-RSV-260218", "return_in", 28,
               "TH-0318", D(2026, 3, 18))
    a = src(db, "SRC-APP1", "appeal", "APP1", "申诉材料系统",
            "申诉书 SS-20260316-07 + 退货验收单 TH-0318", dt(2026, 3, 16, 14),
            received=dt(2026, 3, 16, 16),
            payload={"return_order": "TH-0318", "refund_no": "TK-3309"})
    db.add(Appeal(code="APP1", appeal_no="SS-20260316-07", person_code="P5",
                  case_code="C-004", submitted_at=dt(2026, 3, 16, 14),
                  reason="3月6日在悦康大药房购买的瑞舒伐他汀与家中存量重复，未拆封，"
                         "已于3月18日退回药店并收到退款，申请核查。",
                  result="upheld", drug_returned=True,
                  return_note="退货验收单 TH-0318：外包装完好、批号一致，28片全退；退款 TK-3309 已到账",
                  decided_at=dt(2026, 3, 20, 11), source_id=a.id))


def seed_p6(db):
    filing = src(db, "SRC-FILE6", "chronic_filing", "MBBA-P6-2025",
                 "慢病备案系统", "门诊慢特病备案 BA-110108-2025-0098（高血压、糖尿病）",
                 dt(2025, 12, 28))
    for idx, (d0, code_rx, no, st_code) in enumerate([
        (D(2026, 1, 5), "RX8", "CF-RJ-0105-2201", "ST8"),
        (D(2026, 2, 5), "RX9", "CF-RJ-0205-2240", "ST9"),
        (D(2026, 3, 8), "RX10", "CF-RJ-0308-2311", "ST10"),
    ]):
        add_visit(db, f"V6-{idx+1}", "P6", "H2", "D2", d0, "慢病协作月度续方", "HIS-仁济")
        add_rx(db, code_rx, no, f"V6-{idx+1}", "P6", "H2", "D2", d0, [
            ("苯磺酸氨氯地平片", "氨氯地平", "5mg*7片/盒", 30, 1, 30, d0,
             D.fromordinal(d0.toordinal() + 29))])
        add_settle(db, st_code, f"JS-HD-{d0.month:02d}{d0.day:02d}-{4100+idx}",
                   "P6", code_rx, "PH4", d0, 88.0, 72.0, 16.0)
        add_supply(db, f"SUP{8+idx}", "PH4", st_code, f"{code_rx}-I1", "P6",
                   "氨氯地平", f"B-AML-2601{idx}", 30, d0)
    db.flush()


def seed_p9_split(db):
    d = D(2026, 3, 18)
    add_visit(db, "V7", "P9", "H3", "D3", d, "不寐、焦虑状态")
    for i in range(1, 4):
        add_rx(db, f"RX1{i}", f"CF-DZ-90{i}", "V7", "P9", "H3", "D3", d, [
            ("艾司唑仑片", "艾司唑仑", "1mg*20片/盒", 18, 1, 18, d, D(2026, 4, 4))])
        add_settle(db, f"ST1{i}", f"JS-DC-0318-770{i}", "P9", f"RX1{i}", "PH7", d,
                   168.0, 130.0, 38.0, terminal="TERM-H3-02", operator="OP-331")
        add_supply(db, f"SUP1{i}", "PH7", f"ST1{i}", f"RX1{i}-I1", "P9",
                   "艾司唑仑", f"B-ESZ-2603{i}", 18, d)
        add_ledger(db, f"INV9-{i}", "PH7", "艾司唑仑", f"B-ESZ-2603{i}",
                   "sale_out", -18, f"ST1{i}", d)


def seed_central(db):
    names = [("P10", "郑传福"), ("P11", "冯玉梅"), ("P12", "蒋大山"), ("P13", "沈慧兰")]
    for i, (pc, nm) in enumerate(names):
        d = D(2026, 3, 21)
        hh, mm = 9, 12 + i * 11
        add_visit(db, f"V10-{i}", pc, "PH5", None, d, "购药处方核验", "悦康药店处方端")
        add_rx(db, f"RX-C{i}", f"CF-YK-{i:04d}", f"V10-{i}", pc, "PH5", None, d, [
            ("人血白蛋白", "人血白蛋白", "10g/50ml 瓶", 2, 0, 14, d, D(2026, 4, 3))])
        add_settle(db, f"ST-C{i}", f"JS-CY-0321-88{i:02d}", pc, f"RX-C{i}", "PH5",
                   DT(2026, 3, 21, hh, mm), 5980.0, 4780.0, 1200.0,
                   terminal="TERM-YK-05", operator="OP-117")
        add_supply(db, f"SUP-C{i}", "PH5", f"ST-C{i}", f"RX-C{i}-I1", pc,
                   "人血白蛋白", f"B-ALB-2603{i}", 2, d)


def seed_referral(db):
    refs = [
        ("RF1", D(2026, 1, 12), "ST14A", D(2026, 1, 15), 12600, 9800),
        ("RF2", D(2026, 2, 16), "ST14B", D(2026, 2, 18), 28800, 23000),
        ("RF3", D(2026, 3, 19), "ST14C", D(2026, 3, 20), 46800, 39000),
    ]
    for i, (code, rd, stcode, sd, total, fund) in enumerate(refs, 1):
        rs = src(db, f"SRC-{code}", "referral", code, "双向转诊平台",
                 f"转诊单 ZZ-2026-{i:04d}", dt(rd.year, rd.month, rd.day, 10))
        db.add(Referral(code=code, referral_no=f"ZZ-2026-{i:04d}", person_code="P14",
                        from_org="H2", to_org="H4", refer_date=rd,
                        reason="双膝骨关节炎加重，反复上转骨科评估/治疗",
                        status="completed", source_id=rs.id))
        add_visit(db, f"V14-{i}", "P14", "H4", "D4", sd, "膝骨关节炎住院/手术评估", "HIS-华康")
        add_settle(db, stcode, f"JS-CY-{sd.month:02d}{sd.day:02d}-H{i}", "P14",
                   None, "H4", sd, total, fund, total - fund, system="医保结算系统（住院）")
        bs = src(db, f"SRC-BED14-{i}", "bed_service", f"BED14-{i}", "H4 住院床位系统",
                 f"住院床位记录 ZY-{i:04d}", dt(sd.year, sd.month, sd.day))
        db.add(BedService(code=f"BED14-{i}", agency_code="H4", person_code="P14",
                          bed_no=f"骨科病区 {30+i}床", service_from=sd,
                          service_to=D.fromordinal(sd.toordinal() + (5 if i < 3 else 3)),
                          service_type="住院床位", source_id=bs.id))


def seed_pattern_configs(db):
    cfgs = [
        PatternConfig(
            code="MULTI_POINT", name="多点重复购药", enabled=True,
            description="同一伪标识/强身份簇内，限定窗口与边类型，计算≥2家机构同成分、"
                        "数量与给药期重叠、结算落入同一给药期间，并叠加排班冲突等必要事实。",
            window_days=14, max_hops=2,
            allowed_edge_types=["identity_strong", "person_visit", "visit_rx",
                                "rx_settlement", "settle_org", "settle_supply",
                                "supply_org", "ingredient_overlap", "schedule_conflict",
                                "rx_doctor"],
            params={"min_pharmacies": 2, "min_overlap_days": 1,
                    "same_day_weight": 18, "overlap_weight": 24,
                    "cluster_weight": 15, "schedule_weight": 20,
                    "admin_period_weight": 15, "qty_overlap_weight": 8},
            high_amount_threshold=50000,
            extended_edge_types=["referral_to", "central_group", "cross_region_flow"],
            extension_cutoff="以高金额结算日为起点向后30日，且不晚于新冻结截止日",
            policy_code="POL-RX-LIMIT", policy_version="v2026.1"),
        PatternConfig(
            code="SPLIT_RX", name="拆方", enabled=True,
            description="同一就诊当日开具≥3张处方、单张数量均贴近预警阈值且总量明显超量。",
            window_days=1, max_hops=2,
            allowed_edge_types=["person_visit", "visit_rx", "rx_settlement",
                                "settle_org", "settle_supply", "split_group"],
            params={"min_rx_count": 3, "qty_each_lte": 20, "total_qty_gte": 48,
                    "weight": 70},
            high_amount_threshold=50000,
            extended_edge_types=[], extension_cutoff=None,
            policy_code="POL-RX-LIMIT", policy_version="v2026.1"),
        PatternConfig(
            code="CENTRAL_SETTLE", name="集中结算", enabled=True,
            description="同一终端/操作员在60分钟内为≥4名无强身份关联人员集中结算且金额达到监测线。",
            window_days=1, max_hops=2,
            allowed_edge_types=["rx_settlement", "settle_org", "central_group",
                                "settle_supply"],
            params={"minutes": 60, "min_persons": 4, "min_total_amount": 20000,
                    "weight": 65},
            high_amount_threshold=50000,
            extended_edge_types=["identity_weak", "agency_link"],
            extension_cutoff="向前追溯24小时同终端全部结算",
            policy_code="POL-CENTRAL", policy_version="v2025.4"),
        PatternConfig(
            code="ABNORMAL_REFERRAL", name="异常转诊", enabled=True,
            description="90日内同一参保人/社区向同一目标机构转诊≥3次，并伴随后续高额结算。",
            window_days=90, max_hops=2,
            allowed_edge_types=["person_referral", "referral_from", "referral_to",
                                "rx_settlement", "settle_org", "person_visit"],
            params={"min_count": 3, "same_target": True, "weight": 60},
            high_amount_threshold=50000,
            extended_edge_types=["bed_org", "person_bed", "cross_region_flow"],
            extension_cutoff="最后一次转诊后30日结算/住院记录",
            policy_code="POL-REFERRAL", policy_version="v2025.3"),
    ]
    db.add_all(cfgs)
    db.flush()


CASE_META = [
    dict(code="C-001", title="P1 两药店同日同成分重叠购药、开方医师停诊",
         pattern_codes=["MULTI_POINT"], primary="P1", region="CY",
         orgs=["H1", "PH1", "PH2"], status="pending",
         anchors={"person_codes": ["P1", "P1B"], "rx_codes": ["RX1", "RX2"],
                  "settlement_codes": ["ST1", "ST2"], "pharmacies": ["PH1", "PH2"],
                  "ingredient": "氨氯地平", "schedule_code": "SCH1"}),
    dict(code="C-002", title="P2/P3 共用养老机构电话（弱身份候选，不并人）",
         pattern_codes=["WEAK_IDENTITY"], primary="P2", region="HD",
         orgs=["E1", "PH4", "H2"], status="weak_identity",
         anchors={"evidence_codes": ["IE-03", "IE-04"], "person_codes": ["P2", "P3"]}),
    dict(code="C-003", title="P4 表面重复购药：药店间调拨与串户更正",
         pattern_codes=["MULTI_POINT"], primary="P4", region="XC",
         orgs=["H3", "PH3", "PH4"], status="excluded_legitimate",
         anchors={"person_codes": ["P4"], "rx_codes": ["RX5"],
                  "settlement_codes": ["ST5A", "ST5B"], "transfer_code": "TR4",
                  "ingredient": "阿托伐他汀"}),
    dict(code="C-004", title="P5 重复购药疑点：申诉成立、药品已退回",
         pattern_codes=["MULTI_POINT"], primary="P5", region="CY",
         orgs=["H1", "PH1", "PH5"], status="closed_returned",
         anchors={"person_codes": ["P5"], "rx_codes": ["RX6", "RX7"],
                  "settlement_codes": ["ST6", "ST7"], "appeal_code": "APP1",
                  "ingredient": "瑞舒伐他汀"}),
    dict(code="C-005", title="P9 同一就诊三张处方均贴近超量阈值（拆方）",
         pattern_codes=["SPLIT_RX"], primary="P9", region="DC",
         orgs=["H3", "PH7"], status="pending",
         anchors={"person_codes": ["P9"], "visit_code": "V7",
                  "rx_codes": ["RX11", "RX12", "RX13"],
                  "settlement_codes": ["ST11", "ST12", "ST13"]}),
    dict(code="C-006", title="PH5 同一终端一小时内四人集中高额结算",
         pattern_codes=["CENTRAL_SETTLE"], primary="P10", region="CY",
         orgs=["PH5"], status="pending",
         anchors={"pharmacy": "PH5", "terminal": "TERM-YK-05",
                  "settlement_codes": ["ST-C0", "ST-C1", "ST-C2", "ST-C3"],
                  "person_codes": ["P10", "P11", "P12", "P13"]}),
    dict(code="C-007", title="P14 90日三次同向转诊伴高额住院结算（高金额跨机构）",
         pattern_codes=["ABNORMAL_REFERRAL"], primary="P14", region="HD",
         orgs=["H2", "H4"], status="pending",
         anchors={"person_codes": ["P14"], "referral_codes": ["RF1", "RF2", "RF3"],
                  "settlement_codes": ["ST14A", "ST14B", "ST14C"]}),
    dict(code="C-008", title="P6 跨机构月度续方：给药期不重叠且慢病协作已备案",
         pattern_codes=["MULTI_POINT"], primary="P6", region="HD",
         orgs=["H2", "PH4"], status="excluded_legitimate",
         anchors={"person_codes": ["P6"],
                  "rx_codes": ["RX8", "RX9", "RX10"],
                  "settlement_codes": ["ST8", "ST9", "ST10"],
                  "ingredient": "氨氯地平", "filing": "BA-110108-2025-0098"}),
]


def run_seed(reset: bool = True):
    """建表并写入全部演示数据；reset=True 时先 drop_all。"""
    # 延迟导入避免循环依赖
    from .services import counterevidence as ce
    from .services import graph_builder, graphmodel, identity, patterns, projection, snapshot

    if reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_users(db)
        seed_policies(db)
        seed_orgs_persons(db)
        seed_identity_evidence(db)
        seed_p1(db)
        seed_p23(db)
        seed_p4(db)
        seed_p5(db)
        seed_p6(db)
        seed_p9_split(db)
        seed_central(db)
        seed_referral(db)
        seed_pattern_configs(db)
        db.commit()

        # 1) 身份簇 v1
        identity.rebuild_clusters(db, reason="首次身份解析：强证据自动成簇，弱证据保留待核")
        db.commit()

        # 2) 结构图 + 派生模式边
        graph_builder.rebuild_graph(db)
        patterns.upsert_derived_edges(db, CUTOFF)
        db.commit()

        # 3) 案件行
        for m in CASE_META:
            db.add(Case(code=m["code"], title=m["title"], pattern_codes=m["pattern_codes"],
                        primary_person_code=m["primary"], anchors=m["anchors"],
                        region=m["region"], org_codes=m["orgs"], status=m["status"]))
        db.commit()

        # 4) 图模型演练样本 + 初始训练
        graphmodel.ensure_samples(db)
        model_run = graphmodel.train(db, note="种子初始化：时间切分训练（train<=2026-01-31, val=2026-02）")
        db.commit()

        # 5) 逐案件：规则求值 → 反证联查 → 图模型打分
        cases = db.query(Case).order_by(Case.code).all()
        evaluated = {}
        for case in cases:
            rr = patterns.evaluate_case(db, case, CUTOFF)
            cr = ce.run_case_checks(db, case)
            mr = graphmodel.score_case(db, case, rr)
            evaluated[case.code] = (rr, cr, mr)
            case.rule_score = rr.get("raw_score", 0)
            case.model_score = mr.get("calibrated_score")
            case.amount_min = rr.get("amount", {}).get("min", 0)
            case.amount_max = rr.get("amount", {}).get("max", 0)
            case.fund_amount = rr.get("amount", {}).get("fund", 0)
            case.counter_completeness = cr.get("completeness", 0)
            case.unresolved_counter = cr.get("unresolved", 0)
        db.commit()

        # 6) 依据反证/更正状态，将已被否定的支持边置 negated
        ce.apply_edge_negations(db)
        patterns.upsert_derived_edges(db, CUTOFF)  # 刷新派生边状态
        db.commit()

        # 7) 冻结快照 #1（含否定状态，完整保留支持+反驳）
        snap = snapshot.create_snapshot(
            db, reason="基线冻结：虚构验收网络首次分析",
            edge_scope={"profile": "default", "max_hops": 2, "window_days": 180,
                        "same_cluster_only": True,
                        "default_edge_types": "prescription/settlement/supply",
                        "weak_identity": "pending_edge_only_no_expansion"},
            cutoff=CUTOFF, affected_case_codes=[])
        db.commit()

        # 8) 不可变判断 v1 + 最小投影
        for case in cases:
            rr, cr, mr = evaluated[case.code]
            proj = projection.build_projection(db, case, rr, cr)
            j = __import__("app.models", fromlist=["CaseJudgment"]).CaseJudgment(
                case_id=case.id, version=1, kind="auto", author="system",
                note="种子基线判断（规则+反证+图模型）", snapshot_no=snap.snapshot_no,
                rule_result=rr, model_result=mr, counter_result=cr, projection=proj,
                amount_range=rr.get("amount", {}))
            db.add(j)
            db.flush()
            case.current_judgment_id = j.id
            case.first_snapshot_id = snap.id
        db.commit()

        # 9) 冻结排序 Top-10% 命中率评估批次 + 首次双路评估留痕
        from .hitrate_seed import seed_freeze_hitrate
        from .routers.hitrate import run_evaluation
        seed_freeze_hitrate(db)
        for fb in db.query(FreezeBatch).order_by(FreezeBatch.id).all():
            run_evaluation(db, fb, author="system",
                           note="种子初始化：分层随机复核路 + 已办概率校正路双路评估")
        db.commit()
        print(f"[seed] OK: {db.query(Person).count()} persons, "
              f"{db.query(SourceRecord).count()} source records, "
              f"{db.query(GraphNode).count()} nodes, {db.query(GraphEdge).count()} edges, "
              f"snapshot #{snap.snapshot_no}, model_run #{model_run.id}")
    finally:
        db.close()


if __name__ == "__main__":
    run_seed(reset=True)
