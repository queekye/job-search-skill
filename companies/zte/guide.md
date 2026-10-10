# 中兴通讯

官方招聘入口：https://job.zte.com.cn/ 。已观察的 Moka 简历租户为 `https://app.mokahr.com/campus-recruitment/zte/46903#/candidateHome/resume`。

`profilePlan` 供核心 `fillResumeOnce` 使用，填写所在地和证件文本；就绪条件只检查个人信息、教育背景和保存模块，不要求任何特定学校。授权须有 `prepareProfile:true`、origin，敏感字段另需 `shareSensitiveWith[profilePlan.consentKey]:true`。共享 Moka 域名不代表授权其他公司租户。

`updateSummaryOnce({tab,expectedCurrent,summary,authorization})` 在唯一简介 textarea 上精确核对原文后更新；已有值与原文不符时停止。

`ensureProjectOnce({tab,project,add,preserveExistingProjects,authorization})` 处理一条项目。`project` 提供 title、role、description、responsibilities、startMonth、endMonth；年月必须是已确认的 YYYY-MM。现存项目按描述、职责和年月匹配，或使用唯一全空卡。仅 `add:true` 和独立 `authorization.addProject:true` 同时存在时允许通过已观察按钮添加一张卡。其他现存卡保留；可提供保留清单再次确认。最多处理已观察结构内的 10 张卡，不删除项目。

写入前保护非空冲突，写后读回。受保护的非空标题或角色需要本人核对，不能登记为精确匹配。所有方法返回 `saved:false,submitted:false`，不提供申请读取、登录、保存或投递。

新版本删除学校依赖；旧版本页面观察只用于控件依据，新版本异形档案、官网保存和已安装版本仍需分别验收。个人数据仅在调用内存中提供。
