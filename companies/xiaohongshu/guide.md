# 小红书：记录页方案包

官方入口：https://job.xiaohongshu.com 。方案记录 `/campus/record` 的“未开始”区与展开后的“已结束”区，展开仅用于查看既有记录。

规范化扩展方案见 [site-extension.json](references/site-extension.json)。区段“未开始”“已结束”和条目自身显示的状态分别保留，不把区段名称改写成投递成功、拒绝或其他结果。仅观察本次可见范围；未显示的条目身份、全历史、分页、登录控制或表单控件保持未知。展开控件的可靠定位和复核方法尚未转成执行模块，本版没有猜测选择器。

REDstar 与普通校招分别核对当期规则。REDstar 草稿保存及最终投递尚未实测，不继承普通校招的志愿数量、名额或投递规则，也不能把填入或存在草稿记成已保存或已投递。

本包为文档型方案，没有 reader、登录、字段写入、保存或投递脚本；`applications/profile_prepare` 为 unavailable。历史结构证据不等于执行 reader 的验收，`live_read/installed` 保持 `not_run`。合成测试、现场与安装证据分开记录，原件存于仓库外，以包版本和哈希关联。个人值、私人 DOM、申请记录不得进入贡献包。

公开岗位详情及已有实习 JD 的浏览器读取见 [JD 采集说明](references/job-description.md)。public_jd 为 documentation；初始“-”占位符须等待正文渲染，当前完整 JD 与历史投递版本分别记录。
