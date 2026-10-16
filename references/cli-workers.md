# 多 CLI 通用执行通道（v4.7）

v5.0新增frozen_stage_packet_v1准备模式，见[快速执行](fast-execution.md)。新批次的run/status及队列命令自动转入本批skill-snapshot脚本；worker接收单份阶段清单和公共生成工具路径，不重复打开清单已经包含的原始规范。旧批次缺少该标志时仍按原契约运行，不虚构完整历史工具快照。以下CLI身份、收稿及恢复要求保持。

用户已明确允许同时调用多个CLI提高效率。后续新批次默认优先使用本通道，仍由一个总控维护队列；每个CLI只负责一个case的一个attempt。旧批次缺少`dispatch_transports`时仅允许原collaboration通道，不改写冻结策略。CLI不可用时，在未创建启动意图前使用实际可用的collaboration容量；不得换模型、绕过额度或更改全局安全设置。

## 启动与补位

按batch-workflow完成scan/selection/init后，由总控使用文档Python运行：

```text
python scripts/cli_pool.py run --manifest 批次manifest.json --controller 本批控制器标识 --workers 12
python scripts/cli_pool.py status --manifest 批次manifest.json
```

`run`是实际执行命令，会消费当前CLI登录账户的模型额度。用户无需手动逐案输入命令；总控保持运行并观察完成/交付事件。需要指定版本时追加`--executable 原生codex.exe绝对路径`。自动探测PATH中的原生exe或本机`LOCALAPPDATA/OpenAI/Codex/bin/*/codex.exe`的最新文件；不经cmd包装器或shell拼接启动。

版本35默认配置6个初稿、3个只读复查、3个独立修改worker，总目标12，在制最多24。上例--workers 12仅在本批确有12个可用名额时使用，主控先核实际CLI账户与机器资源；不足时填写更低值。新三池省略--workers会拒绝启动，旧批次省略时保持历史默认4。reserved与bound、CLI与本批collaboration均共同占槽，不跨池借到超限。已有4路实测不证明12路容量，状态和派发记录才说明实际运行数；不在普通批次额外付费探针。

版本36新批次由stage_pool_runner维持三个独立准备/收稿通道及独立单写交付通道。初稿完成即排review，fix_required即排revise，修订后回review新会话重验；通过稿即排桌面交付，有空闲名额立即接手。长I/O在主清单锁外，单案短事务合并，accepted不阻塞reserve；准备中占槽，完整证据门禁不变。旧v29/v35无pool_execution标记时保留单件收取、交付后补位及故障drain。具体规则见[三池执行](three-stage-pools.md)。

启动时一次非模型预检核验Windows、CLI必要参数和登录状态；这不等于文档工具验收。仅环境首次配置或变动时需要小样，不逐案做付费探针。渲染环境沿用runtime.md；模型与文档工具都不可用时如实报告，不写虚假成功。CLI执行使用workspace-write，完整保留宿主安全约束和用户配置，不注入ignore-rules、忽略配置、full-access或bypass参数。

新建批次每次`codex exec`显式选择draft=GPT-6.1-sol/high、review/revise=GPT-6.1-sol/xhigh，stdin传本案冻结prompt，`--json`保存真实事件，`-o`保存最终JSON。每次修订后的独立重验启动新进程/新会话，拒绝复用已记录的会话ID；不会resume修改者会话来签发独立通过。

## 证据与正确性

每个attempt保存`cli-job.json`、`cli-launch-intent.json`、`prompt.txt`、`dispatch.json`、原始`events.jsonl`、`worker-final.json`、实际`session.jsonl`、`cli-exit.json`和`cli-completion.json`。PID必须同时绑定创建时间和exe，避免PID复用。CLI退出后由supervisor封存五份证据的SHA；收稿核对事件、实际会话中的模型/强度/cwd/sandbox、原始prompt、真实最终回复和退出码，任一矛盾均不能收为成功。

