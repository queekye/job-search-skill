# 公司自动填表资源制作

公司包保存公共数据声明和具体网页执行代码。个人输入、原始简历、申请编号、浏览器配置、登录信息和凭据留在本地数据目录。模板生成和资源校验只处理调用者提供的公共文本，不读取个人数据库，也不执行公司脚本。

## 从观察到可运行资源

先明确目标公司的官方来源、具体简历路由和待支持模块。在用户授权的招聘浏览器中观察当前页面及控件，确认重复条目的身份、下拉选项、日期精度、保存方式和读回页面。只提取公共路由、控件含义和定位方法；合成夹具从零构造，不能复制带有个人数据的 DOM、截图或浏览器存储。未观察的模块写入 `scope.unsupported`。

`career_resources(operation="resource.template",args={id,name,origin,version,path})` 返回完整公共文件映射。`path` 是 `/resume` 等路径，不含查询参数或片段；适配器继续检查查询、片段及共享招聘域名中的公司租户。模板三个入口均返回 `needs_discovery`，生成模板不建立执行能力。

已有包以新版本修改。编写 `form.json`、`form.mjs` 和 `fixtures/*.test.mjs` 后，用 `resource.validate` 检查结构与哈希；`rehash:true` 返回更新后的完整文件映射供保存。校验不建立代码安全、隐私审阅或真实网站验收。更新 manifest 的能力说明、支持范围与分层验证记录，再导出 ZIP，进入原有下载/导入、完整审阅、启用流程。普通资源制作不自动发布或批准社区代码。

## form.json

完整格式见 [form.schema.json](../schemas/form.schema.json) 和 [京东基本信息示例](../companies/jd/form.json)。保持一个声明文件，不建立选择器 DSL：JSON 声明数据，JavaScript 处理网页控件。

`schema_version:1`；`id` 是包内表单编号；`title` 是展示名称；`origin` 与 manifest 的 HTTPS origin 一致；`routes` 是精确路径或以 `/*` 结束的路径前缀；`adapter` 是包根的 `.mjs` 文件；`sections` 按顶层输入字段分组，每个字段出现一次；`scope` 列明 `supported`、`unsupported` 和 `save`。

`input_schema` 使用有界 JSON Schema 子集：object、array、string、boolean、integer、number、null，以及单类型加 null 的二元素数组。每个 object 设置 `additionalProperties:false`，array 设置 `items/maxItems`（最多 100）；支持 required、enum、类型相符的 bounds、format、短 pattern、title、description。format 为 `date`（真实 YYYY-MM-DD）、`date-month`（YYYY-MM）或 `document-id`。pattern 不支持分组、候选分支、环视或反向引用；复杂公司规则写入代码。最多八层、300 个 schema 节点。

- `x-source:"basic.phone"` 等将公司输入映射到标准档案字段。字段命名可不同，例如京东 `mobile` 来自 `basic.phone`。
- 数组 `x-source:"education"`、`"family"` 或 `"experience:project"` 从用户明确选择的本地记录构造；items 内的 `x-source:"school"`、`"start_date"` 相对于当前选中记录。不默认取第一条档案，不按样例学校或固定学历推断条目。
- 公司专用答案省略 `x-source`，由用户提供。不把“其他”之类选项设成默认事实。
- `x-sensitive:true` 表示填写前需要本目标公司的敏感信息共享授权。
- 附件声明 `type:"string",format:"document-id"`，传入本账号管理的稳定文档 ID。非空附件自动列入敏感字段，即使未声明 `x-sensitive` 或将它设为 false，也需要目标公司的共享授权；执行器解析当前路径，资源包不保存绝对路径。既有上传 helper 还读取 `authorization.shareResumeWith`；会覆盖表单的解析须单独 `replaceProfile` 授权。

required 表示当前支持模块填写前所需输入，完整网站要求与尚未实现的模块在 scope 和 guide 中说明。缺失或未知值保持缺失，不能用空字符串或猜测替代来生成“填写成功”。

## 统一代码入口

`form.mjs` 导出 `async inspect(ctx)`、`async fill(ctx)`、`async verify(ctx)`。上下文包含 `driver`、兼容旧 helper 的 `tab`、本次固定 `input`、`signal`、`progress`、已解析 `documents`、`helpers`、原用户 `authorization` 与声明 `schema`。公司代码不能自行扩充授权。

`inspect` 只读确认当前路由、表面、控件唯一性、重复条目身份和非空冲突。`fill` 执行已支持输入并检查取消；旧文本计划可调用 `ctx.helpers.fillResumeOnce({tab:ctx.tab,plan,facts:ctx.input,authorization:ctx.authorization})`。`verify` 独立读回实际控件，不能复述 input 充当验证结果。教育、项目、下拉与文件上传的具体规则仍在公司代码中实现。

