import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../app/web/app.js',import.meta.url),'utf8')
  .replace("await refreshStatus({quiet:true});",'')
  .replace("if(state.dataset&&location.hash==='#compare')switchTab('compare');",'');
function fixture(fetcher=async()=>({})) {
  const elements=new Map(),storage=new Map(),media={matches:false,listener:null,addEventListener(_,fn){this.listener=fn;}};
  function element(id){
    if(!elements.has(id))elements.set(id,{id,value:'',innerHTML:'',textContent:'',hidden:false,disabled:false,open:false,listeners:{},className:'',
      classList:{toggle(){}},addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(){},
      close(){this.open=false;},showModal(){this.open=true;}});
    return elements.get(id);
  }
  const document={getElementById:element,querySelectorAll:()=>[],documentElement:{dataset:{theme:'light',themePreference:'system'}},createElement:()=>element('download-link')};
  const context=vm.createContext({document,console,matchMedia:()=>media,localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
    history:{replaceState(){}},location:{pathname:'/',search:'',hash:''},setTimeout:()=>1,clearTimeout(){},URL,Blob,
    fetch:async(path,options)=>({ok:true,status:200,json:async()=>fetcher(path,options)})});
  vm.runInContext(source,context);
  return {context,elements,document,media,storage,run:code=>vm.runInContext(code,context)};
}
const question={type:'choice',instructions:'选择队列',criteria:{账务:'退款',技术:'故障'}};
function dataset(id,title){return {id,name:title+'.xlsx',caseCount:1,scenes:[{id:'工单分流',name:'工单分流',count:1}],cases:[{id:'W01',scene:'工单分流',title,state:title,questions:{category:question},expected:{category:'账务'},difficulty:'简单',tags:[]}]};}
const A=dataset('aaa','简单'),B=dataset('bbb','一般');
const models=[{id:'jev',name:'JEV',configured:true,ready:true,local:false,model:'jev-1.14'}];
function seed(f){f.context.payload={models,datasets:[A,B],dataset:A,jobs:[]};f.run('state.models=payload.models;state.datasets=payload.datasets;acceptDataset(payload.dataset,true);state.chosenModels.add("jev");');}

test('same case IDs from separate datasets never share results',()=>{
  const f=fixture();seed(f);
  f.run('rememberResult({datasetId:"aaa",caseId:"W01",modelId:"jev",correct:true,status:"success"});');
  assert.equal(f.run('currentResults().length'),1);
  f.context.next={dataset:B,datasets:[A,B],jobs:[]};f.run('applyDatasetPayload(next,true);');
  assert.equal(f.run('currentResults().length'),0);
  f.run('rememberResult({datasetId:"aaa",caseId:"W01",modelId:"jev",correct:true,status:"success"});');
  assert.equal(f.run('currentResults().length'),0);
  f.context.job={id:'jobB',datasetId:'bbb',state:'completed',total:1,completed:1,results:[{caseId:'W01',modelId:'jev',correct:false,status:'success'}]};
  assert.equal(f.run('acceptJob(job)'),true);
  assert.equal(f.run('currentResults()[0].datasetId'),'bbb');
  assert.equal(f.run('currentResults()[0].correct'),false);
  f.context.oldJob={...f.context.job,datasetId:'aaa',results:[]};
  assert.equal(f.run('acceptJob(oldJob)'),false);
});

test('a status response started before switching cannot restore the old dataset',async()=>{
  let resolveOld;
  const f=fixture((path)=>{
    if(path==='/api/status')return new Promise(resolve=>{resolveOld=resolve;});
    if(path==='/api/datasets/select')return {dataset:B,datasets:[A,B],jobs:[]};
    throw Error(path);
  });seed(f);
  const statusPromise=f.run('refreshStatus({quiet:true})');
  await Promise.resolve();
  const switchPromise=f.run('selectDataset("bbb")');
  await switchPromise;
  resolveOld({models,datasets:[A,B],dataset:A,jobs:[]});
  await statusPromise;
  assert.equal(f.run('state.dataset.id'),'bbb');
  assert.equal(f.elements.get('dataset-select').value,'bbb');
});

test('multi-file import preserves valid files and reports invalid files',async()=>{
  let uploaded=0;
  const f=fixture((path,options)=>{
    if(path==='/api/import'){
      uploaded++;
      const selected=options.body.name.startsWith('simple')?A:B;
      return {dataset:selected,datasets:uploaded===1?[A]:[A,B],warnings:[]};
    }
    if(path==='/api/status')return {models,datasets:[A,B],dataset:B,jobs:[]};
    throw Error(path);
  });
  f.context.files=[{name:'simple.xlsx',size:100},{name:'invalid.csv',size:10},{name:'medium.xlsx',size:100}];
  await f.run('importFiles(files)');
  assert.equal(uploaded,2);
  assert.equal(f.run('state.datasets.length'),2);
  assert.equal(f.run('state.dataset.id'),'bbb');
  assert.match(f.elements.get('notice').textContent,/已导入 2 个用例集/);
  assert.match(f.elements.get('notice').textContent,/invalid.csv/);
  assert.equal(f.run('state.importBusy'),false);
});

