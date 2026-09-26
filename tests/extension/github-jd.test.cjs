const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function loadGitHubJD(fetchImpl, delays) {
  const context = vm.createContext({
    URL,
    location: { origin: 'https://jobs.example.com' },
    fetch: fetchImpl,
    AbortSignal: { timeout: () => undefined },
    GitHubJDEntities: { decodeHTML: value => value },
    setTimeout(callback, delay) {
      delays.push(delay);
      queueMicrotask(callback);
    },
  });
  vm.runInContext(
    fs.readFileSync('extensions/jobright-source/github-jd.js', 'utf8'),
    context,
  );
  return context.GitHubJD;
}

test('GitHub JD fetching uses four paced workers', async () => {
  let active = 0;
  let maxActive = 0;
  const delays = [];
  const githubJD = loadGitHubJD(async () => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    await new Promise(resolve => queueMicrotask(resolve));
    active -= 1;
    throw new Error('synthetic fetch failure');
  }, delays);
  const targets = Array.from({ length: 9 }, (_, index) => ({
    id: String(index + 1),
    title: `Engineer ${index + 1}`,
    url: `https://jobs.example.com/${index + 1}`,
  }));

  const result = await githubJD.batch(targets);

  assert.equal(result.results.length, 9);
  assert.equal(maxActive, 4);
  assert.ok(delays.length >= 5);
  assert.ok(delays.every(delay => delay === 2500));
});

async function parser() {
  const {Window}=await import('../../web-ui/node_modules/happy-dom/lib/index.js');
  const context=vm.createContext({URL,DOMParser:new Window().DOMParser,GitHubJDEntities:{decodeHTML:v=>v}});
  vm.runInContext(fs.readFileSync('extensions/jobright-source/github-jd.js','utf8'),context);
  vm.runInContext(fs.readFileSync("extensions/jobright-source/job-page-snapshot.js","utf8"),context);
  return context.GitHubJD;
}
for (const [name,duties,requirements] of [
  ['PathAI','The Opportunity','Who You Are: (Required)'],
  ['Databricks','The impact you will have:','What we look for:'],
  ['Duolingo','🧠 You will...','✅ You have...'],
  ['CDCN','JOB DUTIES','QUALIFICATIONS'],
  ['Epic Games',"What You'll Do","What we're looking for"],
]) {
  test(`${name} full JD with alternate section headings is extracted`,async()=>{
    const jd=await parser();
    const html=`<h1>Software Engineer Intern</h1><main><h2>${duties}</h2><ul><li>Build, test, and maintain production software with engineers and product managers. Own the implementation of a complete engineering project.</li></ul><h2>${requirements}</h2><ul><li>Currently pursuing a degree in Computer Science. Experience programming in Python and Java and familiarity with algorithms, data structures, and software testing.</li></ul></main>`;
    const result=jd.parse(html,{title:'Software Engineer Intern',url:'https://jobs.example.com/1234567'});
    assert.ok(result.page_snapshot.blocks.some(b=>b.text.includes('Currently pursuing')));
    assert.ok(result.page_snapshot.blocks.some(b=>b.text.includes('Own the implementation')));
    assert.equal(jd.parse(html,{title:'Registered Nurse',url:'https://jobs.example.com/7654321'}).description,null);
    assert.equal(jd.parse(html.replace(/<h2>.*?<\/h2>/g,''),{title:'Software Engineer Intern',url:'https://jobs.example.com/1234567'}).description,null);
  });
}

test('Epic full job copy outside main is extracted without footer',async()=>{
 const jd=await parser();
 const body='Work with engineers to build reliable backend services. '.repeat(8);
 const result=jd.parse(`<h1>Backend Services Programmer Intern</h1><div class="raw-copy"><h2>What You\'ll Do</h2>${body}<h2>What we\'re looking for</h2>Java and cloud experience.</div><footer>Unrelated games</footer>`,{title:'Backend Services Programmer Intern',url:'https://epicgames.com/careers/jobs/6183293004'});
 assert.ok(result.page_snapshot.blocks.some(b=>b.text.includes('Java and cloud')));
 assert.ok(!result.page_snapshot.blocks.some(b=>b.text.includes('Unrelated games')));
});

test('Roblox main-content with You Will / You Have sections is recognized',async()=>{
 const jd=await parser();
 const html='<h1>Software Engineer</h1><div id="main-content"><strong>You Will:</strong><ul><li>'+ 'Build reliable authentication services with engineers. '.repeat(6)+'</li></ul><strong>You Have</strong><br>:<ul><li>Experience programming in Rust and JavaScript.</li></ul></div>';
 assert.ok(jd.parse(html,{title:'Software Engineer',url:'https://careers.roblox.com/jobs/8097701'}).page_snapshot.blocks.some(b=>b.text.includes('Rust and JavaScript')));
});
