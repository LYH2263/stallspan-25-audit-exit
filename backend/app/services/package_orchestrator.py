"""开间结果包编排入口：重读柱位 → 引擎开间 → 打包写出 → 只读校验 → 绿了才落库。

口径铁律：
  * 打包写出（package_writer）不调用校验；校验（package_validator）只读三文件。
  * 三项硬判任一破即失败：本模块返回 ok=False，CLI 以非 0 退出，绝不只打日志当成功。
  * 校验不过绝不写 AllocationRun，库内已成功运行的行数不增不改。
  * 每次编排都重新从数据库读取街段/柱心/摊主，禁止吃写出前的旧柱缓存。
  * 绿仓主图载荷直接解析自刚校验通过的三文件，未改柱时包内放置与绿仓主图逐字节同口径。

CLI:
  python -m app.services.package_orchestrator export --segment-id 1 --out ./pkg[.zip]
  python -m app.services.package_orchestrator verify --path ./pkg
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.models.models import AllocationRun, Pillar, Segment, Vendor
from app.services import package_validator as pv
from app.services.first_fit_engine import AllocResult, allocate_first_fit
from app.services.package_writer import (
    SegmentSpec,
    payload_from_files,
    write_package,
)
from app.services.seed import seed_if_empty


@dataclass
class ExportOutcome:
    ok: bool
    segment_name: str | None = None
    out_path: Path | None = None
    payload: dict | None = None
    run_id: int | None = None
    error_codes: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)


def load_segment_spec(db: Session, segment_id: int) -> tuple[Segment | None, SegmentSpec, list[dict]]:
    """每次都重新读库：新柱心立刻生效，不经过任何旧柱缓存。"""
    seg = db.get(Segment, segment_id)
    if seg is None:
        return None, None, []  # type: ignore[return-value]
    pillars = [{"position_m": p.position_m, "thickness_m": p.thickness_m, "label": p.label}
               for p in db.scalars(
                   select(Pillar).where(Pillar.segment_id == segment_id)
                                 .order_by(Pillar.position_m, Pillar.id)).all()]
    vendors = [{"id": v.id, "name": v.name, "stall_width_m": v.stall_width_m, "priority": v.priority}
               for v in db.scalars(
                   select(Vendor).where(Vendor.market_day_id == seg.market_day_id)
                                 .order_by(Vendor.id)).all()]
    spec = SegmentSpec(id=seg.id, name=seg.name, width_m=seg.width_m, pillars=pillars)
    return seg, spec, vendors


def run_segment_export(db: Session, segment_id: int,
                       out_path: str | Path | None = None) -> ExportOutcome:
    """完整编排。out_path 给目录/.zip 则保留结果包；None 用临时目录（校验后清理）。

    只有包校验全绿才写 AllocationRun；红了库行数不增。
    """
    seg, spec, vendors = load_segment_spec(db, segment_id)
    if seg is None:
        return ExportOutcome(False, error_codes=["E_SEGMENT_NOT_FOUND"],
                             error_messages=[f"街段不存在: id={segment_id}"])

    # 1) 当次读取的柱位 → 引擎（不使用任何缓存结果）
    result: AllocResult = allocate_first_fit(spec.width_m, vendors, spec.pillars)

    # 2) 打包写出（writer 自身绝不校验）
    keep_pkg = out_path is not None
    pkg_dir = Path(out_path) if keep_pkg else Path(tempfile.mkdtemp(prefix="stallspan_pkg_"))
    try:
        write_package(spec, result, pkg_dir)

        # 3) 校验只从盘上读三文件
        verdict = pv.validate_package(pkg_dir)
        if not verdict.ok:
            return ExportOutcome(
                False, segment_name=seg.name, out_path=pkg_dir if keep_pkg else None,
                error_codes=verdict.codes,
                error_messages=[f"[{e.code}] {e.message}" for e in verdict.errors])

        # 4) 绿：主图载荷解析自刚通过校验的三文件（包即事实源，目录/zip 同一套读法）
        raw_files, load_errs = pv.load_three_files(pkg_dir)
        if load_errs or any(raw_files.get(n) is None for n in
                            ("manifest.json", "placements.json", "rejected.json")):
            return ExportOutcome(
                False, segment_name=seg.name, out_path=pkg_dir if keep_pkg else None,
                error_codes=["E_PACKAGE_UNREADABLE"],
                error_messages=["[E_PACKAGE_UNREADABLE] 绿包回读三文件失败"])
        files_text = {name: raw_files[name].decode("utf-8")
                      for name in ("manifest.json", "placements.json", "rejected.json")}
        payload = payload_from_files(files_text)

        run = AllocationRun(segment_id=seg.id,
                            result_json=json.dumps(payload, ensure_ascii=False))
        db.add(run)
        db.commit()
        db.refresh(run)
        return ExportOutcome(True, segment_name=seg.name,
                             out_path=pkg_dir if keep_pkg else None,
                             payload=payload, run_id=run.id)
    finally:
        if not keep_pkg:
            shutil.rmtree(pkg_dir, ignore_errors=True)


def verify_only(path: str | Path) -> pv.ValidationResult:
    return pv.validate_package(path)


def _print_green(outcome: ExportOutcome) -> None:
    p = outcome.payload
    print(f"[绿] 街段「{outcome.segment_name}」开间结果包校验通过"
          f"{(' -> ' + str(outcome.out_path)) if outcome.out_path else ''}")
    names = "、".join(x["vendor_name"] for x in p["placements"]) or "（无）"
    print(f"已放置 {len(p['placements'])} 个色块：{names}")
    if p["rejected"]:
        rj = "、".join(f"{x['vendor_name']}({x['reason_code']})" for x in p["rejected"])
        print(f"放不下 {len(p['rejected'])} 个：{rj}")
    else:
        print("放不下 0 个：全部放下")
    print(f"绿仓主图运行记录 id={outcome.run_id}")


def _print_red(outcome: ExportOutcome) -> None:
    print("[废] 结果包未通过三项硬判，已判废且未写库：", file=sys.stderr)
    for msg in outcome.error_messages:
        print("  " + msg, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="package_orchestrator",
                                     description="开间结果包写出 / 校验编排")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_exp = sub.add_parser("export", help="重新开间→写包→校验→绿了落库")
    p_exp.add_argument("--segment-id", type=int, default=1)
    p_exp.add_argument("--out", default=None, help="结果包目录或 .zip 路径")
    p_ver = sub.add_parser("verify", help="只读校验已有结果包（目录或 .zip）")
    p_ver.add_argument("--path", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "verify":
        verdict = verify_only(args.path)
        if verdict.ok:
            print(f"[绿] 结果包三项硬判全部通过：{args.path}")
            return 0
        print(f"[废] 结果包三项硬判未通过：{args.path}", file=sys.stderr)
        for e in verdict.errors:
            print(f"  [{e.code}] {e.message}", file=sys.stderr)
        return 1

    # export
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
        outcome = run_segment_export(db, args.segment_id, args.out)
    except Exception:  # 任何意外也不得伪装成功
        traceback.print_exc()
        return 2
    finally:
        db.close()
    if outcome.ok:
        _print_green(outcome)
        return 0
    _print_red(outcome)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
