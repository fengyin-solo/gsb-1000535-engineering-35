# 地质勘探数据管理平台

面向地质勘探的钻孔编录、岩心取样、物探数据、化探分析、测绘资料与储量估算的综合数据管理后台。

前后端分离：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
数据初始化、构建与部署由一条**可重复流水线**驱动，支持种子数据迁移、跨日补录、
三方对账、失败续跑与版本回滚。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个台账页 + Dashboard 概览/偏离清单
│   └── nginx.conf            生产镜像用 nginx（静态产物 + /api 反代）
├── backend/                  FastAPI（Python） 后端
│   ├── app/modules.py        业务模块台账注册表（18 个模块的统一口径）
│   ├── app/pipeline/         可重复流水线
│   │   ├── migrations.py     种子数据迁移（有序、版本化、只登记一次）
│   │   ├── backfill.py       跨日补录样例（按业务时区跨午夜）
│   │   ├── reconcile.py      汇总页/模块台账/偏离清单三方对账与汇总重算
│   │   ├── build.py          前端构建与带批次号的构建清单
│   │   ├── deps.py           部署闸门：缓存/队列依赖逐项探测
│   │   ├── state.py          检查点、不可变快照、发布指针（原子写+文件锁）
│   │   └── runner.py         编排器（幂等/重试/续跑/回滚 CLI）
│   ├── app/routers/          各业务模块接口（路由工厂统一生成）+ /api/pipeline
│   ├── entrypoint.sh         部署入口：流水线未成功发布则不启动服务
│   └── tests/                流水线与 HTTP 行为测试（pytest）
├── Makefile                  pipeline / deploy / gate / status / rollback
├── docker-compose.yml        生产编排（含就绪探针、状态卷、依赖闸门）
└── docker-compose.dev.yml    本地开发编排（dev server 热更新）
```

## 可重复流水线

固定阶段顺序，全部确定性、可重放：

```
migrate → backfill → reconcile → verify → build → gate → release
```

| 阶段 | 职责 |
| --- | --- |
| `migrate` | 种子数据迁移：给内置示例数据补批次血缘；迁移版本在 `migrations/applied.json` 只登记一次 |
| `backfill` | 按业务时区生成**跨日补录**样例：前一业务日 23:50 发生、当日 00:10 补录 |
| `reconcile` | 核对**汇总页 / 各模块台账 / 偏离清单**为同一批次，并把结论同时写到三处，统计由快照重算 |
| `verify` | 发布前复核：重新读快照，校验三处批次号一致、卡片合计能由台账重算 |
| `build` | 前端构建，产物带 `build-manifest.json`（批次号 + 文件指纹） |
| `gate` | 部署闸门：缓存/队列逐项探测、核对当前批次构建清单，不可用即阻断并枚举依赖 |
| `release` | 写入不可变快照并原子切换发布指针；未成功阶段绝不推进指针 |

### 关键保障

- **幂等**：业务日期决定固定批次号（`batch-YYYY-MM-DD`）。重复执行不重复插数据、
  不产生第二个发布；同批次快照内容必须字节一致，否则拒绝覆盖。
- **检查点续跑**：连接断开/进程复位后再次执行，已成功阶段不重放，从 checkpoint
  的下一阶段继续提交；瞬时错误按 `PIPELINE_RETRY` 退避重试。
- **未成功不就绪**：任何阶段失败，批次停在 `failed`，release 指针不动，
  `/api/health` 的 `ready=false`，部署入口直接退出、不启动服务。
- **版本回滚**：`rollback <批次号>` 校验目标快照存在且为成功发布，再原子切回指针。
- **种子数据迁移**：迁移有序、版本化，只应用未登记的版本；既有示例数据固定
  保留在原批次 `seed-v0001`，不会被新批次改写。
- **环境转换以业务时区为准**：跨日切日只认 `BUSINESS_TZ`（默认 `Asia/Shanghai`），
  与容器/宿主时区无关；时间戳带 UTC 偏移，可追溯。

### 常用命令

```bash
make install          # 安装前后端依赖

make pipeline         # 仅数据流水线：迁移→补录→对账→复核→闸门→发布（不构建前端）
make deploy           # 完整流水线：先 npm build，再跑全部阶段
make build            # 只构建前端（产出带批次清单的 dist）
make gate             # 单独执行部署闸门（枚举缓存/队列可用性）
make status           # 查看当前批次检查点与发布指针
make test             # 后端 pytest + 前端类型检查与构建

