# 个人资料字段目标 v2 与当前实现

状态：字段目标与实现对照，core 0.4.0。教育、项目、论文常用类型化字段、唯一意向、简历方案、公司答案及公司决定已接入共用服务，保留既有 education / experience kind 与 ID。实际可编辑字段以 `schemas/profile-fields.json` 和 `schemas/intention-fields.json` 为准；数据库 schema 4、portable v3，兼容 v1/v2 导入。下表仍包括尚待实现的细分实体和字段目标，不能据表声明网页或公司包已完整支持。

当前意向使用每账号唯一 `settings.preferences`；方案、答案和公司决定使用 `records` 内的 `resume_variant/company_answer/company_decision`，继承记录级来源、范围、状态和修订。它们不进入个人事实编辑分区，`profile.patch` 不编辑这三类实体。方案/答案供人工与 MCP 共同维护；意向、决定、确认及引用复核写入仅限本人。

## 约定

实体共享 id、user_id、revision、schema_version、created_at、updated_at、archived。证据独立存 entity_id/field_path/source_record_id/document_id/source_label/source_scope/confirmation_status/confirmed_at。合并多来源不丢证据；AI 分类和改写是 proposed，不自动继承原文 confirmed。

日期 ISO YYYY / YYYY-MM / YYYY-MM-DD；不补缺失日。区间另有 is_current，不从空 end_date 推导“至今”。标量未知 null；patch 缺省不改，清空为显式操作；空数组为确认无条目，未知数组 null。枚举有 unknown；额外字段拒绝。所有长度、数值和数组有限额；长原文引用材料，不截断覆盖。

## 固定字段

| 实体 | 字段 | 类型 / 语义 |
| --- | --- | --- |
| basic 单份 | name, preferred_name, phone, email, gender, birth_date, nationality, ethnicity, hometown, residence_city, location, mailing_address, postal_code, identity_type, id_number, summary, homepage, github_url | text/URL/partial-date；敏感字段最小输出；籍贯、户籍与所在地不能按名字互换 |
| education[] | school, campus, department, major, major_category, education_level, degree_name, study_mode, education_status, start_date, expected_graduation_date, graduation_date, degree_awarded_date, is_highest, double_degree, laboratory, supervisor_name, supervisor_title, research_directions, gpa, gpa_scale, rank_percent, description | 学历 / 学位分开；studying/completed/withdrawn/unknown；排名为事实数值；站点“其他”存答案；research_directions:text[] |
| employment[] | organization, department, employment_type, title, role, start_date, end_date, is_current, responsibilities, achievements, skills, location | work/internship/part_time；课题统筹不能变成正式任职 |
| project[] | title, short_title, organization, project_type, project_code, role, start_date, end_date, is_current, description, responsibilities, results, technologies | 本人参与区间；官方题名与短标题分开；背景 / 职责 / 成果分开；实验条件随数值保存 |
| research[] | title, method_name, research_status, start_date, end_date, is_current, problem, methods, contributions, results, metrics, technologies | supporting/ongoing/completed/unknown；metrics 为 dataset/metric/value/unit/configuration/baseline/source 对象[]；不以发表年补日期 |
| publication[] | title, authors, author_position, corresponding_author, venue, publication_type, track, year, publication_status, doi, url, volume, issue, article_number, pages, abstract | 有序 authors:text[]；year 整数；published/accepted/submitted/in_preparation/unknown；挑战赛不变主会长文；分区标签须时间证据 |
| skill[] | name, category, proficiency, description, evidence_entity_ids | programming/framework/tool/domain；等级自报；未提供则 null |
| language[] | name, proficiency, certificates | certificates 为证书 ID[]；CET-6 不推导熟练程度 |
| certificate[] | title, issuer, obtained_date, expires_date, score, score_scale, document_id | 不虚构分数和取得日期 |
| award[] | title, issuer, level, awarded_date, description, document_id | 竞赛论文不自动变奖项 |
| family_declaration[] | relation, name, organization, department, role, employment_status, confirmed_at, notes | unknown/current/former/none；未填写任职资料默认无任职，不逐公司建立否定记录；已填资料按公司和任职状态判断 |
| job_intention 单份 | recruitment_types, city_tiers, allowed_cities, other_cities_allowed, primary_directions, exploratory_directions, excluded_directions, industries, excluded_companies, earliest_start_date, salary, notes | city_tiers:text[][]；salary:expected_amount/amount_min/amount_max/currency/period/tax_basis；期望值不自动变成上下限，未知税基保留 unknown；campus/social/internship |
| resume_variant[] | name, document_id, project_ids, research_ids, publication_ids, employment_ids, skill_ids, summary_text, section_order, text_overrides, target_scope | 当前字段；有序 ID 引用事实，覆盖仅限选用条目的 description/responsibilities/results/abstract；不复制一套个人事实 |
| company_answer[] | company_id, origin, recruitment_scope, question_key, label, value, value_type, linked_record_id, linked_field, expires_at | 当前字段；string/boolean/number/array/object 类型值，按公司及招聘范围精确匹配；来源、调剂、特殊声明存答案，全球事实保持不变 |
| company_decision[] | company_id, company_name, decision, reason | 当前字段；pause/exclude/watch，由本人维护；不等同官网状态、推荐排除规则或单次执行许可 |

