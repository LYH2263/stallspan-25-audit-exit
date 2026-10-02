"""开间结果包：写出、校验、编排的口径测试。"""
import ast
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.models import AllocationRun, Pillar
from app.services import bay_package_writer, export_bay_package
from app.services.allocate_service import compute_allocation_result
from app.services.bay_package_validator import (
    EXIT_MISSING_FILE,
    EXIT_OK,
    EXIT_PILLAR_OVERLAP,
    validate_package,
)
from app.services.bay_package_writer import PACKAGE_FILENAMES, write_package
from app.services.first_fit_engine import REASON_NO_CONTIGUOUS_SPAN
from app.services.seed import seed_if_empty

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed_if_empty(session)
    yield session
    session.close()


def run_count(db):
    return db.scalar(select(func.count()).select_from(AllocationRun)) or 0


def read_pkg(path):
    return {name: json.loads((Path(path) / name).read_text(encoding="utf-8")) for name in PACKAGE_FILENAMES}


# ---- 写出物：固定三文件，口径字段齐全 ----

def test_writer_emits_exact_three_files_with_required_fields(db, tmp_path):
    out = tmp_path / "pkg"
    assert export_bay_package.run_export(db, 1, str(out)) == EXIT_OK
    assert sorted(p.name for p in out.iterdir()) == sorted(PACKAGE_FILENAMES)
    pkg = read_pkg(out)
    assert pkg["manifest.json"]["segment"]["width_m"] == 30.0          # 清单含街宽
    assert {p["position_m"] for p in pkg["manifest.json"]["pillars"]} == {10.0, 20.0}  # 与柱心
    first = pkg["placements.json"][0]
    assert {"start_m", "end_m", "vendor_name"} <= first.keys()          # 放置含起止与摊名
    assert pkg["rejected.json"][0]["reason_code"] == REASON_NO_CONTIGUOUS_SPAN  # 拒绝含拒因码


def test_zip_package_validates_green(db, tmp_path):
    out = tmp_path / "pkg.zip"
    assert export_bay_package.run_export(db, 1, str(out)) == EXIT_OK
    assert validate_package(out).ok


def test_rejected_file_matches_unfit_list(db, tmp_path):
    out = tmp_path / "pkg"
    export_bay_package.run_export(db, 1, str(out))
    rejected = read_pkg(out)["rejected.json"]
    assert [r["vendor_name"] for r in rejected] == ["巨型舞台车"]        # 与放不下名单同一口径


# ---- 三项硬判：分码、不互判、非 0 结束 ----

def test_missing_placements_and_pillar_overlap_have_distinct_codes():
    missing = validate_package(FIXTURES / "bad_missing_placements")
    overlap = validate_package(FIXTURES / "bad_pillar_overlap")
    assert missing.exit_code == EXIT_MISSING_FILE
    assert overlap.exit_code == EXIT_PILLAR_OVERLAP
    assert missing.exit_code != overlap.exit_code                      # 必须分码
    msg_missing = " ".join(f.message for f in missing.failures)
    msg_overlap = " ".join(f.message for f in overlap.failures)
    assert "缺放置文件" in msg_missing and "压柱" not in msg_missing     # 缺文件不得写成压柱
    assert "压柱" in msg_overlap and "缺" not in msg_overlap             # 压柱不得判成缺文件


def test_validator_cli_nonzero_on_bad_packages():
    env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
    for fixture, code in [("bad_missing_placements", EXIT_MISSING_FILE),
                          ("bad_pillar_overlap", EXIT_PILLAR_OVERLAP)]:
        proc = subprocess.run(
            [sys.executable, "-m", "app.services.bay_package_validator", str(FIXTURES / fixture)],
            cwd=BACKEND_DIR, env=env, capture_output=True, text=True)
        assert proc.returncode == code and proc.returncode != 0        # 禁止只打日志仍当成功


