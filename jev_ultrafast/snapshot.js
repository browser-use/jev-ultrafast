(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  // Traverse only open roots and accessible frame documents. IDs belong to the top
  // document, never to a frame-local counter. Re-enumerate for freshness checks.
  const parent = e => e.parentElement || e.getRootNode()?.host ||
    e.ownerDocument?.defaultView?.frameElement;
  const attached = e => {
    if (!e?.isConnected) return false;
    let d=e.ownerDocument;
    while (d && d!==document) {
      const f=d.defaultView?.frameElement;
      if (!f?.isConnected || f.contentDocument!==d) return false;
      d=f.ownerDocument;
    }
    return d===document;
  };
  for (const [id,e] of cache.nodes) if (!attached(e)) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const blocked = (e,selector) => {
    for (let n=e;n;n=n.getRootNode()?.host || n.ownerDocument?.defaultView?.frameElement)
      if (n.closest(selector)) return true;
    return false;
  };
  const visible = e => attached(e) && !blocked(e,'[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) &&
    (()=>{for(let d=e.ownerDocument;d!==document;d=d.defaultView.frameElement.ownerDocument)
      if (!d.defaultView.frameElement.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return false;
      return true;})();
  // Padded frames and transforms/zoom are deliberately unsupported: forward
  // and inverse hit-test coordinates must use the same unambiguous content origin.
  const frameSupported = f => {
    const fs=f.ownerDocument.defaultView.getComputedStyle(f);
    if (['paddingLeft','paddingRight','paddingTop','paddingBottom'].some(k=>parseFloat(fs[k])!==0)) return false;
    for(let n=f;n;n=parent(n)) {
      const s=n.ownerDocument.defaultView.getComputedStyle(n);
      if (s.transform!=='none' || !['1','normal'].includes(s.zoom) ||
          s.rotate!=='none' || s.scale!=='none') return false;
    }
    return true;
  };
  const enumerate = () => {
    const roots=[], unsupported=[];
    const visit = root => {
      roots.push(root);
      for (const e of root.querySelectorAll('*')) {
        if (e.shadowRoot) visit(e.shadowRoot);
        if (e.tagName==='IFRAME' || e.tagName==='FRAME') {
          const d=e.contentDocument;
          if (d?.documentElement && frameSupported(e)) visit(d);
          else unsupported.push({node:identity(e),reason:d?'unsupported frame geometry':'inaccessible frame document'});
        }
      }
    };
    visit(document);
    return {roots,unsupported};
  };
  const {roots,unsupported}=enumerate();
  const geometry = e => {
    if (!visible(e)) return null;
    const r=e.getBoundingClientRect();
    let x=r.x+r.width/2,y=r.y+r.height/2,left=r.x,top=r.y,d=e.ownerDocument;
    if (!r.width || !r.height) return null;
    while (true) {
      const w=d.defaultView;
      if (x<0 || y<0 || x>=w.innerWidth || y>=w.innerHeight) return null;
      if (d===document) break;
      const f=w.frameElement;
      if (!frameSupported(f) || x>=f.clientWidth || y>=f.clientHeight) return null;
      const fr=f.getBoundingClientRect(), dx=fr.x+f.clientLeft,dy=fr.y+f.clientTop;
      x+=dx;y+=dy;left+=dx;top+=dy;d=f.ownerDocument;
    }
    return {x,y,rect:{x:left,y:top,w:r.width,h:r.height}};
  };
  const hit = (root,x,y) => {
    let e=root.elementFromPoint(x,y);
    if (e?.shadowRoot && e.shadowRoot!==root) {
      const inner=hit(e.shadowRoot,x,y);
      if (inner && inner!==e) e=inner;
    }
    if (e?.contentDocument && frameSupported(e)) {
      const r=e.getBoundingClientRect();
      return hit(e.contentDocument,x-r.x-e.clientLeft,y-r.y-e.clientTop) || e;
    }
    return e;
  };
  cache.resolve = e => {
    if (!attached(e) || e.matches(':disabled') || blocked(e,'[aria-disabled="true"],[inert]')) return null;
    const g=geometry(e);
    if (!g) return null;
    for (let n=hit(document,g.x,g.y);n;n=parent(n)) if (n===e) return g;
    return null;
  };
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(e.getRootNode().getElementById(id),seen)).filter(Boolean).join(' ');
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
  cache.pageKey=(current=enumerate())=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    current.roots.map(root=>[identity(root),root.nodeType===9 ? [root.URL,root.defaultView.scrollX,root.defaultView.scrollY] : null,
      [...root.querySelectorAll('input,textarea,select')].filter(safe)
        .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])]),current.unsupported];
  cache.guard=e=>{
    if (!e || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),(scope?.innerText ??
        [...(e.getRootNode().children||[])].map(n=>n.innerText||'').join('\n')).slice(0,6000)];
  };
  const actions=[];
  for (const root of roots) for (const e of root.querySelectorAll(selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || blocked(e,'[aria-disabled="true"]')) continue;
    const g=geometry(e), rname=role(e);
    if (!rname || !g) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,label:name(e)||rname,
      rect:g.rect};
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
  // Clip text fragments through every frame viewport, not their parent's center.
  const textRectVisible = (r,d) => {
    let left=r.left,right=r.right,top=r.top,bottom=r.bottom;
    while (true) {
      const w=d.defaultView, f=d===document ? null : w.frameElement;
      if (f && !frameSupported(f)) return false;
      left=Math.max(0,left);top=Math.max(0,top);
      right=Math.min(right,w.innerWidth,f ? f.clientWidth : w.innerWidth);
      bottom=Math.min(bottom,w.innerHeight,f ? f.clientHeight : w.innerHeight);
      if (right<=left || bottom<=top) return false;
      if (!f) return true;
      const fr=f.getBoundingClientRect(),dx=fr.x+f.clientLeft,dy=fr.y+f.clientTop;
      left+=dx;right+=dx;top+=dy;bottom+=dy;d=f.ownerDocument;
    }
  };
  const words=[]; let length=0;
  for (const root of roots) {
    const doc=root.nodeType===9 ? root : root.ownerDocument;
    const walker=doc.createTreeWalker(root.nodeType===9 ? root.body||root : root,NodeFilter.SHOW_TEXT);
    const range=doc.createRange(); let node;
    while ((node=walker.nextNode()) && length<6000) {
      const value=node.textContent.trim(), p=node.parentElement;
      if (!value || !p || p.closest('script,style,noscript,template') || !visible(p)) continue;
      range.selectNodeContents(node);
      if ([...range.getClientRects()].some(r=>textRectVisible(r,doc))) {
        words.push(value); length+=value.length;
      }
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey({roots,unsupported}), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key.slice(6)];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions,unsupported_frames:unsupported};
})()
