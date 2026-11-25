"""冻结排序命中率评估 API。

读接口对所有登录用户开放；评估运行、复核登记等写操作仅管理员。
目标量、删失规则与不可识别护栏见 services/hitrate.py。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FreezeBatch, FreezeCandidate, HitRateRun
from ..security import current_user, require_admin
from ..services import hitrate as stats

router = APIRouter(prefix="/api/hitrate", tags=["hitrate"],
                   dependencies=[Depends(current_user)])


# ---------------------------------------------------------------- 序列化
def candidate_dict(c: FreezeCandidate) -> dict:
    return {
        "id": c.id, "rank_no": c.rank_no, "candidate_code": c.candidate_code,
        "region": c.region, "frozen_score": c.frozen_score,
        "score_band": c.score_band,
        "review_source": c.review_source,
        "review_probability": c.review_probability,
        "probability_basis": c.probability_basis,
        "evidence_label": c.evidence_label,
        "evidence_codes": c.evidence_codes or [],
        "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "censor_status": c.censor_status,
        "propensity_score": c.propensity_score,
        "recorded_factors": c.recorded_factors or {},
    }


def batch_dict(b: FreezeBatch, latest_run: HitRateRun | None = None) -> dict:
    return {
        "id": b.id, "code": b.code, "title": b.title,
        "ranking_version": b.ranking_version,
        "cutoff_date": b.cutoff_date.isoformat(),
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "scope": b.scope or {}, "note": b.note,
        "candidate_count": None,
        "latest_run": _run_summary(latest_run),
    }


def _run_summary(r: HitRateRun | None) -> dict | None:
    if not r:
        return None
    return {
        "id": r.id, "run_at": r.run_at.isoformat() if r.run_at else None,
        "author": r.author,
        "random_point": (r.random_audit_result or {}).get("point_estimate"),
        "random_ci95": (r.random_audit_result or {}).get("ci95"),
        "random_identifiable": (r.random_audit_result or {}).get("identifiable"),
        "ipw_point": (r.ipw_result or {}).get("point_estimate"),
        "ipw_ci95": (r.ipw_result or {}).get("ci95"),
        "ipw_identifiable": (r.ipw_result or {}).get("identifiable"),
        "can_claim_ranking_lift": (r.identifiability or {}).get(
            "can_claim_ranking_lift"),
        "verdict": (r.comparison or {}).get("verdict"),
    }


def _get_batch(db: Session, code: str) -> FreezeBatch:
    b = db.query(FreezeBatch).filter_by(code=code).first()
    if not b:
        raise HTTPException(404, "冻结批次不存在")
    return b


# ---------------------------------------------------------------- 评估运行
def run_evaluation(db: Session, batch: FreezeBatch, author: str,
                   note: str) -> HitRateRun:
    """执行双路评估并持久化留痕（seed 与 API 共用）。"""
    candidates = [candidate_dict(c) for c in
                  db.query(FreezeCandidate).filter_by(batch_id=batch.id)
                  .order_by(FreezeCandidate.rank_no).all()]
    strata_keys = tuple(batch.scope.get("strata") or ["A", "B"])
    result = stats.evaluate_hitrate(candidates, strata_keys)
    run = HitRateRun(
        batch_id=batch.id, author=author, method_note=note or "",
        random_audit_result=result["random_audit"],
        ipw_result=result["ipw"], comparison=result["comparison"],
        identifiability=result["identifiability"],
        conclusion=result["conclusion"],
    )
    db.add(run)
    db.flush()
    run._payload = result  # 供调用方直接取用，不持久化
    return run


# ---------------------------------------------------------------- 接口
@router.get("/batches")
def list_batches(db: Session = Depends(get_db)):
    out = []
    for b in db.query(FreezeBatch).order_by(FreezeBatch.id).all():
        latest = db.query(HitRateRun).filter_by(batch_id=b.id) \
            .order_by(HitRateRun.id.desc()).first()
        d = batch_dict(b, latest)
        d["candidate_count"] = db.query(FreezeCandidate) \
            .filter_by(batch_id=b.id).count()
        out.append(d)
    return {"items": out}


@router.get("/batches/{code}")
def batch_detail(code: str, db: Session = Depends(get_db)):
    b = _get_batch(db, code)
    latest = db.query(HitRateRun).filter_by(batch_id=b.id) \
        .order_by(HitRateRun.id.desc()).first()
    d = batch_dict(b, latest)
    d["candidate_count"] = db.query(FreezeCandidate) \
        .filter_by(batch_id=b.id).count()
    return d


@router.get("/batches/{code}/candidates")
def candidates(code: str, region: str | None = None,
               censor_status: str | None = None,
               review_source: str | None = None,
               db: Session = Depends(get_db)):
    b = _get_batch(db, code)
    q = db.query(FreezeCandidate).filter_by(batch_id=b.id)
    if region:
        q = q.filter(FreezeCandidate.region == region)
    if censor_status:
        q = q.filter(FreezeCandidate.censor_status == censor_status)
    if review_source:
        q = q.filter(FreezeCandidate.review_source == review_source)
    rows = q.order_by(FreezeCandidate.rank_no).all()
    return {"items": [candidate_dict(c) for c in rows], "total": len(rows)}


@router.get("/batches/{code}/accounting")
def accounting(code: str, db: Session = Depends(get_db)):
    """分层核查概率账（只读汇总）。"""
    b = _get_batch(db, code)
    rows = [candidate_dict(c) for c in
            db.query(FreezeCandidate).filter_by(batch_id=b.id).all()]
    strata = stats.build_strata(
        rows, tuple(b.scope.get("strata") or ["A", "B"]))
    return {
        "batch_code": code,
        "strata": [stats._public_stratum(s) for s in strata.values()],
        "naive_adjudicated": stats.naive_adjudicated_rate(strata),
        "censor_rule": "右删失/待核件无证据标签，不计阴性",
    }


@router.post("/batches/{code}/evaluate")
def evaluate(code: str, body: dict | None = None,
             db: Session = Depends(get_db), admin=Depends(require_admin)):
    b = _get_batch(db, code)
    note = (body or {}).get("note") or "API 手动触发双路命中率评估"
    run = run_evaluation(db, b, admin.username, note)
    db.commit()
    return {"ok": True, "run_id": run.id, "result": run._payload}


@router.get("/runs")
def list_runs(batch_code: str | None = None, db: Session = Depends(get_db)):
    q = db.query(HitRateRun).join(FreezeBatch)
    if batch_code:
        q = q.filter(FreezeBatch.code == batch_code)
    runs = q.order_by(HitRateRun.id.desc()).all()
    return {"items": [{
        "id": r.id, "batch_code": db.get(FreezeBatch, r.batch_id).code,
        "run_at": r.run_at.isoformat() if r.run_at else None,
        "author": r.author, "method_note": r.method_note,
        **_run_summary(r),
    } for r in runs]}


@router.get("/runs/{run_id}")
def run_detail(run_id: int, db: Session = Depends(get_db)):
    r = db.get(HitRateRun, run_id)
    if not r:
        raise HTTPException(404, "评估运行不存在")
    b = db.get(FreezeBatch, r.batch_id)
    return {
        "id": r.id, "batch_code": b.code,
        "run_at": r.run_at.isoformat() if r.run_at else None,
        "author": r.author, "method_note": r.method_note,
        "estimand": "冻结 Top-N 候选中经规定证据确认欺诈的比例（非已办阳性率）",
        "random_audit": r.random_audit_result,
        "ipw": r.ipw_result,
        "comparison": r.comparison,
        "identifiability": r.identifiability,
        "conclusion": r.conclusion,
    }


class AuditIn(BaseModel):
    evidence_label: str  # confirmed / cleared
    evidence_codes: list[str] = []
    note: str | None = None


@router.post("/candidates/{candidate_id}/audit")
def register_audit(candidate_id: int, body: AuditIn,
                   db: Session = Depends(get_db), admin=Depends(require_admin)):
    """登记一件待核案件的随机复核结论：写入证据标签并解除右删失。

    待核件原本无标签（不计阴性）；复核后按规定证据写入 confirmed/cleared，
    复核入样概率沿用该层随机复核方案 π=m/M。
    """
    from datetime import datetime
    c = db.get(FreezeCandidate, candidate_id)
    if not c:
        raise HTTPException(404, "候选不存在")
    if body.evidence_label not in ("confirmed", "cleared"):
        raise HTTPException(400, "证据标签必须为 confirmed 或 cleared")
    if c.review_source != "none":
        raise HTTPException(400, "仅待核（未核查）候选可登记随机复核结论")
    b = db.get(FreezeBatch, c.batch_id)
    strata = stats.build_strata(
        [candidate_dict(x) for x in
         db.query(FreezeCandidate).filter_by(batch_id=b.id).all()],
        tuple(b.scope.get("strata") or ["A", "B"]))
    s = strata[c.region]
    new_m = s["m_audit"] + 1
    M = s["M_unreviewed"]
    pi = new_m / M if M else None
    # 抽样方案扩大为从原未核查总体 M 中抽 new_m 件：层内全部随机复核样本的
    # 入样概率统一为 new_m/M（均匀 SRS 重设计），保证概率账与 HT 估计口径一致。
    peers = db.query(FreezeCandidate).filter_by(
        batch_id=b.id, region=c.region,
        review_source="random_audit").all()
    basis = (f"随机复核方案扩大：{c.region} 层样本量 m={new_m}/M={M}，"
             f"层内统一入样概率 π={pi:.4f}")
    for peer in peers:
        peer.review_probability = pi
        peer.propensity_score = pi
        peer.probability_basis = basis
    c.review_source = "random_audit"
    c.evidence_label = body.evidence_label
    c.evidence_codes = body.evidence_codes or [
        f"EV-MANUAL-{c.candidate_code}"]
    c.censor_status = "resolved"
    c.reviewed_at = datetime.utcnow()
    c.review_probability = pi
    c.propensity_score = pi
    c.probability_basis = basis
    db.commit()
    return {"ok": True, "candidate_code": c.candidate_code,
            "stratum_sample_size": new_m, "new_inclusion_pi": pi,
            "updated_audit_rows": len(peers) + 1,
            "hint": "复核已留痕，层内入样概率已统一更新；请重新运行评估以刷新点估计与区间"}
