# 开发交接 · 0.2.1

交接日期：2026-10-12。本文件只记录公开项目的开发状态；个人档案、备份路径、现场核对结果和凭据保存在仓库外的本人目录。

## 当前基线

- 0.2.1 为开发预览版，包含独立填表、默认公共入口和坚果云 WebDAV 兼容。公开源码、运行包和 Cloudflare 目录分别发布；提交代码不等于完成三者的发布。
- 独立自动填表已经接通网页和 `career_forms` MCP：准备输入、打开专用浏览器、填写、读回、状态、取消、恢复。无需模型或 Codex 参与浏览器执行。
- 公司包使用 `form.json` 声明输入，`inspect/fill/verify` 提供执行代码；`company-resource` Skill、模板/校验 MCP 和独立 CLI 支持制作、合成测试与导出。
- 统一一键入口先覆盖京东 0.4.0 的六项基本信息。其他公司仍按自身 guide 提供有限读取、helper 或流程指引。教育、项目、附件、官网保存和最终投递不属于京东统一入口的已验证范围。
- 推荐功能支持本人排除及恢复、职责方向反馈、已存在实际申请公司的推荐收束。推荐、本人选岗和正式申请继续分别记录。
- WebDAV 是单写者的个人快照交接，不合并两台设备的离线修改；必须先完成实际同步，不能把保存设置视为成功。

## 代码入口与数据边界

| 模块 | 入口及职责 |
| --- | --- |
| 数据与账号 | `service/app.py`、`storage.py`；一个 SQLite 库、明确的 `user_id`、修订与历史 |
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

后续优先逐家公司补充真实页面观察、适配器与合成回归，记录明确支持/不支持的字段，再做本人授权的现场填写及读回。保持独立 runner、网页和 MCP 共用一套契约。WebDAV 实际探测文件版本条件更新和安全首次创建；标准服务用条件创建，坚果云式服务用 `MOVE/Overwrite:F`。不支持两种能力的服务仍拒绝覆盖，不能用无条件写入替代。
