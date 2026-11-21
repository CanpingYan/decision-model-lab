"""One decision path for the demo and batch comparison, without answer leakage."""
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import threading
import time
import uuid

MODEL_NAMES = {'deepseek': 'DeepSeek Flash', 'jev': 'JEV',
               'laya': 'Laya 多语言', 'alibaba': '阿里巴巴 Decision Model',
               'chinese': 'Chinese Decision 0.8B'}


def now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def validate_answers(answers, questions):
    if not isinstance(answers, dict):
        raise ValueError('模型没有返回有效的决策答案对象')
    normalized = {}
    for key, question in questions.items():
        answer = answers.get(key)
        if isinstance(answer, str):
            answer = {'choice': answer}
        if not isinstance(answer, dict) or answer.get('choice') not in question['criteria']:
            raise ValueError(f'模型对问题“{key}”返回了候选项以外的标签')
        normalized[key] = {'choice': answer['choice']}
        probabilities = answer.get('probabilities')
        if probabilities is not None:
            if not isinstance(probabilities, dict) or set(probabilities) != set(question['criteria']):
                raise ValueError(f'问题“{key}”的概率没有覆盖全部候选项')
            if any(isinstance(p, bool) or not isinstance(p, (int, float)) or
                   not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
                raise ValueError(f'问题“{key}”的概率数值无效')
            if not abs(sum(probabilities.values()) - 1) <= .03:
                raise ValueError(f'问题“{key}”的概率之和不为 1')
            normalized[key]['probabilities'] = probabilities
        # A generated JSON label has no native probability; leave it absent.
    return normalized


def run_case(registry, case, model_id, cancelled=None):
    if model_id not in MODEL_NAMES:
        raise ValueError('未知模型')
    start = time.perf_counter()
    result = {'id': uuid.uuid4().hex, 'caseId': case['id'], 'scene': case['scene'],
              'modelId': model_id, 'modelName': MODEL_NAMES[model_id], 'status': 'success',
              'labels': {}, 'answers': {}, 'correct': None, 'fields': {},
              'trace': [], 'raw': {'stages': []}, 'createdAt': now()}
    pending = dict(case['questions'])
    conditions = case.get('conditions', {})
    settled = set()
    try:
        while pending:
            ready = {}
            pending_before = len(pending)
            for key, question in list(pending.items()):
                condition = conditions.get(key)
                if condition:
                    parent = condition['question']
                    if parent not in settled:
                        continue
                    if result['labels'].get(parent) != condition['equals']:
                        result['trace'].append({'questionIds': [key], 'status': 'skipped',
                                                'skipped': f'前置条件 {parent}={condition["equals"]} 未满足',
                                                'elapsedMs': 0})
                        del pending[key]
                        settled.add(key)
                        continue
                if len(ready) < 4:
                    ready[key] = question
            if not ready:
                if len(pending) < pending_before:
                    continue
                raise ValueError('问题依赖无法解析，请检查启用条件')
            # This allowlist is the sole seam that constructs model input.
            task = {'state': deepcopy(case['state']), 'questions': deepcopy(ready)}
            if cancelled and cancelled():
                result['status'] = 'stopped'
                result['trace'].append({'questionIds': list(pending), 'status': 'skipped',
                                        'skipped': '用户已停止，未发送后续判断', 'elapsedMs': 0})
                break
            stage_start = time.perf_counter()
            try:
                response = registry.decide(model_id, task)
                answers = validate_answers(response.get('answers'), ready)
            except Exception as error:
                result['trace'].append({'questionIds': list(ready), 'status': 'error',
                                        'request': task, 'error': str(error),
                                        'elapsedMs': (time.perf_counter() - stage_start) * 1000})
                raw_error = getattr(error, 'raw', None)
                if raw_error is not None:
                    result['raw']['stages'].append(raw_error)
                raise
            result['trace'].append({'questionIds': list(ready), 'status': 'success',
                                    'request': task, 'answers': answers,
                                    'elapsedMs': response.get('elapsedMs', (time.perf_counter() - stage_start) * 1000),
                                    'model': response.get('model'), 'usage': response.get('usage')})
            result['raw']['stages'].append(response.get('raw', {}))
            for key, answer in answers.items():
                result['answers'][key] = answer
                result['labels'][key] = answer['choice']
                settled.add(key)
                del pending[key]
    except Exception as error:
        result['status'] = 'error'
        result['error'] = str(error)[:1500]
    result['fields'] = {key: result['labels'].get(key) == value for key, value in case.get('expected', {}).items()}
    if result['fields'] and result['status'] != 'stopped':
        result['correct'] = result['status'] == 'success' and all(result['fields'].values())
    result['elapsedMs'] = (time.perf_counter() - start) * 1000
    return result


class LabStore:
    """Persist imported data and immutable run snapshots outside the web root."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.datasets = {}
        self.dataset_summaries = {}
        self.current_id = None
        self.jobs = {}
        self.stops = {}
        for folder in ['datasets', 'runs']:
            (self.directory / folder).mkdir(parents=True, exist_ok=True)
        # Existing single-dataset installations already have these files. Build
        # a small catalog without keeping every complete workbook in memory.
        for path in (self.directory / 'datasets').glob('*.json'):
            try:
                dataset = json.loads(path.read_text())
                if dataset.get('id') != path.stem:
                    continue
                self.dataset_summaries[dataset['id']] = self._dataset_summary(dataset, path)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        current = self.directory / 'current.json'
        if current.exists():
            try:
                dataset_id = json.loads(current.read_text())['datasetId']
                self.current_id = self.get_dataset(dataset_id)['id']
            except (OSError, ValueError, KeyError):
                pass
        paths = sorted((self.directory / 'runs').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        for path in paths:
            try:
                job = json.loads(path.read_text())
                if job['state'] == 'running':
                    job['state'] = 'stopped'
                    job['finishedAt'] = now()
                    job['note'] = '服务已重新启动，保留已完成结果'
                    _write_json(path, job)
                self.jobs[job['id']] = job
            except (OSError, ValueError, KeyError):
                continue

    def get_dataset(self, dataset_id=None):
        with self.lock:
            dataset_id = dataset_id or self.current_id
            if not dataset_id or not isinstance(dataset_id, str) or not dataset_id.isalnum():
                raise KeyError('请先导入 Excel 测试用例')
            if dataset_id not in self.datasets:
                path = self.directory / 'datasets' / (dataset_id + '.json')
                if not path.exists():
                    raise KeyError('导入题集不存在，请重新导入')
                self.datasets[dataset_id] = json.loads(path.read_text())
            return deepcopy(self.datasets[dataset_id])

    def _dataset_summary(self, dataset, path=None):
        cases = dataset.get('cases', [])
        scenes = dataset.get('scenes')
        if scenes is None:
            counts = Counter(case['scene'] for case in cases)
            scenes = [{'id': scene, 'name': scene, 'count': count} for scene, count in counts.items()]
        imported = dataset.get('importedAt')
        if not imported and path is not None:
            imported = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        return {'id': dataset['id'], 'name': dataset.get('name', dataset['id']),
                'caseCount': dataset.get('caseCount', len(cases)), 'scenes': deepcopy(scenes),
                'importedAt': imported or now()}

    def list_datasets(self):
        with self.lock:
            return deepcopy(sorted(self.dataset_summaries.values(),
                                   key=lambda dataset: (dataset['importedAt'], dataset['name']), reverse=True))

    def _require_idle(self):
        if any(job['state'] == 'running' for job in self.jobs.values()):
            raise ValueError('批量测试仍在运行，请先停止后再切换或导入用例集')

    def get_current_dataset(self, requested_id=None):
        with self.lock:
            if requested_id is not None and requested_id != self.current_id:
                raise ValueError('当前用例集已切换，请刷新后重试')
            return self.get_dataset(self.current_id)

    def select_dataset(self, dataset_id):
        with self.lock:
            self._require_idle()
            if not dataset_id:
                raise ValueError('请选择用例集')
            dataset = self.get_dataset(dataset_id)
            self.current_id = dataset['id']
            _write_json(self.directory / 'current.json', {'datasetId': self.current_id})
            return dataset

    def selection(self):
        """A consistent snapshot for import, switching and page restoration."""
        with self.lock:
            dataset = self.get_dataset() if self.current_id else None
            return {'dataset': dataset, 'datasets': self.list_datasets(),
                    'jobs': self.recent_jobs(self.current_id)[:1] if self.current_id else []}

    def save_dataset(self, dataset):
        with self.lock:
            self._require_idle()
            dataset_id = dataset.get('id')
            if not isinstance(dataset_id, str) or not dataset_id.isalnum():
                raise ValueError('用例集 ID 无效')
            path = self.directory / 'datasets' / (dataset_id + '.json')
            if path.exists():
                # The file hash is the identity: reimporting a workbook reuses
                # the same collection and keeps its history and original name.
                dataset = self.get_dataset(dataset_id)
            else:
                dataset = deepcopy(dataset)
                dataset.setdefault('importedAt', now())
                _write_json(path, dataset)
            self.datasets[dataset_id] = deepcopy(dataset)
            self.dataset_summaries[dataset_id] = self._dataset_summary(dataset, path)
            return self.select_dataset(dataset_id)

    def get_job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError('测试批次不存在')
            return deepcopy(self.jobs[job_id])

    def recent_jobs(self, dataset_id=None):
        with self.lock:
            if dataset_id is not None:
                # The initial cache is bounded, but switching to an older
                # collection must still restore its most recent saved run.
                paths = sorted((self.directory / 'runs').glob('*.json'),
                               key=lambda path: path.stat().st_mtime, reverse=True)
                found = 0
                for path in paths:
                    job = self.jobs.get(path.stem)
                    if job is None:
                        try:
                            candidate = json.loads(path.read_text())
                        except (OSError, ValueError):
                            continue
                        if candidate.get('datasetId') != dataset_id:
                            continue
                        job = candidate
                        if job.get('state') == 'running':
                            job['state'] = 'stopped'
                            job['finishedAt'] = now()
                            job['note'] = '服务已重新启动，保留已完成结果'
                            _write_json(path, job)
                        self.jobs[job['id']] = job
                    if job.get('datasetId') == dataset_id:
                        found += 1
                        if found == 10:
                            break
            values = sorted(self.jobs.values(), key=lambda j: j['startedAt'], reverse=True)
            return deepcopy([j for j in values if dataset_id is None or j['datasetId'] == dataset_id][:10])

    def _save_job(self, job):
        _write_json(self.directory / 'runs' / (job['id'] + '.json'), job)

    def start_job(self, registry, dataset_id, models, case_ids):
        dataset = self.get_current_dataset(dataset_id)
        dataset_id = dataset['id']
        if not isinstance(models, list) or not models or len(models) != len(set(models)):
            raise ValueError('请选择一个或多个不重复的模型')
        statuses = {s['id']: s for s in registry.statuses()}
        for model in models:
            if model not in statuses or not statuses[model]['configured']:
                raise ValueError(f'{MODEL_NAMES.get(model, model)} 尚未配置，请检查 .env')
            if statuses[model].get('local') and not statuses[model]['ready']:
                raise ValueError(f'{MODEL_NAMES[model]} 需要先加载本地模型')
        if not isinstance(case_ids, list) or not case_ids or len(case_ids) != len(set(case_ids)):
            raise ValueError('请选择一个或多个不重复的测试用例')
        case_map = {case['id']: case for case in dataset['cases']}
        if any(case_id not in case_map for case_id in case_ids):
            raise ValueError('所选用例不属于当前导入题集')
        with self.lock:
            if dataset_id != self.current_id:
                raise ValueError('当前用例集已切换，请刷新后重试')
            if any(j['state'] == 'running' for j in self.jobs.values()):
                raise ValueError('已有批量测试正在运行，请先等待完成或停止')
            job_id = uuid.uuid4().hex
            job = {'id': job_id, 'datasetId': dataset_id, 'datasetName': dataset['name'],
                   'models': models, 'caseIds': case_ids, 'total': len(models) * len(case_ids),
                   'completed': 0, 'state': 'running', 'startedAt': now(), 'results': []}
            self.jobs[job_id] = job
            stop = threading.Event()
            self.stops[job_id] = stop
            self._save_job(job)
        # One simple worker; requests are sequential for comparable repeatability.
        def execute():
            try:
                for case_id in case_ids:
                    for model in models:
                        if stop.is_set():
                            return
                        result = run_case(registry, case_map[case_id], model, cancelled=stop.is_set)
                        result['datasetId'] = dataset_id
                        with self.lock:
                            job['results'].append(result)
                            job['completed'] += 1
                            self._save_job(job)
            finally:
                with self.lock:
                    job['state'] = 'stopped' if stop.is_set() and job['completed'] < job['total'] else 'completed'
                    job['finishedAt'] = now()
                    self._save_job(job)
                    self.stops.pop(job_id, None)
        threading.Thread(target=execute, name='lab-batch-' + job_id[:8], daemon=True).start()
        return self.get_job(job_id)

    def stop_job(self, job_id):
        with self.lock:
            job = self.get_job(job_id)
            if job_id in self.stops:
                self.stops[job_id].set()
                self.jobs[job_id]['stopping'] = True
                self._save_job(self.jobs[job_id])
                job['stopping'] = True
            return job