这是总控采集的本地执行证据，不是外部签名或独立服务商证明。CLI成功只证明一次执行完成；仍调用原`parallel_batch.record`检查来源、attempt、DOCX与哈希、六项审查、最终渲染及全部页面证据。模型必须实际查看首次、改变和未审页；verified_scope_v1允许已实际审过且与当前渲染一致的页按原回执证据继承。模型自报pass、进程退出或缓存命中均不能单独成为交付条件。

总控只写manifest；每个supervisor只写本attempt的执行记录，不改业务状态。worker只写本attempt产物，不下派其他worker、CLI或后台服务；来源快照、旧稿和规则只读。按prepared实际路径读缓存、规则active条目和导航，必要时读取完整OMML；原有源N→稿N、零源不造式、所述前置及公式字形/结构规则完整保留。

## 中断、失败和恢复

2026-10-14用户批准修复成功重连被误拒收的问题。执行核验只允许严格匹配`Reconnecting... n/m (stream disconnected before completion: ...)`的中间连接重试：必须在唯一turn.started之后，且后续有新的最终agent_message及唯一turn.completed；所有原始日志保持原字节。未知error、turn.failed、非零退出、真实会话失败及模型/强度/prompt/封存哈希不一致仍拒绝。连接恢复不代表专利验收，原六项内容和页面门禁不变。

对旧程序仅因该判定而终结的成功draft，确认整池已退出后运行`python scripts/recover_cli_batch.py --manifest 旧manifest --work-root 工作根目录 --controller 新控制器标识`。工具重新核验原始完整执行证据、冻结Sol/high、来源和稿件哈希，保留旧manifest、attempt与拒收记录；另建批次复制验证成功的初稿，只设为drafted，沿用原冻结来源/内容规则/模型策略。恢复批次必须按原冻结策略派发新的独立review，不能继承pass或用原Sol会话复查。恢复证据与新稿哈希绑定；幂等指针不能指向无关批次。该入口仅支持上述draft误拒收，不泛化恢复未知失败、修改稿或旧通过结论。

恢复初稿放在本案`stages/recovered-draft`导入目录，不能假造一次模型attempt。若旧版恢复布局导致全部案件在派发前被`Foreign or changed current artifact`拦截，可显式加`--new-generation`：工具只允许无活动/历史attempt、无启动意图/派发记录且原因完全匹配的布局准备失败，保留旧manifest和恢复指针历史后重建。未知失败不得借此重启。该路径须测试真实`reserve --prepare`及交接证据核验。

- 总控重启：先`status`再对同manifest运行`run`。独立supervisor可继续等待CLI并保存退出证据；已完成结果只收取，不重跑模型，已交付稿不重新生成。
- 锁与启动意图：整池唯一控制器锁、每attempt唯一supervisor锁及独占输出文件防止重复启动。有启动意图即不能按“尚未bind”直接取消；必须有确认原进程停止的证据。
- 单案来源损坏/准备失败：未提交占槽，记录该案failed，继续其他可用项目；来源哈希变化的案保留阻断原因，不能换快照悄悄继续。
- CLI非零退出、会话/回执不一致：停止新付费派发，其他已运行worker继续收稿。准确确认进程已退出的失败案才释放槽位；坏JSON、身份查询拒绝、缺退出证据均保留槽位并报告，不猜测已停止。
- v5.7例外仅限已核验执行身份后明确抛出的CaseReceiptError，如不适用的verdict或缺少必填业务字段：保存cli-rejection.json并隔离本案，其他案继续补位，不自动重跑失败案。来源、身份、哈希、跨案路径和未知异常不属于此例外。worker提交前先按[回执预校验](worker-receipt.md)处理可发现的字段问题，工具不代填审查通过。
- 来源不足的有效needs_input回执：该案单列，其余案件继续。`run`存在阻断/待处理时返回非零码，不能把pending清单称作全部完成。
- 启动中断或权限/额度故障：先诊断本attempt日志及进程身份，修复原因后恢复；不能因耗时长启动第二份相同模型任务，也不能盲删锁或终止无关进程。若不可安全恢复，保留旧批次和稿件，按原恢复规则建立新批次。

