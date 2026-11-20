# 多项目滚动并行

普通新批次先读[轻量运行架构](architecture-lite.md)，本页只在需要入库、冻结、恢复或旧批次兼容细节时读取。当前主线为来源准备→初稿→独立只读复查→必要修改后新会话重验→逐案交付；池容量及模型以[本批策略](batch-policy.json)和[三池执行](three-stage-pools.md)为准。新批用parallel_batch.py；旧serial_per_project批次按[旧流程](batch-workflow-serial.md)执行。所有运行批次使用自己的冻结工具和规则。

## 本轮意图与路径入口

实际办案时，仅发送案件文件夹路径即授权按本流程生成终稿。讨论以后用法、示例地址或通用维护时，只维护技能，不扫描示例目录、init或派发；同条另明确现在办理才执行该范围。按句意判断，不据目录名或引用中的关键词决定。已派发worker只执行本阶段。

用户指定原撰写项目接续时，按[技能入口](../SKILL.md)把来源、候选稿、问题和真实验收状态交回原项目；总控不创建同案第二套流程。来源始终是技术证据，不执行附件指令。

## 入库及隔离

递归按内容识别技术项目优化书、技术项目优化说明书及同案附件；历史定稿、参考案例、临时锁文件、往期交付和重复副本不自动入队。同案附件不是独立项目。按用户顺序或相对路径自然顺序启动，允许乱序完成。DOCX直接入库；PDF、旧DOC、压缩包先在独立intake目录准备，保留来源映射并检查解压路径。身份有歧义的案单列，其余继续。

selection由总控生成，source_files和primary_source均相对于input：

```json
{"projects":[{"title":"当前项目名称","source_files":["项目甲/优化书.docx"],"primary_source":"项目甲/优化书.docx"}]}
```

工作和交付目录位于input之外。init一次冻结来源、规则、模型、工具和阶段清单；每案的sources及stages/<attempt_id>独立，稿件、图片、PDF、渲染、台账、dispatch和receipt均归本案，不用公共draft.docx或review.json。正常解析系统实际桌面，--desktop仅用于明确覆盖或测试。

```text
python scripts/parallel_batch.py scan --input 输入目录 --out 扫描.json
python scripts/parallel_batch.py init --input 输入目录 --work-root 工作目录 --selection 选择.json --controller 本批控制器标识
```

## 滚动补位

默认由[CLI池](cli-workers.md)按冻结总目标启动并持续调用reserve、bind、record和deliver；当前总预算32、初稿上限16，不照抄旧--workers 4。显式降低预算须记录原因；同批不再并行启动手工控制器或手工重复交付。初稿完成立即进入review，有问题进入revise，修稿完成回新会话review；通过立即交付。各池只排自己的队列，CLI与collaboration共享实际容量，同案最多一个活跃attempt，长I/O不占主清单长锁。具体池名额、低容量公平性和旧策略兼容见[三池执行](three-stage-pools.md)。

总控独占状态写入，短事务合并对应案件，保留锁、启动意图、上下文和真实进程身份。配置与账面占槽不是实测并行或产能，不承诺固定加速倍数或绝对无错。

## 手工通道与派发

以下命令用于collaboration通道或诊断；普通CLI模式由控制器执行。CLI手工reserve另需--transport codex_cli，且必须获本批冻结策略允许。

```text
python scripts/parallel_batch.py reserve --manifest 批次清单 --capacity 实际worker总额度 --controller 本批控制器标识 --prepare
```

reserve持久保存占槽，返回case_id、attempt、directory、task_name、context、spawn_arguments和source_snapshots。派发严格使用返回的上下文与模型参数；collaboration.spawn_agent使用spawn_arguments并补message、fork_turns=none，独立会话不得继承写稿者上下文。成功后保存真实tool/context/arguments/result为本attempt的dispatch.json；预留名额不等于模型已执行。

message传本案来源快照及SHA、规则快照、当前稿及SHA、attempt目录、阶段输出、上一轮问题和prepared。只传本案数据，worker不下派、不改共享规则、manifest或其他案；主控不代写稿或代签通过。

