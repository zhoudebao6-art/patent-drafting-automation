# 专利撰写自动化

把指定文件夹中的技术项目优化书逐案写成 Word 专利稿，完成复查、修正和页面验收后，集中交付到桌面。支持继续增加规则、对照新定稿改进话术，以及从故障中恢复未完成项目。

当前技能维护版本 **v3.2**，业务规则版本 **v3**，含 **39 条结构化规则**。此私有仓库保存完整技能及三组参考项目资料。

## 使用

技能安装在 Codex 的 `skills/patent-drafting-automation` 目录后，在支持指定模型子代理的 Codex 环境中调用：

```text
请使用 $patent-drafting-automation，批量处理“D:\待撰写项目”里的技术优化项目书，逐个完成撰写、复查及修改，全部成稿放到桌面。
```

每个项目完整执行以下流程后，才进入下一个项目：

1. GPT-6.1-sol / high 撰写。
2. GPT-6-astra / high 独立复查。
3. 有错由 GPT-6-astra / high 修改，再由 GPT-6-astra / high 复查。
4. 最终 Word 逐页验收通过后交付桌面；缺少关键材料的项目另列具体说明。

这是调用后执行的技能工作流，不是后台监控程序。Python 脚本管理队列和证据；实际撰写与复查由 Codex 通过真实子代理调用完成。模型不可调用时保存进度并报告，不自动替换模型。

## 文件

- [SKILL.md](SKILL.md)：技能入口。
- [规则库](references/rules.json)、[规则维护](references/rule-maintenance.md)：规则、适用范围、版本和更新方式。
- [撰写规范](references/writing-standard.md)、[技术校核](references/technical-review.md)：章节、话术、公式与附图检查。
- [批处理流程](references/batch-workflow.md)、[模型策略](references/batch-policy.json)：逐案串行、指定模型、复查及桌面交付。
- [运行环境](references/runtime.md)：本机文档转换配置及页面验收。
- [案例资料](references/corpus)、[来源清单](references/source-manifest.json)：三组优化书与定稿，仅用作规则和风格参考。
- [scripts](scripts)：文档提取、批次管理、渲染验证、规则更新及测试。

长期更新可以直接提出：`请使用 $patent-drafting-automation，把以下要求加入长期规则：……`。一次性项目修改不会自动写入长期规则。

## 运行环境与验证范围

`references/runtime.json` 保存当前 Windows 机器上已验证的 Python、官方文档渲染脚本、LibreOffice 和 Poppler 路径。换电脑或软件升级后，应按运行环境说明重新探测、配置和实际渲染。仓库及发行 ZIP 不附带这些运行程序。

2026-10-13 的 v3.2 验证结果：66 项自动测试通过；一份明确标注的虚拟样稿实际完成 Sol/high 撰写、Astra/high 复查和修正、重新渲染、独立最终复查及桌面交付，全部 6 页通过验收。三组历史案例没有被宣称为已完成全页视觉验收；新的真实项目仍需逐案核对技术来源和最终页面。

完整 v3.2 备份 ZIP 可在本仓库的 Releases 中下载。日后更新以实际安装目录为准，同步经过验证的改动到仓库，保留历史版本。
