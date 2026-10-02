import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import AllocationRun
from app.services.package_orchestrator import run_segment_export
router = APIRouter(prefix="/allocate", tags=["allocate"])

@router.post("/run")
def run_allocate(segment_id: int = 1, db: Session = Depends(get_db)):
    # 与结果包同一套口径：重读柱位 → 引擎 → 写包 → 只读三文件校验 → 绿了才落库
    outcome = run_segment_export(db, segment_id)
    if not outcome.ok:
        if "E_SEGMENT_NOT_FOUND" in outcome.error_codes:
            raise HTTPException(404, "街段不存在")
        raise HTTPException(422, detail={
            "message": "开间结果包三项硬判未通过，本次运行判废、未写库",
            "error_codes": outcome.error_codes,
            "errors": outcome.error_messages,
        })
    return {"id": outcome.run_id, **outcome.payload}

@router.get("/latest")
def latest(segment_id: int = 1, db: Session = Depends(get_db)):
    run = db.scalars(select(AllocationRun).where(AllocationRun.segment_id == segment_id)
                     .order_by(AllocationRun.id.desc())).first()
    if not run:
        outcome = run_segment_export(db, segment_id)
        if not outcome.ok:
            if "E_SEGMENT_NOT_FOUND" in outcome.error_codes:
                raise HTTPException(404, "街段不存在")
            raise HTTPException(422, detail={
                "message": "开间结果包三项硬判未通过",
                "error_codes": outcome.error_codes,
                "errors": outcome.error_messages,
            })
        return {"id": outcome.run_id, **outcome.payload}
    data = json.loads(run.result_json)
    return {"id": run.id, **data}
