# 阿里巴巴集团校招
入口：https://campus-talent.alibaba.com 。只读取 /personal/applications 的已观察非空表格。
使用 createAlibabaAdapter 与核心 createRecruitingSession，传当前获准 origin、readApplications:true，先 inspect 再 readCapability("applications")。既有标签必须显示受支持账户状态。本包不登录、写简历、投递、撤回。
进行中与已完成区分别读取，保留申请编号、官方 href 和原始状态。位于已完成区不自动等于淘汰或录用。表头、列关系、行身份或状态变化返回 needs_discovery。空态和全量分页不在已验证范围，无法读取不代表无申请。其他招聘项目或门户不能照搬本入口。

公开 JD 的 `/campus/position/<position-id>` 浏览器读取与身份核对见 [JD 采集说明](references/job-description.md)。该能力为 documentation；完整正文、时间和版本关系写回本地岗位记录，旧 reader 的执行范围和历史证据不因此扩大。
