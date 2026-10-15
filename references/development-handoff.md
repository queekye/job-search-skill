# 开发交接 · 0.4.1 开发预览发行

2026-10-15 增补：公共能力目录 `capability.list/describe`、`interview.schema` 和 `scripts/career-call.py` 单次 MCP 客户端已接入；四个 skills 由 `career` 路由，面试流程先读已有材料、保留事实与建议边界、通过真实回答及复测更新进度。面试复用原 `custom` 任务，网页任务区提供模板、关联资料、计划、项目卡及练习视图；`task.context.interview_context` 检查本账号引用与版本变化，不修改原资料、数据库 schema、官网状态或已有自动化。完整参数见 [调用配方](agent-recipes.md) 与 [开发契约](development-contract.md)。当前版本的源码、本地浏览器、运行包和安装新会话结果分别记录在 [验证](validation.md)，不能从接口存在推断全部验收。

2026-10-13：[个人资料与人工 / Agent 共用能力](profile-redesign.md)、[字段目标与当前实现](profile-fields-v2.md)。教育、项目、论文已有类型化编辑器、显式关联、独立来源和事务迁移；本轮增加唯一类型化意向、旧偏好整理、简历方案与 Markdown 预览导出、公司答案及本人公司决定。网页与源码 MCP 共用服务，意向和决定写入、迁移执行、草稿确认及引用复核由本人完成。数据库 schema 4、portable v3；恢复兼容 schema 2/3，导入兼容 portable v1/v2。既有安装插件没有自动升级。接口详见 [开发契约](development-contract.md)。

交接日期：2026-10-13。本文件只记录公开项目的开发状态；个人档案、备份路径、现场核对结果和凭据保存在仓库外的本人目录。本轮源码检查、私有迁移、网页与同步验收须分别记录，接口完成不能代替验收结果。

## 当前基线

- 当前发行版本为 0.4.1 开发预览，包含共用类型化资料、独立简历方案、能力目录、面试准备及 JD 证据。公开源码、运行包和 Cloudflare 目录分别发布；提交代码不等于完成三者的发布。
- 一份 `settings.preferences` 为意向权威；旧偏好行仅用于整理和历史，不与新表单双写。薪资期望、上下限、币种与税基分开，未知事实不推断。
- 简历方案保存事实 ID、顺序及独立文案，不复制个人事实；来源更新需复核，公司答案精确绑定公司与招聘范围。暂停/排除决定由本人维护并阻止自动填写推进，历史授权不成为现时授权。
- 独立自动填表已经接通网页和 `career_forms` MCP：准备输入、打开专用浏览器、填写、读回、状态、取消、恢复。无需模型或 Codex 参与浏览器执行。
- 公司包使用 `form.json` 声明输入，`inspect/fill/verify` 提供执行代码；`company-resource` Skill、模板/校验 MCP 和独立 CLI 支持制作、合成测试与导出。
- 统一一键入口先覆盖京东 0.4.0 的六项基本信息。其他公司仍按自身 guide 提供有限读取、helper 或流程指引。教育、项目、附件、官网保存和最终投递不属于京东统一入口的已验证范围。
- 推荐功能支持本人排除及恢复、职责方向反馈、已存在实际申请公司的推荐收束。推荐、本人选岗和正式申请继续分别记录。
- WebDAV 是单写者的个人快照交接，不合并两台设备的离线修改；必须先完成实际同步，不能把保存设置视为成功。

## 代码入口与数据边界

| 模块 | 入口及职责 |
| --- | --- |
| 数据与账号 | `service/app.py`、`storage.py`；一个 SQLite 库、明确的 `user_id`、修订与历史 |
| 个人事实与意向 | `service/profile.py`、`intention.py`；字段 schema、来源、显式关联和旧行整理 |
| 简历与公司范围 | `service/resumes.py`；方案、文案、组合、答案、公司决定与引用复核 |
| 网页 / MCP | `service/http.py`、`mcp.py`，`web/app.js`、`forms.js`；共用服务操作 |
| 独立填写 | `service/forms.py`、`browser_setup.py`；`scripts/form-runner.mjs`、`browser-driver.mjs` |
| 公司资源 | `service/packages.py`、`form_contract.py`；`companies/`、`schemas/form.schema.json` |
| 制作公司包 | `scripts/company-resource.py`、`skills/company-resource/SKILL.md`、[制作指南](company-resource.md) |
| 推荐反馈 | `service/recommendations.py`、`skills/job-recommendation/` |
| 迁移与同步 | `service/backup.py`、`sync.py`；[离线迁移](migration.md)、[WebDAV](sync.md) |
| 发行 | `scripts/check_source.py`、`build_release.py`、`build_catalog.py`、`verify_release.py` |

