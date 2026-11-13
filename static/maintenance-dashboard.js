const monitoringCards = [
 ['自檢心跳','每約五分鐘回報一次',['heartbeat']],
 ['EC2 狀態檢查','失敗狀態的區間最大值',['ec2']],
 ['主佇列最舊訊息','等待時間，單位為秒',['queue']],
 ['DLQ 可見訊息','可讀取的近似筆數',['dlq']],
 ['文件索引更新','成功／失敗次數',['index_ok','index_failure']],
 ['AI 問答成功','程式記錄的成功事件',['ai_ok']],
 ['AI Token 用量','輸入／輸出 Token 數',['input_tokens','output_tokens']],
 ['MCP 申請工具','成功呼叫事件',['mcp_ok']],
 ['MCP 平均耗時','成功呼叫的平均延遲，單位為毫秒',['mcp_ms']],
];
const monitoringSeries = {
 index_ok:'成功', index_failure:'失敗', input_tokens:'輸入', output_tokens:'輸出',
};
const monitoringAlarms = {
 'roleops-ec2-statuscheck':'EC2 狀態檢查', 'roleops-index-failure':'索引更新失敗',
 'roleops-mcp-timeout':'MCP 逾時', 'roleops-selfcheck-heartbeat':'心跳缺失',
};
const monitoringState = {
 OK:'正常', ALARM:'告警', INSUFFICIENT_DATA:'資料不足', missing:'尚未建立', unavailable:'讀取失敗',
};
let monitoringWindow = '1h';

