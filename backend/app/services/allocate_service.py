"""分配口径服务：API 与结果包编排共用同一条“现查库 → 跑引擎”路径。

每次调用都重新读取街段、挡柱、摊主，不留旧柱缓存——
改柱后再分配/再写出，一律按库里当前柱计算。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Pillar, Segment, Vendor
from app.services.first_fit_engine import allocate_first_fit, result_to_dict


def compute_allocation_result(db: Session, segment_id: int) -> dict:
    """现查库内街段/挡柱/摊主并跑 First-Fit，返回与主图、放不下同一口径的结果字典。

    结构：{placements, rejected, free_spans, segment, pillars}
    街段不存在时抛 LookupError。
    """
    seg = db.get(Segment, segment_id)
    if not seg:
        raise LookupError(f"街段不存在: {segment_id}")
    pillars = [
        {"position_m": p.position_m, "thickness_m": p.thickness_m, "label": p.label}
        for p in db.scalars(select(Pillar).where(Pillar.segment_id == segment_id)).all()
    ]
    vendors = [
        {"id": v.id, "name": v.name, "stall_width_m": v.stall_width_m, "priority": v.priority}
        for v in db.scalars(select(Vendor).where(Vendor.market_day_id == seg.market_day_id)).all()
    ]
    result = result_to_dict(allocate_first_fit(seg.width_m, vendors, pillars))
    result["segment"] = {"id": seg.id, "name": seg.name, "width_m": seg.width_m}
    result["pillars"] = pillars
    return result
