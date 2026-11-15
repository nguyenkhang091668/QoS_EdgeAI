'use strict';
const $=id=>document.getElementById(id);
const write=(id,value)=>{$(id).textContent=value;};
const age=ms=>ms==null?'—':ms<1000?`${Math.round(ms)} ms`:`${(ms/1000).toFixed(1)} giây`;
const rate=value=>Number.isFinite(value)?value.toLocaleString('vi-VN',{maximumFractionDigits:2}):'—';
function badge(id,text,tone='neutral'){$(id).className=`badge ${tone}`;$(id).replaceChildren(Object.assign(document.createElement('i'),{}),document.createTextNode(text));}
let lastSignature={},lastSample=null,history=[],videoFresh=false,hasFrame=false;
function event(key,value,text){if(lastSignature[key]===value)return;const previous=lastSignature[key];lastSignature[key]=value;if(previous===undefined&&key!=='connection')return;const list=$('events');const empty=list.querySelector('.empty-events');if(empty)empty.remove();const li=document.createElement('li');const time=document.createElement('time');time.textContent=new Date().toLocaleTimeString('vi-VN',{hour12:false,timeZone:'Asia/Ho_Chi_Minh'});const message=document.createElement('span');message.textContent=text;li.append(time,message);list.prepend(li);while(list.children.length>20)list.lastElementChild.remove();}
const networkNames=['R1','R2','DSW1','DSW2','ASW'];
const networkStates={ONLINE:['Có phản hồi','good'],STREAMING:['Có lưu lượng','good'],
  INTERFACE_DOWN:['Cổng xuống','warn'],OFFLINE:['Không phản hồi','bad'],
  COLLECTOR_ERROR:['Lỗi thu thập','warn'],STALE:['Dữ liệu cũ','warn'],
  WARMING_UP:['Đang lấy mẫu','neutral'],SIMULATED:['Mô phỏng','neutral'],UNKNOWN:['Chưa rõ','neutral']};