当前支持带证据的 project_research、research_publication、project_publication；education_research 仍为目标。论文、课题和研究不是同一种实体；标题相似不能自动推导关系，也不自动改变方案选用。日期占位转换必须由具体公司映射明确，事实仍保留月份；旧预计毕业例外不能改写全局预计、实际毕业或拿证日期。

服务维护方案 `source_fingerprints` 和答案 `source_fingerprint`，对文案或答案引用的原事实变化提示复核，字段不是用户填表内容。`resume.get/answer.get` 返回独立 `source_review`；保存可带 `source_versions` 锁定事实修订，仅本人可按当前 fingerprint 重新确认。过期、未确认、范围不符或冲突的公司答案不标为可用。

## 兼容与身份

实体 ID 稳定。去重只提候选：教育用学校 / 学历 / 区间，项目用名称 / 编号 / 组织 / 参与区间，工作用组织 / 职位 / 区间，论文优先 DOI 并核对作者和发表信息。合并保存别名、旧 ID、关系、字段来源与冲突；updated_at 不代替本人确认顺序。

patch 带记录 revision；方案、关系、答案和决定有独立 revision，意向沿用 settings revision。已实现迁移以全量 digest 锁定快照并记录收据；通用实体合并仍为目标。方案组合 pins 返回读取的事实与答案，独立 fill.prepare/run 使用自己的输入及公司包版本锁；将方案文案及公司答案接入填写时仍须扩展输入契约。

- basic.phone 规范化；站点 mobile 保持映射别名。
- education.degree → education_level；description 经证据解析 laboratory/supervisor/research_fields/study_mode/double_degree。旧 end_date 先判断预计 / 实际，不统一塞毕业事实。最高学历只在教育覆盖完整时派生并标 derived。
- experience.category 映射对应实体。研究先判断与项目关系；无法解析说明完整保留 source evidence。
- preference 逐条归意向 / 教育事实 / 公司答案 / 简历方案 / 公司决定 / 任务约束 / 历史。other 无统一自动映射；不能按 notes 任意创建 schema 字段。
- 单次授权与旧指令只进历史 / 待复核，不升级为长期规则。confirmed 与 currently_applicable 分开。
- 研究数值保留来源与实验条件；本轮整理不等于独立核验最新学术结果。

实施时补齐各字段 length/items/range/format、条件必填、单位、敏感输出与字段审计，并写进 JSON Schema 与服务校验。FIELDS/ENUMS、网页字段和 MCP schema 从同源生成。本文不宣称生产界面或所有公司包已支持这些字段。
