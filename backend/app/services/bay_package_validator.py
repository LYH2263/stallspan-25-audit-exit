"""开间结果包校验：只读包内三文件，三项硬判，任一项破即以非 0 结束。

三项硬判（按序短路，互不越界判读）：
1. 三文件齐全且可解析   → 退出码 2（缺放置文件就是缺放置文件，不得写成压柱）
2. 口径字段齐全         → 退出码 3（清单含街宽与柱心、放置含起止与摊名、拒绝含放不下与拒因码）
3. 色块不压柱           → 退出码 4（放置区间不得与柱心±半柱厚的阻断区间相交）

校验只读 manifest.json / placements.json / rejected.json，不查库、不算引擎。
"""
from __future__ import annotations

import argparse
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.bay_package_writer import (
    MANIFEST_NAME,
    PACKAGE_FILENAMES,
    PLACEMENTS_NAME,
    REJECTED_NAME,
)

EXIT_OK = 0
EXIT_MISSING_FILE = 2
EXIT_SCHEMA = 3
EXIT_PILLAR_OVERLAP = 4

_FILE_LABELS = {
    MANIFEST_NAME: "清单文件",
    PLACEMENTS_NAME: "放置文件",
    REJECTED_NAME: "拒绝文件",
}
_EPS = 1e-9


@dataclass
class Failure:
    code: int
    message: str


@dataclass
class ValidationReport:
    failures: list[Failure]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def exit_code(self) -> int:
        return EXIT_OK if self.ok else self.failures[0].code


def _read_three_files(pkg: Path) -> tuple[dict[str, str], list[Failure]]:
    """只取固定三文件的文本；缺哪个报哪个，不替别的判项背锅。"""
    texts: dict[str, str] = {}
    failures: list[Failure] = []
    names: set[str] = set()
    if pkg.is_dir():
        names = {p.name for p in pkg.iterdir() if p.is_file()}
        read = lambda n: (pkg / n).read_text(encoding="utf-8")
    elif pkg.is_file() and zipfile.is_zipfile(pkg):
        with zipfile.ZipFile(pkg) as zf:
            names = set(zf.namelist())
            for name in PACKAGE_FILENAMES:
                if name in names:
                    texts[name] = zf.read(name).decode("utf-8")
            read = None
    else:
        return {}, [Failure(EXIT_MISSING_FILE, f"结果包不存在或不可读：{pkg}")]
    for name in PACKAGE_FILENAMES:
        if name not in names:
            failures.append(Failure(EXIT_MISSING_FILE, f"缺{_FILE_LABELS[name]}：{name}"))
        elif read is not None:
            texts[name] = read(name)
    return texts, failures


def _parse_three(texts: dict[str, str]) -> tuple[dict[str, Any], list[Failure]]:
    docs: dict[str, Any] = {}
    failures: list[Failure] = []
    for name, text in texts.items():
        try:
            docs[name] = json.loads(text)
        except json.JSONDecodeError as exc:
            failures.append(Failure(EXIT_MISSING_FILE, f"{_FILE_LABELS[name]}非合法 JSON，视同缺失：{name}（{exc.msg}）"))
    return docs, failures


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _check_schema(docs: dict[str, Any]) -> list[Failure]:
    failures: list[Failure] = []
    manifest = docs[MANIFEST_NAME]
    segment = manifest.get("segment") if isinstance(manifest, dict) else None
    width = segment.get("width_m") if isinstance(segment, dict) else None
    if not _is_num(width) or width <= 0:
        failures.append(Failure(EXIT_SCHEMA, "清单缺街宽 segment.width_m"))
    pillars = manifest.get("pillars") if isinstance(manifest, dict) else None
    if not isinstance(pillars, list):
        failures.append(Failure(EXIT_SCHEMA, "清单缺柱心 pillars 数组"))
    else:
        for i, p in enumerate(pillars):
            if not isinstance(p, dict) or not _is_num(p.get("position_m")):
                failures.append(Failure(EXIT_SCHEMA, f"清单第 {i + 1} 根柱缺柱心 position_m"))
    placements = docs[PLACEMENTS_NAME]
    if not isinstance(placements, list):
        failures.append(Failure(EXIT_SCHEMA, "放置文件须为数组"))
    else:
        for i, p in enumerate(placements):
            ok = (
                isinstance(p, dict)
                and isinstance(p.get("vendor_name"), str) and p["vendor_name"]
                and _is_num(p.get("start_m")) and _is_num(p.get("end_m"))
                and p["end_m"] > p["start_m"]
            )
            if not ok:
                failures.append(Failure(EXIT_SCHEMA, f"放置第 {i + 1} 项缺起止或摊名"))
    rejected = docs[REJECTED_NAME]
    if not isinstance(rejected, list):
        failures.append(Failure(EXIT_SCHEMA, "拒绝文件须为数组"))
    else:
        for i, r in enumerate(rejected):
            ok = (
                isinstance(r, dict)
                and isinstance(r.get("vendor_name"), str) and r["vendor_name"]
                and isinstance(r.get("reason_code"), str) and r["reason_code"]
            )
            if not ok:
                failures.append(Failure(EXIT_SCHEMA, f"拒绝第 {i + 1} 项缺放不下摊名或拒因码"))
    return failures


def _check_pillar_overlap(docs: dict[str, Any]) -> list[Failure]:
    failures: list[Failure] = []
    for pillar in docs[MANIFEST_NAME]["pillars"]:
        center = pillar["position_m"]
        half = (pillar.get("thickness_m") if _is_num(pillar.get("thickness_m")) else 0.4) / 2.0
        lo, hi = center - half, center + half
        for p in docs[PLACEMENTS_NAME]:
            if p["start_m"] < hi - _EPS and p["end_m"] > lo + _EPS:
                failures.append(Failure(
                    EXIT_PILLAR_OVERLAP,
                    f"色块压柱：{p['vendor_name']} [{p['start_m']}, {p['end_m']}] 压柱心 {center}",
                ))
    return failures


def validate_package(path: str | Path) -> ValidationReport:
    """校验目录或 zip 结果包；只读三文件，三项硬判按序短路。"""
    texts, failures = _read_three_files(Path(path))
    if failures:
        return ValidationReport(failures)
    docs, failures = _parse_three(texts)
    if failures:
        return ValidationReport(failures)
    failures = _check_schema(docs)
    if failures:
        return ValidationReport(failures)
    return ValidationReport(_check_pillar_overlap(docs))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="开间结果包校验（只读三文件，三项硬判）")
    ap.add_argument("package", help="结果包目录或 .zip 路径")
    args = ap.parse_args(argv)
    report = validate_package(args.package)
    if report.ok:
        print(f"三项硬判全绿：{args.package}")
        return EXIT_OK
    for f in report.failures:
        print(f"[退出码 {f.code}] {f.message}")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
