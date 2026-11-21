from io import BytesIO
from pathlib import Path
import sys
import unittest
import zipfile
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from lab.workbook import HEADERS, import_workbook


def xlsx(rows, formula=False):
    content = []
    for i, row in enumerate([HEADERS] + rows, 1):
        cells = []
        for j, value in enumerate(row):
            ref = chr(65 + j) + str(i)
            body = '<f>1+1</f><v>2</v>' if formula and i == 2 and j == 3 else '<is><t>' + escape(str(value)) + '</t></is>'
            cells.append(f'<c r="{ref}" t="inlineStr">{body}</c>')
        content.append(f'<row r="{i}">{"".join(cells)}</row>')
    target = BytesIO()
    with zipfile.ZipFile(target, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="测试用例" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(content) + '</sheetData></worksheet>')
    return target.getvalue()


def row():
    return ['T1', '自定义场景', '有条件的判断', '这是待分析的上下文', 'kind',
            '选择消息类型', '报告：已经发生\n提问：寻求确认', '报告', '', '存在事实依据', '复杂', '时间线，否定，例外']


class WorkbookTest(unittest.TestCase):
    def test_group_questions_and_inherit_blank_metadata(self):
        child = ['', '', '', '', 'relation', '旧记忆是否被替代？',
                 '{"替代":"新的当前值","保留":"旧值有效"}', '替代', 'kind=报告', '', '', '']
        dataset, warnings = import_workbook(xlsx([row(), child]), '用户题集.xlsx')
        self.assertEqual(dataset['caseCount'], 1)
        case = dataset['cases'][0]
        self.assertEqual(len(case['questions']), 2)
        self.assertEqual(case['conditions']['relation'], {'question': 'kind', 'equals': '报告'})
        self.assertEqual(case['expected'], {'kind': '报告', 'relation': '替代'})
        self.assertEqual(warnings, [])

    def test_unknown_gold_and_duplicate_question_are_rejected(self):
        invalid = row()
        invalid[7] = '不在选项中'
        with self.assertRaisesRegex(ValueError, '标准答案'):
            import_workbook(xlsx([invalid]), '错误.xlsx')
        with self.assertRaisesRegex(ValueError, '重复'):
            import_workbook(xlsx([row(), row()]), '重复.xlsx')

    def test_duplicate_json_choice_labels_are_rejected_before_overwriting(self):
        duplicate = row()
        duplicate[6] = '{"报告":"已经发生","提问":"寻求确认","报告":"尚未发生"}'
        with self.assertRaisesRegex(ValueError, '候选项“报告”重复'):
            import_workbook(xlsx([duplicate]), '重复候选.xlsx')

    def test_conditional_cycles_are_rejected_before_running(self):
        parent = row()
        parent[8] = 'relation=替代'
        child = ['T1', '', '', '', 'relation', '关系', '替代：新值\n保留：旧值', '替代', 'kind=报告', '', '', '']
        with self.assertRaisesRegex(ValueError, '循环'):
            import_workbook(xlsx([parent, child]), '循环.xlsx')

    def test_formula_is_not_evaluated_or_silently_read_from_stale_cache(self):
        with self.assertRaisesRegex(ValueError, '粘贴为值'):
            import_workbook(xlsx([row()], formula=True), '公式.xlsx')

    def test_gold_must_agree_with_condition_and_include_its_parent(self):
        parent = row()
        parent[7] = '提问'
        child = ['T1', '', '', '', 'relation', '关系', '替代：新值\n保留：旧值', '替代', 'kind=报告', '', '', '']
        with self.assertRaisesRegex(ValueError, '矛盾'):
            import_workbook(xlsx([parent, child]), '矛盾.xlsx')
        parent[7] = ''
        with self.assertRaisesRegex(ValueError, '前置问题'):
            import_workbook(xlsx([parent, child]), '缺少前置标准.xlsx')

    def test_unscored_cases_warn_instead_of_becoming_wrong_answers(self):
        unscored = row()
        unscored[7] = ''
        dataset, warnings = import_workbook(xlsx([unscored]), '观察.xlsx')
        self.assertEqual(dataset['cases'][0]['expected'], {})
        self.assertIn('不计正确率', warnings[0])

    def test_chinese_tag_separators_round_trip_from_downloaded_workbooks(self):
        tagged = row()
        tagged[11] = '时间线、否定、例外'
        dataset, _ = import_workbook(xlsx([tagged]), '中文标签.xlsx')
        self.assertEqual(dataset['cases'][0]['tags'], ['时间线', '否定', '例外'])

    def test_non_xlsx_rejected(self):
        with self.assertRaisesRegex(ValueError, '不是有效'):
            import_workbook(b'not an excel file', '错误.xlsx')

    def test_actual_download_workbook_has_200_cases(self):
        path = ROOT / 'app/web/examples/中文复杂用例200题.xlsx'
        if not path.exists():
            self.skipTest('工作簿制作中')
        dataset, warnings = import_workbook(path.read_bytes(), path.name)
        self.assertEqual(dataset['caseCount'], 200)
        self.assertEqual([s['count'] for s in dataset['scenes']], [50] * 4)
        self.assertEqual(warnings, [])


if __name__ == '__main__':
    unittest.main()
