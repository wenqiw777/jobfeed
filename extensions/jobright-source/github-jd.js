/* Complete JD extraction shared by original-extension Scan and browser checks. */
var GitHubJD = (() => {
  const FETCH_CONCURRENCY = 4;
  const FETCH_PACING_MS = 2500;

  function text(value) {
    if (typeof value !== 'string') return '';
    return GitHubJDEntities.decodeHTML(value
      .replace(/<(script|style|nav|header|footer|form)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, '')
      .replace(/<!--[^]*?-->/g, '')
      .replace(/<\/?(?:br|p|div|li|h[1-6]|section)\b[^>]*>/gi, '\n')
      .replace(/<[^>]*>/g, ''))
      .replace(/[ \t]+/g,' ').replace(/\n\s*\n\s*\n/g,'\n\n').trim();
  }
  function tokens(s) { return String(s||'').toLowerCase().match(/[a-z0-9]+/g)||[]; }
  function titleMatches(a,b) {
    const wanted=tokens(a).filter(t=>t.length>2), actual=new Set(tokens(b));
    return wanted.length>0 && wanted.filter(t=>actual.has(t)).length / wanted.length >= .5;
  }
  function valid(body) { return body.length>=300 && !/^(access denied|verify you are human|just a moment)/i.test(body); }
  function fromParts(parts,title,method) {
    const seen=new Set(), output=[];
    for(const [label,raw] of parts) {const s=text(raw);if(s&&!seen.has(s)){seen.add(s);output.push(label?`${label}\n${s}`:s);}}
    const description=output.join('\n\n');
    if(!valid(description)) throw new Error('No complete job description');
    return {title,description,method};
  }
  function parse(html,target) {
    return parseDocument(new DOMParser().parseFromString(html,'text/html'),target,html);
  }
  function parseDocument(doc,target,html) {
    const postings=[];
    function collect(v) {
      if(!v||typeof v!=='object')return;
      if([].concat(v['@type']||[]).includes('JobPosting')) postings.push(v);
      if(Array.isArray(v))v.forEach(collect);else if(v['@graph'])collect(v['@graph']);
    }
    for(const script of doc.querySelectorAll('script[type="application/ld+json"],script#job-posting')){
      try{collect(JSON.parse(script.textContent));}catch{}
    }
    const nativeIds=(target.url.match(/[a-z0-9-]{6,}/gi)||[]).filter(s=>/\d/.test(s));
    const posting=postings.find(p=>{
      const id=typeof p.identifier==='object'?p.identifier?.value:p.identifier;
      return id&&nativeIds.includes(String(id));
    }) || postings.find(p=>titleMatches(target.title,p.title));
    if(posting?.description) return fromParts([
      ['',posting.description],['Responsibilities',posting.responsibilities],
      ['Qualifications',posting.qualifications],['Skills',posting.skills],
      ['Education',posting.educationRequirements],['Experience',posting.experienceRequirements],
      ['Benefits',posting.jobBenefits]
    ],posting.title,'jobposting-jsonld');
    return {description:null,error:'Page requires original-block extraction',
      page_snapshot:JobPageSnapshot.capture(doc,target.url)};
  }

  async function fetchOne(target) {
    const url=new URL(target.url);
    if(url.origin!==location.origin) throw new Error('Origin changed; needs its own page');
    let response;
    if(url.hostname==='apply.workable.com') {
      const match=url.pathname.match(/^\/([^/]+)\/j\/([^/]+)/);
      if(!match)throw new Error('Unsupported Workable job URL');
      response=await fetch(`/api/v2/accounts/${encodeURIComponent(match[1])}/jobs/${encodeURIComponent(match[2])}`,{credentials:'include',signal:AbortSignal.timeout(30000)});
      if(!response.ok)throw Object.assign(new Error(`HTTP ${response.status}`),{status:response.status});
      const job=await response.json();
      if(job.shortcode!==match[2] || !titleMatches(target.title,job.title))throw new Error('Workable job identity mismatch');
      return fromParts([['',job.description],['Requirements',job.requirements],['Benefits',job.benefits]],job.title,'workable-api');
    }
    response=await fetch(url.href,{credentials:'include',signal:AbortSignal.timeout(30000)});
    if(!response.ok)throw Object.assign(new Error(`HTTP ${response.status}`),{status:response.status});
    return parse(await response.text(),target);
  }
  async function batch(targets) {
    let next=0,blocked=false;
    const results=[];
    await Promise.all(Array.from({length:FETCH_CONCURRENCY},async()=>{
      while(!blocked&&next<targets.length){const target=targets[next++];try{results.push({...target,...await fetchOne(target),source:'github-jd'});}catch(e){
        if([401,403,429].includes(e.status))blocked=true;
        results.push({...target,source:'github-jd',description:null,error:e.message,status:e.status||null});
      }
      if(!blocked&&next<targets.length)await new Promise(resolve=>setTimeout(resolve,FETCH_PACING_MS));}
    }));
    return {results,blocked,remaining:targets.slice(next)};
  }
  function rendered(target) { const result=parseDocument(document,target,document.documentElement.outerHTML);if(result.page_snapshot)result.page_snapshot.url=location.href;return {...target,...result,source:'github-jd'}; }
  return {parse,batch,rendered,titleMatches};
})();
