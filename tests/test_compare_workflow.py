"""Check the compare-auto-with-human workflow and its scripts without GitHub, Daytona or a model."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / '.github/compare'
WORKFLOW = yaml.load(
    (ROOT / '.github/workflows/compare-auto-with-human.yml').read_text(), Loader=yaml.BaseLoader
)


def load(name):
    spec = importlib.util.spec_from_file_location(f'compare_{name}', TOOLS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_pairs = load('build_pairs')
job_config = load('job_config')
options = load('options')
stats = load('stats')
report = load('report')
sheet = load('sheet')


def step(job, name):
    return next(item for item in WORKFLOW['jobs'][job]['steps'] if item.get('name') == name)


def write_task(root, name):
    (root / name).mkdir(parents=True)
    (root / name / 'task.toml').write_text('version = "1.0"\n')
    (root / name / 'instruction.md').write_text(f'Problem {name}\n')


def trial(job_dir, task, reward, exception=None):
    trial_dir = job_dir / f'{task}__x'
    trial_dir.mkdir(parents=True)
    result = {
        # Harbor's task_name can carry an org prefix from task.toml; task_id.path ends with the folder.
        'task_name': f'org/{task}-named', 'trial_name': f'{task}__x', 'finished_at': '2026-11-05T00:00:00',
        'task_id': {'path': f'compare/side/{task}'},
        'verifier_result': None if reward is None else {'rewards': {'reward': reward}},
        'exception_info': None if exception is None else {'exception_type': exception},
    }
    (trial_dir / 'result.json').write_text(json.dumps(result))


class WorkflowTest(unittest.TestCase):
    def test_trigger_is_limited_to_trusted_comments(self):
        condition = WORKFLOW['jobs']['check']['if']
        self.assertIn("startsWith(github.event.comment.body, '/compare-auto-with-human')", condition)
        self.assertIn('github.event.issue.pull_request', condition)
        self.assertIn('"OWNER","MEMBER","COLLABORATOR"', condition)
        self.assertEqual(list(WORKFLOW['on']), ['issue_comment'])
        self.assertEqual(WORKFLOW['on']['issue_comment']['types'], ['created'])
        self.assertEqual(WORKFLOW['permissions'], {})
        self.assertEqual(WORKFLOW['env']['PR_NUMBER'], '${{ github.event.issue.number }}')

    def test_claude_model_and_effort_are_set(self):
        env = WORKFLOW['env']
        self.assertNotEqual(env['CLAUDE_MODEL'], 'CHANGE_ME')
        self.assertIn(env['CLAUDE_EFFORT'], ['low', 'medium', 'high', 'xhigh', 'max'])
        for job, name in [('prepare', 'Let Claude find the human adapter and pair the tasks'),
                          ('report', 'Let Claude judge the result')]:
            self.assertIn('--effort "$CLAUDE_EFFORT"', step(job, name)['run'])
            self.assertIn('export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL%/v1}"', step(job, name)['run'])

    def test_pr_comment_gives_the_verdict_line(self):
        comment = step('report', 'Comment on the pull request')
        self.assertEqual(comment['if'], 'always()')
        self.assertIn('result.json', comment['env']['RESULT'])
        self.assertIn("cell.split('\\n').pop()", comment['with']['script'])
        self.assertIn("'@\\u200b'", comment['with']['script'])

    def test_matrix_matches_runs_per_side(self):
        matrix = WORKFLOW['jobs']['run']['strategy']['matrix']
        runs = int(WORKFLOW['env']['RUNS_PER_SIDE'])
        self.assertEqual(matrix['run'], [f'run{i}' for i in range(1, runs + 1)])
        self.assertEqual(matrix['side'], ['auto', 'human'])

    def test_claude_steps_get_only_the_anthropic_key(self):
        for job, name in [('prepare', 'Let Claude find the human adapter and pair the tasks'),
                          ('report', 'Let Claude judge the result')]:
            env = step(job, name)['env']
            secrets = [k for k, v in env.items() if 'secrets.' in v]
            self.assertEqual(sorted(secrets), ['ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL'])
            self.assertNotIn('GH_TOKEN', env)

    def test_secrets_appear_only_where_needed(self):
        allowed = {
            'check': set(),
            'prepare': {'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL'},
            'run': {'DAYTONA_API_KEY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL'},
            'report': {'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'GOOGLE_SERVICE_ACCOUNT_JSON'},
        }
        for job, names in allowed.items():
            text = yaml.dump(WORKFLOW['jobs'][job])
            used = {part.split('}')[0].strip() for part in text.split('secrets.')[1:]}
            self.assertEqual(used, names, job)

    def test_scripts_run_from_a_copy_after_claude_can_write(self):
        for job in ('prepare', 'report'):
            names = [s.get('name') for s in WORKFLOW['jobs'][job]['steps']]
            copy = names.index('Copy the scripts out of the workspace')
            claude = next(i for i, n in enumerate(names) if n and n.startswith('Let Claude'))
            self.assertLess(copy, claude)
            for later in WORKFLOW['jobs'][job]['steps'][claude + 1:]:
                self.assertNotIn('.github/compare', later.get('run', ''))


class BuildPairsTest(unittest.TestCase):
    def make_work(self, tmp, pairs, choice=None):
        work = Path(tmp) / 'work'
        for side, folder, names in [('auto', 'b-autoadapter', ['b-1', 'b-2', 'b-3']),
                                    ('human', 'b', ['1', '2'])]:
            for name in names:
                write_task(work / 'datasets' / side / folder, name)
            (work / 'datasets' / f'{side}.json').write_text(
                json.dumps({'folder': folder, 'commit': f'{side}sha'}))
        (work / 'choice.json').write_text(json.dumps(choice or self.choice()))
        with open(work / 'pairs.csv', 'w', newline='') as f:
            f.write('original_id,auto_task,human_task\n' + ''.join(f'{p}\n' for p in pairs))
        return work

    @staticmethod
    def choice(**parity):
        base = {'status': 'ok', 'benchmark': 'B', 'auto_adapter': 'b-autoadapter', 'auto_dataset_pr': 7,
                'auto_dataset': 'b-autoadapter', 'human_adapter': 'b', 'human_dataset': 'b',
                'parity_source': 'full', 'parity_size': 2}
        base.update(parity)
        return base

    def make_many(self, tmp, n, choice, parity_ids=None):
        work = Path(tmp) / 'work'
        for side, folder, prefix in [('auto', 'b-autoadapter', 'b-'), ('human', 'b', '')]:
            for i in range(n):
                write_task(work / 'datasets' / side / folder, f'{prefix}{i}')
            (work / 'datasets' / f'{side}.json').write_text(json.dumps({'folder': folder, 'commit': 'x'}))
        (work / 'choice.json').write_text(json.dumps(choice))
        (work / 'pairs.csv').write_text(
            'original_id,auto_task,human_task\n' + ''.join(f'B/{i},b-{i},{i}\n' for i in range(n)))
        if parity_ids is not None:
            (work / 'parity_ids.txt').write_text(''.join(f'{i}\n' for i in parity_ids))
        return work

    def test_keeps_the_listed_parity_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = self.make_many(tmp, 10, self.choice(parity_source='hf_runs', parity_size=4),
                                  ['B/1', 'B/3', 'B/5', 'B/99'])
            meta = build_pairs.build(work, Path(tmp) / 'compare')
            self.assertEqual(meta['status'], 'ok')
            self.assertEqual((meta['paired_all_count'], meta['paired_count']), (10, 3))
            self.assertEqual((meta['parity_listed'], meta['parity_not_paired']), (4, 1))
            self.assertEqual(sorted(p.name for p in (Path(tmp) / 'compare/human').iterdir()), ['1', '3', '5'])
            self.assertTrue((Path(tmp) / 'compare/parity_ids.txt').is_file())

    def test_samples_the_parity_size_with_a_fixed_seed(self):
        picks = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                work = self.make_many(tmp, 20, self.choice(parity_source='sample', parity_size=5))
                meta = build_pairs.build(work, Path(tmp) / 'compare')
                self.assertEqual((meta['status'], meta['paired_count']), ('ok', 5))
                picks.append(sorted(p.name for p in (Path(tmp) / 'compare/auto').iterdir()))
        self.assertEqual(picks[0], picks[1])

    def test_full_parity_keeps_all_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = build_pairs.build(self.make_many(tmp, 6, self.choice(parity_size=6)), Path(tmp) / 'compare')
            self.assertEqual((meta['status'], meta['paired_count']), ('ok', 6))

    def test_bad_parity_choices_fail(self):
        for choice, ids, words in [(self.choice(parity_source='guess'), None, 'parity_source'),
                                   (self.choice(parity_source='registry'), None, 'parity_ids.txt is missing'),
                                   (self.choice(parity_source='readme'), ['X/1'], 'no paired problem'),
                                   (self.choice(parity_source='sample', parity_size=0), None, 'parity_size')]:
            with tempfile.TemporaryDirectory() as tmp:
                meta = build_pairs.build(self.make_many(tmp, 4, choice, ids), Path(tmp) / 'compare')
                self.assertEqual(meta['status'], 'failed')
                self.assertIn(words, meta['reason'])

    def test_builds_the_paired_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = self.make_work(tmp, ['B/1,b-1,1', 'B/2,b-2,2'])
            meta = build_pairs.build(work, Path(tmp) / 'compare')
            self.assertEqual(meta['status'], 'ok')
            self.assertEqual(meta['paired_count'], 2)
            self.assertEqual(meta['auto_task_count'], 3)
            self.assertEqual(sorted(p.name for p in (Path(tmp) / 'compare/auto').iterdir()), ['b-1', 'b-2'])
            self.assertEqual(sorted(p.name for p in (Path(tmp) / 'compare/human').iterdir()), ['1', '2'])

    def test_rejects_missing_or_repeated_tasks(self):
        for rows, words in [(['B/1,b-1,1', 'B/9,b-9,9'], 'do not exist'),
                            (['B/1,b-1,1', 'B/2,b-2,1'], 'repeats'),
                            (['B/1,../x,1'], 'bad task folder')]:
            with tempfile.TemporaryDirectory() as tmp:
                meta = build_pairs.build(self.make_work(tmp, rows), Path(tmp) / 'compare')
                self.assertEqual(meta['status'], 'failed')
                self.assertIn(words, meta['reason'])

    def test_passes_on_no_human_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = self.make_work(tmp, [], {'status': 'no_human_adapter', 'reason': 'none links the repo'})
            meta = build_pairs.build(work, Path(tmp) / 'compare')
            self.assertEqual(meta['status'], 'no_human_adapter')
            self.assertEqual(meta['reason'], 'none links the repo')

    def test_missing_choice_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'work').mkdir()
            meta = build_pairs.build(Path(tmp) / 'work', Path(tmp) / 'compare')
            self.assertEqual(meta['status'], 'failed')


class JobConfigTest(unittest.TestCase):
    ENV = {'AGENT': 'terminus-2', 'AGENT_MODEL': 'm', 'AGENT_MAX_TOKENS': '65536',
           'TIMEOUT_MULTIPLIER': '20', 'N_CONCURRENT': '10', 'MAX_RETRIES': '2',
           'OPENAI_BASE_URL': 'https://gw/v1'}

    def test_agent_runs_only(self):
        agent = job_config.build('auto', 'run3', 5, self.ENV)
        self.assertEqual(agent['job_name'], 'auto-run3')
        self.assertEqual(agent['agents'][0]['model_name'], 'openai/m')
        self.assertEqual(agent['agents'][0]['kwargs']['api_base'], 'https://gw/v1')
        self.assertEqual(agent['timeout_multiplier'], 20.0)
        self.assertEqual(agent['retry'], {'max_retries': 2})
        self.assertEqual(agent['datasets'], [{'path': 'compare/auto'}])
        self.assertNotIn('KEY', json.dumps(agent))
        for side, run in [('both', 'run1'), ('human', 'oracle')]:
            with self.assertRaises(ValueError):
                job_config.build(side, run, 5, self.ENV)

    def test_api_base_ends_with_v1(self):
        for url in ['https://gw', 'https://gw/', 'https://gw/v1', 'https://gw/v1/ ']:
            self.assertEqual(job_config.api_base(url), 'https://gw/v1')


class OptionsTest(unittest.TestCase):
    ENV = {'TIMEOUT_MULTIPLIER': '1', 'AGENT_MODEL': 'default', 'N_CONCURRENT': '40'}

    def test_defaults_come_from_the_workflow_env(self):
        for body in ['/compare-auto-with-human', '/compare-auto-with-human  \nmodel=x on line 2']:
            self.assertEqual(options.parse(body, self.ENV),
                             {'timeout_multiplier': '1', 'agent_model': 'default', 'n_concurrent': '40',
                              'verifier_env': '{}'})

    def test_comment_settings_replace_the_defaults(self):
        body = '/compare-auto-with-human timeout-multiplier=2.5 model=deepseek-v4.1-flash concurrency=10'
        self.assertEqual(options.parse(body, self.ENV),
                         {'timeout_multiplier': '2.5', 'agent_model': 'deepseek-v4.1-flash',
                          'n_concurrent': '10', 'verifier_env': '{}'})
        self.assertEqual(options.parse('/compare-auto-with-human concurrency=5', self.ENV)['timeout_multiplier'], '1')

    def test_verifier_env_goes_to_the_job_config_and_the_report(self):
        body = '/compare-auto-with-human verifier-env=JUDGE_MODEL=gpt-5-mini verifier-env=JUDGE_EFFORT=low'
        given = options.parse(body, self.ENV)['verifier_env']
        self.assertEqual(json.loads(given), {'JUDGE_MODEL': 'gpt-5-mini', 'JUDGE_EFFORT': 'low'})
        env = {**JobConfigTest.ENV, 'VERIFIER_ENV': given}
        self.assertEqual(job_config.build('human', 'run1', 5, env)['verifier'],
                         {'env': {'JUDGE_MODEL': 'gpt-5-mini', 'JUDGE_EFFORT': 'low'}})
        for empty in ({}, {'VERIFIER_ENV': ''}, {'VERIFIER_ENV': '{}'}):
            self.assertNotIn('verifier', job_config.build('auto', 'run1', 5, {**JobConfigTest.ENV, **empty}))
        for words in ['verifier-env=JUDGE_MODEL', 'verifier-env=judge=x', 'verifier-env=A=b;c',
                      'verifier-env=A=$(id)', 'verifier-env=A=1 verifier-env=A=2', 'verifier-env']:
            with self.assertRaises(ValueError, msg=words):
                options.parse(f'/compare-auto-with-human {words}', self.ENV)
        for job, name in [('run', 'Run Harbor'), ('report', 'Write the report')]:
            self.assertEqual(step(job, name)['env']['VERIFIER_ENV'], '${{ needs.check.outputs.verifier_env }}')

    def test_bad_settings_fail(self):
        for words in ['timeout=5', 'model', 'timeout-multiplier=0', 'timeout-multiplier=fast',
                      'concurrency=1.5', 'concurrency=0', 'model=$(id)', 'model=a;b',
                      'concurrency=5 concurrency=6']:
            with self.assertRaises(ValueError, msg=words):
                options.parse(f'/compare-auto-with-human {words}', self.ENV)
        with self.assertRaises(ValueError):
            options.parse('/compare-auto-with-humans', self.ENV)

    def test_workflow_passes_the_settings_to_the_run_and_the_report(self):
        read = step('check', 'Read the settings from the comment')
        self.assertEqual(read['env'], {'COMMENT_BODY': '${{ github.event.comment.body }}'})
        self.assertNotIn('github.event', read['run'])
        outputs = WORKFLOW['jobs']['check']['outputs']
        for name in ('agent_model', 'timeout_multiplier', 'n_concurrent'):
            self.assertEqual(outputs[name], '${{ steps.options.outputs.%s }}' % name)
        run = step('run', 'Run Harbor')['env']
        self.assertEqual(run['AGENT_MODEL'], '${{ needs.check.outputs.agent_model }}')
        self.assertEqual(run['TIMEOUT_MULTIPLIER'], '${{ needs.check.outputs.timeout_multiplier }}')
        self.assertEqual(run['N_CONCURRENT'], '${{ needs.check.outputs.n_concurrent }}')
        report_env = step('report', 'Write the report')['env']
        self.assertEqual(report_env['AGENT_MODEL'], '${{ needs.check.outputs.agent_model }}')
        self.assertEqual(report_env['TIMEOUT_MULTIPLIER'], '${{ needs.check.outputs.timeout_multiplier }}')


class StatsTest(unittest.TestCase):
    def make(self, tmp, scores):
        """scores: {(side, run): [rewards for tasks 1..4]}; None = no reward."""
        jobs = Path(tmp) / 'jobs'
        for (side, run), rewards in scores.items():
            job_dir = jobs / f'job-{side}-{run}' / f'{side}-{run}'
            for i, reward in enumerate(rewards, start=1):
                name = f'a{i}' if side == 'auto' else f'h{i}'
                trial(job_dir, name, reward, 'DaytonaError' if reward is None else None)
        pairs = [{'original_id': str(i), 'auto_task': f'a{i}', 'human_task': f'h{i}'} for i in range(1, 5)]
        return pairs, stats.load_trials(jobs)

    def test_scores_kept_runs_and_overlap(self):
        runs = {}
        auto = [[1, 1, 0, 0], [1, 1, 1, 0], [1, 0, 0, 0], [1, 1, 0, 0], [1, 1, 1, 1]]
        human = [[1, 1, 0, 0], [1, 1, 0, 0], [1, 1, 1, 0], [1, 0, 0, 0], [1, 1, 0, 0]]
        for i in range(5):
            runs[('auto', f'run{i + 1}')] = auto[i]
            runs[('human', f'run{i + 1}')] = human[i]
        with tempfile.TemporaryDirectory() as tmp:
            pairs, trials = self.make(tmp, runs)
            result = stats.compute(pairs, trials, 5, 0.10)
        a = result['sides']['auto']
        self.assertEqual(a['scores'], [50.0, 75.0, 25.0, 50.0, 100.0])
        self.assertEqual(a['kept'], [50.0, 50.0, 75.0])
        self.assertAlmostEqual(a['mean'], 58.3333, places=3)
        self.assertAlmostEqual(a['sem'], 8.3333, places=3)
        self.assertEqual(result['sides']['human']['kept'], [50.0, 50.0, 50.0])
        self.assertTrue(result['overlap'])
        self.assertEqual(result['rule_verdict'], 'match')
        self.assertEqual(result['problems'], [])

    def test_missing_run_and_errors_fail(self):
        runs = {(side, run): [1, 1, 0, 0] for side in ('auto', 'human')
                for run in ['run1', 'run2', 'run3', 'run4']}
        runs[('auto', 'run2')] = [1, None, 0, 0]
        with tempfile.TemporaryDirectory() as tmp:
            pairs, trials = self.make(tmp, runs)
            result = stats.compute(pairs, trials, 5, 0.10)
        self.assertEqual(result['rule_verdict'], 'failed')
        self.assertIn('auto run5: no results', result['problems'])
        self.assertIn('auto run2: 1 of 4 trials failed', result['problems'])
        self.assertEqual(result['sides']['auto']['runs']['run2']['exceptions'], {'DaytonaError': 1})

    def test_per_task_rows_sorted_by_gap(self):
        runs = {(side, run): [1, 0, 0, 0] for side in ('auto', 'human')
                for run in ['run1', 'run2']}
        runs[('auto', 'run1')] = [1, 0, 0, 1]
        runs[('auto', 'run2')] = [1, 0, 0, 1]
        with tempfile.TemporaryDirectory() as tmp:
            pairs, trials = self.make(tmp, runs)
            rows = stats.per_task_rows(pairs, trials, 2)
        self.assertEqual(rows[0]['original_id'], '4')
        self.assertEqual(rows[0]['gap'], 2.0)


class ReportTest(unittest.TestCase):
    STATS = {'available': True, 'rule_verdict': 'no match', 'problems': [], 'sides': {
        'human': {'mean': 66.6667, 'sem': 0.6667}, 'auto': {'mean': 66.3333, 'sem': 1.4530}}}
    META = {'status': 'ok'}

    def test_cell_with_claude_verdict(self):
        claude = report.claude_verdict({'verdict': 'no match', 'cause': 'infra',
                                        'summary': 'auto run2 hit\n429 errors'})
        cell, verdict, source = report.decide(self.META, self.STATS, claude, 'success')
        self.assertEqual(cell, 'human: 66.67 ± 0.67\nauto: 66.33 ± 1.45\n'
                               'no match (infra): auto run2 hit 429 errors')
        self.assertEqual((verdict, source), ('no match', 'claude'))
        match = report.claude_verdict({'verdict': 'match', 'cause': 'adapter', 'summary': ''})
        self.assertTrue(report.decide(self.META, self.STATS, match, 'success')[0].endswith('\nmatch'))

    def test_rule_is_the_fallback(self):
        self.assertIsNone(report.claude_verdict({'verdict': 'maybe'}))
        cell, verdict, source = report.decide(self.META, self.STATS, None, 'success')
        self.assertTrue(cell.endswith("no match (unclear): Claude's analysis is missing"))
        self.assertEqual(source, 'rule')

    def test_prepare_outcomes(self):
        self.assertEqual(report.decide({'status': 'no_human_adapter'}, {}, None, 'success')[0],
                         'no human adapter')
        cell = report.decide({}, {}, None, 'failure')[0]
        self.assertEqual(cell, 'failed: the prepare job ended with failure')


class SheetTest(unittest.TestCase):
    HEADER = ['Benchmark', 'GitHub Account', 'Github PR', 'Verification']
    RESULT = {'benchmark': 'Bench', 'author': 'alice', 'cell': 'human: 1\nauto: 2\nmatch',
              'pr_url': 'https://github.com/o/r/pull/3'}

    def test_appends_a_new_row(self):
        plan = sheet.plan_update([self.HEADER, ['Other', '@x', 'https://github.com/o/r/pull/1', 'm']],
                                 self.RESULT)
        self.assertEqual(plan['append'], ['Bench', '@alice', self.RESULT['pr_url'], self.RESULT['cell']])

    def test_updates_the_matching_row_and_keeps_typed_values(self):
        rows = [self.HEADER, ['Bench', '@someone', self.RESULT['pr_url'].upper() + '/', 'old']]
        plan = sheet.plan_update(rows, self.RESULT)
        self.assertEqual(plan['updates'], [('D2', self.RESULT['cell'])])
        rows = [self.HEADER, ['', '', self.RESULT['pr_url'], '']]
        plan = sheet.plan_update(rows, self.RESULT)
        self.assertEqual(plan['updates'], [('D2', self.RESULT['cell']), ('A2', 'Bench'), ('B2', '@alice')])

    def test_finds_columns_by_name(self):
        rows = [['Github PR', 'Verification', 'Benchmark', 'GitHub Account']]
        self.assertEqual(sheet.plan_update(rows, self.RESULT)['append'],
                         [self.RESULT['pr_url'], self.RESULT['cell'], 'Bench', '@alice'])
        with self.assertRaises(ValueError):
            sheet.plan_update([['Benchmark', 'Status']], self.RESULT)
        self.assertEqual(sheet.column_letter(0), 'A')
        self.assertEqual(sheet.column_letter(27), 'AB')


if __name__ == '__main__':
    unittest.main()
