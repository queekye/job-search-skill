# 科大讯飞

官方招聘入口：https://iflytek.zhiye.com/ 。本包填写方法限已观察的 `/form?fromPage=editMyResume` 简历表单；`ensurePracticeOnce` 另支持已观察的 `fromPage=myResume`。

`profilePlan` 配合核心 `fillResumeOnce` 填写个人文本、性别以及唯一教育控件。通用教育键为 educationDegree、educationExpectedSchool、educationSchool、educationAcademy、educationMajor、educationLaboratory、educationResearchField、educationTutor；必须提供准确现存学校身份和官网学历文本。存在多个同名教育控件时停止，应使用逐卡方法。旧 doctoral* 键作为兼容别名保留；不得将其用于非博士记录。身份证与联系方式须有该网站单独敏感资料授权，保护性隐藏读回不证明精确匹配。

null、undefined、空文本兼容字段不请求教育写入，单独给 educationExpectedSchool 也不会选择学历；通用教育文本必须同时有明确的 educationDegree。旧博士键也须提供 educationExpectedSchool 或明确 doctoralSchool，不能仅靠 doctoralMajor 等字段假定教育身份。现存学校非空时须精确匹配；明确博士学校事实可补唯一空学校控件。学历和学校锚点在准备后及每个文本写入前复验，同 URL 学校变化也会停止。

`fillParsedSectionsOnce({tab,facts,authorization})` 使用 `facts.educations` 在现存可变数量教育卡上补填。每条以 degree、school、startDate、endDate 完整身份匹配唯一卡，年月为 YYYY-MM；同学历可以有多条记录，卡片重排不影响匹配。最多 50 卡。可补 department、major、laboratory、researchField、tutor、studyMode、highest、rank；学历仍限本科、硕士研究生、博士研究生，学习形式仍限统招全日制，排名仍限其他/前50%。地区用明确的省市勾选与确认；developmentLanguage1 仍限 Python/Java/C++/Go。这些选择菜单之外的结构需现场发现。

`fillExistingResearchOnce` 补既有实践文本和论文行。`facts.practice` 使用准确开始/结束月份识别唯一既有实践；多实践场景用下一方法。`facts.papers` 使用最新 index、title、description，数量不要求固定，最多 50 行；必须至少有一条现存标题与索引精确对应，其他非空冲突停止，不创建论文。

`ensurePracticeOnce({tab,practice,authorization})` 使用 title、role、desc、startMonth、endMonth，支持现存精确标题卡、唯一全空卡和明确 `addPractice:true` 的一次新增。非空未完成卡的接续另需 `resumePartialPractice:true`，且每个非空字段精确一致。最多 8 卡，月份只使用已观察选择器；未知月份不猜测。

`resumeUploadPlan` 配合核心上传 helper，自动解析可能替换表单，须同时授权上传与 `replaceProfile:true`；没有只换附件模式。文件出现不证明解析正确，逐项复核附件及各条经历。所有填写方法保护旧值、读回结果并返回 `saved:false,submitted:false`，不提供保存或投递。新数量/学历泛化的现场及安装验收仍未执行。
