# 多项目滚动并行

v5.0新批次的worker_preparation=frozen_stage_packet_v1：init一次冻结本技能规范、程序与阶段清单，prepared.pipeline传实际路径；队列及CLI命令自动路由到该快照。新worker读取一次stage_guide及按需math_guide，公共Word工具生成支持的结构与客观台账，避免重复读取/编程。模型、六项审查、分案与即时交付不变。详见[快速执行](fast-execution.md)，旧无标志批次不自动迁移。

新批次使用scripts/parallel_batch.py和schema_version=2的[策略](batch-policy.json)，execution=rolling_parallel。旧批次execution=serial_per_project仍使用scripts/batch_tool.py和[历史流程](batch-workflow-serial.md)；不改写旧清单、派发或快照，正在测试的旧项目不自动迁移。

## 入库及隔离

扫描用户文件夹，按内容识别优化书及同案补充文件。历史定稿、参考案例、临时锁文件、往期交付和重复副本不自动入队。按用户顺序或相对路径自然顺序安排启动，允许乱序完成。DOCX直接入库，PDF/旧DOC/压缩包先在独立intake目录准备，保留来源映射，检查解压路径，不执行附件。身份有歧义的案单列，其余继续。

selection由总控生成，source_files和primary_source相对于input，例如：
```json
{"projects":[{"title":"当前项目名称","source_files":["项目甲/优化书.docx"],"primary_source":"项目甲/优化书.docx"}]}
```

工作目录和交付目录在input外。init冻结来源、规则、模型策略，每案独立cases/001/sources快照和cases/001/stages/<attempt_id>目录。稿件、图片、PDF、渲染、台账、dispatch和receipt均归本案；不使用公共draft.docx或review.json。--desktop仅供明确覆盖和测试，正常解析系统实际桌面。

```text
python scripts/parallel_batch.py scan --input 输入目录 --out 扫描.json
python scripts/parallel_batch.py init --input 输入目录 --work-root 工作目录 --selection 选择.json --controller 本批控制器标识
```

## 滚动补位

默认目标4个worker，最多8个在制项目，保留1个草稿推进机会。--capacity填写本批可使用的worker总额度，包含本批已占槽，不计总控和无关任务；不是当前空闲数。容量受真实工具额度约束，不能根据配置臆造名额。新批次优先由[多CLI执行池](cli-workers.md)自动执行整个循环；独立CLI容量与app子代理槽分别核实，不能机械共用槽位数。只有1个worker时如实顺序执行，不能声称真实多模型并行，也不能通过未授权的新聊天、全局设置或递归agent绕过额度。

总控维护事件循环：启动时连续reserve并派发多个不同项目；每个worker完成即登记回执；本案最终验收通过就立即deliver，然后直接reserve推进复查/修正并补位，不等待整批完成。plan仅用于需要观察队列的情况，不在每次reserve前后重复调用。有草稿与审查同时待办时兼顾两个方向，修正和重验及时推进，达到在制上限后先消化积压。满槽时等待完成事件，避免紧密轮询及固定长时间睡眠。单案缺资料或输入变化要具体记录，其他案继续。

每案内部最多一个活跃attempt。旧策略为draft→review→revise(需要时)→review；新review_fix_then_recheck策略允许review内一次有依据的修正返回revised，再由新Astra/high review独立重验，仍可在需要时使用独立revise路径。不同案可以同时处于不同阶段。各案按实际验收完成时间直接交付到同一文件夹根目录，交付台账保留原队列ID；规则版本18起正式名称须唯一且与文内主标题一致，冲突先解决，不自动加案号；旧快照保持旧命名。并行不能取代最终验收，不承诺绝对零错误或未经实测的加速倍数。

## 占槽和真实派发

新批次默认运行`cli_pool.py run`，由其调用reserve/bind/record/deliver，不同时另起手工控制器。以下步骤保留用于collaboration通道及诊断。CLI模式reserve使用`--transport codex_cli --prepare`，仅在冻结策略允许时生效；真实进程与会话证据契约见[多CLI执行](cli-workers.md)。

```text
python scripts/parallel_batch.py reserve --manifest 批次清单 --capacity 实际worker总额度 --controller 本批控制器标识 --prepare
```

reserve持久保存占槽并返回case_id、attempt、directory、task_name、context、spawn_arguments及source_snapshots。严格使用返回的上下文及路径。collaboration通道使用真实collaboration.spawn_agent，参数取spawn_arguments再补message：draft=gpt-6.1-sol/high；review/revise=gpt-6-astra/high；fork_turns=none。成功后保存真实tool/context/arguments/result为本attempt的dispatch.json；reserve本身不代表模型已经运行。

