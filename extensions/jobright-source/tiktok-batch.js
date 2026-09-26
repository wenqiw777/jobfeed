/* Read-only JD pilot; run in a lifeattiktok.com page through Chrome. */
var TikTokBatch = (() => {
  function parse(html, url) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const labels = ['Responsibilities', 'Qualifications', 'Job Information'];
    const sections = labels.map(label => {
      const heading = [...doc.querySelectorAll('p')].find(p => p.textContent.trim() === label);
      return {label, text: heading ? [...heading.parentElement.children].map(n => n.textContent.trim()).join('\n\n') : ''};
    });
    if (!sections[0].text || !sections[1].text) throw new Error('Missing responsibilities or qualifications');
    return {url, title:doc.querySelector('h2')?.textContent?.trim(),
      description:sections.map(s=>s.text).filter(Boolean).join('\n\n'),
      sections:sections.filter(s=>s.text).map(s=>s.label)};
  }
  async function scan(targets, progress = () => {}, concurrency = 4) {
    const start = performance.now(), jobs = [], errors = [];
    let cursor = 0, stopped = false;
    async function worker() {
      while (!stopped && cursor < targets.length) {
        const target = targets[cursor++];
        try {
          const url = new URL(target.url);
          if (url.origin !== location.origin || !/^\/search\/\d+$/.test(url.pathname)) throw new Error('Invalid TikTok job URL');
          const response = await fetch(url.href, {credentials:'include',signal:AbortSignal.timeout(30000)});
          if (!response.ok) {
            if ([401,403,429].includes(response.status)) stopped = true;
            throw new Error(`HTTP ${response.status}`);
          }
          jobs.push({id:target.id,...parse(await response.text(),target.url)});
        } catch (error) { errors.push({id:target.id,url:target.url,error:error.message}); }
        progress({processed:jobs.length+errors.length,withDescription:jobs.length,errors:errors.length});
      }
    }
    await Promise.all(Array.from({length:Math.min(8, Math.max(1, Math.floor(concurrency)))},worker));
    return {source:'tiktok',status:errors.length || stopped ? 'partial' : 'succeeded',
      requested:targets.length,concurrency,jobs,errors,stopped,withDescription:jobs.length,
      elapsedMs:Math.round(performance.now()-start),observedAt:new Date().toISOString()};
  }
  return {parse,scan};
})();
