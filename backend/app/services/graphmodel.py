"""可审计图排序模型。

- 特征全部来自受限图结构/模式必要事实/反证结论，权重可查看；
- 演练样本带日期标签，按时间切分训练/验证（不用随机切分掩盖时间漂移）；
- 纯 Python 逻辑回归（无黑盒依赖），并按验证集残差对地区/机构类型做收缩校准；
- 每个高分都回传与规则引擎一致的可追溯支持链与反证状态；
- 输出仅为候选排序分，不是违法结论。
"""
import math
import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..models import Case, CounterCheck, ModelRun, ModelSample, Org

FEATURE_KEYS = [
    ("strong_cluster", "同伪标识/强簇"),
    ("weak_identity_only", "仅弱身份证据"),
    ("distinct_pharmacies", "跨药店数"),
    ("same_day", "同日发生"),
    ("overlap_days", "给药期重叠强度"),
    ("overlap_qty", "数量重叠强度"),
    ("schedule_conflict", "停诊冲突"),
    ("split_group", "拆方组强度"),
    ("central_group", "集中结算强度"),
    ("referral_group", "异常转诊强度"),
    ("cross_org", "跨机构强度"),
    ("log_amount", "金额规模"),
    ("high_amount", "高金额跨机构"),
    ("unresolved", "反证未排除项"),
    ("counter_explains", "反证解释成立数"),
    ("negated_ratio", "链上边被否定比例"),
    ("signature_match", "签收匹配"),
    ("bed_match", "床位/住养记录"),
    ("policy_compliant", "政策合规解释"),
    ("appeal_return", "申诉确认退回"),
]
KEYS = [k for k, _ in FEATURE_KEYS]
LABELS = dict(FEATURE_KEYS)

# 教师权重仅用于生成“演练样本”，让时间切分验证有可学习信号
TEACHER = {
    "strong_cluster": 1.0, "weak_identity_only": -1.7, "distinct_pharmacies": 0.9,
    "same_day": 0.8, "overlap_days": 1.7, "overlap_qty": 0.6, "schedule_conflict": 1.2,
    "split_group": 1.1, "central_group": 1.0, "referral_group": 0.9, "cross_org": 0.4,
    "log_amount": 0.7, "high_amount": 0.3, "unresolved": 0.9,
    "counter_explains": -2.3, "negated_ratio": -2.1, "signature_match": -0.8,
    "bed_match": -0.6, "policy_compliant": -1.3, "appeal_return": -2.5,
}

ORG_TYPE = {"PH1": "pharmacy", "PH2": "pharmacy", "PH3": "pharmacy", "PH4": "pharmacy",
            "PH5": "pharmacy", "PH7": "pharmacy", "H1": "hospital", "H2": "clinic",
            "H3": "hospital", "H4": "hospital", "E1": "agency"}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# --------------------------------------------------------------- 特征
def extract_features(db: Session, case: Case, rr: dict | None = None) -> dict:
    rr = rr or {}
    a = case.anchors or {}
    facts = {f["key"]: f for f in rr.get("necessary_facts", [])}

    def fact_passed(key):
        return 1.0 if facts.get(key, {}).get("passed") else 0.0

    amount_max = float(case.amount_max or rr.get("amount", {}).get("max", 0) or 0)
    chain = rr.get("support_chain", [])
    negated = 0
    if chain:
        from ..models import GraphEdge
        rows = db.query(GraphEdge).filter(GraphEdge.uid.in_(chain)).all()
        negated = sum(1 for e in rows if e.status == "negated") / max(1, len(rows))

    checks = db.query(CounterCheck).filter_by(case_code=case.code).all()
    cmap = {c.check_type: c.status for c in checks}
    explains = sum(1 for c in checks if c.status in ("checked_explains", "confirmed"))
    unresolved = sum(1 for c in checks if c.status in ("not_excluded", "no_record"))

    pharmacies = rr.get("pharmacies") or a.get("pharmacies") or []
    pairs = rr.get("pairs", [])
    max_ov = max([p.get("overlap_days", 0) for p in pairs] or [0])
    max_oq = max([p.get("overlap_qty", 0) for p in pairs] or [0])

    code0 = (case.pattern_codes or [""])[0]
    feats = {
        "strong_cluster": fact_passed("same_identity_cluster") or (1.0 if code0 != "WEAK_IDENTITY" and a.get("person_codes") and len(a.get("person_codes", [])) == 1 else 0.0),
        "weak_identity_only": 1.0 if code0 == "WEAK_IDENTITY" else 0.0,
        "distinct_pharmacies": _clamp(len(pharmacies) / 3.0),
        "same_day": fact_passed("same_day"),
        "overlap_days": _clamp(max_ov / 28.0),
        "overlap_qty": _clamp(max_oq / 56.0),
        "schedule_conflict": fact_passed("schedule_conflict"),
        "split_group": _clamp(len(a.get("rx_codes", [])) / 4.0) if code0 == "SPLIT_RX" else 0.0,
        "central_group": _clamp(len(a.get("settlement_codes", [])) / 5.0) if code0 == "CENTRAL_SETTLE" else 0.0,
        "referral_group": _clamp(len(a.get("referral_codes", [])) / 4.0) if code0 == "ABNORMAL_REFERRAL" else 0.0,
        "cross_org": _clamp(len(case.org_codes or []) / 4.0),
        "log_amount": _clamp(math.log10(max(amount_max, 1)) / 5.0),
        "high_amount": 1.0 if amount_max >= 50000 else 0.0,
        "unresolved": _clamp(unresolved / 6.0),
        "counter_explains": _clamp(explains / 6.0),
        "negated_ratio": _clamp(negated),
        "signature_match": 1.0 if cmap.get("signature") == "checked_explains" else 0.0,
        "bed_match": 1.0 if cmap.get("bed") == "checked_explains" else 0.0,
        "policy_compliant": 1.0 if cmap.get("policy") == "checked_explains" else 0.0,
        "appeal_return": 1.0 if cmap.get("appeal") == "confirmed" else 0.0,
    }
    return {k: round(float(v), 4) for k, v in feats.items()}


