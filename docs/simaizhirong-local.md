# 思迈挚融本地验证

本 fork 首个增量是期初模拟账本；不连接券商、不执行实盘。研究能力沿用上游，期初值使用独立的 simulation 表，不写入上游累加式 portfolio API。

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

显示均价用整数十进制运算四舍五入至两位，不能反写原始总成本。历史费用保持未知；行情、市值、收益、可卖数量、模拟手续费和滑点尚未确定。模拟下单暂未开放，后续订单与成交属于下一增量。

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
uv run pytest tests/unit/server/services/test_simulation.py tests/unit/server/test_import_layering.py tests/unit/server/database/test_shared_lock_session.py tests/unit/server/database/test_portfolio_db.py tests/unit/server/app/test_portfolio.py -q
cd web
pnpm test src/pages/Simulation/__tests__
pnpm typecheck
pnpm lint
pnpm build
```

`tests/integration/test_simulation_opening.py -m integration` 验证真实事务、12 个并发请求、回滚、冲突和认证用户隔离。运行前必须将全部 `TEST_DB_*` 指向独立可丢弃数据库；现有集成 fixture 会重建该库的 public schema，不能使用开发或生产库。本次使用专用测试库完成验证，测试不调用付费模型、不读取个人文件。
