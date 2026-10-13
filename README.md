# 专利撰写自动化

v4.0采用多项目滚动并行、持续补位和独立分区。业务规则版本v4，42条结构化规则。原模型分工保持：GPT-6.1-sol/high撰写；GPT-6-astra/high复查、修正及再次复查。

## 使用

```text
请使用 $patent-drafting-automation，并行处理“D:\待撰写项目”中的技术项目优化书，按可用名额持续补位，每案独立复查和修改，通过验收的Word集中交付桌面。
```

不同项目可以同时处于撰写、复查、修正阶段，不再等待前一项目全部验收。每案内部保持有序且只允许一个活跃阶段；独立case/attempt目录和哈希绑定防止错案、错稿、旧回执混用。

默认目标4个worker、最多8个在制项目，实际容量以运行环境允许的子代理名额为准。只有1个worker时顺序执行并如实报告。脚本管理队列和证据，模型由总控真实调用；不是双击即运行的后台程序。指定模型或容量不可用时不虚构结果、不静默更换模型。

## 文件与维护

- [SKILL.md](SKILL.md)：技能入口。
- [并行流程](references/batch-workflow.md)、[策略](references/batch-policy.json)：占槽、派发、补位、取消、验收及交付。
- [规则库](references/rules.json)、[规则维护](references/rule-maintenance.md)：话术、范围、来源和版本记录。
- [写作标准](references/writing-standard.md)、[技术校核](references/technical-review.md)：原有来源、所述、公式、图文要求。
- [运行环境](references/runtime.md)：Windows Word页面转换及哈希验证。
- [案例资料](references/corpus)、[来源清单](references/source-manifest.json)：三组历史参考项目。

已有serial_per_project批次使用旧batch_tool.py及其冻结规则恢复，不被升级自动迁移。新批次使用parallel_batch.py。长期改规则可说“把下面要求加入技能长期规则：……”。

效率优化包括事件完成即补位、复用未改变的来源提取和Word渲染、按等待时间推进复查、限制草稿积压。最终六项Astra审查及全部页面验收保留，不承诺绝对零错误或未经测量的加速比例。

## 验证与环境

v3.2历史验证：66项测试通过，一份虚拟样稿实际完成指定模型撰写、复查、修订、6页验收和桌面交付。v4.0新增多项目调度、隔离、并发写入、补位和故障恢复测试；测试中的模拟dispatch仅用于状态机验证，不代表实际多模型并发已经完成。每批summary报告占槽与绑定峰值；该计数本身不独立证明模型实际同时运行。

runtime.json为本机已验证工具路径；当前文档插件26.1011.11113。换电脑或升级后需重新探测和实际渲染，ZIP不包含Python、LibreOffice或Poppler程序。三组历史参考文件未被宣称为全页视觉验收通过。
