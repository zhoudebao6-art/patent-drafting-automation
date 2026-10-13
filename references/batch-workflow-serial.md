> 历史串行流程：仅用于冻结execution=serial_per_project的既有批次及其retry。新批次使用batch-workflow.md和parallel_batch.py；不要按本页init创建新批次。

# 文件夹批量撰写

## 用户的一次操作

用户把要撰写的技术优化项目书放在一个文件夹，告知路径并要求执行。主控扫描、识别、按队列逐案完成，无需逐案再问许可。明确这是技能配置修改时只维护技能；不能把历史三组学习案例重新写一遍当作本批次。

允许一个项目一个 DOCX，也允许一项目一个子文件夹。先查看目录并按内容识别优化书；同案补充资料关联到本案，不当成独立案件。已有定稿、参考案例、~$锁文件、往期交付、重复副本不自动纳入。按用户给定顺序；未给顺序时按相对路径自然顺序冻结，每案保存稳定 ID 与顺序号。

PDF/旧DOC或压缩包先记录为需准备材料，转换或安全解压至批次外的专用 intake 目录并保存原文件及来源映射；再用其中的DOCX及附件生成选择清单。不覆盖输入。归档不以“一个压缩包”直接视为一个已经识别的优化书。关键身份歧义不能猜；明确材料继续处理，歧义项单列待确认。

## 队列与工作目录

使用 `scripts/batch_tool.py` 管理队列与验收记录；**脚本不调用模型，也不撰写或修改专利正文**，实际模型阶段由主控调用子代理完成。先 scan 读取清单，主控依据正文识别项目并生成 selection JSON，不要求用户手写 JSON：

新批次开始前按[运行环境](runtime.md)运行`render_docx_checked.py --preflight`；环境首次配置或改变时实际渲染小样。当前Windows修复配置使用已校验的原生LibreOffice，仍由官方`render_docx.py`生成真实Word页面，不把不存在的打包soffice路径作为唯一入口。

```json
{
  "projects": [
    {"title": "项目名称", "source_files": ["项目甲/技术项目优化书.docx"], "primary_source": "项目甲/技术项目优化书.docx"}
  ]
}
```

路径相对于 input 目录。选择清单中的项目名称取自当前资料，不借历史案例填充。source_files 含主文件和所有实际用于写作的支持文件。

```text
python scripts/batch_tool.py scan --input 输入目录 --out 扫描.json
python scripts/batch_tool.py init --input 输入目录 --work-root 当前工作区/batches --selection 选择.json
python scripts/batch_tool.py next --manifest 批次清单路径
```

init 冻结输入哈希、模型策略和 rules.json 版本，在唯一批次工作目录保存 policy.snapshot.json 与逐字节 rules.snapshot.json，manifest 记录 rule_version、rules_sha256。worker 使用该批次规则快照；后续全局规则更新不会改变运行中的批次。脚本检查快照完整性，但不会自动把规则全文或所有参考资料塞给worker。

工作目录、scan输出与输入分开，实际桌面由Windows桌面目录接口解析，不硬编码“C盘Desktop”。根目录与来源/产物路径拒绝符号链接、junction等重解析点；scan跳过子目录链接。每案中间稿、原始数据定位、公式图台账、审阅报告及派发证据均放在该批次内。`--desktop` 仅供有明确需要的路径覆盖和临时目录测试；正常交付省略该参数。

对每个项目完整执行后再进入下一个，不同时撰写多案，也不先批量起草全部项目再统一审查。当前案若确实无法继续则记录原因后进入后续案；不能因一案卡住就跳过其余确定可做的案。断点恢复读取清单和哈希，已完成的案不重复，已完成草稿从复查继续。

## 真实模型派发

当当前环境提供 `collaboration.spawn_agent` 时使用该工具；这些是工具参数示意，不是 shell 命令：

```json
{"task_name":"draft_case_001","fork_turns":"none","model":"gpt-6.1-sol","reasoning_effort":"high","message":"本项目draft阶段的完整任务与绝对资料路径"}
```

```json
{"task_name":"review_case_001_r1","fork_turns":"none","model":"gpt-6-astra","reasoning_effort":"high","message":"本项目review阶段的完整任务与绝对资料路径"}
```

```json
{"task_name":"revise_case_001_r1","fork_turns":"none","model":"gpt-6-astra","reasoning_effort":"high","message":"本项目revise阶段的完整任务与绝对资料路径"}
```

本批次每个需要模型工作的阶段都创建指定模型的 worker，避免继承不明确的当前模型。全历史fork可能不允许模型覆盖，所以使用 none 并显式提供该案所需内容。一个worker完成后释放并发槽再派下一个；主控不要占用所有槽后再等不存在的空位。

仅有技能文字或 JSON 配置不能切换模型。若本环境没有可指定模型的子代理工具，或派发失败、模型不可用、配额不足，保留队列与已生成文件，报告具体阶段和原因；不能改用当前模型完成内容后标记为指定模型。未经用户另行要求不新建侧栏聊天、不更改全局配置、不建立定时任务。

