# 思迈挚融本地验证

本 fork 已实现期初模拟账本与建议/确认/订单/成交审计；不连接券商、不执行实盘。研究能力沿用上游，期初值使用独立的 simulation 表，不写入上游累加式 portfolio API。

## 本次环境

基线为 `90ce1246c44cdd3e6a3d223b571ea50e57feab91`，二开分支 `codex/simaizhirong-local-validation`。使用 Python 3.14.7、uv、项目声明的 pnpm 12.6.0 与锁定依赖。

| 服务 | 本地地址 / 标识 |
| --- | --- |
| 前端 | `http://127.0.0.1:15173/simulation` |
| 后端健康检查 | `http://127.0.0.1:18000/health` |
| PostgreSQL | `127.0.0.1:15432`，容器 `simaizhirong-f1-postgres` |
| Redis | `127.0.0.1:16379`，容器 `simaizhirong-f1-redis` |
| 集成测试数据库 | `simaizhirong_f1_test`，独立于开发库 |

当前实例使用 OSS 单用户模式，只监听回环地址，不是外部用户的身份隔离方案。上游内置 MCP、行情预热、新闻和市场洞察在本地覆盖配置中关闭；未启动工作区沙箱或启用通知。

本次会话生成的覆盖配置和进程启动器均被 Git 忽略：`config.simaizhirong.yaml`、`agent_config.simaizhirong.yaml`、`tmp/f1/run.py` 与 `tmp/f1/runtime-env.json`。它们是本机验证环境，不是可复制的生产部署。运行时文件包含本地数据库凭据和 BYOK 加密密钥，权限为 600，不能输出、提交或丢失后假定已有 API Key 仍可解密。

该环境已生成时，可以从仓库根目录执行 `.venv/bin/python tmp/f1/run.py backend` 或 `.venv/bin/python tmp/f1/run.py web`；先检查对应 PID/端口，避免重复启动。迁移使用 `.venv/bin/python tmp/f1/run.py migrate`。新机器须先按上游 README 配置独立依赖，再明确注入自己的环境变量；不要复制本机运行时文件。

## 期初接口

`GET /api/v1/users/me/simulation/opening` 返回当前认证用户的快照，未初始化时为 `{"snapshot": null}`。`POST` 同一路径接受：

- `opening_date`、`initial_cash`、`cost_includes_fees` 和 `holdings`。
- 金额为非负十进制字符串；现金最多两位小数，精确总成本最多八位。数量为正整数，证券代码为六位字符串，交易所为 SH/SZ/BJ。
- 每项持仓有名称、数量、总成本和买入日期。批次分配未知时，保留 `acquisition_dates` 并设置 `batch_details_known=false`，不把汇总拆成臆造批次。

首次提交返回 201，相同业务内容重试返回 200 和同一账户；行顺序或金额尾随零不影响重试身份。不同期初内容返回 409 / `OPENING_CONFLICT`，错误输入返回 422。用户 ID 只取认证上下文；请求不得指定其他用户。

迁移 067 新建期初账户与独立现金事件。账户与现金事件在同一事务保存，使用 PostgreSQL 用户锁和唯一约束防止并发累加。迁移不回填旧 portfolio；downgrade 会删除新增账本，未在开发数据上执行降级。

显示均价用整数十进制运算四舍五入至两位，不能反写原始总成本。历史费用保持未知；F3 已实现逐笔模拟确认、资金与可卖数量校验、版本化模拟费用/滑点及成交后已实现盈亏。真实行情尚未接通，市值与浮动盈亏保持待核实；确认的订单等待有效行情，详见下方模拟订单说明。

## 私有文件载入

```bash
# 只校验，默认不写入，也不输出私人金额。
PYTHON_DOTENV_DISABLED=1 uv run python -m scripts.utils.import_simulation_opening /absolute/private/opening.json

# 仅在核对数据和当前本地用户后载入；同一快照重试不重复记账。
PYTHON_DOTENV_DISABLED=1 uv run python -m scripts.utils.import_simulation_opening /absolute/private/opening.json --apply
```

适配器接受版本 1 的 CNY `opening_snapshot` / `simulation` 文件，验证现金与持仓汇总、已知批次和日期；文件上限 1 MiB。已知不含费用批次按已报告数量和价格保存，缺失分配的持仓保留汇总与所有已知日期。异常停止处理，CLI 不回显输入或服务端响应正文。导入仅允许回环 HTTP 地址，不跟随重定向、不使用环境代理。

## 模型与验证

本地用户通过既有 BYOK 接口保存加密密钥；自定义提供商 `volcengine-ark` 使用 OpenAI 兼容 Chat Completions，模型名 `doubao-2.1-lite` 对应实际查询到的 `doubao-seed-2-1-lite-260915`。主模型、Flash、压缩、摘要和 fetch 槽位使用同一模型，无未配置的回退。当前设置关闭思考、单次输出上限 1,024 token；这不是月度消费限额。实际账号额度与单价仍需核验。

已验证真实 API 最小调用，以及产品 `LLMService.complete` 使用 BYOK 的调用；没有向模型发送真实持仓。不要把静态模型列表或一次成功调用写成生产可用。

相关检查：