test('switching and import pending states disable all actions tied to the dataset',()=>{
  const f=fixture();seed(f);
  f.run('state.datasetBusy=true;syncControls();');
  for(const id of ['dataset-select','import-file','demo-run','compare-run','compare-run-all','export-json'])
    assert.equal(f.elements.get(id).disabled,true,id);
});

test('light, dark and system themes persist and follow changes only in system mode',()=>{
  const f=fixture(),select=f.elements.get('theme-select');
  assert.equal(f.document.documentElement.dataset.theme,'light');
  select.value='dark';select.listeners.change();
  assert.equal(f.document.documentElement.dataset.theme,'dark');
  assert.equal(f.storage.get('decision-lab-theme'),'dark');
  f.media.matches=false;f.media.listener();
  assert.equal(f.document.documentElement.dataset.theme,'dark');
  select.value='system';select.listeners.change();
  assert.equal(f.document.documentElement.dataset.theme,'light');
  f.media.matches=true;f.media.listener();
  assert.equal(f.document.documentElement.dataset.theme,'dark');
  select.value='light';select.listeners.change();
  assert.equal(f.document.documentElement.dataset.theme,'light');
});

test('new controls and layout preserve accessible table scrollers',()=>{
  const html=fs.readFileSync(new URL('../../app/web/index.html',import.meta.url),'utf8');
  const css=fs.readFileSync(new URL('../../app/web/styles.css',import.meta.url),'utf8');
  assert.match(html,/id="import-file"[^>]*multiple/);
  assert.match(html,/id="dataset-select"/);
  assert.match(html,/id="theme-select"/);
  assert.ok(html.indexOf('decision-lab-theme')<html.indexOf('href="styles.css"'));
  assert.doesNotMatch(html,/Excel 怎么填写|统一从本地|SCENARIO|DECISION TRACE|BENCHMARK/);
  assert.match(css,/\.table-scroll\{position:relative/);
  assert.match(css,/:root\[data-theme=dark\]/);
});

test('fifth local model adds comparison columns without inventing historical scores',()=>{
  const f=fixture();seed(f);
  f.context.five=['deepseek','jev','laya','alibaba','chinese'].map(id=>({id,name:id==='chinese'?'Chinese Decision 0.8B':id,configured:true,ready:true,local:['laya','chinese'].includes(id)}));
  f.run('state.models=five;rememberResult({datasetId:"aaa",caseId:"W01",modelId:"jev",correct:true,status:"success"});renderModels();renderModelChoices();renderScores();renderResults();');
  assert.equal((f.elements.get('model-grid').innerHTML.match(/<article /g)||[]).length,5);
  assert.equal((f.elements.get('compare-models').innerHTML.match(/type="checkbox"/g)||[]).length,5);
  assert.equal((f.elements.get('score-cards').innerHTML.match(/<article /g)||[]).length,5);
  assert.match(f.elements.get('result-table').innerHTML,/<th>Chinese Decision 0\.8B<\/th>/);
  assert.equal(f.run('modelStats("chinese").count'),0);
  assert.equal(f.run('modelStats("jev").correct'),1);
});

test('restoring a Chinese-only run keeps previous models and newest result per model',()=>{
  const f=fixture();seed(f);
  f.context.history={dataset:A,datasets:[A,B],jobs:[
    {id:'new',datasetId:'aaa',state:'completed',total:1,completed:1,results:[{caseId:'W01',modelId:'chinese',correct:true,status:'success'}]},
    {id:'previous',datasetId:'aaa',state:'completed',total:1,completed:1,results:[{caseId:'W01',modelId:'jev',correct:true,status:'success'}]},
    {id:'other',datasetId:'bbb',state:'completed',total:1,completed:1,results:[{caseId:'W01',modelId:'alibaba',correct:true,status:'success'}]},
    {id:'oldest',datasetId:'aaa',state:'completed',total:1,completed:1,results:[{caseId:'W01',modelId:'jev',correct:false,status:'success'}]},
  ]};
  f.run('applyDatasetPayload(history,true)');
  assert.equal(f.run('currentResults().length'),2);
  assert.equal(f.run('modelStats("jev").correct'),1);
  assert.equal(f.run('modelStats("chinese").correct'),1);
  assert.equal(f.run('modelStats("alibaba").count'),0);
  assert.equal(f.run('state.job.id'),'new');
});
