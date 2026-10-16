# 蚂蚁集团

官方招聘入口：https://talent.antgroup.com/ 。已观察校园简历路径为 `/personal/campus-resume`。

`profilePlan` 配合核心 `fillResumeOnce` 填写个人文本、联系方式及现存首条教育卡。通用键为 educationDegree、educationExpectedSchool、educationSchool、educationAcademy、educationMajor、educationTutor、educationLaboratory、educationResearchField；必须给准确的现存学校及官网学历文本，不推断学历。旧 doctoral* 键作为博士记录的兼容别名保留。同一字段的别名值冲突时停止。本方法不选择其他教育卡、不新增教育；需要其他卡时先现场发现其稳定身份与控件。来源仅在本人明确提供“官网”时选择对应官方来源选项。

null、undefined、空文本兼容字段不请求教育写入，单独给 educationExpectedSchool 也不会选择学历；通用教育文本必须同时有明确的 educationDegree。旧博士键也须提供 educationExpectedSchool 或明确 doctoralSchool，不能仅靠 doctoralMajor 等字段假定首卡身份。现存学校非空时须精确匹配；明确博士学校事实可补首卡空学校。学历和学校锚点在准备后及每个文本写入前复验，同 URL 学校变化也会停止。

`fillResearchOnce({tab,facts,authorization})` 补现存论文与其他研究说明。`facts.papers` 为 `{index,title,description,status:"已发布"}`，index 来自本次读取的实际条目，最多 50 条、索引 0..49；不要求有固定数量论文。只使用唯一已观察 ID 控件，不追加论文。otherInfo 独立可选；成果等级和未观察状态不猜填。

`correctProjectMonthsOnce` 只修正经标题识别的现存项目、已观察同年月份选择器。必须有独立日期纠正授权；不能从项目总周期推断本人参与时间，跨年选择器仍需发现。

`resumeUploadPlan` 配合核心上传 helper。上传与解析覆盖是不同授权；`parse:false` 只换附件，`parse:true` 需独立 `replaceProfile:true` 并选择明确的覆盖确认。附件出现不证明解析正确。

联系方式需本网站单独授权。填入前预检全部请求字段，保护非空冲突；每次写入后读回。所有方法不保存或投递，返回 `saved:false,submitted:false`。新学历别名及可变论文行已有合成回归；旧控件观察不代替新版本现场与安装验收。

公开岗位的原始JD、公司/租户身份、登录及下架边界见[JD读取说明](references/job-description.md)。2026-10-16 本轮只核验公开岗位详情读取；不提升既有表单执行代码的现场或安装验收结论。