```bash
uv run ruff check src/ libs/market-protocol/
uv run pytest tests/unit/server/services/test_simulation.py tests/unit/server/services/test_simulation_orders.py tests/unit/server/test_import_layering.py tests/unit/server/database/test_shared_lock_session.py tests/unit/server/database/test_portfolio_db.py tests/unit/server/app/test_portfolio.py -q
cd web
pnpm test src/pages/Simulation/__tests__
pnpm typecheck
pnpm lint
pnpm build
```

`tests/integration/test_simulation_opening.py -m integration` 验证真实事务、12 个并发请求、回滚、冲突和认证用户隔离。运行前必须将全部 `TEST_DB_*` 指向独立可丢弃数据库；现有集成 fixture 会重建该库的 public schema，不能使用开发或生产库。本次使用专用测试库完成验证，测试不调用付费模型、不读取个人文件。

## 模拟订单与当前账本

迁移 068 增加账户当前状态、建议、订单、成交和审计表，不改写迁移 067 或原始期初快照。账户行锁与唯一约束保证多进程下的幂等；成交、现金、仓位、成本和审计在同一事务中更新。金额与价格从浏览器以十进制字符串传入，不接受浮点数金额。

| 接口（均在 `/api/v1/users/me/simulation` 下） | 行为 |
| --- | --- |
| `GET /ledger` | 当前现金、预留、保守可卖数量、精确成本；全部待处理建议/订单及各最近 100 条终态记录、最近 100 条成交；读取时释放到期订单预留 |
| `POST /suggestions` | 保存带请求 UUID、账本版本、理由、反证、限价和时区期限的手动建议；相同请求重试不重复生成 |
| `POST /suggestions/{id}/confirm` | 明确确认建议版本、账本版本和模拟用途；验证资金/数量后只建立订单和预留 |
| `POST /suggestions/{id}/cancel`、`POST /orders/{id}/cancel` | 结束未确认建议或待执行订单；取消订单释放预留，不记成交 |
| `GET /events?limit=50&before={cursor}` | 通过稳定游标向前翻页；现金事件可与期初现金对账 |
| `GET /suggestions/{id}`、`GET /orders/{id}`、`GET /fills/{id}` | 按当前认证用户回查原始记录，超过近期 100 条仍可追溯 |

所有响应 `Cache-Control: no-store`。建议绑定账本版本；其他确认、取消、到期或成交改变资源时，旧卡片失效，需重新生成后再确认。重复确认同一建议回读同一订单，包括已取消/到期的订单，不重新创建。

`sim-fees-v1` 是实验参数：佣金 0.03%、最低 5 元，卖出模拟税费 0.05%、双向模拟过户费 0.001%、不利滑点 5 基点。卡片展示并冻结参数，不表示券商实际费率。限价买入预留包含费用；卖出按移动加权平均法摊销成本，最后一笔卖出精确清零。已实现盈亏只覆盖起算后的模拟成交，历史未知费用不补造。未知批次分配按最晚已知日期保守解锁。

当前规则仅支持沪深主板普通 A 股；其他板块/品种等待规则核验。模拟为整单成交，不模拟盘口排队、部分成交、集合竞价或公司行动。可信适配器须提供证券/交易日历核验、停牌状态、价格边界、来源及证据。报价和执行时刻都须处于北京时间连续竞价时段；报价晚于确认、同日、非未来且不超过 60 秒；不利滑点后的成交价仍须满足限价和已核验价格边界。

**R2 行情适配器尚未接通**：没有浏览器报价注入接口，也未启用自动成交任务。真实账户已保存的期初信息没有被试验下单修改；产品中确认的订单保持等待有效行情，直到取消或到期，不拿历史收盘价补成交。不展示未经核验的市值/浮动盈亏/总资产。

验证实例 `http://127.0.0.1:15174/simulation` / 后端 `18001` 专门绑定虚构 `simaizhirong-smoke` 用户；用户入口仍是 15173 / 18000。内部测试行情默认禁用，显式启用也仅允许隔离测试用户名。虚构 HTTP/PostgreSQL 验证完成买卖、12 次并发确认/成交、取消、T+1、精确成本与现金对账；前端确认弹窗、待执行/取消、审计回查和 390px 无整体横向溢出已实际验收。`tests/integration/test_simulation_orders.py` 使用真实数据库及受控测试时间，包含竞争预留、终态、错误行情、隔离、认证、审计游标与事务回滚。未运行完整测试套件、真实行情、研究沙箱或通知链路。

本机新进程的运行时环境显式配置 `PYTHONPATH` 为仓库根、`src` 与 `libs/market-protocol`，以便解析本地源码包；此路径配置保留在被忽略的本机启动环境中。

F3 最终验证：112 项相关后端单测、24 项真实 PostgreSQL 集成测试、13 项前端测试通过；Ruff、TypeScript 与构建通过，关键路径 337.6 kB gz，ESLint 0 错误/421 个既有警告。独立审查发现较早待执行订单会被 100 条历史上限隐藏；已在真实测试库复现并修复，超过 100 条更新历史记录后仍能查看/取消原订单、释放预留，待确认建议同样始终可见。审查还纠正了上方过时的 F2 范围说明。全部成交验收只使用虚构身份和测试报价，未验证供应商行情、通知任务、外部发布身份隔离、完整套件或远端 CI。
