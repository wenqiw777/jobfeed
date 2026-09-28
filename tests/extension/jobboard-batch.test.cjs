global.JobPageSnapshot=require('../../extensions/jobright-source/job-page-snapshot.js');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { scan } = require('../../extensions/jobright-source/jobboard-batch.js');
const reply = data => ({ok:true,status:200,json:async()=>data});
test('LinkedIn detail workers are bounded and report completed jobs before a slow peer',async()=>{
 let active=0,peak=0;const updates=[];
 const result=await scan({source:'linkedin',maxJobs:6,pacingMs:0,discoveredRows:Array.from({length:6},(_,i)=>({id:String(i)}))},async url=>{
  active++;peak=Math.max(peak,active);
  await new Promise(r=>setTimeout(r,url.endsWith('/0')?40:5));active--;
  return reply({title:'SWE',employer:{name:'ACME'},description:'JD'});
 },update=>updates.push(update));
 assert.equal(peak,2);
 assert.equal(updates.filter(u=>u.phase==='details').length,6);
 assert.notEqual(updates.find(u=>u.phase==='details').currentJobId,'0');
 assert.deepEqual(result.jobs.map(j=>j.id),['0','1','2','3','4','5']);
});
test('LinkedIn retains in-flight successes when one detail fails',async()=>{
 const result=await scan({source:'linkedin',maxJobs:5,pacingMs:0,discoveredRows:['1','2','3','4','5'].map(id=>({id}))},async url=>{
  if(url.endsWith('/1'))return {ok:false,status:401};
  await new Promise(r=>setTimeout(r,5));return reply({title:'SWE',employer:{name:'ACME'},description:'JD'});
 });
 assert.match(result.error,/401/);assert.deepEqual(result.jobs.map(j=>j.id),['2']);
});
test('LinkedIn detail 429 shares Retry-After delay with all workers',async()=>{
 let limitedAt;const starts=[],attempts=new Map(),updates=[];
 const result=await scan({source:'linkedin',maxJobs:5,pacingMs:0,discoveredRows:['1','2','3','4','5'].map(id=>({id}))},async url=>{
  const id=url.split('/').at(-1);const n=(attempts.get(id)||0)+1;attempts.set(id,n);starts.push({id,n,at:Date.now()});
  if(id==='1'&&n===1){limitedAt=Date.now();return {ok:false,status:429,headers:{get:()=> '0.05'}};}
  await new Promise(r=>setTimeout(r,5));return reply({title:'SWE',employer:{name:'ACME'},description:'JD'});
 },u=>updates.push(u));
 assert.equal(result.error,null);assert.equal(result.jobs.length,5);assert.equal(attempts.get('1'),2);
 assert.ok(starts.filter(s=>s.n>1||['3','4','5'].includes(s.id)).every(s=>s.at-limitedAt>=45));
 assert.ok(updates.some(u=>u.phase==='rate_limited'&&u.retryAfterMs>=45));
});
test('a fatal peer failure prevents a waiting detail retry from dispatching',async()=>{
 const starts=[];
 const result=await scan({source:'linkedin',maxJobs:3,pacingMs:0,discoveredRows:['1','2','3'].map(id=>({id}))},async url=>{
  const id=url.split('/').at(-1);starts.push(id);
  if(id==='1'&&starts.filter(x=>x==='1').length===1)return {ok:false,status:429,headers:{get:()=>'.03'}};
  if(id==='2'){await new Promise(r=>setTimeout(r,5));return {ok:false,status:401};}
  return reply({employer:{name:'ACME'},description:'JD'});
 });
 assert.match(result.error,/401/);assert.deepEqual(starts,['1','2']);assert.equal(result.jobs.length,0);
});
test('a second worker extends the shared deadline and repeated 429 stops after three attempts',async()=>{
 const starts=[],attempts=new Map();let lastLimitAt=0,terminal=false;
 const result=await scan({source:'linkedin',maxJobs:4,pacingMs:0,discoveredRows:['1','2','3','4'].map(id=>({id}))},async url=>{
  const id=url.split('/').at(-1),n=(attempts.get(id)||0)+1;attempts.set(id,n);starts.push({id,n,at:Date.now(),afterTerminal:terminal});
  if(id==='1'){if(n===3)terminal=true;return {ok:false,status:429,headers:{get:()=>'.01'}};}
  if(id==='2'&&n===1){await new Promise(r=>setTimeout(r,3));lastLimitAt=Date.now();return {ok:false,status:429,headers:{get:()=>'.04'}};}
  return reply({employer:{name:'ACME'},description:'JD'});
 });
 assert.match(result.error,/429/);assert.equal(attempts.get('1'),3);
 assert.ok(starts.filter(s=>s.n>1).every(s=>s.at-lastLimitAt>=35));
 assert.ok(starts.every(s=>!s.afterTerminal));
});
test('company-page 429 also delays subsequent API details while preserving fetched JDs',async()=>{
 let limitedAt=0,lookups=0;const newDetails=[];
 const result=await scan({source:'linkedin',maxJobs:4,pacingMs:0,discoveredRows:['1','2','3','4'].map(id=>({id}))},async url=>{
  if(url.includes('/jobs/view/')){lookups++;limitedAt=Date.now();return {ok:false,status:429,headers:{get:()=>'.04'}};}
  const id=url.split('/').at(-1);if(['3','4'].includes(id))newDetails.push(Date.now());
  if(id==='2')await new Promise(r=>setTimeout(r,5));
  return reply({description:'JD'});
 });
 assert.equal(result.error,null);assert.equal(result.jobs.length,4);assert.equal(lookups,1);
 assert.ok(newDetails.length===2&&newDetails.every(at=>at-limitedAt>=35));
});
test('unrecognized empty semantic page is uncertain, never claimed exhausted',async()=>{
 let calls=0;
 const result=await scan({source:'linkedin',searchUrl:'https://www.linkedin.com/jobs/search-results/?keywords=SWE',discoverOnly:true,pacingMs:0},async()=>{calls++;return {ok:true,text:async()=> '1:{"children":[]}'};});
 assert.equal(calls,3);
 assert.equal(result.stopReason,'unconfirmed_empty_page');
 assert.ok(result.warning);
 assert.equal(result.error,null);
});
test('explicit empty structured LinkedIn total ends normally',async()=>{
 const result=await scan({source:'linkedin',query:'SWE',discoverOnly:true,pacingMs:0},async()=>reply({included:[],data:{elements:[],paging:{total:0}}}));
 assert.equal(result.stopReason,'exhausted');
 assert.ok(!result.warning);
});
test('semantic search resolves company from card-local RSC state references',()=>{
 const {linkedInCards}=require('../../extensions/jobright-source/jobboard-batch.js');
 const card=id=>({componentKey:`job-card-component-ref-${id}`,viewTrackingSpecs:{viewName:'job-search-job-card'},children:['$','$L1',null,{states:`$Q${id}`} ]});
 const paragraph=name=>['$','p',null,{children:[name]}];
 const text=[`1:${JSON.stringify(card('123'))}`,`2:${JSON.stringify(card('456'))}`,
  `123:${JSON.stringify([['Default',['$','div',null,{children:[paragraph('Noom')]}]],['Dismissed',paragraph('Wrong')]])}`,
  `456:${JSON.stringify([['Default',paragraph('Example & Co')]])}`].join('\n');
 assert.deepEqual(linkedInCards(text).map(r=>[r.id,r.employer?.name]),[['123','Noom'],['456','Example & Co']]);
});
test('ambiguous paragraphs and cyclic references do not invent a company',()=>{
 const {linkedInCards}=require('../../extensions/jobright-source/jobboard-batch.js');
 const text='1:'+JSON.stringify({componentKey:'job-card-component-ref-123',viewTrackingSpecs:{viewName:'job-search-job-card'},children:'$Q2'})+'\n2:'+JSON.stringify([['Default',['$','div',null,{children:[['$','p',null,{children:['Name']}],['$','p',null,{children:['Location']}],'$Q2']}]]]);
 assert.equal(linkedInCards(text)[0].employer,null);
});
test('LinkedIn discovery does not fetch details; gated detail phase only fetches remaining IDs',async()=>{
 const requests=[];
 const request=async url=>{
  requests.push(url);
  if(url.includes('voyagerJobsDashJobCards'))return reply({included:['1','2'].map(id=>({entityUrn:`urn:li:fsd_jobPosting:${id}`}))});
  return reply({title:'SWE',employer:{name:'ACME'},description:{text:'new JD'}});
 };
 const discovery=await scan({source:'linkedin',query:'SWE',maxJobs:2,maxPages:1,pacingMs:0,discoverOnly:true},request);
 assert.equal(requests.length,1);
 assert.deepEqual(discovery.discoveredRows.map(r=>r.id),['1','2']);
 const details=await scan({source:'linkedin',maxJobs:2,maxPages:1,pacingMs:0,discoveredRows:discovery.discoveredRows,skipIds:['1']},request);
 assert.deepEqual(details.jobs.map(j=>j.id),['2']);
 assert.equal(requests.length,2);
 assert.equal(requests.filter(url=>url.includes('/jobs/view/')).length,0);
});
test('missing company uses signed-in posting title, never the public guest endpoint',async()=>{
 const { Window } = await import('../../web-ui/node_modules/happy-dom/lib/index.js');
 global.DOMParser=new Window().DOMParser;
 const result=await scan({source:'linkedin',searchUrl:'https://www.linkedin.com/jobs/search-results/?keywords=SWE',maxJobs:1,pacingMs:0},async (url,options)=>{
  assert.ok(!url.includes('/jobs-guest/'));
  if(url.includes('/flagship-web/'))return {ok:true,text:async()=> '1:'+JSON.stringify({componentKey:'job-card-component-ref-123',viewTrackingSpecs:{viewName:'job-search-job-card'}})};
  if(url.includes('/jobs/view/')){assert.equal(options.credentials,'include');return {ok:true,url,text:async()=>'<title>Engineer | Example &amp; Co | LinkedIn</title>'};}
  return reply({data:{title:'Engineer',description:{text:'Full JD'}}});
 });
 assert.equal(result.jobs[0].employer?.name,'Example & Co');
 assert.equal(result.jobs[0].description,'Full JD');
});
test('company lookup rate limit retains JD and stops further company requests',async()=>{
 let lookups=0;
 const result=await scan({source:'linkedin',query:'SWE',maxJobs:2,pacingMs:0},async url=>{
  if(url.includes('voyagerJobsDashJobCards'))return reply({included:[1,2].map(id=>({entityUrn:`urn:li:fsd_jobPosting:${id}`,title:'SWE'}))});
  if(url.includes('/jobs/view/')){lookups++;return {ok:false,status:429};}
  return reply({data:{title:'Engineer',description:{text:'Full JD'}}});
 });
 assert.equal(lookups,1);assert.equal(result.jobs.length,2);assert.equal(result.withDescription,2);
});
test('new LinkedIn links retain search filters and paginate native result batches',async()=>{
 const urls=[];
 const result=await scan({source:'linkedin',searchUrl:'https://www.linkedin.com/jobs/search-results/?keywords=SWE&geoId=1%2C2&f_TPR=r86400&start=100',startOffset:0,maxJobs:2,pacingMs:0},async(url,opts)=>{
  urls.push(url);
  if(url.includes('/flagship-web/')){
   assert.equal(opts.method,'POST');
   const u=new URL(url);assert.equal(u.searchParams.get('geoId'),'1,2');assert.equal(u.searchParams.get('f_TPR'),'r86400');
   const id=u.searchParams.get('start')==='0'?'123':'456';
   return {ok:true,text:async()=>`1:${JSON.stringify({componentKey:`job-card-component-ref-${id}`,viewTrackingSpecs:{viewName:'job-search-job-card'},children:[]})}\n`};
  }
  return reply({data:{title:'Engineer',description:{text:'Full JD'}}});
 });
 assert.equal(result.status,'succeeded');assert.deepEqual(result.jobs.map(j=>j.id),['123','456']);
 assert.deepEqual(urls.filter(u=>u.includes('/flagship-web/')).map(u=>new URL(u).searchParams.get('start')),['0','1']);
 assert.equal(result.withDescription,2);
});
test('Handshake batches preserve descriptions and advance past duplicate IDs', async()=>{
 const bodies=[];
 const result=await scan({source:'handshake',query:'AI Engineer New Grad',maxJobs:3,batchSize:2,pacingMs:0},async(url,opts)=>{
  const b=JSON.parse(opts.body);bodies.push(b);
  return reply({data:{jobSearch:{totalCount:4,edges:(b.variables.after? [{id:'2',description:'two'},{id:'3',description:'three'}]:[{id:'1',description:'one'},{id:'2',description:'two'}]).map(job=>({node:{job}}))}}});
 });
 assert.equal(result.jobs.length,3);assert.deepEqual(result.jobs.map(x=>x.description),['one','two','three']);assert.equal(bodies[1].variables.after,'Mg==');
 assert.match(bodies[0].query,/description/);
});
test('authentication error stops immediately and retains prior rows', async()=>{
 let n=0;const result=await scan({source:'handshake',query:'SWE',maxJobs:5,batchSize:1,pacingMs:0},async()=>++n===1?reply({data:{jobSearch:{totalCount:5,edges:[{node:{job:{id:'1',description:'JD'}}}]}}}):{ok:false,status:429});
 assert.equal(n,2);assert.equal(result.jobs.length,1);assert.equal(result.status,'failed');assert.match(result.error,/429/);
});
test('GraphQL errors are failures, not empty successful scans',async()=>{
 const result=await scan({source:'handshake',query:'SWE'},async()=>reply({errors:[{message:'Denied'}]}));assert.equal(result.status,'failed');
});
test('LinkedIn fetches descriptions without detail-page navigation',async()=>{
 const urls=[];const result=await scan({source:'linkedin',query:'AI Engineer New Grad',maxJobs:2,batchSize:2,pacingMs:0},async url=>{
  urls.push(url);
  if(url.includes('voyagerJobsDashJobCards'))return reply({included:[{entityUrn:'urn:li:fsd_jobPosting:123',title:'SWE'},{entityUrn:'urn:li:fsd_jobPosting:456',title:'AI'}]});
  return reply({title:'Engineer',employer:{name:'Example'},description:{text:'Full job description'}});
 });
 assert.equal(result.jobs.length,2);assert.ok(result.jobs.every(x=>x.description==='Full job description'));assert.equal(urls.length,3);assert.equal(urls.filter(x=>x.includes('/jobs/view/')).length,0);
});
test('missing body is retained as missing, never inferred from title',async()=>{
 const result=await scan({source:'handshake',query:'SWE',maxJobs:1},async()=>reply({data:{jobSearch:{edges:[{node:{job:{id:'1',title:'SWE'}}}]}}}));assert.equal(result.withDescription,0);
});
test('LinkedIn Rest.li query keeps structural delimiters and encodes keyword once',async()=>{
 let seen;await scan({source:'linkedin',query:'Software Engineer New Grad',maxJobs:1},async url=>{seen=url;return reply({included:[]});});
 assert.ok(seen.includes('&query=(origin:'));assert.ok(seen.includes('keywords:Software%20Engineer%20New%20Grad,'));assert.ok(!seen.includes('%2520'));
});
test('LinkedIn retains company name from search card',async()=>{
 const result=await scan({source:'linkedin',query:'SWE',maxJobs:1},async url=>url.includes('voyagerJobsDashJobCards')?reply({included:[{entityUrn:'urn:li:fsd_jobPosting:123',title:'SWE'},{jobPostingUrn:'urn:li:fsd_jobPosting:123',primaryDescription:{text:'Example Inc'}}]}):reply({data:{description:{text:'JD'}}}));
 assert.equal(result.jobs[0].employer.name,'Example Inc');
});
test('single-page mode resumes by raw offset and reports HTTP limit metadata',async()=>{
 let body;const first=await scan({source:'handshake',query:'SWE',maxJobs:50,startOffset:25,maxPages:1,pacingMs:0},async(_,opts)=>{body=JSON.parse(opts.body);return reply({data:{jobSearch:{totalCount:100,edges:[{node:{job:{id:'26',description:'Full JD'}}}]}}});});
 assert.equal(body.variables.after,btoa('25'));assert.equal(first.nextOffset,26);assert.equal(first.requestCount,1);
 const limited=await scan({source:'handshake',query:'SWE'},async()=>({ok:false,status:429,headers:{get:()=> '60'}}));
 assert.equal(limited.httpStatus,429);assert.equal(limited.retryAfter,'60');assert.equal(limited.requestCount,1);
});
test('Handshake newest uses the observed POST_DATE descending enum',async()=>{
 let body;await scan({source:'handshake',query:'SWE',sort:'newest'},async(_,opts)=>{body=JSON.parse(opts.body);return reply({data:{jobSearch:{edges:[]}}});});
 assert.deepEqual(body.variables.input.sort,{field:'POST_DATE',direction:'DESC'});
});
test('Handshake category scan sends role/full-time/job filters without keyword',async()=>{
 let body;await scan({source:'handshake',sort:'newest',filters:{jobTypeIds:['9'],employmentTypeIds:['1'],jobRoleGroupIds:['64']}},async(_,opts)=>{body=JSON.parse(opts.body);return reply({data:{jobSearch:{edges:[]}}});});
 assert.deepEqual(body.variables.input.filter,{jobTypeIds:['9'],employmentTypeIds:['1'],jobRoleGroupIds:['64']});
});

