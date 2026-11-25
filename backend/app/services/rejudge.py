"""案件复判：在数据/裁定变化后重新求值，并追加不可变 CaseJudgment 版本。

旧版本及其 snapshot_no / projection / 解释全部保留，用于“旧案件材料保留原判断来源”。
"""
from datetime import date

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Case, CaseJudgment, GraphSnapshot
from . import counterevidence as ce
from . import graphmodel, patterns, projection

CUTOFF = date.fromisoformat(settings.DEFAULT_CUTOFF)


def rejudge_case(db: Session, case: Case, author: str = "system",
                 note: str = "重新求值", kind: str = "resnapshot",
                 snapshot_no: int | None = None, commit: bool = True) -> CaseJudgment:
    rr = patterns.evaluate_case(db, case, CUTOFF)
    cr = ce.run_case_checks(db, case)
    mr = graphmodel.score_case(db, case, rr)

    case.rule_score = rr.get("raw_score", 0)
    case.model_score = mr.get("calibrated_score")
    amt = rr.get("amount", {})
    case.amount_min = amt.get("min", 0)
    case.amount_max = amt.get("max", 0)
    case.fund_amount = amt.get("fund", 0)
    case.counter_completeness = cr.get("completeness", 0)
    case.unresolved_counter = cr.get("unresolved", 0)

    proj = projection.build_projection(db, case, rr, cr)
    last_v = db.query(CaseJudgment).filter_by(case_id=case.id) \
        .order_by(CaseJudgment.version.desc()).first()
    version = 1 if not last_v else last_v.version + 1
    if snapshot_no is None:
        snap = db.query(GraphSnapshot).order_by(GraphSnapshot.snapshot_no.desc()).first()
        snapshot_no = snap.snapshot_no if snap else 1
    j = CaseJudgment(case_id=case.id, version=version, kind=kind, author=author, note=note,
                     snapshot_no=snapshot_no, rule_result=rr, model_result=mr,
                     counter_result=cr, projection=proj, amount_range=amt)
    db.add(j)
    db.flush()
    case.current_judgment_id = j.id
    db.flush()
    if commit:
        db.commit()
        db.refresh(j)
    return j
