const start=document.querySelector('#start'),progress=document.querySelector('#progress');
let lastResult=null;
function show(result){
  lastResult=result;document.querySelector('#download').disabled=false;
  document.querySelector('#result').textContent=JSON.stringify({source:result.source,status:result.status,jobs:result.jobs.length,withDescription:result.withDescription,apiSeconds:result.elapsedMs/1000,totalSeconds:result.totalElapsedMs/1000,error:result.error},null,2);
}
chrome.storage.local.get(['jobboardPilotResult','jobboardPilotState']).then(data=>{
  if(data.jobboardPilotResult)show(data.jobboardPilotResult);
  if(data.jobboardPilotState?.status==='running')progress.textContent='A test is running. Results will be saved here.';
});
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local'&&changes.jobboardPilotResult?.newValue)show(changes.jobboardPilotResult.newValue);});
start.addEventListener('click',async()=>{
  start.disabled=true;progress.textContent='Opening signed-in search and collecting batches…';
  try{
    const response=await chrome.runtime.sendMessage({type:'pilot_scan',source:document.querySelector('#source').value,query:document.querySelector('#query').value,maxJobs:Number(document.querySelector('#limit').value)});
    if(response.error)throw new Error(response.error);
    show(response.result);progress.textContent=response.result.status==='succeeded'?'Finished':'Stopped; partial results retained';
  }catch(e){progress.textContent=e.message;}finally{start.disabled=false;}
});
document.querySelector('#download').addEventListener('click',()=>{
  if(!lastResult)return;const url=URL.createObjectURL(new Blob([JSON.stringify(lastResult,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download=`${lastResult.source}-batch-${Date.now()}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
