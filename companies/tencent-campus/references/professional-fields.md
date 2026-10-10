# 专业文本方法与边界

构造 createTencentProfessionalEditor({tab,authorization,expectedJobTitle})。authorization 包含 origins:["https://join.qq.com"]、当前已选岗位 specificJobId、prepareProfile:true；只读使用 readResume:true。

readProfessionalSection({section}) 返回最新重复卡片结构。fillProfessionalText({section,experienceIndex,infoIndex,field,expectedCurrent,value,expectedFields}) 在内存中比较旧值，再核对 URL、岗位、卡片允许字段快照和控件归属。现值等于目标时不写。结果返回 saved:false、submitted:false。

| section | 重复容器与字段 |
| --- | --- |
| education | li.send_box#page-resume-sections4 下的 experience_box/info_list；researchDirection、papers |
| projects | li.send_box#page-resume-sections6 下的 experience_box/info_list；name、role、description |
| supplement | li.send_box#page-resume-sections13；唯一 additionalInfo textarea |

重复数量按本次页面解析，最多 50 卡。education/projects 写入必须有 expectedFields，值由本次 read 中所选 card.fields 的全部 available:true 字段构造；仅允许上表专业字段，隐藏字段不加入快照。例如 `Object.fromEntries(Object.entries(card.fields).filter(([,f])=>f.available).map(([key,f])=>[key,f.value]))`。

experienceIndex/infoIndex 保留为已读取索引参数并须在现存结构中存在，实际定位使用 expectedFields 精确匹配唯一卡，读后同数重排能重新找到该卡；缺少、空、不完整、过期或不唯一快照均不写。唯一 supplement 不要求这一快照。写前再次比较该卡全部可见允许专业字段；写后检查数量、未修改字段及目标值。写入期间数量变化、重复控件、其他字段并发修改或当前值冲突均停止。隐藏字段 available:false,value:null。未知结构和失败后先检查，不重复写。

仅处理已观察文本控件，不增删教育或项目，不提供身份、联系方式、日期、附件、下拉、checkbox、保存或投递。新版本数量泛化须重新进行真实页面与已安装版本验收，历史源码观察不能自动升级为新版本通过。