# 固定业务日期重跑（用于演练/补算）：
cd backend && .venv/bin/python -m app.pipeline.runner run --as-of 2026-09-30

# 连接断开演练（在 reconcile 注入瞬时故障，复位后重跑即从检查点继续）：
.venv/bin/python -m app.pipeline.runner run --no-build --fail-stage reconcile
.venv/bin/python -m app.pipeline.runner run --no-build      # 复位后续跑

# 版本回滚：
make rollback BATCH=batch-2026-09-30
```

### 配置（环境变量，见 `.env.example`）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `BUSINESS_TZ` | `Asia/Shanghai` | 业务时区，跨日补录与“今日”唯一准绳 |
| `PIPELINE_STATE_DIR` | `var/pipeline` | 检查点、快照、发布指针、迁移登记目录（部署卷） |
| `REQUIRED_CACHE` | `file:var/cache` | 必需缓存依赖，逗号分隔；可填 `redis://host:port` |
| `REQUIRED_QUEUE` | `file:var/queue` | 必需队列依赖；可填 `amqp://host:port`、`fail:名称`（演练阻断） |
| `PIPELINE_RETRY` / `PIPELINE_RETRY_BACKOFF` | `3` / `0.2` | 瞬时故障重试次数与退避基准秒 |
| `APP_ENV` / `APP_VERSION` | `local` / `1.0.0` | 运行环境与版本（记入快照与构建清单） |

缓存或队列任一不可用时，闸门返回 `blocked=true` 并逐项列出
`[{kind: cache|queue, ref, message}]`，发布被阻断。

## 启动

### 一键（推荐）

```bash
make install
make pipeline        # 初始化数据并发布当前业务批次
make backend         # 启动后端（run.sh 也会先幂等跑流水线）
make frontend        # 另开终端启动前端 dev server
```

健康检查区分“端口在听”和“数据就绪”：

```bash
curl http://127.0.0.1:8000/api/health
# {"ok":true,"listening":true,"ready":true,...,"batch_id":"batch-..."}
```

### Docker

```bash
make build
docker compose up -d --build
# backend 入口先跑流水线，闸门不过或未发布则容器退出；
# healthcheck 只在 ready=true 时放行，frontend 等 backend healthy 后才起
```

## HTTP 接口

- `GET /api/health`：就绪状态（`listening` vs `ready`）与当前批次。
- `GET /api/overview`：概览卡片、各模块计数、对账结论（`reconciliation.matched`）。
- `GET /api/<模块>`：模块台账分页，信封带当前发布 `batch_id`；支持 `?batch_id=` 过滤。
- `GET /api/<模块>/export`：导出台账（盖批次戳）。
- `GET /api/pipeline/deviations`：偏离清单（跨日补录等，与汇总页同批次）。
- `GET /api/pipeline/ledgers`：18 个模块台账的批次戳与计数，逐项 `batch_consistent`。
- `GET /api/pipeline/status`：检查点、阶段尝试次数、迁移登记、发布指针。
- `GET /api/pipeline/releases`：发布与回滚历史。

## 业务模块

| 模块 | 目录 | 业务对象 |
| --- | --- | --- |
| 钻孔编录 | `borehole` | 钻孔 |
| 岩心管理 | `core` | 岩心样本 |
| 地层划分 | `stratigraphy` | 地层单元 |
| 地球物理 | `geophysics` | 物探测线 |
| 化探分析 | `geochem` | 化探样品 |
| 化验数据 | `assay` | 化验结果 |
| 地质填图 | `mapping` | 填图单元 |
| 测绘控制 | `survey_point` | 控制点 |
| 钻探日志 | `drilling_log` | 钻探记录 |
| 储量估算 | `reserve` | 矿体块段 |
| 样品登记 | `sample_registry` | 送检样品 |
| 勘探设备 | `equipment` | 勘探仪器 |
| 水文地质 | `hydro` | 水文观测点 |
| 剖面编录 | `section` | 实测剖面 |
| 地质报告 | `geological_report` | 勘探报告 |
| 遥感解译 | `remote` | 遥感数据 |
| 矿产评价 | `mineral` | 矿化线索 |
| 环境地质 | `environmental` | 环境调查点 |

## 约定

- 模块口径集中在 `app/modules.py`：新增模块只改注册表，service/router 由工厂生成。
- 列表接口统一返回 `{ items, total, page, size, batch_id }`；动作接口返回 `{ ok, message }`。
- 状态流转只在 `app/services` 里改，路由层不做业务判断。
- 流水线状态目录是部署卷，不进 git（`backend/var/`）。