## 减少重复步骤与测量边界

v5.8新模式在同一次collect中处理退出但未bind的worker时，bind与record共用临时completion_scope：首次完整解析，后次重新核对执行/封存文件实际SHA及文件身份、attempt/dispatch/模型/上下文和进程退出后复用解析结果。离开本次作用域即清除，变更或重新封存不能命中；独立收取/重启仍重新完整解析。不得扩大成mtime缓存或接受worker自报通过。其余收稿、改稿独立重验、立即交付和故障隔离边界不变。

reserve先按状态和公平顺序排列候选，再校验到首个可派案即停止，同一事务不再重复验证已选案。显式plan仍输出全部阻断；bind、record、deliver各自的完整性边界继续保留。正常CLI收稿以record作为完整执行证据和业务门禁，不在调用前再做一轮相同的全量日志校验；恢复时bind仍需单独确认派发身份。

本地池每0.5秒观察进程/文件状态，不发模型轮询请求；worker遇到运行中工具用对应等待工具等待30–60秒，避免高频短轮询和重复打印全文。缓存/有效页面按现有规则复用，语义结论不复用。普通运行不执行本技能回归测试。

v5.7在同一空闲轮只加载验证一次manifest，供collect及status读取；状态变更、异常和交付后立即重载，下一轮重新验证，不能跨轮缓存通过结论。阶段prompt按职责派发：draft完成初稿和结构检查，最终全页视觉验收归review；review修改后仍由新独立review重验。非重叠规则块路径由prepared提供，不整读后重复分块。

池重启时已交付历史完整核验一次，之后才允许新付费派发；若审计失败，记录具体原因并停止新派发和自动交付，仍接收已在运行的worker结果。末案以failed/needs_input结束也执行批次收尾核验，不依赖有新accepted案。回执局部字段错误延迟到全部已提供路径及可核哈希/身份检查之后分类，不能用缺字段掩盖跨案或错误哈希。

2026-10-14在本机真实运行4个隔离的文件读写与计算任务，两个Sol/high、两个Astra/high，峰值4个CLI进程，均退出0且实际会话和各案结果匹配；总墙钟307.523秒。首次工具执行存在约5分钟等待和大量缓存输入，未证明整批专利产能或成本提高，不能宣称4倍速度。这是执行通道实测，不是4个专利项目的全流程验收。已完成探针不重复收费重跑，之后靠真实批次阶段时间评估吞吐。

外部参考只用于状态恢复、分案并行和已验证数据复用等提效思路，不加载外部技能全文或采用其业务规则、模板、模型分工及交付验收要求。现行通道按本技能冻结策略和专利验收规则执行；普通批次无需访问外部参考仓库。CLI接口依据[OpenAI非交互执行文档](https://developers.openai.com/codex/noninteractive)及本机CLI参数验证。

## 版本47已确认工具缺陷

未知执行/身份异常仍先停新付费派发并保全证据；确认是内部工具误判后，由所属总控按[工具修复接续](tool-fault-recovery.md)修复、验证并继续，无须重复请求批准。有效真实回执直接沿原收稿；已失败且回执无效不能重写旧最终回复，候选初稿通过受控恢复后仍由新会话独立复查。原快照只读，单案唯一worker、来源/模型/六项及唯一交付目录不变。既有recover_cli_batch.py仍只支持其明确故障，不把新问题硬套旧入口。

版本49提效沿用既有多CLI三池；入口改为角色路由，已在阶段包提供的细则不重复铺陈。同次JSON证据以一份字节核SHA并解析，下一核验仍重新读取，保留全部独立验收与即时交付。详见[单次证据读取](fast-execution.md#v622-单次证据读取与角色入口)。
