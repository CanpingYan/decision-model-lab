"""Small, dependency-free XLSX import at the shared case interface."""
from collections import Counter
from io import BytesIO
from pathlib import PurePosixPath
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET

HEADERS = ['用例ID', '场景', '用例名称', '上下文', '问题ID', '决策问题', '候选项',
           '标准答案', '启用条件', '判断依据', '难度', '标签']
REQUIRED = HEADERS[:7]
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def _xml(archive, name):
    data = archive.read(name)
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Excel 含有不支持的 XML 声明，请另存为普通 .xlsx')
    return ET.fromstring(data)


def read_rows(data):
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError('文件不是有效的 Excel .xlsx 工作簿') from None
    with archive:
        entries = archive.infolist()
        if len(entries) > 3000 or sum(e.file_size for e in entries) > 64 * 1024 * 1024:
            raise ValueError('Excel 解压后过大，请拆分题集后导入')
        if any(e.flag_bits & 1 for e in entries):
            raise ValueError('请先移除 Excel 密码保护')
        names = set(archive.namelist())
        if 'xl/workbook.xml' not in names:
            raise ValueError('文件缺少 Excel 工作簿信息')
        workbook = _xml(archive, 'xl/workbook.xml')
        sheets = workbook.findall('s:sheets/s:sheet', NS)
        if not sheets:
            raise ValueError('Excel 中没有工作表')
        sheet = next((s for s in sheets if s.get('name') == '测试用例'), sheets[0])
        relationships = _xml(archive, 'xl/_rels/workbook.xml.rels')
        relation = next((r for r in relationships if r.get('Id') == sheet.get('{%s}id' % NS['r'])), None)
        if relation is None or relation.get('TargetMode') == 'External':
            raise ValueError('无法读取测试用例工作表')
        target = relation.get('Target', '')
        target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        if '..' in PurePosixPath(target).parts or target not in names:
            raise ValueError('Excel 工作表路径无效')
        strings = []
        if 'xl/sharedStrings.xml' in names:
            for item in _xml(archive, 'xl/sharedStrings.xml').findall('s:si', NS):
                strings.append(''.join(t.text or '' for t in item.iter('{%s}t' % NS['s'])))
        rows = []
        for row in _xml(archive, target).findall('s:sheetData/s:row', NS):
            cells = {}
            for cell in row.findall('s:c', NS):
                if cell.find('s:f', NS) is not None:
                    raise ValueError(f'第 {row.get("r", "?")} 行含公式。请粘贴为值后再导入')
                ref = cell.get('r', '')
                match = re.fullmatch(r'([A-Z]+)(\d+)', ref)
                if not match:
                    continue
                column = 0
                for letter in match.group(1):
                    column = column * 26 + ord(letter) - 64
                if column > 100:
                    continue
                kind = cell.get('t')
                value = cell.find('s:v', NS)
                text = value.text if value is not None and value.text is not None else ''
                if kind == 's':
                    try:
                        text = strings[int(text)]
                    except (IndexError, ValueError):
                        raise ValueError('Excel 共享文本索引无效') from None
                elif kind == 'inlineStr':
                    text = ''.join(t.text or '' for t in cell.iter('{%s}t' % NS['s']))
                elif kind == 'e':
                    raise ValueError(f'第 {row.get("r", "?")} 行含 Excel 错误值')
                cells[column - 1] = text.strip()
            if any(cells.values()):
                rows.append((int(row.get('r', len(rows) + 1)), cells))
            if len(rows) > 5001:
                raise ValueError('每次最多导入 5000 个决策问题')
        return rows


def _unique_choices(pairs):
    choices = {}
    for key, description in pairs:
        if key in choices:
            raise ValueError(f'候选项“{key}”重复')
        choices[key] = description
    return choices


def parse_choices(text):
    try:
        value = json.loads(text, object_pairs_hook=_unique_choices)
    except (json.JSONDecodeError, TypeError):
        pairs = []
        for line in str(text).splitlines():
            if not line.strip():
                continue
            parts = re.split(r'[:：]', line, maxsplit=1)
            if len(parts) != 2:
                raise ValueError('候选项请每行写“选项：说明”，或填写 JSON 对象')
            key, description = (p.strip() for p in parts)
            pairs.append((key, description))
        value = _unique_choices(pairs)
    if not isinstance(value, dict) or not 2 <= len(value) <= 10:
        raise ValueError('每个决策问题需要 2–10 个候选项')
    if any(not isinstance(k, str) or not k.strip() or len(k) > 80 or
           not isinstance(v, str) or not v.strip() or len(v) > 4000 for k, v in value.items()):
        raise ValueError('候选项的名称和说明必须是非空文本')
    return value


