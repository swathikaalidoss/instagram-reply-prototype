const $ = s => document.querySelector(s);
let loaded = false, lastActivity = '';
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Local-App':'1'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function node(tag, text, cls) {const n = document.createElement(tag); if(text !== undefined) n.textContent=text; if(cls)n.className=cls;return n;}
function addRule(rule={keywords:'',reply:''}) {
  const box=node('div',undefined,'rule');
  for(const [key,title] of [['keywords','Keywords, separated by commas'],['reply','Private DM message']]) {
    const label=node('label',title), field=node(key==='reply'?'textarea':'input');
    field.dataset.field=key; field.value=rule[key];field.required=true;field.maxLength=1000;if(key==='reply')field.rows=2;label.append(field);box.append(label);
  }
  const remove=node('button','Remove rule','remove');remove.type='button';remove.onclick=()=>box.remove();box.append(remove);$('#rules').append(box);
}
function render(data){
  $('#mode').textContent=data.mode==='demo'?'DEMO MODE':'LIVE MODE';
  $('#notice').textContent=data.mode==='demo'?'Demo mode — replies are simulated. No Instagram account is connected.':'Live mode — verified Instagram comments trigger real DMs and public replies. Simulator comments remain simulated.';
  $('#count').textContent=data.comments.length;
  $('#replied').textContent=data.comments.filter(c=>['simulated','sent'].includes(c.status)).length;
  $('#automation').textContent=data.settings.enabled?'On':'Paused';
  if(!loaded){$('#enabled').checked=data.settings.enabled;$('#fallback').value=data.settings.fallback;$('#public-reply').value=data.settings.public_reply;data.settings.rules.forEach(addRule);loaded=true;}
  const current=JSON.stringify(data.comments);if(current===lastActivity)return;lastActivity=current;
  $('#activity').replaceChildren();
  if(!data.comments.length)$('#activity').append(node('div','Your first conversation starts here. Try a sample comment above.','empty'));
  for(const c of data.comments){
    const card=node('article',undefined,'comment'),head=node('div',undefined,'comment-head');
    head.append(node('strong','@'+c.username),node('span',c.status,'status '+c.status));
    card.append(head,node('p',c.text));
    if(c.public_reply){card.append(node('div','PRIVATE DM · '+c.dm_status+'\n'+c.reply,'reply'),node('div','PUBLIC COMMENT · '+c.public_status+'\n'+c.public_reply,'reply public-reply'));}
    else{card.append(node('div','Previous comment reply\n'+c.reply,'reply'));}
    card.append(node('span',c.detail || (c.status==='skipped'?'Automation was paused.':'DM and public reply queued.'),'detail'));$('#activity').append(card);
  }
}
async function refresh(){try{render(await api('/api/state'));}catch(e){$('#notice').textContent='Could not reach the local server. '+e.message;}}
$('#add').onclick=()=>addRule();
document.querySelectorAll('[data-example]').forEach(b=>b.onclick=()=>{$('#comment').value=b.dataset.example;});
$('#settings').onsubmit=async e=>{
  e.preventDefault(); const button=e.submitter;button.disabled=true;
  try{const rules=[...document.querySelectorAll('.rule')].map(box=>({keywords:box.querySelector('[data-field=keywords]').value,reply:box.querySelector('[data-field=reply]').value}));await api('/api/settings',{enabled:$('#enabled').checked,fallback:$('#fallback').value,public_reply:$('#public-reply').value,rules});$('#saved').textContent='Saved';setTimeout(()=>$('#saved').textContent='',2500);await refresh();}catch(error){$('#saved').textContent=error.message;}finally{button.disabled=false;}
};
$('#simulate').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{await api('/api/simulate',{username:$('#username').value,text:$('#comment').value});$('#comment').value='';await refresh();}catch(error){$('#notice').textContent=error.message;}finally{button.disabled=false;}};
refresh();setInterval(refresh,2000);
