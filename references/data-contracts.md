# 结构化记录

规则唯一可编辑权威是 rules.json；其他参考文件解释执行方式，修改规则时同步冲突说明。

## 规则库

顶层 `schema_version=1, version（递增整数）, updated_at, rules, history`。

规则字段：`id` 稳定唯一；`status` 为 active/candidate/retired；`origin` 为 user/example/workflow；`scope` 为 global/domain:名称/project:标识；`section` 为章节名或 all/formula/figure/update；`level` 为 must/preferred；`instruction` 可执行约束；`evidence` 非空来源数组；`check` 检查方式和边界；`exceptions` 明确例外数组。

evidence 每项含 `source, locator`，可选 note。source 可为技能内相对路径、`user:日期或消息标识`、`workflow:程序保障`。内置案例定位为 `word/document.xml Pxxxx`，TXT 用行号，不能伪造页码。history 记录实际更新说明、版本、时间和 changes；旧库备份在 references/rule-backups。

## 项目记录

- `source_map.json`：输入文件/哈希、规则版本、项目范围、本次用户覆盖、技术特征。每特征 `id, source_file, source_locator, source_text, claims, invention_paragraphs, embodiment_paragraphs, decision`。
- `terms.json`：terms 包含 `id, canonical_name, definition, source_locator, variants, prohibited_ambiguities`；claims 包含 `number, parents, introduced_terms, references`。每处 references 记录实际短语、位置、前置基础位置、父链、审核结论，不能只记“已检查所述”。
- `formula_registry.json`：每项 `id, source_file, source_locator, representation, raw_formula, normalized_formula, symbols, assumptions, dimension_check, numerical_check, target_locator, status, corrections`。representation 为 OMML/text/image/OLE；规范表达未核定为 null。symbols 每项含符号、含义、类型、维数、单位证据、范围、定义位置。无单位证据不得猜成无单位。
- `figure_registry.json`：每项 `id, source_file, source_media, sha256, old_number, title, first_mention, dependent_paragraphs, evidence, status, action, final_number, abstract_reuse, review_notes`。删除图保留台账，final_number 为 null，记全部删改处；无图用空数组和原因，不造条目。
- `review.json`：`input_hashes, rule_version, output_hash, checks, issues, release_status`。checks 记录实际检查及证据，未做为 not_run；issues 含位置、影响、程度、处理、剩余缺口。release_status 为 ready/needs_confirmation/visual_review_pending。语义与技术核查完成且最终渲染逐页验收后才 ready，脚本不自动签发 ready。

小项目可把这些记录合成一个 JSON；必要对应关系不能省略。记录中的 null 代表未核定，不用零或空字符串掩盖。

## 批次记录

`batch-policy.json` 是批处理可执行配置，与rules.json中的BATCH规则同步维护；具体编排见batch-workflow.md。每次init在工作目录建立批次清单，冻结输入目录、输入文件哈希、项目ID及顺序、桌面路径、模型策略和时间。每案保存当前阶段、稿件路径/哈希、阶段回执、真实派发证据、审阅问题和交付状态。输入来源、实际派发、最终文件三类证据不能互相替代。

派发记录是主控从真实工具调用保存的 `{tool, arguments, result, context}`，context为本批次的 `{batch_id, case_id, stage}`，用于避免把其他项目/阶段的派发记录误用。阶段身份为 `{stage, agent_id, model, reasoning_effort, dispatch_evidence}`；draft/revise加artifact，review加reviewed_sha256、verdict、findings及checks。具体字段校验由batch_tool.py负责；状态和哈希检查不等于数学或法律正确性鉴证。

不能派发、缺源文件等情况可由主控defer记录具体原因，不伪造模型回执。规则快照包含rule_version、rules_sha256及rules.snapshot.json，worker使用该批次冻结的规则；主控事件与真实模型工作分别留痕。

已交付的失败/待处理单案可通过retry建立新批次。新批次使用retry_origin保存源批次、源案件、原始回执与继承稿件证据，当前receipts从controller_retry事件起；这不是一次新的模型调用。状态从原失败前阶段恢复，旧文稿必须哈希一致，后续仍须新派发的Astra复查/修改。旧通过标记不可继承。

每次最终Word渲染在独立目录保存render.json，绑定input_sha256、官方渲染脚本与配置的程序哈希、PDF和连续编号PNG的哈希。其成功状态rendered_pending_visual_review只证明转换和完整性，review.json另记实际逐页审阅结论。

## 并行批次（schema_version=2）

以上旧批次回执格式只用于冻结的serial_per_project。新rolling_parallel批次使用parallel_batch.py，以CLI返回为精确契约。

manifest包含控制器标识、策略/规则哈希、项目来源快照、每案状态、attempt历史和活动占槽。每案cases/<id>/sources保存独立来源快照，cases/<id>/stages/<attempt_id>保存阶段产物。项目名可以相同，ID及目录不可混用。

reserve返回完整context、spawn_arguments、worker_context及receipt_template。context绑定batch_id/case_id/stage/attempt、source_digest、rules_sha256及input_artifact_sha256，dispatch和receipt逐字段保持一致。不要从另案或上轮拷贝后只改显示名称。

worker_context给出该案来源、当前稿件、规则快照、上一轮审查、可写目录和期望输出路径。未变化的来源提取、当前稿渲染可按哈希复用。独立审查不能用前一worker的自我评价替代。

pass回执render={artifact_sha256,evidence,evidence_sha256}指向本案官方render.json；其PDF、连续PNG及输入哈希由render_docx_checked.verify_render校验，实际逐页查看结果仍写入checks及详细证据。写稿artifact和dispatch/receipt应位于当前attempt目录；引用已存在的本案当前稿仅用于review输入。

总控为唯一状态写入者，锁保护事务读改写。reserved不等于已成功调用模型，bound不等于已完成；取消仅释放已确认不会继续写入的worker，保留attempt历史，拒绝迟到记录。单案一个活跃阶段，跨案可以乱序完成，最终交付仍按队列ID顺序。

并行批次另记录controller_input_change事件：保持冻结快照和worker原回执，移除该案当前通过标志，转needs_input。summary中的peak_reserved_slots/peak_bound_workers是账面占槽/已绑定派发峰值，不是独立的模型同时执行证明。
