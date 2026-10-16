# 结构化记录

v5.0带prepared.pipeline的worker以stage_guide中已包含的“项目记录”原文为证据字段，不另读本文件的控制器状态操作部分；仅遇契约疑点再定位查阅。本文件其他数据要求仍由相应执行角色遵守。

规则唯一可编辑权威是 rules.json；其他参考文件解释执行方式，修改规则时同步冲突说明。

## 规则库

顶层 `schema_version=1, version（递增整数）, updated_at, rules, history`。

规则字段：`id` 稳定唯一；`status` 为 active/candidate/retired；`origin` 为 user/example/workflow；`scope` 为 global/domain:名称/project:标识；`section` 为章节名或 all/formula/figure/update；`level` 为 must/preferred；`instruction` 可执行约束；`evidence` 非空来源数组；`check` 检查方式和边界；`exceptions` 明确例外数组。

evidence 每项含 `source, locator`，可选 note。source 可为技能内相对路径、`user:日期或消息标识`、`workflow:程序保障`。内置案例定位为 `word/document.xml Pxxxx`，TXT 用行号，不能伪造页码。history 记录实际更新说明、版本、时间和 changes；旧库备份在 references/rule-backups。

## 项目记录

- `source_map.json`：输入文件/哈希、规则版本、项目范围、本次用户覆盖、技术特征。每特征 `id, source_file, source_locator, source_text, claims, invention_paragraphs, embodiment_paragraphs, decision`。
- `terms.json`：terms 包含 `id, canonical_name, definition, source_locator, variants, prohibited_ambiguities`；claims 包含 `number, parents, introduced_terms, references`。每处 references 记录 following_noun_phrase（所述实际修饰的完整名词）、head_span（修饰范围）、reference_kind（技术对象回指/权项引用头/说明书行文指引）、occurrence_locator（含句内偏移）、scope、introduced_at、ancestor_path、identity_evidence、verdict、correction；引用头单独分类，不能只记“已检查所述”。
- 版本37所述记录复用上述references，补introduction_text、introduction_span、standalone_introduction、boundary_reason、two_character_noun，绑定当前稿SHA及前置/回指的part、paragraph、字符范围。只有独立引入的完整同一对象才有前置；长词中的子串及同级项不能补足。两个汉字完整名词回指不通过，实际范围由语义核定，不能只数开头两字或补修饰词凑长度。inspection的two_character_prefix_candidate只是有限字面提示，不代替完整词边界、父链或身份核查；所有所述均复用原antecedents审查，不增加重复台账。
- `formula_registry.json`：顶层记录 source_inventory_complete、source_main_count、target_main_count、source/target分类计数、coverage_by_representation及unresolved；未核定数量为null，不用零掩盖。每项 `id, source_file, source_locator, representation, raw_formula, normalized_formula, symbols, assumptions, dimension_check, numerical_check, target_locator, status, corrections`。representation 为 OMML/text/image/OLE；规范表达未核定为 null。每项另记 category（main_formula/source_example/symbol_reference）、source_occurrence_id、target_occurrences及mapping_verdict；逐条完整公式一对一、所有成稿表达都有源，不能用相等计数代替映射。symbols 每项含符号、含义、类型、维数、单位证据、范围、定义位置，以及canonical_math_fragment、codepoints、structure、font/style/size、explanation_occurrences、identity_check。公式另记 literal_whitespace_findings、format_check；无单位证据不得猜成无单位。
- `figure_registry.json`：每项 `id, source_file, source_media, sha256, old_number, title, first_mention, dependent_paragraphs, evidence, status, action, final_number, abstract_reuse, review_notes`。版本28复用本表的evidence/review_notes记录图类型及依据、技术审查范围和对应文字定位，可用figure_type、classification_basis、technical_scope、source_text_locations表达，不新增必填键；流程图登记节点/连线/分支的差异与修正依据，必要修图登记original_sha256、corrected_sha256和实际目标位置。非流程图技术status=not_in_scope，混合图仅核流程部分；不影响原基本格式检查。修图不新增独立图号，不把衍生图当原始技术证据。仅明确授权删除图时保留台账，final_number 为 null，记全部删改处；无图用空数组和原因，不造条目。另登记description_locator/text、all_text_references、final_image_locator、caption_locator/text及final_page/image_caption_same_page，按源媒体身份核对四处一致；摘要复用不额外增加图号。
- `review.json`：`input_hashes, rule_version, output_hash, checks, issues, release_status`。checks 记录实际检查及证据；antecedents绑定逐处名词与前置记录，formulas绑定来源/成稿分类计数、双向映射、完整符号对应及空白/格式结论（见[执行规范](terminology-and-formula.md)），未做为 not_run；issues 含位置、影响、程度、处理、剩余缺口。release_status 为 ready/needs_confirmation/visual_review_pending。语义与技术核查完成且最终渲染逐页验收后才 ready，脚本不自动签发 ready。

