"""Freeze reusable worker tools and compile lossless, single-read stage packets.

This is data preparation, not a model review or a claim of semantic equivalence.
Rules remain verbatim; section routing only removes controller-only schema prose.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MODE = 'frozen_stage_packet_v1'
CORE = ('writing-standard.md', 'claim-families-and-benefits.md', 'terminology-and-formula.md',
        'reference-style-standard.md', 'manual-format-and-wording.md')
MATH = ('technical-review.md', 'formula-layout-standard.md', 'formula-detail-standard.md')
STAGES = ('draft', 'review', 'revise')
LEAN = 'lean_execution_v24'
# Explicit controller routes only. Unknown/new/project rules stay with workers.
CONTROLLER_RULES = frozenset({'UPDATE-01', 'UPDATE-02', 'BATCH-01', 'BATCH-02',
                              'BATCH-07', 'BATCH-09', 'BATCH-11', 'BATCH-12'})


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def local(path, root):
    p, root = Path(path).absolute(), Path(root).resolve(strict=True)
    if not p.is_relative_to(root):
        raise ValueError('Bundle path escapes its batch')
    for node in (p, *p.parents):
        if node.is_symlink() or (node.exists() and getattr(node, 'is_junction', lambda: False)()):
            raise ValueError('Linked bundle paths prohibited')
        if node == root:
            break
    actual = p.resolve(strict=True)
    if not actual.is_relative_to(root) or not actual.is_file() or actual.stat().st_nlink != 1:
        raise ValueError('Independent regular bundle file required')
    return actual


def selected_files(skill):
    """One copy per batch; exclude historic rule backups and test-only files."""
    skill = Path(skill).resolve(strict=True)
    selected = [skill/'SKILL.md']
    selected += [p for p in (skill/'scripts').glob('*.py') if not p.name.startswith('test_')]
    for p in (skill/'references').rglob('*'):
        if p.is_file() and 'rule-backups' not in p.relative_to(skill).parts:
            selected.append(p)
    if (skill/'assets').exists():
        selected += [p for p in (skill/'assets').rglob('*') if p.is_file()]
    return sorted(selected)


def normalize_links(text):
    # Packets live at the snapshot root; the original references live one level down.
    def replace(match):
        target = match.group(2)
        if '://' in target or target.startswith(('#', '/', 'references/', '../')):
            return match.group(0)
        return '['+match.group(1)+'](references/'+target+')'
    return re.sub(r'\[([^\]]+)\]\(([^)]+)\)', replace, text)


def project_contract(text):
    """Only the worker project schema; batch mutation is controller-owned."""
    start = text.find('## 项目记录')
    end = text.find('\n## 批次记录', start)
    if start < 0 or end < 0:
        raise ValueError('Project contract headings changed; update packet extraction explicitly')
    return text[start:end].strip()


def rule_block(rule):
    # No LLM summarisation. Preserve instruction/check/exceptions and source semantics.
    required = ('id', 'origin', 'scope', 'section', 'level', 'instruction', 'check', 'exceptions', 'evidence')
    if any(k not in rule for k in required):
        raise ValueError('Active rule missing required fields')
    lines = [f"### {rule['id']} [{rule['level']}; {rule['scope']}; {rule['section']}; {rule['origin']}]",
             rule['instruction'], '检查：'+str(rule['check'])]
    if rule['exceptions']:
        lines.append('例外：'+canonical(rule['exceptions']))
    return '\n\n'.join(lines)


def build_packet(snapshot, rules, stage):
    if stage not in STAGES:
        raise ValueError('Unknown worker stage')
    snapshot = Path(snapshot)
    active = [r for r in rules['rules'] if r['status'] == 'active']
    if len({r['id'] for r in active}) != len(active):
        raise ValueError('Duplicate active rule ID')
    policy = json.loads((snapshot/'references/batch-policy.json').read_text(encoding='utf-8-sig'))
    lean = policy.get('execution_optimization') == LEAN
    config = policy['models'][stage]
    identity = config['model']+'/'+str(config['reasoning_effort'])
    stage_text = {
        'draft': '形成完整初稿。先完成本案内容与结构预检；不模拟另一轮独立复查。使用通用生成器可支持的结构时，不另写排版及台账脚本。生成器已经提供有效inspection缓存时直接复用，不再执行一遍；回执使用返回的实际output路径和SHA。自行生成时才按prepared.draft_inspection_cache运行一次inspect_docx。',
        'review': '独立完成六项审查。先审内容，有唯一依据的小问题可集中修正并返回revised，之后必须由新派发的复查模型独立重验；只有最终文件未被本轮修改且六项及所有最终页实际通过，才能pass。',
        'revise': '按问题与依赖关系修改，保留未受影响的内容与来源。不重新创作全文；修订后不能自己签发pass，交新派发的复查模型独立重验。',
    }[stage]
    if stage == 'review' and policy.get('review_mode', 'review_only') == 'review_only':
        stage_text = ('独立只读完成六项审查。发现问题返回fix_required和精确findings，由独立revise工作池修改；'
                      '本轮不修改DOCX、不返回revised。修改后回到review池由新会话独立重验。'
                      '只有当前稿六项及全部最终页实际检查通过，才能pass；未做项写not_run。')
    sections = [f'# 本批{stage}执行清单（规则v{rules["version"]}）', f'本阶段模型/强度：{identity}。'+stage_text,
                '本清单包含全部active规则的原文指令、检查及例外，以及必需写作规范。正常阶段读一次本清单，不再重复读取rules.active.json、整个SKILL或其已包含的参考文件。疑点按索引查询；不能用此安排跳过宿主要求或本案来源。',
                '## 原文规则（含适用范围）']
    routed = [r for r in active if lean and r['id'] in CONTROLLER_RULES and r['scope']=='global'
              and r['section'] in {'batch', 'delivery', 'update'}]
    included = [r for r in active if r not in routed]
    if lean:
        sections[2] = ('本清单保留全部适用worker规则的原文、检查与例外。控制器专用规则原文见worker-controller.md；'
                       '只写本attempt，来源/其他案/规则只读，派发/状态/交付由总控执行。'
                       '按prepared.pipeline.stage_read_parts顺序完整读取各块一次，不先整读再重读分块。'
                       '原文规范仍在冻结references中，worker-standard末表指定复杂情形必须读取的原节；不能跳过宿主技能或来源。')
    for r in included:
        sections.append(rule_block(r))
    sections += ['## 规则来源与完整字段',
                 '来源证据与完整规则见references/rules.json；本清单不改变任何规则优先级。规则更新由总控处理，worker不得修改批次或规则。']
    spans = []
    if lean:
        guard = json.loads((snapshot/'references/worker-standard-coverage.json').read_text(encoding='utf-8'))
        expected = {*CORE, 'worker-standard.md'}
        if set(guard['files']) != expected:
            raise ValueError('Worker standard coverage inventory changed')
        for name, expected_sha in guard['files'].items():
            if sha(snapshot/'references'/name) != expected_sha:
                raise ValueError('Worker standard reference changed; review compact coverage before compilation: '+name)
    for name in (('worker-standard.md',) if lean else CORE):
        raw = (snapshot/'references'/name).read_text(encoding='utf-8-sig')
        sections += ['# 规范原文：'+name, normalize_links(raw)]
        spans.append({'file': 'references/'+name, 'mode': 'complete', 'sha256': sha(snapshot/'references'/name)})
    data = (snapshot/'references/data-contracts.md').read_text(encoding='utf-8-sig')
    sections += ['# 本案证据字段（原文）', normalize_links(project_contract(data))]
    spans.append({'file': 'references/data-contracts.md', 'mode': 'project_section',
                  'sha256': sha(snapshot/'references/data-contracts.md')})
    sections += ['# 按需资料与执行工具',
                 '有公式或附图时读worker-math.md一次；不能因只看到零个OMML就判定无公式，仍检查文本/图片/OLE。它包含技术校核和公式结构规范的完整原文。',
                 '制作或修改Word遵守当前可用documents技能。通用生成器的输入与调用见references/authoring-toolkit.md；运行环境见references/runtime.json及runtime.md。生成器仅负责可验证的机械生成，语义由指定模型完成。',
                 '来源content/index、完整数学对象及当前稿的实际路径见本attempt的prepared/handoff.json。只写本attempt，其他文件只读。复用缓存数据不继承任何通过结论。',
                 '最后依据实际工作填写回执模板；自动生成的哈希/索引不是检查通过证据。未做检查写not_run，未解决问题不得交付。']
    output = '\n\n'.join(sections).rstrip()+'\n'
    coverage = {'stage': stage, 'active_rule_ids': [r['id'] for r in active],
                'rule_payload_sha256': hashlib.sha256(canonical(active).encode('utf-8')).hexdigest(),
                'normative_sections': spans, 'semantic_approval': False}
    if lean:
        coverage.update(included_rule_ids=[r['id'] for r in included],
                        controller_rule_ids=[r['id'] for r in routed],
                        reference_coverage=guard,
                        reading_mode='nonoverlapping_parts',
                        scope='Applicable rule text is verbatim; detailed standards remain frozen and conditionally required')
    return output, coverage


def write_parts(root, stage, text, limit=7000):
    """Lossless bounded reads: concatenation reproduces the whole packet exactly."""
    directory = root/'worker-parts'; directory.mkdir(exist_ok=True)
    chunks, current = [], ''
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current: chunks.append(current); current = ''
            chunks.append(line[:limit]); line = line[limit:]
        if current and len(current)+len(line) > limit:
            chunks.append(current); current = ''
        current += line
    if current: chunks.append(current)
    result = []
    for n, chunk in enumerate(chunks, 1):
        path = directory/f'{stage}-{n:02}.md'
        path.write_text(chunk, encoding='utf-8', newline='')
        result.append({'file': path.relative_to(root).as_posix(), 'sha256': sha(path), 'characters': len(chunk)})
    return result


def freeze(skill, batch, rules_bytes, policy):
    skill, batch = Path(skill).resolve(strict=True), Path(batch).resolve(strict=True)
    root = batch/'skill-snapshot'
    root.mkdir(exist_ok=False)
    original = {}
    for src in selected_files(skill):
        src = local(src, skill)
        relative = src.relative_to(skill)
        target = root/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        before = sha(src)
        shutil.copyfile(src, target)
        if sha(src) != before or sha(target) != before:
            raise ValueError('Skill changed during snapshot')
        original[relative.as_posix()] = before
    # The frozen batch rules/policy are authoritative even in a test/recovery setup.
    (root/'references/rules.json').write_bytes(rules_bytes)
    (root/'references/batch-policy.json').write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding='utf-8')
    rules = json.loads(rules_bytes.decode('utf-8-sig'))
    coverage = {}
    for stage in STAGES:
        text, coverage[stage] = build_packet(root, rules, stage)
        (root/f'worker-{stage}.md').write_text(text, encoding='utf-8', newline='')
        if policy.get('execution_optimization') == LEAN:
            coverage[stage]['read_parts'] = write_parts(root, stage, text)
    if policy.get('execution_optimization') == LEAN:
        routed = [r for r in rules['rules'] if r['status']=='active' and r['id'] in CONTROLLER_RULES
                  and r['scope']=='global' and r['section'] in {'batch', 'delivery', 'update'}]
        (root/'worker-controller.md').write_text('\n\n'.join(rule_block(r) for r in routed)+'\n', encoding='utf-8')
        active = [r for r in rules['rules'] if r['status']=='active']
        (root/'rules.active.json').write_text(json.dumps({'schema_version': 1, 'rule_version': rules['version'],
            'rules_sha256': hashlib.sha256(rules_bytes).hexdigest(), 'rules': active}, ensure_ascii=False), encoding='utf-8')
    (root/'worker-math.md').write_text('\n\n'.join('# '+n+'\n\n'+normalize_links(
        (root/'references'/n).read_text(encoding='utf-8-sig')) for n in MATH), encoding='utf-8')
    (root/'packet-coverage.json').write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding='utf-8')
    files = {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}
    manifest = {'schema_version': 1, 'mode': MODE, 'rules_sha256': hashlib.sha256(rules_bytes).hexdigest(),
                'policy_sha256': hashlib.sha256(canonical(policy).encode('utf-8')).hexdigest(),
                'files': files, 'source_files': original}
    path = root/'snapshot.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'path': str(path), 'sha256': sha(path)}


def verify(m, full=True):
    record = m.get('skill_snapshot')
    if not record:
        if m['policy'].get('worker_preparation') == MODE:
            raise ValueError('Frozen stage bundle is missing')
        return None
    batch = Path(m['batch_dir']).resolve(strict=True)
    path = local(record['path'], batch)
    if path.parent.name not in {'skill-snapshot', 'recovered-skill-snapshot'} or path.parent.parent != batch or path.name != 'snapshot.json':
        raise ValueError('Foreign frozen skill bundle')
    if sha(path) != record['sha256']:
        raise ValueError('Frozen skill manifest changed')
    data = json.loads(path.read_text(encoding='utf-8'))
    if (data.get('mode') != MODE or data.get('rules_sha256') != m['rules_sha256']
            or data.get('policy_sha256') != hashlib.sha256(canonical(m['policy']).encode('utf-8')).hexdigest()):
        raise ValueError('Frozen skill rule/policy identity mismatch')
    if full:
        for rel, expected in data['files'].items():
            if sha(local(path.parent/rel, path.parent)) != expected:
                raise ValueError('Frozen skill file changed: '+rel)
    return path.parent


def worker_paths(m, stage):
    root = verify(m)
    if root is None:
        return None
    if stage not in STAGES:
        raise ValueError('Unknown worker stage')
    result = {'root': str(root), 'stage_guide': str(root/f'worker-{stage}.md'),
            'stage_guide_sha256': sha(root/f'worker-{stage}.md'),
            'math_guide': str(root/'worker-math.md'), 'toolkit': str(root/'scripts/authoring_toolkit.py'),
            'toolkit_guide': str(root/'references/authoring-toolkit.md'),
            'snapshot_sha256': m['skill_snapshot']['sha256']}
    if m['policy'].get('execution_optimization') == LEAN:
        coverage = json.loads((root/'packet-coverage.json').read_text(encoding='utf-8'))[stage]
        result.update(execution_optimization=LEAN,
                      stage_read_parts=[dict(path=str(root/p['file']), sha256=p['sha256'], characters=p['characters'])
                                        for p in coverage['read_parts']],
                      rules_active=str(root/'rules.active.json'),
                      runtime_config=str(root/'references/runtime.json'),
                      evidence_query=str(root/'scripts/evidence_query.py'),
                      receipt_tool=str(root/'scripts/worker_receipt.py'))
    return result


def restore_snapshot(old, new):
    """Recovery retains the old frozen runtime; no acceptance result is inherited."""
    root = verify(old)
    new.pop('skill_snapshot', None)
    if root is not None:
        target = Path(new['batch_dir'])/'recovered-skill-snapshot'
        shutil.copytree(root, target)
        new['skill_snapshot'] = {'path': str(target/'snapshot.json'), 'sha256': old['skill_snapshot']['sha256']}
        verify(new)


def redirect_cli(manifest_path, name, argv):
    """New batch commands execute their frozen code, not a later installed version."""
    if name not in {'parallel_batch.py', 'cli_pool.py'}:
        raise ValueError('Unsupported frozen CLI')
    m = json.loads(Path(manifest_path).read_text(encoding='utf-8-sig'))
    root = verify(m)
    if root is None:
        return None
    target = root/'scripts'/name
    if Path(sys.argv[0]).resolve() == target.resolve():
        return None
    # CREATE_NO_WINDOW needs explicit inherited stream handles on Windows.
    # Otherwise an otherwise successful frozen command can silently lose stdout.
    return subprocess.call([sys.executable, '-X', 'utf8', str(target), *argv],
                           stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
