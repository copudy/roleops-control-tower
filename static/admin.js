'use strict';
let adminState={users:{q:'',department:'',active:'all',sort:'login',page:1},agents:{q:'',page:1}},adminSerial={users:0,agents:0};
function directoryURL(kind,state,size){return '/api/uiux/admin/'+kind+'?'+new URLSearchParams({...state,page_size:size});}
async function people(){
 const node=$('#content');node.innerHTML='<div class="admin-layout"><section class="admin-directories"><section class="card" id="agent-directory" hidden><div class="tools"><h2>助理目錄</h2><button id="addagent" type="button">＋ 建立助理</button></div><form id="agent-search" class="directory-filters"><label>搜尋助理<input name="q" maxlength="100" placeholder="名稱、職務規範"></label><button type="submit">搜尋</button></form><div id="agents" class="agent-grid"></div><div id="agent-pages" class="directory-pages"></div></section><section class="card" id="user-directory" hidden><div class="tools"><h2>公司人員</h2><button id="adduser" type="button">＋ 建立員工</button></div><form id="user-search" class="directory-filters"><label>搜尋人員<input name="q" maxlength="100" placeholder="帳號、姓名、部門、職稱"></label><label>部門<select name="department"><option value="">全部部門</option></select></label><label>狀態<select name="active"><option value="all">全部狀態</option><option value="true">啟用</option><option value="false">停用</option></select></label><label>排序<select name="sort"><option value="login">帳號順序</option><option value="department">部門順序</option><option value="name">姓名順序</option></select></label><button type="submit">搜尋</button></form><div id="users"></div><div id="user-pages" class="directory-pages"></div></section></section><section class="card admin-editor"><h2>人員與助理設定</h2><p class="muted">從左側選擇編輯，或建立人員／助理。形象與名稱供辨識，實際操作沿公司授權。</p><div id="editform"><p class="empty">選擇項目後在此編輯。</p></div><p id="admin-feedback" role="status"></p></section></div>';
 $('#agent-directory').hidden=!can('agent:manage');$('#user-directory').hidden=!can('people:manage');
 for(const kind of ['users','agents']){
  const form=$(kind==='users'?'#user-search':'#agent-search');
  for(const [key,value] of Object.entries(adminState[kind]))if(form.elements[key])form.elements[key].value=value;
  form.onsubmit=e=>{e.preventDefault();adminState[kind]={...adminState[kind],...formData(form),page:1};guard(()=>refreshDirectory(kind));};
 }
 bind('#adduser',()=>openEditor('user'));bind('#addagent',()=>openEditor('agent'));
 await Promise.all([can('agent:manage')?refreshDirectory('agents'):null,can('people:manage')?refreshDirectory('users'):null]);
}
function openEditor(kind,value){kind==='user'?userForm(value):agentForm(value);const editor=$('.admin-editor');if(innerWidth<1000)editor.scrollIntoView({block:'start'});editor.querySelector('input')?.focus();}
function directoryPager(target,data,callback){target.replaceChildren();const label=document.createElement('span');label.textContent=`共 ${data.total} 筆 · 第 ${data.page}／${data.pages} 頁`;target.append(label);for(const [text,page,disabled]of [['上一頁',data.page-1,data.page<=1],['下一頁',data.page+1,data.page>=data.pages]]){const b=document.createElement('button');b.type='button';b.textContent=text;b.disabled=disabled;b.onclick=()=>guard(()=>callback(page));target.append(b);}}
async function refreshDirectory(kind){
 if(!can(kind==='users'?'people:manage':'agent:manage'))return;
 const epoch=++adminSerial[kind],container=$(kind==='users'?'#users':'#agents');if(!container)return;container.setAttribute('aria-busy','true');
 try{
  const data=await api(directoryURL(kind,adminState[kind],kind==='users'?12:6));if(epoch!==adminSerial[kind]||!container.isConnected)return;
  adminState[kind].page=data.page;if(kind==='users')users=data.items;else agents=data.items;
  const fragment=document.createDocumentFragment();
  for(const value of data.items){
   const row=document.createElement('article');row.className=kind==='users'?'staff-row':'agent-tile';row.dataset.id=value.id;
   if(kind==='agents'){
    row.innerHTML=`${companionPortrait(value)}<div><strong>${E(value.name)}</strong><p class="muted">${E(roles[value.profile]||value.profile)} · Prompt ${E(value.prompt_version)}</p></div><button type="button">編輯助理</button>`;
    row.querySelector('button').onclick=()=>openEditor('agent',value);
   }else{
    const a=value.assigned_agent;row.innerHTML=`<div class="staff-identity"><strong>${E(value.name)}</strong><span class="staff-login">${E(value.login)}</span><span>${E(value.department)} · ${E(value.title)}</span><span class="muted">${E(roles[value.role]||value.role)} · ${value.active?'啟用':'停用'}</span></div><div class="assigned-preview">${a?companionPortrait(a):''}<span>${E(a?.name||'尚未派任')}</span></div><div class="staff-actions"><button type="button" data-edit>編輯人員</button>${can('agent:manage')?'<button type="button" data-assign>變更派任</button>':''}</div>`;
    row.querySelector('[data-edit]').onclick=()=>openEditor('user',value);if(can('agent:manage'))row.querySelector('[data-assign]').onclick=()=>guard(()=>assignmentDialog(value));
   }
   fragment.append(row);
  }
  if(!data.items.length){const p=document.createElement('p');p.className='empty';p.textContent='沒有符合條件的項目，請調整搜尋或篩選。';fragment.append(p);}
  container.replaceChildren(fragment);
  if(kind==='users'){
   const select=$('#user-search [name=department]'),current=adminState.users.department;select.innerHTML='<option value="">全部部門</option>'+data.departments.map(d=>`<option value="${E(d)}">${E(d)}</option>`).join('');select.value=current;
  }
  directoryPager($(kind==='users'?'#user-pages':'#agent-pages'),data,async page=>{adminState[kind].page=page;await refreshDirectory(kind);});
 }finally{if(container.isConnected&&epoch===adminSerial[kind])container.setAttribute('aria-busy','false');}
}
async function adminAfterSave(kind,saved){
 if(view!=='people')return;
 const feedback=$('#admin-feedback');feedback.textContent='已保存，目錄已更新。';
 if(kind==='users'){await refreshDirectory('users');const record=users.find(x=>x.id===saved.id);userForm(record||saved);}
 else{await Promise.all([refreshDirectory('agents'),can('people:manage')?refreshDirectory('users'):null]);agentForm(saved);}
 // Only assigned-agent presentation is refreshed. The existing work area remains.
 const identity=await api('/api/me');if(identity.user.id!==me.user.id){login(statusCache,'登入身分已變更，請重新登入。');return;}me.agent=identity.agent;me.companionPreference=await api('/api/uiux/companion-preference');companionRender();
}
async function assignmentDialog(user){
 $('#assignment-dialog')?.remove();const epoch=authEpoch,dialog=document.createElement('dialog');dialog.id='assignment-dialog';dialog.setAttribute('aria-label','選擇派任助理');dialog.innerHTML=`<h2>派任助理給 ${E(user.name)}</h2><p class="muted">${E(user.login)} · ${E(user.department)}</p><form class="directory-filters"><label>搜尋助理<input name="q" maxlength="100"></label><button type="submit">搜尋</button></form><div class="assignment-grid"></div><div class="directory-pages"></div><p class="assignment-feedback" role="status"></p><div class="form-actions"><button type="button" class="primary" data-confirm disabled>確認派任</button><button type="button" data-cancel>取消</button></div>`;document.body.append(dialog);dialog.showModal();dialog.querySelector('[data-cancel]').focus();
 let chosen=null,serial=0,busy=false;const state={q:'',page:1};
 dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
 async function load(){chosen=null;dialog.querySelector('[data-confirm]').disabled=true;const ticket=++serial,data=await api(directoryURL('agents',state,6));if(epoch!==authEpoch||!dialog.isConnected||ticket!==serial)return;const grid=dialog.querySelector('.assignment-grid');grid.innerHTML=data.items.map(a=>`<button type="button" class="assignment-choice ${chosen?.id===a.id?'selected':''}" data-agent="${E(a.id)}" aria-pressed="${chosen?.id===a.id}">${companionPortrait(a)}<strong>${E(a.name)}</strong><span>${E(roles[a.profile]||a.profile)}</span></button>`).join('')||'<p class="empty">沒有符合條件的助理。</p>';for(const button of grid.querySelectorAll('button'))button.onclick=()=>{chosen=data.items.find(a=>a.id===button.dataset.agent);for(const b of grid.querySelectorAll('button')){b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));}dialog.querySelector('[data-confirm]').disabled=false;};directoryPager(dialog.querySelector('.directory-pages'),data,async page=>{state.page=page;await load();});}
 dialog.querySelector('form').onsubmit=e=>{e.preventDefault();state.q=dialog.querySelector('[name=q]').value;state.page=1;guard(load);};
 dialog.querySelector('[data-confirm]').onclick=()=>guard(async()=>{if(!chosen||busy)return;busy=true;for(const b of dialog.querySelectorAll('button'))b.disabled=true;try{await api('/api/users/'+user.id+'/agent-assignment','PUT',{agent_id:chosen.id});dialog.close();notice('派任已更新；受影響員工需重新登入。');await refreshDirectory('users');}catch(error){if(dialog.isConnected)dialog.querySelector('.assignment-feedback').textContent=error.message;throw error;}finally{busy=false;if(dialog.isConnected)for(const b of dialog.querySelectorAll('button'))b.disabled=false;}});
 await load();
}
