"""开间结果包编排入口：算开间 → 写包 → 三项硬判 → 绿了才落库。

- 每次现查库内挡柱（改柱后再写出按新柱，不吃写出前旧柱缓存）；
- 写出不调用校验，校验只读三文件，二者由本入口先后编排；
- 三项硬判任一项破即以非 0 结束，且不改写库内已成功运行（行数不增）；
- 全绿才把本次运行写入 allocation_runs，主图/放不下与包内口径一致。

用法：python -m app.services.export_bay_package [--segment-id 1] [--out exports/bay_package]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models.models import AllocationRun
from app.services.allocate_service import compute_allocation_result
from app.services.bay_package_validator import validate_package
from app.services.bay_package_writer import write_package
from app.services.seed import seed_if_empty

EXIT_ORCHESTRATION_ERROR = 1


def run_export(db: Session, segment_id: int, out_path: str, persist: bool = True) -> int:
    """跑一遍“算→写→判→落库”，返回进程退出码；非 0 时保证不动库内已成功运行。"""
    try:
        result = compute_allocation_result(db, segment_id)  # 现查柱，禁止旧柱缓存
    except LookupError as exc:
        print(str(exc))
        return EXIT_ORCHESTRATION_ERROR
    write_package(result, out_path)          # 写出：只写三文件，不校验
    report = validate_package(out_path)      # 校验：只读三文件
    if not report.ok:
        for f in report.failures:
            print(f"[退出码 {f.code}] {f.message}")
        print("三项硬判未全绿，本次不落库，库内已成功运行保持不变")
        return report.exit_code
    if persist:
        run = AllocationRun(
            segment_id=segment_id,
            created_at=datetime.utcnow(),
            result_json=json.dumps(result, ensure_ascii=False),
        )
        db.add(run)
        db.commit()
    seg = result["segment"]
    placed = "、".join(p["vendor_name"] for p in result["placements"]) or "无"
    rejected = "、".join(r["vendor_name"] for r in result["rejected"]) or "无"
    print(f"街段：{seg['name']}（{seg['width_m']} m）· 挡柱 {len(result['pillars'])} 根")
    print(f"已放置 {len(result['placements'])} 摊：{placed}")
    print(f"放不下 {len(result['rejected'])} 摊：{rejected}")
    print(f"结果包：{out_path}（三项硬判全绿）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="开间结果包编排入口")
    ap.add_argument("--segment-id", type=int, default=1)
    ap.add_argument("--out", default="exports/bay_package",
                    help="输出目录，或以 .zip 结尾的压缩包路径")
    ap.add_argument("--no-persist", action="store_true", help="只写包校验，不落库")
    args = ap.parse_args(argv)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if settings.seed_on_empty:
            seed_if_empty(db)
        return run_export(db, args.segment_id, args.out, persist=not args.no_persist)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
