# vivo 已有投递记录只读

仅覆盖 `https://hr-campus.vivo.com/personal/deliveryRecord` 的非空“已完成的投递”区域。页面横幅须含“投递记录”和“跟进应聘进度，查询暂存投递记录”。不支持登录、发码、简历读写、岗位申请、撤回、打印及暂存投递记录。

[规范化网页方案](references/site-extension.json) 记录入口、页面信号、字段意义、失败处理和分层证据。合成验证保持 partial，隐藏元素组合及分页完整矩阵仍需补验。历史结构观察不证明当前包已现场执行；真实读取与安装验收原件存于仓库外，以包版本和哈希关联。

在已获本人授权的现有浏览器标签页中，先读取当前 CUA 文档。按照核心插件的 `scripts/browser-session.mjs` 建立会话：

```js
const adapter = createVivoAdapter();
const session = createRecruitingSession({
  tab,
  adapter,
  authorization: {origins: [ORIGIN], readApplications: true}
});
const result = await session.readCapability("applications");
```

`createVivoAdapter`、`ORIGIN`、`APPLICATIONS_URL` 由本包 `adapter.mjs` 导出。`applications-reader.mjs` 还导出 `parseApplicationsSnapshot({url,snapshot})` 供合成测试，以及受核心会话授权保护的 `readApplicationsPage({tab,snapshot})`。读取只调用当前标签页的 URL 和 DOM snapshot，不调用网站私有 API、不读取浏览器存储、不点击页面。

卡片必须依次出现：职位名、`当前进度：原始状态`、`校园招聘`、可选 `面试站点：说明`、`YYYY-MM-DD HH:MM 投递`、`查看/打印`。卡片数量须与“已完成的投递 (数量)”一致，且后方具有“没有更多了~”。缺字段、重复职位标题、未知结构、加载/空页面或登录状态均返回 `needs_discovery`，保留本地此前成功观察。

结果仅声明当前已完成投递区域的 `partial` 覆盖，不声明所有历史、其他招聘项目或暂存记录完整。“已完成的投递”不是申请已结束；原始进度不转换为录用、拒绝或其他结论。面试站点说明仅保留原文，不能据此认定收到面试邀约。页面没有提供职位/申请编号和职位链接时，对应字段返回 null；不按卡片顺序创建长期标识。后续关联本地记录必须由调用者依据已核实信息完成，遇到歧义保留原记录。

页面必须具有唯一的可见掩码账号，支持号码前带国际区号及一个空格。缺失账号或出现多个匹配账号（包括同一账号重复出现）时停止读取；同一会话内账号或卡片内容变化也会停止读取，重新检查页面后创建新会话。可见账号只在内存中用于一致性比较，不进入输出、日志或资源文件。只有读取成功后，调用者才可依据证据更新 `checked_at`；失败时记录 `last_attempt_at` 与阻点，保留上一次官网状态和成功时间。

本包来自公开页面结构观察及合成测试。`manifest.json` 的 `live_reverified` 独立表示当前版本代码是否已经现场执行验证；不能用合成测试或原始页面观察代替新包现场验证。

## 公开岗位 JD

2026-10-16 已观察公开岗位详情，按 [岗位说明读取](references/job-description.md) 核对精确身份、完整保存正文及失败阻点。该能力仅为文档，不扩展现有网页动作或替代原执行模块的现场/安装验收；当前公开版本不证明历史投递版本。
