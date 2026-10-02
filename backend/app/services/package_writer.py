"""开间结果包写出：把一次开间结果落成固定三文件（目录或 zip）。

三文件（包内顶层、文件名固定）：
  manifest.json   清单——街宽与柱心
  placements.json 放置——起止与摊名
  rejected.json   拒绝——放不下与拒因码

本模块只负责写出，绝不调用包校验（app.services.package_validator）。
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.services.first_fit_engine import AllocResult

MANIFEST_NAME = "manifest.json"
PLACEMENTS_NAME = "placements.json"
REJECTED_NAME = "rejected.json"
PACKAGE_FILES = (MANIFEST_NAME, PLACEMENTS_NAME, REJECTED_NAME)


@dataclass(frozen=True)
class SegmentSpec:
    """写出所需的街段口径：街宽与柱心。"""
    id: int
    name: str
    width_m: float
    pillars: list[dict]  # position_m / thickness_m / label

    def to_manifest(self) -> dict:
        return {
            "segment": {
                "id": self.id,
                "name": self.name,
                "width_m": round(float(self.width_m), 3),
            },
            # 柱心 = position_m（柱的中心线坐标），厚度向两侧各展 half
            "pillars": [
                {
                    "position_m": round(float(p["position_m"]), 3),
                    "thickness_m": round(float(p.get("thickness_m", 0.4)), 3),
                    "label": p.get("label", "挡柱"),
                }
                for p in sorted(self.pillars, key=lambda p: p["position_m"])
            ],
        }


def result_to_files(spec: SegmentSpec, result: AllocResult) -> dict[str, str]:
    """把开间结果转成 文件名 -> UTF-8 JSON 文本 的映射（固定三文件）。"""
    manifest = spec.to_manifest()
    placements = [
        {
            "vendor_id": p.vendor_id,
            "vendor_name": p.vendor_name,
            "start_m": round(float(p.start_m), 3),
            "end_m": round(float(p.end_m), 3),
            "width_m": round(float(p.width_m), 3),
        }
        for p in result.placements
    ]
    rejected = [
        {
            "vendor_id": r.vendor_id,
            "vendor_name": r.vendor_name,
            "width_m": round(float(r.width_m), 3),
            "cannot_fit": True,
            "reason": r.reason,
            "reason_code": r.reason_code,
        }
        for r in result.rejected
    ]
    return {
        MANIFEST_NAME: json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        PLACEMENTS_NAME: json.dumps(placements, ensure_ascii=False, indent=2, sort_keys=True),
        REJECTED_NAME: json.dumps(rejected, ensure_ascii=False, indent=2, sort_keys=True),
    }


def payload_from_files(files: dict[str, str]) -> dict:
    """三文件 JSON -> 主图/放不下页消费的同口径载荷。

    包是唯一事实源：绿仓主图（AllocationRun.result_json）与页上色块、放不下名单
    都由这三文件解析而来，因此「包绿」与「页上」天然同口径，不会打架。
    """
    manifest = json.loads(files[MANIFEST_NAME])
    placements = json.loads(files[PLACEMENTS_NAME])
    rejected = json.loads(files[REJECTED_NAME])
    return {
        "segment": manifest["segment"],
        "pillars": manifest["pillars"],
        "placements": placements,
        "rejected": rejected,
        "free_spans": [],
    }


def canonical_payload(spec: SegmentSpec, result: AllocResult) -> dict:
    """引擎结果 -> 主图载荷（先序列化为三文件再解析，强制与包同口径）。"""
    return payload_from_files(result_to_files(spec, result))


def write_package_dir(spec: SegmentSpec, result: AllocResult, out_dir: str | Path) -> Path:
    """写出为目录（目录内恰好三文件）。返回目录路径。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in result_to_files(spec, result).items():
        (out / name).write_text(text + "\n", encoding="utf-8")
    return out


def write_package_zip(spec: SegmentSpec, result: AllocResult, out_zip: str | Path) -> Path:
    """写出为 zip 压缩包（包内顶层恰好三文件，不带目录前缀）。返回 zip 路径。"""
    path = Path(out_zip)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, text in result_to_files(spec, result).items():
            zf.writestr(name, text + "\n")
    return path


def write_package(spec: SegmentSpec, result: AllocResult, out_path: str | Path) -> Path:
    """按扩展名分流：.zip 写压缩包，否则写目录。"""
    if str(out_path).lower().endswith(".zip"):
        return write_package_zip(spec, result, out_path)
    return write_package_dir(spec, result, out_path)