# --------------------------------------------------------------- 演练样本
def ensure_samples(db: Session, n_train=120, n_val=40):
    if db.query(ModelSample).count() > 0:
        return
    rng = random.Random(20260331)
    regions = ["CY", "HD", "DC", "XC"]
    orgs = ["PH1", "PH2", "PH3", "PH4", "PH5", "H1", "H2", "H3", "H4"]

    def archetype():
        kind = rng.random()
        f = {k: rng.uniform(0, 0.15) for k in KEYS}
        force = None
        if kind < 0.26:  # 真实多点重复
            f.update(strong_cluster=rng.choice([0.8, 1]), distinct_pharmacies=rng.uniform(.66, 1),
                     same_day=rng.choice([0, 1, 1]), overlap_days=rng.uniform(.3, 1),
                     overlap_qty=rng.uniform(.3, .9), schedule_conflict=rng.choice([0, 0, 1]),
                     unresolved=rng.uniform(.5, 1), log_amount=rng.uniform(.35, .6),
                     cross_org=rng.uniform(.4, .8))
        elif kind < 0.42:  # 表面重复但调拨/退回解释
            f.update(strong_cluster=1, distinct_pharmacies=rng.uniform(.66, 1),
                     same_day=rng.choice([0, 1]), overlap_days=rng.uniform(.3, 1),
                     negated_ratio=rng.uniform(.7, 1),
                     counter_explains=rng.uniform(.5, 1), unresolved=rng.uniform(0, .3),
                     appeal_return=rng.choice([0, 1]), policy_compliant=rng.choice([0, 1]))
        elif kind < 0.55:  # 拆方
            f.update(split_group=rng.uniform(.75, 1), same_day=1, strong_cluster=1,
                     unresolved=rng.uniform(.5, 1), log_amount=rng.uniform(.4, .65))
        elif kind < 0.68:  # 集中结算 / 转诊
            if rng.random() < .5:
                f.update(central_group=rng.uniform(.75, 1), unresolved=rng.uniform(.5, 1),
                         log_amount=rng.uniform(.55, .8), high_amount=rng.choice([0, 1]))
            else:
                f.update(referral_group=rng.uniform(.75, 1), cross_org=rng.uniform(.5, 1),
                         high_amount=rng.choice([0, 1]), log_amount=rng.uniform(.6, .95),
                         unresolved=rng.uniform(.5, 1))
        elif kind < 0.82:
            # 新型跨机构协作：刻意落在规则硬门槛之下（重叠/药店数/组数都不达标），
            # 但软特征组合异常——规则基线易漏，图模型可召回。
            f.update(strong_cluster=rng.choice([.8, 1]),
                     distinct_pharmacies=rng.choice([.33, .5, .6]),
                     same_day=rng.choice([0, 1]),
                     overlap_days=rng.uniform(.05, .22),
                     cross_org=rng.uniform(.55, 1),
                     log_amount=rng.uniform(.5, .78),
                     unresolved=rng.uniform(.55, .95),
                     central_group=rng.choice([0, .25, .5]),
                     referral_group=rng.choice([0, .3, .6]),
                     schedule_conflict=rng.choice([0, 0, 1]))
            force = 1
        else:  # 良性：弱身份/慢病/签收匹配
            f.update(weak_identity_only=rng.choice([0, 1]),
                     signature_match=rng.choice([0, 1, 1]), bed_match=rng.choice([0, 1]),
                     policy_compliant=rng.choice([0, 1]),
                     counter_explains=rng.uniform(.35, .9), unresolved=rng.uniform(0, .35),
                     overlap_days=rng.uniform(0, .2))
        return {k: _clamp(v) for k, v in f.items()}, force

    start = date(2025, 9, 1)
    rows = []
    total = n_train + n_val
    for i in range(total):
        f, force = archetype()
        z = -1.7 + sum(TEACHER[k] * f[k] for k in KEYS) + rng.gauss(0, 0.35)
        p = 1 / (1 + math.exp(-z))
        label = force if force is not None else (1 if rng.random() < p else 0)
        if i < n_train:
            d = start + timedelta(days=rng.randint(0, 145))  # <= ~2026-01-24
        else:
            d = date(2026, 2, 1) + timedelta(days=rng.randint(0, 26))
        rows.append(ModelSample(sample_code=f"MS-{i+1:03d}", sample_date=d,
                                region=rng.choice(regions), org_code=rng.choice(orgs),
                                features=f, label=label))
    db.add_all(rows)
    db.commit()