def test_validator_reads_only_the_three_files(db, tmp_path):
    out = tmp_path / "pkg"
    export_bay_package.run_export(db, 1, str(out))
    (out / "extra_junk.json").write_text("{}", encoding="utf-8")       # 校验只读三文件
    (out / "notes.txt").write_text("junk", encoding="utf-8")
    assert validate_package(out).ok


def test_writer_does_not_call_validator():
    src = inspect.getsource(bay_package_writer)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any("validator" in a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert "validator" not in (node.module or "")
    assert "validate_package(" not in src                              # 写出不调用校验


# ---- 编排：绿了才落库，坏包不改写已成功运行 ----

def test_bad_package_keeps_successful_runs_untouched(db, tmp_path, monkeypatch):
    assert export_bay_package.run_export(db, 1, str(tmp_path / "good")) == EXIT_OK
    assert run_count(db) == 1
    last_json = db.scalars(select(AllocationRun).order_by(AllocationRun.id.desc())).first().result_json

    def broken_writer(result, out_path):                              # 模拟写出侧故障：漏放置文件
        out = Path(out_path)
        out.mkdir(parents=True, exist_ok=True)
        files = bay_package_writer.build_package_files(result)
        (out / "manifest.json").write_text(files["manifest.json"], encoding="utf-8")
        (out / "rejected.json").write_text(files["rejected.json"], encoding="utf-8")

    monkeypatch.setattr(export_bay_package, "write_package", broken_writer)
    code = export_bay_package.run_export(db, 1, str(tmp_path / "bad"))
    assert code == EXIT_MISSING_FILE and code != 0
    assert run_count(db) == 1                                          # 行数不增
    assert db.scalars(select(AllocationRun).order_by(AllocationRun.id.desc())).first().result_json == last_json


def test_green_export_matches_main_map_when_pillars_unchanged(db, tmp_path):
    out = tmp_path / "pkg"
    assert export_bay_package.run_export(db, 1, str(out)) == EXIT_OK
    pkg = read_pkg(out)
    latest = db.scalars(select(AllocationRun).order_by(AllocationRun.id.desc())).first()
    on_map = json.loads(latest.result_json)                            # 主图/放不下读的就是这条
    assert pkg["placements.json"] == on_map["placements"]
    assert pkg["rejected.json"] == on_map["rejected"]
    assert pkg["placements.json"] == compute_allocation_result(db, 1)["placements"]


def test_reexport_after_pillar_change_uses_new_pillars(db, tmp_path):
    out_a = tmp_path / "a"
    export_bay_package.run_export(db, 1, str(out_a))
    pillar = db.scalars(select(Pillar).where(Pillar.position_m == 10.0)).one()
    pillar.position_m = 12.0                                           # 改柱
    db.commit()
    out_b = tmp_path / "b"
    assert export_bay_package.run_export(db, 1, str(out_b)) == EXIT_OK
    pkg_a, pkg_b = read_pkg(out_a), read_pkg(out_b)
    centers_b = {p["position_m"] for p in pkg_b["manifest.json"]["pillars"]}
    assert centers_b == {12.0, 20.0}                                   # 按新柱写出，不吃旧柱缓存
    assert pkg_b["manifest.json"]["pillars"] != pkg_a["manifest.json"]["pillars"]
    assert validate_package(out_b).ok


# ---- 进程级：种子库跑通以 0 结束并打印口径 ----

def test_cli_seed_run_exit0_prints_segment_and_vendor(tmp_path):
    env = {**os.environ,
           "DATABASE_URL": f"sqlite:///{tmp_path}/cli.db",
           "PYTHONPATH": str(BACKEND_DIR)}
    proc = subprocess.run(
        [sys.executable, "-m", "app.services.export_bay_package", "--out", str(tmp_path / "pkg")],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "东街段" in proc.stdout and "阿强烧烤" in proc.stdout
    assert validate_package(tmp_path / "pkg").ok                       # 主图色块/放不下与三项不打架