正文格式核验并入rendered_pages：记录黑字净稿、实际应用颜色/主题继承问题、修订与批注清理和图片/图号同页结果；工具text_color_evidence与figure_placement_evidence只提供结构线索，不能自动签通过。

版本16起terms的每个claim增加paragraphs、literal_shushu_count、valid_shushu_count、reference_head_count和valid_count_at_least_3；只有完整前置和引用核对后才填有效计数。wording记录protected_names（精确名称/标题及位置）、occurrences（词语/位置/权项或章节/适用范围/改前改后或不适用依据）；替换装置/组件/引擎不得破坏受保护完整名称。formulas的symbol_reference记representation=plain_run|OMML、完整符号身份及普通run例外依据。rendered_pages同时记录人工定稿的权项分段、首行缩进、行网格、五分区实际页眉、线条和页码。以上合入既有evidence.json和六项审查，不另增审核阶段；脚本的wording_evidence、style_evidence和isolated_symbol_evidence仅定位线索。

版本21起在既有source_map或evidence.json登记claim_families：本案已核定类别、完整名称、来源定位、相关权项号及真实引用关系、对应的摘要名称/发明内容/实施方式位置。方法与系统/介质必要步骤以同一特征ID映射，保留条件和输出；记录有益效果每个序号、段落、技术手段和效果依据。不因类别或范例固定项数、段数，不另建重复登记文件。

各案默认可把这些记录合成一份evidence.json；必要对应关系不能省略，已有定位使用引用避免重复抄写正文。记录中的 null 代表未核定，不用零或空字符串掩盖。

规则版本10起，formula_registry的format_check另含array_classification（每个eqArr路径、显示续行/独立多式/条件/矩阵等）、layout_changes（源/稿结构定位及依据）、diagnostic_dispositions（预检code/locator、结论、证据），以及visual_locations（最终页码与公式/解释显示记录）。未知保持unresolved；语义不适用的警告凭源关闭，不能自动全忽略。这些证据合入formulas/rendered_pages，不另加状态机通道。

## 批次记录

版本29新批次策略stage_scheduling=overlapped_pipeline_v29绑定在原policy快照内，必须配合after_each_acceptance。撰写区为draft，复查修改区为review/revise及独立review；各自使用本案原有独立attempt工作目录，不复制整案到第二公共目录。已有阶段、来源、模型和哈希字段即为隔离依据，不新增worker回执必填字段。队列plan在accepted且无活跃attempt时先返回action=deliver及accepted_case_ids，ready为空，手工控制器先交付再占槽。缺少标记的旧批次保持原调度和收稿行为。

`batch-policy.json` 是批处理可执行配置，与rules.json中的BATCH规则同步维护；具体编排见batch-workflow.md。每次init在工作目录建立批次清单，冻结输入目录、输入文件哈希、项目ID及顺序、桌面路径、模型策略和时间。每案保存当前阶段、稿件路径/哈希、阶段回执、真实派发证据、审阅问题和交付状态。输入来源、实际派发、最终文件三类证据不能互相替代。

派发记录是主控从真实工具调用保存的 `{tool, arguments, result, context}`，context为本批次的 `{batch_id, case_id, stage}`，用于避免把其他项目/阶段的派发记录误用。阶段身份为 `{stage, agent_id, model, reasoning_effort, dispatch_evidence}`；draft/revise加artifact，review加reviewed_sha256、verdict、findings及checks。具体字段校验由batch_tool.py负责；状态和哈希检查不等于数学或法律正确性鉴证。

