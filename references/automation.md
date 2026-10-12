# 独立自动填表

自动填表由本地服务、独立浏览器执行器和已启用的公司资源组成。网页和 `career_forms` MCP 使用同一执行通道，不依赖 Codex 的浏览器或模型完成已实现的字段操作。用户登录专用招聘浏览器，审阅拟填写内容，执行填写并检查结果；最终投递由用户完成。

公司资源的 `form.json` 声明输入与支持范围，`adapter` 模块导出 `inspect(ctx)`、`fill(ctx)`、`verify(ctx)`。`ctx` 包含 `driver`、兼容的 `tab`、`input`、`authorization`、`signal`、`progress`、`documents`、`helpers` 和 `schema`。公司代码负责实际页面控件；核心负责当前账号取数、版本固定、输入检查、浏览器生命周期与执行结果。资源制作见 [公司资源制作](company-resource.md)。

## 使用

1. 启动 `Start.cmd`，打开“自动填表”，选择有自动填表入口的公司并启用对应资源。
2. 如缺少浏览器依赖，运行 `SetupBrowser.cmd`。依赖保存在本机缓存，运行包和公共资源不包含浏览器配置与登录记录。
3. 打开专用招聘浏览器，自行登录并进入该资源支持的简历页面。
4. 从当前账号档案生成填写内容，补齐缺项并审阅；敏感数据、附件共享和覆盖分别按实际操作确认。
5. 点击填写，查看逐项结果。字段读回不等于网站保存或投递；按该资源的支持范围继续保存和核验。

重复执行应补空、跳过相同值，并报告冲突。未知资料、未支持控件或缺失条目不会自动猜填。资源包执行代码是用户接受的可信本地代码；浏览器接口的限制和文件哈希不构成任意 JavaScript 的安全沙箱。

## 接口

`fill.describe {company_id}` 返回当前已启用版本的数据声明。`fill.prepare {company_id,inputs?,selection?,task_id?,revision?}` 从已确认、作用域适用的档案组装输入；多条单值资料须明确选择。选择以本地记录 ID 为准，附件使用稳定 document ID。公司专用输入与准备结果保存在当前账号的准备任务中，进入个人业务同步。

`browser.open {company_id,url?}` 打开受支持页面，`browser.inspect {}` 读取当前会话，`browser.status {}` 查看运行时状态，`browser.close {}` 关闭。网页切换本地账号不改变网站登录；浏览器配置按本地账号隔离且只留在本机。

`fill.run {id,revision,authorization}` 执行最新准备任务；运行前校验资料版本、资源版本和写入权。`fill.status {id}` 与 `fill.cancel {id}` 读取和停止运行。运行记录区分字段填写、已有一致、缺资料、冲突、失败及保存状态，不自动提升申请阶段。

## 维护

依赖解析顺序为显式 `JOB_SEARCH_PLAYWRIGHT_MODULE`、安装的本机运行时和可用的宿主依赖；Node 可由 `JOB_SEARCH_NODE` 指定。独立用户使用 Node.js 与 `SetupBrowser.cmd` 安装运行时，无需安装 Codex。专用浏览器配置、运行时连接信息和登录状态不进入个人 WebDAV 或公共包；换机后重新登录。

保存与长时间浏览器操作不持有数据库全局锁。HTTP 与 MCP 跨进程共享本机执行器，同一账号的浏览器会话只允许一次填写运行。关闭浏览器、取消或页面跳转后应检查已执行的部分，不假定整次填写有事务回滚。
