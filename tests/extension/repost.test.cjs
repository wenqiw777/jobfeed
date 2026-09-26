global.JobPageSnapshot=require('../../extensions/jobright-source/job-page-snapshot.js');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const board=require('../../extensions/jobright-source/jobboard-batch.js');
const card=(id,children)=>({componentKey:`job-card-component-ref-${id}`,viewTrackingSpecs:{viewName:'job-search-job-card'},children});
const span=text=>['$','span',null,{children:text}];
test('real LinkedIn September 2026 detail HTML identifies repost without an h1',async()=>{
 const {readFileSync}=require('node:fs');
 const { Window }=await import('../../web-ui/node_modules/happy-dom/lib/index.js');
 const html=readFileSync(require('node:path').join(__dirname,'fixtures/linkedin-repost-4188979310.html'),'utf8');
 global.DOMParser=new Window().DOMParser;
 const doc=new DOMParser().parseFromString(html,'text/html');
 assert.equal(doc.querySelector('h1'),null);
 assert.ok(board.linkedInPostingEvidence(doc,'https://www.linkedin.com/jobs/view/4188979310/').page_snapshot.blocks.some(b=>b.text.includes('Reposted 1 day ago')));

});
test('only explicit repost wording from the matching rendered card is evidence',()=>{
 const text=[`1:${JSON.stringify(card('123',['$Q2',card('456',span('Reposted 1 day ago'))]))}`,
 `2:${JSON.stringify([['Default',span('2 hours ago')],['Dismissed',span('Reposted 8 hours ago')]])}`].join('\n');
 assert.equal(board.linkedInCards(text)[0].isRepost,null);
 const explicit='1:'+JSON.stringify(card('123',span('Reposted 1 day ago')));
 const row=board.linkedInCards(explicit)[0];
 assert.equal(row.isRepost,true);assert.equal(row.repostEvidence,'Reposted 1 day ago');
 assert.ok(row.repostObservedAt);
});
test('a repost word in company text or action metadata does not classify a card',()=>{
 const row=card('123',[['$','p',null,{children:'Reposted Labs'}],span('1 day ago')]);
 row.actions={description:'Reposted 1 day ago'};
 assert.equal(board.linkedInCards('1:'+JSON.stringify(row))[0].isRepost,null);
});
test('details carry fresh discovery evidence',async()=>{
 const result=await board.scan({source:'linkedin',pacingMs:0,discoveredRows:[{id:'123',employer:{name:'ACME'},isRepost:true,repostEvidence:'Reposted 1 day ago',repostObservedAt:'2026-09-20T12:00:00Z'}]},async()=>({ok:true,json:async()=>({title:'SWE',description:{text:'JD'}})}));
 assert.equal(result.jobs[0].isRepost,true);
 assert.equal(result.jobs[0].repostEvidence,'Reposted 1 day ago');
});
test('detail evidence stays in the title header and excludes recommendation cards',async()=>{
 const { Window }=await import('../../web-ui/node_modules/happy-dom/lib/index.js');
 const parser=new (new Window().DOMParser)();
 const doc=parser.parseFromString('<main><section><h1>Engineer</h1><span>Reposted 1 day ago</span></section><section><h2>Recommended</h2><span>Reposted 4 days ago</span></section></main>','text/html');
 assert.ok(board.linkedInPostingEvidence(doc,'https://www.linkedin.com/jobs/view/4188979310/').page_snapshot.blocks.some(b=>b.text.includes('Reposted 1 day ago')));
 const unknown=parser.parseFromString('<main><section><h1>Engineer</h1><span>1 day ago</span></section><section><h2>Recommended</h2><span>Reposted 4 days ago</span></section></main>','text/html');
 assert.equal(board.linkedInPostingEvidence(unknown,'https://www.linkedin.com/jobs/view/123/').isRepost,undefined);
});
test('discovery fallback observes existing IDs before a JD cache lookup can skip them',async()=>{
 const { Window }=await import('../../web-ui/node_modules/happy-dom/lib/index.js');
 global.DOMParser=new Window().DOMParser;
 const requests=[];
 const result=await board.scan({source:'linkedin',query:'SWE',discoverOnly:true,observeReposts:true,pacingMs:0},async url=>{
  requests.push(url);
  if(url.includes('voyagerJobsDashJobCards'))return {ok:true,json:async()=>({included:[{entityUrn:'urn:li:fsd_jobPosting:123'}]})};
  return {ok:true,url,text:async()=>'<title>SWE | ACME | LinkedIn</title><section><h1>SWE</h1><span>Reposted 2 hours ago</span></section>'};
 });
 assert.ok(result.discoveredRows[0].page_snapshot.blocks.some(b=>b.text.includes('Reposted 2 hours ago')));
 assert.equal(result.discoveredRows[0].employer.name,'ACME');
 assert.equal(requests.some(url=>url.includes('/voyager/api/jobs/jobPostings/')),false);
});
test('rendered repost labels may be split across nested text and RSC references',()=>{
 const text='1:'+JSON.stringify(card('123',['$','span',null,{children:['Reposted ',['$','strong',null,{children:'2'}],' hours ago']}]))+'\n2:'+JSON.stringify(span('ignore'));
 assert.equal(board.linkedInCards(text)[0].repostEvidence,'Reposted 2 hours ago');
});
