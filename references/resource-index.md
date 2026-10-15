# 稳定资源索引

| 位置 | 职责 | 变更方式 |
| --- | --- | --- |
| `.codex-plugin/plugin.json`、`.mcp.json` | 插件入口和本地 MCP 启动 | 核心版本 |
| `skills/career/SKILL.md` | Codex 求职流程与权限边界 | 核心版本，保持入口稳定 |
| `skills/interview-preparation/SKILL.md` | 依据现有资料进行面试诊断、项目深挖和练习复盘 | 独立隐式触发，结果复用 custom 任务 |
| `references/agent-recipes.md` | 能力发现、过滤分页、任务保存和工具缺失时的调用配方 | 参数与真实 MCP、CLI 同步 |
| `schemas/interview-task.json` | 面试准备任务的共享数据结构 | 与网页和 interview.schema 保持一致，不新增平行档案 |
| `service/app.py`、`http.py`、`mcp.py` | 统一资料、网页和工具接口 | 数据库迁移与接口兼容 |
| `service/packages.py`、`schemas/company-pack.schema.json` | 公司资源验证与版本管理 | 先升级契约再使用新字段 |
| `service/otp.py` | 可选取码来源和消费规则 | 可替换来源，保持工具接口 |
| `companies/<company-id>/` | 公司清单、指引、字段、脚本、合成测试 | 独立版本，不换 ID |
| `scripts/profile-fill.mjs`、`references/profile-fill-coverage.md` | 通用简历批量填入接口及公司能力边界 | 只传运行时本地事实，能力随公司包版本核验 |
| `scripts/browser-session.mjs` | 公司脚本的会话与授权公共接口 | 不内置个人数据或公司特例 |
| `web/` | 本地资料网页与公共目录源文件 | 不持有另一份业务数据 |
| `tests/` | 合成数据测试 | 禁止放真实求职者资料 |
| `references/` | 功能、资源、OTP、验证说明 | 按主题更新既有文件 |
| `dist/` | 可重新生成的发布物 | 不进入源码 Git |

私有目录不在仓库内：`career.db` 是当前档案与历史的唯一事实存储；`documents/` 保留原件；`packages/<id>/<version>/` 保留已经接受的公司包；资源安装锁与验收回执固定当前版本；`otp.json` 保存受系统保护的来源设置。自动化只记录结构化变化和必要局部证据。

稳定的是路径、资源 ID、字段含义和升级规则。招聘页面变化时必须更新方法、控件定位和版本，不能承诺脚本永久不变。执行开始时固定一个已接受资源版本，本次任务不跟随更新切换代码。