主控把**真实工具调用**的参数和成功结果原样整理到该阶段 `dispatch.json`：

```json
{
  "tool": "collaboration.spawn_agent",
  "context": {"batch_id": "当前manifest中的batch_id", "case_id": "001", "stage": "review"},
  "arguments": {"task_name": "review_case_001_r1", "fork_turns": "none", "model": "gpt-6-astra", "reasoning_effort": "high"},
  "result": {"task_name": "/root/review_case_001_r1"}
}
```

不复制示例伪造成功结果。arguments与result来自真实调用，context由主控绑定当前批次、项目和阶段。该文件是主控保存的可追溯日志；脚本只能验证字段、模型参数、成功返回、上下文和哈希的一致性，不能独立鉴定一份手写JSON是否来自真实工具，也不是服务端模型鉴证。若环境返回有效模型信息则另存原返回。模型在正文自称身份不作为证据。阶段回执的 agent_id 必须匹配真实返回的task_name，且末段名称与arguments.task_name一致；带错误结果、缺失绑定信息或重复使用同一agent_id的回执会被拒绝。dispatch.json须在本批次目录内。

## 给worker的任务

每次消息包括：角色 draft/review/revise、当前技能绝对路径、冻结规则版本、项目唯一 ID、全部来源路径及哈希、工作目录、指定输出路径、当前版本稿（如有）、必要的上一阶段检查项。提供文档技能、打包Python及本技能runtime.json中已核实的渲染路径。Windows按用户此次验证修复要求，通过render_docx_checked.py使用已验证的原生LibreOffice与隔离临时配置；仅修改子进程PATH，不接管用户应用。worker可用工具排版和计算，不再下派本阶段或启动其他项目。

**draft / Sol high**：根据优化书及既有技能规则完整撰写一案，生成DOCX和来源/术语/公式/附图台账，完成初步结构和页面检查，保存为本案draft版本。没有来源的关键公式、图或数据标缺口，不套用旧案例补齐。提交实际输出路径。

**review / Astra high**：独立读取源材料和稿件，不依赖Sol的“已通过”结论。检查完整章节与来源、所述引用链、权项与发明内容、公式及参数与算例、附图身份/顺序/真实性及删除联动、最终渲染页面。对实际稿件哈希出具 pass / fix_required / needs_input；有错明确位置、理由、修正依据及关联影响。本阶段只复查，修正文稿由随后同模型的revise阶段执行。

**revise / Astra high**：逐项直接修改，不能只出建议，也不能把修改退回Sol。另存修订版，记录发现与处理，保留原稿。关联章节和图号一起修正；最后修改后重新渲染。新版本再次交Astra/high复查，审阅哈希必须等于最终修订版哈希。

有明确可修问题就继续修正；连续两轮同一问题且没有新证据或修正进展，或必须新增关键技术事实才能“修好”时，标needs_input并写具体缺口，继续其他项目，不无限空转或强行pass。该停止条件不意味着只允许两轮修改；有进展时继续。

## 阶段回执与验收

主控保存worker产物和真实派发记录后调用：

```text
python scripts/batch_tool.py record --manifest 批次清单路径 --case 项目ID --receipt 阶段回执.json
```

draft/revise回执包含 `stage, agent_id, model, reasoning_effort, dispatch_evidence, artifact`；成功时verdict省略或为complete。artifact是批次目录内的实际DOCX绝对路径；每次另存新文件，不能复用原稿、其他阶段产物或输入的硬链接。脚本拒绝不可读ZIP、缺少word/document.xml等伪DOCX，但这不替代页面渲染验收。draft、review、revise及重验均必须在真实调用和回执中显式设置reasoning_effort为high；缺失、null或运行时默认值不符合本次用户要求，也不能只在回执中虚写high。review回执包含前述身份/派发字段，加 `reviewed_sha256, verdict, findings, checks`，不通过复查不接受。

review checks必须逐项记录 `source_support, antecedents, claims_alignment, formulas, figures, rendered_pages`。全部实际检查passed、没有未解决问题、当前文件和所审文件哈希一致才pass。未涉及公式或图时也要说明“已检查：源材料无该项”，不伪造一次计算或图片审核。每项结果的详细证据在本案review.json中保存；回执是摘要，不是专业正确性自动证明。

页面检查前用render_docx_checked.py的`--verify`核对当前稿件与PDF/PNG哈希，然后打开每一页并记录页数和发现。任一后续改动使旧渲染证据失效；仅生成render.json或记录`rendered_pending_visual_review`不能填rendered_pages=passed。

实际已成功派发的worker因缺资料或工具失败而返回needs_input/failed时，回执仍保留真实派发证据，并须有非空reason；不能被当成accepted。若派发本身失败、当前环境没有指定模型工具、输入已变化或尚未派发便确定缺关键材料，使用控制器事件：

