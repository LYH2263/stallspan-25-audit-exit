import json
import zipfile
from pathlib import Path

import pytest

from app.database import Base, SessionLocal, engine
from app.models.models import AllocationRun, Pillar
from app.services import package_orchestrator as orch
from app.services import package_validator as pv
from app.services.package_writer import (
    MANIFEST_NAME, PLACEMENTS_NAME, REJECTED_NAME,
)
from app.services.seed import seed_if_empty

FIXTURES = Path(__file__).parent / "fixtures"
BAD_MISSING = FIXTURES / "bad_missing_placements.zip"
BAD_PILLAR = FIXTURES / "bad_over_pillar.zip"


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    seed_if_empty(session)
    yield session
    session.close()


def _run_count(db) -> int:
    return db.query(AllocationRun).count()


# ---------- 种子库：进程 0 退出，打印东街段与阿强烧烤 ----------

def test_cli_export_seed_is_green_and_prints(db, tmp_path, capsys):
    pkg = tmp_path / "seed_pkg"
    rc = orch.main(["export", "--segment-id", "1", "--out", str(pkg)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "东街段" in out
    assert "阿强烧烤" in out
    # 三项硬判：独立校验进程视角再读一次包
    assert pv.validate_package(pkg).ok


def test_seed_payload_matches_main_map_and_rejected(db, tmp_path):
    outcome = orch.run_segment_export(db, 1, tmp_path / "pkg")
    assert outcome.ok
    payload = outcome.payload
    placed = {p["vendor_name"] for p in payload["placements"]}
    rejected = {r["vendor_name"] for r in payload["rejected"]}
    assert "阿强烧烤" in placed
    assert "巨型舞台车" in rejected
    # 绿仓主图就是刚写的 AllocationRun
    run = db.query(AllocationRun).order_by(AllocationRun.id.desc()).first()
    assert json.loads(run.result_json)["placements"] == payload["placements"]


# ---------- 未改柱：包内放置与绿仓主图一致（逐记录） ----------

def test_package_placements_equal_green_store(db, tmp_path):
    pkg = tmp_path / "pkg"
    outcome = orch.run_segment_export(db, 1, pkg)
    assert outcome.ok
    pkg_placements = json.loads((pkg / PLACEMENTS_NAME).read_text("utf-8"))
    run = db.query(AllocationRun).order_by(AllocationRun.id.desc()).first()
    store_placements = json.loads(run.result_json)["placements"]
    assert pkg_placements == store_placements


def test_zip_export_roundtrip(db, tmp_path):
    zpath = tmp_path / "pkg.zip"
    outcome = orch.run_segment_export(db, 1, zpath)
    assert outcome.ok and zpath.is_file()
    with zipfile.ZipFile(zpath) as zf:
        assert set(zf.namelist()) == {MANIFEST_NAME, PLACEMENTS_NAME, REJECTED_NAME}
    assert pv.validate_package(zpath).ok


# ---------- 两份离线坏包：分码、非 0、不增库行 ----------

def test_bad_missing_placements_code_is_distinct():
    verdict = pv.validate_package(BAD_MISSING)
    assert not verdict.ok
    codes = set(verdict.codes)
    assert pv.E_PLACEMENTS_MISSING in codes
    # 缺文件不得被误判成压柱
    assert pv.E_STALL_OVER_PILLAR not in codes


def test_bad_over_pillar_code_is_distinct():
    verdict = pv.validate_package(BAD_PILLAR)
    assert not verdict.ok
    codes = set(verdict.codes)
    assert pv.E_STALL_OVER_PILLAR in codes
    # 三文件在位，不得误判缺文件
    assert pv.E_PLACEMENTS_MISSING not in codes


def test_verify_bad_packages_nonzero_and_db_untouched(db, capsys):
    assert orch.run_segment_export(db, 1, None).ok  # 先有一条成功运行
    db.expire_all()
    rows_before = _run_count(db)
    for bad in (BAD_MISSING, BAD_PILLAR):
        assert orch.main(["verify", "--path", str(bad)]) == 1
    db.expire_all()
    assert _run_count(db) == rows_before == 1


def test_bad_verify_green_path_zero(db, tmp_path, capsys):
    pkg = tmp_path / "g"
    assert orch.run_segment_export(db, 1, pkg).ok
    assert orch.main(["verify", "--path", str(pkg)]) == 0
    assert orch.main(["verify", "--path", str(BAD_PILLAR)]) == 1


# ---------- 放不下复核：名单里其实放得下也算硬判破 ----------

def test_rejected_that_actually_fits_fails(tmp_path):
    pkg = tmp_path / "liar"
    pkg.mkdir()
    manifest = {"segment": {"id": 1, "name": "s", "width_m": 30.0},
                "pillars": [{"position_m": 10.0, "thickness_m": 0.5, "label": "柱"}]}
    rejected = [{"vendor_id": 9, "vendor_name": "其实放得下", "width_m": 3.0,
                 "cannot_fit": True, "reason": "x",
                 "reason_code": "E_NO_FIT_SPAN"}]
    (pkg / MANIFEST_NAME).write_text(json.dumps(manifest), "utf-8")
    (pkg / PLACEMENTS_NAME).write_text("[]", "utf-8")
    (pkg / REJECTED_NAME).write_text(json.dumps(rejected, ensure_ascii=False), "utf-8")
    verdict = pv.validate_package(pkg)
    assert pv.E_REJECTED_FITS in verdict.codes


# ---------- 改柱后再写出按新柱，不吃旧柱缓存 ----------

def test_change_pillar_then_reexport_uses_new_pillar(db, tmp_path):
    pkg1 = tmp_path / "before"
    o1 = orch.run_segment_export(db, 1, pkg1)
    assert o1.ok
    before_manifest = json.loads((pkg1 / MANIFEST_NAME).read_text("utf-8"))
    assert [q["position_m"] for q in before_manifest["pillars"]] == [10.0, 20.0]
    before_placements = json.loads((pkg1 / PLACEMENTS_NAME).read_text("utf-8"))

    # 把灯柱A 从 10 移到 8：中段空档起点提前，放置几何必须随之改变
    p = db.query(Pillar).filter(Pillar.position_m == 10.0).one()
    p.position_m = 8.0
    db.commit()

    pkg2 = tmp_path / "after"
    o2 = orch.run_segment_export(db, 1, pkg2)
    assert o2.ok
    after_manifest = json.loads((pkg2 / MANIFEST_NAME).read_text("utf-8"))
    after_placements = json.loads((pkg2 / PLACEMENTS_NAME).read_text("utf-8"))
    assert [q["position_m"] for q in after_manifest["pillars"]] == [8.0, 20.0]
    assert after_placements != before_placements  # 不得吃写出前旧柱缓存
    # 新柱挡区间 7.75~8.25：所有色块都不得压上，包仍须绿
    assert pv.validate_package(pkg2).ok
    for st in after_placements:
        assert not (st["start_m"] < 8.25 and st["end_m"] > 7.75)
