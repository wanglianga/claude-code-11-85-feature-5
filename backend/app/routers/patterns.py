"""模式配置、排序、全量求值；图模型指标与重训。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Case, ModelRun, PatternConfig
from ..security import current_user, require_admin
from ..services.ranking import rank_cases
from ..services.rejudge import CUTOFF, rejudge_case

router = APIRouter(prefix="/api/patterns", tags=["patterns"],
                   dependencies=[Depends(current_user)])


@router.get("/configs")
def configs(db: Session = Depends(get_db)):
    return {"items": [{
        "code": p.code, "name": p.name, "description": p.description,
        "enabled": p.enabled, "window_days": p.window_days, "max_hops": p.max_hops,
        "allowed_edge_types": p.allowed_edge_types or [], "params": p.params or {},
        "high_amount_threshold": float(p.high_amount_threshold or 50000),
        "extended_edge_types": p.extended_edge_types or [],
        "extension_cutoff": p.extension_cutoff,
        "policy_code": p.policy_code, "policy_version": p.policy_version,
    } for p in db.query(PatternConfig).order_by(PatternConfig.id).all()]}


@router.get("/ranking")
def ranking(pattern_code: str = "MULTI_POINT", sort: str = "amount_completeness",
            db: Session = Depends(get_db), user=Depends(current_user)):
    items = rank_cases(db, pattern_code, sort, user)
    return {"pattern_code": pattern_code, "sort": sort, "items": items,
            "sort_help": {
                "amount_completeness": "规则结构命中后，按金额上限↓、反证完整度↓排序",
                "amount_unresolved": "金额上限↓、反证未排除项↓排序",
                "rule": "反证调整后的可审计规则分↓",
                "model": "时间切分图模型校准分↓"}}


class EvalAllIn(BaseModel):
    note: str | None = None


@router.post("/evaluate")
def evaluate_all(body: EvalAllIn | None = None, db: Session = Depends(get_db),
                 user=Depends(current_user)):
    cases = db.query(Case).order_by(Case.code).all()
    out = []
    for c in cases:
        j = rejudge_case(db, c, author=user.username,
                         note=(body.note if body and body.note else "手动重新运行模式求值/反证/模型"),
                         kind="manual")
        out.append({"code": c.code, "version": j.version,
                    "rule_score": float(c.rule_score or 0),
                    "model_score": float(c.model_score or 0)})
    return {"ok": True, "rejudged": out}


@router.get("/model/latest")
def model_latest(db: Session = Depends(get_db)):
    run = db.query(ModelRun).order_by(ModelRun.id.desc()).first()
    if not run:
        return {"trained": False}
    return {"trained": True, "id": run.id,
            "run_at": run.run_at.isoformat() if run.run_at else None,
            "train_end": run.train_end.isoformat(), "metrics": run.metrics,
            "weights": run.weights, "calibration": run.calibration, "note": run.note}


@router.post("/model/train")
def model_train(db: Session = Depends(get_db), admin=Depends(require_admin)):
    from ..services.graphmodel import train
    run = train(db, note=f"手动重训（{admin.username}）：时间切分验证")
    return {"ok": True, "id": run.id, "metrics": run.metrics,
            "calibration": run.calibration}
