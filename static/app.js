'use strict';
const $=selector=>document.querySelector(selector);
const E=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let me=null,csrf='',view='chat',selected=null,users=[],agents=[];
let authEpoch=0,renderEpoch=0,navigationBusy=false,statusCache={initialized:true,mode:'aws'};
let clientConfig={submit_timeout_ms:10000,read_timeout_ms:15000},requestTab='mine',selectedRequest=null;
let requestListEpoch=0,requestDetailEpoch=0;
const sessionChannel=typeof BroadcastChannel==='function'?new BroadcastChannel('roleops-session'):null;
if(sessionChannel)sessionChannel.onmessage=()=>login(statusCache,'登入狀態已在其他分頁變更，請重新登入。');
const controllers=new Set(),submissions=new Map(),decisionLocks=new Set(),decisionFeedback=new Map();
const roles={support:'一般職務／客服',it:'IT／維運',supervisor:'主管職務',maintenance:'IT／維運'};
const perms={'people:manage':'管理人員','agent:manage':'管理助理與派任','request:approve':'核准申請','chat:audit:read':'查閱公司對話','chat:retain:manage':'管理留存與回收','ops:read':'維運查閱'};
class StaleResponse extends Error{}
class ApiError extends Error{constructor(message,status=0){super(message);this.status=status;}}
function notice(text){$('#notice').textContent=text;}
function can(permission){return !!me?.user?.permissions?.includes(permission);}
function formData(form){return Object.fromEntries(new FormData(form));}
function bind(selector,handler){const node=$(selector);if(node)node.onclick=()=>guard(handler);}
async function guard(handler){try{await handler();}catch(error){if(!(error instanceof StaleResponse))notice(error.message);}}

