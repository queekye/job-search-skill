# 招聘网页方案扩展契约 1

本契约把一次招聘网页观察转为可审阅、可复现的公司方法。方案是结构化文档；方案通过校验、存在适配器、合成测试通过、真实页面读取成功和已安装版本可用是不同结论。校验器不会连接招聘网站、执行方案、安装资源或修改个人档案。

适用范围是已授权的官网只读检查，以及本人已选择范围内的专业字段准备。登录、验证码和最终投递不会因为有此方案而自动获得权限。最终投递继续由本人完成。

## 文件与版本

- 方案放在 `companies/<id>/references/site-extension.json`；页面较多时可使用 `references/<surface>-site-extension.json`。
- 按 [公司包契约](company-pack.md) 升级公司包版本并加入 manifest 文件哈希。`company_pack.base_version` 表示扩展前接受审阅的基线；`version` 表示这份方案自身版本，二者不必相同。
- 公司包内容仍仅允许公开方法、代码和合成 fixture。不要把账号值、申请编号、原始个人页面、简历、浏览器状态或凭据写入方案。
- `evidence.artifact_id` 是安全的证据编号，不是私人文件路径。证据原件或真实个人运行日志保留在仓库外；方案只保留必要的公开结构摘要、时间、版本和可用的 SHA-256。
- 方案文件不会自动成为运行时配置，不能通过导入 JSON 调用任意脚本。实现仍须使用已审阅的模块、升级包版本，并完成原有包校验与安装流程。

从完整的合成初始模板开始：

```bash
python scripts/validate_site_extension.py --template
python scripts/validate_site_extension.py companies/<id>/references/site-extension.json
python -m unittest discover -s tests -p test_site_extension.py -v
```

`--template` 只向标准输出打印方案，不写文件。初始模板使用示例域名，所有验证层和用例均为 `not_run`；通过结构校验不表示已观察真实网站。

## 填写顺序与审阅模板

| 部分 | 应写内容 | 未观察时的表达 |
| --- | --- | --- |
| `id/version/company_pack/origin/purpose/mode` | 一次有边界的扩展目标、基线公司包、官方 HTTPS origin、只读或专业字段准备 | `purpose` 说明待观察的问题，不能描述为已完成能力 |
| `authorization` | 实际任务所需读取权限、本人所选岗位或通用资料范围、最终投递归本人 | 不用虚构 job ID 满足旧 helper 守卫 |
| `entrypoints` | 规范路径、确有必要的 query 参数名、是否现场观察、证据编号 | `observed:false`；参数只保存名称，不保存账号/申请/验证码等值 |
| `surfaces` | 可见页面状态、正向与否定信号、控件归属与可见性规则 | `state:unknown`、`observed:false`、`positive_signals:[]` |
| `controls` | 实际控件、动作和效果、语义定位、唯一性、动作后读回 | 不需要动作时 `[]`；待观察控件 `locator:null, unique:null` |
| `read_scope` | 包含/排除的页面范围、原始状态、覆盖、空态、账号与记录身份规则 | 身份未知为 null，默认部分或未知覆盖，空白页面不能视为零记录 |
| `fields` | 本地已有字段、官网字段含义、读取/准备方向、未知值与来源处理 | 无字段映射时 `[]`；不支持的字段使用 `unsupported` |
| `failure_recovery` | 七类必要失败的停止方式与后续动作 | 保留上次成功记录，`automatic_retries:0`，不隐式重发验证码 |
| `evidence` | 证据类型、带时区的时间、方案/适配器版本、安全原件编号和摘要 | `[]`；不能用迁移日期替代现场观察时间 |
| `verification` | 四个层次各自的状态、确切范围和证据 | 每层独立 `not_run`，登录阻断可以标 `live_read:blocked` |
| `test_matrix` | 正常路径和失败场景各自的预期输出或阻断、运行层次和证据 | 没有实际运行的行保持 `not_run` |

`origin` 只含 HTTPS scheme、主机和必要端口。`entrypoints.route` 只含路径，不含 query、fragment、账号值或猜测的路径；若官方流程确需岗位参数，用 `query_keys` 记录参数名，在本次获准任务运行时提供值。参数化路径可以记录公共路由形式，实际路径必须由本次页面观察确定。

页面状态可为 `public_catalog`、`authenticated_nonempty`、`authenticated_empty`、`profile_form`、`login`、`challenge`、`loading`、`unknown`。登录按钮、昵称或一段通用欢迎语不能单独证明申请列表完整；需要组合页面、归属、可见性及否定信号。认证页面不等于本人的身份已经由网站暴露。

控件的 `action` 是计划采取的操作，`effect` 限于 `read/navigate/prepare_field`。例如只读导航可以是 `action:click, effect:navigate`，不表示能够修改申请。角色定位使用 `kind:role`、实际角色和实际可访问名称；`label/placeholder` 保存现场出现的公共标签。`observed_dom` 仅记录已观察的 DOM 归属和结构，作为审阅资料；它不是可执行 selector 语言。未观察不能填写猜测定位器；已观察控件必须有同版本现场证据、唯一性与读回规则。控件未命中、重复、隐藏、禁用、所属条目改变均停止当前动作。

对申请读取，必须明确以下规则：