message包含本案来源快照及哈希、rules.snapshot.json、当前稿和哈希、attempt目录、阶段输出及上一轮问题。不得传入别案正文。worker不下派、不改共享规则或manifest，不写其他案目录。主控只编排，不能冒充指定模型写稿。

规则版本11起默认使用--prepare：同一条reserve命令同时返回worker_context.prepared，包含本案验证后的来源inspection/content/index路径、精简的冻结active规则、当前稿结构缓存及允许范围内的render候选。总控原样传入prepared；不再要求worker从整批文件夹重新寻找缓存。准备失败不提交占槽/派发，保留分析产物并处理具体错误。prepared仅是数据交接，不是复查通过。普通worker先读content和index，再按公式ID/段落定位读取完整OMML与来源，不能以索引替代原结构；使用rules.active.json，不加载冻结库history。细节见[提效流程](efficiency.md)。

```text
python scripts/parallel_batch.py bind --manifest 批次清单 --case 项目ID --attempt attempt_id --dispatch 本attempt/dispatch.json --controller 本批控制器标识
```

context必须逐字段等于reserve返回值，包括batch/case/stage/attempt及来源、规则、输入稿件哈希。真实派发失败则cancel预留并写原因；不能虚构成功记录。记录是总控日志，不是独立服务端模型鉴证。

## 阶段回执

draft写成本案DOCX及必要证据，完成一次结构预检。review先独立核对来源支持、所述、权项与发明内容、公式、附图；有阻碍通过的内容错误时，若新策略允许且修正依据明确，Astra在本review内另存修订DOCX并返回revised；否则返回fix_required走独立revise，事实不足则needs_input。页面检查如实标not_run，省去已知待改稿的提前渲染。内容可通过时再渲染或复用当前稿页面，核对全部最终页面。revise由Astra/high直接另存修订版，联动章节和图号；新Astra/high worker重验内容后对最终稿渲染并逐页验收。连续两轮同问题无进展或缺关键技术事实时记录缺口，不无限空转。

receipt含reserve完整context和stage/model/reasoning_effort/agent_id。写稿含artifact/artifact_sha256，输出在当前attempt内且不得使用原稿硬链接。审阅含reviewed_sha256/verdict/findings/checks。check键为source_support、antecedents、claims_alignment、formulas、figures、rendered_pages。pass要求全部passed且无未关闭发现。新策略review返回revised时，reviewed_sha256绑定改前输入，artifact/artifact_sha256绑定本review attempt的新独立DOCX，findings须非空，每项包含status=resolved或closed、非空location与correction_evidence；变更稿不会在同一回执中pass，必须由新的Astra/high review重验。规则版本6起，worker必读[所述与公式执行规范](terminology-and-formula.md)：draft先登记完整术语首次引入及源公式清单；review/revise由Astra/high逐处核对完整名词与本项/父链前置，逐式核对源N→稿N、零源不造式、完整符号与解释、无真实空白及历史格式。详细证据并入antecedents/formulas，不另设验收捷径；旧批次保持冻结规则。

pass另含render={artifact_sha256, evidence, evidence_sha256}，evidence指本次review或被审稿写稿attempt内的官方render.json。该文件绑定PDF与全部连续PNG哈希；脚本验证页数/图像完整性，Astra还须实际打开全部页面。旧稿pass、跨案路径、错模型/强度、旧attempt、重复回执均应拒绝。

规则版本10起，有公式的draft/review/revise必读[公式结构与版式标准](formula-layout-standard.md)。复查逐条核对结构诊断处置、源与稿的完整符号/解释、每条主公式与邻接解释的清晰显示；仅有“公式数量相同、XML无空格、整页缩略图正常”不足以formulas或rendered_pages通过。与既有六项审查合并，缓存按最终文件/提取器哈希复用，不增加重复全稿扫描。

```text
python scripts/parallel_batch.py record --manifest 批次清单 --case 项目ID --attempt attempt_id --receipt 本attempt/receipt.json --controller 本批控制器标识
```

总控是唯一状态写入者，串行提交回执，文件锁额外防止并发更新丢失。脚本完整性校验不替代技术语义审查。乱序回执只推进对应项目。

## 取消、恢复与待处理

成功连接重试被旧CLI判定误拒的draft批次，使用[CLI恢复入口](cli-workers.md)中的`recover_cli_batch.py`保留旧批次并另建批次继承完整核验的初稿。此操作不重写旧回执、不新增虚构模型调用、不继承通过状态；新的Astra/high仍独立验收。