async function api(path,method='GET',data,options={}){
  const epoch=authEpoch,render=renderEpoch,controller=new AbortController();controllers.add(controller);
  const timeout=method==='POST'&&path==='/api/requests'?clientConfig.submit_timeout_ms:clientConfig.read_timeout_ms;
  const timer=setTimeout(()=>controller.abort(),timeout);
  try{
    const response=await fetch(path,{method,credentials:'same-origin',signal:controller.signal,
      headers:{'Content-Type':'application/json','x-csrf-token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    let body;try{body=await response.json();}catch{throw new ApiError('回應無法確認，請查詢原申請狀態。',response.status);}
    if(epoch!==authEpoch)throw new StaleResponse();
    const responseUser=response.headers.get('x-roleops-user');
    if(me&&responseUser&&responseUser!==me.user.id){login(statusCache,'登入身分已在其他分頁變更，請重新登入。');throw new StaleResponse();}
    if(response.status===401){login(statusCache,'登入已失效，請重新登入。');throw new StaleResponse();}
    if(render!==renderEpoch&&!options.ignoreRender)throw new StaleResponse();
    if(!response.ok){const error=new ApiError(typeof body.detail==='string'?body.detail:'操作未完成，請重新查詢。',response.status);error.payload=body;throw error;}
    return body;
  }catch(error){
    if(epoch!==authEpoch)throw new StaleResponse();
    if(error.name==='AbortError')throw new ApiError('等待已超時，請查詢原申請狀態。');
    throw error;
  }finally{clearTimeout(timer);controllers.delete(controller);}
}
function closeNavigation(focus=false){
  $('#nav-toggle').setAttribute('aria-expanded','false');$('#nav').classList.remove('is-open');
  if(focus&&!$('#nav-toggle').hidden)$('#nav-toggle').focus();
}
function clearPrivateState(){
  companionClear();clearInteractionState();
  ++authEpoch;++renderEpoch;for(const c of controllers)c.abort();controllers.clear();
  me=null;csrf='';selected=null;users=[];agents=[];selectedRequest=null;view='chat';requestTab='mine';
  submissions.clear();decisionLocks.clear();decisionFeedback.clear();++requestListEpoch;++requestDetailEpoch;$('#decision-dialog')?.remove();$('#identity').replaceChildren();$('#nav').replaceChildren();
  $('#nav').hidden=true;$('#nav-toggle').hidden=true;$('#logout').hidden=true;closeNavigation();
  $('#root').replaceChildren();notice('');
}
function environmentLabel(status){
  const offline=window.roleopsDemo||status.mode!=='aws';$('#mode').hidden=!offline;
  $('#mode').textContent=offline?(window.roleopsDemo?'離線流程展示 · 合成資料':'本機測試 · 合成資料'):'';
}
function login(status,reason=''){
  clearPrivateState();environmentLabel(status);
  $('#root').innerHTML=`<main id="workspace" class="login"><form id="loginform"><h1>${status.initialized?'登入工作平台':'初始化平台管理員'}</h1><p>使用自己的帳號登入，功能依公司授權提供。</p><label>帳號</label><input name="login" autocomplete="username" required pattern="[A-Za-z0-9_.-]{3,40}"><label>密碼</label><input name="password" type="password" minlength="10" autocomplete="${status.initialized?'current-password':'new-password'}" required>${status.initialized||status.browser_bootstrap?'<button class="primary" type="submit">'+(status.initialized?'登入':'建立管理員')+'</button>':'<p>請由操作者在 EC2 完成管理員初始化。</p>'}<p class="muted">公司對話依留存政策保存，經授權查閱會留下紀錄。</p></form></main>`;
  addPasswordToggles($('#loginform'));notice(reason);
  $('#loginform').onsubmit=event=>{event.preventDefault();guard(async()=>{
    const form=event.target,button=form.querySelector('button[type=submit]');if(button)button.disabled=true;
    try{
      const body=formData(form);
      if(!status.initialized){await api('/api/bootstrap','POST',body);status.initialized=true;login(status,'管理員已建立，請登入。');return;}
      await api('/api/auth/login','POST',body);sessionChannel?.postMessage('changed');form.reset();await screen();notice('登入成功。');
    }finally{if(button?.isConnected)button.disabled=false;}
  });};
}
async function start(){
  statusCache=await api('/api/status');environmentLabel(statusCache);
  try{await screen();}catch(error){if(!(error instanceof StaleResponse))login(statusCache,error.message);}
}
async function logout(){
  const token=csrf;sessionChannel?.postMessage('changed');login(statusCache,'已登出。');
  try{await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','x-csrf-token':token},body:'{}'});}catch{notice('畫面已清除；連線恢復後請重新登入。');}
}
function navigationItems(){return [
  ['chat','我的助理',true],['requests','申請與審核',true],
  ['people','人員與助理管理',can('people:manage')||can('agent:manage')],
  ['archive','治理與稽核',can('chat:audit:read')||can('ops:read')],['ops','系統維運',can('ops:read')]
].filter(item=>item[2]);}
async function navigateTo(id){
  if(navigationBusy)return;navigationBusy=true;
  for(const b of $('#nav').querySelectorAll('button'))b.disabled=true;
  try{view=id;selected=null;closeNavigation();await screen();const h=$('#content h1,#content h2');if(h){h.tabIndex=-1;h.focus();}}
  finally{navigationBusy=false;for(const b of $('#nav').querySelectorAll('button'))b.disabled=false;}
}
async function screen(){
  const epoch=++renderEpoch;const identity=await api('/api/me');if(epoch!==renderEpoch)return;
  if(me&&me.user.id!==identity.user.id){clearPrivateState();return screen();}
  me=identity;csrf=identity.csrf;
  clientConfig=await api('/api/uiux/client-config');
  const companionPreference=await api('/api/uiux/companion-preference');
  me.companionPreference=companionPreference;
  $('#identity').innerHTML=`<span class="identity-label">目前登入</span><strong>${E(me.user.name)}</strong><span>${E(me.user.department)} · ${E(me.user.login||'')}</span>`;
  $('#logout').hidden=false;
  const items=navigationItems();if(!items.some(item=>item[0]===view))view='chat';
  $('#root').innerHTML='<main id="workspace" class="workspace" tabindex="-1"><section id="content" aria-label="工作內容"></section></main>';
  const nav=$('#nav');nav.replaceChildren();nav.hidden=false;$('#nav-toggle').hidden=false;
  for(const [id,label]of items){const b=document.createElement('button');b.type='button';b.dataset.view=id;b.textContent=label;
    if(id===view){b.className='active';b.setAttribute('aria-current','page');}b.disabled=navigationBusy;b.onclick=()=>guard(()=>navigateTo(id));nav.append(b);}
  if(view==='chat')await chat();
  else if(view==='requests')await requests();
  else if(view==='people')await people();
  else if(view==='archive')await governance();else await ops();
  if(epoch===renderEpoch)companionRender();
}
async function checkSession(){
  if(!me||document.hidden)return;
  try{const identity=await api('/api/me','GET',undefined,{ignoreRender:true});
    if(identity.user.id!==me?.user.id||identity.csrf!==csrf)login(statusCache,'登入身分已變更，請重新登入。');
  }catch(error){if(!(error instanceof StaleResponse)&&me)notice('連線暫時中斷；敏感操作仍會由伺服器核對授權。');}
}
setInterval(checkSession,15000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkSession();});
bind('#logout',logout);
bind('#nav-toggle',()=>{const open=$('#nav-toggle').getAttribute('aria-expanded')!=='true';$('#nav-toggle').setAttribute('aria-expanded',String(open));$('#nav').classList.toggle('is-open',open);});
$('#nav').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();closeNavigation(true);}});