结果包含 `ok`，逐字段 `fields:[{key,status,verification?}]` 及 `saved/submitted`。失败返回 `blocked:{code}`，保留已完成字段；不返回包含个人输入的日志。当前页面值、保存完成、刷新后的持久化结果与最终投递是不同证据。省略未支持的字段、原值相同、被保护性隐藏和实际写入应在结果中说明；没有请求字段不能报完整成功。最终投递由用户执行。

重复运行不重置非空值；在每次写前核对站点与条目身份，冲突或结构改变时停止受影响操作。已显示附件不证明解析正确。共享域名使用租户路由绑定，不能仅校验 origin。浏览器能力以实际运行时为准，旧 CUA API 不自动等同于所有 Playwright API。

## 浏览器接口与最小实现

`ctx.driver` 提供 `getByRole/getByLabel/getByText/getByPlaceholder/getByTestId/locator`；定位器支持 `filter/nth/first/last`、`count/isVisible/isEnabled/inputValue/getAttribute/evaluate` 等读取，以及 `fill/check/uncheck/selectOption/click/press('Tab')/setInputFiles`。同一次操作固定页面 URL，检查取消和既有值；上传路径必须来自 `ctx.documents`。`inspect/verify` 的 driver 禁止写入。`evaluate` 只用于读取控件，受信任代码自行遵守此约定；这些接口不是任意代码沙箱。

对于已观察的普通文本控件，使用 `driver.fillText({key,control,value})`，`control` 可为 label、role、placeholder、id 或已核验的 css。它检查唯一性、补空、同值跳过、非空冲突及精确读回。以下是一个已观察“姓名”输入框的最小示例；实际公司标签须先核对。

```javascript
const control = {kind: 'label', name: '姓名'};
const field = ctx => ctx.driver.getByLabel('姓名', {exact: true});
export async function inspect(ctx) {
  const loc = field(ctx);
  if (await loc.count() !== 1 || !await loc.isVisible() || !await loc.isEnabled())
    return {ok: false, blocked: {code: 'field_unavailable'}};
  const value = await loc.inputValue();
  return value && value !== ctx.input.name
    ? {ok: false, blocked: {code: 'existing_value_conflict'}} : {ok: true};
}
export async function fill(ctx) {
  const result = await ctx.driver.fillText({key: 'name', control, value: ctx.input.name});
  return {ok: true, fields: [result], completed: ['name'], saved: false, submitted: false};
}
export async function verify(ctx) {
  const exact = await field(ctx).inputValue() === ctx.input.name;
  return {ok: exact, fields: [{key: 'name', status: exact ? 'verified' : 'failed'}],
    saved: false, submitted: false};
}
```

准备接口的 `inputs` 是明确的公司补充/覆盖值，只传实际补充的字段，不能将整份自动映射结果再当作公司答案保存。清空已补充字段可传 `null`；重新从档案生成新任务可以恢复通用来源。

## 本地 CLI

使用项目 Python，不依赖 Agent、数据库或联网服务。输出必须为新目录或新 ZIP，不覆盖已有资源。

```text
python scripts/company-resource.py template --id example-company --name "Example Company" --origin https://careers.example.test --path /resume --output <new-public-draft-directory>
python scripts/company-resource.py validate --directory <public-draft-directory> --rehash --output <new-validated-directory>
node --test <new-validated-directory>/fixtures/form.test.mjs
python scripts/company-resource.py export --directory <new-validated-directory> --output <new-package.zip>
```

Agent 在模板目录修改代码和声明后，应把模板夹具替换为可观察行为的测试：匹配实际控件的填入和读回、错误路由、缺资料、非空冲突、未知结构、重复条目身份、取消和失败读回。模板自带测试只验证无实现时不会填写，不能作为填表验收。真实网站验证单独执行并记录；私人证据留在私有目录，不加入包。

公司包的安装与审阅、不可变版本、哈希和 ZIP 约束继续遵循 [公司资源契约](company-pack.md)。

## Agent 调用填写

`career_forms` 与网页使用同一执行服务：先 `fill.describe {company_id}` 读取支持范围，再 `fill.prepare {company_id,inputs?,selection?}` 从明确选中的本地记录生成固定输入。`selection` 按 basic、education、experience、family、preference 列出记录 ID；`inputs` 补充已审阅的公司答案。缺项、无效值和敏感字段在返回结果中单独列出。

用 `browser.open {company_id}` 打开独立招聘浏览器，由用户登录；`browser.inspect` 确认当前页面。随后 `fill.run {id,revision,session_id?,authorization:{prepareProfile:true,shareSensitiveWith:{"<company-origin>":true}}}` 执行已准备内容，`fill.status {id}` 读取实际结果，`fill.cancel {id}` 取消。授权对象表达用户本次的真实范围，不能因示例为 true 就推定用户已同意共享。首次浏览器安装由网页或本地 CLI 完成，MCP 不提供依赖安装。
