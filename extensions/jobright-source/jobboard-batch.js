/* Job-only API scanner shared by the extension and its offline tests. */
var JobboardBatch = (() => {
  function linkedInSearchUrl(value, offset = 0) {
    const url = new URL(value);
    if (url.origin !== 'https://www.linkedin.com' || url.pathname !== '/jobs/search-results/') throw new Error('Invalid LinkedIn search URL');
    const clean = new URL(url.origin + url.pathname);
    for (const key of ['keywords','origin','referralSearchId','geoId','f_TPR','f_E','f_JT','f_WT','f_C','sortBy']) {
      if (url.searchParams.has(key)) clean.searchParams.set(key,url.searchParams.get(key));
    }
    if (!clean.searchParams.get('keywords')) throw new Error('LinkedIn search requires keywords');
    clean.searchParams.set('start',String(offset));
    return clean;
  }
  function repostObservation(text) {
    const evidence=String(text||'').replace(/\s+/g,' ').trim();
    return /^Reposted (?:\d+ (?:minute|hour|day|week|month|year)s? ago|yesterday|today|just now)$/i.test(evidence)
      ? {isRepost:true,repostEvidence:evidence,repostObservedAt:new Date().toISOString()}
      : {isRepost:null,repostEvidence:null,repostObservedAt:null};
  }
  function linkedInPostingEvidence(doc,url) {
    // Page ownership and repost interpretation use the shared backend extractor.
    return {page_snapshot:JobPageSnapshot.capture(doc,url||doc.URL)};
  }
  function linkedInPostingDocument(html) {
    return new DOMParser().parseFromString(html,'text/html');
  }
  function linkedInCards(text) {
    const cards = new Map();
    const records = new Map();
    for (const line of text.split('\n')) {
      const colon=line.indexOf(':');
      try { records.set(line.slice(0,colon),JSON.parse(line.slice(colon+1))); } catch { /* Non-JSON RSC record. */ }
    }
    function companyInCard(card) {
      const names=new Set(), visited=new Set();
      function textOf(value) {
        if(typeof value==='string')return value.startsWith('$')?'':value;
        if(Array.isArray(value))return value[0]==='$'?textOf(value[3]?.children):value.map(textOf).join('');
        return '';
      }
      function walk(value,depth=0) {
        if(depth>80)return;
        if(typeof value==='string') {
          const ref=/^\$Q?([a-f0-9]+)$/.exec(value);
          if(ref&&!visited.has(ref[1])){visited.add(ref[1]);walk(records.get(ref[1]),depth+1);}
        } else if(Array.isArray(value)) {
          if(value[0]==='$') {
            if(value[1]==='p'){const name=textOf(value[3]?.children).trim();if(name)names.add(name);}
            walk(value[3],depth+1);
          } else {
            const state=value.find(item=>Array.isArray(item)&&item[0]==='Default');
            if(state)walk(state[1],depth+1);else value.forEach(item=>walk(item,depth+1));
          }
        } else if(value&&typeof value==='object') {
          // Follow render children/state only, never tracking/actions or another card.
          if(value!==card&&value.viewTrackingSpecs?.viewName==='job-search-job-card')return;
          walk(value.children,depth+1);walk(value.states,depth+1);
        }
      }
      walk(card);
      return names.size===1 ? {name:[...names][0]} : null;
    }
    function repostInCard(card) {
      const visited=new Set();
      const strings=[];
      function renderedText(value,seen=new Set(),depth=0) {
        if(depth>80)return '';
        if(typeof value==='string') {
          const ref=/^\$Q?([a-f0-9]+)$/.exec(value);
          if(!ref)return value.startsWith('$')?'':value;
          if(seen.has(ref[1]))return '';
          seen.add(ref[1]);return renderedText(records.get(ref[1]),seen,depth+1);
        }
        if(Array.isArray(value)) {
          if(value[0]==='$')return renderedText(value[3],seen,depth+1);
          const state=value.find(item=>Array.isArray(item)&&item[0]==='Default');
          return state?renderedText(state[1],seen,depth+1):value.map(item=>renderedText(item,seen,depth+1)).join('');
        }
        if(value&&typeof value==='object') {
          if(value.viewTrackingSpecs?.viewName==='job-search-job-card')return '';
          return renderedText(value.children??value.textProps?.children??value.states,seen,depth+1);
        }
        return '';
      }

      function walk(value,depth=0) {
        if(depth>80)return;
        if(typeof value==='string') {
          const ref=/^\$Q?([a-f0-9]+)$/.exec(value);
          if(ref&&!visited.has(ref[1])){visited.add(ref[1]);walk(records.get(ref[1]),depth+1);}
          else if(!value.startsWith('$'))strings.push(value);
        } else if(Array.isArray(value)) {
          if(value[0]==='$'){strings.push(renderedText(value));walk(value[3],depth+1);}
          else {
            const state=value.find(item=>Array.isArray(item)&&item[0]==='Default');
            if(state)walk(state[1],depth+1);else value.forEach(item=>walk(item,depth+1));
          }
        } else if(value&&typeof value==='object') {
          if(value!==card&&value.viewTrackingSpecs?.viewName==='job-search-job-card')return;
          walk(value.children,depth+1);walk(value.states,depth+1);walk(value.textProps?.children,depth+1);
        }
      }
      walk(card);
      for(const text of strings){const observation=repostObservation(text);if(observation.isRepost)return observation;}
      return repostObservation(null);
    }
    function visit(value) {
      if (!value || typeof value !== 'object') return;
      if (value.viewTrackingSpecs?.viewName === 'job-search-job-card') {
        const id = /^job-card-component-ref-(\d+)$/.exec(value.componentKey || '')?.[1];
        if (id) cards.set(id,{id,employer:companyInCard(value),...repostInCard(value)});
        return;
      }
      for (const child of Object.values(value)) visit(child);
    }
    for (const record of records.values()) visit(record);
    return [...cards.values()];
  }
  // Recognize the rendered empty-search state, not incidental words in metadata.
  function linkedInEmptyResult(text) {
    let heading=false, guidance=false;
    function visit(value) {
      if(!value||typeof value!=='object')return;
      if(Array.isArray(value)&&value[0]==='$') {
        const props=value[3]||{};
        if(value[1]==='h2'&&[].concat(props.children||[]).includes('No results found'))heading=true;
        if([].concat(props.textProps?.children||props.children||[]).includes('Try shortening or rephrasing your search.'))guidance=true;
      }
      for(const child of Object.values(value))visit(child);
    }
    for(const line of text.split('\n')) {
      try {visit(JSON.parse(line.slice(line.indexOf(':')+1)));}catch { /* Non-JSON RSC record. */ }
    }
    return heading&&guidance;
  }
  const HANDSHAKE_QUERY = `query JobSearchQuery($first: Int, $after: String, $input: JobSearchInput) {
    jobSearch(first: $first, after: $after, input: $input) {
      totalCount edges { node { job {
        id title description createdAt expirationDate applyStart
        employer { id name }
        locations { id displayName }
        studentScreen { acceptsCptCandidates acceptsOptCandidates workAuthRequired willingToSponsorCandidate }
      } } }
    }
  }`;
  async function scan(options, request = fetch, progress = () => {}) {
    const { source, query } = options;
    if (!['linkedin', 'handshake'].includes(source)) throw new Error('Unsupported source');
    const maxJobs = Math.min(500, Math.max(1, options.maxJobs || 50));
    const size = Math.min(25, Math.max(1, options.batchSize || 25));
    const pacing = options.pacingMs ?? 1000;
    const started = Date.now(), jobs = [], batches = [], seen = new Set((options.skipIds || []).map(String));
    const headers = { Accept: source === 'linkedin' ? 'application/vnd.linkedin.normalized+json+2.1' : 'application/json', ...options.headers };
    let offset = Math.max(0, options.startOffset || 0), error = null, total = null;
    let warning = null, stopReason = null;
    let requestCount = 0, httpStatus = null, retryAfter = null;
    let companyLookupBlocked = false;
    async function metadataFromPosting(id) {
      if (companyLookupBlocked) return null;
      if (pacing || options.observeReposts) await new Promise(resolve => setTimeout(resolve, Math.max(pacing,250)));
      try {
        requestCount++;
        const url=`https://www.linkedin.com/jobs/view/${id}/`;
        const response = await request(url, {
          credentials:'include', headers:{Accept:'text/html'}, signal:AbortSignal.timeout(15000),
        });
        if (!response.ok) {
          if ([401,403,429,999].includes(response.status)) companyLookupBlocked = true;
          return null;
        }
        if(response.url && new URL(response.url).pathname!==new URL(url).pathname)return null;
        const doc = linkedInPostingDocument(await response.text());
        const parts=doc.title.split(' | ');
        const name = parts.length>=3 && parts.at(-1)==='LinkedIn' ? parts.at(-2).trim() : '';
        return {employer:name ? {name} : null,...linkedInPostingEvidence(doc,url)};
      } catch { return null; } // Missing company must never discard an already fetched JD.
    }
    async function get(url, body, asText = false) {
      for (let attempt = 0; attempt < 3; attempt++) {
        const signal = AbortSignal.timeout(30000);
        try {
          requestCount++;
          const response = await request(url, {
            method: body ? 'POST' : 'GET', credentials: 'include',
            headers: body ? { ...headers, 'Content-Type': 'application/json', ...(asText ? {'x-li-rsc-stream':'true'} : {}) } : headers,
            ...(body ? {body: JSON.stringify(body)} : {}), signal,
          });
          if (!response.ok) {
            httpStatus = response.status; retryAfter = response.headers?.get?.("Retry-After") ?? null;
            throw new Error(`HTTP ${response.status}`);
          }
          if (asText) return await response.text();
          const value = await response.json();
          if (value.errors?.length) throw new Error('GraphQL returned errors');
          return value;
        } catch (error) {
          const timedOut = signal.aborted || error.name === 'TimeoutError';
          const aborted = error.name === 'AbortError';
          const network = error.name === 'TypeError' && /fetch|network/i.test(error.message);
          if (!timedOut && !aborted && !network) throw error;
          const reason = timedOut ? 'request timed out after 30000 ms' :
            aborted ? 'request aborted by browser (AbortError)' : 'network request failed';
          console.warn('jobboard_request_retry', {source, offset, attempt:attempt+1, reason});
          if (attempt === 2) throw new Error(`${source}: ${reason}; 3 attempts at offset ${offset}`);
          await new Promise(resolve => setTimeout(resolve, (options.retryDelayMs ?? 1000) * (attempt+1)));
        }
      }
    }

    try {
      for (let page = 0; page < Math.min(40, options.maxPages || 40) && jobs.length < maxJobs; page++) {
        if (page && pacing) await new Promise(resolve => setTimeout(resolve, pacing));
        const pageStart = Date.now();
        let rows, rawCount;
        if (options.discoveredRows) {
          rows = options.discoveredRows;
          rawCount = rows.length;
        } else if (source === 'handshake') {
          const payload = await get('https://app.joinhandshake.com/hs/graphql', {
            operationName: 'JobSearchQuery', query: HANDSHAKE_QUERY,
            variables: {first:size, after:offset ? btoa(String(offset)) : null,
              input:{filter:options.filters ?? {query},sort:options.sort === 'newest' ? {direction:'DESC',field:'POST_DATE'} : {direction:'ASC',field:'RELEVANCE'},channel:'NL_SEARCH_CHANNEL'}},
          });
          const connection = payload.data?.jobSearch;
          if (!Array.isArray(connection?.edges)) throw new Error('Missing Handshake jobSearch.edges');
          total = connection.totalCount ?? null;
          rows = connection.edges.map(e => e.node?.job).filter(Boolean);
          rawCount = connection.edges.length;
        } else if (options.searchUrl) {
          const search = linkedInSearchUrl(options.searchUrl,offset);
          const endpoint = new URL(search.href);
          endpoint.pathname = '/flagship-web/jobs/search-results/';
          for(let attempt=0;attempt<3;attempt++) {
          const text = await get(endpoint.href,{
            $type:'proto.sdui.actions.core.NavigateToScreen',
            screenId:'com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails',
            pageKey:'nlsearch_srp_jobs',url:search.pathname + search.search,
          },true);
          rows = linkedInCards(text);
          if(rows.length)break;
          if(linkedInEmptyResult(text)){stopReason='exhausted';break;}
          if(attempt<2)await new Promise(resolve=>setTimeout(resolve,options.pacingMs===0?0:1000));
          }
          rawCount = rows.length;
          if(!rawCount&&stopReason!=='exhausted'){stopReason='unconfirmed_empty_page';warning=`No recognizable LinkedIn cards at offset ${offset} after 3 attempts; more results may exist`;}
        } else {
          const url = new URL('https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards');
          url.searchParams.set('decorationId','com.linkedin.voyager.dash.deco.jobs.search.JobSearchCardsCollection-220');
          url.searchParams.set('count',String(size)); url.searchParams.set('start',String(offset));
          url.searchParams.set('q','jobSearch');
          const keywords = encodeURIComponent(query).replace(/[!'()*]/g,c=>`%${c.charCodeAt(0).toString(16)}`);
          const restQuery = `(origin:JOB_SEARCH_PAGE_SEARCH_BUTTON,keywords:${keywords},locationUnion:(geoId:103644278),selectedFilters:(sortBy:List(DD),timePostedRange:List(r86400)),spellCorrectionEnabled:true)`;
          const payload = await get(`${url.href}&query=${restQuery}`);
          if (!Array.isArray(payload.included)) throw new Error('Missing LinkedIn included records');
          rows = payload.included.filter(x=>/^urn:li:fsd_jobPosting:\d+$/.test(x.entityUrn||''))
            .map(x=>{
              const card=payload.included.find(c=>(c.jobPostingUrn||c['*jobPosting'])===x.entityUrn && c.primaryDescription?.text);
              return {id:x.entityUrn.split(':').pop(),title:x.title,employer:card?{name:card.primaryDescription.text}:null};
            });
          rawCount = payload.data?.elements?.length ?? rows.length;
          total = payload.data?.paging?.total ?? null;
        }
        if(!rawCount && !warning && !stopReason) {
          if(source==='handshake'||(total!==null && offset>=total))stopReason='exhausted';
          else {stopReason='unconfirmed_empty_page';warning=`Empty page at offset ${offset}; exhaustion is unconfirmed`;}
        }
        if(source==='linkedin' && options.observeReposts) {
          for(const row of rows) {
            if(row.isRepost===true)continue;
            const metadata=await metadataFromPosting(row.id);
            if(metadata){Object.assign(row,metadata,row.employer?{employer:row.employer}:{});}
          }
        }
        if (options.discoverOnly) {
          rows=rows.slice(0,maxJobs);
          return {source,status:'succeeded',error:null,warning,stopReason,jobs:[],discoveredRows:rows,
            nextOffset:offset+rows.length,total,requestCount,batches:[{offset,returned:rows.length}],elapsedMs:Date.now()-started};
        }
        let added = 0;
        for (const row of rows) {
          if (jobs.length >= maxJobs) break;
          if (!row.id || seen.has(String(row.id))) continue;
          let detail = row;
          if (source === 'linkedin') {
            const payload = await get(`https://www.linkedin.com/voyager/api/jobs/jobPostings/${row.id}`);
            detail = payload.data || payload;
          }
          const description = typeof detail.description === 'string' ? detail.description : detail.description?.text;
          let employer = detail.employer?.name?.trim() ? detail.employer : row.employer;
          if (source === 'linkedin' && (!employer?.name?.trim() || row.isRepost!==true)) {
            const metadata=await metadataFromPosting(row.id);
            employer=employer || metadata?.employer;
            if(metadata?.page_snapshot)row.page_snapshot=metadata.page_snapshot;
            if(metadata?.isRepost===true)Object.assign(row,metadata);
          }
          jobs.push({source,id:String(row.id),title:detail.title||row.title,
            isRepost:row.isRepost ?? null,repostEvidence:row.repostEvidence ?? null,repostObservedAt:row.repostObservedAt ?? null,
            url:source==='linkedin'?`https://www.linkedin.com/jobs/view/${row.id}/`:`https://app.joinhandshake.com/jobs/${row.id}`,
            description: typeof description==='string'?description:null,
            descriptionFormat:source==='handshake'?'html':'text',
            descriptionAttributes:detail.description?.attributes ?? null,
            ...(row.page_snapshot?{page_snapshot:row.page_snapshot}:{}),
            employer:employer ?? null,locations:detail.locations ?? detail.formattedLocation ?? null,
            postedAt:detail.applyStart ?? detail.originalListedAt ?? null,
            expiresAt:detail.expirationDate ?? detail.expireAt ?? null,
            studentScreen:detail.studentScreen ?? null});
          seen.add(String(row.id)); added++;
        }
        batches.push({offset,returned:rawCount,added,elapsedMs:Date.now()-pageStart});
        progress({source,processed:jobs.length,elapsedMs:Date.now()-started});
        offset += rawCount;
        if (options.discoveredRows || !rawCount) break;
        if (total !== null && offset >= total) break;
      }
    } catch (e) { error = e.message || String(e); }
    return {source,query,status:error?'failed':'succeeded',error,warning,stopReason,jobs,batches,total,nextOffset:offset,requestCount,httpStatus,retryAfter,
      withDescription:jobs.filter(j=>j.description?.trim()).length,elapsedMs:Date.now()-started,
      observedAt:new Date().toISOString()};
  }
  return {scan,linkedInSearchUrl,linkedInCards,linkedInPostingEvidence};
})();
if (typeof module !== 'undefined') module.exports = JobboardBatch;