```text
python scripts/batch_tool.py defer --manifest 批次清单路径 --case 当前项目ID --status needs_input --reason 具体缺口
python scripts/batch_tool.py defer --manifest 批次清单路径 --case 当前项目ID --status failed --reason 指定模型派发失败及真实原因
```

defer仅将最早未完成项目标为待处理，记录controller_defer事件，不伪造模型、agent_id或验收结论。next按当前项目核查来源；某案来源变化时可defer该案，继续其他确定可做的案。deliver只对已验收项目核查输入哈希，待处理案来源变化不会阻挡其他合格件。已验收项目的来源变化仍阻止交付；必须重新确认并建立新项目/批次，不能把旧审阅套到新输入。回执须使用脚本实际支持格式；不猜字段，先查看 `--help` 及脚本的校验报错。

## 桌面交付

每案最终复查通过并记录为accepted后立即交付，不等待其他案；全部项目终态后再调用一次同步最终清单：

```text
python scripts/batch_tool.py deliver --manifest 批次清单路径
```

实际桌面下新建唯一“专利撰写完成_批次标识”文件夹，示意：

```text
专利撰写完成_批次标识/
  已完成/
    001_项目名称/项目名称.docx
    002_项目名称/项目名称.docx
  待处理/各未完成案的具体说明
  交付清单（包含全部项目和状态）
  批次清单（可追溯记录）
```

同名项目由序号区分，不覆盖旧批次。只将经过最终复查的DOCX放进已完成；不把中间稿、历史案例、输入资料或未验收稿混入。待处理目录只写中文原因说明。首案建立桌面批次目录，后续验收通过一案便追加一案到同一目录；每案在临时位置复制并验证后发布，同步交付清单。失败保留已交付文件、工作区文稿和验收状态，可重试deliver；正常重复执行返回同一目录，遇到不属于本批次的目录或已交付文件被修改时拒绝覆盖。处理中稿件不复制。部分交付不影响next继续下一案，不因仍有未完成案而延迟已有成稿的交付。

主控若在pass后改正文，哈希校验将拒绝交付；不得靠重写哈希或回执绕过。当前CLI不提供重开accepted的命令，此批次只能交付与原通过哈希一致的文件。如有新的修改需求，保留原通过文件另开后续校核/修改任务，仍由Astra/high修改与复查，不能将改后文件混入此批交付。每案交付成功立即给用户该案链接和当前已交付数，再继续下一案；最终给桌面目录链接、总数/完成数/待处理数和具体剩余问题。全部可确认问题处理完毕才说本批次全部完成。

## 策略更新

### 故障修复后继续待处理案

旧批次已交付待处理说明且该案为needs_input/failed时，使用`retry`建立后续批次，保留旧manifest、回执及桌面交付不变：

```text
python scripts/batch_tool.py retry --manifest 旧批次清单 --case 原项目ID --reason 本次恢复依据 --work-root 当前工作区/batches
python scripts/batch_tool.py next --manifest 返回的新批次清单
```

工具先核对旧状态流转、派发证据、原始来源及历次稿件哈希；只继承可证实的稿件和失败前阶段，不继承通过标记。旧Sol草稿恢复到review时，新批次从Astra/high复查继续，不重新伪造Sol派发。新的review/revise仍需真实模型调用。`retry_origin`保存历史来源，新receipts用controller_retry事件标明继承。草稿和本案材料已改变时拒绝沿用旧结果；尚未终结/交付或已经通过的案件不使用此入口。恢复批次保持原冻结规则和模型策略，要求更换规则或技术材料时新建普通批次。

用户以后调整模型或输出习惯时，同步rules.json、batch-policy.json及本流程和测试；当前在运行的批次保持冻结策略，除非用户明确要求迁移该批次。一般话术更新不改变模型分工和桌面交付规则。

本次更新起，新建批次和规则v3批次的draft必须为Sol/high，review、revise及重验必须为Astra/high。为恢复历史进度，仅对已冻结规则v2、且规则哈希与策略快照校验一致的旧批次，保留其原有draft未指定推理强度的记录；旧策略不宣称满足新的high要求，也不能用于新建批次。旧派发原始参数若未传reasoning_effort，应如实保留遗漏状态，旧回执按原规则记null；新批次的调用及回执都必须显式为high。若用户明确要求把在途旧批次提升到新规则，应另建v3后续批次并按新模型分工重新执行需要更新的阶段，保留历史，不改写旧派发证据。

能力依据：当前工具的模型覆盖参数，以及[官方子代理说明](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[GPT-6.1 Sol模型说明](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[GPT-6 Astra模型说明](https://developers.openai.com/api/docs/models/gpt-6-astra)。实际可用性以当前派发结果为准。

交付时机更新依据：用户于2026-10-13明确要求“写完一件交付一件”。旧批次的模型、来源、规则快照与撰写阶段保持冻结；用户要求在途批次采用即时交付时，由该批总控记录此交付时机覆盖，不重写原快照。
