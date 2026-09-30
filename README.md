# 地质勘探数据管理平台

面向地质勘探的钻孔编录、岩心取样、物探数据、化探分析、测绘资料与储量估算的综合数据管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 钻孔编录 | `borehole` | 钻孔 | 钻孔编号、勘探区、孔口坐标 |
| 岩心管理 | `core` | 岩心样本 | 岩心编号、所属钻孔、取样深度起 |
| 地层划分 | `stratigraphy` | 地层单元 | 单元编号、钻孔编号、地层名称 |
| 地球物理 | `geophysics` | 物探测线 | 测线编号、勘探区、物探方法 |
| 化探分析 | `geochem` | 化探样品 | 样品编号、样品类型、采样点位 |
| 化验数据 | `assay` | 化验结果 | 化验编号、样品编号、元素名称 |
| 地质填图 | `mapping` | 填图单元 | 图幅编号、图幅名称、比例尺 |
| 测绘控制 | `survey_point` | 控制点 | 点号、点类型、坐标X |
| 钻探日志 | `drilling_log` | 钻探记录 | 日志编号、钻孔编号、钻进深度 |
| 储量估算 | `reserve` | 矿体块段 | 块段编号、矿体名称、面积 |
| 样品登记 | `sample_registry` | 送检样品 | 送检编号、样品名称、采样位置 |
| 勘探设备 | `equipment` | 勘探仪器 | 仪器编号、仪器名称、型号规格 |
| 水文地质 | `hydro` | 水文观测点 | 观测编号、观测类型、所在钻孔 |
| 剖面编录 | `section` | 实测剖面 | 剖面编号、剖面名称、剖面长度 |
| 地质报告 | `geological_report` | 勘探报告 | 报告编号、勘探区、报告类型 |
| 遥感解译 | `remote` | 遥感数据 | 数据编号、数据源、分辨率 |
| 矿产评价 | `mineral` | 矿化线索 | 线索编号、勘探区、矿种 |
| 环境地质 | `environmental` | 环境调查点 | 调查编号、调查区域、灾害类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 初始化 / 构建 / 部署流水线（可重复）

概览页、各模块台账、偏离清单的数据不再来自本地一次性脚本，而是由一条固定
七阶段流水线产出并发布：

```text
preflight -> migrate -> generate -> load -> reconcile -> verify -> publish
```

- **preflight**：部署前枚举并探测依赖（缓存、队列）。任一不可用即阻断发布，
  退出码 `2` 并打印每个依赖的后端地址与失败原因；
- **migrate**：种子迁移（v1 内置示例 → v2 按业务时区补登记时间戳 →
  v3 补确定性关联参考码）。迁移是 `(版本, 业务时区)` 的纯函数，可逐版本
  检查点重放，支持回滚；**既有示例数据始终按原批次 `seed-v1` 保留**；
- **generate**：按业务时区（`BUSINESS_TIMEZONE`，默认 `Asia/Shanghai`）
  为 18 个模块生成业务日样例：每模块 1 条当日样例 + 1 条**跨日补录**
  （数据日期为业务时区内前一自然日，当日才补录登记），时间戳带时区偏移；
- **load**：经队列逐模块幂等装载（作业 upsert，自然键=模块+编号）。
  重复执行不追加重复行；作业中断复位为 `pending` 后从检查点再次提交；
- **reconcile**：核对汇总页、18 个模块台账、偏离清单为**同一批次、同一
  核对号、同一校验和**，核对结果同时落到三处，并驱动汇总统计重算（缓存）；
- **verify**：发布前总校验三件套一致性、各模块校验和、缓存命中、前置阶段
  全部 `done`，随后固化不可变批次快照。任何阶段未成功都不会标记就绪；
- **publish**：校验通过才切换当前批次指针（`ready=true`）。

批次 ID 由 `种子版本|业务日期|业务时区` 哈希派生，同入参重跑得到同批次，
天然幂等。

### 命令

```bash
# 依赖探测（缓存/队列不可用返回 2）
make preflight

# 全量流水线（幂等；已成功阶段从检查点跳过）
make deploy BUSINESS_DATE=2026-09-30 BUSINESS_TIMEZONE=Asia/Shanghai

# 状态 / 重新核对并重算 / 批次回滚 / 种子版本回滚
make status
make recompute
make rollback BATCH=BATCH-20260929-xxxx
make rollback-seed VERSION=v2 BUSINESS_DATE=2026-09-30

# 后端测试（12 个场景，含失败注入、断点续跑、真实 HTTP）
make test
```

流水线产物落在 `DATA_DIR`（默认 `./var/data`，git 忽略）：
`state.json` 当前指针、`runs/` 检查点、`ledgers.json` 台账、
`summary.json` 汇总页、`deviations.json` 偏离清单、`batches/` 不可变快照、
`queue/` 作业、`cache/` 统计缓存。所有写操作都是临时文件 + 原子替换。

### 依赖后端与环境转换

| 环境 | 缓存 / 队列 | 说明 |
| --- | --- | --- |
| local（默认） | file（`DATA_DIR` 内） | 零外部依赖，作业天然落盘 |
| staging / prod | redis（`REDIS_URL`） | preflight 必须探测通过才允许发布 |

环境转换只认 `BUSINESS_TIMEZONE`，不读容器/宿主机 `TZ`；可显式设置
`CACHE_BACKEND` / `QUEUE_BACKEND` 覆盖。容器入口 `backend/entrypoint.sh`
先 `preflight` 再 `deploy`，依赖不可用时容器非零退出，不会把未成功部署
当成就绪。

```bash
# 本地（file 后端）
docker compose --profile local up --build

# 部署（Redis，依赖不健康则阻断）
docker compose -f docker-compose.yml -f docker-compose.deploy.yml \
  --profile deploy up --build -d
```

前端为多阶段镜像（vite 构建 + nginx 托管，`/api` 反代后端）。
页面新增：勘探数据概览（含批次横幅与核对号）、模块台账核对、偏离清单、
流水线（可手动触发/续跑、查看各阶段检查点）。

