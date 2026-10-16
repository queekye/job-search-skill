# 腾讯校招

官方入口：https://join.qq.com 。`createTencentAdapter` 配合核心 session 读取既有申请；公开列表用 `readCandidatePage`，完整详情用 `readCandidateDetailPage`，需要 origin 和相应读取授权。列表覆盖当前页面，详情识别已观察的普通与青云布局；规则按当期官网核验。

进度读取限已授权的非空 `/progress.html`。水合加载、登录失效和未知状态不能清空旧记录；缺少 ID、城市、项目保留未知，读取范围仍为 partial。测评期限没有起点时不推断截止时间。

专业文本使用 `createTencentProfessionalEditor({tab,authorization,expectedJobTitle})`；必须有具体已选岗位的数字字符串 specificJobId 与准确标题。只读用 readResume:true，写入用 prepareProfile:true。`readProfessionalSection({section})` 读取最新卡片，再 `fillProfessionalText({section,experienceIndex,infoIndex,field,expectedCurrent,value,expectedFields})` 精确比对。

教育和项目重复容器按当前实际数量解析，最多 50 卡；不要求固定学历或经历条数。不增删卡片。expectedFields 必须包含所选卡的全部可见允许专业字段旧值，按这一快照匹配唯一现存卡；原索引保留为接口参数，读后重排按身份重新定位。旧调用缺少快照时停止，身份缺失、重复或内容改变时也停止。写入期间重新核对字段快照与结构，读回目标值。仅支持 education 的 researchDirection/papers、projects 的 name/role/description、supplement 的 additionalInfo；隐藏字段不读取不填写。细节见 references/professional-fields.md。

可见弹层、导航、过期值、重复控件、未知 query 或岗位不符需要局部发现。每次最多一次 fill 后读回，不重试不明结果。不提供身份、日期、下拉、附件、保存或投递。新数量泛化仅有合成回归，现场与安装验证需分开进行。

references/company-rules.json 是历史公开规则来源，使用前核对项目和当期条款。

JD 补齐与本人登录交接见 [job-description.md](references/job-description.md)；新增内容为文档，不扩展已有执行范围。
