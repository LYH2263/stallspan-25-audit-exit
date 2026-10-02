"""离线坏包生成脚本（可复现）：产出两份固定坏包，供校验/编排测试与人工核对。

  bad_missing_placements.zip —— 缺 placements.json，只放清单与拒绝
  bad_over_pillar.zip        —— 三文件齐，但色块压住柱心

运行：python tests/fixtures/build_bad_packages.py
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

HERE = Path(__file__).parent

MANIFEST = {
    "segment": {"id": 1, "name": "坏包测试段", "width_m": 30.0},
    "pillars": [
        {"position_m": 10.0, "thickness_m": 0.5, "label": "灯柱A"},
        {"position_m": 20.0, "thickness_m": 0.5, "label": "灯柱B"},
    ],
}

# 坏包一：故意不放 placements.json
REJECTED_EMPTY: list = []


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_missing_placements() -> Path:
    path = HERE / "bad_missing_placements.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", _dump(MANIFEST))
        zf.writestr("rejected.json", _dump(REJECTED_EMPTY))
    return path


# 坏包二：三文件齐全，但摊 A [8,13] 横跨灯柱A（柱挡区间 9.75~10.25）
PLACEMENTS_OVER_PILLAR = [
    {"vendor_id": 1, "vendor_name": "压线摊", "start_m": 8.0, "end_m": 13.0, "width_m": 5.0},
]


def build_over_pillar() -> Path:
    path = HERE / "bad_over_pillar.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", _dump(MANIFEST))
        zf.writestr("placements.json", _dump(PLACEMENTS_OVER_PILLAR))
        zf.writestr("rejected.json", _dump(REJECTED_EMPTY))
    return path


def main() -> None:
    for p in (build_missing_placements(), build_over_pillar()):
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