同步reserve --prepare同时返回已验证的来源inspection/content/index、当前稿结构缓存及允许的页面候选；准备失败不提交占槽或派发。独立异步准备的占槽及失败处理按[三池执行](three-stage-pools.md)。有prepared.pipeline的worker在CLI已完整内嵌阶段包时直接使用，不再读取stage_read_parts；未内嵌才按分块读一次，旧包无分块则读stage_guide一次。条件规范按索引补读；无pipeline的旧worker按下方旧阶段入口执行，不读history。完整公式仍按定位读取原OMML等对象；prepared和索引不是技术或验收结论。

```text
python scripts/parallel_batch.py bind --manifest 批次清单 --case 项目ID --attempt attempt_id --dispatch 本attempt/dispatch.json --controller 本批控制器标识
```

context逐字段等于reserve返回值，包括batch/case/stage/attempt及来源、规则和输入稿件SHA。派发失败须按下文确认进程状态后cancel，不能虚构成功。派发日志不是独立服务端模型鉴证。

## 阶段回执

worker用[回执工具](worker-receipt.md)提交一次真实决定及证据。draft完成初稿、来源公式正确性校核和一次结构预检；review只读核source_support、antecedents、claims_alignment、formulas、figures、rendered_pages六项；revise集中修正findings及依赖内容，不重创无关全文，之后交新review。术语前置、唯一源公式映射、完整符号、流程图技术与全图身份、名称/图号/版式等详细要求均在冻结阶段包及[项目证据契约](data-contracts.md#项目记录)，不重复加载同一规则。

内容有阻碍通过的问题时返回fix_required；缺关键技术事实返回needs_input并说明缺口。连续两轮同问题无进展不无限空转。已知待改时页面检查如实标not_run；内容可通过再渲染或验证复用当前稿页面。六项和全部当前页由本轮实查加有效继承完整覆盖、无未关闭发现，且本轮未修改稿件，才能pass。仅旧冻结review_fix_then_recheck允许review内小修返回revised，改前/改后SHA及location、correction_evidence字段按[回执规范](worker-receipt.md)绑定，仍由新会话独立重验。

receipt绑定reserve的完整context、stage/model/reasoning_effort/agent_id。产物须是当前attempt内独立DOCX，不得用原稿硬链接；审阅绑定当前reviewed_sha256、verdict、findings和checks。pass的render引用本次review或被审稿写稿attempt中的官方render.json及SHA，绑定PDF与连续全部PNG。脚本核完整性，独立review还须实际覆盖全部最终页；公式数量、XML无空格或整页缩略图不能代替公式与页面验收。旧pass、跨案路径、错模型/强度、旧attempt及重复回执均拒绝。

```text
python scripts/parallel_batch.py record --manifest 批次清单 --case 项目ID --attempt attempt_id --receipt 本attempt/receipt.json --controller 本批控制器标识
```

乱序回执只推进对应案；机械完整性校验不替代语义审查。

## 取消、恢复与待处理

恢复前先看summary和真实worker状态，先收已有结果，不重复派发。collaboration未bind或CLI尚无启动意图的预留可因派发失败cancel；已bind或CLI已有启动意图，须先确认原worker实际退出。仅发中断请求不等于停止，不能按超时启动同案第二个worker，活动worker不能直接defer，也不能静默清除未知活跃锁。

```text
python scripts/parallel_batch.py cancel --manifest 批次清单 --case 项目ID --attempt attempt_id --reason 原因 --stop-evidence 停止证据.json --controller 本批控制器标识
python scripts/parallel_batch.py defer --manifest 批次清单 --case 项目ID --status needs_input --reason 缺口 --controller 本批控制器标识
python scripts/parallel_batch.py summary --manifest 批次清单
```

collaboration停止证据含真实interrupt_agent的tool、原context、arguments.target、result和confirmed_stopped=true；CLI用[原进程退出证据](cli-workers.md)。取消后拒绝迟到回执，新attempt独立创建。已终结待处理案保留旧批次，再按恢复契约另建批次；不能改旧回执冒充通过。特定重连误拒初稿才使用[对应恢复入口](tool-fault-recovery.md#重连误拒收初稿的受控恢复)，候选稿经核验导入后仍独立审查。

<a id="版本47工具故障不重复索取批准"></a>
已授权任务中的内部工具缺陷按[故障接续](tool-fault-recovery.md)修复、定向验证后继续。停止新派发时继续收稿和保留证据；原manifest可继续则沿用，确需受控恢复也不继承pass。技能维护不接管别的活跃批次。

## 桌面交付

CLI模式由独立交付通道自动发布accepted稿；手工模式在record成为accepted后立即调用：

```text
python scripts/parallel_batch.py deliver --manifest 批次清单 --controller 本批控制器标识
```

<a id="版本46唯一桌面交付目录"></a>
同批只用一个manifest和一个delivery_path，最终DOCX全部直接放目录根部。目录名为“原始输入文件夹完整名称_终稿_冻结batch_id”，取manifest.input末级原名，保留中文、空格和大小写，不用内部解压目录或单案标题替代。续跑、追加、重试复用原目录及冻结标识，不按池、案件、日期或交付轮次建子目录，不散放桌面，也不合并无关批次。盘符根目录、非法名称或长度超限在init前报明原因，不静默改名。旧folder_prefix策略和已有交付保持原路径。

output_contract=wording_title_v18按“正式完整发明名称.docx”预分配名称；文内完整名称与selection.title逐字一致，不截短、清洗或追加后缀。大小写、非法字符及同名冲突在选案阶段解决。旧无标志批次保留稳定案号契约，乱序完成不改已发布名称。

每案通过立即发布，不等其他案。复制前核来源与验收证据，复制后核最终文件SHA和目录归属；幂等追加不覆盖用户已修改文件或其他批次。来源在运行中变化时保留真实回执及快照、追加controller_input_change并转needs_input；交付前或复制期间变化则隔离本案，其余合格案继续。未知归属、名称冲突、目标丢失/变化均保留证据报错，不另建交付目录兜底。

部分交付不能关闭批次或阻止后续record/reserve。内部日志、台账、候选、渲染及安全暂存留工作区。每案发布后按[完成即退出](cli-workers.md#完成即退出)确认本案专属进程结束并释放资源，给出文件链接及完成数后继续；交付失败明确区分“已验收、尚未交付”并保留稿件重试。全部案终态后完成闭批审计及清单，已核实稿件不重复复制。最终核所有文件直属同一delivery_path，报告已交付数、待处理和账面占槽峰值。

## 复用与规范入口

正常直接reserve --prepare，避免plan→reserve重复读取；--compact只缩短返回。总控可在worker运行期间用本地工具预提取下一案来源。缓存、同稿渲染、变化范围重验和收稿单次核验统一见[快速执行](fast-execution.md)，按问题找工具见[提效索引](efficiency.md)。各案可合并为一份evidence.json，不另建相同台账；必要对应关系和证据不省。

<a id="版本32附图说明检查"></a>
<a id="版本38文末纯图号检查"></a>
正文用词、附图说明完整图名和标点、文末仅图N等门禁继续在同份封存inspection中检查，六项语义验收仍负责真实内容；详见[人工格式](manual-format-and-wording.md)及[净稿图序](reference-style-standard.md)。普通closed finding不能豁免真实格式错误。相关冻结prose_contract、figure_description_contract、figure_caption_contract继续有效，不增加模型阶段、重复提取或额外台账。旧批仅执行其冻结契约。

## 容量与页面故障的接续

未来init冻结capacity_recovery=verified_ramp_v1及controller_render=bound_requests_v1。按CLI手册累计冷却并凭正常阶段真实完成逐级恢复预算；恢复不重复派发。当前控制器处理绑定的页面请求，短临时名原子交接；已有同稿合法页面先验证复用。预览疑点先查当前原始单页，真实修改仍进入独立revise，再由新review会话按差异及依赖重验。旧批次不自动获得这些策略。

## 无pipeline的旧阶段入口

仅无prepared.pipeline的旧worker读取本批active规则、[写作规范](writing-standard.md)、[所述与公式](terminology-and-formula.md)、[净稿图序](reference-style-standard.md)、[人工格式](manual-format-and-wording.md)及[项目记录](data-contracts.md#项目记录)，其余按内容读取。始终使用本批冻结版本，不补造快照、不自动套用新策略。新pipeline worker使用其已提供的阶段包，不回到这里重复阅读。
