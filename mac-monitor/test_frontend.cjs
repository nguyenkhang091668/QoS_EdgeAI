const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
// Unit-test the real renderer with an in-memory DOM; no browser or production data.
class Element{
  constructor(){this.textContent='';this.children=[];this.hidden=false;this.className='';this.attrs={};}
  replaceChildren(...x){this.children=x;this.textContent=x.map(v=>v.textContent||'').join('');}
  append(...x){this.children.push(...x);for(const v of x)v.parent=this;}
  prepend(x){this.children.unshift(x);x.parent=this;}
  get lastElementChild(){return this.children.at(-1);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
  querySelector(){return null;}
  setAttribute(k,v){this.attrs[k]=v;}
}
const elements={};const context=vm.createContext({console,Date,Number,Math,document:{
  getElementById:id=>elements[id]??=(new Element()),createElement:()=>new Element(),
  createTextNode:text=>({textContent:text})}});
const code=fs.readFileSync(__dirname+'/web/app.js','utf8');
new vm.Script(code);
vm.runInContext(code.slice(0,code.indexOf('function clock(){')),context);
const router=()=>({verification:'VERIFIED',verified_age_ms:1000,verified_ttl_ms:35000,
  applied_policy:'ALERT',applied_kbps:8000,pending_policy:null,has_error:false,wan:{mbps:.5}});
const sample=()=>({connection:{state:'CONNECTED',source_age_ms:50},pi:{sampled_at_utc:'fixture',
  ai:{state:'ALERT',available:true,simulated:false,person_in_roi:true,observation_age_ms:200,observation_ttl_ms:3500,frame_id:12},
  qos:{profile:'standard',normal_kbps:1000,alert_kbps:8000,desired_policy:'ALERT',routers:{R1:router(),R2:router()}},
  camera:{encoder:{status:'RUNNING'}}},video:{state:'LIVE',frame_age_ms:20}});
const render=data=>{context.fixture=data;vm.runInContext('render(fixture)',context);};
vm.runInContext('hasFrame=true',context);let data=sample();render(data);assert.equal(elements.aiState.textContent,'ALERT');
assert.equal(elements.r1Rate.textContent,(8000).toLocaleString('vi-VN'));
data.connection.state='OFFLINE';render(data);assert.equal(elements.aiState.textContent,'CHỜ DỮ LIỆU');
assert.equal(elements.person.textContent,'Chưa rõ');assert.equal(elements.r1Rate.textContent,'—');
assert.equal(elements.videoOverlay.hidden,true); // RTP can remain live after status SSH is lost.
data=sample();data.pi.ai.observation_age_ms=4000;render(data);assert.equal(elements.aiState.textContent,'CHỜ DỮ LIỆU');
data=sample();data.pi.qos.routers.R1.verified_age_ms=40000;render(data);assert.equal(elements.r1Rate.textContent,'—');
data=sample();data.video.state='STALE';render(data);assert.equal(elements.video.hidden,true);
assert.equal(elements.videoOverlay.hidden,false);assert.equal(elements.aiState.textContent,'ALERT');
data=sample();data.pi.qos.routers.R1.pending_policy='NORMAL';render(data);
assert.match(elements.r1Badge.textContent,/Đang đồng bộ/);
data=sample();data.pi.ai.simulated=true;render(data);assert.equal(elements.aiState.textContent,'CHỜ DỮ LIỆU');
data=sample();data.connection.source_age_ms=5000;render(data);assert.equal(elements.r1Rate.textContent,'—');
data=sample();data.pi.qos.routers.R1.pending_policy='ALERT';render(data);assert.match(elements.r1Badge.textContent,/Đã xác minh/);
console.log('9 renderer scenarios passed: fresh, offline, stale AI, stale policy, stale video, pending, simulated, old publisher.');

const deviceNames=['R1','R2','DSW1','DSW2','ASW'];
function withDevices(){const d=sample();d.pi.devices=Object.fromEntries(deviceNames.map(name=>[name,{
  ip:'192.0.2.1',status:'ONLINE',age_ms:1000,ttl_ms:6000,
  interfaces:[{source_id:name+'_uplink',interface:'GigabitEthernet0/0',label:name+' uplink',direction:'rx',
    status:'ONLINE',age_ms:1000,mbps:.5,rx_mbps:.5,tx_mbps:.1}]}]));return d;}
function content(el){return [el.textContent,...(el.children||[]).map(content)].join(' ');}
data=withDevices();render(data);assert.equal(elements.deviceCards.children.length,5);
assert.equal(elements.portRows.children.length,5);assert.match(elements.networkSummary.textContent,/5\/5/);
data=withDevices();data.pi.devices.ASW.interfaces[0].status='INTERFACE_DOWN';render(data);
assert.match(elements.networkSummary.textContent,/5\/5/);assert.match(content(elements.deviceCards.children[4]),/1 cổng xuống/);
assert.match(content(elements.portRows.children[4]),/Cổng xuống/);
data=withDevices();data.pi.devices.DSW1.age_ms=5900;data.pi.devices.DSW1.interfaces[0].age_ms=5900;
data.connection.source_age_ms=200;render(data);assert.match(content(elements.deviceCards.children[2]),/Dữ liệu cũ/);
assert.match(content(elements.portRows.children[2]),/Dữ liệu cũ/);assert.doesNotMatch(content(elements.portRows.children[2]),/0,5/);
data=withDevices();data.pi.devices.DSW2.status='OFFLINE';data.pi.devices.DSW2.interfaces[0].status='OFFLINE';render(data);
assert.match(content(elements.deviceCards.children[3]),/Không phản hồi/);
data=withDevices();data.connection.state='OFFLINE';render(data);
assert.match(content(elements.deviceCards.children[2]),/Chưa rõ/);assert.match(elements.networkSummary.textContent,/Chờ kết nối/);
data=sample();render(data);assert.match(elements.networkSummary.textContent,/Chưa có dữ liệu/);
console.log('6 device scenarios passed: all devices, interface down, expired transport, offline host, disconnected Pi, older API.');