test('Handshake retries a browser abort at the same cursor without claiming user cancellation',async()=>{
 const requests=[];
 const result=await scan({source:'handshake',startOffset:25,discoverOnly:true,retryDelayMs:0},async(url,options)=>{
  requests.push(JSON.parse(options.body).variables.after);
  if(requests.length===1)throw new DOMException('The user aborted a request.','AbortError');
  return reply({data:{jobSearch:{edges:[{node:{job:{id:'26'}}}],totalCount:26}}});
 });
 assert.equal(result.error,null);
 assert.equal(result.discoveredRows[0].id,'26');
 assert.deepEqual(requests,[btoa('25'),btoa('25')]);
});
test('repeated request timeout is bounded and attributed to timeout, never user',async()=>{
 let calls=0;
 const result=await scan({source:'handshake',discoverOnly:true,retryDelayMs:0},async()=>{
  calls++;throw new DOMException('signal timed out','TimeoutError');
 });
 assert.equal(calls,3);
 assert.match(result.error,/handshake.*30000 ms.*3 attempts/i);
 assert.doesNotMatch(result.error,/user/i);
});
test('authentication failure is not retried',async()=>{
 let calls=0;
 const result=await scan({source:'handshake',retryDelayMs:0},async()=>{
  calls++;return {ok:false,status:401};
 });
 assert.equal(calls,1);assert.match(result.error,/401/);
});

test('recognized semantic empty-result heading and guidance end pagination',async()=>{
 const empty=['$','div',null,{children:[['$','h2',null,{children:['No results found']}],['$','$L5',null,{textProps:{children:['Try shortening or rephrasing your search.']}}]]}];
 const result=await scan({source:'linkedin',searchUrl:'https://www.linkedin.com/jobs/search-results/?keywords=SWE',startOffset:350,discoverOnly:true,pacingMs:0},async()=>({ok:true,text:async()=> '1:'+JSON.stringify(empty)}));
 assert.equal(result.stopReason,'exhausted');
 assert.equal(result.warning,null);
 assert.equal(result.requestCount,1);
 assert.equal(result.nextOffset,350);
});
test('mere empty-result words in metadata never confirm exhaustion',async()=>{
 const result=await scan({source:'linkedin',searchUrl:'https://www.linkedin.com/jobs/search-results/?keywords=SWE',discoverOnly:true,pacingMs:0},async()=>({ok:true,text:async()=> '1:'+JSON.stringify({tracking:'No results found',message:'Try shortening or rephrasing your search.'})}));
 assert.equal(result.stopReason,'unconfirmed_empty_page');
});
