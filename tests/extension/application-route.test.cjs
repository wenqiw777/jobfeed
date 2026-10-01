const {test}=require('node:test');
const assert=require('node:assert/strict');
test('rendered HTML preserves Apply, base and structured job data but never form values',async()=>{
  const {Window}=await import('../../web-ui/node_modules/happy-dom/lib/index.js');
  global.JobPageSnapshot=require('../../extensions/jobright-source/job-page-snapshot.js');
  const reader=require('../../extensions/jobright-source/application-route.js');
  const win=new Window({url:'https://careers.example/jobs/123'});
  win.document.write('<head><base href="https://careers.example/"><script type="application/ld+json">{"@type":"JobPosting","title":"Engineer"}</script><script>window.secret="private"</script></head><body><main><h1>Engineer</h1><a href="/apply/123">Apply</a><iframe src="https://boards.greenhouse.io/embed/job_app?for=acme&token=123"></iframe><form><input value="private-email"><textarea>private-letter</textarea><select><option selected>private-name</option></select></form></main></body>');
  const snapshot=reader.capture(win.document,win.location.href);
  assert.equal(snapshot.url,win.location.href);assert.match(snapshot.html,/application\/ld\+json/);assert.match(snapshot.html,/\/apply\/123/);assert.match(snapshot.html,/job_app/);assert.match(snapshot.html,/<base /);
  assert.doesNotMatch(snapshot.html,/private/);assert.doesNotMatch(JSON.stringify(snapshot.page_snapshot),/private/);
});
test('oversize DOM fails instead of supplying truncated identity evidence',async()=>{
  const {Window}=await import('../../web-ui/node_modules/happy-dom/lib/index.js');
  global.JobPageSnapshot=require('../../extensions/jobright-source/job-page-snapshot.js');
  const reader=require('../../extensions/jobright-source/application-route.js');
  const win=new Window();win.document.body.textContent='x'.repeat(512001);
  assert.throws(()=>reader.capture(win.document,'https://careers.example/jobs/1'),/size/);
});