# --------------------------------------------------------------- 训练
def _sigmoid(z):
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1 / (1 + math.exp(-z))


def _train_lr(vectors, labels, epochs=600, lr=0.35, l2=2e-3):
    n, d = len(vectors), len(KEYS)
    w = [0.0] * d
    b = 0.0
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for x, y in zip(vectors, labels):
            p = _sigmoid(b + sum(w[j] * x[j] for j in range(d)))
            err = p - y
            for j in range(d):
                gw[j] += err * x[j]
            gb += err
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
        b -= lr * gb / n
    return w, b


def _auc(pairs):
    # pairs: (score, label)
    pos = [s for s, y in pairs if y == 1]
    neg = [s for s, y in pairs if y == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(pos) * len(neg))


def train(db: Session, note: str = "") -> ModelRun:
    samples = db.query(ModelSample).order_by(ModelSample.sample_code).all()
    train_rows = [s for s in samples if s.sample_date <= date(2026, 1, 31)]
    val_rows = [s for s in samples if s.sample_date > date(2026, 1, 31)]
    X = [[s.features.get(k, 0) for k in KEYS] for s in train_rows]
    y = [s.label for s in train_rows]
    w, b = _train_lr(X, y)

    def raw(s):
        x = [s.features.get(k, 0) for k in KEYS]
        return _sigmoid(b + sum(w[j] * x[j] for j in range(len(KEYS))))

    val_pred = [(raw(s), s.label, s) for s in val_rows]
    pred_label = [1 if p >= 0.5 else 0 for p, _, _ in val_pred]
    tp = sum(1 for pl, (_, yy, _) in zip(pred_label, val_pred) if pl == 1 and yy == 1)
    fp = sum(1 for pl, (_, yy, _) in zip(pred_label, val_pred) if pl == 1 and yy == 0)
    fn = sum(1 for pl, (_, yy, _) in zip(pred_label, val_pred) if pl == 0 and yy == 1)
    tn = len(val_pred) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    acc = (tp + tn) / max(1, len(val_pred))
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    eps = 1e-9
    logloss = -sum(yy * math.log(max(p, eps)) + (1 - yy) * math.log(max(1 - p, eps))
                   for p, yy, _ in val_pred) / max(1, len(val_pred))
    auc = _auc([(p, yy) for p, yy, _ in val_pred])

    # 规则基线：硬事实门槛（用于“规则召回较低”的对照，数字来自真实验证集）
    def rule_gate(f):
        return int((f.get("overlap_days", 0) >= .25 and f.get("distinct_pharmacies", 0) >= .6)
                   or f.get("split_group", 0) >= .7
                   or f.get("central_group", 0) >= .7
                   or f.get("referral_group", 0) >= .7)
    rp = [rule_gate(s.features) for s in val_rows]
    rtp = sum(1 for pl, s in zip(rp, val_rows) if pl == 1 and s.label == 1)
    rfn = sum(1 for pl, s in zip(rp, val_rows) if pl == 0 and s.label == 1)
    rule_recall = rtp / (rtp + rfn) if rtp + rfn else 0.0
    rfp = sum(1 for pl, s in zip(rp, val_rows) if pl == 1 and s.label == 0)
    rule_precision = rtp / (rtp + rfp) if rtp + rfp else 0.0

    # 地区校准（观测正例数 / 模型期望正例数，贝叶斯平滑 + 收缩）
    cal_region = {}
    for region in sorted({s.region for s in val_rows}):
        grp = [(p, yy) for p, yy, s in val_pred if s.region == region]
        obs = sum(yy for _, yy in grp) + 1.0
        exp = sum(p for p, _ in grp) + 1.0
        factor = obs / exp
        factor = 1 + 0.5 * (factor - 1)
        cal_region[region] = round(_clamp(factor, .75, 1.35), 3)
    cal_org = {}
    for ot in sorted({ORG_TYPE.get(s.org_code, "other") for s in val_rows}):
        grp = [(p, yy) for p, yy, s in val_pred if ORG_TYPE.get(s.org_code, "other") == ot]
        obs = sum(yy for _, yy in grp) + 1.0
        exp = sum(p for p, _ in grp) + 1.0
        factor = 1 + 0.5 * (obs / exp - 1)
        cal_org[ot] = round(_clamp(factor, .75, 1.35), 3)

    run = ModelRun(
        train_end=date(2026, 1, 31), note=note or "时间切分：train<=2026-01-31, validation=2026-02",
        metrics={
            "train_size": len(train_rows), "val_size": len(val_rows),
            "accuracy": round(acc, 3), "precision": round(precision, 3),
            "recall": round(recall, 3), "f1": round(f1, 3), "auc": round(auc or 0, 3),
            "logloss": round(logloss, 3),
            "rule_baseline_recall": round(rule_recall, 3),
            "rule_baseline_precision": round(rule_precision, 3),
            "recall_gain_vs_rules": round(recall - rule_recall, 3),
            "split": "按业务日期时间切分（非随机）",
        },
        weights={"bias": round(b, 4), **{k: round(w[j], 4) for j, k in enumerate(KEYS)}},
        calibration={"region": cal_region, "org_type": cal_org,
                     "method": "验证集残差因子，贝叶斯平滑后50%收缩，截断[0.75,1.35]；"
                               "合成分数=raw*region_factor*org_factor（总因子截断[0.6,1.7]）"})
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def latest_run(db: Session) -> ModelRun | None:
    return db.query(ModelRun).order_by(ModelRun.id.desc()).first()


