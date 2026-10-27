# 多CLI执行

本页只维护CLI启动、执行证据、故障分流和进程收尾。主控先按[批处理流程](batch-workflow.md)入库；调度细节查[三池执行](three-stage-pools.md)，证据复用查[快速执行](fast-execution.md)，仅遇对应故障再查恢复条目。worker只读本attempt冻结阶段包，不把这些主控手册当作额外必读规范。

用户已明确允许同时调用多个CLI提高效率。后续新批次默认优先使用本通道，仍由一个总控维护队列；每个CLI只负责一个case的一个attempt。旧批次缺少`dispatch_transports`时仅允许原collaboration通道，不改写冻结策略。CLI不可用时，在未创建启动意图前使用实际可用的collaboration容量；不得换模型、绕过额度或更改全局安全设置。

## 启动与补位

按batch-workflow完成scan/selection/init后，由总控使用文档Python运行：

```text
python scripts/cli_pool.py run --manifest 批次manifest.json --controller 本批控制器标识 --workers 12
python scripts/cli_pool.py status --manifest 批次manifest.json
```

`run`是实际执行命令，会消费当前CLI登录账户的模型额度。用户无需手动逐案输入命令；总控保持运行并观察完成/交付事件。需要指定版本时追加`--executable 原生codex.exe绝对路径`。自动探测PATH中的原生exe或本机`LOCALAPPDATA/OpenAI/Codex/bin/*/codex.exe`的最新文件；不经cmd包装器或shell拼接启动。

上例`--workers 12`仅在本批确有12个可用名额时使用，不足时填写实际总容量。6/3/3池上限、占槽、补位及旧批兼容统一见[三池执行](three-stage-pools.md)；配置和历史4路小样均不证明实际12路容量，不在普通批次重做付费探针。

启动时一次非模型预检核验Windows、CLI必要参数和登录状态；这不等于文档工具验收。仅环境首次配置或变动时需要小样，不逐案做付费探针。渲染环境沿用runtime.md；模型与文档工具都不可用时如实报告，不写虚假成功。CLI执行使用workspace-write，完整保留宿主安全约束和用户配置，不注入ignore-rules、忽略配置、full-access或bypass参数。

新建批次每次`codex exec`显式选择draft=GPT-6.1-sol/high、review/revise=GPT-6.1-sol/xhigh，stdin传本案冻结prompt，`--json`保存真实事件，`-o`保存最终JSON。每次修订后的独立重验启动新进程/新会话，拒绝复用已记录的会话ID；不会resume修改者会话来签发独立通过。

## 证据与正确性

每个attempt保存`cli-job.json`、`cli-launch-intent.json`、`prompt.txt`、`dispatch.json`、原始`events.jsonl`、`worker-final.json`、实际`session.jsonl`、`cli-exit.json`和`cli-completion.json`。PID必须同时绑定创建时间和exe，避免PID复用。CLI退出后由supervisor封存五份证据的SHA；收稿核对事件、实际会话中的模型/强度/cwd/sandbox、原始prompt、真实最终回复和退出码，任一矛盾均不能收为成功。

这是总控采集的本地执行证据，不是外部签名或独立服务商证明。CLI成功只证明一次执行完成；仍调用原`parallel_batch.record`检查来源、attempt、DOCX与哈希、六项审查、最终渲染及全部页面证据。模型必须实际查看首次、改变和未审页；verified_scope_v1允许已实际审过且与当前渲染一致的页按原回执证据继承。模型自报pass、进程退出或缓存命中均不能单独成为交付条件。

总控只写manifest；每个supervisor只写本attempt的执行记录，不改业务状态。worker只写本attempt产物，不下派其他worker、CLI或后台服务；来源快照、旧稿和规则只读。按prepared实际路径读缓存、规则active条目和导航，必要时读取完整OMML；原有源N→稿N、零源不造式、所述前置及公式字形/结构规则完整保留。

## 完成即退出

每案独立验收通过且桌面交付成功后，同次收尾立即确认该案已不再需要的专属进程退出并释放资源，不等其他案件或整批结束，也不留后台模型、循环脚本或空闲服务。初稿完成不是项目完成；每个已完成阶段的专属工具也应及时退出，不为后续阶段保留上一模型进程。worker保存本阶段产物、关闭本次打开的文档/文件句柄、结束本阶段专属辅助任务后提交真实回执，提交后立即返回，不继续开工具或新任务。

正常CLI沿用现有communicate/wait→退出证据→supervisor封存并退出→collect/record释放槽位的顺序；不得收到早期回执就杀还在写证据的进程。helper与栅格化使用现有同步等待；已确认自然退出就算完成，不再次停止、不重启来验证。协作worker完成后及时返回并释放实际可释放的名额；不能把同一宿主应用当作单案worker终止。

