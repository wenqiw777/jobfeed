/* Generic original-text blocks. No employer names, CSS classes, or JD headings. */
var JobPageSnapshot=(()=>{
  function capture(doc,url){
    const blocks=[];let size=0,truncated=false;
    const skip=new Set(['SCRIPT','STYLE','NOSCRIPT','NAV','FOOTER','LABEL','INPUT','TEXTAREA','SELECT','BUTTON','SVG','TEMPLATE']);
    const boundaries=new Set(['P','DIV','SECTION','ARTICLE','MAIN','HEADER','ASIDE','LI','UL','OL','TABLE','TR','TD','H1','H2','H3','H4','H5','H6','BR']);
    function hidden(el){
      if(skip.has(el.tagName)||el.hidden||el.getAttribute('aria-hidden')==='true'||['navigation'].includes(el.getAttribute('role'))||/display\s*:\s*none/i.test(el.getAttribute('style')||''))return true;
      if(doc.defaultView){const style=doc.defaultView.getComputedStyle(el);return style.display==='none';}
      return false;
    }
    function visibleText(el){
      if(doc.defaultView)return !['hidden','collapse'].includes(doc.defaultView.getComputedStyle(el).visibility);
      return !/visibility\s*:\s*(?:hidden|collapse)/i.test(el.getAttribute('style')||'');
    }
    function walk(el,path=[]){
      if(hidden(el))return;
      const group=[...path,++sequence];let chunks=[],links=[];
      function flush(){const value=chunks.join('').replace(/\s+/g,' ').trim();chunks=[];if(!value)return;if(blocks.length>=1200||size+value.length>80000){truncated=true;return;}size+=value.length;blocks.push({id:blocks.length,text:value,kind:/^H[1-6]$/.test(el.tagName)?'heading':el.tagName==='LI'?'list_item':'text',path:group,links:[...new Set(links)]});links=[];}
      function inline(node){if(node.nodeType===3){if(visibleText(node.parentElement))chunks.push(node.textContent);return;}if(node.nodeType!==1||hidden(node))return;if(boundaries.has(node.tagName)){flush();walk(node,group);return;}if(node.tagName==='A'){try{const u=new URL(node.getAttribute('href'),url);if(['http:','https:'].includes(u.protocol))links.push(u.href);}catch{}}for(const child of node.childNodes)inline(child);}
      for(const child of el.childNodes)inline(child);flush();
    }
    let sequence=0;walk(doc.body||doc.documentElement);
    return {url,title:doc.title||'',blocks,truncated};
  }
  return {capture};
})();
if(typeof module!=='undefined')module.exports=JobPageSnapshot;