1. 原样保留官网状态文字和必要提示；内部阶段归类另行说明，不能把“已结束”自动改成拒绝，也不能从时限提示推算不存在的开始时间。
2. 当前页、当前项目、展开流程、全部历史是不同覆盖范围。折叠流程和未遍历分页保持未知；列表加载中、登录过期和不可识别的空态不会清空已有记录。
3. 稳定身份来自已观察的申请编号或官方岗位链接。未暴露的编号、城市或项目保持 null；同名条目不会自动合并。记录项目与志愿的层级关系。
4. 观察前后复核 origin、route、页面身份和关键内容。账号切换、同 URL 内容变化或读取中导航会丢弃本次不稳定结果，保留上次有效数据。
5. 私有记录保留来源、原始状态、检查时间和覆盖范围。公开方案只描述采集规则，不包含实际求职者数据。

## 四层验证及证据

每层 `status` 为 `not_run/passed/partial/failed/blocked`；`scope` 必须说明页面和能力范围。`passed` 是该层声明范围内完成，不能扩展成整家公司完整闭环。校验器检查证据类型、版本和测试声明之间的关系；它不审判日志是否真实，原件仍要由审阅者核对。

| 层 | 可支持的证据 `kind` | 可以证明什么 |
| --- | --- | --- |
| `plan` | `plan_review` | 方案已完成方法、边界和事实来源审阅 |
| `synthetic` | `synthetic_run` | 对指定实现及合成结构跑过列明用例 |
| `live_read` | `live_read` | 本版本在当次真实页面中读取了列明范围 |
| `installed` | `installed_run` | 已接受的安装版本可加载并完成列明工具检查 |

`source_history` 仅表明旧源码或旧版本历史，不可支持本版本任一验证层通过。`site_observation` 可以支持路径/页面/控件的现场观察，也可支持 `live_read:blocked/failed`；看到登录页不等于已读申请。`synthetic_run/live_read/installed_run` 必须有原件摘要哈希；公共包没有权利自动读取私人原件。

例如浏览器仅打开官方入口并显示未登录页面时：增加 `kind:site_observation` 的安全观察证据；入口和登录 surface 可以标为 observed；把 `live_read.status` 标为 `blocked`，`scope` 说明尚未获得申请记录；`installed` 与 `synthetic` 维持各自真实状态。不要将 `applications` 能力升级为已现场验证。

最低合成测试矩阵包括 `wrong-origin`、`unauthorized`、`loading-empty`、`unknown-structure`、`hidden-duplicate`、`navigation-race`、`identity-change`、`partial-pagination`、`raw-status`、`readback-failure`、`private-output`。给每行写具体预期；只读方案的 readback 用例应验证读取后的复核或写入能力不可用。另补正常非空、已证实空态、公司特有流程层级及可执行控件用例。

真实读取和安装用例按实际运行另增行。某验证层只有在该层所有声明用例通过时才能标 `passed`，只覆盖部分用例用 `partial`。增加、修改或移除页面能力后更新方案版本并重新核对引用；不能凭旧证据升级新版本。

## 与现有四家公司接口衔接

目前 manifest 的能力状态是 `executable/documentation/unavailable`；运行时 adapter 的 `verified/needs_discovery` 是旧浏览器接口的有限能力门控，两者不是同一套验证等级。运行时 `verified` 不能代替本契约的 `verification.live_read`。公司包 `verification` 目前为自由结构，本方案单独承担更严格的证据分层。

公共 session 的 contract version 为 1：`createRecruitingSession({tab,adapter,authorization})`，adapter 提供 `id/version/origin/classify/nextAction/capabilities/actions`。session 提供 `inspect/act/readCapability/fillProfileField`；`readCapability` 只处理 `applications/resume`。公开目录和公司独立编辑器必须调用其实际导出方法，不能把新的 capability 名称直接传入 session。所有调用仍受当前 CUA 文档约束，方案不授予额外 browser API。

| 公司 | 当前接入点 | 方案扩展应保留的实际范围 |
| --- | --- | --- |
| 字节 | `createBytedanceAdapter`；应用读取与简历审计；独立表单辅助方法 | 当前可见卡片、已观察空态；编号未暴露保持未知；字段填入不等于已保存 |
| 腾讯 | `createTencentAdapter`；独立公开目录/详情 reader 与 `createTencentProfessionalEditor` | 单页非空进度；限定研究/论文/项目/补充文本结构；独立编辑器返回 `saved:false` |
| 阿里 | `createAlibabaAdapter` | 中文集团门户已观察非空已完成区；稳定申请编号与岗位链接成对；不推断结果；账号变化需重新建立会话 |
| 美团 | `createMeituanAdapter` | 当前可见招聘项目与志愿分别记录；项目流程不替代志愿状态；折叠流程和历史未知；导航来自已观察链接 |

执行扩展前在方案之外写清实际选择的模块、导出方法、输入授权与输出到本地记录的映射；从已接受版本路径加载，实现不得由方案的自由文本生成后直接执行。通用 `fillProfileField` 目前要求本人选择的 `specificJobId`；通用资料编辑需独立扩展与验证这个守卫，不能制造岗位编号绕过。

## 校验的保证范围

JSON Schema 定义结构，stdlib 校验脚本解释本 schema 使用的子集并加上跨引用、证据层次、版本、权限、路由和字段检查。脚本拒绝重复 JSON 键、非有限数字、超限文件、未知属性、未观察的定位器、错误证据层和虚假的同层用例完成声明。诊断只给 schema 字段路径和固定错误码，不回显输入值。

脚本的个人信息筛查只覆盖常见联系方式、私人绝对路径和原始页面标签，不能证明任意自由文字已经脱敏。发布前仍须检查整个包、运行原有发布扫描和公司 fixture，并分别记录源码、真实网站及安装验证。此契约不改变原有资源安装/审阅权限，也不自动改变任何公司的现有能力。