不能派发、缺源文件等情况可由主控defer记录具体原因，不伪造模型回执。规则快照包含rule_version、rules_sha256及rules.snapshot.json，worker使用该批次冻结的规则；主控事件与真实模型工作分别留痕。

已交付的失败/待处理单案可通过retry建立新批次。新批次使用retry_origin保存源批次、源案件、原始回执与继承稿件证据，当前receipts从controller_retry事件起；这不是一次新的模型调用。状态从原失败前阶段恢复，旧文稿必须哈希一致，后续仍须新派发的复查模型复查/修改。旧通过标记不可继承。

先完成内容核查，有阻碍通过的问题时页面检查标not_run并先修正；最终pass仍须六项全部通过。每次最终Word渲染在独立目录保存render.json，绑定input_sha256、官方渲染脚本与配置的程序哈希、PDF和连续编号PNG的哈希。其成功状态rendered_pending_visual_review只证明转换和完整性，review.json另记实际逐页审阅结论。

## 并行批次（schema_version=2）

以上旧批次回执格式只用于冻结的serial_per_project。新rolling_parallel批次使用parallel_batch.py，以CLI返回为精确契约。

manifest包含控制器标识、策略/规则哈希、项目来源快照、每案状态、attempt历史和活动占槽。每案cases/<id>/sources保存独立来源快照，cases/<id>/stages/<attempt_id>保存阶段产物。内部ID及目录不可混用；旧批次可用相同项目名，新wording_title_v18平铺批次须先解决正式名称重名。

reserve返回完整context、spawn_arguments、worker_context及receipt_template。context绑定batch_id/case_id/stage/attempt、source_digest、rules_sha256及input_artifact_sha256，dispatch和receipt逐字段保持一致。不要从另案或上轮拷贝后只改显示名称。

同步手工reserve及旧模式：可选--prepare在占槽提交前准备worker_context.prepared：rules_active、source_inspections、input_inspection、render_candidates、draft_inspection_cache与数据用途边界；attempt.prepared_handoff绑定本attempt内handoff.json及哈希，其prepared_files绑定精简规则/导航；inspection摘要以cache_evidence_sha256封存cache.json原哈希。bind核验本案允许缓存位置、当前完整身份、封存元数据及全部缓存产物，拒绝改动，过程不重解析DOCX。准备异常不提交manifest占槽，保留已生成的缓存供后续核查/重试；无效ZIP/XML返回队列拒绝。候选页面未证明完整性或视觉通过，仍走原verify_render与六项独立审查；旧无prepared回执/批次兼容。

worker_context给出该案来源、当前稿件、规则快照、上一轮审查、可写目录和期望输出路径。未变化的来源提取、当前稿渲染可按哈希复用。独立审查不能用前一worker的自我评价替代。

pass回执render={artifact_sha256,evidence,evidence_sha256}指向本案官方render.json；其PDF、连续PNG及输入哈希由render_docx_checked.verify_render校验，实际逐页查看结果仍写入checks及详细证据。写稿artifact和dispatch/receipt应位于当前attempt目录；引用已存在的本案当前稿仅用于review输入。

总控为唯一状态写入者，锁保护事务读改写。reserved不等于已成功调用模型，bound不等于已完成；取消仅释放已确认不会继续写入的worker，保留attempt历史，拒绝迟到记录。单案一个活跃阶段，跨案可以乱序完成，验收一案交付一案，交付台账保留队列ID，不等待前序未完成案；新批次的成稿直接同层交付；版本18起正式名称与文内主标题逐字一致，重名先解决，旧快照仍按原案号契约。

并行批次另记录controller_input_change事件：保持冻结快照和worker原回执，移除该案当前通过标志，转needs_input。summary中的peak_reserved_slots/peak_bound_workers是账面占槽/已绑定派发峰值，不是独立的模型同时执行证明。

## 旧层级逐案增量交付（兼容）

