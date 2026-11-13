'use strict';
let chatSerial=0,messageSerial=0,chatSending=false,chatDrafts=new Map(),chatActivity=new Map();
function clearInteractionState(){if(me?.user?.id)try{sessionStorage.removeItem('roleops-conversation:'+me.user.id);}catch{}++chatSerial;++messageSerial;chatSending=false;chatDrafts.clear();chatActivity.clear();adminState={users:{q:'',department:'',active:'all',sort:'login',page:1},agents:{q:'',page:1}};++adminSerial.users;++adminSerial.agents;$('#assignment-dialog')?.remove();}
function addPasswordToggles(scope){for(const input of scope.querySelectorAll('input[type=password]')){if(input.parentElement.classList.contains('password-control'))continue;const wrap=document.createElement('div');wrap.className='password-control';input.before(wrap);wrap.append(input);const button=document.createElement('button');button.type='button';button.className='password-toggle';button.textContent='顯示密碼';button.setAttribute('aria-label','顯示密碼');button.setAttribute('aria-pressed','false');button.onclick=()=>{const show=input.type==='password';input.type=show?'text':'password';button.textContent=show?'隱藏密碼':'顯示密碼';button.setAttribute('aria-label',button.textContent);button.setAttribute('aria-pressed',String(show));};wrap.append(button);}}
function activityKey(item){return item.conversation_status?.status_id||item.trace_id||item.id;}
function localActivity(cid,item){const items=chatActivity.get(cid)||[];const key=activityKey(item);chatActivity.set(cid,[...items.filter(x=>activityKey(x)!==key),item]);}
function statusHTML(message){
 const state=message.conversation_status;
 if(!state)return '';
 const saved=state.message_saved===true&&state.state==='saved',fallback=saved&&state.answer_origin==='verified_source_fallback';
 const text=saved?'已保存'+(fallback?' · 依核對文件整理':''):state.text||'處理狀態待確認。';
 return `<div class="chat-status ${saved?'saved':E(state.state)}" role="status"><span>${E(text)}</span>${!saved?`<small>${state.status_saved?'系統狀態已保存；本次回答未完成。':state.state==='waiting'?'問題等待處理中。':'請保留原問題。'}${state.trace_id?' 追蹤碼：'+E(state.trace_id):''}</small>`:''}</div>`;
}
function drawMessages(area,cid,messages,readonly,same){
 if(!readonly)companionSetMode(messages.filter(m=>m.kind!=='system_status').at(-1)?.proposal?'dispatcher':'mentor');
 const atBottom=area.scrollHeight-area.scrollTop-area.clientHeight<64,oldTop=area.scrollTop,existing=new Map([...area.querySelectorAll(':scope > [data-message-id]')].map(n=>[n.dataset.messageId,n]));
 area.querySelector(':scope > .empty')?.remove();const jobs=[];let cursor=area.firstElementChild;
 for(const message of messages){
  const system=message.kind==='system_status',id=(system?'status:':'')+message.id,signature=JSON.stringify(message);let row=existing.get(id);existing.delete(id);
  if(!row||row._signature!==signature){
   const old=row;row=document.createElement('article');row.className='message-row'+(system?' system-activity':'');row.dataset.messageId=id;row._signature=signature;
   row.innerHTML=`${message.question?`<div class="bubble human">${E(message.question)}</div>`:''}${system?statusHTML(message):`${message.answer?.trim()?`<div class="bubble assistant">${E(message.answer)}<div class="cite">${E((message.sources||[]).map(s=>s.document_id+' · '+s.policy_version).join(' ｜ '))}</div>${statusHTML(message)}</div>`:''}`}`;
   if(!readonly&&!system)extensionSources(row,cid,message);
   if(old){if(old===cursor)cursor=old.nextElementSibling;old.replaceWith(row);}else area.insertBefore(row,cursor);
   if(!readonly&&!system&&message.proposal){const card=document.createElement('section');card.className='card proposal-card';card.dataset.message=message.id;row.append(card);jobs.push(()=>proposalCard(card,cid,message));}
   if(!readonly&&system&&!['waiting','hidden'].includes(message.conversation_status?.state)){
    const button=document.createElement('button');button.type='button';button.className='status-action';button.textContent=message.conversation_status?.state==='unknown'?'查回原對話':'編輯原問題';
    button.onclick=()=>guard(async()=>{
     if(selected!==cid||view!=='chat')return;
     if(message.conversation_status?.state==='unknown'){
      button.disabled=true;try{await renderMessages(cid,false);}finally{if(button.isConnected)button.disabled=false;}return;
     }
     const input=$('#question textarea');input.value=message.question||'';chatDrafts.set(cid,input.value);input.focus();
    });row.append(button);
   }
  }
  if(row!==cursor&&row.nextElementSibling!==cursor)area.insertBefore(row,cursor);
  cursor=row.nextElementSibling;
 }
 for(const row of existing.values())row.remove();
 if(!messages.length)area.innerHTML='<p class="empty">從一個工作問題開始。</p>';
 if(!same||atBottom)area.scrollTop=area.scrollHeight;else area.scrollTop=oldTop;
 return {jobs,atBottom,oldTop};
}
function mergeActivity(cid,messages,activity){
 const seen=new Set(),all=[...messages,...activity];
 for(const item of all)seen.add(activityKey(item));
 for(const item of chatActivity.get(cid)||[])if(!seen.has(activityKey(item)))all.push(item);
 return all.sort((a,b)=>(a.at||0)-(b.at||0)||(a.sequence&&b.sequence?a.sequence-b.sequence:0)||String(a.id).localeCompare(String(b.id)));
}
function showLocalActivity(cid){const area=$('#messages');if(area?.dataset.conversation===cid&&selected===cid&&view==='chat'){const result=drawMessages(area,cid,mergeActivity(cid,area._messages||[],area._activity||[]),false,true);Promise.all(result.jobs.map(job=>job())).catch(error=>{if(!(error instanceof StaleResponse)&&area.isConnected)notice(error.message);});}}
async function renderMessages(cid,readonly){
 const area=$('#messages');if(!area)return;const ticket=++messageSerial,epoch=authEpoch,same=area.dataset.conversation===cid;
 if(!same){area.replaceChildren();area.dataset.conversation=cid;area._messages=[];area._activity=[];}
 area.setAttribute('aria-busy','true');
 try{
  const suffix=readonly?'?management=true':'',base='/api/conversations/'+encodeURIComponent(cid);
  const [messages,activity]=await Promise.all([api(base+'/messages'+suffix),api(base+'/activity'+suffix)]);
  if(ticket!==messageSerial||epoch!==authEpoch||!area.isConnected||area.dataset.conversation!==cid||(!readonly&&selected!==cid))return;
  // Server reads replace prior content and masking, including system activities.
  const authoritative=[...messages,...activity],keys=new Set(authoritative.map(activityKey));
  chatActivity.set(cid,(chatActivity.get(cid)||[]).filter(x=>!x.conversation_status?.status_saved&&!keys.has(activityKey(x))));
  // A matching saved question resolves a network result without issuing another write.
  chatActivity.set(cid,(chatActivity.get(cid)||[]).filter(x=>!(x.conversation_status?.state==='unknown'&&messages.some(m=>m.question===x.question&&!x.baseIds?.includes(m.id)))));
  area._messages=messages;area._activity=activity;
  const result=drawMessages(area,cid,mergeActivity(cid,messages,activity),readonly,same);
  await Promise.all(result.jobs.map(job=>job()));
  if(area.isConnected&&ticket===messageSerial){if(readonly&&!same)area.scrollTop=0;else if(!same||result.atBottom)area.scrollTop=area.scrollHeight;else area.scrollTop=result.oldTop;}
 }catch(error){if(epoch===authEpoch&&ticket===messageSerial&&[401,403,404].includes(error.status)){area.replaceChildren();area._messages=[];area._activity=[];chatActivity.delete(cid);}throw error;}
 finally{if(area.isConnected&&ticket===messageSerial)area.setAttribute('aria-busy','false');}
}
async function chat(){
 const epoch=authEpoch,ticket=++chatSerial,list=await api('/api/conversations');if(epoch!==authEpoch||ticket!==chatSerial||view!=='chat')return;
 if(!selected)try{selected=sessionStorage.getItem('roleops-conversation:'+me.user.id);}catch{}
 if(!list.some(c=>c.id===selected))selected=list[0]?.id||null;try{if(selected)sessionStorage.setItem('roleops-conversation:'+me.user.id,selected);}catch{}const current=list.find(c=>c.id===selected),content=$('#content');
 if(!$('#chat-workspace')){
  content.innerHTML='<div id="chat-workspace" class="chat-grid"><aside class="conversation-sidebar"><div class="conversation-heading"><h3>我的對話</h3><span id="conversation-count"></span></div><button id="newchat" class="new-conversation" type="button">＋ 新對話</button><label class="sr-only" for="conversation-search">搜尋對話</label><input id="conversation-search" type="search" placeholder="搜尋對話標題"><div id="conversation-list" role="list"></div><p class="muted">從列表移除後，公司依政策保存對話；正式刪除由經授權管理員執行。</p></aside><section class="card chat-main"><div class="tools"><h2 class="assistant-heading"></h2><span class="badge" id="chat-role"></span></div><p class="muted">以公司有效文件提供指引；資料與工具依員工及助理共同授權。</p><div class="tools conversation-toolbar"><strong id="conversation-title"></strong><button id="rename" type="button">重新命名</button><button id="hide" type="button">從列表移除</button></div><div id="messages" class="messages" aria-label="對話內容"></div><p id="chat-progress" role="status"></p><form id="question" class="message-input"><textarea name="question" maxlength="2000" placeholder="描述工作問題，或詢問交接事項…" required></textarea><button class="primary" type="submit">傳送</button></form></section></div>';
  $('#conversation-search').oninput=()=>filterConversations();
  $('#question textarea').oninput=e=>chatDrafts.set(selected||'',e.target.value);
  $('#question').onsubmit=submitQuestion;
 }
 $('#chat-workspace')._list=list;$('#conversation-count').textContent=String(list.length);$('.chat-main .assistant-heading').innerHTML=(me.agent?companionPortrait(me.agent):'')+E(me.agent?companionName():'尚未派任助理');$('#chat-role').textContent=roles[me.user.role]||me.user.role;
 $('#conversation-title').textContent=current?.title||'建立對話開始';$('#rename').hidden=!current;$('#hide').hidden=!current;
 filterConversations();$('#question textarea').value=chatDrafts.get(selected||'')||'';$('#question textarea').readOnly=chatSending;$('#question button').disabled=chatSending||!me.agent;
 bind('#newchat',async()=>{if(chatSending)return;const result=await api('/api/conversations','POST',{title:'新對話'});selected=result.id;await chat();$('#question textarea')?.focus();});
 bind('#rename',async()=>{const cid=selected,title=prompt('對話名稱（最多 100 字）',current.title);if(title){await api('/api/conversations/'+cid,'PATCH',{action:'rename',title});await chat();}});
 bind('#hide',async()=>{if(confirm('從自己的列表移除？公司仍保存此對話。')){const cid=selected;await api('/api/conversations/'+cid,'PATCH',{action:'hide'});chatDrafts.delete(cid);selected=null;await chat();}});
 if(current)await renderMessages(current.id,false);else{$('#messages').replaceChildren();$('#messages').dataset.conversation='';}
}
function filterConversations(){
 const list=$('#chat-workspace')?._list||[],search=$('#conversation-search').value.trim().toLocaleLowerCase(),area=$('#conversation-list');area.replaceChildren();
 for(const item of list.filter(c=>c.title.toLocaleLowerCase().includes(search))){const button=document.createElement('button');button.type='button';button.className='conversation-item';button.textContent=item.title;if(item.id===selected){button.classList.add('selected');button.setAttribute('aria-current','true');}button.onclick=()=>guard(async()=>{if(selected===item.id)return;selected=item.id;await chat();});area.append(button);}
 if(!area.children.length)area.innerHTML='<p class="empty">沒有符合的對話。</p>';
}
async function submitQuestion(event){
 event.preventDefault();if(chatSending||!me?.agent)return;const form=event.target,input=form.elements.question,question=input.value;if(!question.trim())return;
 const epoch=authEpoch;let cid=selected,token='local:'+crypto.randomUUID(),at=Date.now()/1000;
 chatSending=true;input.readOnly=true;form.querySelector('button').disabled=true;
 const waiting={id:token,trace_id:token,question,at,baseIds:($('#messages')?._messages||[]).map(x=>x.id),kind:'system_status',conversation_status:{state:'waiting',text:'正在處理這則問題…'}};
 try{
  if(!cid){cid=(await api('/api/conversations','POST',{title:question.slice(0,35)},{ignoreRender:true})).id;if(epoch!==authEpoch)return;selected=cid;localActivity(cid,waiting);await chat();}
  else{localActivity(cid,waiting);showLocalActivity(cid);}
  chatDrafts.set(cid,question);
  const result=await api('/api/conversations/'+cid+'/messages','POST',{question},{ignoreRender:true});
  if(epoch!==authEpoch)return;
  if(result.rejected){localActivity(cid,{...waiting,conversation_status:result.conversation_status||{state:'blocked',text:result.answer||'請修改原問題後再送出。'}});showLocalActivity(cid);return;}
  if(!result.id||result.conversation_id!==cid)throw new ApiError('這次回覆的保存結果待確認。');
  chatActivity.set(cid,(chatActivity.get(cid)||[]).filter(x=>x.id!==token));chatDrafts.delete(cid);
  if(selected!==cid||view!=='chat')return;
  input.value='';const area=$('#messages');area._messages=[...(area._messages||[]).filter(x=>x.id!==result.id),{question,at,...result}];showLocalActivity(cid);
  try{await chat();}catch(error){if(error instanceof StaleResponse)throw error;/* Already received a saved response; keep it visible until next read. */}
 }catch(error){
  if(error instanceof StaleResponse||epoch!==authEpoch)return;
  if(cid){
   const state=error.payload?.conversation_status||{state:'unknown',text:'連線中斷或等待逾時，這次回覆結果待確認。請先查回原對話，保留問題，稍後再決定是否重試。',retry:'lookup_original'};
   chatActivity.set(cid,(chatActivity.get(cid)||[]).filter(x=>x.id!==token));
   localActivity(cid,{...waiting,id:state.status_id||token,trace_id:state.trace_id||token,conversation_status:state});showLocalActivity(cid);
   if(state.state==='unknown'&&selected===cid&&view==='chat')try{await renderMessages(cid,false);}catch{/* User keeps the original question and explicit lookup action. */}
  }else if($('#chat-progress'))$('#chat-progress').textContent='尚未取得對話建立結果。請重新載入列表確認，再選擇對話。';
 }finally{if(epoch===authEpoch){chatSending=false;const f=$('#question');if(f){f.elements.question.readOnly=false;f.querySelector('button').disabled=!me?.agent;}}}
}
