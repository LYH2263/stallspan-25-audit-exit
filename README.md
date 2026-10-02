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

把当前库内口径（与主图、放不下同源）写成固定三文件的结果包，并做三项硬判：

```bash
docker compose exec api python -m app.services.export_bay_package --out exports/bay_package
# 或写压缩包：--out exports/bay_package.zip
```

| 文件 | 口径 |
| --- | --- |
| `manifest.json` | 清单：街宽 `segment.width_m` 与柱心 `pillars[].position_m` |
| `placements.json` | 放置：每摊起止 `start_m`/`end_m` 与摊名 `vendor_name` |
| `rejected.json` | 拒绝：放不下的摊与拒因码 `reason_code` |

编排入口每次现查库内挡柱（改柱后再写出按新柱），先写包、再校验，全绿才把本次运行落库。

三项硬判（`python -m app.services.bay_package_validator <包路径>`，只读三文件）：

| 判项 | 退出码 |
| --- | --- |
| 全绿 | 0 |
| 缺文件（如缺放置文件） | 2 |
| 口径字段缺失 | 3 |
| 色块压柱 | 4 |

任一项破即以非 0 结束，且不改写库内已成功运行。离线坏包样例见 `backend/tests/fixtures/`。
