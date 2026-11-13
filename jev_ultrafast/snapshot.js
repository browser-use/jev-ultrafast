(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const query = (selector,found) => found.flatMap(root=>[...root.querySelectorAll(selector)]);
  const composed = (start=document) => {
    const elements=[], roots=start===document ? [document] : [], seen=new Set();
    const children = node => {
      if (node.tagName==='SLOT') {
        const assigned=node.assignedNodes({flatten:true});
        return assigned.length ? assigned.filter(n=>n.nodeType===1) : [...node.children];
      }
      if (node.shadowRoot) {
        roots.push(node.shadowRoot);
        return [...node.shadowRoot.children];
      }
      return [...node.children];
    };
    const stack=(start===document ? [...document.children] : children(start)).reverse();
    while (stack.length) {
      const e=stack.pop();
      if (seen.has(e)) continue;
      seen.add(e);
      elements.push(e);
      const descendants=children(e);
      for (let i=descendants.length-1;i>=0;i--) stack.push(descendants[i]);
    }
    return {elements,roots};
  };
  const observed=composed(), observedRoots=observed.roots;
  const parent = e => e?.assignedSlot || e?.parentElement || e?.getRootNode?.().host || null;
  const closest = (e,selector) => {
    for (let node=e;node;node=parent(node)) if (node.matches?.(selector)) return node;
    return null;
  };
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !closest(e,'[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const root=e.getRootNode(), children=e.tagName==='SLOT' ? e.assignedNodes({flatten:true}) : e.childNodes;
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(root.getElementById?.(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...children].map(n=>n.nodeType===3 ? n.textContent :
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
  cache.pageKey=(found=composed().roots)=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    query('input,textarea,select',found).filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=closest(e,'form,dialog,[role="dialog"],article,li,tr,[role="row"]') ||
      e.parentElement || e.getRootNode();
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),(scope?.innerText??scope?.textContent??'').slice(0,6000)];
  };
  const actions=[];
  for (const e of observed.elements) {
    if (!e.matches(selector)) continue;
    if (!safe(e) || !visible(e) || e.matches(':disabled') || closest(e,'[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    if (rname==='gridcell' && composed(e).elements.some(child=>child.matches('button,[role="button"]'))) continue;
    const base={node:identity(e),role:rname,label:name(e)||rname,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !closest(o,'optgroup[disabled]'))
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
  const words=[], range=document.createRange(); let length=0;
  for (const root of observedRoots) {
    const walker=document.createTreeWalker(root===document ? document.body : root,NodeFilter.SHOW_TEXT);
    let node;
    while ((node=walker.nextNode()) && length<6000) {
      const value=node.textContent.trim(), owner=node.parentElement||root.host;
      if (!value || !owner || closest(owner,'script,style,noscript,template') || !visible(owner)) continue;
      range.selectNodeContents(node); const r=range.getBoundingClientRect();
      if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
        words.push(value); length+=value.length;
      }
    }
    if (length>=6000) break;
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(observedRoots), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6]];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
