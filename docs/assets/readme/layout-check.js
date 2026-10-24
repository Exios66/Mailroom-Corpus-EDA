/* Browser geometry regression for generated constellation maps. No dependencies. */
async function checkConstellation(source, time=0) {
  const host = document.createElement('div');
  host.style.cssText = 'position:absolute;left:-10000px;top:0;width:1200px';
  host.innerHTML = source;
  document.body.append(host);
  await document.fonts.ready;
  const svg = host.querySelector('svg');
  svg.pauseAnimations();
  svg.setCurrentTime(time);
  const view = svg.viewBox.baseVal;
  const errors = [];
  const bounds = element => {
    const b = element.getBBox(), m = element.getCTM();
    const points = [[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]]
      .map(([x,y]) => new DOMPoint(x,y).matrixTransform(m));
    return {x:Math.min(...points.map(p=>p.x)),y:Math.min(...points.map(p=>p.y)),
      right:Math.max(...points.map(p=>p.x)),bottom:Math.max(...points.map(p=>p.y))};
  };
  // SVG's intrinsic dimensions equal viewBox dimensions in these assets.
  const contains = (outer, inner, pad=0) => inner.x>=outer.x+pad && inner.right<=outer.right-pad &&
    inner.y>=outer.y+pad && inner.bottom<=outer.bottom-pad;
  const overlaps = (a,b,pad=0) => a.x<b.right+pad && a.right>b.x-pad && a.y<b.bottom+pad && a.bottom>b.y-pad;
  const canvas = {x:0,y:0,right:view.width,bottom:view.height};
  const nodeRects = [...svg.querySelectorAll('rect[rx="14"]:not(.motion)')].filter(r=>+r.getAttribute('width')>=150);
  const nodes = nodeRects.map(r=>({element:r,box:bounds(r)}));
  for(const t of svg.querySelectorAll('text')) {
    const b=bounds(t), label=t.textContent;
    if(!contains(canvas,b,12))errors.push({kind:'canvas-margin',label,box:b});
    if(label==='YOU ARE HERE') {
      const pill=t.previousElementSibling;
      if(!pill || pill.tagName!=='rect' || !contains(bounds(pill),b,8))
        errors.push({kind:'badge-padding',label,box:b,pill:pill?bounds(pill):null});
    } else {
      const own=nodes.find(n=>contains(n.box,b));
      if(own&&!contains(own.box,b,10))errors.push({kind:'node-padding',label,box:b,node:own.box});
    }
  }
  for(const node of svg.querySelectorAll('[data-node]')) {
    const box=bounds(node.querySelector('rect'));
    for(const caption of node.querySelectorAll('text')) {
      const b=bounds(caption);
      if(b.x-box.x<12 || box.right-b.right<12 || b.y-box.y<10 || box.bottom-b.bottom<10)
        errors.push({kind:'owned-node-padding',label:caption.textContent,box:b,node:box});
    }
  }
  const labels=[...svg.querySelectorAll('[data-edge-label],g[font-style="italic"] > text')];
  for(const label of labels) {
    const b=bounds(label);
    const backing=label.querySelector?.('[data-callout-box]');
    const caption=label.querySelector?.('text');
    if(!backing || !caption) errors.push({kind:'missing-label-backing',label:label.textContent});
    else {
      const outer=bounds(backing), inner=bounds(caption);
      if(inner.x-outer.x<8 || outer.right-inner.right<8 || inner.y-outer.y<5 || outer.bottom-inner.bottom<5)
        errors.push({kind:'callout-padding',label:label.textContent,box:inner,backing:outer});
    }
    if(!contains(canvas,b,12))errors.push({kind:'callout-canvas-margin',label:label.textContent,box:b});
    for(const badge of svg.querySelectorAll('[data-current-badge] rect'))
      if(overlaps(b,bounds(badge),4))errors.push({kind:'label-badge-overlap',label:label.textContent});
    for(const node of nodes)if(overlaps(b,node.box,4))errors.push({kind:'label-node-overlap',label:label.textContent,box:b,node:node.box});
  }
  for(let i=0;i<labels.length;i++)for(let j=i+1;j<labels.length;j++)
    if(overlaps(bounds(labels[i]),bounds(labels[j]),4))errors.push({kind:'callout-overlap',labels:[labels[i].textContent,labels[j].textContent]});
  for(const badge of svg.querySelectorAll('[data-current-badge]')) {
    const b=bounds(badge.querySelector('rect'));
    for(const node of svg.querySelectorAll('[data-node]')) {
      for(const caption of node.querySelectorAll('text'))
        if(overlaps(b,bounds(caption),4))errors.push({kind:'badge-node-text-overlap',label:caption.textContent});
      if(node.dataset.node!==badge.dataset.currentBadge && overlaps(b,bounds(node.querySelector('rect')),4))
        errors.push({kind:'badge-unrelated-node-overlap',node:node.dataset.node});
    }
  }
  // A legend must not collide with the last row of cards.
  for(const t of svg.querySelectorAll('text'))if(t.textContent.startsWith('dashed')) {
    for(const node of nodes)if(overlaps(bounds(t),node.box,16))errors.push({kind:'legend-node-overlap',label:t.textContent});
  }
  // The static labels must be later in paint order than animated markers.
  const particles=[...svg.querySelectorAll('circle.motion')];
  for(const label of labels)for(const dot of particles)
    if(label.compareDocumentPosition(dot)&Node.DOCUMENT_POSITION_FOLLOWING) {
      errors.push({kind:'particle-over-label',label:label.textContent});break;
    }
  host.remove();
  return errors;
}


async function checkStats(source) {
  const host=document.createElement('div');host.style.cssText='position:absolute;left:-10000px';host.innerHTML=source;document.body.append(host);
  await document.fonts.ready;
  const texts=[...host.querySelectorAll('svg > text')];
  const errors=[];
  for(let i=0;i<texts.length;i+=2) {
    const value=texts[i].getBBox(),caption=texts[i+1].getBBox();
    const gap=caption.y-(value.y+value.height);
    if(gap<10)errors.push({kind:'stat-value-caption-gap',label:texts[i+1].textContent,gap});
  }
  host.remove();return errors;
}
