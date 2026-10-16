# 美团招聘
入口：https://job.meituan.com ，既有记录 /web/personal-center/delivery-record，简历查看 /web/personal-center/resume-detail。
使用 createMeituanAdapter 与核心 createRecruitingSession，传实际获准 origin 和 readApplications:true，先 inspect，仅在受支持状态使用声明的导航和读取。
项目招聘流程与每项志愿状态分别保存，不能把项目面试状态套用到所有志愿。主志愿、其他志愿分别保留原文与身份。未知控件、缺标题/状态、加载空态停止该次读取，保留以前有效状态及本次覆盖范围。
本包只实现记录读取，不登录、写简历、换志愿、撤回或投递。人工操作经验需在新版本封装验证后才能宣称自动填写。

规范化结构及分层证据见 [site-extension.json](references/site-extension.json)。已观察结构为单项目两志愿和双项目三志愿，`schema_live_evidence` 仅声明这些有限形态。流程折叠时不推断步骤；分页 disabled 不证明已遍历全部历史。任意项目数、其他结构、全部历史及未暴露账号身份仍需发现，结果保持 partial。

合成测试、历史页面结构、当前版本现场读取与已安装版本验收分别记录。旧版本读取不证明新包已现场执行；验收原件存于仓库外，以包版本和哈希关联，不含个人申请记录。

公开校招/LongCat 目录及 JD 详情的浏览器读取见 [JD 采集说明](references/job-description.md)。该能力为 documentation；未知历史岗位 ID 或项目/地点变化时保存 related 证据，不按同名覆盖历史岗位或申请状态。