delivery.timing=after_each_acceptance表示每案验收通过后立即调用deliver；无此字段的旧快照仍可恢复。部分交付记录delivery_mode=incremental_v1、delivery_path、delivery_items及delivery_closed=false。delivery_path存在不再等于整批结束；只有全部项目进入终态且交付清单同步完成后才能关闭。delivery_items按项目ID记录relative_path、sha256和kind；kind=accepted绑定成稿通过哈希，kind=pending绑定待处理说明。

交付目录归属本批次，已有成稿不重新覆盖；每案发布成功后更新台账和清单。发生中断时重复deliver核对归属、验收哈希与已发布产物后恢复，不能把目录存在视为已完成证明。仍在撰写、复查或修正的项目继续原状态和attempt，未验收稿不会复制。

并行增量交付以delivery_recovery_events记录已发布文件在台账提交中断后的恢复事实及发布后原资料是否发生变化；只有归属、成稿哈希和原验收证据均核实的已发布案才能恢复为delivered。事件独立于receipts，未发布案仍须核对来源并隔离变化。

## 同一文件夹直接交付

规则版本8起，新批次delivery={folder_prefix:专利撰写完成,layout:flat_final_files,timing:after_each_acceptance}。此布局记录delivery_mode=flat_final_files_v1；所有验收DOCX直接位于delivery_path根目录，relative_path为单个文件名，不包含“已完成”或项目子目录。名称按冻结全队列预分配并持久保存；旧无output_contract模式通常为项目名称.docx，冲突用稳定案号。版本18起output_contract=wording_title_v18要求正式名称逐字一致，不清洗/截短/附案号；同名或非法文件名须明确解决。不能按完工先后任意重命名。

桌面仅展示成稿；只保留一个隐藏的目录归属标记，批次清单、逐案发布记录、中断恢复信息和待处理说明位于批次工作目录的flat-delivery/中。新发布的复制阶段使用桌面同卷、核验批次归属的隐藏事务临时区，再原子改名到成稿根目录；进程中断的临时痕迹可能保留在该隐藏区，不属于交付成稿，也不能盲删未知文件。任何已发布文件的名称、归属和验收哈希均须一致；文件被修改、缺失或目标被其他内容占用时保留现场并报告，不能覆盖。复制或状态提交中断后依据真实发布证据恢复，不能把存在同名文件当作验收通过。未通过稿不发布；全部案一次交付与逐案追加使用相同布局。旧无layout快照保持原契约。

## 提效模式

旧冻结策略review_mode=review_fix_then_recheck允许Sol/xhigh review在一次调用内修正并返回verdict=revised。reviewed_sha256对应输入旧稿；artifact/artifact_sha256对应当前review attempt内字节已改变的新稿；findings须非空，每项status=resolved或closed，并提供非空location和correction_evidence；checks六键记录实际结果，未做项标not_run。该回执只使案件进入revised，下次必须派发新的Sol/xhigh review，最终pass不得同时夹带修改产物。版本35新三池review_mode=review_only及未带review_mode的旧策略均不接受revised审查回执，仍走独立revise。缓存只复用结构或渲染产物，不复用语义通过结论；--compact只影响工具返回，完整记录照常持久保存。

## CLI派发（规则版本12起）

规则版本17起的新批次可声明worker_preparation=frozen_stage_packet_v1。manifest.skill_snapshot={path,sha256}绑定本批快照清单，清单逐项绑定工具/规范文件以及规则和策略身份。prepared.pipeline提供root、stage_guide及其sha、math_guide、toolkit及其guide、snapshot_sha256；绑定与收稿核验这些身份，worker不写快照。仅引用缓存和生成器的客观结果不产生任何六项passed结论。旧批次字段缺失时仍按旧契约执行。

冻结策略`dispatch_transports`可含collaboration与codex_cli；旧策略省略时只允许collaboration。CLI attempt额外记录transport=codex_cli、完成后的cli_session_id，阶段receipt仍使用原完整context和六项验收格式。

