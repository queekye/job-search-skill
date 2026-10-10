# 京东校招

官方校园招聘入口：https://campus.jd.com/home 。集团招聘入口 https://zhaopin.jd.com/home 与校园简历属于不同站点，资料不视为自动同步。

`profilePlan` 配合核心 `fillResumeOnce` 填写已观察姓名、联系方式、证件文本和性别。联系方式及证件须有本网站单独授权；保护性隐藏值需要本人核对。来源只接受明确提供的已观察选项“其他”，不推断或改写真实来源。

`resumeUploadPlan` 配合 `uploadResumeOnce`。上传须有 `prepareProfile:true` 和 `shareResumeWith[origin]:true`，`parse:true` 另需 `replaceProfile:true`。附件显示只证明上传，不证明解析字段正确。`readResumeUploadStatus` 只读，`confirmResumeUploadOnce` 恢复待确认弹窗，不重复上传。

`fillParsedEducationOnce({tab,educations,authorization})` 处理现存教育卡的调用方子集，最多 50 条；不要求固定学历或条数。每条以精确学校和开始/结束时间匹配唯一卡片，可支持同开始时间的不同学校。字段包括 department、major、lab、tutor、researchDirection、degree、studyMode、highest、doubleDegree、rank、majorCategory。学历选项限已观察的本科、硕士、博士；专业类别仍限已观察的计算机相关类和航空航天相关类，其他分支需现场发现。未知事实不得猜填，不追加教育卡。

`fillExistingProjectsOnce({tab,projects,authorization})` 处理任意现存项目子集，最多 50 卡。`projects` 为 `{index,expectedTitle,title,role,description,startDate,endDate}`；调用前读最新索引及原始标题。非空 expectedTitle 必须在页面唯一，且当前索引精确对应；重复标题或同数重排后停止并重新读取。expectedTitle 为空时，仅可重用 title、role、description、startDate、endDate 全空且页面唯一的空卡；无标题但已有其他内容的记录不当作空卡。写入期间逐项维护五字段旧值快照，任何未修改字段变化均停止。日期仅支持明确授权的 YYYY-MM-01 占位日，另需 `placeholderDayOne:true`；不把月份推断成真实日期。

所有请求字段在首次写入前预检非空冲突，每次写前核对条目身份，写后读回；不增删卡片、不保存或投递，返回 `saved:false,submitted:false`。版本升级的数量泛化仅有合成回归，真实官网及已安装版本需分别验证。
