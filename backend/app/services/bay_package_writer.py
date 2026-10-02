"""开间结果包打包写出：固定三文件，目录或 zip 压缩包。

三文件口径：
- manifest.json   清单：街宽 segment.width_m 与柱心 pillars[].position_m
- placements.json 放置：每摊起止 start_m/end_m 与摊名 vendor_name
- rejected.json   拒绝：放不下的摊与拒因码 reason_code

本模块只负责写出，不调用校验；校验见 bay_package_validator。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

MANIFEST_NAME = "manifest.json"
PLACEMENTS_NAME = "placements.json"
REJECTED_NAME = "rejected.json"
PACKAGE_FILENAMES = (MANIFEST_NAME, PLACEMENTS_NAME, REJECTED_NAME)


def build_package_files(result: dict) -> dict[str, str]:
    """把分配结果字典（allocate_service 口径）拆成三文件的文件名 → JSON 文本。"""
    manifest = {
        "segment": result["segment"],   # 含街宽 width_m
        "pillars": result["pillars"],   # 含柱心 position_m
    }
    return {
        MANIFEST_NAME: json.dumps(manifest, ensure_ascii=False, indent=2),
        PLACEMENTS_NAME: json.dumps(result["placements"], ensure_ascii=False, indent=2),
        REJECTED_NAME: json.dumps(result["rejected"], ensure_ascii=False, indent=2),
    }


def write_package(result: dict, out_path: str | Path) -> list[str]:
    """写出结果包。out_path 以 .zip 结尾写压缩包，否则写目录。返回写出的三文件标识。"""
    files = build_package_files(result)
    out = Path(out_path)
    if out.suffix == ".zip":
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in files.items():
                zf.writestr(name, text)
        return [f"{out}!{name}" for name in files]
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    return [str(out / name) for name in files]
