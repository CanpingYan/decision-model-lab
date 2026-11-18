"""Cross-check the authored Chinese corpus against the frozen English source."""
from pathlib import Path
import collections,json,re,unittest,xml.etree.ElementTree as ET,zipfile
ROOT=Path(__file__).resolve().parents[2]
SOURCE=json.loads((ROOT/'tests/fixtures/benchmark-v3-cases.json').read_text())['cases']
DATA=json.loads((ROOT/'app/web/examples/中文复杂用例200题.json').read_text())['cases']
BY_ID={c['id']:c for c in DATA}
LABELS={
 'category':{'bug':'产品故障','how_to':'使用咨询','billing':'账务问题','account_access':'账号访问'},
 'verdict':{'supported':'有证据支持','contradicted':'与证据冲突','not_mentioned':'证据不足'},
 'tool':{'calculator':'计算器','unit_convert':'单位换算','note_find':'查找便签','note_save':'保存便签','unsupported':'超出工具能力'},
 'kind':{'report':'事实报告','question':'提问','proposal':'未来计划','command':'修改指令'},
 'relation':{'unrelated':'无关','confirms':'确认原事实','replaces':'替代旧值','contradicts':'否定原事实','minor_change':'附属细节变化'},
 'argument':{'v0':'参数1','v1':'参数2','v2':'参数3','v3':'参数4','v4':'参数5','__none__':'无匹配参数'}
}
HEADERS=['用例ID','场景','用例名称','上下文','问题ID','决策问题','候选项','标准答案','启用条件','判断依据','难度','标签']
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def xlsx_rows(p):
 with zipfile.ZipFile(p) as z:
  strings=[]
  if 'xl/sharedStrings.xml' in z.namelist():
   strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',NS)]
  xml=ET.fromstring(z.read('xl/worksheets/sheet1.xml'));result=[]
  for row in xml.findall('s:sheetData/s:row',NS):
   values=['']*12
   for cell in row.findall('s:c',NS):
    address=cell.get('r');col=0
    for char in re.match('[A-Z]+',address).group():col=col*26+ord(char)-64
    if col>12:continue
    if cell.get('t')=='inlineStr':value=''.join(cell.find('s:is',NS).itertext())
    else:
     value=cell.findtext('s:v','',NS)
     if cell.get('t')=='s':value=strings[int(value)]
    values[col-1]=value
   result.append(values)
  return result,xml

class ChineseCorpusTests(unittest.TestCase):
 def test_complete_ids_and_scene_balance(self):
  self.assertEqual([c['id'] for c in SOURCE],[c['id'] for c in DATA])
  self.assertEqual(collections.Counter(c['scene'] for c in DATA),{'工单分流':50,'证据判断':50,'工具选择':50,'记忆更新':50})
  self.assertEqual(len(BY_ID),200)

 def test_gold_and_candidate_order_are_preserved(self):
  for old in SOURCE:
   c=BY_ID[old['id']]
   self.assertEqual(c['expected'],{k:LABELS[k][v] for k,v in old['expected'].items()},old['id'])
   for key,value in c['expected'].items():self.assertIn(value,c['questions'][key]['criteria'],old['id'])
   if old['scene']=='agent':self.assertEqual(len(c['questions']['argument']['criteria'])-1,len(old['candidates']))

 def test_numeric_literals_preserved_for_every_case(self):
  numbers=lambda x:set(re.findall(r'(?<![A-Za-z])\d+(?:\.\d+)?',x))
  for old in SOURCE:
   c=BY_ID[old['id']];source=json.dumps(old['state'],ensure_ascii=False);translated=json.dumps(c['state'],ensure_ascii=False)
   if old['scene']=='agent':
    source+=json.dumps(old['candidates'],ensure_ascii=False)
    translated+=json.dumps(list(c['questions']['argument']['criteria'].values())[:-1],ensure_ascii=False)
   self.assertEqual(numbers(source),numbers(translated),old['id'])

 def test_chinese_content_and_complexity_annotations(self):
  for c in DATA:
   context=json.dumps(c['state'],ensure_ascii=False)
   self.assertGreater(len(context),60,c['id'])
   self.assertRegex(context,r'[\u4e00-\u9fff]',c['id'])
   self.assertNotRegex(context,r'[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){2,}',c['id'])
   self.assertGreaterEqual(len(c['tags']),3,c['id'])
   self.assertTrue(all(re.search(r'[\u4e00-\u9fff]',tag) for tag in c['tags']),c['id'])
   for q in c['questions'].values():
    self.assertRegex(q['instructions'],r'[\u4e00-\u9fff]',c['id'])
    self.assertTrue(2<=len(q['criteria'])<=10,c['id'])

 def test_memory_conditions_do_not_invent_gold(self):
  for c in DATA:
   if c['scene']!='记忆更新':continue
   self.assertEqual(c['conditions'],{'relation':{'question':'kind','equals':'事实报告'}})
   self.assertEqual('relation' in c['expected'],c['expected']['kind']=='事实报告')

 def test_saved_workbooks_match_json_and_keep_panes(self):
  for name,ids in [('中文复杂用例200题.xlsx',list(BY_ID)),('测试用例模板.xlsx',['W01','R01','A01','M01'])]:
   rows,xml=xlsx_rows(ROOT/'app/web/examples'/name)
   self.assertEqual(rows[0],HEADERS)
   self.assertEqual(len(rows)-1,sum(len(BY_ID[i]['questions']) for i in ids))
   self.assertEqual(set(r[0] for r in rows[1:]),set(ids))
   for row in rows[1:]:
    c=BY_ID[row[0]];qid=row[4]
    self.assertEqual(json.loads(row[3]),c['state'])
    self.assertEqual(row[5],c['questions'][qid]['instructions'])
    self.assertEqual(row[7],c['expected'].get(qid,''))
    expected_condition=c.get('conditions',{}).get(qid)
    self.assertEqual(row[8],f"{expected_condition['question']}={expected_condition['equals']}" if expected_condition else '')
   pane=xml.find('.//s:pane',NS)
   self.assertEqual((pane.get('xSplit'),pane.get('ySplit')),('3','1'))
   self.assertEqual(xml.findall('.//s:f',NS),[])

if __name__=='__main__':unittest.main()
