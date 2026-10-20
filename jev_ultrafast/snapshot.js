(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  cache.scrollGuard=()=>{
    const root=document.scrollingElement;
    if (!root) return null;
    const point={x:Math.min(550,innerWidth-1),y:Math.min(650,innerHeight-1)};
    const hit=document.elementFromPoint(point.x,point.y), context=[];
    let blocked=!hit || [root,document.body].some(e=>
      e && (['hidden','clip'].includes(getComputedStyle(e).overflowY) ||
        getComputedStyle(e).position==='fixed'));
    for (let e=hit;e && e!==root;e=e.parentElement) {
      const style=getComputedStyle(e);
      if (['fixed','sticky'].includes(style.position) || e.matches('dialog,[aria-modal="true"]'))
        context.push(identity(e));
      // Global scroll actions must not accidentally scroll a nested container.
      if (e!==document.body && ['auto','scroll'].includes(style.overflowY) &&
          e.scrollHeight>e.clientHeight+2) blocked=true;
    }
    return {
      position:[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight],
      point,context,
      can_scroll_up:!blocked && scrollY>0,
      can_scroll_down:!blocked && scrollY+innerHeight<root.scrollHeight-2,
    };
  };
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
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
  // Some custom buttons have only a pointer cursor and a name, with delegated click handlers.
  const pointerCandidates=new Map(), pointerParents=new Set();
  for (const e of document.querySelectorAll('div,span,[onclick],[tabindex]')) {
    if (e.closest(selector) || !safe(e) || !visible(e) || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect();
    if (r.width<=0 || r.height<=0 || getComputedStyle(e).cursor!=='pointer') continue;
    const label=name(e).replace(/\s+/g,' ').trim();
    if (label && label.length<=160) pointerCandidates.set(e,label);
  }
  for (const e of pointerCandidates.keys())
    for (let p=e.parentElement;p;p=p.parentElement) pointerParents.add(p);
  cache.pointerControls=new Set();
  for (const [e,label] of pointerCandidates) {
    if (pointerParents.has(e)) continue;
    let target=e;
    while (pointerCandidates.get(target.parentElement)===label) target=target.parentElement;
    cache.pointerControls.add(target);
  }
  cache.hitPoint=e=>{
    const r=e.getBoundingClientRect();
    const left=Math.max(0,r.left), right=Math.min(innerWidth,r.right);
    const top=Math.max(0,r.top), bottom=Math.min(innerHeight,r.bottom);
    if (right<=left || bottom<=top) return null;
    const dx=Math.min(8,(right-left)/4), dy=Math.min(8,(bottom-top)/4);
    const midX=(left+right)/2, midY=(top+bottom)/2;
    const points=[
      [r.x+r.width/2,r.y+r.height/2],
      [left+dx,top+dy], [right-dx,top+dy], [left+dx,bottom-dy], [right-dx,bottom-dy],
      [midX,top+dy], [midX,bottom-dy], [left+dx,midY], [right-dx,midY],
    ];
    return points.find(([x,y])=>{
      if (x<0 || y<0 || x>=innerWidth || y>=innerHeight) return false;
      const hit=document.elementFromPoint(x,y);
      if (!e.contains(hit)) return false;
      // A nested, independent control must not receive the parent's click.
      for (let p=hit;p && p!==e;p=p.parentElement)
        if (p.matches(selector) || cache.pointerControls.has(p)) return false;
      return true;
    }) || null;
  };
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
    return cache.pointerControls.has(e) && getComputedStyle(e).cursor==='pointer' ? 'button' : null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  // These summaries guide document scrolling only; they never identify executable targets.
  const offscreen={above:new Map(),below:new Map()};
  const reachableByPageScroll = (e,r) => {
    for (let p=e; p && p!==document.documentElement; p=p.parentElement) {
      const style=getComputedStyle(p);
      if (style.position==='fixed') return false;
      if (p===e || p===document.body) continue;
      const box=p.getBoundingClientRect();
      if (style.overflowY!=='visible' && (r.bottom<=box.top || r.top>=box.bottom)) return false;
      if (style.overflowX!=='visible' && (r.right<=box.left || r.left>=box.right)) return false;
    }
    return true;
  };
  const height=document.documentElement.scrollHeight;
  const scroll_guard=cache.scrollGuard();
  const actions=[];
  for (const e of [...document.querySelectorAll(selector),...cache.pointerControls]) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0 || x<0 || x>=innerWidth) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    if (y<0 || y>=innerHeight) {
      const direction=y<0 ? 'above' : 'below';
      const canScroll=direction==='above' ? scroll_guard?.can_scroll_up : scroll_guard?.can_scroll_down;
      if (canScroll && reachableByPageScroll(e,r)) {
        const label=name(e).replace(/\s+/g,' ').trim().slice(0,160);
        if (label) offscreen[direction].set(JSON.stringify([rname,label]),{role:rname,label,direction});
      }
      continue;
    }
    // Use the same read-only hit tests here and immediately before actual input.
    if (!cache.hitPoint(e)) continue;
    const base={node:identity(e),role:rname,label:name(e)||rname,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
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
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000);
  const offscreen_controls=[], omitted_offscreen_controls={};
  for (const direction of ['above','below']) {
    // Bound both directions independently. Buttons/fields precede repeated navigation links.
    const controls=[...offscreen[direction].values()].sort((a,b)=>
      Number(a.role==='link')-Number(b.role==='link'));
    offscreen_controls.push(...controls.slice(0,40));
    omitted_offscreen_controls[direction]=Math.max(0,controls.length-40);
  }
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6],offscreen_controls,omitted_offscreen_controls,scroll_guard];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scroll_guard?.can_scroll_down) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scroll_guard?.can_scroll_up) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,scroll_guard,omitted_actions,
    offscreen_controls,omitted_offscreen_controls};
})()