真实dispatch={tool:codex.exec,context,arguments:{argv,cwd,model,reasoning_effort},result:{pid,created,executable},prompt_sha256,agent_id:cli:<attempt_id>}。argv必须是规定的workspace-write命令；PID绑定Windows创建FILETIME。cli-completion.json必须含exit_code=0、pid、created，以及events.jsonl、worker-final.json、session.jsonl、prompt.txt、cli-exit.json五文件哈希。实际会话身份、模型/强度、cwd/sandbox、原始prompt、最终assistant回复和退出码均需匹配；最终回执不能由总控补写模型的审查结论。

CLI退出证明tool=codex.exec.exit，绑定原context/pid/created，含真实整数returncode及confirmed_stopped=true；校验原进程已停止。坏日志或未知身份不是退出证明，不释放不确定槽位。CLI有效完成后的record仍执行原来源/文件/六项/页面门禁，CLI启动/退出证据不替代业务验收。细节见[CLI执行](cli-workers.md)。

## v18成稿文字与名称检查

新策略output_contract=wording_title_v18仅配合frozen_stage_packet_v1和flat_final_files。每案正式名称冻结在selection.title；初始化检查Windows文件名合法性及全队列重名，不静默清洗、截短或加案号。最终inspection的document_contract_evidence记录直属正式标题、说明书范围、global_word_hits及description_word_hits，命中包含part/paragraph/offset/text/context。其他OOXML部件的可提取文字也纳入全稿禁词，图片文字仍由模型和页面检查核对。

最终pass从本attempt封存的input_inspection读取同SHA报告，生成receipt.document_contract={mode,artifact_sha256,document_title,delivery_filename,title_paragraph,scope}；交付前复核并比对该记录。缓存身份、正文正式标题或禁词不符均拒绝；不重新解压解析稿件，也不增加模型阶段。WORDING-04非修饰词的closed finding按part/paragraph/offset绑定当前稿，不能豁免明确的一一修饰表达。此客观检查不替代六项语义/页面审查。无此模式的旧冻结批次继续旧契约。


版本19的新冻结策略另外声明prose_contract=clean_prose_v19，要求现有wording_title_v18及frozen_stage_packet_v1。inspection.punctuation_evidence结构及逐字符公式/技术名词保护finding见manual-format-and-wording.md的版本19节。最终receipt.document_contract增加prose_contract；交付重新验证同稿封存结果及记录一致性。缺字段或非公式命中未解决均拒绝；旧批次无此字段保持原契约。此字段同时启用公式外标点和指定话术检查，不改变六项复查或模型分工。

版本30公式预检增加`formula_prose_evidence.group_separator_hits`，同稿strict_formulas要求其存在且为空。新行内格式候选复用`review_candidates`，含`offset/end_offset/offset_projection`；其FORMULA-05 finding必须匹配该区间、投影、原part/paragraph及精确path/object_id，并提供`format_disposition=not_formula|format_already_correct`、双SHA及来源理由，真实错误不能豁免。公式解释source_symbol可用连续`node_paths`，source_operator记录原名/实参定位与`source_transform=operator_argument_placeholder`；显示组记录原树/显示树SHA和真实equation_count。仅新增既有台账字段，不新增模型或验收阶段。

## 版本36独立调度与提交

pool_execution=nonblocking_handoff_v36要求三池、只读复查、冻结准备和flat交付。CLI先reserve(defer_preparation=True)持久占槽，attempt.preparation_state=pending；prepare_reserved仅做本案I/O，finish_preparation核对当前attempt/context/源与输入SHA及封存handoff后短提交ready，再启动模型。同步手工reserve仍返回完整prepared。准备中禁止bind；中断未知槽不自动重放。bind/record在锁外验证本案，短锁检查case未变及全局agent/session唯一，仅合并该case与当前sequence/peak。独立交付提前持久化transaction_id，锁外复用原flat发布和校验，只合并真实发布case及ledger增量，不回写旧快照覆盖其他案。published但未合并可凭原发布收据恢复；新文件先发布，最终历史审计成功后关闭。模型回执字段与六项审查不变。

## 版本38文末纯图号契约

