const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('service worker accepts four lanes and rejects only the busy lane', async () => {
  const sent = [], started = [], release = [];
  const event = {addListener(){}};
  const context = vm.createContext({
    importScripts(){}, cancelDiscovery(){}, URL, console, setTimeout, clearTimeout, setInterval, clearInterval,
    WebSocket: {OPEN: 1, CONNECTING: 0}, pilotRunning: false,
    chrome: {runtime: {onInstalled:event,onStartup:event,onMessage:event},
      alarms: {onAlarm:event}, storage: {local:{get:() => new Promise(() => {})}}},
  });
  vm.runInContext(fs.readFileSync('extensions/jobright-source/service-worker.js', 'utf8'), context);
  context.send = message => sent.push(message);
  context.runScan = context.runBoardScan = (command, task) => {
    started.push([command.task_id, task.lane]);
    return new Promise(resolve => release.push(resolve));
  };
  const runs = ['jobright','linkedin','handshake','github-jd'].map(source =>
    context.handleMessage(JSON.stringify({type: source === 'jobright' ? 'start_scan' : 'start_board_scan', source, task_id:source})));
  assert.equal(started.length, 4);
  await context.handleMessage(JSON.stringify({type:'start_board_scan',source:'tiktok',task_id:'another'}));
  assert.equal(sent.length, 1);
  assert.match(sent[0].error, /enrichment.*busy/);
  release.forEach(resolve => resolve());
  await Promise.all(runs);
});
