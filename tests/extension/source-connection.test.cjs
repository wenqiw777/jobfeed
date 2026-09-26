const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
test('concurrent startup and alarm connect calls own only one socket',async()=>{
 const pending=[],sockets=[],event={addListener(){}};
 class Socket {
  static OPEN=1;static CONNECTING=0;
  constructor(){this.readyState=0;this.listeners={};sockets.push(this);}
  addEventListener(name,fn){this.listeners[name]=fn;}
  send(){}close(){this.readyState=3;}
 }
 const c=vm.createContext({importScripts(){},URL,console,WebSocket:Socket,setTimeout,clearTimeout,setInterval:()=>1,clearInterval(){},chrome:{runtime:{onInstalled:event,onStartup:event,onMessage:event},alarms:{onAlarm:event},storage:{local:{get:()=>new Promise(resolve=>pending.push(resolve))}}}});
 vm.runInContext(fs.readFileSync('extensions/jobright-source/service-worker.js','utf8'),c);
 const second=c.connect();
 for(const resolve of pending)resolve({});
 await second;await new Promise(resolve=>setImmediate(resolve));
 assert.equal(sockets.length,1);
 sockets[0].readyState=1;sockets[0].listeners.open();
 c.closeSocket();
 sockets[0].listeners.message({data:JSON.stringify({type:'ready',protocol:1})});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(vm.runInContext('connectionState',c),'disconnected');
});
