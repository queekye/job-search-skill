# 阶跃星辰

官方招聘入口：https://app.mokahr.com/campus-recruitment/step/94905#/jobs 。核对当期招聘批次、地点与任职资格；默认按正式校园招聘范围筛选，实习仅在本人明确请求时纳入。

`profilePlan` 配合核心 `fillResumeOnce`，只覆盖已观察的所在地、证件号码文本控件。简历路径为 `/campus-recruitment/step/94905#/candidateHome/resume`；就绪检查依据个人信息、教育背景、保存模块，适用于不同学校的申请人。

两字段均属敏感资料，须先有本人准备简历及本租户的单独敏感资料授权，授权键为 `https://app.mokahr.com/campus-recruitment/step/94905`。共享 Moka 域名不共享公司授权。仅使用明确本人事实，保护既有值，并逐项读回；保护性隐藏值需本人复核，不能当作精确匹配。

本包没有登录、其他字段、申请进度读取、保存或投递方法。填入结果始终返回 `saved:false,submitted:false`；保存后应由本人刷新、重读官网权威状态。旧控件观察没有确立保存成功，新版本泛化尚未进行现场或安装验收。个人资料及申请记录只放本地数据库，不放入公司包。