const statusText={pending:'等待主管審核',approved:'主管已核准',rejected:'主管已駁回'};
function statusBadge(status){return `<span class="request-status ${E(status)}">${E(statusText[status]||'狀態未提供')}</span>`;}
function timeLabel(value){return typeof value==='number'&&Number.isFinite(value)&&value>0?new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',dateStyle:'medium',timeStyle:'short'}).format(new Date(value*1000))+'（台北時間）':'未記錄';}
function personLabel(person){return person?.current_name?person.current_name+'（目前姓名）':person?.id?'姓名未記錄 · '+person.id:'未記錄';}
function summaryHTML(r){return `<div class="request-summary"><strong>${E(r.system_id||'系統未記錄')}</strong>${statusBadge(r.status)}<p>${E(r.reason||'理由未記錄')}</p><dl><dt>申請編號</dt><dd class="request-id">${E(r.id)}</dd><dt>申請人</dt><dd>${E(personLabel(r.applicant))}</dd><dt>申請時間</dt><dd>${E(timeLabel(r.created_at))}</dd></dl>${r.status==='approved'?'<p class="muted">申請已核准；軟體開通由外部系統執行。</p>':''}</div>`;}
async function copyID(value,feedback){
  try{await navigator.clipboard.writeText(value);feedback.textContent='完整申請編號已複製。';}
  catch{feedback.textContent='複製失敗，請在詳情中選取完整申請編號。';}
}
async function openRequest(rid){selectedRequest=rid;view='requests';requestTab='mine';await screen();}
function resultLink(container,r){
  const actions=document.createElement('div');actions.className='tools';
  const copy=document.createElement('button');copy.type='button';copy.textContent='複製完整編號';
  const detail=document.createElement('button');detail.type='button';detail.textContent='查看申請';
  const feedback=document.createElement('span');feedback.className='muted';feedback.setAttribute('role','status');
  copy.onclick=()=>copyID(r.id,feedback);detail.onclick=()=>guard(()=>openRequest(r.id));actions.append(copy,detail,feedback);container.append(actions);
}
function technicalHTML(values){return `<details class="technical"><summary>技術詳細資料</summary><dl>${Object.entries(values||{}).map(([k,v])=>`<dt>${E(k)}</dt><dd>${E(v??'未記錄')}</dd>`).join('')}</dl></details>`;}
async function proposalCard(card,cid,message){
  const key=message.id,context=cid+':'+key,epoch=authEpoch;
  const query=()=>api('/api/uiux/request-result?conversation_id='+encodeURIComponent(cid)+'&message_id='+encodeURIComponent(key));
  function local(text,kind){if(!card.isConnected||epoch!==authEpoch)return;card.dataset.state=kind;card.setAttribute('aria-busy',String(kind==='submitting'));card.innerHTML=`<div class="submission-feedback ${E(kind)}" role="status">${E(text)}</div>`;}
  function completed(r){
    submissions.set(context,{state:'completed',request:r});local(r.conversation_status?.text||({pending:'申請已建立，等待主管審核。',approved:'主管已核准這項申請；後續開通由權責單位辦理。',rejected:'主管已駁回這項申請。'}[r.status])||'請查詢原申請的目前狀態。','success');
    card.insertAdjacentHTML('beforeend',summaryHTML(r));resultLink(card,r);card.insertAdjacentHTML('beforeend',technicalHTML({'申請 trace':r.technical?.trace_id,'原防重複鍵':r.technical?.idempotency_key}));
  }
  function unknown(text){
    submissions.set(context,{state:'unknown'});local(text||'提交結果尚不明確，請查詢原申請；這張卡片會保留同一個防重複鍵。','unknown');
    const button=document.createElement('button');button.type='button';button.textContent='查詢提交狀態';
    button.onclick=()=>guard(async()=>{button.disabled=true;try{const result=await query();if(result.request)completed(result.request);else unknown(result.state==='processing'?'原申請仍在處理中，請稍後再查詢。':'目前仍查不到確定結果，請保留原提案並聯絡維護人員。');}catch(error){if(error instanceof StaleResponse)return;unknown('狀態查詢暫時失敗，請稍後再查；原申請識別保持。');}finally{if(button.isConnected)button.disabled=false;}});
    card.append(button);card.insertAdjacentHTML('beforeend',technicalHTML({'原防重複鍵':key,'對話識別':cid,'原提案識別':message.id}));
  }
  function editable(reason=''){
    if(!message.proposal){card.remove();return;}
    card.dataset.state='ready';card.setAttribute('aria-busy','false');card.innerHTML=`<form class="request-form"><h3>存取申請建議</h3><label>目標系統</label><input name="system_id" value="${E(message.proposal.system_id)}" readonly><label>申請理由</label><textarea name="reason" maxlength="1000" required>${E(message.proposal.reason)}</textarea><div class="submission-feedback" role="status">${E(reason)}</div><button class="primary" type="submit">確認並送出</button><p class="muted">送出後建立待審核申請；軟體開通由外部系統執行。</p></form>`;
    const form=card.querySelector('form');form.onsubmit=event=>{event.preventDefault();guard(async()=>{
      if(submissions.get(context)?.state==='submitting'||submissions.get(context)?.state==='unknown'||submissions.get(context)?.state==='completed')return;
      const body={...formData(form),idempotency_key:key,confirmed:true,conversation_id:cid,message_id:key};
      submissions.set(context,{state:'submitting'});form.querySelector('button').disabled=true;form.querySelector('textarea').readOnly=true;card.dataset.state='submitting';card.setAttribute('aria-busy','true');form.querySelector('[role=status]').textContent='申請處理中，請稍候…';
      try{
        const result=await api('/api/requests','POST',body,{ignoreRender:true});if(epoch!==authEpoch||!card.isConnected)return;
        if(result.id){completed({...result,applicant:{id:me.user.id,current_name:me.user.name},technical:{trace_id:result.trace_id,idempotency_key:result.idempotency_key}});try{const current=await query();if(current.request&&card.isConnected&&epoch===authEpoch)completed(current.request);}catch{}}
        else unknown();
      }catch(error){
        if(error instanceof StaleResponse||epoch!==authEpoch)return;
        try{const recovered=await query();if(recovered.request){completed(recovered.request);return;}
          if(recovered.state!=='not_submitted'){unknown();return;}
          if([400,403,422].includes(error.status)){
            submissions.delete(context);editable(error.message+'；請檢查內容與提案有效性。');
            const current=card.querySelector('textarea');if(current)current.value=body.reason;card.dataset.state='failed';return;
          }
        }catch(queryError){if(queryError instanceof StaleResponse)return;}
        unknown(error.message);
      }finally{if(card.isConnected&&epoch===authEpoch){card.setAttribute('aria-busy','false');}}
    });};
  }
  const state=submissions.get(context);
  if(state?.state==='submitting'){unknown('同一筆提交正在處理，請查詢原申請。');return;}
  local('正在核對原申請…','checking');
  try{const result=await query();if(epoch!==authEpoch||!card.isConnected)return;
    if(result.request)completed(result.request);
    else if(result.state==='not_submitted'&&!state)editable();
    else unknown();
  }catch(error){if(error instanceof StaleResponse)return;if(!message.proposal){card.remove();return;}unknown('原申請狀態暫時無法確認，請先查詢。');}
}

async function requests(){
  const listEpoch=++requestListEpoch;++requestDetailEpoch;
  const approval=can('request:approve');if(!approval)requestTab='mine';
  const list=await api('/api/uiux/requests'+(requestTab==='review'?'?approvals=true':''));
  if(listEpoch!==requestListEpoch)return;
  $('#content').innerHTML=`<section class="card"><h2>申請與審核</h2><p class="muted">建立、審核及軟體開通分別處理；此處顯示申請的真實狀態。時間以台北時間 UTC+08:00 顯示。</p><div class="tools"><button id="mine-requests" ${requestTab==='mine'?'class="primary"':''}>本人申請</button>${approval?'<button id="review-requests" '+(requestTab==='review'?'class="primary"':'')+'>審核清單</button>':''}<button id="request-chat">向助理提出申請</button></div><div id="requests"></div></section><section id="request-detail" class="card" hidden></section>`;
  bind('#mine-requests',async()=>{requestTab='mine';selectedRequest=null;await requests();});
  bind('#review-requests',async()=>{requestTab='review';selectedRequest=null;await requests();});
  bind('#request-chat',()=>navigateTo('chat'));
  const area=$('#requests');if(!list.length)area.innerHTML='<p class="empty">目前沒有可顯示的申請。</p>';
  for(const record of list){const row=document.createElement('article');row.className='request-row';row.dataset.request=record.id;
    row.innerHTML=`<div class="request-row-heading"><strong>${E(record.system_id)}</strong>${statusBadge(record.status)}</div><p>${E(record.reason||'理由未記錄')}</p><dl><dt>申請人</dt><dd>${E(personLabel(record.applicant))}</dd><dt>申請時間</dt><dd>${E(timeLabel(record.created_at))}</dd><dt>審核人</dt><dd>${record.status==='pending'?'尚未審核':E(personLabel(record.reviewer))}</dd><dt>決策時間</dt><dd>${record.status==='pending'?'尚未審核':E(timeLabel(record.decided_at))}</dd><dt>申請編號</dt><dd><abbr title="${E(record.id)}">${E(record.id.slice(0,12))}…</abbr></dd></dl><div class="request-actions"></div>`;
    const actions=row.querySelector('.request-actions');const detail=document.createElement('button');detail.type='button';detail.textContent='查看詳情';detail.onclick=()=>guard(()=>showRequest(record.id));
    const copy=document.createElement('button');copy.type='button';copy.textContent='複製完整編號';const feedback=document.createElement('span');feedback.setAttribute('role','status');feedback.textContent=decisionFeedback.get(record.id)||'';copy.onclick=()=>copyID(record.id,feedback);actions.append(detail,copy,feedback);
    if(approval&&record.status==='pending'&&record.applicant?.id!==me.user.id){
      for(const [decision,label]of [['approved','核准'],['rejected','拒絕']]){const button=document.createElement('button');button.type='button';button.className=decision==='approved'?'approve':'reject';button.textContent=label;button.disabled=decisionLocks.has(record.id);button.onclick=()=>confirmDecision(record,decision);actions.append(button);}
    }
    area.append(row);
  }
  if(selectedRequest)await showRequest(selectedRequest);
}
async function showRequest(rid){
  const detailEpoch=++requestDetailEpoch;
  const record=await api('/api/uiux/requests/'+encodeURIComponent(rid));if(detailEpoch!==requestDetailEpoch)return;selectedRequest=rid;
  const area=$('#request-detail');area.hidden=false;area.innerHTML=`<h2 tabindex="-1">申請詳情</h2>${summaryHTML(record)}<dl><dt>審核人</dt><dd>${record.status==='pending'?'尚未審核':E(personLabel(record.reviewer))}</dd><dt>${record.status==='pending'?'決策時間':record.status==='rejected'?'拒絕時間':'核准時間'}</dt><dd>${record.status==='pending'?'尚未審核':E(timeLabel(record.decided_at))}</dd><dt>審核理由</dt><dd>未記錄</dd></dl><label>完整申請編號</label><input class="full-request-id" readonly value="${E(record.id)}"><h3>已記錄的狀態歷程</h3><p class="muted">僅列實際保存的建立與決策事件；姓名為目前姓名。同秒事件以編號排序。</p><div id="request-history"></div><p id="history-status" role="status"></p><button id="history-more" hidden>載入更多歷程</button>${technicalHTML({'申請 trace':record.technical?.trace_id,'原防重複鍵':record.technical?.idempotency_key,'助理 ID':record.technical?.agent_id})}`;
  area.querySelector('h2').focus();const seen=new Set();let cursor='';
  async function load(){
    const button=$('#history-more');button.disabled=true;
    try{const result=await api('/api/uiux/requests/'+rid+'/history'+(cursor?'?cursor='+encodeURIComponent(cursor):''));
      if(detailEpoch!==requestDetailEpoch||selectedRequest!==rid)return;
      for(const event of result.items){if(seen.has(event.id))continue;seen.add(event.id);const row=document.createElement('div');row.className='history-event';
        row.innerHTML=`<strong>${E(event.action==='request_created'?'建立申請':event.decision==='approved'?'核准申請':'拒絕申請')}</strong><p>${E(timeLabel(event.at))} · ${E(personLabel(event.actor))}</p>${technicalHTML({'事件 ID':event.id,'事件 trace':event.trace_id})}`;$('#request-history').append(row);}
      cursor=result.next_cursor||'';button.hidden=!cursor;$('#history-status').textContent=cursor?'已載入部分歷程，請繼續載入。':seen.size?'已顯示此申請所有已保存的建立與決策事件。':'此申請尚無已保存的歷程事件。';
    }finally{if(button.isConnected)button.disabled=false;}
  }
  bind('#history-more',load);await load();
}
function confirmDecision(record,decision){
  if(decisionLocks.has(record.id)||$('#decision-dialog'))return;
  const dialog=document.createElement('dialog');dialog.id='decision-dialog';dialog.setAttribute('aria-labelledby','decision-title');
  dialog.innerHTML=`<h2 id="decision-title">確認${decision==='approved'?'核准':'拒絕'}申請</h2>${summaryHTML(record)}<p>請核對上述申請；確認後保存本次決策。</p><div class="decision-actions"><button id="decision-cancel" autofocus>取消</button><button id="decision-confirm" class="${decision==='approved'?'primary':'danger'}">確認${decision==='approved'?'核准':'拒絕'}</button></div>`;
  document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();$('#decision-cancel').focus();
  $('#decision-cancel').onclick=()=>dialog.close();$('#decision-confirm').onclick=()=>guard(async()=>{
    decisionLocks.add(record.id);dialog.close();for(const b of document.querySelectorAll(`[data-request="${record.id}"] .approve,[data-request="${record.id}"] .reject`))b.disabled=true;
    try{await api('/api/approvals/'+record.id,'POST',{decision});decisionFeedback.set(record.id,'決策已保存。');notice('決策已保存。');}
    catch(error){if(error instanceof StaleResponse)return;decisionFeedback.set(record.id,error.message+'，請核對下方最新狀態。');notice(error.message+'，正在讀取最新狀態。');}
    finally{decisionLocks.delete(record.id);}
    if(me)await requests();
  });
}
async function renderGovernanceEvents(target=$('#content')){
  const data = await api('/api/operations');
  if (!target.isConnected) return;
  target.innerHTML = `<section class="card"><h2>平台事件治理</h2><p class="muted">查看何人在何時執行了哪些治理操作，以及系統如何處理。</p>${data.events.map(governanceEvent).join('') || '<p>目前沒有事件。</p>'}</section>`;
}

function systemSpecifications(system) {
  const rows = [['執行環境',system.mode],['部署區域',system.region],
    ['基底程式版本',system.release],['功能修訂（系統回傳）',system.uiux_revision]];
  return `<section id="ops-system-specs"><h3>系統規格資訊</h3><dl>${rows.map(([label,value])=>
    `<dt>${E(label)}</dt><dd>${E(value || '未取得')}</dd>`).join('')}</dl>
    <p class="muted">${E(system.source || '')}</p></section>`;
}

function auditIdentity(person, id) {
  if (person?.current_login) {
    return person.current_name ? `${person.current_login}（目前姓名：${person.current_name}）` : person.current_login;
  }
  if (id === 'bootstrap') return '系統初始化';
  if (id === 'system') return '系統';
  return id ? `帳號未記錄（識別碼：${id}）` : '未記錄';
}

function governanceEvent(event) {
  const detail = event.detail || {};
  const actions = {
    login:'登入平台', AuthorizationDenied:'嘗試存取受限制的功能',
    SourceVersionExcluded:'查詢文件時排除不適用的版本', conversation_read:'查閱對話正文',
    conversation_rename:'重新命名對話', conversation_delete:'將對話移入回收區',
    conversation_restore:'復原對話', conversation_hold:'將對話設為調查保留',
    conversation_release_hold:'解除對話的調查保留', conversation_hide:'從員工清單移除對話',
    conversation_purged:'清除已到期的對話正文', request_decision:'審核軟體存取申請',
    request_created:'建立軟體存取申請', user_created:'新增帳號',
    EgressDenied:'嘗試送出受限制的資料', VerifiedSourceAnswer:'以已驗證的文件完成回答'
  };
  const outcomes = {success:'成功',denied:'已拒絕',filtered:'已排除',routed:'已派送',recovered:'已恢復'};
  const resources = {platform_operations:'系統維運', platform_audit:'治理與稽核'};
  const permissions = {'ops:read':'維運查閱','chat:audit:read':'公司對話查閱'};
  const targets = [
    [detail.conversation_id,detail.title || detail.after || '對話 '+detail.conversation_id],
    [detail.request_id,'申請 '+detail.request_id],[detail.login,'帳號 '+detail.login],
    [detail.system_id,'系統 '+detail.system_id],[detail.document_id,'文件 '+detail.document_id],
    [detail.resource,resources[detail.resource] || detail.resource]
  ];
  const target = targets.filter(([value]) => value).map(([,label])=>label).join('、');
  const actor = event.actor?.current_login || auditIdentity(event.actor,event.actor_id);
  const action = Object.hasOwn(actions,event.action) ? actions[event.action] : event.action || '動作未記錄';
  const result = detail.decision === 'approved' ? '已核准' : detail.decision === 'rejected' ? '已駁回'
    : Object.hasOwn(outcomes,event.outcome) ? outcomes[event.outcome] : event.outcome || '未記錄';
  const reason = detail.required_permission
    ? `需要「${permissions[detail.required_permission] || detail.required_permission}」權限。` : '';
  return `<details class="governance-event"><summary>
    <strong>${E(actor)}　${E(action)}${target?'｜'+E(target):''}</strong>
    <span class="muted">${E(timeLabel(event.at))}　·　結果：${E(result)}</span></summary>
    ${reason?`<p>${E(reason)}</p>`:''}
    <p>追蹤碼：${E(detail.trace_id || event.trace_id || '未記錄')}</p>
    <details><summary>原事件資料</summary><pre>${E(JSON.stringify(detail,null,2))}</pre></details></details>`;
}

let governancePage = 'conversations';
async function governance() {
  const tabs = [
    ['conversations','對話紀錄',can('chat:audit:read')],
    ['events','治理事件',can('ops:read')]
  ].filter(([, , allowed])=>allowed);
  if (!tabs.some(([id])=>id===governancePage)) governancePage = tabs[0]?.[0];
  $('#content').innerHTML = '<div class="engineering-subnav" id="governance-nav"></div><section id="governance-body"></section>';
  renderSectionTabs($('#governance-nav'),tabs,governancePage,async id=>{
    governancePage=id;
    await governance();
  });
  if (governancePage==='events') await renderGovernanceEvents($('#governance-body'));
  else if (governancePage==='conversations') await archive($('#governance-body'));
}

function renderSectionTabs(container,tabs,current,select) {
  for (const [id,label] of tabs) {
    const button=document.createElement('button');
    button.type='button';
    button.textContent=label;
    if (id===current) {
      button.className='primary';
      button.setAttribute('aria-current','page');
    }
    button.onclick=()=>guard(()=>select(id));
    container.append(button);
  }
}

async function archive(content=$('#content')) {
  const list = await api('/api/conversations?management=true');
  if (view !== 'archive' || !content.isConnected) return;
  content.innerHTML = `<section class="card" id="audit-workspace">
    <h2>歷史對話紀錄</h2><p class="muted">每次查閱正文均寫入稽核。員工列表移除仍保留公司正文。正式刪除進入 30 天回收期；調查保留期間暫停清除。</p>
    <form id="audit-search" class="directory-filters" role="search" aria-label="搜尋對話">
      <label>搜尋對話<input id="audit-query" type="search" maxlength="100" placeholder="帳號、姓名或對話標題"></label>
      <label>部門<select id="audit-department"><option value="">全部部門</option></select></label>
      <label>日期<select id="audit-month"><option value="">全部日期</option></select></label>
      <label>排序<select id="audit-sort"><option value="newest">最新建立</option><option value="oldest">最早建立</option><option value="account">帳號順序</option></select></label>
      <button type="submit">搜尋</button>
    </form>
    <div class="audit-grid">
      <section id="audit-results" aria-label="對話清單">
        <p id="audit-count" role="status"></p><div id="archive-list"></div>
        <div class="tools"><button id="audit-prev">上一頁</button><button id="audit-next">下一頁</button></div>
      </section>
      <section id="audit-reader" hidden aria-labelledby="audit-heading">
        <button id="audit-back" type="button">返回對話清單</button>
        <header class="audit-detail-heading"><h3 id="audit-heading" tabindex="-1"></h3></header>
        <dl class="audit-metadata" id="audit-context">
          <div><dt>部門：</dt><dd id="audit-detail-department"></dd></div>
          <div><dt>帳號：</dt><dd id="audit-detail-owner"></dd></div>
          <div><dt>建立日期：</dt><dd id="audit-detail-date"></dd></div>
          <div><dt>對話狀態：</dt><dd id="audit-detail-state"></dd></div>
        </dl>
        <p id="audit-status" role="status"></p><div id="messages" class="messages readonly" aria-label="對話正文"></div>
      </section>
    </div></section>`;
  const workspace = $('#audit-workspace'), reader = $('#audit-reader');
  const search = $('#audit-query'), department = $('#audit-department');
  const month = $('#audit-month'), sort = $('#audit-sort');
  const departments = [...new Set(list.map(c=>c.owner?.current_department).filter(Boolean))];
  for (const name of departments.sort((a,b)=>a.localeCompare(b,'zh-TW'))) department.add(new Option(name,name));
  const taipeiNow = new Date(Date.now() + 8*3600*1000);
  for (let offset=0; offset<12; offset++) {
    const date = new Date(Date.UTC(taipeiNow.getUTCFullYear(),taipeiNow.getUTCMonth()-offset,1));
    const year = date.getUTCFullYear(), number = String(date.getUTCMonth()+1).padStart(2,'0');
    month.add(new Option(String(year-1911)+number,`${year}-${number}`));
  }
  const pageSize = 20;
  let page = 0, opened = 0, trigger, terms = [], selectedDepartment = '', selectedMonth = '', order = 'newest';
  const states = {active:'使用中',deleted:'回收期',purged:'已清除',purging:'清除中'};
  const stateLabel = c => [states[c.state] || c.state || '未記錄',
    c.hidden && '員工列表已移除', c.hold && '調查保留'].filter(Boolean).join(' · ');
  const timestamp = c => c.updated_at || c.created_at;
  const taipeiDay = value => typeof value === 'number' && Number.isFinite(value) && value > 0
    ? new Date((value + 8*3600)*1000).toISOString().slice(0,10) : '';
  function closeReader() {
    ++opened;
    ++messageSerial;
    reader.hidden = true;
    workspace.classList.remove('audit-open');
    reader.querySelector('#messages').replaceChildren();
    trigger?.focus();
  }
  async function openReader(c, button) {
    const ticket = ++opened;
    trigger = button;
    const messages = $('#messages'), status = $('#audit-status');
    messages.replaceChildren();
    delete messages.dataset.conversation;
    $('#audit-heading').textContent = c.title || '標題未記錄';
    $('#audit-detail-department').textContent = c.owner?.current_department || '未記錄';
    $('#audit-detail-owner').textContent = c.owner?.current_login || auditIdentity(c.owner,c.owner_id);
    $('#audit-detail-date').textContent = timeLabel(c.created_at);
    $('#audit-detail-state').textContent = stateLabel(c);
    status.textContent = '正在讀取正文…';
    reader.hidden = false;
    workspace.classList.add('audit-open');
    $('#audit-heading').focus();
    try {
      await renderMessages(c.id,true);
      if (ticket === opened && reader.isConnected) status.textContent = '本次查閱已留稽核。';
    } catch (error) {
      if (ticket === opened && reader.isConnected && !(error instanceof StaleResponse)) status.textContent = error.message;
    }
  }
  function renderList() {
    const filtered = list.filter(c => {
      const searchable = [c.owner?.current_login,c.owner?.current_name,c.owner_id,c.title]
        .filter(Boolean).join(' ').toLowerCase();
      return (!selectedDepartment || c.owner?.current_department === selectedDepartment)
        && (!selectedMonth || taipeiDay(c.created_at).slice(0,7) === selectedMonth)
        && terms.every(term => searchable.includes(term));
    });
    filtered.sort((a,b) => {
      if (order === 'account') {
        const accountOrder = (a.owner?.current_login || '').localeCompare(b.owner?.current_login || '');
        if (accountOrder) return accountOrder;
      }
      if (!a.created_at || !b.created_at) return Number(Boolean(b.created_at)) - Number(Boolean(a.created_at));
      return order === 'oldest' ? a.created_at-b.created_at : b.created_at-a.created_at;
    });
    const pages = Math.max(1,Math.ceil(filtered.length/pageSize));
    page = Math.min(page,pages-1);
    $('#audit-count').textContent = `${filtered.length} 則 · 第 ${page+1}／${pages} 頁（台北時間）`;
    $('#audit-prev').disabled = page === 0;
    $('#audit-next').disabled = page >= pages-1;
    const rows = $('#archive-list');
    rows.replaceChildren();
    for (const c of filtered.slice(page*pageSize,(page+1)*pageSize)) {
      const row = document.createElement('div');
      row.className = 'row';
      row.innerHTML = `<strong>${E(c.title || '標題未記錄')}</strong> · ${E(stateLabel(c))}
        <p>${E(auditIdentity(c.owner,c.owner_id))} · ${E(timeLabel(timestamp(c)))}</p>
        ${c.purge_after?`<small>到期：${E(timeLabel(c.purge_after))}</small>`:''}<div class="tools"></div>`;
      const tools = row.querySelector('.tools');
      if (!['purged','purging'].includes(c.state)) {
        const button = document.createElement('button');
        button.textContent = '查閱正文';
        button.onclick = () => openReader(c,button);
        tools.append(button);
        if (can('chat:retain:manage')) {
          const actions = [[c.state==='active'?'delete':'restore',c.state==='active'?'正式刪除':'復原'],
                           [c.hold?'release-hold':'hold',c.hold?'解除調查保留':'調查保留']];
          for (const [action,label] of actions) {
            const control = document.createElement('button');
            control.textContent = label;
            control.onclick = () => guard(async () => {
              const reason = prompt(label+'：請填寫操作原因');
              if (!reason) return;
              await api('/api/conversations/'+encodeURIComponent(c.id),'PATCH',{action,reason});
              await governance();
              notice('留存狀態已更新。');
            });
            tools.append(control);
          }
        }
      }
      rows.append(row);
    }
    if (!filtered.length) rows.textContent = '沒有符合條件的對話。';
  }
  $('#audit-search').onsubmit = event => {
    event.preventDefault();
    terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    selectedDepartment = department.value;
    selectedMonth = month.value;
    order = sort.value;
    page = 0;
    renderList();
  };
  search.oninput = () => {
    if (!search.value && terms.length) $('#audit-search').requestSubmit();
  };
  $('#audit-prev').onclick = () => {--page;renderList();};
  $('#audit-next').onclick = () => {++page;renderList();};
  $('#audit-back').onclick = closeReader;
  renderList();
}

guard(start);

let opsPage='monitoring', opsIncidentId=null;

async function openEngineeringIncident(id=null){
 opsIncidentId=id;opsPage='incidents';await ops();
}
function engineeringAssets(record,data){return record.assets.map(id=>window.roleopsDemo?'<details class="demo-asset" data-asset="'+E(id)+'"><summary>展開來源原件：'+E(data.assets[id].label)+'</summary><pre></pre></details>':'<p><a href="/api/engineering/assets/'+encodeURIComponent(id)+'">下載證據：'+E(data.assets[id].label)+'</a></p>').join('')}
async function ops(){
 const fullMaintenance=!window.roleopsDemo&&can('ops:read')&&can('people:manage');
 const buttons=[
  ['monitoring','運營概況',fullMaintenance],
  ['monitor','系統資訊與自檢',true],
  ['journals','維運日誌',true],
  ['incidents','工程事故紀錄',true]
 ].filter(([, , allowed])=>allowed);
 if(!buttons.some(([id])=>id===opsPage))opsPage=buttons[0][0];
 $('#content').innerHTML='<div class="engineering-subnav" id="opsnav"></div><section id="opsbody"></section>';
 renderSectionTabs($('#opsnav'),buttons,opsPage,async id=>{opsIncidentId=null;opsPage=id;await ops();});
 const area=$('#opsbody');
 if(opsPage==='monitoring'&&buttons.some(([id])=>id===opsPage)){await renderMonitoring(area);return;}
 if(opsPage==='incidents'&&buttons.some(([id])=>id===opsPage)){await renderIncidentRecords(area,fullMaintenance,opsIncidentId);return;}
 if(opsPage==='monitor'){
  const [data,system]=await Promise.all([
    api('/api/selfcheck'),
    api('/api/uiux/system').catch(error=>({source:error.message || '系統規格暫時無法讀取。'}))
  ]);
  if(!area.isConnected)return;
  const names={ok:'已通過該項檢查',attention:'需要查核',unknown:'待確認'};
  area.innerHTML='<div class="card"><h2>系統資訊與自檢</h2>'+systemSpecifications(system)
    +'<div class="tools" id="selfcheck-actions"><button id="monitor-read">讀取最新快照</button><button id="monitor-journal">查看維運日誌</button><button id="monitor-report">查看工程事故紀錄</button></div>'
    +'<section id="selfcheck-results"><h3>自檢結果</h3><p class="muted">來源：'+E(data.source||'未取得')+' · 最後檢查：'+E(timeLabel(data.checked_at))+'</p>'
    +(window.roleopsDemo?'<label>展示情境（合成示意，不執行檢查）</label><select id="demo-monitor"><option value="disconnected">尚未接入</option><option value="failed">歷史索引卡關</option><option value="stale">資料過期示意</option><option value="healthy">檢查通過示意</option></select>':'')
    +'<p>'+(!data.fresh?E(data.message||'資料已過期，不能判定健康。'):'以下僅表示已列檢查範圍；未知項目不能判為正常。')+'</p>'
    +data.checks.map(c=>'<details class="engineering-detail"><summary>'+E(c.title)+' · '+E(data.fresh?(names[c.status]||'待確認'):'資料過期／待確認')+'</summary><p>'+E(c.detail)+'</p></details>').join('')
    +'<p class="muted">網站讀取排程已產生的快照。自檢不呼叫模型或建立申請。</p></section></div>';

  bind('#monitor-read',ops);bind('#monitor-journal',async()=>{opsPage='journals';await ops()});bind('#monitor-report',()=>openEngineeringIncident());
  if(window.roleopsDemo){$('#demo-monitor').value=window.roleopsDemoMonitor||'disconnected';$('#demo-monitor').onchange=e=>{window.roleopsDemoMonitor=e.target.value;guard(ops)}}return;
 }
 const data=await api('/api/engineering');
 if(!area.isConnected)return;
 renderMaintenanceJournal(area,data);
 bind('#journal-report',()=>openEngineeringIncident());
 bind('#journal-monitor',async()=>{opsPage='monitor';await ops()});
}

function extensionSources(row,cid,message){
 if(!message.sources?.length)return;
 for(const [i,source]of message.sources.entries()){
  const details=document.createElement('details');details.className='extension-source';
  const summary=document.createElement('summary');summary.textContent='展開來源原文：'+source.document_id+' · '+source.policy_version;
  const body=document.createElement('pre');details.append(summary,body);row.append(details);
  let request=0;
  details.ontoggle=()=>{const current=++request;body.textContent='';if(!details.open)return;
   guard(async()=>{const value=await api('/api/conversations/'+encodeURIComponent(cid)+'/sources/'+encodeURIComponent(message.id)+'/'+i);
    if(details.isConnected&&details.open&&current===request)body.textContent=value.body;});};
 }
}

const roleChoices=Object.entries(roles).filter(([k])=>k!=='maintenance');
