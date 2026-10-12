/* charts.js — chart toolkit.
   Time series use ApexCharts (hover, themed). Everything categorical (composition, rankings,
   sparklines, calendar, diverging bars) is plain HTML/SVG: crisper, directly labelled, no legend
   lookup, no render races.  All colours come from the active theme at call time. */
(function(g){
const PAL_L=['#2C3FD8','#0E7C5A','#B4690E','#C2352E','#1F6F8B','#7A3FB0','#8A8F3A','#C0508A','#5B6770','#2F8F9D'];
const PAL_D=['#8E9BFF','#4FD1A0','#E8A94A','#FF7A70','#6DB6D1','#C79BFF','#C9D06A','#F28DBB','#9AA7A0','#7FD6E0'];
function isDark(){
  const a=document.documentElement.getAttribute('data-theme');
  return a?a==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
}
const pal=()=>isDark()?PAL_D:PAL_L;
const FONT="'Geist','Segoe UI',sans-serif";
const fmt=n=>new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Math.round(n||0));
const fmtK=n=>Math.abs(n)>=1e6?(n/1e6).toFixed(1)+'M':Math.abs(n)>=1000?(n/1000).toFixed(Math.abs(n)>=10000?0:1)+'k':fmt(n);
const esc_=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function T(){
  return isDark()
    ? {fore:'#A4B2AA',grid:'rgba(233,239,235,.09)',dim:'#7A8A81',ink:'#E9EFEB',tooltip:'dark',neutral:'#3A4640',accent:'#8E9BFF',pos:'#4FD1A0',neg:'#FF7A70',warn:'#E8A94A'}
    : {fore:'#445349',grid:'rgba(18,32,26,.09)',dim:'#5F6E66',ink:'#12201A',tooltip:'light',neutral:'#C3C9BF',accent:'#2C3FD8',pos:'#0E7C5A',neg:'#C2352E',warn:'#A85F0A'};
}
function base(){
  const t=T();
  return {
    chart:{background:'transparent',foreColor:t.fore,fontFamily:FONT,toolbar:{show:false},zoom:{enabled:false},
      animations:{enabled:true,easing:'easeinout',speed:400}},
    grid:{borderColor:t.grid,strokeDashArray:0,padding:{left:6,right:10},xaxis:{lines:{show:false}}},
    tooltip:{theme:t.tooltip},
    legend:{fontSize:'12.5px',fontFamily:FONT,labels:{colors:t.fore},markers:{width:8,height:8,radius:2},itemMargin:{vertical:3}},
    xaxis:{axisBorder:{show:false},axisTicks:{show:false},labels:{style:{colors:t.dim,fontSize:'11.5px',fontFamily:FONT}}},
    yaxis:{labels:{style:{colors:t.dim,fontSize:'11.5px',fontFamily:FONT},formatter:v=>fmtK(v)}},
  };
}

/* phones: no slanted labels, fewer ticks, slightly smaller type so axes stay readable */
function phoneFit(c){
  const x=c.xaxis||{},horiz=c.plotOptions&&c.plotOptions.bar&&c.plotOptions.bar.horizontal;
  const n=(x.categories||[]).length,small=f=>({...f,style:{...(f&&f.style),fontSize:'10.5px'}});
  const out={...c,
    chart:{...c.chart,animations:{enabled:false}},
    grid:{...c.grid,padding:{left:0,right:18}},
    legend:c.legend&&{...c.legend,fontSize:'11.5px',itemMargin:{horizontal:6,vertical:2}},
    yaxis:Array.isArray(c.yaxis)?c.yaxis.map(y=>({...y,labels:small(y.labels)})):c.yaxis&&{...c.yaxis,labels:small(c.yaxis.labels)}};
  const ys=c.series||[],nonNeg=ys.length&&ys.every(q=>(q.data||[]).every(v=>v==null||v>=0));
  if(c.chart&&c.chart.type==='bar'&&!horiz&&nonNeg&&!Array.isArray(c.yaxis))out.yaxis={...out.yaxis,min:0};
  if(!horiz){
    const lab=x.labels||{},fm=lab.formatter,step=n>5?Math.ceil(n/(n>12?4:5)):1;
    out.xaxis={...x,tickAmount:undefined,
      labels:{...small(lab),rotate:0,rotateAlways:false,hideOverlappingLabels:false,trim:false,
        formatter:(v,ts,o)=>{const i=o&&o.i!==undefined?o.i:(x.categories||[]).indexOf(v);
          if(step>1&&i>=0&&i%step!==0)return '';return fm?fm(v,ts,o):v;}}};
  }
  return out;
}

const _instances={};
function mount(container,config){
  if(!container)return Promise.resolve(null);
  const id=container.id||container;
  if(_instances[id]){try{_instances[id].destroy();}catch(e){}delete _instances[id];}
  container.innerHTML='';
  if(window.innerWidth<640)config=phoneFit(config);
  const chart=new ApexCharts(container,config);
  _instances[id]=chart;
  return chart.render().then(()=>chart).catch(()=>chart);
}
function note(container,text,sub){
  if(!container)return;
  container.innerHTML=`<div class="chart-note"><div>${text}</div>${sub?`<div class="chart-note-sub">${sub}</div>`:''}</div>`;
}
const shortDate=d=>{const x=new Date(d+'T00:00:00');return isNaN(x)?d:x.toLocaleDateString('en-US',{month:'short',day:'numeric'});};

/* ─── net-worth style trend (area). Honest about sparse data: straight segments + dots when few points ─── */
function line(container,points,opts={}){
  if(!container)return Promise.resolve();
  if(!points||!points.length){note(container,'No history yet','Net worth is saved automatically once a day.');return Promise.resolve();}
  if(points.length===1){note(container,`<b class="note-big">${CUR} ${fmt(points[0].total)}</b>`,'One day of history so far. The trend appears from tomorrow.');return Promise.resolve();}
  const t=T(),B=base(),few=points.length<=12;
  const vals=points.map(p=>Math.round(p.total*100)/100);
  const lo=Math.min(...vals),hi=Math.max(...vals),pad=Math.max((hi-lo)*0.25,hi*0.01);
  const config={...B,
    chart:{...B.chart,type:'area',height:opts.height||240},
    series:[{name:opts.name||'Value',data:vals}],
    xaxis:{...B.xaxis,categories:points.map(p=>p.snap_date),tickAmount:Math.min(5,points.length-1),
      labels:{...B.xaxis.labels,rotate:0,hideOverlappingLabels:true,formatter:v=>shortDate(v)}},
    yaxis:{...B.yaxis,min:Math.max(0,lo-pad),max:hi+pad,tickAmount:4},
    colors:[t.accent],
    stroke:{curve:few?'straight':'smooth',width:2.5},
    fill:{type:'gradient',gradient:{shadeIntensity:1,opacityFrom:.16,opacityTo:0,stops:[0,95,100]}},
    markers:{size:few?4:0,strokeWidth:0,hover:{size:6}},
    dataLabels:{enabled:false},
    tooltip:{theme:t.tooltip,x:{formatter:(v,o)=>shortDate(points[o.dataPointIndex].snap_date)},y:{formatter:v=>CUR+' '+fmt(v)}},
  };
  return mount(container,config);
}

/* ─── columns: neutral bars, the latest (or selected) one in accent, optional average line ─── */
function bar(container,data,opts={}){
  if(!container)return;
  if(!data||!data.length||!data.some(d=>d.value>0)){note(container,'No data yet');return;}
  const t=T(),B=base(),sel=opts.selected;
  const maxI=data.reduce((m,d,i)=>d.value>data[m].value?i:m,0);
  const hi=opts.highlight==='max'?maxI:data.length-1;
  const colors=data.map((d,i)=>sel!=null?(d.label===sel?t.accent:t.neutral):(opts.highlight==='none'?t.accent:i===hi?t.accent:t.neutral));
  const avg=opts.average?data.reduce((s,d)=>s+d.value,0)/data.length:null;
  const config={...B,
    chart:{...B.chart,type:'bar',height:opts.height||230,
      events:opts.onClick?{dataPointSelection:(e,ctx,cfg)=>opts.onClick(data[cfg.dataPointIndex].label)}:{}},
    series:[{name:'Amount',data:data.map(d=>Math.round(d.value*100)/100)}],
    xaxis:{...B.xaxis,categories:data.map(d=>d.label)},
    yaxis:{...B.yaxis,tickAmount:4,min:0},
    colors,
    plotOptions:{bar:{borderRadius:3,columnWidth:data.length>8?'60%':'48%',distributed:true,dataLabels:{position:'top'}}},
    dataLabels:{enabled:data.length<=8,offsetY:-18,style:{fontSize:'11.5px',fontFamily:FONT,colors:[t.fore]},formatter:v=>v>0?fmtK(v):''},
    legend:{show:false},
    annotations:avg!=null?{yaxis:[{y:avg,borderColor:t.dim,strokeDashArray:4,
      label:{text:'avg '+fmtK(avg),position:'left',offsetX:8,style:{color:t.fore,background:'transparent',fontSize:'11px',fontFamily:FONT}}}]}:{},
    tooltip:{theme:t.tooltip,y:{formatter:v=>CUR+' '+fmt(v)}},
    states:{hover:{filter:{type:'none'}},active:{filter:{type:'none'}}},
  };
  mount(container,config);
}

/* ─── month-to-date pace: last month vs this month, optional dashed run-rate projection ─── */
function dualLine(container,seriesA,seriesB,opts={}){
  if(!container)return;
  if((!seriesA||!seriesA.length)&&(!seriesB||!seriesB.length)){note(container,'Not enough data yet');return;}
  const t=T(),B=base();
  const proj=opts.projection;
  const maxDay=Math.max(1,...(seriesA||[]).map(p=>p.day),...(seriesB||[]).map(p=>p.day),proj?proj.endDay:0);
  const toSeries=pts=>{const m={};(pts||[]).forEach(p=>m[p.day]=p.total);return Array.from({length:maxDay},(_,i)=>m[i+1]!=null?m[i+1]:null);};
  const series=[{name:opts.labelA||'A',data:toSeries(seriesA)},{name:opts.labelB||'B',data:toSeries(seriesB)}];
  const dash=[0,0];
  if(proj){
    series.push({name:'On pace for',data:Array.from({length:maxDay},(_,i)=>{const d=i+1;return d===proj.startDay?proj.startValue:d===proj.endDay?proj.endValue:null;})});
    dash.push(5);
  }
  const config={...B,
    chart:{...B.chart,type:'line',height:opts.height||260},
    series,
    xaxis:{...B.xaxis,categories:Array.from({length:maxDay},(_,i)=>i+1),tickAmount:8,title:{text:'Day of month',style:{color:t.dim,fontSize:'11.5px',fontFamily:FONT,fontWeight:400}}},
    colors:[t.dim,t.accent,t.accent],
    stroke:{curve:'straight',width:[2,3,2],dashArray:dash},
    markers:{size:proj?[0,0,5]:0,strokeWidth:0},
    dataLabels:{enabled:false},
    legend:{...B.legend,position:'top',horizontalAlign:'left'},
    tooltip:{theme:t.tooltip,shared:true,y:{formatter:v=>v==null?'—':CUR+' '+fmt(v)}},
  };
  if(proj)config.stroke.connectNulls=true;
  mount(container,config);
}

/* ─── generic multi-series line chart (assistant output): labels + any number of series ─── */
function lines(container,labels,series,opts={}){
  if(!container)return;
  if(!labels||!labels.length||!series||!series.length){note(container,'No data to chart');return;}
  const t=T(),B=base(),u=opts.unit?opts.unit+' ':'';
  mount(container,{...B,
    chart:{...B.chart,type:'line',height:opts.height||280},
    series:series.map(s=>({name:s.name,data:s.data})),
    xaxis:{...B.xaxis,categories:labels,tickAmount:Math.min(labels.length-1,7),labels:{...B.xaxis.labels,rotate:0,hideOverlappingLabels:true,formatter:v=>/^\d{4}-\d{2}-\d{2}$/.test(v)?shortDate(v):v}},
    yaxis:{...B.yaxis,labels:{...B.yaxis.labels,formatter:v=>u+fmtK(v)}},
    colors:pal(),stroke:{curve:'straight',width:2.4},markers:{size:0,hover:{size:5}},dataLabels:{enabled:false},
    legend:{...B.legend,position:'top',horizontalAlign:'left',show:series.length>1},
    tooltip:{theme:t.tooltip,shared:true,y:{formatter:v=>v==null?'-':u+(Math.abs(v)<100?v.toFixed(1):fmt(v))}},
  });
}

/* ─── stacked columns (e.g. take-home + deductions = gross, per month) ─── */
function stackedColumns(container,categories,series,opts={}){
  if(!container)return;
  if(!categories||!categories.length){note(container,'No data yet');return;}
  const t=T(),B=base();
  mount(container,{...B,
    chart:{...B.chart,type:'bar',height:opts.height||280,stacked:true,
      events:opts.onClick?{dataPointSelection:(e,ctx,cfg)=>opts.onClick(cfg.dataPointIndex)}:{}},
    series:series.map(s=>({name:s.name,data:s.data.map(v=>Math.round(v*100)/100)})),
    xaxis:{...B.xaxis,categories,labels:{...B.xaxis.labels,rotate:categories.length>8?-45:0,hideOverlappingLabels:true}},
    colors:opts.colors||[t.accent,t.neutral],
    plotOptions:{bar:{columnWidth:categories.length>14?'72%':'52%',borderRadius:2}},
    dataLabels:{enabled:false},legend:{...B.legend,position:'top',horizontalAlign:'left'},
    tooltip:{theme:t.tooltip,shared:true,intersect:false,y:{formatter:v=>CUR+' '+fmt(v)}},
    states:{hover:{filter:{type:'none'}}},
  });
}

/* ─── stacked area (composition over time) ─── */
function stackedArea(container,categories,series,opts={}){
  if(!container)return;
  if(!series||!series.length||!categories||categories.length<4){note(container,'Composition over time builds with daily snapshots','Needs at least 4 days of history.');return;}
  const B=base(),t=T();
  mount(container,{...B,
    chart:{...B.chart,type:'area',height:opts.height||280,stacked:true},
    series:series.map(s=>({name:s.name,data:s.values.map(v=>Math.round(v*100)/100)})),
    xaxis:{...B.xaxis,categories,labels:{...B.xaxis.labels,hideOverlappingLabels:true,formatter:v=>shortDate(v)}},
    colors:pal(),stroke:{curve:'straight',width:1.5,colors:[isDark()?'#161C18':'#F7F8F5']},fill:{type:'solid',opacity:.9},
    dataLabels:{enabled:false},legend:{...B.legend,position:'bottom',horizontalAlign:'left'},
    tooltip:{theme:t.tooltip,shared:true,y:{formatter:v=>CUR+' '+fmt(v)}},
  });
}

/* ─── projection: contributions-only vs with growth, goal lines ─── */
function projection(container,labels,seriesList,goals,opts={}){
  if(!container)return;
  const t=T(),B=base();
  const allMax=Math.max(...seriesList.flatMap(s=>s.data));
  const goalLines=(goals||[]).filter(g=>g.amount<=allMax*1.15).map(g=>({y:g.amount,borderColor:t.pos,strokeDashArray:4,
    label:{text:g.name,position:'left',offsetX:8,style:{color:'#fff',background:t.pos,fontSize:'11px',fontFamily:FONT}}}));
  mount(container,{...B,
    chart:{...B.chart,type:'line',height:opts.height||280},
    series:seriesList.map(s=>({name:s.name,data:s.data.map(v=>Math.round(v))})),
    xaxis:{...B.xaxis,categories:labels,tickAmount:Math.min(labels.length-1,6)},
    colors:[t.dim,t.accent],stroke:{curve:'straight',width:[2,3],dashArray:[5,0]},
    markers:{size:0,hover:{size:5}},dataLabels:{enabled:false},
    legend:{...B.legend,position:'top',horizontalAlign:'left'},
    annotations:{yaxis:goalLines},
    tooltip:{theme:t.tooltip,shared:true,y:{formatter:v=>CUR+' '+fmt(v)}},
  });
}

/* ─── composition: one stacked bar + directly-labelled rows (replaces donuts) ─── */
function stackBar(container,items,opts={}){
  if(!container)return;
  items=(items||[]).filter(i=>Math.abs(i.value)>0);
  if(!items.length){note(container,'No data yet');return;}
  const P=pal(),total=opts.total||items.reduce((s,i)=>s+Math.abs(i.value),0)||1,fmtv=opts.format||(v=>CUR+' '+fmt(v));
  let rows=items.slice(0,opts.max||6);
  if(items.length>rows.length){const rest=items.slice(rows.length);rows=[...rows,{label:'Other',value:rest.reduce((s,i)=>s+i.value,0),color:null}];}
  rows=rows.map((r,i)=>({...r,color:r.color||(r.label==='Other'?T().neutral:P[i%P.length])}));
  container.innerHTML=`<div class="sb"><div class="sb-bar" role="img" aria-label="${esc_(opts.label||'Composition')}">${
    rows.map(r=>`<span style="flex:${Math.abs(r.value)};background:${r.color}" title="${esc_(r.label)} · ${(Math.abs(r.value)/total*100).toFixed(1)}%"></span>`).join('')}</div>
    <div class="sb-rows">${rows.map(r=>`<div class="sb-row ${opts.onClick?'clickable':''}" data-label="${esc_(r.label)}">
      <i style="background:${r.color}"></i><span class="sb-l">${esc_(r.label)}</span>
      <span class="sb-v">${fmtv(r.value)}</span><span class="sb-p">${(Math.abs(r.value)/total*100).toFixed(r.value/total<.1?1:0)}%</span></div>`).join('')}</div></div>`;
  if(opts.onClick)container.querySelectorAll('.sb-row').forEach(el=>el.onclick=()=>opts.onClick(el.dataset.label));
}

/* ─── ranked horizontal bars (categories, merchants, cards, positions) ─── */
function rankBars(container,items,opts={}){
  if(!container)return;
  items=(items||[]).filter(i=>i.value>0);
  if(!items.length){note(container,opts.empty||'No data yet');return;}
  const max=opts.max||Math.max(...items.map(i=>i.value)),sel=opts.selected,fmtv=opts.format||(v=>fmt(v));
  const total=items.reduce((s,i)=>s+i.value,0);
  container.innerHTML=`<div class="rb">${items.slice(0,opts.limit||8).map(i=>{
    const dim=sel!=null&&sel!==i.label;
    return `<div class="rb-row ${opts.onClick?'clickable':''} ${dim?'dim':''}" data-label="${esc_(i.label)}">
      <span class="rb-l" title="${esc_(i.label)}">${esc_(i.label)}</span>
      <span class="rb-track"><span class="rb-fill" style="width:${Math.max(1.5,i.value/max*100)}%;${i.color?`background:${i.color}`:''}"></span></span>
      <span class="rb-v">${fmtv(i.value)}</span>${opts.showPct?`<span class="rb-p">${(i.value/total*100).toFixed(0)}%</span>`:''}</div>`;}).join('')}</div>`;
  if(opts.onClick)container.querySelectorAll('.rb-row').forEach(el=>el.onclick=()=>opts.onClick(el.dataset.label));
}

/* ─── sparkline (returns SVG markup) ─── */
function spark(values,opts={}){
  const w=opts.w||120,h=opts.h||36,v=(values||[]).filter(x=>x!=null);
  if(v.length<2)return `<svg class="spark" width="${w}" height="${h}"></svg>`;
  const lo=Math.min(...v),hi=Math.max(...v),rng=(hi-lo)||1,pad=3;
  const pts=v.map((x,i)=>[pad+i*(w-2*pad)/(v.length-1),h-pad-(x-lo)/rng*(h-2*pad)]);
  const d=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
  const last=pts[pts.length-1],color=opts.color||'var(--accent)';
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <path d="${d} L${last[0]} ${h} L${pts[0][0]} ${h}Z" fill="${color}" opacity=".08"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.6" fill="${color}"/></svg>`;
}

/* ─── calendar heat grid: weeks as columns, weekdays as rows ─── */
function calendar(container,days){
  if(!container)return;
  if(!days||!days.length){note(container,'No spend data yet');return;}
  const vals=days.map(d=>d.value).filter(v=>v>0).sort((a,b)=>a-b);
  const q=p=>vals.length?vals[Math.min(vals.length-1,Math.floor(p*vals.length))]:0;
  const cuts=[q(.25),q(.5),q(.75),q(.92)];
  const level=v=>v<=0?0:v<=cuts[0]?1:v<=cuts[1]?2:v<=cuts[2]?3:4;
  const weeks=[];for(let i=0;i<days.length;i+=7)weeks.push(days.slice(i,i+7));
  const DOW=['S','M','T','W','T','F','S'];
  const col=w=>DOW.map((_,dow)=>{const d=w.find(x=>x.weekday===dow);
    return d?`<span class="cal-c l${level(d.value)}" title="${d.date} · ${d.value>0?CUR+' '+fmt(d.value):'no spend'}"></span>`:`<span class="cal-c empty"></span>`;}).join('');
  container.innerHTML=`<div class="cal"><div class="cal-days">${DOW.map((d,i)=>`<span>${i%2?d:''}</span>`).join('')}</div>
    <div class="cal-grid">${weeks.map(w=>`<div class="cal-col">${col(w)}</div>`).join('')}</div></div>
    <div class="cal-legend"><span>Less</span>${[0,1,2,3,4].map(l=>`<span class="cal-c l${l}"></span>`).join('')}<span>More</span></div>`;
}

/* ─── diverging bars: change per item around a zero line. up = bad (spend), down = good ─── */
function diverge(container,items,opts={}){
  if(!container)return;
  items=(items||[]).filter(i=>i.delta!=null);
  if(!items.length){note(container,'No change to show yet');return;}
  const max=Math.max(...items.map(i=>Math.abs(i.delta)),0.01),fmtv=opts.format||(v=>(v>0?'+':'')+v.toFixed(1));
  container.innerHTML=`<div class="dv">${items.map(i=>{const w=Math.abs(i.delta)/max*50,up=i.delta>0,tone=(opts.goodUp?!up:up)?'up':'down';
    return `<div class="dv-row"><span class="dv-l" title="${esc_(i.label)}">${esc_(i.label)}</span>
      <span class="dv-track"><span class="dv-zero"></span><span class="dv-fill ${tone}" style="width:${w}%;${up?'left:50%':'right:50%'}"></span></span>
      <span class="dv-v ${tone}">${fmtv(i.delta)}</span></div>`;}).join('')}</div>`;
}

/* destroy charts before their page is hidden, so a zero-width resize can't redraw them as NaN */
function destroyAll(){for(const id in _instances){try{_instances[id].destroy();}catch(e){}delete _instances[id];}}

g.MiniChart={destroyAll,pal,lines,stackedColumns,line,bar,dualLine,stackedArea,projection,stackBar,rankBars,spark,calendar,diverge,note,fmt,fmtK};
})(window);