只有确有存活残留时，先用本案现有启动/执行/渲染记录确认其专属归属；复核进程句柄或PID与创建时间、程序路径、命令行/工作目录及case/attempt绑定。优先正常关闭，必要时仅终止重新核定的原目标并回查退出；父子关系或进程名不能单独作为归属证明，PID复用不能命中新进程。不新增全机常驻扫描或平行台账，结果记原执行日志/收尾说明；退出未知或归属不明则明确记录未清理对象与原因，不猜已停、不误杀、不影响其他在途案件。清理失败不撤销已核实的交付，也不重新撰写/复查已通过文件。

共享WPS/Word宿主、用户打开的文档窗口、Codex应用及其他案件服务不属于本案专属进程；只关闭本次只读Document和已证明专属的helper。现行WPS后端没有独占宿主的证明，不调用Quit或按进程名终止共享Office。不得用全机taskkill、Stop-Process进程名匹配或无归属验证的进程树清除来代替收尾。

全批交付和必要收尾完成且没有活动attempt、阶段I/O或发布任务后，调度循环返回、线程池退出、释放控制器锁并结束本批控制器进程，不继续空轮询。仍有其他案件时只结束已完成案的专属进程，保留它们仍需使用的共享调度器。failed/needs_input如实为待处理，保留证据；本轮已经结束的专属执行进程照常退出，不冒充终稿全部完成。

## 中断、失败和恢复

重连日志判定、旧版误拒收初稿及恢复布局故障仅按[受控恢复](tool-fault-recovery.md#重连误拒收初稿的受控恢复)处理；JSONL字符边界见[日志格式判定](tool-fault-recovery.md#日志格式判定)。未知失败不套用已有窄恢复入口。

- 总控重启：先`status`再对同manifest运行`run`。独立supervisor可继续等待CLI并保存退出证据；已完成结果只收取，不重跑模型，已交付稿不重新生成。
- 锁与启动意图：整池唯一控制器锁、每attempt唯一supervisor锁及独占输出文件防止重复启动。有启动意图即不能按“尚未bind”直接取消；必须有确认原进程停止的证据。
- 单案来源损坏/准备失败：未提交占槽，记录该案failed，继续其他可用项目；来源哈希变化的案保留阻断原因，不能换快照悄悄继续。
- CLI非零退出、会话/回执不一致：停止新付费派发，其他已运行worker继续收稿。准确确认进程已退出的失败案才释放槽位；坏JSON、身份查询拒绝、缺退出证据均保留槽位并报告，不猜测已停止。
- 例外仅限已核验执行身份后明确抛出的CaseReceiptError，如不适用的verdict或缺少必填业务字段：保存cli-rejection.json并隔离本案，其他案继续补位，不自动重跑失败案。来源、身份、哈希、跨案路径和未知异常不属于此例外。worker提交前先按[回执预校验](worker-receipt.md)处理可发现的字段问题，工具不代填审查通过。
- 来源不足的有效needs_input回执：该案单列，其余案件继续。`run`存在阻断/待处理时返回非零码，不能把pending清单称作全部完成。
- 启动中断或权限/额度故障：先诊断本attempt日志及进程身份，修复原因后恢复；不能因耗时长启动第二份相同模型任务，也不能盲删锁或终止无关进程。若不可安全恢复，保留旧批次和稿件，按原恢复规则建立新批次。

## 减少重复步骤与测量边界

collect内日志解析复用、reserve按需校验、单轮manifest读取、收稿与历史审计的完整边界统一见[收稿与交付](fast-execution.md#收稿与交付)。每个验证边界保持现有检查，不新增跨轮通过缓存；阶段职责仍由冻结清单提供。

等待工具返回时采用30–60秒等待，不频繁重复打印全文。产能只按[真实批次统计](fast-execution.md#速度统计)报告；历史4路文件读写小样已记入[变更日志](../CHANGELOG.md)，不重复收费重跑，也不外推专利产能。

外部参考只用于状态恢复、分案并行和已验证数据复用等提效思路，不加载外部技能全文或采用其业务规则、模板、模型分工及交付验收要求。现行通道按本技能冻结策略和专利验收规则执行；普通批次无需访问外部参考仓库。CLI接口依据[OpenAI非交互执行文档](https://developers.openai.com/codex/noninteractive)及本机CLI参数验证。

## 内部工具故障

完整性或未知身份异常先停新付费派发并保全证据，确认内部工具缺陷后，由所属总控按[工具修复接续](tool-fault-recovery.md)修复、定向验证并继续已授权任务，无须重复请求批准。有效真实回执沿原流程收取；旧快照和真实回执不改，候选恢复不继承通过。
