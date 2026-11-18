"""Independent checks for the two new difficulty tiers and exported workbooks."""
from collections import Counter
from pathlib import Path
import ast
import hashlib
import json
import operator
import re
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from lab.workbook import import_workbook, validate_case

OUT = ROOT / 'app/web/examples'
TIERS = {tier: json.loads((OUT / f'中文{tier}用例200题.json').read_text()) for tier in ['简单', '一般']}
BY_ID = {tier: {case['id']: case for case in data['cases']} for tier, data in TIERS.items()}
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def arithmetic(text):
    operations = {ast.Add: operator.add, ast.Sub: operator.sub,
                  ast.Mult: operator.mul, ast.Div: operator.truediv}
    def visit(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in operations:
            return operations[type(node.op)](visit(node.left), visit(node.right))
        raise ValueError('Unexpected arithmetic expression')
    return visit(ast.parse(text, mode='eval').body)


class TierDataTests(unittest.TestCase):
    def test_each_tier_has_200_unique_cases_and_four_balanced_scenes(self):
        original = json.loads((OUT / '中文复杂用例200题.json').read_text())['cases']
        for tier, data in TIERS.items():
            cases = data['cases']
            self.assertEqual(len(cases), 200)
            self.assertEqual(len({case['id'] for case in cases}), 200)
            self.assertEqual({case['id'] for case in cases}, {case['id'] for case in original})
            self.assertEqual(Counter(case['scene'] for case in cases), dict.fromkeys(['工单分流','证据判断','工具选择','记忆更新'], 50))
            self.assertEqual(sum(len(case['questions']) for case in cases), 300)
            self.assertTrue(all(case['difficulty'] == tier for case in cases))
            for case in cases:
                validate_case(case)

    def test_tiers_share_topics_and_gold_but_have_substantively_different_contexts(self):
        longer_contexts = 0
        for case_id, easy in BY_ID['简单'].items():
            medium = BY_ID['一般'][case_id]
            self.assertEqual(easy['title'], medium['title'])
            self.assertEqual(easy['expected'], medium['expected'])
            self.assertEqual(easy['questions'], medium['questions'])
            self.assertNotEqual(easy['state'], medium['state'])
            longer_contexts += len(json.dumps(medium['state'], ensure_ascii=False)) > len(json.dumps(easy['state'], ensure_ascii=False))
            self.assertEqual(len(easy['tags']), 2)
            self.assertGreaterEqual(len(medium['tags']), 3)
        # Added constraints usually lengthen context, but length alone does not define difficulty.
        self.assertGreater(longer_contexts,180)

    def test_contexts_are_unique_chinese_and_do_not_embed_answer_metadata(self):
        for data in TIERS.values():
            texts = [json.dumps(case['state'], ensure_ascii=False, sort_keys=True) for case in data['cases']]
            self.assertEqual(len(set(texts)), 200)
            for text in texts:
                self.assertRegex(text, r'[\u4e00-\u9fff]')
                self.assertNotRegex(text, r'[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){2,}')
                self.assertNotRegex(text, r'标准答案|正确选项|gold|expected')

    def test_primary_labels_are_balanced_not_constant(self):
        for data in TIERS.values():
            cases = data['cases']
            tool_counts = Counter(case['expected']['tool'] for case in cases if case['scene'] == '工具选择')
            self.assertEqual(set(tool_counts.values()), {10})
            rag_counts = Counter(case['expected']['verdict'] for case in cases if case['scene'] == '证据判断')
            self.assertEqual(sorted(rag_counts.values()), [16, 17, 17])
            categories = Counter(case['expected']['category'] for case in cases if case['scene'] == '工单分流')
            self.assertEqual(sorted(categories.values()), [12, 12, 13, 13])
            self.assertEqual(Counter(case['expected']['relation'] for case in cases if 'relation' in case['expected']),
                             dict.fromkeys(['替代旧值','确认原事实','否定原事实','附属细节变化','无关'],6))

    def test_calculator_candidates_have_one_mathematically_correct_operation(self):
        expected_values = [155,190,48,90,180,13,12,8,25,15]
        for tier in TIERS:
            for number, expected in enumerate(expected_values,1):
                case = BY_ID[tier][f'A{number:02}']
                self.assertEqual(case['expected']['tool'], '计算器')
                choices = case['questions']['argument']['criteria']
                correct_key = case['expected']['argument']
                self.assertAlmostEqual(arithmetic(choices[correct_key]), expected)
                self.assertEqual(sum(abs(arithmetic(value)-expected)<1e-9 for key,value in choices.items() if key!='无匹配参数'),1)
            positions = Counter(BY_ID[tier][f'A{number:02}']['expected']['argument'] for number in range(1,41))
            self.assertEqual(set(positions), {'参数1','参数2','参数3'})

    def test_memory_conditions_and_model_visible_field_definitions(self):
        for tier in TIERS:
            for number in range(1,51):
                case = BY_ID[tier][f'M{number:02}']
                self.assertEqual(case['conditions'], {'relation': {'question':'kind','equals':'事实报告'}})
                self.assertEqual('relation' in case['expected'], case['expected']['kind']=='事实报告')
            self.assertIn('办公楼宇',BY_ID[tier]['M21']['state']['字段规则'])
            self.assertIn('所在仓库',BY_ID[tier]['M22']['state']['字段规则'])
            for number in range(19,25):
                self.assertIn('核心字段',BY_ID[tier][f'M{number:02}']['state']['字段规则'])

    def test_named_subjects_in_save_arguments_are_supplied_by_context(self):
        for data in TIERS.values():
            for case in data['cases']:
                if case['scene']!='工具选择' or case['expected']['tool']!='保存便签':
                    continue
                argument = case['questions']['argument']['criteria'][case['expected']['argument']]
                context = json.dumps(case['state'],ensure_ascii=False)
                for name in ['海棠','林岚']:
                    if name in argument:
                        self.assertIn(name,context,case['id'])

    def test_workbook_import_matches_all_executable_fields_and_frozen_panes(self):
        for tier, data in TIERS.items():
            path = OUT / f'中文{tier}用例200题.xlsx'
            self.assertTrue(path.is_file(), path)
            imported, warnings = import_workbook(path.read_bytes(), path.name)
            self.assertEqual(warnings, [])
            self.assertEqual(imported['caseCount'],200)
            self.assertEqual(imported['cases'],data['cases'])
            with zipfile.ZipFile(path) as archive:
                xml = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
                pane = xml.find('.//s:pane',NS)
                self.assertEqual((pane.get('xSplit'),pane.get('ySplit')),('3','1'))
                self.assertEqual(xml.findall('.//s:f',NS),[])


if __name__ == '__main__':
    unittest.main()
