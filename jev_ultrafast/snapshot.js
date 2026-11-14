(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    (!e.checkVisibility || e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}));
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const doc = e.ownerDocument || document;
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(doc.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  const getDocuments = (rootDoc = document, ox = 0, oy = 0, depth = 0, fw = undefined, fh = undefined) => {
    if (depth > 6) return [];
    const list = [{ doc: rootDoc, ox, oy, fw, fh }];
    try {
      const frames = rootDoc.querySelectorAll('iframe, frame');
      for (const f of frames) {
        try {
          if (!visible(f)) continue;
          const fr = f.getBoundingClientRect();
          if (fr.width <= 0 || fr.height <= 0) continue;
          const fox = ox + fr.x, foy = oy + fr.y;
          if (fox >= innerWidth || foy >= innerHeight || fox + fr.width <= 0 || foy + fr.height <= 0) continue;
          const cdoc = f.contentDocument;
          if (cdoc && cdoc.body) {
            list.push(...getDocuments(cdoc, fox, foy, depth + 1, fr.width, fr.height));
          }
        } catch (e) {}
      }
    } catch (e) {}
    return list;
  };
  const docs = getDocuments();
  cache.pageKey=()=>{
    const inputs = [];
    for (const {doc} of docs) {
      inputs.push(...[...doc.querySelectorAll('input,textarea,select')].filter(safe)
        .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly]));
    }
    return [performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,inputs];
  };
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  const actions=[];
  for (const {doc, ox, oy, fw, fh} of docs) {
    for (const e of doc.querySelectorAll(selector)) {
      if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
      const r=e.getBoundingClientRect(), x=ox+r.x+r.width/2, y=oy+r.y+r.height/2, rname=role(e);
      if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
      if (fw !== undefined && (r.x + r.width <= 0 || r.y + r.height <= 0 || r.x >= fw || r.y >= fh)) continue;
      if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
      const base={node:identity(e),role:rname,label:name(e)||rname,
        rect:{x:ox+r.x,y:oy+r.y,w:r.width,h:r.height}};
      for (const key of ['checked','selected','expanded']) {
        const value=e.getAttribute('aria-'+key);
        if (value!==null) base[key]=value;
      }
      if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
      if (e.tagName==='SELECT') {
        for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
          actions.push({...base,kind:'select',value:o.value,
            current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
      } else {
        const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
          (['textbox','searchbox','spinbutton'].includes(rname) ||
            (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
        const value='value' in e ? String(e.value) :
          e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
        actions.push({...base,kind:editable?'fill':'click',value});
        if (editable) actions.push({...base,kind:'click',value,label:'Open '+base.label});
      }
    }
  }
  const words=[];
  let totalLength = 0;
  for (const {doc, ox, oy} of docs) {
    if (!doc.body || totalLength >= 6000) break;
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    const range = doc.createRange();
    let node;
    while ((node = walker.nextNode()) && totalLength < 6000) {
      const value = node.textContent.trim(), parent = node.parentElement;
      if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
      range.selectNodeContents(node);
      const r = range.getBoundingClientRect();
      const rx = ox + r.x, ry = oy + r.y;
      if (r.width > 0 && r.height > 0 && (ry + r.height) > 0 && ry < innerHeight && (rx + r.width) > 0 && rx < innerWidth) {
        words.push(value);
        totalLength += value.length;
      }
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6]];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  const scrollDelta = Math.round(innerHeight * 0.75);
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:scrollDelta});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-scrollDelta});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
