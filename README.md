# Job Search Skill

面向 **Windows** 的本地求职协作工具和 Codex 插件，当前源码为 **0.4.4 本地开发版**。网页与 Agent 共用 SQLite 档案、类型化意向、简历方案和公司答案；个人事实与方案文案分别维护，自动填表可独立于 Codex 运行。用户选择岗位、审阅资料并最终投递。默认优先正式校招，也可按明确范围处理社招；实习须用户明确要求。每人独立本地使用，账号是同一可信 Windows 用户下的档案分区。

## 开始使用

当前开发预览发行版为 [0.4.1](https://github.com/queekye/job-search-skill/releases/tag/v0.4.1)，新增类型化资料、简历方案、能力目录、面试准备及 JD 证据。旧 [0.2.1 运行包](https://github.com/queekye/job-search-skill/releases/tag/v0.2.1) 使用旧数据库格式。升级前关闭旧服务和 MCP 实例并保存完整备份，schema 4 资料库须由新版服务访问。

1. 将运行 ZIP 解压到普通本地目录，双击 `Start.cmd`，打开 <http://127.0.0.1:8766/>。
2. 选择或新建账号，填写资料或导入简历材料，保存招聘类型、城市和方向等偏好，建立 Agent 任务。
3. 需要原生工具时运行 `Install.cmd`，在新 Codex 任务中使用 `$career`。运行包自带本地市场与文件清单，安装核验后直接使用；源码仓库首次安装会先构建运行快照。

启动器优先使用 Codex 自带 Python，也可通过 `JOB_SEARCH_PYTHON` 指定 Python 3.11+。核心服务只用标准库；文本型 PDF 需要可选 `pypdf`，DOCX、Markdown、文本和 JSON 无需额外依赖。自动填表使用 Node.js 与 Playwright；旧公司 helper 也可通过宿主浏览器调用。可选的 Codex 插件安装需要 PATH 中支持 `plugin marketplace add` 与 `plugin add` 的 Codex CLI，缺失能力会明确报错。

**独立自动填表**：打开“自动填表”，选择支持统一入口的公司，准备并审阅填写内容，然后点击“一键填写”。首次需要安装 Node.js，并通过网页“安装浏览器依赖”或 `SetupBrowser.cmd` 安装固定版本 Playwright；使用本机 Edge/Chrome 的专用配置目录，由本人登录。无需 Codex、模型或 npm 参与填写。当前统一入口覆盖京东六项已观察的基本信息；其余资源仍保留各自已有 helper 与范围。用 [自动填表说明](references/automation.md) 查看接口及边界。

更新时解压新版本，运行新目录的 `Install.cmd`，再开启新的 Codex 任务。安装器核对本项目旧入口后切换，失败时恢复旧入口；成功前保留旧解压目录，其他市场不受影响。个人数据继续使用原来的私有目录。

[公共公司资源目录](https://job-search-company-resources.pages.dev/)已上线。网页已默认填好资源目录和 GitHub 共建入口，可直接刷新目录、下载并完整审阅所需版本，再由本人启用。公司资源更新从 [GitHub 仓库](https://github.com/queekye/job-search-skill) 的 `main` 自动构建并部署，个人数据仍在本机或私有 WebDAV。

```powershell
.\Start.cmd --data-dir D:\PrivateCareer --port 8767
```

网页和 MCP 默认共用用户目录 `.job-search-skill/career.db`。自定义目录时，两者的 `--data-dir` 或 `JOB_SEARCH_DATA_DIR` 必须一致；个人数据目录不能位于源码/运行包目录内。网页停止可在终端按 Ctrl+C，MCP 可独立运行。

每次工具调用显式传顶层 `user_id`；省略时绑定 MCP 启动账号，网页切换不会改变它。新账号资料、任务、设置和附件独立，旧单人库自动迁移到 `default`，保留来源、版本和历史。服务仅监听回环地址，不提供远程多人认证。

## 主要流程

- **档案与材料**：基础信息、教育、工作/项目/研究经历、按需亲属资料；导入文本并保留原件。事实有来源、作用域、确认状态和修改历史，支持版本冲突保护与撤销。
- **意向与简历**：一份求职意向管理城市梯队、主线与探索方向、薪资和到岗时间；简历方案显式选择已有项目、研究及论文并单独改写文案，预览后导出 Markdown。公司答案按公司和招聘范围保存，公司暂停、排除及恢复由本人维护。旧偏好通过预览整理，历史指令不成为当前授权。
- **推荐与申请**：未选岗位在“推荐岗位”；明确选定后进入准备，实际投递分别记录本人自报和官网核验。保留岗位/申请编号、原始状态、覆盖、决定、待办、阻点和证据。
- **任务协作**：账号偏好与任务存在同一数据库。Codex 执行前重新读取上下文与分页，执行后写回进度与结果；用户取消任务或修改偏好后按最新状态执行。
- **面试准备**：使用 `$interview-preparation` 先读取已有岗位、简历与研究，再诊断知识缺口、深挖项目并进行追问练习。网页“任务协作”可创建面试任务，显示计划、项目卡与练习结果；复用原任务表，不改变投递状态或自动化。
- **公司资源**：本地源码有 30 家独立版本包，新增 JD 路由、原岗位匹配和本人登录交接文档；已发布目录以远端实际版本为准。支持本地安装、公共目录下载、完整审阅后导入、回退与公开包导出。新增文档不扩充自动填表范围，字段脚本仅覆盖已观察控件；未知结构需要发现和扩展。
- **个人跨电脑同步**：网页配置 WebDAV，轮流换机时同步并交接写入权；约每 30 秒同步全部账号的业务数据和原始附件，公司资源不参与个人同步。
- **可选取码与跟进**：用户配置自己的 Gotify，Token 由 Windows DPAPI 保护；取码窗口单次消费。每日设置生成绑定账号的宿主任务说明，保存时间不等于自动化已建立。

原生工具为 `career_get`、`career_save`、`career_import`、`career_resources`、`career_forms`、`career_otp`、`career_tasks`、`career_schedule`。执行任务先用 `career_tasks(operation="task.context",user_id="账号",args={"id":"任务编号"})`；无指定任务可省略 `id`。按 `total/next_offset` 继续分页，更新携带最新 `revision`。临时接口也可用 `scripts/run.py mcp --data-dir ... --user-id ...` 绑定同一档案。偏好、账号创建与凭据配置由用户网页完成。

使用 `career_get(operation="capability.list")` 发现能力，再用 `capability.describe` 的 `args.id` 读取具体入口、参数、前置条件、限制和证据范围；`interview.schema` 返回面试任务数据约定。目录是公共能力说明，不表示本机配置、真实网站或当前会话已经验收。调用示例、先查后问规则和无原生工具时的 `scripts/career-call.py` 入口见 [Agent 调用配方](references/agent-recipes.md)。本插件包含 `career`、`job-recommendation`、`interview-preparation`、`company-resource` 四个 skills；源码存在、插件已安装、会话可调用需分别核实。

Agent 制作新的公司适配器时使用 `$company-resource`，通过 `career_resources` 的 `resource.template` 生成公共模板、`resource.validate` 校验声明和成员哈希。也可独立运行 `python scripts/run.py resources template|validate|export`；不读取个人数据库。规范、执行接口、合成用例与导出流程见 [公司资源制作](references/company-resource.md)。

岗位发现规则见 [推荐 Skill](skills/job-recommendation/SKILL.md)：从本人事实构建词族，阅读完整 JD，按资格与职责依据分区、分 A/B/C 梯队，同档并列。用户新增范围、要求刷新，或当前范围内某公司已记录流程明确结束后可继续发现其他岗位；活跃、暂停决定及当期额度仍需核对，该触发不修改已有自动化。

## 公司能力

| 资源 | 可用范围 | 主要边界 |
| --- | --- | --- |
| 字节、腾讯 | 有限岗位/申请读取与部分表单准备 | 不承诺整站或所有控件 |
| 阿里、美团、vivo | 已有申请读取 | 不写公司简历 |
| 京东、中兴、讯飞、蚂蚁 | 部分文本、教育/项目/研究条目与附件能力 | 各自页面、授权与新增控件范围见 guide |
| 阶跃星辰 | 有限 Moka 文本字段 | 保存持久化须单独核验 |
| OPPO、拼多多、大疆、小红书、深信服 | 官方入口与流程指引 | 文档型资源 |
| 百度、B站、海康、华为、小米、卓驭、长江存储、迈瑞、楚兴及中船各研究所 | 本地新增的 JD 来源、路由、身份和登录边界 | 只读文档，无新增填表代码 |

具体接口见 [填表能力](references/profile-fill-coverage.md)、[公司包契约](references/company-pack.md)，执行时固定已接受版本并核对当前页面。脚本填写后读回，官网保存后刷新或进入权威查看页再次核验；返回 `saved:false` 时仅完成字段动作。用户最终投递。合成测试和历史页面证据不会自动证明新版本现场可用。

## 数据、迁移与备份

个人材料和凭据留在源码外，不进入公共 Git、公司包或测试。Codex 会读取必要经历与偏好，**本地存储不代表模型离线运行**。验证码来源不会自动授予登录、资料覆盖或投递权限；附件解析覆盖需独立授权。

轮流使用多台电脑，可在网页配置[个人 WebDAV 同步](references/sync.md)：两台电脑使用相同 WebDAV 地址和工作区，换机前同步并交接；新电脑先用空个人数据目录接入。同步全库全部账号和原始附件，公司资源从[公共目录](references/public-resources.md)下载并审阅。启用后业务修改需要联网验证写入权，程序关闭时没有后台同步；快照未做端到端加密，使用本人私有 HTTPS 目录。

手工换机仍可用 [完整迁移](references/migration.md)：旧电脑停止网页与 MCP 后运行 `backup`，复制私有 ZIP，在新电脑运行 `restore` 到新目录，再启动网页及 Codex。账号、记录、附件、偏好、任务、修改历史和本机已接受公司包全部保留；运行中任务转为待继续。新电脑重新登录招聘网站、配置验证码来源及绑定自动化，业务状态与官网核验时间保留。附件使用稳定 `document_id` 在新电脑解析当前路径。

portable v3 JSON 用于当前账号结构化记录、字段来源、资料关联、简历方案、公司答案和公司决定互换，导入兼容 v1/v2。方案中的事实 ID 在导入时映射；不含附件原件、意向设置、任务和完整历史，不能作为换机备份。仅复制数据库也缺少原始附件，活动库应通过 SQLite backup API 生成一致快照。私有备份与同步快照不能进入公共 Git、公司资源目录或投稿包；不要用网盘直接同步活动数据库目录。

首版不支持 OCR、远程多人服务、多人同时或离线合并写入、公司资源自动启用或自动最终投递。源码测试、本地网页、真实 WebDAV、真实招聘网站和安装后调用分别验收；跨公司完整填表仍需逐家验证。

## 开发与发行

换电脑继续开发请先阅读 [开发交接](references/development-handoff.md)。使用兼容 schema 4 的新版源码或运行包，个人档案通过同一 WebDAV 工作区接续；私有离线备份作为备用。简历方案的公司预览只检查该公司已声明的字段范围，不能据此认定整站资料完整；方案文案与公司答案接入各站填写仍须逐家实现和验证。

源码仓库保留 `Check.cmd`、`tests/` 和开发脚本。使用 `Check.cmd`、`python scripts/check_node.py` 运行合成回归，`python scripts/check_source.py` 扫描全部允许的公开源码，`python scripts/build_release.py` 生成 `dist/` 内的运行 ZIP、公司 ZIP、静态目录、SHA-256 清单和本地市场。源码与运行快照分别扫描；运行 ZIP 不携带开发测试、CI、构建器或开发约定。贡献与维护在源码仓库进行。

公司包只包含公共方法、合成用例及许可材料；Cloudflare Pages 原生连接 GitHub 的 `main`，先扫描公开源码，再构建并仅部署 `dist/catalog/`。目录包含版本 ZIP 与哈希索引，无后台服务、部署令牌或额外依赖。部署与版本维护见[公共资源分发](references/public-resources.md)。许可证：[MIT](LICENSE)。