稳定请求契约见 [development-contract.md](development-contract.md)，实际使用见 [automation.md](automation.md)。不要为已有数据创建第二个事实来源，或绕过修订、账号隔离和同步写入权。公司包代码是经本人审阅的可信代码，浏览器 driver 不是任意 JavaScript 沙箱。

个人基础目录默认 `%USERPROFILE%\.job-search-skill`，可用 `JOB_SEARCH_DATA_DIR` 修改；网页和 MCP 必须指向同一基础目录。数据库、托管原件和私有证据不进入 Git。浏览器依赖、登录 profile、broker 凭据留在本机缓存，不从旧机复制进仓库。修改源码不会自动更新已经安装的插件快照。

## 新电脑继续开发

1. 从公开 Git 仓库取得最新代码。离线开发时可另用 Git bundle。
   ```bash
   git clone https://github.com/queekye/job-search-skill.git
   cd job-search-skill
   git log -1 --oneline
   ```
2. Python 3.11+，优先 Codex 自带 Python；Git Bash。可设置 `JOB_SEARCH_PYTHON`。独立填表需要 Node 22+（或 `JOB_SEARCH_NODE`）与本机 Edge/Chrome；运行 `SetupBrowser.cmd` 安装固定 Playwright 1.62.1 到本机缓存。
3. 新电脑运行 `Start.cmd`，在空个人数据目录配置相同 WebDAV 地址、用户名、应用密码及工作区，拉取资料后接管。旧电脑先同步并交接。需要 Codex 原生工具时运行 `Install.cmd` 并新建 Codex 任务。离线恢复须先恢复再启动服务；不要搬运旧机插件缓存、全局配置或绑定旧路径的自动化。
4. 阅读 `AGENTS.md` 和开发契约，核对分支与工作区。测试使用合成数据、仓库外临时目录；不要对真实档案运行测试或批量重置。

## 检查与构建

CI 在 Windows 上使用 Python 3.12、Node 22。下列命令中的 `python`、`node` 可换为配置的完整程序路径：

```bash
python -B scripts/run.py check
python -B scripts/check_node.py
node --check web/app.js
node --check web/forms.js
python -B scripts/check_source.py
python -B scripts/build_release.py
python -B scripts/verify_release.py dist/marketplace
git -c core.whitespace=cr-at-eol diff --check
```

开发交接文档仅属于源码扫描白名单，不增加运行包体积。`dist/` 不提交；重新构建运行 ZIP、公司 ZIP、静态目录和哈希清单。公司成员文件有字节级哈希，勿为了换行风格批量改写；适配器内容变更须增加公司版本并更新 manifest 哈希。

自动填表已做真实 Chrome 合成页面、网页完整操作、多个独立 MCP 进程共享浏览器及隔离安装验收。真实招聘网站、真实 WebDAV 与当前公共下载网络必须另外核对；合成测试和历史现场证据不能代替新版本现场验收。

## 公共资源与下一步

- 资源索引：`https://job-search-company-resources.pages.dev/index.json`
- 共建表单：`https://github.com/queekye/job-search-skill/issues/new?template=company-resource.yml`
- 源码：`https://github.com/queekye/job-search-skill`

网页默认显示这两个公共入口，旧账号的空值也回退默认，自定义地址保留；用户点击刷新才访问远端。公司包从公共目录重新下载审阅，也可离线导入；个人同步不转移社区代码的接受权限。旧填表任务固定的包版本发生变化时重新准备，个人答案保留。Cloudflare 原生连接 GitHub `main`，执行 `python scripts/check_source.py && python scripts/build_catalog.py --output dist/catalog`，只部署 `dist/catalog`。

后续优先将方案选用、独立文案和适用公司答案接入各站明确字段，再逐家公司补充真实页面观察、适配器与合成回归。当前 `resume.compose.company_form` 只检查公司包声明范围和原事实，始终标记 `scope_complete=false`；它不证明全站完整，也不自动写入官网。记录明确支持/不支持的字段，再做本人授权的现场填写及读回。保持独立 runner、网页和 MCP 共用一套契约。WebDAV 实际探测文件版本条件更新和安全首次创建；标准服务用条件创建，坚果云式服务用 `MOVE/Overwrite:F`。不支持两种能力的服务仍拒绝覆盖，不能用无条件写入替代。
