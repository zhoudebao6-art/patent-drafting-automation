"""No paid model calls: frozen worker packets, identity and legacy routing."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import batch_tool as b
import parallel_batch as q
import prepare_handoff as handoff
import cli_pool
import stage_bundle as s
from test_batch_tool import docx
from test_parallel_batch import POLICY


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.skill = self.root/'skill'; (self.skill/'references').mkdir(parents=True)
        (self.skill/'scripts').mkdir()
        self.actual = Path(__file__).resolve().parent.parent
        for name in (*s.CORE, *s.MATH, 'data-contracts.md'):
            shutil.copyfile(self.actual/'references'/name, self.skill/'references'/name)
        (self.skill/'SKILL.md').write_text('fixture skill', encoding='utf-8')
        (self.skill/'references/authoring-toolkit.md').write_text('fixture guide', encoding='utf-8')
        for name in ('authoring_toolkit.py', 'parallel_batch.py', 'cli_pool.py'):
            (self.skill/'scripts'/name).write_text('# fixture only', encoding='utf-8')
        self.rules = b.read(self.actual/'references/rules.json')
        self.rules_bytes = json.dumps(self.rules, ensure_ascii=False).encode('utf-8')
        self.policy = dict(copy.deepcopy(POLICY), worker_preparation=s.MODE)
        self.batch = self.root/'batch'; self.batch.mkdir()
        record = s.freeze(self.skill, self.batch, self.rules_bytes, self.policy)
        self.m = {'batch_dir': str(self.batch), 'policy': self.policy, 'rules_sha256':
                  __import__('hashlib').sha256(self.rules_bytes).hexdigest(), 'skill_snapshot': record}

    def test_every_active_rule_keeps_verbatim_semantics_and_exceptions(self):
        root = s.verify(self.m)
        coverage = b.read(root/'packet-coverage.json')
        for stage in s.STAGES:
            packet = (root/f'worker-{stage}.md').read_text(encoding='utf-8')
            active = [r for r in self.rules['rules'] if r['status']=='active']
            self.assertEqual(coverage[stage]['active_rule_ids'], [r['id'] for r in active])
            for rule in active:
                self.assertIn(rule['instruction'], packet)
                self.assertIn(str(rule['check']), packet)
                if rule['exceptions']: self.assertIn(s.canonical(rule['exceptions']), packet)
            self.assertFalse(coverage[stage]['semantic_approval'])

    def test_live_reference_edits_do_not_change_frozen_stage(self):
        root = s.verify(self.m); before = (root/'worker-draft.md').read_bytes()
        (self.skill/'references/manual-format-and-wording.md').write_text('changed live rules', encoding='utf-8')
        self.assertEqual((s.verify(self.m)/'worker-draft.md').read_bytes(), before)

    def test_incremental_policy_routes_review_without_changing_legacy_or_writer_roles(self):
        root = s.verify(self.m)
        for enabled in (False, True):
            with self.subTest(incremental=enabled):
                policy = dict(self.policy)
                if enabled: policy['review_reuse'] = 'verified_scope_v1'
                b.atomic(root/'references/batch-policy.json', policy)
                packet, _ = s.build_packet(root, self.rules, 'review')
                self.assertEqual('后轮按prepared.review_reuse' in packet, enabled)
                reserved = {'attempt': {'stage':'review','id':'test-review'},
                    'spawn_arguments': {'model':'gpt-6.1-sol','reasoning_effort':'xhigh'},
                    'receipt_template': {}, 'source_snapshots': [],
                    'worker_context': {'review_mode':'review_only',
                        'prepared': {'pipeline': {'root':str(root),'stage_guide':'guide',
                            'math_guide':'math','toolkit_guide':'toolkit-guide','toolkit':'toolkit'}}}}
                if enabled: reserved['worker_context']['review_reuse']='verified_scope_v1'
                prompt = cli_pool.worker_prompt(reserved)
                self.assertIn('冻结阶段清单执行', prompt)
                self.assertIn('review只读', prompt)
                self.assertNotIn('撰写/修改时读toolkit-guide', prompt)
                self.assertEqual('核差异与依赖' in packet, enabled)
                self.assertIn('独立只读', packet)
                for writer in ('draft', 'revise'):
                    reserved['attempt']['stage'] = writer
                    prompt = cli_pool.worker_prompt(reserved)
                    self.assertIn('撰写/修改时读toolkit-guide', prompt)
                    self.assertIn('线性索引不代替完整公式', prompt)

    def test_wps_helper_is_explicitly_frozen_without_arbitrary_powershell(self):
        helper = self.skill/'scripts/wps_readonly_pdf.ps1'
        helper.write_text('# runtime helper fixture', encoding='utf-8')
        unrelated = self.skill/'scripts/unrelated.ps1'
        unrelated.write_text('# excluded', encoding='utf-8')
        selected = s.selected_files(self.skill)
        self.assertIn(helper, selected)
        self.assertNotIn(unrelated, selected)
        other_batch = self.root/'wps-batch'; other_batch.mkdir()
        record = s.freeze(self.skill, other_batch, self.rules_bytes, self.policy)
        manifest = dict(self.m, batch_dir=str(other_batch), skill_snapshot=record)
        root = s.verify(manifest)
        self.assertEqual((root/'scripts/wps_readonly_pdf.ps1').read_bytes(), helper.read_bytes())
        (root/'scripts/wps_readonly_pdf.ps1').write_text('changed frozen helper', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Frozen skill file changed'):
            s.verify(manifest)

    def test_compiler_does_not_drop_unknown_or_project_scoped_rules(self):
        rules = copy.deepcopy(self.rules)
        new = copy.deepcopy(rules['rules'][0]); new.update(id='FUTURE-01', scope='project:测试', instruction='只在指定项目适用；保留例外。', exceptions=['名称中保留原词'])
        rules['rules'].append(new)
        packet, coverage = s.build_packet(s.verify(self.m), rules, 'draft')
        self.assertIn(new['instruction'], packet); self.assertIn(new['scope'], packet)
        self.assertIn('FUTURE-01', coverage['active_rule_ids'])

    def test_changed_packet_runtime_and_manifest_are_refused(self):
        root = s.verify(self.m)
        for rel in ('worker-review.md', 'scripts/authoring_toolkit.py', 'references/manual-format-and-wording.md'):
            path = root/rel; before = path.read_bytes(); path.write_bytes(before+b'changed')
            with self.assertRaisesRegex(ValueError, 'file changed'): s.verify(self.m)
            path.write_bytes(before)
        (root/'snapshot.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manifest changed'): s.verify(self.m)

    def test_policy_rules_and_foreign_bundle_cannot_be_mixed(self):
        for field in ('policy', 'rules_sha256'):
            changed = copy.deepcopy(self.m)
            if field=='policy': changed[field]['concurrency']['target_workers']=3
            else: changed[field]='0'*64
            with self.assertRaisesRegex(ValueError, 'identity mismatch'): s.verify(changed)
        other = self.root/'other'; other.mkdir()
        changed = dict(self.m, batch_dir=str(other))
        with self.assertRaises(ValueError): s.verify(changed)

    def test_new_mode_fails_without_snapshot_legacy_keeps_old_route(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            s.verify({k:v for k,v in self.m.items() if k!='skill_snapshot'})
        legacy = dict(self.m, policy=copy.deepcopy(POLICY)); legacy.pop('skill_snapshot')
        self.assertIsNone(s.verify(legacy))

    def test_recovery_keeps_original_runtime_and_legacy_does_not_gain_it(self):
        target = self.root/'recovery'; target.mkdir()
        new = dict(copy.deepcopy(self.m), batch_dir=str(target))
        s.restore_snapshot(self.m, new)
        self.assertEqual((s.verify(new)/'worker-draft.md').read_bytes(),
                         (s.verify(self.m)/'worker-draft.md').read_bytes())
        legacy = dict(self.m, policy=copy.deepcopy(POLICY)); legacy.pop('skill_snapshot')
        new['policy']=legacy['policy']; s.restore_snapshot(legacy, new)
        self.assertNotIn('skill_snapshot',new)

    def test_cli_redirect_is_real_subprocess_to_frozen_script_and_not_recursive(self):
        manifest = self.batch/'manifest.json'; b.atomic(manifest,self.m)
        with patch.object(s.sys,'argv',[str(self.actual/'scripts/parallel_batch.py')]), patch.object(s.subprocess,'call',return_value=7) as call:
            self.assertEqual(s.redirect_cli(manifest,'parallel_batch.py',['summary','--manifest',str(manifest)]),7)
            self.assertEqual(Path(call.call_args.args[0][3]),s.verify(self.m)/'scripts/parallel_batch.py')
            self.assertIs(call.call_args.kwargs['stdout'],s.sys.stdout)
            self.assertIs(call.call_args.kwargs['stderr'],s.sys.stderr)
        with patch.object(s.sys,'argv',[str(s.verify(self.m)/'scripts/parallel_batch.py')]), patch.object(s.subprocess,'call') as call:
            self.assertIsNone(s.redirect_cli(manifest,'parallel_batch.py',[])); call.assert_not_called()

    def test_invalid_contract_structure_requires_explicit_update(self):
        with self.assertRaises(ValueError): s.project_contract('missing headings')
        bad=copy.deepcopy(self.rules); del bad['rules'][0]['exceptions']
        with self.assertRaises(ValueError): s.build_packet(s.verify(self.m),bad,'draft')

    def test_frozen_subprocess_preserves_stdout_stderr_utf8_and_exit_code(self):
        (self.skill/'scripts/parallel_batch.py').write_text(
            "import sys\nprint('冻结输出')\nprint('错误证据', file=sys.stderr)\nsys.exit(7)\n", encoding='utf-8')
        target=self.root/'streams'; target.mkdir()
        record=s.freeze(self.skill,target,self.rules_bytes,self.policy)
        m=dict(self.m,batch_dir=str(target),skill_snapshot=record)
        manifest=target/'manifest.json'; b.atomic(manifest,m)
        program="import sys;sys.path.insert(0,sys.argv[1]);import stage_bundle as s;raise SystemExit(s.redirect_cli(sys.argv[2],'parallel_batch.py',[]))"
        result=s.subprocess.run([s.sys.executable,'-X','utf8','-c',program,str(self.actual/'scripts'),str(manifest)],
                                capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,7)
        self.assertEqual(result.stdout.strip(),'冻结输出')
        self.assertEqual(result.stderr.strip(),'错误证据')

    def test_real_queue_prepares_frozen_packet_and_prompt_without_changing_receipt_gate(self):
        source=self.root/'input'; source.mkdir(); docx(source/'source.docx','来源')
        policy=self.root/'policy.json'; b.atomic(policy,self.policy)
        rules=self.root/'rules.json'; rules.write_bytes(self.rules_bytes)
        selection={'projects':[{'title':'项目','source_files':['source.docx'],'primary_source':'source.docx'}]}
        with patch.object(q,'POLICY',policy), patch.object(q,'RULES',rules):
            manifest=q.init(source,self.root/'work',selection,self.root/'desktop')
        reserved=q.reserve(manifest,1)  # New mode prepares even without the optional flag.
        prepared=reserved['worker_context']['prepared']; m=q.load_manifest(manifest)
        case=m['projects'][0]; attempt=case['attempts'][0]
        handoff.verify_prepared(m,case,attempt)
        self.assertTrue(Path(prepared['pipeline']['stage_guide']).is_file())
        prompt=cli_pool.worker_prompt(reserved)
        self.assertIn(prepared['pipeline']['stage_guide'],prompt)
        self.assertNotIn(str(self.actual/'references/writing-standard.md'),prompt)
        self.assertEqual(reserved['spawn_arguments']['model'],'gpt-6.1-sol')
        self.assertEqual(reserved['spawn_arguments']['reasoning_effort'],'high')
        Path(prepared['pipeline']['stage_guide']).write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError): handoff.verify_prepared(m,case,attempt)


if __name__=='__main__': unittest.main()