collaboration未bind的预留可因派发失败cancel；CLI已有启动意图即须确认原进程退出，不能因尚未bind直接取消。已bind先停止对应worker并确认实际停止，再保存停止证据并cancel。仅发出中断请求不等于停止确认。不得因超时直接启动同案第二个worker，活动worker不能直接defer。

```text
python scripts/parallel_batch.py cancel --manifest 批次清单 --case 项目ID --attempt attempt_id --reason 原因 --stop-evidence 停止证据.json --controller 本批控制器标识
python scripts/parallel_batch.py defer --manifest 批次清单 --case 项目ID --status needs_input --reason 缺口 --controller 本批控制器标识
python scripts/parallel_batch.py summary --manifest 批次清单
```

stop-evidence含tool=collaboration.interrupt_agent、原context、arguments.target=agent_id、实际result及confirmed_stopped=true；只有真实确认停止才填写。collaboration reserved或尚无CLI启动意图的reserved取消不需要停止模型证据；其余CLI使用cli-workers规定的原进程退出证据。取消后的迟到回执拒绝，新attempt独立创建。恢复前先读取summary及真实worker状态，避免重复派发。不静默清除可能仍活跃的锁。对已终结待处理案，保留旧批次另建新批次，不能修改旧回执冒充通过。

## 减少重复工作

默认使用[提效流程](efficiency.md)中的缓存、简短回执和按需检查。正常直接reserve，避免plan→reserve重复读取；总控在worker工作时可提前用本地工具提取下一案来源。--compact只缩短返回内容，不删证据。

环境预检每批一次，依赖改变后重新检查和实测。来源快照按源SHA和提取器哈希复用提取结果，同名异内容不能命中；复查复用数据结构但独立核对原材料及OMML。一个evidence.json可以汇总来源/术语/公式/附图，避免重复台账。独立只读检查批量执行。

Word字节未变且render_docx_checked.py --verify通过时，直接复用同案已生成PDF/全部PNG，不重复转换。任何修订改变DOCX后重新渲染。最终Astra仍查看全部页、完成六项检查；可聚焦变更及依赖，不以省略验收换效率。

## 桌面交付

```text
python scripts/parallel_batch.py deliver --manifest 批次清单 --controller 本批控制器标识
```

每收到一案最终pass并成功record为accepted，立即运行deliver；不用等待其他项目、复查或活跃worker。首案建立唯一桌面批次目录，后续合格DOCX直接追加到同一目录根部，不创建“已完成/案号_项目名”等交付子目录。新output_contract=wording_title_v18按“正式完整发明名称.docx”预分配名称，Astra核对文内标题与冻结selection.title一致；不截短、清洗或加后缀。Windows大小写、非法字符及同名冲突须在选案阶段明确解决，不能改名掩盖；旧无此标志批次保持原稳定案号契约。乱序完成不改变已发布名称。每案复制后核对最终文件与通过审阅哈希，按案发布并同步工作目录中的清单，重复调用保持幂等且不覆盖用户已修改文件或其他批次。部分交付不能关闭批次或阻止继续record/reserve；待处理说明留工作目录并在聊天中报告，处理中稿件不进入桌面交付目录。一次交付成功后向用户给出该案文件链接与当前完成数，随后继续队列；交付失败单独说明已验收但尚未交付，保留稿件并重试。全部项目终态后再运行deliver补齐最终清单，无需重新复制已经核实的成稿。最终汇总总数、已交付数、待处理和占槽/绑定峰值（账面计数，不独立证明模型同时运行）。

原始资料在阶段运行中发生变化时，record保留真实worker回执和冻结快照，追加controller_input_change事件并将该案转needs_input，释放已完成的attempt；不能将返回needs_input当作阶段验收通过。交付前再次逐案核对原始资料，变化案单列而其他合格案继续。若复制过程中来源变化，不发布该案，隔离后重试deliver；其他先前已交付文件保留，不悄悄替换来源或重写旧通过记录。

交付时机更新依据：用户于2026-10-13明确要求“写完一件交付一件”。旧批次的模型、来源、规则快照与撰写阶段保持冻结；用户要求在途批次采用即时交付时，由该批总控记录此交付时机覆盖，不重写原快照。

交付布局更新依据：用户于2026-10-13要求“所有的项目完成后验收到同一个文件夹中即可，就像我刚发给你工作的文件夹里面放文件那种即可，通用修改”。新批次使用delivery.layout=flat_final_files，验收稿全部直接放同一文件夹根目录；仍通过一案即交付一案。桌面仅展示成稿；归属标记隐藏，逐案发布记录、清单及待处理说明留工作批次目录。只改变交付布局，撰写/复查case与attempt目录仍独立。旧无layout批次使用其冻结布局，除非用户明确要求迁移。
