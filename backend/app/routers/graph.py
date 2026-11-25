"""受限图扩展接口。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..security import current_user
from ..services.expansion import DEFAULT_ALLOWED, expand, high_amount_extension

router = APIRouter(prefix="/api/graph", tags=["graph"],
                   dependencies=[Depends(current_user)])


@router.get("/edge-types")
def edge_types():
    return {"default_allowed": DEFAULT_ALLOWED}


@router.get("/expand")
def do_expand(start_type: str, start_code: str, window_days: int = 180,
              max_hops: int = 2, include_weak: bool = True,
              include_negated: bool = False, edge_types: str | None = None,
              db: Session = Depends(get_db)):
    if start_type not in ("beneficiary", "claim", "node"):
        raise HTTPException(400, "start_type 仅支持 beneficiary/claim/node")
    allowed = edge_types.split(",") if edge_types else None
    return expand(db, start_type, start_code, window_days=window_days,
                  max_hops=max_hops, allowed_edge_types=allowed,
                  include_weak=include_weak, include_negated=include_negated)


class ExtIn(BaseModel):
    case_code: str
    freeze: bool = False


@router.post("/high-amount-extension")
def do_extend(body: ExtIn, db: Session = Depends(get_db), user=Depends(current_user)):
    try:
        return high_amount_extension(db, body.case_code, freeze=body.freeze,
                                     user_name=user.username)
    except ValueError as e:
        raise HTTPException(404, str(e))