新批次冻结figure_caption_contract=number_only_v38，依赖wording_title_v18及manual_flow_abstract_v22。figure_caption_evidence复用已解析段落，记录schema_version=1、当前artifact_sha256、complete、scope_resolved、captions/images/issues/review_candidates；inspect缓存依赖绑定figure_caption_contract.py。缺证据、范围不明、旧SHA或任何明确图外内容issues不得接受，不能由泛化finding关闭。未带新开关的旧冻结批次保持旧行为；每次最终verify复用封存input_report，不二次提取。

图内复杂标注仍按原figures/rendered_pages检查。在原findings追加rule_id=TXT-11、classification=figure_internal_label、status=closed，绑定当前artifact_sha256及候选完整part/paragraph/path/text，写现存figure_number、label_role、source_sha256/source_locator/registry_id、visual_locator和reason。label_role仅axis_label、axis_unit、legend、flow_node、technical_mark；source_sha256必须属于本案冻结来源，源位置及注册项应对应同一图，visual_locator指出当前最终页面图内位置，reason说明为什么属于真实图内技术标注而非图外说明。文本框或a:t可包含图外注释，不能机械填角色放行。该记录同时满足同位置complex_body_content分页候选，不要求第二份page_flow_reviewed。源图像素/图表内容及页内关系仍由原六项复查核实；不改技术审查范围，不加台账或模型轮次。

## 版本39全部完整符号解释覆盖

复用formula_registry.json每式symbols[]，不另建symbol_registry。symbols每项保留既有完整数学片段、字形、含义、作用域和explanation_occurrences，并补symbol_id、role、formula_occurrences和meaning_source。role区分完整量/参数/索引/集合/函数调用/算子本体；formula_occurrences定位该完整量在原式及当前式的每次使用，meaning_source记录本案来源SHA、位置及含义依据。explanation_occurrences保存当前稿SHA、part/paragraph、字符范围或数学对象定位、实际完整符号片段和解释全文，不能只填工作文件中的文字；相关source_objects片段与树身份直接复用。

每式symbol_coverage用required_symbol_ids、explained_symbol_ids、missing_symbol_ids、truncated_symbol_ids、unresolved_symbol_ids关联这些已有项，并记coverage_verdict。只有原formulas独立核对左右两侧/嵌套部分的全部完整身份、实际文中解释、同义作用域及最终显示后，且缺失/截短/未解决均为空，才能记complete；这些计数和标签不能自动证明穷尽或技术正确。未知含义不造数据，不以空数组跳过真实公式。无公式须沿用已核定的全表示方式来源清单与原因。

同身份同义符号可让多个formula_occurrences指向同一explanation_occurrence，减少重复解释；不同下标、时刻、语义字形或作用域/含义不得仅按基字合并。独立索引的解释不能覆盖完整量，算子占位解释不能覆盖具体调用量；函数输入量可以独立解释。缺失或错位时原formulas不通过，仍由原修改池修正后独立重验；此处是既有语义证据契约，不是新增自动数学解析器、独立台账或模型阶段。

版本41的symbols及required_symbol_ids只登记有独立数学含义的身份。同一算子的完整记号解释已覆盖其名称，不另建裸函数头/算子名重复项，不为凑覆盖另写名称说明。已有重复项经同身份、同义、同作用域核定后关联到同一完整算子项，保留原来源定位、formula_occurrences和实际explanation_occurrences；独有技术信息合并进保留解释。独立变量/索引、实际调用量、同名异义不删除或合并，missing/truncated/unresolved约束不变。无新增字段、台账或自动删除机制。

版本42流程图连线检查复用figure_registry的evidence/review_notes，不新增必填键或独立台账。记录原图号/媒体、冲突位置或相关起止边、对应来源和已核定流程、重排/绕线动作、改前改后SHA及最终稿显示位置。仅布局修改逐边核起止/方向/条件/回路保持；真实逻辑修正单独据源记录。若使用同图唯一成对续接，记该标记对对应的原有向边及其两个端点、方向和条件，标记不得混用成新业务节点。逻辑正确和媒体一致不能豁免交叉/重叠/遮挡；实际看最终图与当前DOCX渲染页后按既有figures/rendered_pages状态提交，未完成或未解决不得通过。