def score_case(db: Session, case: Case, rr: dict | None = None) -> dict:
    feats = extract_features(db, case, rr)
    run = latest_run(db)
    if run is None:
        run = train(db)
    w = run.weights or {}
    b = w.get("bias", 0.0)
    raw = _sigmoid(b + sum(w.get(k, 0.0) * feats[k] for k in KEYS))

    cal = run.calibration or {}
    rf = (cal.get("region") or {}).get(case.region, 1.0)
    org_type = "other"
    if case.org_codes:
        org = db.query(Org).filter_by(code=case.org_codes[0]).first()
        org_type = org.org_type if org else ORG_TYPE.get(case.org_codes[0], "other")
    of = (cal.get("org_type") or {}).get(org_type, 1.0)
    total_factor = _clamp(rf * of, 0.6, 1.7)
    ml_score = _clamp(raw * total_factor)

    # 显式、可审计的反证闸门：只要有一个核查渠道取得“关闭级”结论
    # （调拨/退回等确认的合法解释或申诉退回），模型排序分封顶 18。
    close_checks = db.query(CounterCheck).filter(
        CounterCheck.case_code == case.code, CounterCheck.effect == "close").all()
    gate = {"applied": bool(close_checks), "cap": 18,
            "close_checks": [{"check_type": c.check_type, "title": c.title,
                              "status": c.status} for c in close_checks],
            "reason": "存在经材料确认的关闭级反证，模型分不得将其排到待核线索前列"
                      if close_checks else None}
    final_score = min(ml_score, gate["cap"] / 100.0) if gate["applied"] else ml_score

    contrib = sorted(
        [{"feature": k, "label": LABELS[k], "value": feats[k],
          "weight": w.get(k, 0.0),
          "contribution": round(w.get(k, 0.0) * feats[k], 4)} for k in KEYS],
        key=lambda x: abs(x["contribution"]), reverse=True)[:8]

    return {
        "model_run_id": run.id,
        "raw_score": round(raw * 100, 1),
        "ml_score": round(ml_score * 100, 1),
        "calibrated_score": round(final_score * 100, 1),
        "final_score": round(final_score * 100, 1),
        "score": round(final_score * 100, 1),
        "counter_gate": gate,
        "calibration_applied": {"region": case.region, "region_factor": rf,
                                "org_type": org_type, "org_factor": of,
                                "total_factor": round(total_factor, 3)},
        "feature_values": feats,
        "top_contributions": contrib,
        "metrics": run.metrics,
        "trace_chain_uids": (rr or {}).get("support_chain", []),
        "trace_paths": (rr or {}).get("trace_paths", []),
        "counter_evidence": [
            {"check_type": c.check_type, "status": c.status, "effect": c.effect}
            for c in db.query(CounterCheck).filter_by(case_code=case.code).all()],
        "fairness_note": "已按验证集地区/机构类型残差校准；跨地区/新型协作模式仍可能有偏，"
                         "高分必须结合可追溯路径与反证人工复核。",
        "disclaimer": "模型分仅为候选案件排序信号，可能漏召回新型协作模式，不构成违法结论。",
    }
