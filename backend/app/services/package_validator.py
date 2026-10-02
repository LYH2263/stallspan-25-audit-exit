"""开间结果包校验：只读固定三文件，做三项硬判。

三项硬判（任一破即失败、进程非 0，禁止只打日志仍当成功）：
  1. 清单 manifest.json   —— 文件在位、街宽与柱心合法
  2. 放置 placements.json —— 文件在位、起止合法、色块不压柱、不出界、互不重叠
  3. 拒绝 rejected.json   —— 文件在位、每项带拒因码、且按清单复核确实放不下

分码铁律：缺放置文件 = E_PLACEMENTS_MISSING；色块压柱 = E_STALL_OVER_PILLAR。
两者互不代判——放置文件缺失时绝不产出压柱错误，反之亦然。

本模块只读包，不写任何文件，也不依赖数据库。
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.services.first_fit_engine import REASON_NO_FIT_SPAN
from app.services.package_writer import (
    MANIFEST_NAME,
    PLACEMENTS_NAME,
    REJECTED_NAME,
)

EPS = 1e-6

# ---- 错误码：三文件缺失各自分码，压柱独立成码，绝不混判 ----
E_PACKAGE_UNREADABLE = "E_PACKAGE_UNREADABLE"
E_MANIFEST_MISSING = "E_MANIFEST_MISSING"
E_PLACEMENTS_MISSING = "E_PLACEMENTS_MISSING"
E_REJECTED_MISSING = "E_REJECTED_MISSING"
E_MANIFEST_BAD = "E_MANIFEST_BAD"
E_PLACEMENTS_BAD = "E_PLACEMENTS_BAD"
E_REJECTED_BAD = "E_REJECTED_BAD"
E_MANIFEST_GEOMETRY = "E_MANIFEST_GEOMETRY"
E_STALL_OUT_OF_BOUNDS = "E_STALL_OUT_OF_BOUNDS"
E_STALL_RANGE_INVALID = "E_STALL_RANGE_INVALID"
E_STALL_OVER_PILLAR = "E_STALL_OVER_PILLAR"
E_STALL_OVERLAP = "E_STALL_OVERLAP"
E_REJECTED_FITS = "E_REJECTED_FITS"
E_REJECTED_REASON_CODE = "E_REJECTED_REASON_CODE"
E_VENDOR_LIST_CONFLICT = "E_VENDOR_LIST_CONFLICT"

KNOWN_REASON_CODES = {REASON_NO_FIT_SPAN}


@dataclass
class ValidationError:
    code: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return [e.code for e in self.errors]


def load_three_files(path: Path) -> tuple[dict[str, bytes | None], list[ValidationError]]:
    """只读取出三文件原文；打不开的包与缺失文件分开报错。"""
    errs: list[ValidationError] = []
    files: dict[str, bytes | None] = {}
    if path.is_dir():
        for name in (MANIFEST_NAME, PLACEMENTS_NAME, REJECTED_NAME):
            fp = path / name
            files[name] = fp.read_bytes() if fp.is_file() else None
    elif path.is_file() and str(path).lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = set(zf.namelist())
                for name in (MANIFEST_NAME, PLACEMENTS_NAME, REJECTED_NAME):
                    files[name] = zf.read(name) if name in names else None
        except (zipfile.BadZipFile, OSError) as exc:
            errs.append(ValidationError(E_PACKAGE_UNREADABLE, f"压缩包无法读取: {exc}",
                                        {"path": str(path)}))
            return {}, errs
    else:
        errs.append(ValidationError(E_PACKAGE_UNREADABLE, "既非目录也非 .zip 包",
                                    {"path": str(path)}))
        return {}, errs
    return files, errs


def _parse(name: str, raw: bytes | None, missing_code: str, bad_code: str,
           errs: list[ValidationError]):
    """单文件 存在性 + JSON 可解析。缺失与内容坏是两个码。"""
    if raw is None:
        errs.append(ValidationError(missing_code, f"缺少文件 {name}", {"file": name}))
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errs.append(ValidationError(bad_code, f"{name} 不是合法 JSON: {exc}", {"file": name}))
        return None


def _pillar_interval(p: dict) -> tuple[float, float]:
    half = float(p.get("thickness_m", 0.4)) / 2.0
    return float(p["position_m"]) - half, float(p["position_m"]) + half


def validate_package(path: str | Path) -> ValidationResult:
    path = Path(path)
    files, errs = load_three_files(path)
    if any(e.code == E_PACKAGE_UNREADABLE for e in errs):
        return ValidationResult(False, errs)

    # ---- 三项硬判之一：清单 ----
    manifest_raw = _parse(MANIFEST_NAME, files.get(MANIFEST_NAME),
                          E_MANIFEST_MISSING, E_MANIFEST_BAD, errs)
    width = None
    pillars: list[dict] = []
    if manifest_raw is not None:
        try:
            seg = manifest_raw["segment"]
            width = float(seg["width_m"])
            pillars = list(manifest_raw["pillars"])
            if not (width > 0):
                raise ValueError("width_m 必须为正")
            for p in pillars:
                pos, thick = float(p["position_m"]), float(p.get("thickness_m", 0.4))
                if thick < 0 or not (-EPS <= pos <= width + EPS):
                    raise ValueError(f"柱心越界: {p}")
        except (KeyError, TypeError, ValueError) as exc:
            errs.append(ValidationError(E_MANIFEST_GEOMETRY, f"清单街宽/柱心非法: {exc}"))
            width, pillars = None, []

    # ---- 三项硬判之二：放置（文件缺失时只记 MISSING，绝不转判压柱）----
    placements_raw = _parse(PLACEMENTS_NAME, files.get(PLACEMENTS_NAME),
                            E_PLACEMENTS_MISSING, E_PLACEMENTS_BAD, errs)
    placements: list[dict] = []
    if placements_raw is not None:
        if not isinstance(placements_raw, list):
            errs.append(ValidationError(E_PLACEMENTS_BAD, "placements 必须是数组"))
        else:
            try:
                placements = [
                    {"vendor_id": int(p["vendor_id"]), "vendor_name": str(p["vendor_name"]),
                     "start_m": float(p["start_m"]), "end_m": float(p["end_m"]),
                     "width_m": float(p["width_m"])}
                    for p in placements_raw
                ]
            except (KeyError, TypeError, ValueError) as exc:
                errs.append(ValidationError(E_PLACEMENTS_BAD, f"放置记录字段非法: {exc}"))
                placements = []

    placements_missing = any(e.code == E_PLACEMENTS_MISSING for e in errs)
    if width is not None and placements and not placements_missing:
        intervals: list[tuple[float, float, int]] = []
        for p in placements:
            s, e = p["start_m"], p["end_m"]
            if e <= s + EPS:
                errs.append(ValidationError(E_STALL_RANGE_INVALID,
                                            f"{p['vendor_name']} 终点必须大于起点",
                                            {"vendor": p["vendor_name"]}))
                continue
            if s < -EPS or e > width + EPS:
                errs.append(ValidationError(E_STALL_OUT_OF_BOUNDS,
                                            f"{p['vendor_name']} 色块超出街宽 0~{width}",
                                            {"vendor": p["vendor_name"], "start_m": s, "end_m": e}))
            # 色块压柱：与任一柱挡区间内部相交（相切不算）
            for pillar in pillars:
                lo, hi = _pillar_interval(pillar)
                if s < hi - EPS and e > lo + EPS:
                    errs.append(ValidationError(E_STALL_OVER_PILLAR,
                                                f"{p['vendor_name']} 色块压住柱心 {pillar['position_m']}",
                                                {"vendor": p["vendor_name"],
                                                 "pillar_position_m": pillar["position_m"],
                                                 "start_m": s, "end_m": e}))
            intervals.append((s, e, p["vendor_id"]))
        intervals.sort()
        for (s1, e1, v1), (s2, _e2, v2) in zip(intervals, intervals[1:]):
            if s2 < e1 - EPS:
                errs.append(ValidationError(E_STALL_OVERLAP,
                                            f"色块互相重叠: vendor {v1} / {v2}",
                                            {"vendor_ids": [v1, v2]}))

    # ---- 三项硬判之三：拒绝（文件缺失只记 REJECTED_MISSING）----
    rejected_raw = _parse(REJECTED_NAME, files.get(REJECTED_NAME),
                          E_REJECTED_MISSING, E_REJECTED_BAD, errs)
    rejected: list[dict] = []
    if rejected_raw is not None:
        if not isinstance(rejected_raw, list):
            errs.append(ValidationError(E_REJECTED_BAD, "rejected 必须是数组"))
        else:
            for r in rejected_raw:
                try:
                    item = {"vendor_id": int(r["vendor_id"]), "vendor_name": str(r["vendor_name"]),
                            "width_m": float(r["width_m"]), "reason_code": str(r["reason_code"])}
                    if not r.get("cannot_fit", False):
                        raise ValueError("cannot_fit 必须为 true")
                except (KeyError, TypeError, ValueError) as exc:
                    errs.append(ValidationError(E_REJECTED_BAD, f"拒绝记录字段非法: {exc}", {"raw": r}))
                    continue
                if item["reason_code"] not in KNOWN_REASON_CODES:
                    errs.append(ValidationError(E_REJECTED_REASON_CODE,
                                                f"{item['vendor_name']} 拒因码不被识别: {item['reason_code']}",
                                                {"vendor": item["vendor_name"], "code": item["reason_code"]}))
                rejected.append(item)

    placed_ids = {p["vendor_id"] for p in placements}
    for r in rejected:
        if r["vendor_id"] in placed_ids:
            errs.append(ValidationError(E_VENDOR_LIST_CONFLICT,
                                        f"{r['vendor_name']} 既在放置又在放不下名单",
                                        {"vendor": r["vendor_name"]}))

    # 放不下复核：柱挡 + 已放置共同占用后，是否真无连续空档可容下
    if width is not None and not placements_missing and rejected:
        blocked = [[max(0.0, lo), min(width, hi)]
                   for lo, hi in (_pillar_interval(p) for p in pillars) if hi > lo]
        blocked += [[p["start_m"], p["end_m"]] for p in placements]
        blocked.sort()
        free: list[list[float]] = []
        cursor = 0.0
        for lo, hi in blocked:
            if lo > cursor + EPS:
                free.append([cursor, lo])
            cursor = max(cursor, hi)
        if cursor < width - EPS:
            free.append([cursor, width])
        for r in rejected:
            if any((b - a) + EPS >= r["width_m"] for a, b in free):
                errs.append(ValidationError(E_REJECTED_FITS,
                                            f"{r['vendor_name']} 实际仍有空档可放下，不应进放不下名单",
                                            {"vendor": r["vendor_name"], "width_m": r["width_m"]}))

    return ValidationResult(not errs, errs)
