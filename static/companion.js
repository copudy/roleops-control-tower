'use strict';
// Batch items 1-3: isolated art, click-only menu, personal nickname.
const COMPANION_SKINS=[['sprout','芽芽'],['owl','小梟'],['cloud','雲寶'],['fox','狐狐'],['star','星仔'],['turtle','龜龜']];
let companionMode='mentor',companionOwner=null,companionState={},companionDrag=null,companionCleanup=()=>{};
function companionSkin(agent){return COMPANION_SKINS.some(([id])=>id===agent?.companion_skin)?agent.companion_skin:'sprout';}
function companionName(){return me?.companionPreference?.display_name||me?.agent?.name||'我的助理';}
function companionPortrait(agent){return `<span class="companion-sprite companion-portrait" data-skin="${COMPANION_SKINS.findIndex(([id])=>id===companionSkin(agent))}" aria-hidden="true"></span>`;}
function companionClear(){companionCleanup();companionCleanup=()=>{};$('#floating-companion')?.remove();$('#companion-menu')?.remove();companionOwner=null;companionState={};companionDrag=null;companionMode='mentor';}
function companionSetMode(mode){companionMode=mode==='dispatcher'?'dispatcher':'mentor';const box=$('#floating-companion'),menu=$('#companion-menu'),label=companionMode==='mentor'?'Mentor · 工作指引':'Dispatcher · 申請派任';if(box){box.dataset.mode=companionMode;box.querySelector('.companion-handle').title=companionName()+' · '+label+'（點一下開啟選單，拖曳可移位）';}if(menu)menu.querySelector('.companion-role').textContent=label;}
function companionSave(){if(!companionOwner)return;try{localStorage.setItem('roleops-companion-v1:'+companionOwner,JSON.stringify(companionState));}catch{}}
function companionPlace(box,x,y){
 const maxX=Math.max(8,innerWidth-box.offsetWidth-8),maxY=Math.max(8,innerHeight-box.offsetHeight-8);
 x=Math.max(8,Math.min(maxX,Number.isFinite(x)?x:maxX));y=Math.max(8,Math.min(maxY,Number.isFinite(y)?y:maxY));
 box.style.left=x+'px';box.style.top=y+'px';companionState.x=x/maxX;companionState.y=y/maxY;
}
function companionRestore(box){companionPlace(box,(Number.isFinite(companionState.x)?companionState.x:1)*Math.max(8,innerWidth-box.offsetWidth-8),(Number.isFinite(companionState.y)?companionState.y:0.55)*Math.max(8,innerHeight-box.offsetHeight-8));}
function companionAvoidAction(box){
 if(companionDrag||box.dataset.collapsed==='true')return;
 const action=document.querySelector('#question button[type=submit]');if(!action)return;
 const a=action.getBoundingClientRect(),b=box.getBoundingClientRect();
 if(a.top<innerHeight&&a.bottom>0&&b.left<a.right&&b.right>a.left&&b.top<a.bottom&&b.bottom>a.top){companionPlace(box,b.left,Math.max(8,a.top-b.height-16));}
}
function companionRender(){
 if(!me?.agent){companionClear();return;}
 const user=me.user.id,agent=me.agent,index=COMPANION_SKINS.findIndex(([id])=>id===companionSkin(agent));
 let box=$('#floating-companion');
 if(companionOwner!==user){const mode=companionMode;companionClear();companionMode=mode;companionOwner=user;try{const saved=JSON.parse(localStorage.getItem('roleops-companion-v1:'+user)||'{}');if(saved&&typeof saved==='object')companionState=saved;}catch{companionState={};}}
 if(box?.isConnected){box.querySelector('.companion-sprite').dataset.skin=index;const menu=$('#companion-menu');menu.querySelector('.companion-name').textContent=companionName();companionSetMode(companionMode);companionAvoidAction(box);return;}
 box=document.createElement('aside');box.id='floating-companion';box.className='floating-companion';box.setAttribute('aria-label','浮動助理');box.dataset.quiet=String(companionState.quiet===true);
 box.innerHTML='<button type="button" class="companion-handle" aria-label="開啟助理選單；可拖曳或用方向鍵移動" aria-controls="companion-menu" aria-expanded="false"><span class="companion-sprite" aria-hidden="true"></span></button><button type="button" class="companion-reopen">顯示助理</button><span class="sr-only" role="status" data-feedback></span>';
 const menu=document.createElement('section');menu.id='companion-menu';menu.className='companion-menu';menu.hidden=true;menu.setAttribute('role','dialog');menu.setAttribute('aria-label','助理操作選單');menu.tabIndex=-1;
 menu.innerHTML='<div class="companion-menu-heading"><div><strong class="companion-name"></strong><span class="companion-role"></span></div><button type="button" data-action="close" aria-label="關閉助理選單">×</button></div><div class="companion-menu-actions"><button type="button" data-action="chat">開始對話</button><button type="button" data-action="rename">修改我的助理暱稱</button><button type="button" data-action="reset">重設位置</button><button type="button" data-action="hide">收起助理</button></div><form class="companion-rename" hidden><label for="companion-nickname">我的助理暱稱</label><input id="companion-nickname" name="nickname" maxlength="30" autocomplete="off"><p class="muted">只更改你的顯示名稱。留白可恢復管理員設定的名稱。</p><div class="form-actions"><button type="submit" class="primary">保存暱稱</button><button type="button" data-action="cancel-rename">取消</button></div><p class="companion-name-feedback" role="status"></p></form><label class="companion-quiet"><input type="checkbox" data-action="quiet">減少動畫</label><p class="companion-menu-help">拖曳角色移位；聚焦角色後用方向鍵移動，Shift 加快。</p>';
 document.body.append(box,menu);box.querySelector('.companion-sprite').dataset.skin=index;menu.querySelector('.companion-name').textContent=companionName();companionSetMode(companionMode);
 const handle=box.querySelector('.companion-handle'),feedback=box.querySelector('[data-feedback]'),rename=menu.querySelector('.companion-rename');let suppressClick=false;
 box.dataset.collapsed=String(companionState.hidden===true);menu.querySelector('[data-action="quiet"]').checked=companionState.quiet===true;companionRestore(box);companionAvoidAction(box);
 function placeMenu(){const r=box.getBoundingClientRect(),w=menu.offsetWidth,h=menu.offsetHeight;menu.style.left=Math.max(8,Math.min(innerWidth-w-8,r.right-w))+'px';const above=r.top-h-8;menu.style.top=Math.max(8,Math.min(innerHeight-h-8,above>=8?above:r.bottom+8))+'px';}
 function closeMenu(focus=false){menu.hidden=true;handle.setAttribute('aria-expanded','false');if(focus)handle.focus();}
 function openMenu(){menu.hidden=false;rename.hidden=true;handle.setAttribute('aria-expanded','true');placeMenu();menu.querySelector('[data-action="chat"]').focus();}
 handle.onpointerdown=e=>{if(e.button!==0||!e.isPrimary)return;const r=box.getBoundingClientRect();companionDrag={id:e.pointerId,x:e.clientX,y:e.clientY,left:r.left,top:r.top,moved:false};suppressClick=false;handle.setPointerCapture(e.pointerId);};
 handle.onpointermove=e=>{const d=companionDrag;if(!d||d.id!==e.pointerId)return;const dx=e.clientX-d.x,dy=e.clientY-d.y;if(Math.hypot(dx,dy)>5){d.moved=true;box.classList.add('dragging');closeMenu();}if(d.moved)companionPlace(box,d.left+dx,d.top+dy);};
 const finish=e=>{if(!companionDrag||e.pointerId!==companionDrag.id)return;const moved=companionDrag.moved;suppressClick=moved;companionDrag=null;box.classList.remove('dragging');if(moved){companionSave();feedback.textContent='助理位置已保存。';}};
 handle.onpointerup=finish;handle.onpointercancel=finish;handle.onlostpointercapture=finish;
 handle.onclick=()=>{if(suppressClick){suppressClick=false;return;}menu.hidden?openMenu():closeMenu();};
 handle.onkeydown=e=>{if(e.key==='Escape'){closeMenu();return;}if(e.key==='Enter'||e.key===' ')suppressClick=false;const delta={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];if(!delta)return;e.preventDefault();closeMenu();const r=box.getBoundingClientRect(),step=e.shiftKey?30:10;companionPlace(box,r.left+delta[0]*step,r.top+delta[1]*step);companionSave();feedback.textContent='助理位置已保存。';};
 menu.querySelector('[data-action="close"]').onclick=()=>closeMenu(true);
 menu.querySelector('[data-action="chat"]').onclick=()=>guard(async()=>{closeMenu();await navigateTo('chat');$('#question textarea')?.focus();});
 menu.querySelector('[data-action="rename"]').onclick=()=>{rename.hidden=false;rename.elements.nickname.value=me.companionPreference?.nickname||'';menu.querySelector('.companion-name-feedback').textContent='';placeMenu();rename.elements.nickname.focus();};
 menu.querySelector('[data-action="cancel-rename"]').onclick=()=>{rename.hidden=true;placeMenu();menu.querySelector('[data-action="rename"]').focus();};
 rename.onsubmit=e=>{e.preventDefault();guard(async()=>{const button=rename.querySelector('button[type=submit]'),epoch=authEpoch;button.disabled=true;try{const pref=await api('/api/uiux/companion-preference','PATCH',{agent_id:me.agent.id,nickname:rename.elements.nickname.value});if(epoch!==authEpoch)return;me.companionPreference=pref;menu.querySelector('.companion-name').textContent=companionName();companionSetMode(companionMode);const heading=$('.chat-main .assistant-heading');if(heading)heading.innerHTML=companionPortrait(me.agent)+E(companionName());menu.querySelector('.companion-name-feedback').textContent='你的助理暱稱已保存。';}catch(error){if(rename.isConnected)menu.querySelector('.companion-name-feedback').textContent=error.message;throw error;}finally{if(button.isConnected)button.disabled=false;}});};
 menu.querySelector('[data-action="hide"]').onclick=()=>{closeMenu();companionState.hidden=true;box.dataset.collapsed='true';companionRestore(box);companionSave();box.querySelector('.companion-reopen').focus();};
 box.querySelector('.companion-reopen').onclick=()=>{companionState.hidden=false;box.dataset.collapsed='false';companionRestore(box);companionSave();handle.focus();};
 menu.querySelector('[data-action="quiet"]').onchange=e=>{companionState.quiet=e.target.checked;box.dataset.quiet=String(e.target.checked);companionSave();};
 menu.querySelector('[data-action="reset"]').onclick=()=>{closeMenu();companionState.x=1;companionState.y=1;companionRestore(box);companionSave();feedback.textContent='助理已回到右下角。';handle.focus();};
 const resize=()=>{companionRestore(box);companionAvoidAction(box);if(!menu.hidden)placeMenu();},outside=e=>{if(!menu.hidden&&!menu.contains(e.target)&&!box.contains(e.target))closeMenu();},escape=e=>{if(e.key==='Escape'&&!menu.hidden){e.preventDefault();closeMenu(true);}};
 const avoid=()=>{companionAvoidAction(box);};window.addEventListener('scroll',avoid,true);window.addEventListener('resize',resize);document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);const observer=new ResizeObserver(()=>{if(!menu.hidden)placeMenu();});observer.observe(menu);
 companionCleanup=()=>{window.removeEventListener('scroll',avoid,true);window.removeEventListener('resize',resize);document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);observer.disconnect();};
}