function monitoringTime(epoch){
 return new Date(epoch*1000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'});
}

function monitoringChart(window, keys, title){
 const rows=keys.map(key=>window.series[key]);
 const points=rows.flatMap(row=>row.points);
 if(!points.length)return '';
 const max=Math.max(1,...points.map(([,value])=>value));
 const colors=['#007b80','#a95e13'];
 const x=at=>34+294*(at-window.start)/(window.end-window.start);
 const y=value=>68-49*value/max;
 const layers=rows.map((row,i)=>row.points.map(([at,value],j)=>{
  const prior=row.points[j-1];
  const line=prior&&at-prior[0]===window.period?'<line x1="'+x(prior[0])+'" y1="'+y(prior[1])+'" x2="'+x(at)+'" y2="'+y(value)+'" stroke="'+colors[i]+'"/>':'';
  return line+'<circle cx="'+x(at)+'" cy="'+y(value)+'" r="2.4" fill="'+colors[i]+'"><title>'+E(monitoringTime(at)+' · '+value)+'</title></circle>';
 }).join('')).join('');
 return '<svg class="maintenance-chart" viewBox="0 0 334 94" role="img" aria-label="'+E(title)+'">'
  +'<path d="M34 19H328 M34 68H328" stroke="#dae5eb"/><text x="2" y="23">'+E(String(+max.toPrecision(4)))+'</text><text x="2" y="72">0</text>'
  +layers+'<text x="34" y="88">區間起點</text><text x="328" y="88" text-anchor="end">區間終點</text></svg>';
}

async function renderMonitoring(area){
 const selected=monitoringWindow;
 const [data,selfcheck]=await Promise.all([
  api('/api/engineering/monitoring?window='+selected),api('/api/selfcheck')
 ]);
 const heading='<div class="card"><h2>運營概況</h2><p>最新自檢快照：'+E(selfcheck.fresh?monitoringTime(selfcheck.checked_at):'未取得有效快照')+'</p>'
  +'<div class="tools"><button id="monitoring-hour" aria-pressed="'+(selected==='1h')+'">最近 1 小時</button>'
  +'<button id="monitoring-day" aria-pressed="'+(selected==='24h')+'">最近 24 小時</button></div></div>';
 if(data.status!=='fresh'){
  area.innerHTML=heading+'<p class="card">'+(data.status==='stale'?'CloudWatch 快照已過期。':'尚未取得 CloudWatch 快照。')+'</p>';
 }else{
  const window=data.metrics;
  const charts=monitoringCards.map(([title,note,keys])=>{
   const labels=keys.map((key,i)=>{
    const {status,points}=window.series[key];
    const message=status==='ok'?'最後樣本 '+points.at(-1)[1]:status==='empty'?'沒有資料':'讀取失敗';
    return '<span class="maintenance-series series-'+i+'">'+E((monitoringSeries[key]?monitoringSeries[key]+' · ':'')+message)+'</span>';
   }).join('');
   return '<article class="card"><h3>'+E(title)+'</h3><p class="muted">'+E(note)+'</p>'+labels+monitoringChart(window,keys,title)+'</article>';
  }).join('');
  const alarms=Object.entries(monitoringAlarms).map(([key,name])=>'<li>'+E(name)+' · '+E(monitoringState[data.alarms[key]]||'待確認')+'</li>').join('');
  area.innerHTML=heading+'<p class="card muted">CloudWatch 擷取：'+E(monitoringTime(data.sampled_at))
   +' · '+E(monitoringTime(window.start))+' 至 '+E(monitoringTime(window.end))
   +' · 每 '+window.period/60+' 分鐘彙整。空白表示沒有樣本。</p><div class="maintenance-grid">'+charts
   +'</div><div class="card"><h3>RoleOps 告警</h3><ul>'+alarms+'</ul><p class="muted">Email 送達另依通知紀錄驗證。</p></div>';
 }
 for(const [id,key] of [['monitoring-hour','1h'],['monitoring-day','24h']]){
  area.querySelector('#'+id).onclick=()=>guard(async()=>{
   monitoringWindow=key;
   await renderMonitoring(area);
  });
 }
}

function bindEngineeringReportLinks(area){
 for(const button of area.querySelectorAll('[data-report-id]')){
  button.onclick=()=>guard(()=>openEngineeringIncident(button.dataset.reportId));
 }
}

function renderMaintenanceJournal(area,data){
 const reports=new Set(data.reports.map(report=>report.id));
 const days=new Map();
 const items=[...data.journals].sort((a,b)=>String(b.snapshot_date||'').localeCompare(String(a.snapshot_date||'')));
 for(const item of items){
  const date=item.snapshot_date||'日期未記錄';
  if(!days.has(date))days.set(date,[]);
  days.get(date).push(item);
 }
 area.innerHTML='<section class="card"><h2>維運日誌</h2><p class="muted">按日期查看已發布的維運處置。發布版本：'+E(data.version)+'</p>'
  +'<div class="tools"><button id="journal-report">查看工程事故紀錄</button><button id="journal-monitor">回系統資訊與自檢</button></div>'
  +([...days].map(([day,entries])=>'<section class="journal-day"><h3>'+E(day)+'</h3>'
   +entries.map(j=>'<article class="engineering-detail journal-entry"><h4>'+E(j.title)+'</h4>'
    +'<p class="muted">'+E(j.status||'狀態未記錄')+'</p><dl class="journal-fields">'
    +[['為什麼處理',j.purpose],['做了什麼',j.action],['結果',j.result],['後續',j.next]]
     .map(([label,value])=>'<div><dt>'+E(label)+'</dt><dd>'+E(value||'未記錄')+'</dd></div>').join('')
    +'</dl><details><summary>查看關聯事件與原始證據</summary><p>關聯事件：'+E(j.incident_id||'未記錄')+'</p>'
    +(j.technical?'<p>'+E(j.technical)+'</p>':'')+engineeringAssets(j,data)
    +(reports.has(j.incident_id)?'<button type="button" data-report-id="'+E(j.incident_id)+'">查看這件工程事故</button>':'')
    +'</details></article>').join('')+'</section>').join('')||'<p>目前沒有已發布的維運日誌。</p>')+'</section>';
 bindEngineeringReportLinks(area);
}

function incidentReportHTML(report,data){
 const sections=[['發生什麼事',report.problem],['影響範圍',report.impact],
  ['怎麼查到原因',report.reasoning],['怎麼處理',report.change],
  ['怎麼確認結果',report.verification],['後續預防',report.prevention]];
 return '<section class="incident-report"><h4>除錯紀錄</h4>'
  +sections.map(([label,value])=>'<h5>'+E(label)+'</h5><p>'+E(value||'未記錄')+'</p>').join('')
  +'<details><summary>技術細節與分工</summary><p>'+E(report.technical||'未記錄')
  +'</p><p>'+E(report.contribution||'未記錄')+'</p></details>'
  +'<h5>原始附件</h5>'+(engineeringAssets(report,data)||'<p>此報告沒有已發布附件。</p>')+'</section>';
}

async function renderIncidentRecords(area,includeSummaries,openId){
 const [summary,data]=await Promise.all([
  includeSummaries?api('/api/engineering/incidents').catch(()=>null):null,
  api('/api/engineering').catch(()=>null)
 ]);
 if(!area.isConnected)return;
 const records=new Map((summary?.incidents||[]).map(item=>[item.id,{item}]));
 for(const report of data?.reports||[]){
  if(records.has(report.id))records.get(report.id).report=report;
  else records.set(report.id,{report});
 }
 const rows=[...records.entries()].sort(([,a],[,b])=>
  String(b.item?.date||b.report?.snapshot_date||'').localeCompare(String(a.item?.date||a.report?.snapshot_date||'')));
 const categories=[...new Set(rows.map(([,entry])=>entry.item?.category||'其他'))];
 area.innerHTML='<div class="card incident-toolbar"><div><h2>工程事故紀錄</h2><p class="muted">依日期查看事故，展開可閱讀除錯內容及附件。</p></div>'
  +'<label class="incident-filter">子系統 <select id="incident-category"><option value="">全部</option>'
  +categories.map(category=>'<option>'+E(category)+'</option>').join('')+'</select></label></div>'
  +(data?'':'<p class="card">暫時無法讀取已發布的除錯報告。</p>')
  +(includeSummaries&&!summary?'<p class="card">暫時無法讀取事故摘要。</p>':'')
  +'<p id="incident-count" class="incident-count" aria-live="polite"></p><ol id="incident-list" class="incident-timeline"></ol>';
 const filter=area.querySelector('#incident-category'),list=area.querySelector('#incident-list');
 function render(){
  const visible=rows.filter(([,entry])=>!filter.value||(entry.item?.category||'其他')===filter.value);
  area.querySelector('#incident-count').textContent='顯示 '+visible.length+' 件工程事故';
  list.innerHTML=visible.map(([id,{item,report}])=>{
   const date=item?.date||report?.snapshot_date||'日期未記錄';
   const title=item?.title||report.title;
   return '<li class="incident-entry"><time>'+E(date)+'</time><details class="incident-case" data-incident-id="'+E(id)+'"'+(id===openId?' open':'')+'>'
    +'<summary><span class="incident-heading"><strong>'+E(title)+'</strong><span class="incident-badges"><span>'+E(item?.category||'除錯報告')
    +'</span><span>'+E(item?.status||report?.status||'狀態未記錄')+'</span></span></span><span class="incident-chevron" aria-hidden="true"></span></summary>'
    +'<div class="incident-body">'+(item?'<dl>'+[['影響',item.impact],['處置',item.action],['驗證',item.verification]]
     .map(([label,value])=>'<div><dt>'+E(label)+'</dt><dd>'+E(value||'未記錄')+'</dd></div>').join('')+'</dl>':'')
    +'<div class="incident-source"><strong>事件編號</strong><span>'+E(id)+'</span>'+(item?.source?'<span>'+E(item.source)+'</span>':'')+'</div>'
    +(report?incidentReportHTML(report,data):'<p class="muted">'+(data?'此事件目前有摘要，尚無同編號的完整報告。':'完整報告暫時無法讀取。')+'</p>')
    +'</div></details></li>';
  }).join('');
 }
 filter.onchange=render;
 render();
 if(openId){
  const opened=[...list.querySelectorAll('[data-incident-id]')].find(node=>node.dataset.incidentId===openId);
  if(opened){opened.querySelector('summary').focus();opened.scrollIntoView({block:'nearest'});}
 }
}