function node(tag,text='',className=''){const el=document.createElement(tag);el.textContent=text;el.className=className;return el;}
function stateBadge(status){const [label,tone]=networkStates[status]||networkStates.UNKNOWN;const el=node('span','','badge '+tone);el.append(node('i'),document.createTextNode(label));return el;}
function measuredState(item,fresh,extra,ttl){
  if(!fresh||!item)return 'UNKNOWN';
  if(item.status==='SIMULATED')return 'SIMULATED';
  if(!Number.isFinite(item.age_ms))return item.status==='STALE'?'STALE':'WARMING_UP';
  if(!Number.isFinite(ttl)||item.age_ms+extra>ttl)return 'STALE';
  return networkStates[item.status]?item.status:'UNKNOWN';
}
function renderDevices(pi,fresh,extra){
  const devices=pi?.devices;const cards=[],rows=[];let online=0;
  for(const name of networkNames){
    const device=devices?.[name];const state=measuredState(device,fresh,extra,device?.ttl_ms);
    if(state==='ONLINE')online++;
    const ports=Array.isArray(device?.interfaces)?device.interfaces:[];
    const statuses=ports.map(p=>measuredState(p,fresh,extra,device.ttl_ms));
    const down=statuses.filter(s=>s==='INTERFACE_DOWN').length;
    const good=statuses.filter(s=>['ONLINE','STREAMING'].includes(s)).length;
    const card=node('article','','network-device');
    const heading=node('div','','device-heading');heading.append(node('h3',name),stateBadge(state));
    const role=name.startsWith('DSW')?'Switch phân phối':name==='ASW'?'Switch truy cập':'Router biên';
    const summary=!fresh?'Đợi dữ liệu mới từ Pi':down?`${down} cổng xuống · ${good}/${ports.length} cổng hoạt động`:
      state==='ONLINE'?`${good}/${ports.length} cổng có số liệu`:'Chưa xác nhận được các cổng';
    card.append(heading,node('p',role,'device-role'),node('p',device?.ip||'—','device-ip'),
      node('p',summary,'device-port-summary'+(down?' attention':'')),
      node('small',fresh&&Number.isFinite(device?.age_ms)?`Mẫu gần nhất: ${age(device.age_ms+extra)}`:'Chưa có mẫu mới','device-age'));
    cards.push(card);
    for(let i=0;i<ports.length;i++){
      const port=ports[i],status=statuses[i];const valid=['ONLINE','STREAMING'].includes(status);
      const row=node('tr');const identity=node('td');
      identity.append(node('strong',port.interface.replace('GigabitEthernet','Gi').replace('Port-channel','Po')),
        node('small',port.label||port.source_id));
      const cell=node('td');cell.append(stateBadge(status));
      row.append(node('td',name),identity,cell,
        node('td',valid?rate(port.rx_mbps):'—','port-rate'),
        node('td',valid?rate(port.tx_mbps):'—','port-rate'),
        node('td',fresh&&Number.isFinite(port.age_ms)?age(port.age_ms+extra):'—','port-age'));
      rows.push(row);
    }
    event('device-'+name,state,`${name}: ${networkStates[state][0].toLowerCase()} (SNMP).`);
  }
  $('deviceCards').replaceChildren(...cards);
  if(!rows.length){const row=node('tr');const cell=node('td','Chưa có dữ liệu cổng từ Pi.','ports-empty');cell.colSpan=6;row.append(cell);rows.push(row);}
  $('portRows').replaceChildren(...rows);
  write('portCount',devices?`Chi tiết ${networkNames.reduce((n,name)=>n+(devices[name]?.interfaces?.length||0),0)} cổng được giám sát`:'Chi tiết các cổng được giám sát');
  write('networkSummary',!fresh?'Chờ kết nối Pi':!devices?'Chưa có dữ liệu thiết bị':`${online}/5 thiết bị có phản hồi`);
}
function drawChart(pi,fresh){
  const routers=pi?.qos?.routers||{};
  for(const name of ['R1','R2']){const wan=routers[name]?.wan;write(`${name.toLowerCase()}Wan`,fresh&&wan&&Number.isFinite(wan.mbps)?`${rate(wan.mbps)} Mbps`:'—');}
  if(fresh&&pi.sampled_at_utc!==lastSample){lastSample=pi.sampled_at_utc;history.push({at:Date.now(),R1:routers.R1?.wan?.mbps??null,R2:routers.R2?.wan?.mbps??null});}
  const now=Date.now();history=history.filter(x=>now-x.at<=60000);
  const values=history.flatMap(x=>[x.R1,x.R2]).filter(Number.isFinite);const max=Math.max(1,...values)*1.15;
  $('chartEmpty').hidden=values.length>0;write('axisTop',values.length?`${rate(max)} Mbps`:'Mbps');
  for(const name of ['R1','R2']){let segments=[],segment=[];for(const item of history){if(Number.isFinite(item[name]))segment.push(`${650*(1-(now-item.at)/60000)},${135-115*item[name]/max}`);else{if(segment.length)segments.push(segment);segment=[];}}if(segment.length)segments.push(segment);const path=$(`line${name}`);path.setAttribute('points',segments.length?segments[segments.length-1].join(' '):'');}
}
function render(data){
  const connection=data.connection||{state:'OFFLINE'};const pi=data.pi;const extra=connection.source_age_ms||0;const fresh=connection.state==='CONNECTED'&&!!pi&&extra<=3500;
  badge('connectionBadge',fresh?'Đã kết nối Pi':connection.state==='STALE'?'Trạng thái Pi đã cũ':'Chưa kết nối Pi',fresh?'good':'neutral');
  $('notice').hidden=fresh;
  $('notice').textContent=connection.state==='STALE'?'Pi vẫn trả lời nhưng trạng thái ứng dụng đã hết hạn. AI và chính sách bên dưới cần được xác minh lại.':'Chưa nhận trạng thái mới từ Pi. Khởi chạy ứng dụng giám sát trên Pi, rồi mở kết nối từ Mac.';
  event('connection',connection.state,fresh?'Đã kết nối trạng thái Pi.':connection.state==='STALE'?'Trạng thái Pi đã hết hạn.':(lastSignature.connection===undefined?'Chưa có kết nối trạng thái Pi.':'Mất kết nối trạng thái Pi.'));
  const ai=pi?.ai;const aiAge=ai?.observation_age_ms==null?null:ai.observation_age_ms+extra;
  const aiFresh=fresh&&ai&&ai.available&&!ai.simulated&&typeof ai.person_in_roi==='boolean'&&ai.state!=='UNKNOWN'&&aiAge!=null&&aiAge<=ai.observation_ttl_ms;
  const state=aiFresh?ai.state:'UNKNOWN';
  $('aiMain').className=`ai-main ${state==='NORMAL'?'good':state==='ALERT'?'bad':''}`;
  write('aiState',state==='UNKNOWN'?'CHỜ DỮ LIỆU':state);
  write('aiExplanation',state==='ALERT'?(ai.person_in_roi?'Đã xác nhận người trong ROI':'Đang giữ cảnh báo, chờ phục hồi'):state==='NORMAL'?'Quan sát hợp lệ · trạng thái bình thường':ai?.simulated?'Ứng dụng Pi đang ở chế độ mô phỏng':'Chưa có quan sát AI còn mới');
  write('person',aiFresh?(ai.person_in_roi?'Có':'Không'):'Chưa rõ');write('observationAge',age(aiAge));
  write('frameId',aiFresh&&ai.frame_id!=null?`#${ai.frame_id}`:'—');
  event('ai',state,`Trạng thái AI: ${state==='UNKNOWN'?'chưa có dữ liệu mới':state}.`);
  const qos=pi?.qos;write('profile',qos?.profile||'—');
  const desired=qos?.desired_policy;const requested=desired==='ALERT'?qos.alert_kbps:desired==='NORMAL'?qos.normal_kbps:null;
  write('desired',fresh&&requested!=null?`${desired} · ${rate(requested)} kbps`:'—');
  for(const name of ['R1','R2']){
    const id=name.toLowerCase();const r=qos?.routers?.[name];const verificationAge=r?.verified_age_ms==null?null:r.verified_age_ms+extra;
    const verified=fresh&&r&&['VERIFIED','PENDING'].includes(r.verification)&&verificationAge!=null&&verificationAge<=r.verified_ttl_ms;
    const mismatch=verified&&((r.pending_policy&&r.pending_policy!==r.applied_policy)||r.applied_policy!==desired);
    const status=!fresh?'Chưa kết nối':r?.verification==='SIMULATED'?'Mô phỏng':r?.has_error?'Lỗi xác minh':!verified?'Chưa xác minh':mismatch?'Đang đồng bộ':'Đã xác minh';
    badge(`${id}Badge`,status,verified?(mismatch?'warn':'good'):r?.has_error&&fresh?'bad':'neutral');
    write(`${id}Rate`,verified?rate(r.applied_kbps):'—');write(`${id}Policy`,verified?r.applied_policy:'—');
    write(`${id}Detail`,!fresh?'Đợi kết nối Pi':r?.has_error?'Chưa đọc lại được chính sách':mismatch?`Đang yêu cầu ${desired}`:verified?'Chính sách đã được đọc lại':'Kết quả đọc lại chưa có hoặc đã cũ');
    write(`${id}Age`,verificationAge==null?'—':age(verificationAge));
    event(name,`${status}:${verified?r.applied_policy:'?'}`,`${name}: ${status.toLowerCase()}${verified?` ${r.applied_policy} (${rate(r.applied_kbps)} kbps)`:''}.`);
  }
  const encoder=pi?.camera?.encoder;write('encoder',fresh?(encoder?.status==='RUNNING'?'Đang phát RTP':encoder?.status==='ERROR'?'Có lỗi':encoder?.status==='WAITING'?'Đợi AI / khung hình':encoder?.status||'—'):'—');
  const video=data.video||{};videoFresh=video.state==='LIVE';
  badge('videoBadge',videoFresh?'Đang nhận RTP':video.state==='STALE'?'Khung hình đã cũ':video.error==='UDP_PORT_IN_USE'?'Cổng video đang bận':'Đợi video',videoFresh?'good':video.state==='STALE'?'warn':'neutral');
  if(!videoFresh)hasFrame=false;$('videoOverlay').hidden=videoFresh&&hasFrame;$('videoLabel').hidden=!(videoFresh&&hasFrame);$('video').hidden=!(videoFresh&&hasFrame);
  write('videoHeading',video.state==='STALE'?'Chưa có khung hình mới':video.error==='UDP_PORT_IN_USE'?'Cổng video đang được dùng':'Đợi luồng CAM03');
  write('videoDescription',video.error==='UDP_PORT_IN_USE'?'Đóng cửa sổ ffplay đang nhận CAM03 để dashboard sử dụng luồng video.':video.state==='STALE'?'Đang chờ video trở lại. Khung hình cũ không được hiển thị như video trực tiếp.':'Hình ảnh sẽ xuất hiện khi bộ phát RTP trên Pi sẵn sàng.');
  write('videoAge',video.frame_age_ms==null?'Chưa có khung hình':`Khung gần nhất: ${age(video.frame_age_ms)}`);
  write('lastUpdated',fresh?`Trạng thái Pi · ${age(connection.source_age_ms)} trước`:pi?'Đang giữ dữ liệu trước đó · đã đánh dấu hết hạn':'Chưa nhận trạng thái');
  renderDevices(pi,fresh,extra);
  drawChart(pi,fresh);
}
async function pollStatus(){try{const response=await fetch('/api/status',{cache:'no-store',signal:AbortSignal.timeout(4000)});if(!response.ok)throw Error();render(await response.json());}catch{render({connection:{state:'OFFLINE'},pi:null,video:{state:'WAITING'}});}finally{setTimeout(pollStatus,1000);}}
function pollFrame(){
  if(!videoFresh){setTimeout(pollFrame,400);return;}
  const image=$('video');let finished=false;
  const complete=ok=>{if(finished)return;finished=true;clearTimeout(timeout);hasFrame=ok;
    image.hidden=!(ok&&videoFresh);$('videoOverlay').hidden=ok&&videoFresh;$('videoLabel').hidden=!(ok&&videoFresh);
    setTimeout(pollFrame,ok?125:500);};
  const timeout=setTimeout(()=>complete(false),3000);
  image.onload=()=>complete(true);image.onerror=()=>complete(false);
  image.src='/api/frame.jpg?frame='+Date.now();
}

function clock(){write('clock',new Date().toLocaleString('vi-VN',{hour12:false,timeZone:'Asia/Ho_Chi_Minh',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}));}clock();setInterval(clock,1000);pollStatus();pollFrame();
