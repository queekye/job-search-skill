# 字节跳动校招
官方入口：https://jobs.bytedance.com/campus/ 。入口及规则为迁移记录，使用前核验当前页面。通过 createBytedanceAdapter（adapter.mjs）与核心 createRecruitingSession 组合；authorization 来自当次用户任务。无特定岗位时可先整理通用资料，但旧填表 helper 仍要求真实 specificJobId，不得编造 ID 解锁。
session.inspect() 返回状态；readCapability("applications") 读取当前页申请，readCapability("resume") 仅审计创建简历表单。非空申请是 partial；缺少的 ID 保持 null。仅已观察明确空态可以返回空记录，其余加载/未知结构不能清除旧进度。普通校园招聘和博士专项规则分别核验，不能把未知额度理解为无限。
登录动作 fill_phone、check_terms、request_otp、fill_otp、login 只在授权范围执行。网站可能自动注册，register/acceptTerms 守卫不能省略。先创建本地 OTP 窗口再发送网站验证码；冷却或尝试结果未知先观察，不重复发送。图形验证交用户或按当前工具能力处理。open_applications/open_resume 只在已确认账户状态导航。
候选目录 candidate-catalog.mjs 只规范化已观察的公开卡片。翻页等待确切旧卡片隐藏后读新页，同名不同 ID 保留，强候选再读官方详情。不要宣称全站穷尽。
session.fillProfileField(label,{value}) 支持部分基本文本。复杂控件用 createBytedanceFormEditor（form-controls.mjs）：fillFieldText({label,value})、selectDropdown({label,option})、selectYearMonth({endpoint,year,month})。重复经历需传 {verified:true,locator} 的已观察唯一 scope，或单次 field，两者互斥。
起止月份必须使用月份选择器；直接 fill 曾清空。目标年一致时直接选月；下拉需读实际 selected marker，包含菜单选项的整块文字不算选中。默认“没有实习经历”不是用户事实。工作地点多选目前未开放。完整接口与控件限制见 references/form-controls.md。
恢复一节用 inspectFieldMap、restoreSectionFields（field-map.mjs），先核验整节数量/顺序/类型，每项写前重新映射。空未知跳过，非空且不支持的值阻断，失败停止。值只在私有会话内保留。细节见 references/field-map.md。
写前后调用 inspectVerificationOverlay（verification-overlay.mjs）。空验证层可无文字，empty_overlay/unknown 停止重试，不把刷新或 Escape 当已验证恢复。字段 helper 返回布尔读回及 saved:false,submitted:false，不保存/上传/删除/投递。另行保存需有当前授权和保存后页面证据。

已保存带日期的公开规则见 [company-rules.json](references/company-rules.json)。这是历史来源记录，不是本次重新核验；使用前确认项目与当前条款。
