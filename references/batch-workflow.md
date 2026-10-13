# 多项目滚动并行

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
python scripts/parallel_batch.py plan --manifest 批次清单 --capacity 实际worker总额度
```

## 滚动补位

默认目标4个worker，最多8个在制项目，保留1个草稿推进机会。--capacity填写本批可使用的worker总额度，包含本批已占槽，不计总控和无关任务；不是当前空闲数。容量受真实工具额度约束，不能根据配置臆造名额。只有1个worker时如实顺序执行，不能声称真实多模型并行，也不能通过未授权的新聊天、全局设置或递归agent绕过额度。

总控维护事件循环：启动时连续reserve并派发多个不同项目；每个worker完成即登记回执；本案最终验收通过就立即deliver，然后推进其他案复查/修正、重新plan并补位，不等待整批完成。有草稿与审查同时待办时兼顾两个方向，修正和重验及时推进，达到在制上限后先消化积压。满槽时等待完成事件，避免紧密轮询及固定长时间睡眠。单案缺资料或输入变化要具体记录，其他案继续。

每案内部仍是draft→review→revise(需要时)→review，最多一个活跃attempt。不同案可以同时处于不同阶段。各案按实际验收完成时间交付，同一目录内保留原队列ID。并行不能取代最终验收，不承诺绝对零错误或未经实测的加速倍数。

## 占槽和真实派发

```text
python scripts/parallel_batch.py reserve --manifest 批次清单 --capacity 实际worker总额度 --controller 本批控制器标识
```

reserve持久保存占槽并返回case_id、attempt、directory、task_name、context、spawn_arguments及source_snapshots。严格使用返回的上下文及路径。使用真实collaboration.spawn_agent，参数取spawn_arguments再补message：draft=gpt-6.1-sol/high；review/revise=gpt-6-astra/high；fork_turns=none。成功后保存真实tool/context/arguments/result为本attempt的dispatch.json；reserve本身不代表模型已经运行。

message包含本案来源快照及哈希、rules.snapshot.json、当前稿和哈希、attempt目录、阶段输出及上一轮问题。不得传入别案正文。worker不下派、不改共享规则或manifest，不写其他案目录。主控只编排，不能冒充指定模型写稿。

```text
python scripts/parallel_batch.py bind --manifest 批次清单 --case 项目ID --attempt attempt_id --dispatch 本attempt/dispatch.json --controller 本批控制器标识
```

context必须逐字段等于reserve返回值，包括batch/case/stage/attempt及来源、规则、输入稿件哈希。真实派发失败则cancel预留并写原因；不能虚构成功记录。记录是总控日志，不是独立服务端模型鉴证。

## 阶段回执

draft写成本案DOCX及必要证据，结构预检后渲染最后版本。review独立读取来源和稿件，核对来源支持、所述、权项与发明内容、公式、附图、全部最终页面。revise由Astra/high直接另存修订版，联动章节和图号，重新渲染后再交新的Astra/high worker重验。连续两轮同问题无进展或缺关键技术事实时记录缺口，不无限空转。

receipt含reserve完整context和stage/model/reasoning_effort/agent_id。写稿含artifact/artifact_sha256，输出在当前attempt内且不得使用原稿硬链接。审阅含reviewed_sha256/verdict/findings/checks。check键为source_support、antecedents、claims_alignment、formulas、figures、rendered_pages。pass要求全部passed且无未关闭发现。规则版本6起，worker必读[所述与公式执行规范](terminology-and-formula.md)：draft先登记完整术语首次引入及源公式清单；review/revise由Astra/high逐处核对完整名词与本项/父链前置，逐式核对源N→稿N、零源不造式、完整符号与解释、无真实空白及历史格式。详细证据并入antecedents/formulas，不另设验收捷径；旧批次保持冻结规则。

pass另含render={artifact_sha256, evidence, evidence_sha256}，evidence指本次review或被审稿写稿attempt内的官方render.json。该文件绑定PDF与全部连续PNG哈希；脚本验证页数/图像完整性，Astra还须实际打开全部页面。旧稿pass、跨案路径、错模型/强度、旧attempt、重复回执均应拒绝。

```text
python scripts/parallel_batch.py record --manifest 批次清单 --case 项目ID --attempt attempt_id --receipt 本attempt/receipt.json --controller 本批控制器标识
```

总控是唯一状态写入者，串行提交回执，文件锁额外防止并发更新丢失。脚本完整性校验不替代技术语义审查。乱序回执只推进对应项目。

## 取消、恢复与待处理

未bind的预留可因派发失败cancel；已bind先停止对应worker并确认实际停止，再保存停止证据并cancel。仅发出中断请求不等于停止确认。不得因超时直接启动同案第二个worker，活动worker不能直接defer。

```text
python scripts/parallel_batch.py cancel --manifest 批次清单 --case 项目ID --attempt attempt_id --reason 原因 --stop-evidence 停止证据.json --controller 本批控制器标识
python scripts/parallel_batch.py defer --manifest 批次清单 --case 项目ID --status needs_input --reason 缺口 --controller 本批控制器标识
python scripts/parallel_batch.py summary --manifest 批次清单
```

stop-evidence含tool=collaboration.interrupt_agent、原context、arguments.target=agent_id、实际result及confirmed_stopped=true；只有真实确认停止才填写。reserved取消不需要停止模型证据。取消后的迟到回执拒绝，新attempt独立创建。恢复前先读取summary及真实worker状态，避免重复派发。不静默清除可能仍活跃的锁。对已终结待处理案，保留旧批次另建新批次，不能修改旧回执冒充通过。

## 减少重复工作

环境预检每批一次，依赖改变后重新检查和实测。来源快照按源SHA和提取器哈希复用提取结果，同名异内容不能命中；复查复用数据结构但独立核对原材料及OMML。一个evidence.json可以汇总来源/术语/公式/附图，避免重复台账。独立只读检查批量执行。

Word字节未变且render_docx_checked.py --verify通过时，直接复用同案已生成PDF/全部PNG，不重复转换。任何修订改变DOCX后重新渲染。最终Astra仍查看全部页、完成六项检查；可聚焦变更及依赖，不以省略验收换效率。

## 桌面交付

```text
python scripts/parallel_batch.py deliver --manifest 批次清单 --controller 本批控制器标识
```

每收到一案最终pass并成功record为accepted，立即运行deliver；不用等待其他项目、复查或活跃worker。首案建立唯一桌面批次目录，后续合格稿追加到同一目录的已完成中，同名案以ID区分。每案复制后核对最终文件与通过审阅哈希，按案发布并同步清单，重复调用保持幂等且不覆盖用户已修改文件或其他批次。部分交付不能关闭批次或阻止继续record/reserve；待处理案仅写具体说明，处理中稿件不放入已完成。一次交付成功后向用户给出该案文件链接与当前完成数，随后继续队列；交付失败单独说明已验收但尚未交付，保留稿件并重试。全部项目终态后再运行deliver补齐最终清单，无需重新复制已经核实的成稿。最终汇总总数、已交付数、待处理和占槽/绑定峰值（账面计数，不独立证明模型同时运行）。

原始资料在阶段运行中发生变化时，record保留真实worker回执和冻结快照，追加controller_input_change事件并将该案转needs_input，释放已完成的attempt；不能将返回needs_input当作阶段验收通过。交付前再次逐案核对原始资料，变化案单列而其他合格案继续。若复制过程中来源变化，不发布该案，隔离后重试deliver；其他先前已交付文件保留，不悄悄替换来源或重写旧通过记录。

交付时机更新依据：用户于2026-10-13明确要求“写完一件交付一件”。旧批次的模型、来源、规则快照与撰写阶段保持冻结；用户要求在途批次采用即时交付时，由该批总控记录此交付时机覆盖，不重写原快照。
