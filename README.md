# StallSpan 市集摊档开间

沿街段一维 First-Fit 开间分配，挡柱不可被摊位跨越，输出分配图与放不下清单。

技术栈：Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL / Vue 3 / TypeScript / Vite

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4700 |
| API | http://localhost:9700 |
| API 文档 | http://localhost:9700/docs |
| Postgres | localhost:5448 |

健康检查：`GET http://localhost:9700/api/health`

## 使用说明

1. 在「集日」「街段」确认开市日与可用宽度。
2. 在「摊主」「挡柱」维护需求宽度与障碍位置。
3. 打开「分配图」执行一维开间分配。
4. 在「放不下」查看无法安置的摊位。

## 开发与测试

```bash
docker compose exec api pytest -q
```

## 开间结果包

每次开间都按同一套口径产出固定三文件的结果包（目录或 `.zip`），与主图色块、放不下名单同源：

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 清单：街宽与柱心（`position_m` / `thickness_m`） |
| `placements.json` | 放置：起点、终点、宽度与摊名 |
| `rejected.json` | 拒绝：放不下与拒因码（`E_NO_FIT_SPAN`） |

写出与校验严格分离：**写出不调用校验；校验只读这三文件**。校验做三项硬判（清单合法、放置不压柱/不出界/不重叠、拒绝项确实放不下），任一破即以非 0 结束，且不写库。缺放置文件（`E_PLACEMENTS_MISSING`）与色块压柱（`E_STALL_OVER_PILLAR`）分码、互不代判。校验不过时库内已成功运行的行数不增。

```bash
# 重新读柱位 → 开间 → 写包 → 只读校验 → 绿了才落库（改柱后自动按新柱，不吃旧缓存）
python -m app.services.package_orchestrator export --segment-id 1 --out ./pkg        # 目录
python -m app.services.package_orchestrator export --segment-id 1 --out ./pkg.zip    # 压缩包
# 只读校验已有包：绿 0 / 废 1
python -m app.services.package_orchestrator verify --path ./pkg
```

`tests/fixtures/` 内附两份离线坏包：`bad_missing_placements.zip`（缺放置文件）、`bad_over_pillar.zip`（色块压柱），可用 `python tests/fixtures/build_bad_packages.py` 重新生成。
