"""候选案件排序：可审计规则组合筛选风险结构 → 金额 + 反证完整度排序。"""
from ..models import Case, CaseJudgment, CounterCheck
from ..security import can_access_case


def _penalty(db, code):
    ps = 0
    for c in db.query(CounterCheck).filter_by(case_code=code).all():
        if c.effect == "close":
            ps += 28
        elif c.effect == "downweight":
            ps += 10
    return min(85, ps)


def case_row(db, c: Case) -> dict:
    checks = db.query(CounterCheck).filter_by(case_code=c.code).all()
    close = any(x.effect == "close" for x in checks)
    down = sum(1 for x in checks if x.effect == "downweight")
    raw_rule = float(c.rule_score or 0)
    # 关闭级反证：规则生效分封顶 12；降权级每项 -12。原始分仍展示，保证可审计。
    effective_rule = 12.0 if close else max(0.0, raw_rule - 12.0 * down)
    j = db.query(CaseJudgment).filter_by(id=c.current_judgment_id).first() if c.current_judgment_id else None
    return {
        "code": c.code, "title": c.title, "pattern_codes": c.pattern_codes,
        "primary_person_code": c.primary_person_code, "region": c.region,
        "org_codes": c.org_codes or [], "status": c.status,
        "rule_score_raw": float(c.rule_score or 0),
        "rule_score_effective": effective_rule,
        "model_score": float(c.model_score) if c.model_score is not None else None,
        "amount_min": float(c.amount_min or 0), "amount_max": float(c.amount_max or 0),
        "fund_amount": float(c.fund_amount or 0),
        "counter_completeness": c.counter_completeness or 0,
        "unresolved_counter": c.unresolved_counter or 0,
        "affected": c.affected, "assignee": c.assignee,
        "judgment_version": j.version if j else None,
        "snapshot_no": j.snapshot_no if j else None,
    }


def rank_cases(db, pattern_code: str | None = None, sort: str = "amount_completeness",
               user=None) -> list[dict]:
    q = db.query(Case)
    cases = q.all()
    if pattern_code and pattern_code != "ALL":
        cases = [c for c in cases if pattern_code in (c.pattern_codes or [])]
    if user is not None:
        cases = [c for c in cases if can_access_case(user, c.region or "", c.org_codes or [])]
    rows = [case_row(db, c) for c in cases]

    if sort == "rule":
        rows.sort(key=lambda r: (-r["rule_score_effective"], -r["amount_max"]))
    elif sort == "model":
        rows.sort(key=lambda r: (-(r["model_score"] or -1), -r["amount_max"]))
    elif sort == "amount_unresolved":
        rows.sort(key=lambda r: (-r["amount_max"], -r["unresolved_counter"],
                                -r["rule_score_effective"]))
    else:  # amount_completeness（题目默认：金额 → 反证完整度）
        rows.sort(key=lambda r: (-r["amount_max"], -r["counter_completeness"],
                                 -r["rule_score_effective"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
