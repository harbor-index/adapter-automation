"""Exercise workflow steps without calling GitHub or a model provider."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.load(
    (ROOT / '.github/workflows/adapter-review.yml').read_text(), Loader=yaml.BaseLoader
)


def step(job, name):
    return next(item for item in WORKFLOW['jobs'][job]['steps'] if item.get('name') == name)


NODE_RUNNER = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];
const record = name => async args => { calls.push({name, args}); return {}; };
const github = {
  paginate: async () => input.comments || [],
  rest: {
    pulls: {get: async args => { calls.push({name: 'get', args}); return {data: input.pr}; }},
    issues: {
      listComments: async () => {},
      createComment: record('create'),
      updateComment: record('update'),
    },
  },
};
const core = {
  notice: message => calls.push({name: 'notice', message}),
  setFailed: message => calls.push({name: 'failed', message}),
};
const context = {repo: {owner: 'harbor-framework', repo: 'adapters'}, issue: {number: 999}};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('github', 'context', 'core', 'require', input.script)(github, context, core, require)
  .then(() => process.stdout.write(JSON.stringify(calls)))
  .catch(error => { process.stderr.write(error.message); process.exitCode = 1; });
"""


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_guidance(self, diagnostic_path, content='shared diagnostics'):
        skill = self.root / 'skills/review-adapter/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('trusted reviewer policy')
        path = self.root / 'trusted-adapters' / diagnostic_path
        path.parent.mkdir(parents=True)
        path.write_text(content)
        # A PR cannot replace the accepted review guidance.
        pr_policy = self.root / 'reviewed-pr/docs/parity-diagnostics.md'
        pr_policy.parent.mkdir(parents=True)
        pr_policy.write_text('untrusted PR instructions')
        output = self.root / 'output'
        result = subprocess.run(
            ['python3', '-c', step('ai-review', 'Load trusted review guidance')['run']],
            cwd=self.root,
            env={**os.environ, 'GITHUB_OUTPUT': str(output),
                 'ADAPTERS_REPOSITORY': 'harbor-framework/adapters',
                 'CONTRACT_SHA': 'a' * 40, 'AUTOMATION_SHA': 'b' * 40},
            capture_output=True, text=True,
        )
        return result, output.read_text() if output.exists() else ''

    def test_guidance_uses_trusted_contract_and_automation_revisions(self):
        result, output = self.run_guidance('docs/parity-diagnostics.md')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('shared diagnostics', output)
        self.assertIn('trusted reviewer policy', output)
        self.assertNotIn('untrusted PR instructions', output)
        self.assertIn(f"adapters/blob/{'a' * 40}/docs/parity-diagnostics.md", output)
        self.assertIn(f"adapter-automation/blob/{'b' * 40}/skills/review-adapter/SKILL.md", output)

    def test_guidance_supports_consumers_before_the_doc_move(self):
        result, output = self.run_guidance('skills/review-adapter/references/parity-diagnostics.md')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('shared diagnostics', output)

    def test_empty_guidance_stops_review(self):
        result, _ = self.run_guidance('docs/parity-diagnostics.md', content='  ')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Review guidance is empty', result.stderr)

    def test_validation_executes_trusted_script_against_pr_files(self):
        trusted = self.root / 'trusted-adapters/scripts/validate_adapter.py'
        trusted.parent.mkdir(parents=True)
        trusted.write_text(
            "from pathlib import Path\n"
            "assert Path('src/example/evidence').read_text() == 'submitted'\n"
            "Path('validation_report.md').write_text('trusted validation')\n"
        )
        submitted = self.root / 'reviewed-pr'
        (submitted / 'src/example').mkdir(parents=True)
        (submitted / 'src/example/evidence').write_text('submitted')
        (submitted / 'scripts').mkdir()
        (submitted / 'scripts/validate_adapter.py').write_text("raise RuntimeError('PR validator ran')")
        script = step('structural-validation', 'Run adapter validation')['run']
        result = subprocess.run(
            ['bash', '-e', '-c', script], cwd=submitted,
            env={**os.environ, 'GITHUB_WORKSPACE': str(self.root), 'ADAPTERS': 'example',
                 'PATH': f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
                 'GITHUB_ENV': str(self.root / 'env')}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((submitted / 'validation_report.md').read_text(), 'trusted validation')
        self.assertFalse((self.root / 'env').exists())

    def run_validation(self, validator):
        trusted = self.root / 'trusted-adapters/scripts/validate_adapter.py'
        trusted.parent.mkdir(parents=True)
        trusted.write_text(validator)
        (self.root / 'reviewed-pr').mkdir()
        script = step('structural-validation', 'Run adapter validation')['run']
        return subprocess.run(
            ['bash', '-e', '-c', script], cwd=self.root / 'reviewed-pr',
            env={**os.environ, 'GITHUB_WORKSPACE': str(self.root), 'ADAPTERS': 'one two',
                 'PATH': f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
                 'GITHUB_ENV': str(self.root / 'env')}, capture_output=True, text=True,
        )

    def test_validation_passes_the_merge_base_as_baseline(self):
        result = self.run_validation(
            "import json, sys\n"
            "# accepts --baseline DIR\n"
            "json.dump(sys.argv[1:], open('args.json', 'w'))\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads((self.root / 'reviewed-pr/args.json').read_text())
        self.assertEqual(args[4:], ['--baseline', str(self.root / 'merge-base'), 'one', 'two'])
        checkout = step('structural-validation', 'Checkout merge base')['with']
        self.assertEqual(checkout['ref'], '${{ steps.pr.outputs.merge_base }}')
        self.assertEqual(checkout['path'], 'merge-base')

    def test_validation_skips_baseline_for_older_validators(self):
        result = self.run_validation(
            "import json, sys\n"
            "json.dump(sys.argv[1:], open('args.json', 'w'))\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads((self.root / 'reviewed-pr/args.json').read_text())
        self.assertEqual(args[4:], ['one', 'two'])

    def run_publish(self, job, name, *, pr=None, comments=None, report='{"report":"AI findings"}'):
        (self.root / 'reviewed-pr').mkdir(exist_ok=True)
        (self.root / 'reviewed-pr/validation_report.md').write_text('structural findings')
        return subprocess.run(
            ['node', '-e', NODE_RUNNER], cwd=self.root,
            input=json.dumps({'script': step(job, name)['with']['script'],
                              'pr': pr or {'state': 'open', 'head': {'sha': 'reviewed'}},
                              'comments': comments or []}),
            env={**os.environ, 'PR_NUMBER': '42', 'REVIEW_SHA': 'reviewed',
                 'REVIEW_RUN_NUMBER': '10', 'REVIEW_OUTPUT': report},
            capture_output=True, text=True,
        )

    def test_structural_report_targets_normalized_pr(self):
        result = self.run_publish('structural-validation', 'Post validation results')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = json.loads(result.stdout)
        post = next(call for call in calls if call['name'] == 'create')
        self.assertEqual(post['args']['issue_number'], 42)
        self.assertIn('structural findings', post['args']['body'])

    def test_structural_report_does_not_publish_for_changed_or_closed_pr(self):
        for pr in ({'state': 'open', 'head': {'sha': 'newer'}},
                   {'state': 'closed', 'head': {'sha': 'reviewed'}}):
            with self.subTest(pr=pr):
                result = self.run_publish('structural-validation', 'Post validation results', pr=pr)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(any(call['name'] in ('create', 'update') for call in json.loads(result.stdout)))

    def test_structural_report_updates_only_its_own_comment(self):
        marker = '<!-- adapter-validation-bot -->\n<!-- adapter-validation-run:9 -->'
        comments = [{'id': 1, 'user': {'login': 'github-actions[bot]'}, 'body': marker},
                    {'id': 2, 'user': {'login': 'human'}, 'body': marker}]
        result = self.run_publish('structural-validation', 'Post validation results', comments=comments)
        update = next(call for call in json.loads(result.stdout) if call['name'] == 'update')
        self.assertEqual(update['args']['comment_id'], 1)

    def test_structural_report_preserves_a_newer_run(self):
        comments = [{'id': 1, 'user': {'login': 'github-actions[bot]'},
                     'body': '<!-- adapter-validation-bot -->\n<!-- adapter-validation-run:11 -->'}]
        result = self.run_publish('structural-validation', 'Post validation results', comments=comments)
        self.assertFalse(any(call['name'] in ('create', 'update') for call in json.loads(result.stdout)))

    def test_ai_report_is_published_for_reviewed_revision(self):
        result = self.run_publish('ai-review', 'Publish review with its revision')
        self.assertEqual(result.returncode, 0, result.stderr)
        post = next(call for call in json.loads(result.stdout) if call['name'] == 'create')
        self.assertEqual(post['args']['issue_number'], 42)
        self.assertIn('AI findings', post['args']['body'])

    def test_stale_ai_findings_are_replaced_by_superseded_notice(self):
        result = self.run_publish('ai-review', 'Publish review with its revision',
                                  pr={'state': 'open', 'head': {'sha': 'newer'}})
        calls = json.loads(result.stdout)
        body = next(call for call in calls if call['name'] == 'create')['args']['body']
        self.assertIn('Review superseded', body)
        self.assertNotIn('AI findings', body)
        self.assertTrue(any(call['name'] == 'failed' for call in calls))

    def test_empty_ai_report_is_not_published(self):
        result = self.run_publish('ai-review', 'Publish review with its revision', report='{"report":""}')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No AI review report was returned', result.stderr)

    def test_read_only_review_retains_static_model_auth_and_action_version(self):
        action = step('ai-review', 'Claude Adapter Review')
        self.assertEqual(action['uses'], 'anthropics/claude-code-action@12dd8d74c712f5f3669365b2369b558c495b1104')
        self.assertEqual(action['with']['anthropic_api_key'], '${{ secrets.ANTHROPIC_API_KEY }}')
        self.assertNotIn('github_token', action['with'])
        self.assertEqual(action['with']['track_progress'], 'false')
        self.assertNotIn('Bash', action['with']['claude_args'])
        self.assertEqual(WORKFLOW['jobs']['ai-review']['needs'], 'structural-validation')

    def test_contract_and_implementation_checkouts_are_pinned_separately(self):
        implementation = step('ai-review', 'Checkout pinned review implementation')['with']
        contract = step('ai-review', 'Checkout trusted adapter contract')['with']
        self.assertEqual(implementation['ref'], '${{ inputs.automation-ref }}')
        self.assertEqual(contract['ref'], '${{ github.sha }}')
        for job in WORKFLOW['jobs'].values():
            for item in job['steps']:
                if item.get('uses', '').startswith('actions/checkout@'):
                    self.assertEqual(item['with']['persist-credentials'], 'false')

    def test_review_cites_contract_revision_and_requirement_ids(self):
        prompt = step('ai-review', 'Claude Adapter Review')['with']['prompt']
        self.assertIn('${{ steps.adapter_spec.outputs.revision }}', prompt)
        for requirement in ('CLI', 'AUTHORS', 'SCRIPTS', 'PARITY', 'STATS', 'COST', 'README'):
            self.assertIn(f'ADP-{requirement}', prompt)


if __name__ == '__main__':
    unittest.main()