def validate_case(case):
    if not case.get('id') or len(case['id']) > 100:
        raise ValueError('用例 ID 不能为空，且不超过 100 字符')
    questions = case.get('questions', {})
    if not 1 <= len(questions) <= 16:
        raise ValueError(f'用例 {case["id"]} 需要 1–16 个问题')
    for key, question in questions.items():
        if not key or len(key) > 80 or not question.get('instructions'):
            raise ValueError(f'用例 {case["id"]} 的问题 ID 或决策问题为空')
        if question.get('type') != 'choice':
            raise ValueError('当前版本统一使用候选项选择问题')
        parse_choices(json.dumps(question['criteria'], ensure_ascii=False))
    expected = case.get('expected', {})
    for key, value in expected.items():
        if key not in questions or value not in questions[key]['criteria']:
            raise ValueError(f'用例 {case["id"]} 的标准答案“{key}={value}”不在候选项中')
    conditions = case.get('conditions', {})
    visited, visiting = set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError(f'用例 {case["id"]} 的启用条件存在循环')
        if key in visited:
            return
        visiting.add(key)
        condition = conditions.get(key)
        if condition:
            parent = condition.get('question')
            if parent not in questions or condition.get('equals') not in questions[parent]['criteria']:
                raise ValueError(f'用例 {case["id"]} 的启用条件引用了无效问题或选项')
            visit(parent)
        visiting.remove(key)
        visited.add(key)

    for key in questions:
        visit(key)
    for key in conditions:
        if key not in questions:
            raise ValueError(f'用例 {case["id"]} 的启用条件包含未知问题')
        if key in expected:
            parent = conditions[key]['question']
            if parent not in expected:
                raise ValueError(f'用例 {case["id"]}：条件问题有标准答案时，请同时填写前置问题“{parent}”的标准答案')
            if expected[parent] != conditions[key]['equals']:
                raise ValueError(f'用例 {case["id"]}：问题“{key}”的标准答案与启用条件矛盾；条件不成立时应留空')
    if not case.get('scene') or not case.get('title') or not case.get('state'):
        raise ValueError(f'用例 {case["id"]} 的场景、名称或上下文为空')
    if len(json.dumps(case['state'], ensure_ascii=False)) > 100000:
        raise ValueError(f'用例 {case["id"]} 的上下文过长')
    return case


def import_workbook(data, name):
    rows = read_rows(data)
    if not rows:
        raise ValueError('Excel 没有测试用例，请使用下载的导入模板')
    _, first = rows[0]
    headers = {text.replace(' ', ''): column for column, text in first.items() if text}
    missing = [h for h in REQUIRED if h not in headers]
    if missing:
        raise ValueError('缺少列：' + '、'.join(missing) + '。请保留模板第一行表头')
    cases = {}
    previous_id = None
    for row_number, cells in rows[1:]:
        def field(header):
            return cells.get(headers.get(header), '').strip()
        case_id = field('用例ID') or previous_id
        if not case_id:
            raise ValueError(f'第 {row_number} 行缺少用例 ID')
        previous_id = case_id
        try:
            if case_id not in cases:
                state = field('上下文')
                if state.startswith(('{', '[')):
                    try:
                        state = json.loads(state)
                    except json.JSONDecodeError:
                        pass
                cases[case_id] = {'id': case_id, 'scene': field('场景'), 'title': field('用例名称'),
                                  'state': state, 'questions': {}, 'expected': {},
                                  'conditions': {}, 'rationale': field('判断依据'),
                                  'difficulty': field('难度') or '未标注',
                                  'tags': [t.strip() for t in re.split(r'[,，、;；\n]', field('标签')) if t.strip()]}
            case = cases[case_id]
            # Blank repeated metadata inherits only within the same explicit case ID.
            for column, key in [('场景', 'scene'), ('用例名称', 'title'), ('判断依据', 'rationale'), ('难度', 'difficulty')]:
                current = field(column)
                if current and case.get(key) not in ('', '未标注', current):
                    raise ValueError(f'同一用例的“{column}”不一致')
                if current:
                    case[key] = current
            state_text = field('上下文')
            if state_text:
                try:
                    state_value = json.loads(state_text)
                except json.JSONDecodeError:
                    state_value = state_text
                if state_value != case['state']:
                    raise ValueError('同一用例的上下文不一致')
            question_id = field('问题ID')
            if not question_id or question_id in case['questions']:
                raise ValueError('问题 ID 为空或在同一用例内重复')
            case['questions'][question_id] = {'type': 'choice', 'instructions': field('决策问题'),
                                              'criteria': parse_choices(field('候选项'))}
            expected = field('标准答案')
            if expected:
                case['expected'][question_id] = expected
            condition = field('启用条件')
            if condition:
                parent, separator, value = condition.partition('=')
                if not separator or not parent.strip() or not value.strip():
                    raise ValueError('启用条件请写“前置问题ID=选项名称”')
                case['conditions'][question_id] = {'question': parent.strip(), 'equals': value.strip()}
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f'第 {row_number} 行：{error}') from None
    if not cases:
        raise ValueError('Excel 只有表头，请先添加测试用例')
    for case in cases.values():
        validate_case(case)
    if len(cases) > 2000:
        raise ValueError('每次最多导入 2000 个用例')
    count = Counter(c['scene'] for c in cases.values())
    dataset = {'id': hashlib.sha256(data).hexdigest()[:24], 'name': name,
               'language': 'zh-CN', 'caseCount': len(cases),
               'scenes': [{'id': scene, 'name': scene, 'count': size} for scene, size in count.items()],
               'cases': list(cases.values())}
    warnings = []
    unscored = sum(not c['expected'] for c in cases.values())
    if unscored:
        warnings.append(f'{unscored} 个用例未填标准答案，可观察输出，但不计正确率')
    return dataset, warnings
