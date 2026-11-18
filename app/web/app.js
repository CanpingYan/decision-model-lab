const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const text = (value) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const modelNames = {deepseek:'DeepSeek Flash', jev:'JEV', laya:'Laya', alibaba:'Alibaba Decision', chinese:'Chinese Decision 0.8B'};
const modelTypes = {deepseek:'普通大模型', jev:'专用决策模型', laya:'开源决策模型', alibaba:'阿里巴巴决策模型', chinese:'中国开源决策模型'};
const state = {models:[], datasets:[], dataset:null, datasetBusy:false, refreshBusy:false, jobStarting:false, contextToken:0, statusSeq:0, activeTab:'demo', demoResult:null, demoBusy:false, importBusy:false, pendingModels:new Set(), chosenModels:new Set(), chosenCases:new Set(), results:new Map(), job:null, pollTimer:null, pollBusy:false, initialized:false};

function notice(message, kind='info') {
  const el=$('notice'); el.textContent=message; el.className=`notice ${kind}`; el.hidden=!message;
}
function modelById(id) { return state.models.find((model)=>model.id===id); }
function modelName(id) { return modelById(id)?.name || modelNames[id] || id; }
function runnable(model) { return !!model?.configured && (!model.local || model.ready) && !state.pendingModels.has(model.id); }
function jobActive() { return !!state.job && ['running','queued','stopping'].includes(state.job.state); }
function currentCase() { return state.dataset?.cases?.find((item)=>item.id===$('demo-case').value); }
function currentModel() { return modelById($('demo-model').value); }
function allCases() { return state.dataset?.cases || []; }
function sceneName(scene) { return state.dataset?.scenes?.find((item)=>item.id===scene)?.name || scene; }
function casesForScene(scene) { return !scene || scene==='all' ? allCases() : allCases().filter((item)=>item.scene===scene); }
function sceneList() { return state.dataset?.scenes?.length ? state.dataset.scenes : [...new Set(allCases().map((item)=>item.scene))].map((name)=>({id:name,name,count:allCases().filter((item)=>item.scene===name).length})); }
function formatMs(ms) { const number=Number(ms); return Number.isFinite(number) ? number<1000 ? `${Math.round(number)} ms` : `${(number/1000).toFixed(2)} s` : '—'; }
function resultKey(caseId, modelId) { return state.dataset?.id+'\u0000'+caseId+'\u0000'+modelId; }
function resultFor(caseId, modelId) { return state.results.get(resultKey(caseId,modelId)); }
function rememberResult(result, datasetId=state.dataset?.id) {
  const owner=result?.datasetId||datasetId;
  if(!result||!result.caseId||!result.modelId||owner!==state.dataset?.id)return;
  if(!allCases().some((item)=>item.id===result.caseId))return;
  state.results.set(resultKey(result.caseId,result.modelId),{...result,datasetId:owner});
}
async function api(path, options={}) {
  const response=await fetch(path,options);
  let data;
  try {data=await response.json();} catch {throw new Error(`本地服务返回了无法读取的响应（HTTP ${response.status}），请确认应用已启动。`);}
  if(!response.ok || (data.error && !data.caseId && !data.job)) throw new Error(typeof data.error==='string'?data.error:`请求失败（HTTP ${response.status}）`);
  return data;
}
function post(path, body={}) { return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); }
function fillSelect(id, options, selected) {
  const el=$(id); const fallback=options.some((item)=>item.value===selected) ? selected : options[0]?.value;
  el.innerHTML=options.map((item)=>`<option value="${esc(item.value)}"${item.disabled?' disabled':''}>${esc(item.label)}</option>`).join('');
  if(fallback!==undefined)el.value=fallback;
}
function renderModels() {
  $('model-grid').innerHTML=state.models.map((model)=>{
    const pending=state.pendingModels.has(model.id),available=runnable(model);
    const status=pending?'加载中':available?'就绪':model.configured?'待加载':'未配置';
    const detail=model.detail||'';
    const action=model.local&&model.configured?'<button class="model-action" data-model-action="'+(model.ready?'unload':'load')+'" data-model-id="'+esc(model.id)+'"'+(pending||contextBusy()?' disabled':'')+'>'+(pending?'稍候…':model.ready?'卸载':'加载')+'</button>':'';
    const subtitle=model.subtitle||(model.id==='laya'?'Multilingual · FP32':model.model||'');
    return '<article class="model-card'+(available?' ready':'')+'" data-model="'+esc(model.id)+'"><div class="model-topline"><span class="model-type">'+esc(model.category||modelTypes[model.id]||'')+'</span><span class="status-pill '+(pending?'loading':available?'ready':model.configured?'wait':'')+'"><i></i>'+status+'</span></div><h3 title="'+esc(detail)+'">'+esc(model.name||modelNames[model.id]||model.id)+'</h3><p class="model-subtitle">'+esc(subtitle)+'</p><div class="model-foot"><p class="model-detail" title="'+esc(detail)+'">'+(model.local?'本机':'云端')+'</p>'+action+'</div></article>';
  }).join('')||'<div class="model-placeholder">未获取到模型状态</div>';
  renderDemoModelSelect();renderModelChoices();syncControls();
}
function renderDemoModelSelect() {
  const selected=$('demo-model').value;
  fillSelect('demo-model',state.models.map((model)=>({value:model.id,label:`${model.name||modelNames[model.id]||model.id}${!model.configured?' · 未配置':model.local&&!model.ready?' · 待加载':''}`})),selected || state.models.find(runnable)?.id);
}
function contextBusy() { return state.demoBusy||jobActive()||state.importBusy||state.datasetBusy||state.jobStarting; }
function renderDatasetCatalog() {
  const catalog=[...state.datasets];
  if(state.dataset&&!catalog.some((item)=>item.id===state.dataset.id))catalog.push(state.dataset);
  fillSelect('dataset-select',catalog.length?catalog.map((item)=>({value:item.id,label:(item.name||'未命名用例集').replace(/\.xlsx$/i,'')})):[{value:'',label:'请导入 Excel'}],state.dataset?.id||'');
}
function renderDataset() {
  const dataset=state.dataset;
  $('needs-import').hidden=!!dataset;$('lab-content').hidden=!dataset;
  $('dataset-info').hidden=!dataset;$('import-button-text').textContent='导入 Excel';
  renderDatasetCatalog();
  if(!dataset){$('tab-context').textContent='请先导入用例';syncControls();return;}
  const scenes=sceneList();
  $('dataset-info').innerHTML='<span>'+allCases().length+'题</span><span>'+scenes.length+'场景</span>';
  $('tab-context').textContent=allCases().length+'题 · '+scenes.length+'场景';
  fillSelect('demo-scene',scenes.map((scene)=>({value:scene.id,label:scene.name||scene.id})),$('demo-scene').value);
  fillSelect('compare-scene',[{value:'all',label:'全部场景'},...scenes.map((scene)=>({value:scene.id,label:(scene.name||scene.id)+' · '+(scene.count??casesForScene(scene.id).length)+'题'}))],$('compare-scene').value||'all');
  renderDemoCases();renderCompareCases();renderScores();renderResults();syncControls();
}
function acceptDataset(dataset, clearResults=false) {
  if(!dataset||!Array.isArray(dataset.cases))throw new Error('用例集数据无效');
  if(clearResults||state.dataset?.id!==dataset.id){
    state.results.clear();state.demoResult=null;state.job=null;state.chosenCases.clear();
    clearTimeout(state.pollTimer);$('run-progress').hidden=true;
    if($('result-dialog').open)$('result-dialog').close();
    $('demo-case').value='';$('compare-case').value='';$('case-search').value='';$('result-filter').value='all';
  }
  state.dataset=dataset;renderDataset();
}
function applyDatasetPayload(payload, clearResults=true) {
  if(Array.isArray(payload.datasets))state.datasets=payload.datasets;
  if(payload.dataset)acceptDataset(payload.dataset,clearResults);
  restoreJobs(payload.jobs);
  renderDatasetCatalog();syncControls();
}
function restoreJobs(jobs=[]) {
  const matching=jobs.filter((job)=>job.datasetId===state.dataset?.id);
  // The server returns newest first. Retain each model/case's latest result,
  // so a run of the new model does not hide the previous four-model baseline.
  for(const job of [...matching].reverse())for(const result of job.results||[])rememberResult(result,job.datasetId);
  if(matching.length)acceptJob(matching[0]);
}
function renderDemoCases() {
  fillSelect('demo-case',casesForScene($('demo-scene').value).map((item)=>({value:item.id,label:`${item.id} · ${item.title}`})),$('demo-case').value);
  renderCase();
}
function renderCase() {
  const item=currentCase(); if(!item)return;
  $('case-meta').innerHTML=`<span class="tag difficulty">${esc(item.difficulty||'复杂用例')}</span>${(item.tags||[]).map((tag)=>`<span class="tag">${esc(tag)}</span>`).join('')}`;
  $('case-state').textContent=text(item.state);
  $('case-questions').innerHTML=Object.entries(item.questions||{}).map(([qid,question],index)=>{
    const condition=item.conditions?.[qid];
    return `<div class="question-preview"><div class="question-title"><span class="question-number">${String(index+1).padStart(2,'0')}</span><span>${esc(question.instructions)}</span></div>${condition?`<div class="condition-note">仅当前置问题 ${esc(condition.question)} 的结果为「${esc(condition.equals)}」时执行</div>`:''}<div class="option-preview">${Object.entries(question.criteria||{}).map(([key,value])=>`<span title="${esc(value)}">${esc(key)}</span>`).join('')}</div></div>`;
  }).join('');
  $('case-gold').innerHTML=goldHtml(item);
  const result=state.demoResult?.caseId===item.id&&state.demoResult.modelId===$('demo-model').value?state.demoResult:null;
  renderDemoResult(result,item); syncControls();
}
function goldHtml(item) {
  return `<div class="gold-labels">${Object.entries(item.expected||{}).map(([qid,choice])=>`<div><span>${esc(qid)}</span> → <strong>${esc(choice)}</strong></div>`).join('')||'未设置标准答案'}</div>${item.rationale?`<div class="gold-reason">${esc(item.rationale)}</div>`:''}`;
}
function answerHtml(qid,answer,item,result) {
  const question=item.questions[qid]||{};
  const choice=answer?.choice??result.labels?.[qid];
  const probabilities=answer?.probabilities;
  let content='';
  if(probabilities && Object.keys(probabilities).length) {
    content=Object.entries(probabilities).sort((a,b)=>Number(b[1])-Number(a[1])).map(([key,value])=>{
      const number=Number(value),probability=Number.isFinite(number)?Math.max(0,Math.min(1,number)):0;
      return `<div class="probability-row${key===choice?' selected':''}"><span class="choice-label" title="${esc(question.criteria?.[key]||'')}">${key===choice?'↳ ':''}${esc(key)}</span><span class="prob-bar"><i style="width:${probability*100}%"></i></span><span class="prob-value">${(probability*100).toFixed(1)}%</span></div>`;
    }).join('');
  } else {
    content=`<div class="prediction-text">选择 → ${esc(choice??'未返回有效选项')}</div><p class="no-prob-note">未返回选项概率</p>`;
  }
  const expected=item.expected?.[qid];
  const correct=expected!==undefined&&choice===expected;
  const criteria=Object.entries(question.criteria||{});
  const meanings=criteria.length?`<details class="criteria-details"><summary>候选项含义</summary><dl>${criteria.map(([key,description])=>`<div><dt>${esc(key)}</dt><dd>${esc(description)}</dd></div>`).join('')}</dl></details>`:'';
  return `<div class="answer-group"><p class="answer-question">${esc(question.instructions||qid)}</p>${content}${meanings}${expected!==undefined?`<p class="answer-check${correct?'':' wrong'}">${correct?'✓ 与标准答案一致':`↗ 标准答案：${esc(expected)}`}</p>`:''}</div>`;
}
function resultHtml(result,item,includeRaw=true) {
  if(!result)return '<div class="small-empty">该模型尚未测试这条用例。</div>';
  const failed=result.status==='error';
  const title=result.status==='stopped'?'已停止后续判断':failed?'请求未完成':result.correct===true?'与标准答案一致':result.correct===false?'与标准答案不一致':'判断已完成';
  let html=`<div class="result-banner ${failed?'error':result.correct===false?'incorrect':''}"><div class="result-banner-header"><h3>${title}</h3><span class="elapsed">${formatMs(result.elapsedMs)}</span></div><p>${failed?esc(result.error||'模型请求失败，请检查配置。'):esc(`${result.modelName||modelName(result.modelId)} · ${item.id} · ${Object.keys(result.labels||{}).length} 项判断`)}</p></div>`;
  const trace=result.trace||[];
  const shown=new Set();
  if(trace.length) {
    html+=trace.map((step,index)=>{
      const qids=step.questionIds||Object.keys(step.request?.questions||step.answers||{});
      const skipped=step.status==='skipped'||step.skipped;
      if(skipped) return `<section class="trace-step"><span class="trace-step-number">${index+1}</span><h3>条件未满足，跳过这一步</h3><p class="skipped-step">${esc(typeof step.skipped==='string'?step.skipped:step.reason||qids.join('、'))}</p></section>`;
      const answers=step.answers||{};
      const body=qids.map((qid)=>{shown.add(qid);return answerHtml(qid,answers[qid]||result.answers?.[qid],item,result);}).join('');
      return `<section class="trace-step"><span class="trace-step-number">${index+1}</span><h3>${qids.length>1?'一并判断 '+qids.length+' 个问题':'读取状态与有限候选项'}</h3><p class="muted">${esc(qids.join(' · '))}${step.elapsedMs!==undefined?' · '+formatMs(step.elapsedMs):''}</p>${step.status==='error'?`<p class="error-inline">${esc(step.error||'本步请求失败')}</p>`:''}${body}</section>`;
    }).join('');
  }
  const remaining=Object.keys(result.answers||result.labels||{}).filter((qid)=>!shown.has(qid));
  if(remaining.length) html+=`<section class="trace-step"><span class="trace-step-number">${trace.length+1}</span><h3>模型决策结果</h3>${remaining.map((qid)=>answerHtml(qid,result.answers?.[qid],item,result)).join('')}</section>`;
  if(!trace.length&&!remaining.length&&!failed)html+='<p class="error-inline">响应未包含可显示的判断结果。</p>';
  if(includeRaw)html+=`<details class="raw-details"><summary>实际请求与原始响应</summary><pre>${esc(JSON.stringify({trace:result.trace,raw:result.raw,labels:result.labels,fields:result.fields,usage:result.usage,error:result.error},null,2))}</pre></details>`;
  return html;
}
function renderDemoResult(result,item=currentCase()) {
  if(state.demoBusy) {
    $('demo-result').innerHTML='<div class="working-state"><span class="spinner"></span>模型正在读取当前状态与候选项…<br><span class="muted">结果返回后，会逐步展示真实决策。</span></div>'; return;
  }
  $('demo-result').innerHTML=result&&item?resultHtml(result,item):'<div class="trace-empty"><span aria-hidden="true">⌁</span><p>选择用例后运行</p></div>';
}
function switchTab(tab) {
  if(!state.dataset)return;
  state.activeTab=tab;
  for(const name of ['demo','compare']) {
    $(`tab-${name}`).classList.toggle('active',name===tab);
    $(`tab-${name}`).setAttribute('aria-selected',String(name===tab));
    $(`${name}-view`).hidden=name!==tab;
  }
  history.replaceState(null,'',tab==='compare'?'#compare':location.pathname+location.search);
}
function renderModelChoices() {
  $('compare-models').innerHTML=state.models.map((model)=>{
    const available=runnable(model);
    return `<label class="model-choice${available?'':' unavailable'}"><input type="checkbox" name="compare-model" value="${esc(model.id)}"${state.chosenModels.has(model.id)?' checked':''}${!available||jobActive()?' disabled':''}><span>${esc(model.name||modelNames[model.id]||model.id)}${!available?`<small>${model.configured?'待加载':'未配置'}</small>`:''}</span></label>`;
  }).join('');
  updatePlan();
}
function renderCompareCases() {
  const cases=casesForScene($('compare-scene').value);
  fillSelect('compare-case',cases.map((item)=>({value:item.id,label:`${item.id} · ${item.title}`})),$('compare-case').value);
  $('case-checkboxes').innerHTML=cases.map((item)=>`<label class="case-check"><input type="checkbox" name="case-selection" value="${esc(item.id)}"${state.chosenCases.has(item.id)?' checked':''}${jobActive()?' disabled':''}><span>${esc(item.id)} · ${esc(item.title)}</span></label>`).join('');
  renderScope(); updatePlan(); renderResults();
}
function selectedCases(forceAll=false) {
  if(forceAll)return allCases();
  const cases=casesForScene($('compare-scene').value),scope=$('compare-scope').value;
  if(scope==='single')return cases.filter((item)=>item.id===$('compare-case').value);
  if(scope==='selected')return cases.filter((item)=>state.chosenCases.has(item.id));
  return cases;
}
function selectedModels() { return state.models.filter((model)=>state.chosenModels.has(model.id)); }
function renderScope() {
  const scope=$('compare-scope').value;
  $('compare-case-wrap').hidden=scope!=='single'; $('toggle-cases').hidden=scope!=='selected';
  if(scope!=='selected')$('case-picker').hidden=true;
  $('toggle-cases').textContent=$('case-picker').hidden?'选择用例':'收起选择';
}
function updatePlan() {
  const cases=selectedCases(),models=selectedModels(),count=cases.length*models.length;
  $('batch-plan').textContent=state.dataset?`${models.length} 个模型 × ${cases.length} 条用例 = ${count} 次测试`:'';
  $('selected-cases-count').textContent=`当前场景已选 ${casesForScene($('compare-scene').value).filter((item)=>state.chosenCases.has(item.id)).length} 条`;
  syncControls();
}
function syncControls() {
  const busy=contextBusy(),dataset=!!state.dataset;
  $('import-file').disabled=busy;$('import-label').classList.toggle('disabled',busy);
  $('dataset-select').disabled=!state.datasets.length||busy;
  $('refresh-status').disabled=state.refreshBusy||state.importBusy||state.datasetBusy||state.demoBusy||state.jobStarting;
  $('tab-demo').disabled=!dataset||state.importBusy||state.datasetBusy;
  $('tab-compare').disabled=!dataset||state.importBusy||state.datasetBusy;
  $('demo-run').disabled=!dataset||busy||!runnable(currentModel());
  for(const id of ['demo-model','demo-scene','demo-case'])$(id).disabled=!dataset||state.demoBusy||state.importBusy||state.datasetBusy;
  const model=currentModel();
  $('demo-hint').textContent=!model?'':!model.configured?'模型未配置':model.local&&!model.ready?'请先加载模型':model.local?'本机推理':'云端调用';
  const validModels=selectedModels().filter(runnable),cases=selectedCases();
  $('compare-run').disabled=!dataset||busy||!validModels.length||!cases.length||validModels.length!==selectedModels().length;
  $('compare-run-all').disabled=!dataset||busy||!validModels.length||validModels.length!==selectedModels().length;
  $('compare-stop').disabled=!jobActive()||state.job?.state==='stopping';
  for(const id of ['compare-scene','compare-scope','compare-case','toggle-cases','select-all-cases','clear-cases'])$(id).disabled=busy;
  for(const input of document.querySelectorAll('input[name="compare-model"]'))input.disabled=busy||!runnable(modelById(input.value));
  for(const input of document.querySelectorAll('input[name="case-selection"]'))input.disabled=busy;
  for(const button of document.querySelectorAll('[data-model-action]'))button.disabled=busy||state.pendingModels.has(button.dataset.modelId);
  $('export-json').disabled=state.results.size===0||state.importBusy||state.datasetBusy;
  $('export-csv').disabled=$('export-json').disabled;
}
function currentResults() { return [...state.results.values()].filter((result)=>result.datasetId===state.dataset?.id); }
function modelStats(id,cases=allCases()) {
  const ids=new Set(cases.map((item)=>item.id));
  const rows=currentResults().filter((result)=>result.modelId===id&&ids.has(result.caseId));
  return {count:rows.length,correct:rows.filter((result)=>result.correct===true).length,errors:rows.filter((result)=>result.status==='error').length,scored:rows.filter((result)=>typeof result.correct==='boolean').length};
}
function renderScores() {
  const any=state.results.size>0;
  $('score-cards').innerHTML=state.models.map((model)=>{
    const stats=modelStats(model.id),rate=stats.scored?stats.correct/stats.scored:0;
    return `<article class="score-card"><h3>${esc(model.name||modelNames[model.id]||model.id)}</h3><div class="score-number${stats.scored?'':' no-score'}">${stats.scored?(rate*100).toFixed(1)+'%':'—'}</div><p>正确 ${stats.correct} / 已评分 ${stats.scored}<br>已测 ${stats.count} · 异常 ${stats.errors}${stats.count&&stats.scored<stats.count?'<br>未设标准答案的用例不计准确率':''}</p><div class="rate-bar"><i style="width:${rate*100}%"></i></div></article>`;
  }).join('');
  $('matrix-panel').hidden=!any;
  if(any) {
    const header=`<thead><tr><th>业务场景</th>${state.models.map((model)=>`<th>${esc(model.name||modelNames[model.id]||model.id)}</th>`).join('')}</tr></thead>`;
    const scoreCell=(stats)=>`${stats.scored?`${stats.correct} / ${stats.scored}`:'—'}${stats.count>stats.scored?`<small class="matrix-tested">已测 ${stats.count}</small>`:''}`;
    const body=`<tbody>${sceneList().map((scene)=>`<tr><td>${esc(scene.name||scene.id)}</td>${state.models.map((model)=>{const stats=modelStats(model.id,casesForScene(scene.id));return `<td>${scoreCell(stats)}</td>`;}).join('')}</tr>`).join('')}</tbody>`;
    const foot=`<tfoot><tr><td>合计</td>${state.models.map((model)=>{const stats=modelStats(model.id);return `<td>${scoreCell(stats)}</td>`;}).join('')}</tr></tfoot>`;
    $('comparison-matrix').innerHTML=header+body+foot;
  }
  $('results-caption').textContent=any?`${state.results.size} 次测试 · 未标注答案不计准确率`:'本轮尚未运行。结果按当前导入题集独立记录；未填标准答案的用例不计准确率。';
  syncControls();
}
function resultCell(result,item,model) {
  if(!result)return '<span class="result-unrun">未测试</span>';
  const kind=result.status==='error'?'error':result.correct===true?'correct':result.correct===false?'incorrect':'unscored';
  const label=result.status==='stopped'?'已停止':kind==='error'?'请求异常':kind==='correct'?'正确':kind==='incorrect'?'不一致':'已返回';
  const icon=kind==='error'?'!':kind==='correct'?'✓':kind==='incorrect'?'×':'·';
  return `<button data-result-case="${esc(item.id)}" data-result-model="${esc(model.id)}" title="查看${esc(model.name||model.id)}的真实结果"><span class="result-icon ${kind}">${icon}</span><span class="result-cell-label">${label}</span></button><span class="result-time">${formatMs(result.elapsedMs)}</span>`;
}
function renderResults() {
  const filter=$('result-filter').value,query=$('case-search').value.trim().toLowerCase();
  const scoped=casesForScene($('compare-scene').value);
  const cases=scoped.filter((item)=>{
    if(query&&!`${item.id} ${item.title}`.toLowerCase().includes(query))return false;
    const rows=state.models.map((model)=>resultFor(item.id,model.id)).filter(Boolean);
    if(filter==='incorrect')return rows.some((result)=>result.correct===false);
    if(filter==='error')return rows.some((result)=>result.status==='error');
    if(filter==='tested')return rows.length>0;
    return true;
  });
  $('result-count').textContent=`显示 ${cases.length} / ${scoped.length} 条`;
  $('result-table').innerHTML=`<thead><tr><th>测试用例</th>${state.models.map((model)=>`<th>${esc(model.name||modelNames[model.id]||model.id)}</th>`).join('')}<th><span class="sr-only">查看详情</span></th></tr></thead><tbody>${cases.map((item)=>`<tr><td class="result-case-cell"><div class="case-row-title"><span>${esc(item.id)}</span>${esc(item.title)}</div><div class="case-row-scene">${esc(sceneName(item.scene))}${item.difficulty?' · '+esc(item.difficulty):''}</div></td>${state.models.map((model)=>`<td class="result-cell">${resultCell(resultFor(item.id,model.id),item,model)}</td>`).join('')}<td><button class="row-open" data-open-case="${esc(item.id)}" aria-label="查看${esc(item.id)}详情">↗</button></td></tr>`).join('')}</tbody>`;
  $('result-empty').hidden=cases.length>0;
}
function renderProgress() {
  const job=state.job;if(!job)return;
  $('run-progress').hidden=false;
  const descriptions={queued:'等待开始',running:'正在对比测试',completed:'本轮测试完成',stopped:'已停止后续测试',stopping:'正在停止，保留进行中的结果',error:'测试批次中断',failed:'测试批次中断'};
  const complete=Number(job.completed??job.results?.length??0),total=Number(job.total)||0;
  $('progress-label').textContent=descriptions[job.state]||job.state;
  $('progress-count').textContent=`${complete} / ${total}`;
  $('batch-progress').max=Math.max(1,total);$('batch-progress').value=complete;
  const last=job.results?.at(-1);
  $('progress-detail').textContent=job.error||`${last?`最近完成：${last.caseId} · ${last.modelName||modelName(last.modelId)}${last.status==='error'?' · 请求异常':''}。 `:''}${jobActive()?'停止后保留在途结果':'结果已保存'}`;
  syncControls();
}
function acceptJob(job) {
  if(!job||job.datasetId!==state.dataset?.id)return false;
  const previouslyActive=jobActive();
  state.job=job.state==='running'&&job.stopping?{...job,state:'stopping'}:job;
  for(const result of job.results||[])rememberResult(result,job.datasetId);
  renderProgress();renderScores();renderResults();renderModelChoices();
  clearTimeout(state.pollTimer);
  if(jobActive())state.pollTimer=setTimeout(pollJob,1000);
  if(previouslyActive!==jobActive())renderModels();
  return true;
}
async function pollJob() {
  if(!state.job||state.pollBusy)return;
  const jobId=state.job.id,datasetId=state.dataset?.id,token=state.contextToken;
  state.pollBusy=true;
  try{
    const data=await api('/api/jobs/'+encodeURIComponent(jobId));
    if(token===state.contextToken&&datasetId===state.dataset?.id&&jobId===state.job?.id)acceptJob(data.job||data);
  }catch(error){
    if(token===state.contextToken){notice('进度读取失败：'+error.message,'warning');state.pollTimer=setTimeout(pollJob,2500);}
  }finally{state.pollBusy=false;}
}
async function refreshStatus({quiet=false,restore=true}={}) {
  const sequence=++state.statusSeq,token=state.contextToken;
  state.refreshBusy=true;syncControls();
  try{
    const initialLoad=!state.initialized,status=await api('/api/status');
    if(sequence!==state.statusSeq)return status;
    state.models=status.models||[];
    if(!state.initialized){state.chosenModels=new Set(state.models.filter(runnable).map((model)=>model.id));state.initialized=true;}
    renderModels();
    if(token===state.contextToken&&!state.importBusy&&!state.datasetBusy){
      if(Array.isArray(status.datasets))state.datasets=status.datasets;
      const changed=status.dataset&&status.dataset.id!==state.dataset?.id;
      if(changed){state.contextToken++;acceptDataset(status.dataset,true);}
      const latest=restore?status.jobs?.find((job)=>job.datasetId===state.dataset?.id):null;
      if(latest&&(initialLoad||changed||['running','queued','stopping'].includes(latest.state)))restoreJobs(status.jobs);
      renderDatasetCatalog();
    }
    if(!quiet)notice('');
    return status;
  }catch(error){if(sequence===state.statusSeq)notice(error.message,'error');}
  finally{if(sequence===state.statusSeq){state.refreshBusy=false;syncControls();}}
}
async function importFiles(fileList) {
  const files=[...fileList];if(!files.length||contextBusy())return;
  state.importBusy=true;state.contextToken++;state.statusSeq++;state.refreshBusy=false;
  syncControls();notice('');
  const imported=[],failures=[],warnings=[];
  let last=null;
  try{
    for(let i=0;i<files.length;i++){
      const file=files[i];$('import-button-text').textContent='导入 '+(i+1)+'/'+files.length;
      try{
        if(!/\.xlsx$/i.test(file.name))throw new Error('仅支持 .xlsx');
        if(file.size>20*1024*1024)throw new Error('文件超过 20 MB');
        last=await api('/api/import',{method:'POST',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','X-Filename':encodeURIComponent(file.name)},body:file});
        imported.push(file.name);warnings.push(...(last.warnings||[]));
      }catch(error){failures.push(file.name+'：'+error.message);}
    }
    if(last){
      applyDatasetPayload(last,true);
      try{
        const status=await api('/api/status');state.models=status.models||state.models;
        applyDatasetPayload(status,true);renderModels();
      }catch(error){warnings.push('题集已导入，状态刷新失败：'+error.message);}
      switchTab(state.activeTab);
    }
    const parts=[];
    if(imported.length)parts.push('已导入 '+imported.length+' 个用例集');
    if(failures.length)parts.push('导入失败：\n'+failures.join('\n'));
    if(warnings.length)parts.push([...new Set(warnings)].join('\n'));
    notice(parts.join('\n'),failures.length||warnings.length?'warning':'info');
  }finally{
    state.importBusy=false;$('import-file').value='';$('import-button-text').textContent='导入 Excel';renderDatasetCatalog();syncControls();
  }
}
async function selectDataset(datasetId) {
  if(!datasetId||datasetId===state.dataset?.id||contextBusy()){renderDatasetCatalog();return;}
  state.datasetBusy=true;state.contextToken++;state.statusSeq++;state.refreshBusy=false;syncControls();notice('');
  try{
    const payload=await post('/api/datasets/select',{datasetId});
    applyDatasetPayload(payload,true);switchTab(state.activeTab);
  }catch(error){notice('切换失败：'+error.message,'error');renderDatasetCatalog();}
  finally{state.datasetBusy=false;syncControls();}
}
async function modelAction(id,action) {
  state.pendingModels.add(id);renderModels();notice('');
  try {
    await post(`/api/models/${encodeURIComponent(id)}/${action}`);
    const before=state.chosenModels.size;
    state.pendingModels.delete(id);
    await refreshStatus({quiet:true,restore:false});
    if(action==='load'&&runnable(modelById(id))){if(!before)state.chosenModels.add(id);if(!runnable(currentModel())){$('demo-model').value=id;if(state.dataset)renderCase();}notice(`${modelName(id)} 已加载，可以开始测试。`);}
    else if(action==='unload'){state.chosenModels.delete(id);notice(`${modelName(id)} 已卸载。`);}
  }catch(error){notice(`${modelName(id)} ${action==='load'?'加载':'卸载'}失败：${error.message}`,'error');}
  finally{state.pendingModels.delete(id);await refreshStatus({quiet:true,restore:false});renderModelChoices();syncControls();}
}
async function runDemo() {
  const item=currentCase(),model=currentModel();
  if(!item||!runnable(model)||contextBusy())return;
  const datasetId=state.dataset.id,token=state.contextToken;
  state.demoBusy=true;state.demoResult=null;renderDemoResult(null);syncControls();renderModels();notice('');
  try{
    const data=await post('/api/decide',{datasetId,caseId:item.id,model:model.id}),result=data.result||data;
    if(token!==state.contextToken||datasetId!==state.dataset?.id||result.datasetId&&result.datasetId!==datasetId)return;
    state.demoResult=result;rememberResult(result,datasetId);
    if(result.status==='error')notice(result.error||'本题请求失败','error');
  }catch(error){
    if(token===state.contextToken){state.demoResult={datasetId,caseId:item.id,modelId:model.id,modelName:modelName(model.id),status:'error',error:error.message,labels:{},answers:{},trace:[],correct:Object.keys(item.expected||{}).length?false:null};notice(error.message,'error');}
  }finally{
    state.demoBusy=false;
    if(token===state.contextToken){renderDemoResult(state.demoResult,item);renderScores();renderResults();}
    renderModels();syncControls();
  }
}
async function startJob(forceAll=false) {
  if(contextBusy())return;
  if(forceAll){$('compare-scene').value='all';$('compare-scope').value='all';renderCompareCases();}
  const cases=selectedCases(forceAll),models=selectedModels();
  if(!cases.length||!models.length){notice('请选择模型和用例','warning');return;}
  if(models.some((model)=>!runnable(model))){notice('所选模型尚未就绪','warning');return;}
  const datasetId=state.dataset.id,token=state.contextToken;
  state.jobStarting=true;syncControls();notice('');
  try{
    const data=await post('/api/jobs',{datasetId,models:models.map((model)=>model.id),caseIds:cases.map((item)=>item.id)});
    if(token===state.contextToken&&datasetId===state.dataset?.id)acceptJob(data.job||data);
  }catch(error){if(token===state.contextToken)notice('无法开始测试：'+error.message,'error');}
  finally{state.jobStarting=false;renderModels();syncControls();}
}
async function stopJob() {
  if(!jobActive())return;
  $('compare-stop').disabled=true;
  try { const data=await post(`/api/jobs/${encodeURIComponent(state.job.id)}/stop`);acceptJob(data.job||data); }
  catch(error){notice(`停止请求未成功：${error.message}`,'error');syncControls();}
}
function openResult(caseId,focusedModel=null) {
  const item=allCases().find((candidate)=>candidate.id===caseId);if(!item)return;
  $('dialog-title').textContent=`${item.id} · ${item.title}`;
  const models=focusedModel?state.models.filter((model)=>model.id===focusedModel):state.models;
  $('dialog-body').innerHTML=`<pre class="dialog-context">${esc(text(item.state))}</pre><div class="dialog-gold">${goldHtml(item)}</div><div class="dialog-results">${models.map((model)=>`<article class="dialog-model-result"><h3>${esc(model.name||model.id)}</h3>${resultHtml(resultFor(item.id,model.id),item)}</article>`).join('')}</div>`;
  $('result-dialog').showModal();
}
function download(filename,content,type) {
  const url=URL.createObjectURL(new Blob([content],{type}));
  const link=document.createElement('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function exportResults(format) {
  const stamp=new Date().toISOString().replace(/[:.]/g,'-');
  if(format==='json') {
    const output={tool:'Decision Model Lab',exportedAt:new Date().toISOString(),dataset:state.dataset,models:state.models.map(({id,name,model,local})=>({id,name,model,local})),job:state.job,results:currentResults()};
    download(`Decision-Model-Lab-${stamp}.json`,JSON.stringify(output,null,2),'application/json;charset=utf-8');return;
  }
  const headers=['用例ID','场景','用例名称','模型','测试状态','是否全对','预测答案','标准答案','耗时ms','错误信息'];
  const rows=currentResults().map((result)=>{
    const item=allCases().find((candidate)=>candidate.id===result.caseId);
    return [result.caseId,sceneName(item?.scene||''),item?.title||'',result.modelName||modelName(result.modelId),result.status==='stopped'?'已停止':result.status==='error'?'请求异常':'已完成',result.correct===true?'是':result.correct===false?'否':'未评分',JSON.stringify(result.labels||{}),JSON.stringify(item?.expected||{}),Math.round(result.elapsedMs||0),result.error||''];
  });
  const cell=(value)=>{const safe=String(value??'').replace(/^[=+\-@]/,"'$&");return '"'+safe.replace(/"/g,'""')+'"';};
  download(`Decision-Model-Lab-${stamp}.csv`,'\ufeff'+[headers,...rows].map((row)=>row.map(cell).join(',')).join('\r\n'),'text/csv;charset=utf-8');
}

$('import-file').addEventListener('change',(event)=>importFiles(event.target.files));
$('dataset-select').addEventListener('change',(event)=>selectDataset(event.target.value));
$('refresh-status').addEventListener('click',()=>refreshStatus());
$('model-grid').addEventListener('click',(event)=>{const button=event.target.closest('[data-model-action]');if(button)modelAction(button.dataset.modelId,button.dataset.modelAction);});
$('tab-demo').addEventListener('click',()=>switchTab('demo'));
$('tab-compare').addEventListener('click',()=>switchTab('compare'));
$('demo-scene').addEventListener('change',renderDemoCases);
$('demo-case').addEventListener('change',renderCase);
$('demo-model').addEventListener('change',renderCase);
$('demo-run').addEventListener('click',runDemo);
$('compare-models').addEventListener('change',(event)=>{const input=event.target;if(input.name==='compare-model'){if(input.checked)state.chosenModels.add(input.value);else state.chosenModels.delete(input.value);updatePlan();}});
$('compare-scene').addEventListener('change',renderCompareCases);
$('compare-scope').addEventListener('change',()=>{renderScope();updatePlan();});
$('compare-case').addEventListener('change',updatePlan);
$('toggle-cases').addEventListener('click',()=>{$('case-picker').hidden=!$('case-picker').hidden;renderScope();});
$('select-all-cases').addEventListener('click',()=>{for(const item of casesForScene($('compare-scene').value))state.chosenCases.add(item.id);renderCompareCases();});
$('clear-cases').addEventListener('click',()=>{for(const item of casesForScene($('compare-scene').value))state.chosenCases.delete(item.id);renderCompareCases();});
$('case-checkboxes').addEventListener('change',(event)=>{const input=event.target;if(input.name==='case-selection'){if(input.checked)state.chosenCases.add(input.value);else state.chosenCases.delete(input.value);updatePlan();}});
$('compare-run').addEventListener('click',()=>startJob(false));
$('compare-run-all').addEventListener('click',()=>startJob(true));
$('compare-stop').addEventListener('click',stopJob);
$('result-filter').addEventListener('change',renderResults);
$('case-search').addEventListener('input',renderResults);
$('result-table').addEventListener('click',(event)=>{const button=event.target.closest('[data-result-case],[data-open-case]');if(button)openResult(button.dataset.resultCase||button.dataset.openCase,button.dataset.resultModel);});
$('close-dialog').addEventListener('click',()=>$('result-dialog').close());
$('result-dialog').addEventListener('click',(event)=>{if(event.target===$('result-dialog')){const rect=$('result-dialog').getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)$('result-dialog').close();}});
$('export-json').addEventListener('click',()=>exportResults('json'));
$('export-csv').addEventListener('click',()=>exportResults('csv'));

function initTheme() {
  const media=matchMedia('(prefers-color-scheme: dark)'),select=$('theme-select');
  const apply=(preference)=>{
    const value=['light','dark','system'].includes(preference)?preference:'system';
    document.documentElement.dataset.themePreference=value;
    document.documentElement.dataset.theme=value==='system'?(media.matches?'dark':'light'):value;
    select.value=value;
  };
  apply(document.documentElement.dataset.themePreference||'system');
  select.addEventListener('change',()=>{
    apply(select.value);
    try{localStorage.setItem('decision-lab-theme',select.value);}catch{}
  });
  media.addEventListener('change',()=>{if(select.value==='system')apply('system');});
}
initTheme();

await refreshStatus({quiet:true});
if(state.dataset&&location.hash==='#compare')switchTab('compare');
