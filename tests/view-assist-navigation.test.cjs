const {test}=require('node:test');

const assert=require('node:assert/strict');

const fs=require('node:fs');

const vm=require('node:vm');

const source=fs.readFileSync(`${__dirname}/../custom_components/view_assist/js_modules/view_assist.js`,'utf8');

function fixture(){
 const storage=new Map(),events=[],pushes=[],intervals=new Map();
let next=0;

 const location={href:'http://ha/panel-kitchen/home'};

 const context=vm.createContext({URL,Boolean,console:{log(){}},CustomEvent:class{constructor(type){this.type=type;
}},
  window:{location,dispatchEvent(e){events.push(e.type);
}},history:{pushState(s,t,path){pushes.push(path);
location.href=new URL(path,location.href).href;
}},
  localStorage:{getItem(k){return storage.get(k);
},setItem(k,v){storage.set(k,v);
},removeItem(k){storage.delete(k);
}},
  setTimeout(){return ++next;
},clearTimeout(){},setInterval(fn){const id=++next;
intervals.set(id,fn);
return id;
},clearInterval(id){intervals.delete(id);
}});

 const methods=source.slice(source.indexOf('  async subscribe_to_server('),source.indexOf('  async inject_assist_listening_overlay()'));

 const instance=vm.runInContext(`new (class {${methods}})()`,context);

 instance.variables={browser_id:'test',config:undefined};
instance.get_browser_id=()=> 'test';
instance.set_time_delta=async()=>{};

 instance.inject_assist_listening_overlay=async()=>{};

 return {instance,storage,events,pushes,intervals,location};

}
test('concurrent connects and reconnects retain one subscription and time-sync interval',async()=>{
 const {instance,intervals}=fixture();
let subscribes=0;

 const conn={async subscribeMessage(){subscribes++;
return async()=>{};
}};

 instance._hass={connection:conn,callWS:async()=>0};

 await Promise.all([instance.connect(),instance.connect()]);
await instance.connect();

 assert.equal(subscribes,1);
assert.equal(intervals.size,1);

});

test('explicit reload replaces the old subscription before registering again',async()=>{
 const {instance}=fixture();
let active=0,max=0;

 const conn={async subscribeMessage(){active++;
max=Math.max(active,max);
return async()=>{active--;
};
}};

 await instance.subscribe_to_server(conn);
await instance.subscribe_to_server(conn,true);

 assert.equal(active,1);
assert.equal(max,1);

});

test('rejected subscriptions can retry and changed connections release the previous subscription',async()=>{
 const {instance}=fixture();
let attempts=0,closed=0;

 const first={async subscribeMessage(){if(++attempts===1)throw new Error('offline');
return async()=>{closed++;
};
}};

 await assert.rejects(instance.subscribe_to_server(first),/offline/);

 await instance.subscribe_to_server(first);

 await instance.subscribe_to_server({async subscribeMessage(){return async()=>{};
}});

 assert.equal(attempts,2);
assert.equal(closed,1);

});

test('registration navigates initially, preserves a current view on reconnect, and honors changed binding/home',()=>{
 const {instance,storage}=fixture();
const calls=[];
instance.browser_navigate=p=>calls.push(p);

 const config={entity_id:'sensor.kitchen',home:'/panel-kitchen'};

 instance.process_config('registered',config);

 instance.process_config('registered',{...config});

 instance.process_config('registered',{...config,home:'/panel-kitchen/other'});

 instance.process_config('registered',{...config,entity_id:'sensor.library'});

 assert.equal(calls.length,3);
assert.equal(storage.get('view_assist_sensor'),'sensor.library');

});

test('identical URLs do not navigate;
 new intent variables still refresh without adding history',async()=>{
 const {instance,events,pushes}=fixture();
instance.variables.config={mimic_device:false};

 instance.browser_navigate('/panel-kitchen/home');
assert.equal(events.length,0);
assert.equal(pushes.length,0);

 await instance.incoming_message({event:'navigate',payload:{path:'/panel-kitchen/home',variables:{entity:'light.test'}}});

 assert.equal(events.length,1);
assert.equal(pushes.length,0);
assert.equal(instance.variables.navigation.entity,'light.test');

 instance.browser_navigate('/view-assist/clock');
assert.equal(events.length,2);
assert.equal(pushes.length,1);

});

test('mimic registration and navigation never move the browser',async()=>{
 const {instance,pushes}=fixture();

 instance.process_config('registered',{entity_id:'sensor.kitchen',home:'/panel-kitchen',mimic_device:true});

 await instance.incoming_message({event:'navigate',payload:{path:'/view-assist/clock'}});

 assert.equal(pushes.length,0);

});

