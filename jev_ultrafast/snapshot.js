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
  const styleCache = new WeakMap();
  const styleOf = el => {
    if (!el || el.nodeType !== 1) return null;
    let s = styleCache.get(el);
    if (!s) {
      s = window.getComputedStyle(el);
      styleCache.set(el, s);
    }
    return s;
  };
  const isClickable = e => {
    if (['HTML','BODY','SCRIPT','STYLE','NOSCRIPT','TEMPLATE','SVG','PATH'].includes(e.tagName)) return false;
    if (e.offsetWidth === 0 && e.offsetHeight === 0 && e.getClientRects().length === 0) return false;
    if (e.parentElement?.closest('button,a[href],select,[role="button"],[role="link"]')) return false;
    if (e.querySelector('button,select,input,textarea,a[href],[role="button"],[role="link"]')) return false;
    const style = styleOf(e);
    if (!style || style.cursor !== 'pointer') return false;
    const parentStyle = styleOf(e.parentElement);
    if (parentStyle && parentStyle.cursor === 'pointer') return false;
    const distinctChildren = Array.from(e.children).filter(c => {
      if (['IMG','SVG','PATH','I','CANVAS'].includes(c.tagName)) return false;
      const ct = c.textContent.trim(), et = e.textContent.trim();
      if (!ct || ct === et) return false;
      return styleOf(c)?.cursor === 'pointer';
    });
    return distinctChildren.length === 0;
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
    if (isClickable(e)) return 'button';
    return null;
  };
  const flyoutSelector = 'dialog[open],[role="dialog"],[aria-modal="true"],.ant-popover:not(.ant-popover-hidden),.ant-dropdown:not(.ant-dropdown-hidden),.ant-select-dropdown:not(.ant-select-dropdown-hidden)';
  const findActiveFlyout = () =>
    [...document.querySelectorAll(flyoutSelector)].find(f => f.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}));
  cache.flyoutSelector = flyoutSelector;
  cache.findActiveFlyout = findActiveFlyout;
  cache.pageKey=(flyout=findActiveFlyout())=>{
    return [performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
      [...document.querySelectorAll('input,textarea,select')].filter(safe)
        .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly]),
      flyout ? identity(flyout) : null];
  };
  cache.guard=(e,flyout=findActiveFlyout())=>{
    if (!e?.isConnected || !visible(e)) return null;
    if (flyout && !flyout.contains(e)) return null;
    const rname=role(e);
    const editable=e.tagName==='INPUT' || e.tagName==='TEXTAREA' || e.isContentEditable ||
      ['textbox','searchbox','combobox','spinbutton'].includes(rname);
    const scope=editable ? e : (e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement);
    return [identity(e),rname,name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),flyout ? identity(flyout) : null,editable ? '' : (scope?.innerText?.slice(0,6000)||'')];
  };
  const actions=[], seen=new Set();
  const elements=[...document.querySelectorAll(selector)];
  for (const e of document.querySelectorAll('div,span,li,p')) {
    if (e.offsetWidth === 0 && e.offsetHeight === 0 && e.getClientRects().length === 0) continue;
    if (isClickable(e)) elements.push(e);
  }
  const activeFlyout=findActiveFlyout();
  const hasFlyout=!!activeFlyout;
  for (const e of elements) {
    if (seen.has(e)) continue;
    seen.add(e);
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    const inFlyout=!!(hasFlyout && activeFlyout.contains(e));
    if (hasFlyout && !inFlyout) continue;
    const maxY=inFlyout ? innerHeight + 150 : innerHeight;
    if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=maxY) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    let lbl=name(e).trim();
    if (!lbl && !e.querySelector('img')) lbl=rname;
    const base={node:identity(e),role:rname,label:lbl||rname,
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
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(activeFlyout), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node),activeFlyout);
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6],page_key[7]];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
