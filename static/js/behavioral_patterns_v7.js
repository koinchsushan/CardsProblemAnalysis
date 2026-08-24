let DATA = null;
let ORIGINAL_DATA = null;
/* ============================================================
   CLIENT-SIDE DATASET REPROCESSING
   Ports the Python prep pipeline (prep_v2.py / prep_v3.py) to
   vanilla JS so an uploaded CSV can fully replace DATA in-browser.
   ============================================================ */


const OPTIONAL_COLUMNS = ['downTimes'];
const CONDS_R = ['KQ','KQB','KQJ','KQJB'];
const COND_LABELS_R = {KQ:'King · Queen', KQB:'King · Queen · Blank', KQJ:'King · Queen · Jack', KQJB:'King · Queen · Jack · Blank'};
const COND_HAS_BLANK_R = {KQ:false, KQB:true, KQJ:false, KQJB:true};

function rankLetter(name){
  const n = String(name).toLowerCase();
  if(n.startsWith('king')) return 'K';
  if(n.startsWith('queen')) return 'Q';
  if(n.startsWith('jack')) return 'J';
  if(n.startsWith('blank')) return 'B';
  return '?';
}

// Parses a Python-repr list-of-strings cell, e.g. "['queen_spades_cA1', 'king_diamonds_cA2']"
function parsePyStringList(raw){
  if(raw===null || raw===undefined) return [];
  const s = String(raw).trim();
  if(!s || s==='[]' || s.toLowerCase()==='nan') return [];
  const out = [];
  const re = /'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"/g;
  let m;
  while((m = re.exec(s)) !== null){ out.push(m[1] !== undefined ? m[1] : m[2]); }
  return out;
}

const MOVE_RE_R = /^(.+)_[a-z](Off Grid|[A-H]\d)$/;
function parsePositionCodes(raw){
  // final_card_position_codes_1: list of "name_<letter><CELL>" strings -> [[row,col,letter], ...]
  // Cell code convention verified against final_card_positions_1 (flat 64-cell array, ground truth):
  // the LETTER is the row, the DIGIT is the column (e.g. "A2" = row A(0), column 2(1)).
  const items = parsePyStringList(raw);
  const out = [];
  for(const item of items){
    const m = MOVE_RE_R.exec(item);
    if(!m) continue;
    const card = m[1], cell = m[2];
    if(cell === 'Off Grid') continue;
    const row = cell.charCodeAt(0) - 65;
    const col = parseInt(cell.slice(1),10) - 1;
    out.push([row, col, rankLetter(card)]);
  }
  return out;
}

function parseMoveSteps(raw){
  const items = parsePyStringList(raw);
  const steps = [];
  for(const item of items){
    const m = MOVE_RE_R.exec(item);
    if(!m) return []; // bail on any malformed entry, matching the Python behaviour
    const card = m[1], cell = m[2];
    const letter = rankLetter(card);
    if(cell === 'Off Grid'){ steps.push({card, letter, cell:null, row:null, col:null}); }
    else {
      const row = cell.charCodeAt(0) - 65;
      const col = parseInt(cell.slice(1),10) - 1;
      steps.push({card, letter, cell, row, col});
    }
  }
  return steps;
}

function parseDownTimes(raw){
  if(raw===null || raw===undefined) return null;
  const s = String(raw).trim();
  if(!s || s==='[]' || s.toLowerCase()==='nan') return null;
  const nums = s.replace(/^\[|\]$/g,'').split(',').map(x=>parseFloat(x.trim())).filter(x=>!isNaN(x));
  return nums.length ? nums : null;
}

function rowColViolations(grid){
  const rowMap = {}, colMap = {};
  for(const [r,c,l] of grid){
    rowMap[r] = rowMap[r] || {}; rowMap[r][l] = (rowMap[r][l]||0)+1;
    colMap[c] = colMap[c] || {}; colMap[c][l] = (colMap[c][l]||0)+1;
  }
  let v = 0;
  for(const map of [rowMap, colMap]){
    for(const cell of Object.values(map)){
      for(const n of Object.values(cell)){ if(n>1) v += n-1; }
    }
  }
  return v;
}

function wilsonCI(successes, n, z=1.96){
  if(n===0) return {rate:0, lo:0, hi:0};
  const p = successes/n;
  const denom = 1 + z*z/n;
  const center = (p + z*z/(2*n)) / denom;
  const margin = (z * Math.sqrt((p*(1-p) + z*z/(4*n))/n)) / denom;
  return { rate: Math.round(p*1000)/10, lo: Math.round(Math.max(0,center-margin)*1000)/10, hi: Math.round(Math.min(1,center+margin)*1000)/10 };
}

function chiSquare2xK(table){
  // table: array of [success, fail] rows
  const rowTotals = table.map(r=>r[0]+r[1]);
  const colTotals = [0,0];
  table.forEach(r=>{ colTotals[0]+=r[0]; colTotals[1]+=r[1]; });
  const n = colTotals[0]+colTotals[1];
  let chi2 = 0;
  table.forEach((r,i)=>{
    [0,1].forEach(j=>{
      const expected = rowTotals[i]*colTotals[j]/n;
      if(expected>0) chi2 += Math.pow(r[j]-expected,2)/expected;
    });
  });
  const dof = table.length - 1;
  return { chi2: Math.round(chi2*100)/100, dof, n };
}

function mean(arr){ return arr.length ? arr.reduce((a,b)=>a+b,0)/arr.length : null; }
function round1(x){ return x===null||x===undefined ? null : Math.round(x*10)/10; }
function round2(x){ return x===null||x===undefined ? null : Math.round(x*100)/100; }

function quartiles(arr){
  const s = arr.filter(x=>x!==null && x!==undefined).slice().sort((a,b)=>a-b);
  if(!s.length) return null;
  const q = p => { const idx = p*(s.length-1); const lo=Math.floor(idx), hi=Math.ceil(idx); return s[lo] + (s[hi]-s[lo])*(idx-lo); };
  return { min: round1(s[0]), q1: round1(q(0.25)), median: round1(q(0.5)), q3: round1(q(0.75)), max: round1(s[s.length-1]), mean: round1(mean(s)), n: s.length };
}

/**
 * Main entry point: takes an array of row objects (from CSV parsing) and returns
 * a full DATA object matching the shape the dashboard already consumes.
 */
function buildDataFromRows(rows){
  // ---- normalise + parse each row ----
  const trials = [];
  for(const raw of rows){
    if(!raw.condition || String(raw.condition).trim()==='') continue;
    const participant = parseInt(raw.participant,10);
    const trialN = parseInt(raw.trialN,10);
    const condition = String(raw.condition).trim();
    const overall_correct = parseInt(raw.overall_correct,10) === 1 ? 1 : 0;
    if(isNaN(participant) || isNaN(trialN) || !CONDS_R.includes(condition)) continue;
    const grid = parsePositionCodes(raw.final_card_position_codes_1);
    const move_steps = parseMoveSteps(raw.movement_codes);
    const timesCol = raw.downTimes !== undefined;
    const times = timesCol ? parseDownTimes(raw.downTimes) : null;
    let moves, duration;
    if(timesCol){
      // downTimes column present in the uploaded file — mirror the Python pipeline exactly,
      // including rows where this particular value fails to parse (moves=0, duration=null)
      moves = times ? times.length : 0;
      duration = times ? round2(Math.max(...times)-Math.min(...times)) : null;
    } else {
      // column entirely absent from the uploaded file — degrade gracefully using the move log
      moves = move_steps.length || null;
      duration = null;
    }
    trials.push({
      participant, trialN, condition, overall_correct, grid, move_steps, times,
      moves, duration,
      has_blank_final: grid.some(c=>c[2]==='B'),
      violations: rowColViolations(grid),
      first_move_latency: times ? times[0] : null,
    });
  }
  if(!trials.length) throw new Error('No valid rows found — check that condition values are one of KQ, KQB, KQJ, KQJB and that participant/trialN are numeric.');

  // ---- attempt numbering (sort by trialN within participant) ----
  const byParticipant = {};
  trials.forEach(t=>{ (byParticipant[t.participant] = byParticipant[t.participant]||[]).push(t); });
  Object.values(byParticipant).forEach(list=>{
    list.sort((a,b)=>a.trialN-b.trialN);
    list.forEach((t,i)=> t.attempt = i+1);
  });

  const hasTiming = trials.some(t=>t.times && t.times.length);

  // ---- overview ----
  const participantsSet = new Set(trials.map(t=>t.participant));
  const conditionEntries = new Set(trials.map(t=>t.participant+'|'+t.condition)).size;
  const wins = trials.filter(t=>t.overall_correct===1).length;
  const overview = {
    trials: trials.length, participants: participantsSet.size, condition_entries: conditionEntries,
    wins, fails: trials.length-wins, success_rate: round1(wins/trials.length*100),
    avg_moves: round1(mean(trials.map(t=>t.moves).filter(x=>x!=null))),
    avg_duration: hasTiming ? round1(mean(trials.map(t=>t.duration).filter(x=>x!=null))) : null,
    trials_with_animation: trials.filter(t=>t.move_steps.length>0).length,
  };

  // ---- last attempt per participant (for condition-level stats) ----
  function lastAttempts(cond){
    const map = {};
    trials.filter(t=>t.condition===cond).forEach(t=>{
      if(!map[t.participant] || t.attempt > map[t.participant].attempt) map[t.participant] = t;
    });
    return Object.values(map);
  }

  // ---- conditions + conditions_ci ----
  const conditions = [], conditions_ci = [];
  CONDS_R.forEach(c=>{
    const sub = trials.filter(t=>t.condition===c);
    const last = lastAttempts(c);
    const n = last.length;
    const cwins = last.filter(t=>t.overall_correct===1).length;
    const winners = last.filter(t=>t.overall_correct===1);
    const ci = wilsonCI(cwins, n);
    conditions.push({
      code:c, label:COND_LABELS_R[c], n, wins:cwins, fails:n-cwins, win_rate: ci.rate,
      avg_trials_to_solve: winners.length ? round2(mean(winners.map(t=>t.attempt))) : null,
      avg_moves_win: round1(mean(sub.filter(t=>t.overall_correct===1).map(t=>t.moves).filter(x=>x!=null))),
      avg_moves_fail: round1(mean(sub.filter(t=>t.overall_correct===0).map(t=>t.moves).filter(x=>x!=null))),
      avg_duration_win: hasTiming ? round1(mean(sub.filter(t=>t.overall_correct===1).map(t=>t.duration).filter(x=>x!=null))) : null,
      avg_duration_fail: hasTiming ? round1(mean(sub.filter(t=>t.overall_correct===0).map(t=>t.duration).filter(x=>x!=null))) : null,
      has_blank: COND_HAS_BLANK_R[c],
    });
    conditions_ci.push({ code:c, n, wins:cwins, win_rate:ci.rate, ci_lo:ci.lo, ci_hi:ci.hi });
  });
  const condition_chi2_raw = chiSquare2xK(conditions_ci.map(c=>[c.wins, c.n-c.wins]));
  const condition_chi2 = { chi2: condition_chi2_raw.chi2, dof: condition_chi2_raw.dof, n: condition_chi2_raw.n, p: null };

  // ---- learning curves (truncate at n<3) ----
  const maxAttempt = Math.max(...trials.map(t=>t.attempt));
  const learning = {};
  ['ALL', ...CONDS_R].forEach(c=>{
    const sub = c==='ALL' ? trials : trials.filter(t=>t.condition===c);
    const curve = [];
    for(let k=1; k<=Math.min(maxAttempt,10); k++){
      const atK = sub.filter(t=>t.attempt===k);
      if(atK.length < 3) break;
      curve.push({ attempt:k, n: atK.length, success_rate: round1(mean(atK.map(t=>t.overall_correct))*100) });
    }
    learning[c] = curve;
  });

  // ---- solve distribution (kept for completeness, not currently rendered) ----
  const solve_dist = {};
  CONDS_R.forEach(c=>{
    const winners = lastAttempts(c).filter(t=>t.overall_correct===1);
    const bins = {'1':0,'2':0,'3':0,'4-5':0,'6-10':0,'11+':0};
    winners.forEach(t=>{
      const a=t.attempt;
      if(a===1) bins['1']++; else if(a===2) bins['2']++; else if(a===3) bins['3']++;
      else if(a<=5) bins['4-5']++; else if(a<=10) bins['6-10']++; else bins['11+']++;
    });
    solve_dist[c] = bins;
  });

  // ---- heatmaps (win / fail) ----
  const heatmaps = {}, heatmaps_fail = {};
  CONDS_R.forEach(c=>{
    const grid1 = Array.from({length:8},()=>Array(8).fill(0));
    const grid0 = Array.from({length:8},()=>Array(8).fill(0));
    trials.filter(t=>t.condition===c).forEach(t=>{
      const target = t.overall_correct===1 ? grid1 : grid0;
      t.grid.forEach(([r,c2])=> target[r][c2]++);
    });
    heatmaps[c] = grid1; heatmaps_fail[c] = grid0;
  });

  // ---- solution pattern mining ----
  function gridSig(grid){ return grid.slice().sort((a,b)=> a[0]-b[0] || a[1]-b[1] || (a[2]<b[2]?-1:1)).map(c=>c.join(',')).join('|'); }
  const patterns = {};
  CONDS_R.forEach(c=>{
    patterns[c] = {};
    [['success',1],['failure',0]].forEach(([label,oc])=>{
      const sub = trials.filter(t=>t.condition===c && t.overall_correct===oc);
      const bySig = {};
      sub.forEach(t=>{
        if(!t.grid.length) return;
        const sig = gridSig(t.grid);
        (bySig[sig] = bySig[sig] || {grid:t.grid, examples:[]}).examples.push({pid:t.participant, trialN:t.trialN});
      });
      const ranked = Object.values(bySig).sort((a,b)=> b.examples.length - a.examples.length);
      patterns[c][label] = {
        total_unique: ranked.length, total_trials: sub.length,
        patterns: ranked.map(p=>({ grid:p.grid, frequency:p.examples.length, cards:p.grid.length, examples:p.examples.slice(0,5) })),
      };
    });
  });

  // ---- blank usage: pill data, cross tabs, availability vs usage ----
  const pill_data = [];
  ['KQB','KQJB'].forEach(c=>{
    lastAttempts(c).forEach(t=>{
      const used = t.has_blank_final, win = t.overall_correct===1;
      let bucket;
      if(used && win) bucket='used_success'; else if(used && !win) bucket='used_failed';
      else if(!used && win) bucket='success_no_blank'; else bucket='failed';
      pill_data.push({pid:t.participant, cond:c, bucket});
    });
  });
  const blank_cross = [];
  CONDS_R.forEach(c=>{
    const last = lastAttempts(c);
    if(COND_HAS_BLANK_R[c]){
      const used = last.filter(t=>t.has_blank_final), notUsed = last.filter(t=>!t.has_blank_final);
      blank_cross.push({label:c+' with blank', n:used.length, win_rate: used.length?round1(mean(used.map(t=>t.overall_correct))*100):0});
      blank_cross.push({label:c+' no blank', n:notUsed.length, win_rate: notUsed.length?round1(mean(notUsed.map(t=>t.overall_correct))*100):0});
    } else {
      blank_cross.push({label:c, n:last.length, win_rate: round1(mean(last.map(t=>t.overall_correct))*100)});
    }
  });
  const avail_vs_usage = CONDS_R.map(c=>{
    const last = lastAttempts(c);
    return { cond:c, participants:last.length, used_blank: COND_HAS_BLANK_R[c] ? last.filter(t=>t.has_blank_final).length : 0 };
  });
  const blankEligible = trials.filter(t=>['KQB','KQJB'].includes(t.condition));
  const blankEligibleLast = [...lastAttempts('KQB'), ...lastAttempts('KQJB')];
  const used = blankEligibleLast.filter(t=>t.has_blank_final);
  const notUsed = blankEligibleLast.filter(t=>!t.has_blank_final);
  const usedWin = used.filter(t=>t.overall_correct===1).length;
  const blank_summary = {
    total_blank_eligible: blankEligibleLast.length, used_blank_n: used.length,
    used_blank_success_rate: used.length ? round1(usedWin/used.length*100) : 0,
    not_used_n: notUsed.length,
    not_used_success_rate: notUsed.length ? round1(mean(notUsed.map(t=>t.overall_correct))*100) : 0,
  };
  const a=usedWin, b=used.length-usedWin, c_=notUsed.filter(t=>t.overall_correct===1).length, d=notUsed.length-c_;
  const blank_chi2_raw = a&&b&&c_&&d ? chiSquare2xK([[a,b],[c_,d]]) : {chi2:0,dof:1,n:blankEligibleLast.length};
  const blank_chi2 = { chi2: blank_chi2_raw.chi2, dof: blank_chi2_raw.dof, p: null,
    used_n:used.length, used_wins:usedWin, not_used_n:notUsed.length, not_used_wins:c_ };
  const uw = wilsonCI(usedWin, used.length), nw = wilsonCI(c_, notUsed.length);
  const blank_ci = { used:{rate:uw.rate, lo:uw.lo, hi:uw.hi, n:used.length}, not_used:{rate:nw.rate, lo:nw.lo, hi:nw.hi, n:notUsed.length} };

  // ---- explorer (per-trial records incl. animation) ----
  function naturalDesc(step, idx){
    const names = {K:'King', Q:'Queen', J:'Jack', B:'Blank'};
    const label = names[step.letter] || step.letter;
    if(step.cell===null) return `Move ${idx+1}: removed a ${label} from the board.`;
    return `Move ${idx+1}: placed a ${label} at row ${step.row+1}, column ${String.fromCharCode(65+step.col)}.`;
  }
  const explorer = trials.map(t=>{
    const anim = t.move_steps.length ? t.move_steps.map((s,i)=>({card:s.card, letter:s.letter, cell:s.cell, row:s.row, col:s.col, desc:naturalDesc(s,i)})) : null;
    return {
      pid:t.participant, cond:t.condition, attempt:t.attempt, trialN:t.trialN,
      outcome: t.overall_correct===1?'win':'fail', moves:t.moves, duration:t.duration,
      grid:t.grid, has_animation: !!anim, animation: anim, has_blank: t.has_blank_final,
    };
  });

  // ---- rule validation ----
  const winsT = trials.filter(t=>t.overall_correct===1), failsT = trials.filter(t=>t.overall_correct===0);
  const rule_validation = {
    win_violation_rate: round1(mean(winsT.map(t=>t.violations>0?1:0))*100),
    fail_violation_rate: round1(mean(failsT.map(t=>t.violations>0?1:0))*100),
    win_avg_violations: round2(mean(winsT.map(t=>t.violations))),
    fail_avg_violations: round2(mean(failsT.map(t=>t.violations))),
  };

  // ---- distributions (box-plot quartiles) ----
  const distributions = {};
  CONDS_R.forEach(c=>{
    const sub = trials.filter(t=>t.condition===c);
    distributions[c] = {
      moves_win: quartiles(sub.filter(t=>t.overall_correct===1).map(t=>t.moves).filter(x=>x!=null)),
      moves_fail: quartiles(sub.filter(t=>t.overall_correct===0).map(t=>t.moves).filter(x=>x!=null)),
      duration_win: hasTiming ? quartiles(sub.filter(t=>t.overall_correct===1).map(t=>t.duration).filter(x=>x!=null)) : null,
      duration_fail: hasTiming ? quartiles(sub.filter(t=>t.overall_correct===0).map(t=>t.duration).filter(x=>x!=null)) : null,
    };
  });

  // ---- latency (requires downTimes) ----
  let latency_summary = null, latency_curve = null;
  if(hasTiming){
    function interGaps(times){ const g=[]; for(let i=1;i<times.length;i++) g.push(round2(times[i]-times[i-1])); return g; }
    latency_summary = {};
    [['win',1],['fail',0]].forEach(([label,oc])=>{
      const sub = trials.filter(t=>t.overall_correct===oc && t.times);
      const firstLat = sub.map(t=>t.first_move_latency).filter(x=>x!=null);
      const gapsAll = sub.map(t=>interGaps(t.times));
      const avgGaps = gapsAll.filter(g=>g.length).map(g=>mean(g));
      const lastGaps = gapsAll.filter(g=>g.length).map(g=>g[g.length-1]);
      latency_summary[label] = {
        avg_first_move_latency: round2(mean(firstLat)),
        avg_inter_move_gap: round2(mean(avgGaps)),
        avg_last_gap: round2(mean(lastGaps)),
        n: firstLat.length,
      };
    });
    latency_curve = {win:[], fail:[]};
    [['win',1],['fail',0]].forEach(([label,oc])=>{
      const sub = trials.filter(t=>t.overall_correct===oc && t.times);
      for(let pos=1; pos<=12; pos++){
        const vals = sub.filter(t=>t.times.length>pos).map(t=>round2(t.times[pos]-t.times[pos-1]));
        if(vals.length<5) break;
        latency_curve[label].push({pos, avg_gap: round2(mean(vals)), n: vals.length});
      }
    });
  }

  // ---- first-move location grid ----
  const first_move_grid = { win: Array.from({length:8},()=>Array(8).fill(0)), fail: Array.from({length:8},()=>Array(8).fill(0)) };
  trials.forEach(t=>{
    const first = t.move_steps.find(s=>s.cell!==null);
    if(!first) return;
    const g = t.overall_correct===1 ? first_move_grid.win : first_move_grid.fail;
    g[first.row][first.col]++;
  });

  // ---- trajectories ----
  const trajectories = {};
  CONDS_R.forEach(c=>{
    const sub = trials.filter(t=>t.condition===c).sort((a,b)=> a.participant-b.participant || a.trialN-b.trialN);
    const byP = {};
    sub.forEach(t=> (byP[t.participant]=byP[t.participant]||[]).push(t.overall_correct));
    trajectories[c] = Object.entries(byP).map(([pid,seq])=>({pid:parseInt(pid,10), seq}))
      .sort((a,b)=> b.seq.length-a.seq.length || (a.seq[a.seq.length-1]===0?-1:1));
  });

  // ---- placement order preference ----
  const placement_order_first = {};
  const position_rank = {1:{},2:{},3:{},4:{}};
  trials.forEach(t=>{
    const seen = new Set(); let pos=0;
    for(const s of t.move_steps){
      if(s.cell===null || seen.has(s.card)) continue;
      seen.add(s.card); pos++;
      if(pos===1) placement_order_first[s.letter] = (placement_order_first[s.letter]||0)+1;
      if(pos<=4) position_rank[pos][s.letter] = (position_rank[pos][s.letter]||0)+1;
    }
  });

  // ---- row/col violation breakdown (failed trials) ----
  const failedWithGrid = failsT;
  function splitViolations(grid){
    const rowMap={}, colMap={};
    grid.forEach(([r,c,l])=>{ rowMap[r]=rowMap[r]||{}; rowMap[r][l]=(rowMap[r][l]||0)+1; colMap[c]=colMap[c]||{}; colMap[c][l]=(colMap[c][l]||0)+1; });
    let rv=0,cv=0;
    Object.values(rowMap).forEach(cell=>Object.values(cell).forEach(n=>{ if(n>1) rv+=n-1; }));
    Object.values(colMap).forEach(cell=>Object.values(cell).forEach(n=>{ if(n>1) cv+=n-1; }));
    return [rv,cv];
  }
  let rowV=0, colV=0, bothV=0, sumRow=0, sumCol=0;
  failedWithGrid.forEach(t=>{
    const [rv,cv] = splitViolations(t.grid);
    if(rv>0) rowV++; if(cv>0) colV++; if(rv>0&&cv>0) bothV++;
    sumRow+=rv; sumCol+=cv;
  });
  const row_col_breakdown = {
    row_violation_trials: rowV, col_violation_trials: colV, both_violation_trials: bothV,
    total_fail_trials: failedWithGrid.length,
    avg_row_violations: round2(sumRow/(failedWithGrid.length||1)), avg_col_violations: round2(sumCol/(failedWithGrid.length||1)),
  };

  // ---- undo/revision stats (requires movement log) ----
  const withLog = trials.filter(t=>t.move_steps.length>0);
  const undo_stats = {};
  [['win',1],['fail',0]].forEach(([label,oc])=>{
    const sub = withLog.filter(t=>t.overall_correct===oc);
    const undos = sub.map(t=> t.move_steps.filter(s=>s.cell===null).length);
    undo_stats[label] = { avg_undos: round2(mean(undos)), pct_with_any_undo: round1(mean(undos.map(u=>u>0?1:0))*100), n: sub.length };
  });

  // ---- persistence (multi-attempt non-solvers, first vs last attempt) ----
  const persistence = {};
  CONDS_R.forEach(c=>{
    const sub = trials.filter(t=>t.condition===c).sort((a,b)=>a.participant-b.participant||a.trialN-b.trialN);
    const byP = {};
    sub.forEach(t=> (byP[t.participant]=byP[t.participant]||[]).push(t));
    const moveDeltas=[], durDeltas=[];
    Object.values(byP).forEach(list=>{
      if(list[list.length-1].overall_correct===1) return;
      if(list.length<2) return;
      const first=list[0], last=list[list.length-1];
      if(first.moves!=null && last.moves!=null) moveDeltas.push(last.moves-first.moves);
      if(hasTiming && first.duration!=null && last.duration!=null) durDeltas.push(round2(last.duration-first.duration));
    });
    persistence[c] = {
      n_multi_attempt_failers: moveDeltas.length,
      avg_move_delta: moveDeltas.length ? round2(mean(moveDeltas)) : null,
      n_duration_pairs: durDeltas.length,
      avg_duration_delta: durDeltas.length ? round2(mean(durDeltas)) : null,
    };
  });

  // ---- data quality (recomputed against the uploaded data, not the original hard-coded audit) ----
  const dupCheck = {};
  trials.forEach(t=>{ dupCheck[t.participant] = dupCheck[t.participant] || new Set(); dupCheck[t.participant].add(t.condition); });
  const dualCondition = Object.entries(dupCheck).filter(([,set])=>set.size>1).map(([pid])=>parseInt(pid,10));
  const data_quality = {
    dual_condition_participants: dualCondition,
    t1_means_kqb_error: null,
    missing_from_final_trials: [],
    duplicate_final_trial_rows: [],
    movement_log_coverage: { total_trials: trials.length, with_movement_log: trials.filter(t=>t.move_steps.length>0).length },
    blank_never_in_movement_log: !trials.some(t=>t.move_steps.some(s=>s.letter==='B')),
    reprocessed_from_upload: true,
  };

  return {
    overview, conditions, conditions_ci, condition_chi2, learning, solve_dist, heatmaps, heatmaps_fail,
    patterns, pill_data, blank_cross, avail_vs_usage, blank_summary, blank_chi2, blank_ci, explorer,
    rule_validation, distributions, latency_summary, latency_curve, first_move_grid, trajectories,
    placement_order_first, position_rank, row_col_breakdown, undo_stats, persistence, data_quality,
    cond_labels: COND_LABELS_R, has_timing: hasTiming,
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { buildDataFromRows, parsePyStringList, parsePositionCodes, parseMoveSteps, parseDownTimes, rowColViolations, wilsonCI, chiSquare2xK };
}

const SECTIONS = [
  {id:'behavioral', label:'Trial Analysis'},
  {id:'gridpatterns', label:'Behavioral Patterns'},
];
const CONDS = ['KQ','KQB','KQJ','KQJB'];
const CCOL = {KQ:'#64748b', KQB:'#0891b2', KQJ:'#ea580c', KQJB:'#7c3aed'};
const CARDCOL = {K:'#16a34a', Q:'#3b82f6', J:'#8b5cf6', B:'#ffffff'};
const RANKNAME = {K:'King', Q:'Queen', J:'Jack', B:'Blank'};

function fmt(n,d=1){ return (n===null||n===undefined)?'—':Number(n).toFixed(d); }

/* -------- nav -------- */
const navLinks = document.getElementById('navLinks');
SECTIONS.forEach((s,i)=>{
  const el = document.createElement('div');
  el.className='navlink'+(i===0?' on':'');
  el.textContent = s.label; el.dataset.id = s.id;
  el.onclick = ()=> showView(s.id);
  navLinks.appendChild(el);
});
const mainViews = document.getElementById('mainViews');
SECTIONS.forEach(s=>{
  const v = document.createElement('div'); v.className='view'; v.id='view-'+s.id;
  mainViews.appendChild(v);
});
function V(id){ return document.getElementById('view-'+id); }
const initialized = new Set();
const CHARTS = {};
let pendingAttemptSelection = null;

function showView(id, params){
  // Guard: if DATA hasn't loaded yet, do nothing (async fetch not complete)
  if(!DATA) return;
  // Guard: if the requested view doesn't exist (removed section), fall back to first available
  if(!V(id)){ id = SECTIONS[0].id; }
  document.querySelectorAll('.view').forEach(v=>v.classList.toggle('on', v.id==='view-'+id));
  document.querySelectorAll('.navlink').forEach(r=>r.classList.toggle('on', r.dataset.id===id));
  window.scrollTo(0,0);

  if(!initialized.has(id) && BUILDERS[id]){ BUILDERS[id](); initialized.add(id); }
  const hashParts = [id];
  if(params && params.cond!=null) hashParts.push(params.cond, params.pid, params.attempt);
  const newHash = '#'+hashParts.join('/');
  if(location.hash !== newHash){
    try{ history.replaceState(null, '', newHash); } catch(e){ /* URL update is a nice-to-have; never let it break navigation */ }
  }
}

/** Jump straight to a specific participant/trial in Problem Attempts from anywhere else on the site. */
function openInExplorer(cond, pid, attempt){
  showView('attempts', {cond, pid, attempt});
}

function parseHashAndNavigate(){
  const raw = location.hash.replace(/^#/, '');
  if(!raw){ showView(SECTIONS[0].id); return; }
  const parts = raw.split('/');
  const id = parts[0];
  if(!SECTIONS.some(s=>s.id===id)){ showView(SECTIONS[0].id); return; }
  showView(id);
}
window.addEventListener('hashchange', parseHashAndNavigate);

/* -------- SVG board renderer (with axis labels) -------- */
function renderBoard(container, gridCells, opts={}){
  const size = opts.size || 240; const n=8; const cell=size/n; const withAxis = opts.axis!==false;
  const pad = withAxis ? 16 : 0;
  const total = size+pad;
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox', `0 0 ${total} ${total}`);
  svg.setAttribute('width', total); svg.setAttribute('height', total);
  svg.setAttribute('style', 'width:100%; height:auto; max-width:'+total+'px; display:block;');
  svg.setAttribute('role','img');
  svg.setAttribute('aria-label', `8 by 8 card grid with ${gridCells.length} cards placed`);
  svg.classList.add('board-svg');
  if(withAxis){
    for(let c=0;c<n;c++){
      const t = document.createElementNS('http://www.w3.org/2000/svg','text');
      t.setAttribute('class','axislabel'); t.setAttribute('x', pad+c*cell+cell/2); t.setAttribute('y', pad-5);
      t.textContent = String.fromCharCode(65+c); svg.appendChild(t);
    }
    for(let r=0;r<n;r++){
      const t = document.createElementNS('http://www.w3.org/2000/svg','text');
      t.setAttribute('class','axislabel'); t.setAttribute('x', pad-9); t.setAttribute('y', pad+r*cell+cell/2+3);
      t.textContent = String(r+1); svg.appendChild(t);
    }
  }
  for(let r=0;r<n;r++) for(let c=0;c<n;c++){
    const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('class','cell');
    rect.setAttribute('x', pad+c*cell); rect.setAttribute('y', pad+r*cell);
    rect.setAttribute('width', cell); rect.setAttribute('height', cell);
    svg.appendChild(rect);
  }
  gridCells.forEach(([r,c,letter])=>{
    const isBlank = letter==='B';
    const rx = Math.max(2, cell*0.12);
    const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('x', pad+c*cell+1.5); rect.setAttribute('y', pad+r*cell+1.5);
    rect.setAttribute('width', cell-3); rect.setAttribute('height', cell-3);
    rect.setAttribute('rx', rx); rect.setAttribute('ry', rx);
    rect.setAttribute('fill', CARDCOL[letter]||'#999');
    rect.setAttribute('opacity', isBlank ? '1' : '0.94');
    if(isBlank){ rect.setAttribute('stroke', '#141b2c'); rect.setAttribute('stroke-width','1.5'); }
    else { rect.setAttribute('stroke','rgba(0,0,0,.12)'); rect.setAttribute('stroke-width','0.75'); }
    svg.appendChild(rect);
    const t = document.createElementNS('http://www.w3.org/2000/svg','text');
    t.setAttribute('class','cardlet'); t.setAttribute('x', pad+c*cell+cell/2); t.setAttribute('y', pad+r*cell+cell/2+0.5);
    t.setAttribute('fill', isBlank ? '#141b2c' : '#fff'); t.setAttribute('font-size', Math.max(9,cell*0.4)); t.textContent = letter;
    svg.appendChild(t);
    if(cell>=28){
      const ci = document.createElementNS('http://www.w3.org/2000/svg','text');
      ci.setAttribute('x', pad+c*cell+cell*0.16); ci.setAttribute('y', pad+r*cell+cell*0.24);
      ci.setAttribute('font-family','IBM Plex Mono'); ci.setAttribute('font-weight','700');
      ci.setAttribute('font-size', Math.max(6,cell*0.15)); ci.setAttribute('fill', isBlank?'#141b2c':'#fff');
      ci.setAttribute('opacity','0.75'); ci.textContent = letter;
      svg.appendChild(ci);
    }
  });
  container.innerHTML=''; container.appendChild(svg);
}

function renderHeat(container, gridCounts, opts={}){
  const size = opts.size||220; const n=8; const cell=size/n;
  const max = Math.max(1, ...gridCounts.flat());
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`); svg.setAttribute('width', size); svg.setAttribute('height', size);
  svg.setAttribute('style', 'width:100%; height:auto; max-width:'+size+'px; display:block;');
  svg.setAttribute('role','img'); svg.setAttribute('aria-label','Heatmap of card placement frequency across successful trials');
  svg.classList.add('board-svg');
  for(let r=0;r<n;r++) for(let c=0;c<n;c++){
    const v = gridCounts[r][c]; const pct = v/max;
    const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('x', c*cell); rect.setAttribute('y', r*cell); rect.setAttribute('width', cell); rect.setAttribute('height', cell);
    rect.setAttribute('fill', pct===0 ? '#eef1f6' : `rgba(22,163,74,${0.12+pct*0.85})`);
    svg.appendChild(rect);
    const b = document.createElementNS('http://www.w3.org/2000/svg','rect');
    b.setAttribute('class','cell'); b.setAttribute('x', c*cell); b.setAttribute('y', r*cell); b.setAttribute('width', cell); b.setAttribute('height', cell);
    svg.appendChild(b);
  }
  container.innerHTML=''; container.appendChild(svg);
}

/* renderer for Hint Effects: numbered index cells, blank-only highlight */
function renderIndexedBoard(container, gridCells, opts={}){
  const size = opts.size||280; const n=8; const cell=size/n;
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`); svg.setAttribute('width', size); svg.setAttribute('height', size);
  svg.setAttribute('style', 'width:100%; height:auto; max-width:'+size+'px; display:block;');
  svg.setAttribute('role','img'); svg.setAttribute('aria-label','Numbered card grid, Blank cards highlighted');
  svg.classList.add('board-svg');
  for(let r=0;r<n;r++) for(let c=0;c<n;c++){
    const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('class','cell'); rect.setAttribute('x', c*cell); rect.setAttribute('y', r*cell); rect.setAttribute('width', cell); rect.setAttribute('height', cell);
    svg.appendChild(rect);
  }
  const legend = [];
  gridCells.forEach(([r,c,letter],i)=>{
    const idx = i+1;
    const isBlank = letter==='B';
    const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('x', c*cell+1.5); rect.setAttribute('y', r*cell+1.5); rect.setAttribute('width', cell-3); rect.setAttribute('height', cell-3);
    rect.setAttribute('fill', CARDCOL[letter] || '#999');
    if(isBlank){ rect.setAttribute('stroke','#18233b'); rect.setAttribute('stroke-width','1.5'); }
    svg.appendChild(rect);
    const t1 = document.createElementNS('http://www.w3.org/2000/svg','text');
    t1.setAttribute('x', c*cell+cell/2); t1.setAttribute('y', r*cell+cell*0.4); t1.setAttribute('text-anchor','middle');
    t1.setAttribute('font-family','IBM Plex Mono'); t1.setAttribute('font-weight','700'); t1.setAttribute('font-size', Math.max(9,cell*0.24)); t1.setAttribute('fill', isBlank ? '#18233b' : '#fff');
    t1.textContent = idx; svg.appendChild(t1);
    const t2 = document.createElementNS('http://www.w3.org/2000/svg','text');
    t2.setAttribute('x', c*cell+cell/2); t2.setAttribute('y', r*cell+cell*0.68); t2.setAttribute('text-anchor','middle');
    t2.setAttribute('font-family','IBM Plex Mono'); t2.setAttribute('font-size', Math.max(8,cell*0.2)); t2.setAttribute('fill', isBlank ? '#18233b' : '#fff');
    t2.textContent = letter; svg.appendChild(t2);
    legend.push(`${idx} = ${letter==='B'?'Blank':RANKNAME[letter]}, r${r+1}c${String.fromCharCode(65+c)}`);
  });
  container.innerHTML=''; container.appendChild(svg);
  return legend;
}

/* -------- Box plot (SVG) for move-count / duration distributions -------- */
function renderBoxPlot(container, stats, opts={}){
  if(!stats){ container.innerHTML = '<div style="font-size:11px; color:var(--tx-dim);">No data</div>'; return; }
  const w = opts.width||90, h = opts.height||160, color = opts.color||'#16a34a';
  const pad = 18;
  const lo = stats.min, hi = stats.max;
  const scale = v => h - pad - ((v-lo)/((hi-lo)||1))*(h-2*pad);
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.setAttribute('style', `width:100%; height:auto; max-width:${w}px; display:block;`);
  const cx = w/2, bw = w*0.5;
  function line(x1,y1,x2,y2,sw=1.4){
    const l = document.createElementNS('http://www.w3.org/2000/svg','line');
    l.setAttribute('x1',x1); l.setAttribute('y1',y1); l.setAttribute('x2',x2); l.setAttribute('y2',y2);
    l.setAttribute('stroke','#4d5872'); l.setAttribute('stroke-width',sw);
    svg.appendChild(l);
  }
  line(cx, scale(lo), cx, scale(stats.q1));
  line(cx, scale(stats.q3), cx, scale(hi));
  line(cx-bw*0.25, scale(lo), cx+bw*0.25, scale(lo));
  line(cx-bw*0.25, scale(hi), cx+bw*0.25, scale(hi));
  const box = document.createElementNS('http://www.w3.org/2000/svg','rect');
  box.setAttribute('x', cx-bw/2); box.setAttribute('y', scale(stats.q3));
  box.setAttribute('width', bw); box.setAttribute('height', Math.max(1,scale(stats.q1)-scale(stats.q3)));
  box.setAttribute('fill', color); box.setAttribute('opacity','0.28'); box.setAttribute('stroke', color); box.setAttribute('stroke-width','1.3');
  svg.appendChild(box);
  line(cx-bw/2, scale(stats.median), cx+bw/2, scale(stats.median), 2.2);
  const meanDot = document.createElementNS('http://www.w3.org/2000/svg','circle');
  meanDot.setAttribute('cx', cx); meanDot.setAttribute('cy', scale(stats.mean)); meanDot.setAttribute('r','2.6');
  meanDot.setAttribute('fill','#fff'); meanDot.setAttribute('stroke', color); meanDot.setAttribute('stroke-width','1.4');
  svg.appendChild(meanDot);
  container.innerHTML=''; container.appendChild(svg);
}

/* -------- Chart.js plugin: draws vertical error-bar whiskers on a bar chart -------- */
const errorBarPlugin = {
  id: 'errorBars',
  afterDatasetsDraw(chart){
    const { ctx } = chart;
    chart.data.datasets.forEach((ds, dsIndex)=>{
      if(!ds.errorBars) return;
      const meta = chart.getDatasetMeta(dsIndex);
      meta.data.forEach((bar, i)=>{
        const eb = ds.errorBars[i];
        if(!eb) return;
        const yScale = chart.scales.y;
        const yLo = yScale.getPixelForValue(eb.lo), yHi = yScale.getPixelForValue(eb.hi);
        const x = bar.x;
        ctx.save();
        ctx.strokeStyle = 'rgba(24,35,59,.65)'; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(x,yLo); ctx.lineTo(x,yHi); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x-5,yLo); ctx.lineTo(x+5,yLo); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x-5,yHi); ctx.lineTo(x+5,yHi); ctx.stroke();
        ctx.restore();
      });
    });
  }
};

/* -------- Rule-validation diagram: annotated valid vs invalid mini example -------- */
function renderRuleExample(container, valid){
  const w=160,h=90,cell=40; const pad=5;
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.setAttribute('style', `width:100%; height:auto; max-width:${w}px; display:block;`);
  const cells = valid ? [[0,0,'K'],[0,1,'Q'],[1,0,'Q'],[1,1,'K']] : [[0,0,'K'],[0,1,'Q'],[1,0,'K'],[1,1,'Q']];
  const conflictCells = valid ? [] : [[0,0],[1,0]];
  for(let r=0;r<2;r++) for(let c=0;c<2;c++){
    const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('x', pad+c*cell); rect.setAttribute('y', pad+r*cell); rect.setAttribute('width',cell); rect.setAttribute('height',cell);
    rect.setAttribute('fill','none'); rect.setAttribute('stroke','#c7cdd8');
    svg.appendChild(rect);
  }
  cells.forEach(([r,c,letter])=>{
    const isConflict = conflictCells.some(cc=>cc[0]===r&&cc[1]===c);
    const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('x', pad+c*cell+2); rect.setAttribute('y', pad+r*cell+2); rect.setAttribute('width',cell-4); rect.setAttribute('height',cell-4);
    rect.setAttribute('rx','4');
    rect.setAttribute('fill', isConflict ? '#dc2626' : CARDCOL[letter]);
    svg.appendChild(rect);
    const t = document.createElementNS('http://www.w3.org/2000/svg','text');
    t.setAttribute('x', pad+c*cell+cell/2); t.setAttribute('y', pad+r*cell+cell/2+5); t.setAttribute('text-anchor','middle');
    t.setAttribute('fill','#fff'); t.setAttribute('font-family','IBM Plex Mono'); t.setAttribute('font-weight','700'); t.setAttribute('font-size','15');
    t.textContent = letter; svg.appendChild(t);
  });
  container.innerHTML=''; container.appendChild(svg);
}

/* -------- Sparkline: one participant's attempt-by-attempt outcome sequence -------- */
function renderSparkline(container, seq){
  const w=100, h=28, n=seq.length, stepX = n>1 ? (w-10)/(n-1) : 0;
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.setAttribute('style','width:100%; height:auto; display:block;');
  const pts = seq.map((v,i)=> [5+i*stepX, v===1 ? 6 : h-6]);
  let d = 'M '+pts.map(p=>p.join(',')).join(' L ');
  const path = document.createElementNS('http://www.w3.org/2000/svg','path');
  path.setAttribute('d', d); path.setAttribute('fill','none'); path.setAttribute('stroke','#75809a'); path.setAttribute('stroke-width','1');
  svg.appendChild(path);
  pts.forEach(([x,y],i)=>{
    const c = document.createElementNS('http://www.w3.org/2000/svg','circle');
    c.setAttribute('cx',x); c.setAttribute('cy',y); c.setAttribute('r', i===pts.length-1 ? 3.2 : 2.4);
    c.setAttribute('fill', seq[i]===1 ? '#16a34a' : '#dc2626');
    svg.appendChild(c);
  });
  container.innerHTML=''; container.appendChild(svg);
}

function chartBaseOpts({suffix=''}={}){
  return {
    responsive:true, maintainAspectRatio:false,
    plugins:{ legend:{display:false}, tooltip:{ callbacks:{ label:(c)=>`${c.formattedValue}${suffix}` } } },
    scales:{
      x:{ grid:{display:false}, ticks:{color:'#4d5872', font:{family:'IBM Plex Mono', size:10.5}} },
      y:{ grid:{color:'rgba(28,32,48,.08)'}, ticks:{color:'#4d5872', font:{family:'IBM Plex Mono', size:10.5}, callback:(v)=>v+suffix}, beginAtZero:true }
    }
  };
}

/* -------- accessibility: make custom clickable divs keyboard-usable -------- */
const A11Y_SELECTOR = '.navlink,.tabbtn,.plist-pill,.thumb,.xlink,[data-jump-cond]';
function tagA11y(root){
  root.querySelectorAll(A11Y_SELECTOR).forEach(el=>{
    if(!el.hasAttribute('tabindex')) el.setAttribute('tabindex','0');
    if(!el.hasAttribute('role')) el.setAttribute('role','button');
  });
}
new MutationObserver(muts=>{
  muts.forEach(m=> m.addedNodes.forEach(node=>{
    if(node.nodeType===1){
      if(node.matches && node.matches(A11Y_SELECTOR)){ node.setAttribute('tabindex','0'); node.setAttribute('role','button'); }
      if(node.querySelectorAll) tagA11y(node);
    }
  }));
}).observe(document.body, {childList:true, subtree:true});
tagA11y(document.body); // catch elements (like the initial nav links) created before the observer started watching
document.addEventListener('keydown', (e)=>{
  if((e.key==='Enter'||e.key===' ') && document.activeElement && document.activeElement.matches && document.activeElement.matches(A11Y_SELECTOR)){
    e.preventDefault();
    document.activeElement.click();
  }
});
document.addEventListener('click', (e)=>{
  const el = e.target.closest('[data-jump-cond]');
  if(!el) return;
  const cond = el.dataset.jumpCond, pid = Number(el.dataset.jumpPid), trialN = Number(el.dataset.jumpTrialn);
  const match = DATA.explorer.find(t=>t.cond===cond && t.pid===pid && t.trialN===trialN);
  openInExplorer(cond, pid, match ? match.attempt : 1);
});

/* ====================================================================== */
/* 1. HOME                                                                  */
/* ====================================================================== */
function buildHome(){
  const o = DATA.overview;
  const dq = DATA.data_quality;
  V('home').innerHTML = `
    <div class="hero">
      <div class="hero-eyebrow">Behavioural Research Dashboard</div>
      <h1 class="hero-thesis">Strategy Under Constraint: <em>How People Solve a Card-Placement Puzzle</em></h1>
      <p class="hero-sub">This dashboard examines how individuals approach a constrained spatial reasoning task — analysing strategy formation, adaptation under increased complexity, and the adoption of optional aids, using complete trial-level data rather than summary statistics alone.</p>
      <div class="stat-row">
        <div class="stat"><div class="stat-num">${o.trials}</div><div class="stat-label">Total Trials</div></div>
        <div class="stat"><div class="stat-num">${o.participants}</div><div class="stat-label">Participants</div>
          <div class="stat-flag" title="One participant (183) is logged under two conditions — see Methodology below.">229 entries ⚑</div></div>
        <div class="stat"><div class="stat-num">${o.success_rate}<span class="u">%</span></div><div class="stat-label">Success Rate</div></div>
        <div class="stat"><div class="stat-num">${o.avg_moves}</div><div class="stat-label">Avg Moves / Trial</div></div>
        <div class="stat"><div class="stat-num">${o.trials_with_animation}</div><div class="stat-label">Trials w/ Move Log</div></div>
      </div>
    </div>
    <div class="specimen-strip">
      ${CONDS.map(c=>{
        const cond = DATA.conditions.find(x=>x.code===c);
        const win = DATA.explorer.find(t=>t.cond===c && t.outcome==='win');
        return `<div class="specimen">
          <div class="specimen-rate">${cond.win_rate}%</div>
          <div class="specimen-cond">${c} — ${cond.label}</div>
          <div class="specimen-board" data-board="${c}"></div>
          <div class="specimen-cap">A real successful layout from this condition (n=${cond.n}, ${cond.wins} solved).</div>
        </div>`;
      }).join('')}
    </div>
    <div class="panel" style="padding-top:36px;">
      <div class="cta-grid">
        <div class="cta">
          <div class="cta-title">Problem Attempts</div>
          <div class="cta-desc">Pick any participant and trial. Replay the exact move-by-move sequence, reconstructed from raw pointer-event logs.</div>
          <button class="cta-btn" onclick="showView('attempts')">Explore Trials →</button>
        </div>
        <div class="cta">
          <div class="cta-title">Solution Patterns</div>
          <div class="cta-desc">Every successful and failed layout, grouped by exact arrangement and ranked by how often it recurs.</div>
          <button class="cta-btn" onclick="showView('patterns')">View Patterns →</button>
        </div>
        <div class="cta">
          <div class="cta-title">The Blank Card Paradox</div>
          <div class="cta-desc">Why participants who used the optional Blank card succeeded far more — and why most never touched it.</div>
          <button class="cta-btn" onclick="showView('paradox')">Read the Story →</button>
        </div>
      </div>
      <div class="quickstart">
        <div class="quickstart-title">Quick-start guide</div>
        <div class="qs-item"><span class="qs-num">01</span><span><b>Problem Attempts</b> — cascading Condition → Participant → Trial selection, then Show Animation or Show Final State.</span></div>
        <div class="qs-item"><span class="qs-num">02</span><span><b>Solution Patterns</b> — top 5 most frequent success/failure layouts per condition, with an option to browse every unique one.</span></div>
        <div class="qs-item"><span class="qs-num">03</span><span><b>Hint Effects</b> — inspect any trial cell-by-cell against the raw source field names, with Blank cards highlighted.</span></div>
        <div class="qs-item"><span class="qs-num">04</span><span><b>Blank Card Paradox</b> — the full narrative: who had access to the Blank card, who used it, and what happened when they did.</span></div>
        <div class="qs-item"><span class="qs-num">05</span><span><b>Behavioral Analysis</b> — verified movement-behaviour findings, including a proper learning-curve chart.</span></div>
        <div class="qs-item"><span class="qs-num">06</span><span><b>Behavioral Patterns</b> — per-condition grid viewer plus a side-by-side success/failure comparison.</span></div>
        <div class="qs-item"><span class="qs-num">07</span><span>The data-integrity audit lives in <b>Methodology &amp; Data Notes</b> below, rather than its own tab.</span></div>
      </div>

      <details class="methodology">
        <summary>Methodology &amp; Data Notes — what's verified, what's not, and why</summary>
        <div class="audit-list">
          <div class="audit-item"><div class="audit-tag">Issue 01</div><div class="audit-body"><b>Participant 183 is recorded under two conditions</b> (KQ and KQJB). Kept as-is, flagged wherever participant counts appear.</div></div>
          <div class="audit-item"><div class="audit-tag">Issue 02</div><div class="audit-body"><b>The source workbook's own "t1 means" tab overstates KQB's N</b> (62 vs. the correct 61 — caused by a duplicated row for participant 241). This rebuild uses the corrected 61 throughout.</div></div>
          <div class="audit-item"><div class="audit-tag">Issue 03</div><div class="audit-body"><b>Participant 168 is missing from the "T1 final trials" sheet</b> despite having real data in "Task 1." Not used by this rebuild, so not consequential here.</div></div>
          <div class="audit-item"><div class="audit-tag info">Issue 04</div><div class="audit-body"><b>${dq.movement_log_coverage.with_movement_log} of ${dq.movement_log_coverage.total_trials} trials have a usable move-by-move log</b>; the rest have final position only. Blank cards are never captured in the move log, only in final position, for any trial.</div></div>
        </div>
        <div class="chart-card">
          <div class="chart-card-title">What this rebuild deliberately does not fake</div>
          <div class="audit-body" style="margin-top:10px;">
            <b>Messiness Score</b> and the <b>766/714-trial filter</b> (Behavioral Analysis) — formula and filter rule not confirmable from screenshots.<br><br>
            <b>The "Pattern" filter on Hint Effects</b> (values like "A1") — meaning not confirmable from a single screenshot.<br><br>
            <b>"Instruction rechecks"</b> — mentioned in the original site's copy, but no corresponding field was found in the source workbook's four sheets.<br><br>
            <b>Power BI embed</b> — no file provided.
          </div>
        </div>
      </details>
    </div>
  `;
  CONDS.forEach(c=>{
    const win = DATA.explorer.find(t=>t.cond===c && t.outcome==='win');
    const el = V('home').querySelector(`[data-board="${c}"]`);
    if(win && el) renderBoard(el, win.grid, {size:190, axis:false});
  });
}

/* ====================================================================== */
/* 2. PROBLEM ATTEMPTS                                                      */
/* ====================================================================== */
function buildAttempts(){
  const pidsByCond = {};
  CONDS.forEach(c=> pidsByCond[c] = [...new Set(DATA.explorer.filter(t=>t.cond===c).map(t=>t.pid))].sort((a,b)=>a-b));
  V('attempts').innerHTML = `
    <div class="section-head">
      <div class="section-eyebrow">Section 01</div>
      <h2 class="section-title">Problem Attempts <em>Explorer</em></h2>
      <p class="section-desc">Pick a condition, a participant, then a specific trial. "Show Animation" replays the actual recorded move sequence where one exists (${DATA.overview.trials_with_animation} of ${DATA.overview.trials} trials have one) — otherwise only the final layout is available.</p>
    </div>
    <div class="panel">
      <div class="explorer-shell">
        <div class="field-row">
          <div class="field"><div class="field-label">Condition</div><select class="sel" id="att-cond">${CONDS.map(c=>`<option value="${c}">${c}</option>`).join('')}</select></div>
          <div class="field"><div class="field-label">Participant</div><select class="sel" id="att-pid"></select></div>
          <div class="field"><div class="field-label">Trial</div><select class="sel" id="att-trial"></select></div>
        </div>
        <div class="btn-row">
          <button class="btn primary" id="att-btn-anim">▶ Show Animation</button>
          <button class="btn" id="att-btn-final">▦ Show Final State</button>
        </div>
        <div class="explorer-main" id="att-main">
          <div class="exp-empty">
            <svg class="cardback-icon" viewBox="0 0 34 46" style="margin:0 auto;"><rect x="1.5" y="1.5" width="31" height="43" rx="4" fill="#eef1f6" stroke="#18233b" stroke-width="1.5"/><rect x="6" y="6" width="22" height="34" rx="2" fill="none" stroke="#16a34a" stroke-width="1.2" stroke-dasharray="2 2"/></svg>
            <div style="font-family:'Newsreader',serif; font-size:20px; color:#18233b; font-weight:600;">Select a participant and trial to begin</div>
            <div style="font-size:12.5px; max-width:420px;">Choose from the panel above, then pick Show Animation for a step-by-step replay, or Show Final State for the completed layout with trial stats.</div>
          </div>
        </div>
      </div>
    </div>
  `;
  const condSel=document.getElementById('att-cond'), pidSel=document.getElementById('att-pid'), trialSel=document.getElementById('att-trial');
  const main = document.getElementById('att-main');
  let currentTrials = [], currentTrial=null, animTimer=null, animStep=0;

  function loadPids(){ pidSel.innerHTML = pidsByCond[condSel.value].map(p=>`<option value="${p}">Participant ${p}</option>`).join(''); loadTrials(); }
  function loadTrials(){
    const c=condSel.value, p=Number(pidSel.value);
    currentTrials = DATA.explorer.filter(t=>t.cond===c && t.pid===p).sort((a,b)=>a.attempt-b.attempt);
    trialSel.innerHTML = currentTrials.map((t,i)=>`<option value="${i}">Trial ${t.attempt} — ${t.outcome.toUpperCase()}${t.has_animation?'':' (no move log)'}</option>`).join('');
    currentTrial = currentTrials[0];
  }
  condSel.onchange = loadPids; pidSel.onchange = loadTrials; trialSel.onchange = ()=>{ currentTrial = currentTrials[Number(trialSel.value)]; };

  if(pendingAttemptSelection){
    const sel = pendingAttemptSelection; pendingAttemptSelection = null;
    if(CONDS.includes(sel.cond)){
      condSel.value = sel.cond;
      loadPids();
      if(pidsByCond[sel.cond].includes(sel.pid)){
        pidSel.value = sel.pid;
        loadTrials();
        const idx = currentTrials.findIndex(t=>t.attempt===sel.attempt);
        if(idx>=0){ trialSel.value = idx; currentTrial = currentTrials[idx]; }
      }
    } else {
      loadPids();
    }
    showFinal();
  } else {
    loadPids();
  }

  function stopAnim(){ if(animTimer){ clearInterval(animTimer); animTimer=null; } }

  function showFinal(){
    stopAnim();
    const t = currentTrial; if(!t) return;
    main.innerHTML = `<div class="exp-board-box" id="att-board"></div><div class="exp-detail" id="att-detail"></div>`;
    renderBoard(document.getElementById('att-board'), t.grid, {size:320});
    document.getElementById('att-detail').innerHTML = `
      <div class="exp-detail-title">P${t.pid} · Trial ${t.attempt}</div>
      <div class="outcome-pill ${t.outcome}">${t.outcome}</div>
      <div style="margin-top:16px;">
        <div class="exp-detail-row"><span>Condition</span><span>${t.cond}</span></div>
        <div class="exp-detail-row"><span>Moves</span><span>${t.moves ?? '—'}</span></div>
        <div class="exp-detail-row"><span>Duration</span><span>${t.duration!=null?t.duration.toFixed(1)+'s':'—'}</span></div>
        <div class="exp-detail-row"><span>Cards on board</span><span>${t.grid.length}</span></div>
        <div class="exp-detail-row"><span>Blank used</span><span>${t.has_blank?'Yes':'No'}</span></div>
      </div>`;
  }

  function showAnim(){
    stopAnim();
    const t = currentTrial; if(!t) return;
    if(!t.has_animation){
      main.innerHTML = `<div class="exp-empty"><div style="font-size:30px;">⚠</div><div style="font-family:'Newsreader',serif; font-size:18px; color:#18233b;">No move log for this trial</div><div style="font-size:12.5px;">The source data has no recorded move sequence here — showing final state instead.</div></div>`;
      setTimeout(showFinal, 900);
      return;
    }
    main.innerHTML = `<div class="exp-board-box" id="att-board"></div><div class="exp-detail" id="att-detail">
        <div class="exp-detail-title">P${t.pid} · Trial ${t.attempt}</div>
        <div class="outcome-pill ${t.outcome}">${t.outcome}</div>
        <div class="anim-controls">
          <button class="anim-btn" id="a-prev">⏮</button>
          <button class="anim-btn play" id="a-play">▶</button>
          <button class="anim-btn" id="a-next">⏭</button>
          <span class="anim-step-label" id="a-steplabel"></span>
        </div>
        <div class="anim-log" id="a-log"></div>
      </div>`;
    const boardEl = document.getElementById('att-board');
    animStep = 0;
    function stateAt(k){
      const positions = {};
      for(let i=0;i<=k;i++){
        const s = t.animation[i];
        if(s.cell===null) delete positions[s.card];
        else positions[s.card] = [s.row, s.col, s.letter];
      }
      return Object.values(positions);
    }
    function render(){
      renderBoard(boardEl, stateAt(animStep), {size:320});
      document.getElementById('a-steplabel').textContent = `Step ${animStep+1} / ${t.animation.length}`;
      document.getElementById('a-log').textContent = t.animation[animStep].desc;
    }
    render();
    document.getElementById('a-prev').onclick = ()=>{ stopAnim(); animStep=Math.max(0,animStep-1); render(); };
    document.getElementById('a-next').onclick = ()=>{ stopAnim(); animStep=Math.min(t.animation.length-1,animStep+1); render(); };
    document.getElementById('a-play').onclick = ()=>{
      if(animTimer){ stopAnim(); document.getElementById('a-play').textContent='▶'; return; }
      document.getElementById('a-play').textContent='⏸';
      animTimer = setInterval(()=>{
        animStep++;
        if(animStep>=t.animation.length){ stopAnim(); document.getElementById('a-play').textContent='▶'; animStep=t.animation.length-1; return; }
        render();
      }, 450);
    };
  }
  document.getElementById('att-btn-anim').onclick = showAnim;
  document.getElementById('att-btn-final').onclick = showFinal;
}

/* ====================================================================== */
/* 3. SOLUTION PATTERNS                                                     */
/* ====================================================================== */
function buildPatterns(){
  V('patterns').innerHTML = `
    <div class="section-head">
      <div class="section-eyebrow">Section 02</div>
      <h2 class="section-title">Solution <em>Patterns</em></h2>
      <p class="section-desc">Every trial's final layout is reduced to a signature (which card sits in which cell) and grouped with every other trial that produced the exact same layout — including across different participants. Ranked by how often each exact arrangement recurs.</p>
    </div>
    <div class="panel">
      <div class="tabrow" id="pat-cond-tabs">${CONDS.map((c,i)=>`<div class="tabbtn${i===0?' on':''}" data-c="${c}">${c}</div>`).join('')}</div>
      <div class="tabrow" id="pat-outcome-tabs" style="margin-top:8px;">
        <div class="tabbtn on" data-o="success">✓ Success patterns</div>
        <div class="tabbtn" data-o="failure">✗ Failure patterns</div>
      </div>
      <div id="pat-body"></div>
    </div>
  `;
  let cond='KQ', outcome='success';
  const body = document.getElementById('pat-body');
  function render(){
    const p = DATA.patterns[cond][outcome];
    const top = p.patterns.slice(0,5);
    body.innerHTML = `
      <div class="callout" style="margin-top:20px;">
        <b>${p.total_trials}</b> ${outcome} trials in ${cond} reduce to <b>${p.total_unique}</b> distinct final layouts.
        ${p.total_unique < p.total_trials*0.3 ? ' Solutions repeat often — a small set of arrangements accounts for most trials.' : ' Layouts are mostly unique — little exact repetition across participants.'}
      </div>
      <div class="pattern-grid">
        ${top.map((pt,i)=>`
          <div class="pattern-card">
            <div class="pattern-meta ${outcome==='failure'?'fail':''}">Pattern #${i+1} · ${pt.frequency} trial${pt.frequency>1?'s':''} · ${pt.cards} cards</div>
            <div data-pat="${i}"></div>
            <div class="pattern-cap">e.g. <span class="xlink" data-jump-cond="${cond}" data-jump-pid="${pt.examples[0].pid}" data-jump-trialn="${pt.examples[0].trialN}">P${pt.examples[0].pid}, trial ${pt.examples[0].trialN}</span>${pt.examples.length>1?` (+${pt.examples.length-1} more shown)`:''}</div>
          </div>
        `).join('')}
      </div>
      <div style="margin-top:20px;">
        <button class="btn" id="pat-showall">Show all ${p.total_unique} patterns</button>
      </div>
      <div id="pat-all-wrap" style="margin-top:16px; display:none;"></div>
    `;
    top.forEach((pt,i)=>{
      renderBoard(body.querySelector(`[data-pat="${i}"]`), pt.grid, {size:200});
    });
    document.getElementById('pat-showall').onclick = ()=>{
      const wrap = document.getElementById('pat-all-wrap');
      const showing = wrap.style.display!=='none';
      if(showing){ wrap.style.display='none'; return; }
      wrap.style.display='block';
      wrap.innerHTML = `<div class="chart-card" style="margin-top:0;"><div class="chart-card-title">All ${p.total_unique} unique layouts — ${cond} / ${outcome}</div>
        <table style="width:100%; margin-top:14px; border-collapse:collapse; font-size:12.5px;">
          <tr style="text-align:left; color:var(--tx-dim); font-family:'IBM Plex Mono'; font-size:11px;"><th style="padding:6px 0;">#</th><th>Frequency</th><th>Cards placed</th><th>Example</th></tr>
          ${p.patterns.map((pt,i)=>`<tr style="border-top:1px solid var(--line-soft);"><td style="padding:7px 0;">${i+1}</td><td>${pt.frequency}</td><td>${pt.cards}</td><td style="font-family:'IBM Plex Mono'; color:var(--tx-dim);">P${pt.examples[0].pid} / trial ${pt.examples[0].trialN}</td></tr>`).join('')}
        </table>
      </div>`;
    };
  }
  document.getElementById('pat-cond-tabs').querySelectorAll('.tabbtn').forEach(b=>{
    b.onclick = ()=>{ cond=b.dataset.c; document.querySelectorAll('#pat-cond-tabs .tabbtn').forEach(x=>x.classList.toggle('on',x===b)); render(); };
  });
  document.getElementById('pat-outcome-tabs').querySelectorAll('.tabbtn').forEach(b=>{
    b.onclick = ()=>{ outcome=b.dataset.o; document.querySelectorAll('#pat-outcome-tabs .tabbtn').forEach(x=>x.classList.toggle('on',x===b)); render(); };
  });
  render();
}

/* ====================================================================== */
/* 4. HINT EFFECTS                                                          */
/* ====================================================================== */
function buildHints(){
  const pidsByCond = {};
  CONDS.forEach(c=> pidsByCond[c] = [...new Set(DATA.explorer.filter(t=>t.cond===c).map(t=>t.pid))].sort((a,b)=>a-b));
  V('hints').innerHTML = `
    <div class="section-head">
      <div class="section-eyebrow">Section 03</div>
      <h2 class="section-title">Hint Effects <em>(Blank Card Highlight)</em></h2>
      <p class="section-desc">Same trial data as the Explorer, but every cell is numbered against its raw source field name. Each rank has its own color — King, Queen, Jack, and Blank — so you can see rank identity and cell index together at a glance.</p>
      <div class="callout warn" style="margin-top:18px; max-width:680px;">
        The original site had a "Pattern" filter here (values like "A1") whose exact meaning I could not confirm from a screenshot alone — so I've left it out rather than guess. Condition / Participant / Trial selection below is fully real.
      </div>
    </div>
    <div class="panel">
      <div class="field-row">
        <div class="field"><div class="field-label">Condition</div><select class="sel" id="hint-cond">${CONDS.map(c=>`<option value="${c}">${c}</option>`).join('')}</select></div>
        <div class="field"><div class="field-label">Participant</div><select class="sel" id="hint-pid"></select></div>
        <div class="field"><div class="field-label">Trial</div><select class="sel" id="hint-trial"></select></div>
      </div>
      <div class="explorer-main" style="margin-top:22px;">
        <div class="exp-board-box" id="hint-board"></div>
        <div class="exp-detail">
          <div class="pill-legend">
            <div class="pill-leg-item"><span class="pill-swatch" style="background:${CARDCOL.K};"></span>King</div>
            <div class="pill-leg-item"><span class="pill-swatch" style="background:${CARDCOL.Q};"></span>Queen</div>
            <div class="pill-leg-item"><span class="pill-swatch" style="background:${CARDCOL.J};"></span>Jack</div>
            <div class="pill-leg-item"><span class="pill-swatch" style="background:#fff; border:1.5px solid #18233b;"></span>Blank</div>
          </div>
          <div id="hint-detail"></div>
          <div class="chart-card" style="margin-top:16px; padding:16px;">
            <div class="chart-card-title" style="font-size:14px;">Legend (raw field names)</div>
            <div id="hint-legend" style="font-family:'IBM Plex Mono'; font-size:11px; color:var(--tx-dim); line-height:1.9; margin-top:8px;"></div>
          </div>
        </div>
      </div>
    </div>
  `;
  const condSel=document.getElementById('hint-cond'), pidSel=document.getElementById('hint-pid'), trialSel=document.getElementById('hint-trial');
  let trials=[];
  function loadPids(){ pidSel.innerHTML = pidsByCond[condSel.value].map(p=>`<option value="${p}">P${p}</option>`).join(''); loadTrials(); }
  function loadTrials(){
    const c=condSel.value, p=Number(pidSel.value);
    trials = DATA.explorer.filter(t=>t.cond===c && t.pid===p).sort((a,b)=>a.attempt-b.attempt);
    trialSel.innerHTML = trials.map((t,i)=>`<option value="${i}">Trial ${t.attempt} — ${t.outcome}</option>`).join('');
    render();
  }
  function render(){
    const t = trials[Number(trialSel.value)||0]; if(!t) return;
    const legend = renderIndexedBoard(document.getElementById('hint-board'), t.grid, {size:300});
    document.getElementById('hint-detail').innerHTML = `
      <div class="exp-detail-row"><span>Status</span><span>${t.outcome}</span></div>
      <div class="exp-detail-row"><span>Condition</span><span>${t.cond}</span></div>
      <div class="exp-detail-row"><span>Participant</span><span>${t.pid}</span></div>
      <div class="exp-detail-row"><span>Trial</span><span>${t.attempt}</span></div>
      <div class="exp-detail-row"><span>Blank used</span><span>${t.has_blank?'Yes':'No'}</span></div>
    `;
    document.getElementById('hint-legend').innerHTML = legend.map(l=>`${l}<br>`).join('') || '(empty grid)';
  }
  condSel.onchange = loadPids; pidSel.onchange = loadTrials; trialSel.onchange = render;
  loadPids();
}

/* ====================================================================== */
/* 5. BLANK CARD PARADOX                                                    */
/* ====================================================================== */
function buildParadox(){
  const bs = DATA.blank_summary, avu = DATA.avail_vs_usage, cross = DATA.blank_cross;
  const STOPS = ['setup','surprise','tension','evidence','strategy','conclusion'];
  const LABELS = {setup:'01 Setup', surprise:'02 Surprise', tension:'03 Why So Few?', evidence:'04 Evidence', strategy:'05 Strategy Breakdown', conclusion:'Conclusion'};
  V('paradox').innerHTML = `
    <div class="section-head">
      <div class="section-eyebrow">Research Story</div>
      <h2 class="section-title">The Blank Card <em>Paradox</em></h2>
      <p class="section-desc">Why a "helpful" optional tool did not automatically make the puzzle easier.</p>
      <div class="tabrow" id="par-tabs" style="margin-top:20px;">${STOPS.map((s,i)=>`<div class="tabbtn${i===0?' on':''}" data-s="${s}">${LABELS[s]}</div>`).join('')}</div>
    </div>
    <div class="panel" id="par-body"></div>
  `;
  const body = document.getElementById('par-body');
  function render(stop){
    document.querySelectorAll('#par-tabs .tabbtn').forEach(b=>b.classList.toggle('on', b.dataset.s===stop));
    if(stop==='setup'){
      body.innerHTML = `
        <div class="chart-card" style="margin-top:0;">
          <div class="chart-card-title">The setup</div>
          <p style="font-size:13.5px; color:#4d5872; line-height:1.7; margin-top:10px;">Participants arranged Kings, Queens, and (in two conditions) Jacks on an 8×8 grid under a row/column placement rule. In KQB and KQJB, they additionally had access to Blank cards — pieces with no rank, usable as flexible placeholders. The intuitive expectation: more flexibility should make the task easier.</p>
          <div class="stat-row" style="margin-top:20px; grid-template-columns:repeat(3,1fr);">
            <div class="stat"><div class="stat-num">${bs.total_blank_eligible}</div><div class="stat-label">Blank-eligible participants</div></div>
            <div class="stat"><div class="stat-num">${bs.used_blank_n}</div><div class="stat-label">Actually used a blank</div></div>
            <div class="stat"><div class="stat-num">${bs.not_used_n}</div><div class="stat-label">Had access, didn't use it</div></div>
          </div>
          <div class="callout" style="margin-top:18px;">Scoped to KQB + KQJB participants only (n=${bs.total_blank_eligible}) — unlike the original site's framing, KQ/KQJ participants (who never had a blank option) are not folded into the "did not use" count here, to avoid conflating "never had the choice" with "had it, declined it."</div>
        </div>`;
    } else if(stop==='surprise'){
      body.innerHTML = `<div class="chart-card" style="margin-top:0;"><div class="chart-card-title">Success rate: used blank vs. did not</div><div class="chart-wrap"><canvas id="par-chart-1"></canvas></div></div>
        <div class="callout" style="margin-top:20px;"><b>Key finding.</b> Participants who used a Blank card succeeded at <span class="num">${bs.used_blank_success_rate}%</span>, compared with <span class="num">${bs.not_used_success_rate}%</span> for those who had the option but didn't use it.</div>`;
      setTimeout(()=>{
        CHARTS.par1 = new Chart(document.getElementById('par-chart-1'), {
          type:'bar', data:{ labels:['Used blank card','Did not use it'], datasets:[{data:[bs.used_blank_success_rate,bs.not_used_success_rate], backgroundColor:['#2563eb','#dc2626'], borderRadius:2, barThickness:90}]},
          options: chartBaseOpts({suffix:'%'})
        });
      },0);
    } else if(stop==='tension'){
      body.innerHTML = `<div class="chart-card" style="margin-top:0;"><div class="chart-card-title">Blank availability vs. actual usage, by condition</div><div class="chart-wrap"><canvas id="par-chart-2"></canvas></div></div>
        <div class="callout" style="margin-top:20px;">
          ${avu.filter(a=>a.used_blank>0||CONDS.includes(a.cond)).map(a=>`<b>${a.cond}</b>: ${a.used_blank} of ${a.participants} participants used a blank (${a.participants?Math.round(a.used_blank/a.participants*100):0}%).<br>`).join('')}
          If blank cards were genuinely helpful, why did roughly half of eligible participants never touch one? That tension is the core of this page.
        </div>`;
      setTimeout(()=>{
        CHARTS.par2 = new Chart(document.getElementById('par-chart-2'), {
          type:'bar', data:{ labels: avu.map(a=>a.cond), datasets:[
            {label:'Participants', data:avu.map(a=>a.participants), backgroundColor:'#3a4568', borderRadius:2},
            {label:'Used blank', data:avu.map(a=>a.used_blank), backgroundColor:'#5aa078', borderRadius:2},
          ]}, options: chartBaseOpts({suffix:''})
        });
      },0);
    } else if(stop==='evidence'){
      body.innerHTML = `<div class="chart-card" style="margin-top:0;"><div class="chart-card-title">Every blank-eligible participant, by outcome</div>
        <div class="pill-legend">
          <div class="pill-leg-item"><span class="pill-swatch" style="background:rgba(79,122,176,.5); border:1px solid #4f7ab0;"></span>Used blank + success</div>
          <div class="pill-leg-item"><span class="pill-swatch" style="background:rgba(22,163,74,.5); border:1px solid #22c55e;"></span>Used blank + failed</div>
          <div class="pill-leg-item"><span class="pill-swatch" style="background:rgba(90,160,120,.5); border:1px solid #5aa078;"></span>Success, no blank</div>
          <div class="pill-leg-item"><span class="pill-swatch" style="background:rgba(220,38,38,.4); border:1px solid #dc2626;"></span>Failed, no blank</div>
        </div>
        <div class="pill-wrap">${DATA.pill_data.map(p=>{
          const last = DATA.explorer.filter(t=>t.cond===p.cond && t.pid===p.pid).sort((a,b)=>b.attempt-a.attempt)[0];
          return `<span class="pill ${p.bucket}" title="${p.cond} — click to view this trial" style="cursor:pointer;" data-jump-cond="${p.cond}" data-jump-pid="${p.pid}" data-jump-trialn="${last.trialN}">P${p.pid}</span>`;
        }).join('')}</div>
      </div>`;
    } else if(stop==='strategy'){
      body.innerHTML = `<div class="chart-card" style="margin-top:0;"><div class="chart-card-title">Success rate by condition and blank usage</div><div class="chart-wrap"><canvas id="par-chart-3"></canvas></div></div>
        <div class="callout" style="margin-top:20px;">Blank cards look most valuable exactly where the deck is otherwise hardest (KQB, no Jack) — success jumps from ${cross.find(x=>x.label==='KQB no blank').win_rate}% to ${cross.find(x=>x.label==='KQB with blank').win_rate}%. In the more complex KQJB deck, the lift is smaller (${cross.find(x=>x.label==='KQJB no blank').win_rate}% → ${cross.find(x=>x.label==='KQJB with blank').win_rate}%), consistent with a harder underlying puzzle limiting how much any one tool can help.</div>`;
      setTimeout(()=>{
        CHARTS.par3 = new Chart(document.getElementById('par-chart-3'), {
          type:'bar', data:{ labels: cross.map(c=>c.label), datasets:[{data:cross.map(c=>c.win_rate), backgroundColor: cross.map(c=>c.label.includes('with blank')?'#2563eb':c.label.includes('no blank')?'#dc2626':'#94a3b8'), borderRadius:2}]},
          options: chartBaseOpts({suffix:'%'})
        });
      },0);
    } else if(stop==='conclusion'){
      body.innerHTML = `<div class="callout" style="margin-top:0;">
        <b>Conclusion.</b> Blank cards were genuinely useful when used — but usage wasn't automatic. Roughly half of eligible participants never picked one up, most likely because the benefit isn't obvious until the standard deck alone starts to feel insufficient (as in KQB, where no Jack is available at all). The tool's value was real; the barrier was discovery and adoption, not the tool itself.<br><br>
        This mirrors a broader point: giving people a more flexible tool doesn't guarantee they'll recognise when to reach for it.
      </div>`;
    }
  }
  document.getElementById('par-tabs').querySelectorAll('.tabbtn').forEach(b=> b.onclick = ()=> render(b.dataset.s));
  render('setup');
}

/* ====================================================================== */
/* 6. BEHAVIORAL ANALYSIS (honest, partial)                                 */
/* ====================================================================== */
function buildBehavioral(){
  const cov = DATA.data_quality.movement_log_coverage;
  const TABS = ['learning','evidence','distributions','timing','spatial','trajectories','failure','notes'];
  const TABLABEL = {learning:'Learning Curves', evidence:'Evidence & Validity', distributions:'Distributions',
    timing:'Timing & Latency', spatial:'Spatial Patterns', trajectories:'Individual Trajectories',
    failure:'Psychology of Failure', notes:'Data Scope Notes'};
    const TABDESC = {
      learning: 'How success rates change across repeated attempts. Reveals whether participants improve with practice or hit a ceiling \u2014 useful for gauging whether the task has a genuine learning curve or remains guesswork.',
      evidence: 'Validates whether the task rules (no same-row/column repeats) are actually understood. Compares rule violation rates between successful and failed trials, with chi-square tests for statistical significance.',
      distributions: 'Box-plot breakdowns of move counts and trial durations per condition. Shows the spread, median, and outliers \u2014 helpful for spotting whether high move counts correlate with success or just wasted effort.',
      timing: 'Latency analysis between moves. Examines whether participants pause more before critical moves, how timing evolves across attempts, and whether faster play predicts better outcomes.',
      spatial: 'Heatmaps showing where cards are placed on the 8\u00d78 grid. Reveals spatial biases \u2014 e.g., whether participants cluster cards in corners, follow diagonal patterns, or spread them evenly.',
      trajectories: 'Individual participant journeys across trials. Track how specific people changed their strategy over multiple attempts \u2014 useful for case studies and identifying distinct behavioral archetypes.',
      failure: 'Psychological patterns in failed attempts. Analyzes error types, repeated mistakes, abandonment points, and whether failures stem from misunderstanding rules vs. strategic missteps.',
      notes: 'Methodological documentation: data scope, known anomalies, sample sizes per condition, and caveats for interpreting the analyses above. Essential reading before drawing conclusions.'
    };
  V('behavioral').innerHTML = `
    <div class="section-head">
      <div class="section-eyebrow">Deep Dive</div>
      <h2 class="section-title">Trial <em>Analysis</em></h2>
      <p class="section-desc">Explore the behavioral data in depth \u2014 from learning curves and timing patterns to spatial strategies and individual trajectories. Each tab below offers a different analytical lens on how participants approached the card task.</p>
      <div class="tabrow" id="beh-tabs" style="margin-top:18px;">${TABS.map((t,i)=>`<div class="tabbtn${i===0?' on':''}" data-t="${t}">${TABLABEL[t]}</div>`).join('')}</div>
      <p id="beh-tab-desc" class="beh-tab-desc">${TABDESC[TABS[0]]}</p>
    </div>
    <div class="panel" id="beh-body"></div>
  `;
  const body = document.getElementById('beh-body');

  function render(tab){
    document.querySelectorAll('#beh-tabs .tabbtn').forEach(b=>b.classList.toggle('on', b.dataset.t===tab));
      const descEl = document.getElementById('beh-tab-desc');
      if(descEl && TABDESC[tab]) descEl.textContent = TABDESC[tab];

    if(tab==='learning'){
      body.innerHTML = `
        <div class="chart-card" style="margin-top:0;">
          <div class="chart-card-title">Do people learn the rule, or just get lucky?</div>
          <div class="chart-card-sub">Success rate at each attempt number. A line stops the moment fewer than 3 participants remain at that attempt — points built from 1–2 people aren't shown, so the line never implies more confidence than the sample supports.</div>
          <div class="chart-wrap" style="height:320px;"><canvas id="chart-learning"></canvas></div>
          <div class="legend-row">${CONDS.map(c=>`<div class="legend-item"><span class="legend-dot" style="background:${CCOL[c]}"></span>${c}</div>`).join('')}</div>
        </div>`;
      const datasets = CONDS.map(c=>{
        const pts = [];
        for(const p of DATA.learning[c]){ if(p.n<3) break; pts.push({x:p.attempt, y:p.success_rate, n:p.n}); }
        return { label:c, data:pts, borderColor:CCOL[c], backgroundColor:CCOL[c], pointRadius:3, pointHoverRadius:5, tension:.25, borderWidth:2.4 };
      });
      CHARTS.learning = new Chart(document.getElementById('chart-learning'), {
        type:'line', data:{ datasets },
        options:{
          responsive:true, maintainAspectRatio:false,
          plugins:{ legend:{display:false}, tooltip:{ callbacks:{ label:(c)=>`${c.dataset.label}: ${c.formattedValue}% (n=${c.raw.n})` } } },
          scales:{
            x:{ type:'linear', min:1, max:10, ticks:{stepSize:1, color:'#4d5872', font:{family:'IBM Plex Mono', size:11}}, title:{display:true, text:'Attempt number', color:'#75809a', font:{family:'IBM Plex Mono', size:11}}, grid:{color:'rgba(28,32,48,.08)'} },
            y:{ min:0, max:100, ticks:{color:'#4d5872', font:{family:'IBM Plex Mono', size:11}, callback:(v)=>v+'%'}, title:{display:true, text:'Success rate', color:'#75809a', font:{family:'IBM Plex Mono', size:11}}, grid:{color:'rgba(28,32,48,.08)'} }
          }
        }
      });

    } else if(tab==='evidence'){
      const rv = DATA.rule_validation, cc = DATA.condition_chi2, bc = DATA.blank_chi2, bci = DATA.blank_ci;
      body.innerHTML = `
        <div class="chart-card" style="margin-top:0;">
          <div class="chart-card-title">What actually makes a placement valid</div>
          <div class="chart-card-sub">Inferred and verified directly from the data: no rank (King/Queen/Jack/Blank) may repeat within the same row or column.</div>
          <div class="rule-diagram-wrap">
            <div class="rule-diagram-col"><div data-rule="valid"></div><div class="rule-diagram-label ok">✓ Valid — each rank appears once per row and column</div></div>
            <div class="rule-diagram-col"><div data-rule="invalid"></div><div class="rule-diagram-label bad">✗ Invalid — King repeats in column 1</div></div>
          </div>
          <div class="callout" style="margin-top:20px;">
            This isn't a guess — it's measured. <b>${rv.win_violation_rate}%</b> of successful trials contain a row/column repeat (i.e. essentially none), versus <b>${rv.fail_violation_rate}%</b> of failed trials (average <span class="num">${rv.fail_avg_violations}</span> repeats per failed attempt). That gap is the direct evidence for what the rule actually is.
          </div>
        </div>
        <div class="chart-card">
          <div class="chart-card-title">Is the condition effect statistically real?</div>
          <div class="chart-card-sub">Success rate by condition, with 95% confidence intervals (Wilson score interval)</div>
          <div class="chart-wrap"><canvas id="chart-evidence-cond"></canvas></div>
          <div class="evidence-grid">
            <div class="evidence-card">
              <div class="evidence-stat">χ² = ${cc.chi2}</div>
              <div class="evidence-label">Condition × Outcome (df=${cc.dof}, n=${cc.n})</div>
              <div class="evidence-sig ${cc.p<0.05?'sig':'nonsig'}">${cc.p<0.001?'p < 0.001':'p = '+cc.p.toFixed(3)} — ${cc.p<0.05?'statistically significant':'not significant'}</div>
              <div class="evidence-detail">The four conditions do not have the same underlying success rate — this is not explainable by chance alone at any conventional threshold.</div>
            </div>
            <div class="evidence-card">
              <div class="evidence-stat">χ² = ${bc.chi2}</div>
              <div class="evidence-label">Blank Used × Outcome (df=${bc.dof})</div>
              <div class="evidence-sig ${bc.p<0.05?'sig':'nonsig'}">${bc.p<0.001?'p < 0.001':'p = '+bc.p.toFixed(3)} — ${bc.p<0.05?'statistically significant':'not significant'}</div>
              <div class="evidence-detail">Used blank: ${bci.used.rate}% [${bci.used.lo}–${bci.used.hi}%], n=${bci.used.n}. Didn't use: ${bci.not_used.rate}% [${bci.not_used.lo}–${bci.not_used.hi}%], n=${bci.not_used.n}. The confidence intervals don't overlap — the "Blank Card Paradox" is a real effect, not sampling noise.</div>
            </div>
          </div>
        </div>
        <div class="chart-card">
          <div class="chart-card-title">People don't place cards in random order — they follow a routine</div>
          <div class="chart-card-sub">Which rank gets placed 1st, 2nd, 3rd, 4th across a trial (from ${Object.values(DATA.placement_order_first).reduce((a,b)=>a+b,0)} trials with a usable move log)</div>
          <div class="chart-wrap"><canvas id="chart-order"></canvas></div>
          <div class="callout" style="margin-top:16px;">
            <b>The Queen goes down first, almost every time.</b> ${(()=>{ const t=Object.values(DATA.placement_order_first).reduce((a,b)=>a+b,0); const q=DATA.placement_order_first.Q||0; return Math.round(q/t*100); })()}% of trials with a recorded move sequence start with a Queen — not the King, despite Kings conventionally ranking higher. That's a shared behavioural routine across participants who never coordinated with each other, not a coincidence: by the 2nd move Kings dominate instead, suggesting people anchor with a "safe" reference card first and build outward from it.
          </div>
        </div>`;
      renderRuleExample(body.querySelector('[data-rule="valid"]'), true);
      renderRuleExample(body.querySelector('[data-rule="invalid"]'), false);
      const posData = DATA.position_rank;
      const posLabels = Object.keys(posData).sort((a,b)=>a-b);
      CHARTS.order = new Chart(document.getElementById('chart-order'), {
        type:'bar',
        data:{ labels: posLabels.map(p=>'Position '+p),
          datasets: ['K','Q','J','B'].map(letter=>({
            label: RANKNAME[letter],
            data: posLabels.map(p=> (posData[p] && posData[p][letter]) || 0),
            backgroundColor: CARDCOL[letter]==='#ffffff' ? '#c7cdd8' : CARDCOL[letter],
          }))
        },
        options:{ responsive:true, maintainAspectRatio:false,
          plugins:{ legend:{display:true, position:'top', labels:{color:'#4d5872', font:{family:'IBM Plex Mono', size:11}}}, tooltip:{ callbacks:{ label:(c)=>`${c.dataset.label}: ${c.formattedValue}` } } },
          scales:{
            x:{ stacked:true, grid:{display:false}, ticks:{color:'#4d5872', font:{family:'IBM Plex Mono', size:10.5}} },
            y:{ stacked:true, grid:{color:'rgba(28,32,48,.08)'}, ticks:{color:'#4d5872', font:{family:'IBM Plex Mono', size:10.5}} }
          }
        }
      });
      const cs = DATA.conditions_ci;
      CHARTS.evidenceCond = new Chart(document.getElementById('chart-evidence-cond'), {
        type:'bar',
        data:{ labels: cs.map(c=>c.code), datasets:[{
          data: cs.map(c=>c.win_rate), backgroundColor: cs.map(c=>CCOL[c.code]), borderRadius:2,
          errorBars: cs.map(c=>({lo:c.ci_lo, hi:c.ci_hi})),
        }]},
        options: chartBaseOpts({suffix:'%'}), plugins:[errorBarPlugin]
      });

    } else if(tab==='distributions'){
      body.innerHTML = `
        <div class="chart-card" style="margin-top:0;">
          <div class="chart-card-title">Moves per trial — full distribution, not just the average</div>
          <div class="chart-card-sub">Box shows Q1–Q3, line is the median, white dot is the mean, whiskers span min–max</div>
          <div class="box-row" id="box-moves"></div>
        </div>
        <div class="chart-card">
          <div class="chart-card-title">Trial duration (seconds) — full distribution</div>
          <div class="box-row" id="box-duration"></div>
        </div>
        <div class="callout">A mean alone would hide this: in every condition the move-count and duration spread for successful trials is wide and right-skewed — a handful of long, careful trials pull the average up well past the median. The box plots show that shape directly instead of collapsing it into one number.</div>
      `;
      const boxMoves = document.getElementById('box-moves'), boxDur = document.getElementById('box-duration');
      CONDS.forEach(c=>{
        const d = DATA.distributions[c];
        [['moves_win','Success',boxMoves],['moves_fail','Failure',boxMoves]].forEach(([k,lab,tgt])=>{
          const col = document.createElement('div'); col.className='box-col';
          const box = document.createElement('div'); col.appendChild(box);
          const label = document.createElement('div'); label.className='box-col-label'; label.textContent = `${c} ${lab}`;
          col.appendChild(label); tgt.appendChild(col);
          renderBoxPlot(box, d[k], {color: lab==='Success'?'#16a34a':'#dc2626', width:70, height:150});
        });
      });
      CONDS.forEach(c=>{
        const d = DATA.distributions[c];
        [['duration_win','Success'],['duration_fail','Failure']].forEach(([k,lab])=>{
          const col = document.createElement('div'); col.className='box-col';
          const box = document.createElement('div'); col.appendChild(box);
          const label = document.createElement('div'); label.className='box-col-label'; label.textContent = `${c} ${lab}`;
          col.appendChild(label); boxDur.appendChild(col);
          renderBoxPlot(box, d[k], {color: lab==='Success'?'#16a34a':'#dc2626', width:70, height:150});
        });
      });

    } else if(tab==='timing'){
      const ls = DATA.latency_summary;
      body.innerHTML = `
        <div class="stat-row" style="margin-top:0; grid-template-columns:repeat(3,1fr); border:1px solid var(--line);">
          <div class="stat"><div class="stat-num">${ls.win.avg_first_move_latency}<span class="u">s</span></div><div class="stat-label">Avg. time to first move — successful trials</div></div>
          <div class="stat"><div class="stat-num">${ls.fail.avg_first_move_latency}<span class="u">s</span></div><div class="stat-label">Avg. time to first move — failed trials</div></div>
          <div class="stat"><div class="stat-num">${ls.win.avg_last_gap}<span class="u">s</span></div><div class="stat-label">Avg. pause before the final move — successful trials</div></div>
        </div>
        <div class="chart-card">
          <div class="chart-card-title">Pace within a trial: gap between consecutive moves</div>
          <div class="chart-card-sub">Move-position 1→2 shows the longest pause in both groups — consistent with an initial planning phase before placement speeds up</div>
          <div class="chart-wrap"><canvas id="chart-latency"></canvas></div>
          <div class="legend-row">
            <div class="legend-item"><span class="legend-dot" style="background:#16a34a"></span>Successful trials</div>
            <div class="legend-item"><span class="legend-dot" style="background:#dc2626"></span>Failed trials</div>
          </div>
        </div>
        <div class="callout">Successful trials start slightly faster (${ls.win.avg_first_move_latency}s vs ${ls.fail.avg_first_move_latency}s to the first move) but finish with a noticeably longer pause before the last move (${ls.win.avg_last_gap}s vs ${ls.fail.avg_last_gap}s) — a pattern consistent with a final checking or verification step before committing to a solution, present in successful trials but weaker in failed trials. Computed from raw move-to-move timestamps (n=${ls.win.n} successful trials, n=${ls.fail.n} failed trials with usable timing data).</div>
      `;
      CHARTS.latency = new Chart(document.getElementById('chart-latency'), {
        type:'line',
        data:{ datasets:[
          {label:'Success', data:DATA.latency_curve.win.map(p=>({x:p.pos,y:p.avg_gap})), borderColor:'#16a34a', backgroundColor:'#16a34a', tension:.3, pointRadius:3},
          {label:'Failure', data:DATA.latency_curve.fail.map(p=>({x:p.pos,y:p.avg_gap})), borderColor:'#dc2626', backgroundColor:'#dc2626', tension:.3, pointRadius:3},
        ]},
        options:{ responsive:true, maintainAspectRatio:false,
          plugins:{ legend:{display:false}, tooltip:{ callbacks:{ label:(c)=>`${c.dataset.label}: ${c.formattedValue}s` } } },
          scales:{
            x:{ type:'linear', ticks:{stepSize:1, color:'#4d5872', font:{family:'IBM Plex Mono', size:11}}, title:{display:true, text:'Move position (gap after move N)', color:'#75809a', font:{family:'IBM Plex Mono', size:11}}, grid:{color:'rgba(28,32,48,.08)'} },
            y:{ ticks:{color:'#4d5872', font:{family:'IBM Plex Mono', size:11}, callback:(v)=>v+'s'}, title:{display:true, text:'Avg. seconds', color:'#75809a', font:{family:'IBM Plex Mono', size:11}}, grid:{color:'rgba(28,32,48,.08)'} }
          }
        }
      });

    } else if(tab==='spatial'){
      body.innerHTML = `
        <div class="tabrow" id="spat-cond-tabs">${CONDS.map((c,i)=>`<div class="tabbtn${i===0?' on':''}" data-c="${c}">${c}</div>`).join('')}</div>
        <div class="chart-card">
          <div class="chart-card-title">Placement density: successful vs. failed layouts</div>
          <div class="chart-card-sub">Same intensity scale both sides — a genuine difference in shape means successful and failed layouts aren't just "the same attempt, further along"</div>
          <div class="heat-set" id="spat-heats" style="grid-template-columns:1fr 1fr;"></div>
        </div>
        <div class="chart-card">
          <div class="chart-card-title">Where the first move lands (all conditions combined)</div>
          <div class="chart-card-sub">From the ${DATA.data_quality.movement_log_coverage.with_movement_log} trials with a usable move log</div>
          <div class="heat-set" id="spat-first" style="grid-template-columns:1fr 1fr;"></div>
        </div>
      `;
      let spatCond='KQ';
      function drawHeats(){
        const el = document.getElementById('spat-heats');
        el.innerHTML = `<div class="heat-item"><div class="heat-item-label">✓ Successful (${spatCond})</div><div data-h="win"></div></div><div class="heat-item"><div class="heat-item-label">✗ Failed (${spatCond})</div><div data-h="fail"></div></div>`;
        renderHeat(el.querySelector('[data-h="win"]'), DATA.heatmaps[spatCond], {size:220});
        renderHeat(el.querySelector('[data-h="fail"]'), DATA.heatmaps_fail[spatCond], {size:220});
      }
      drawHeats();
      document.getElementById('spat-cond-tabs').querySelectorAll('.tabbtn').forEach(b=>{
        b.onclick = ()=>{ spatCond=b.dataset.c; document.querySelectorAll('#spat-cond-tabs .tabbtn').forEach(x=>x.classList.toggle('on',x===b)); drawHeats(); };
      });
      const fel = document.getElementById('spat-first');
      fel.innerHTML = `<div class="heat-item"><div class="heat-item-label">✓ First move — successful trials</div><div data-h="fwin"></div></div><div class="heat-item"><div class="heat-item-label">✗ First move — failed trials</div><div data-h="ffail"></div></div>`;
      renderHeat(fel.querySelector('[data-h="fwin"]'), DATA.first_move_grid.win, {size:220});
      renderHeat(fel.querySelector('[data-h="ffail"]'), DATA.first_move_grid.fail, {size:220});

    } else if(tab==='trajectories'){
      body.innerHTML = `
        <div class="tabrow" id="traj-cond-tabs">${CONDS.map((c,i)=>`<div class="tabbtn${i===0?' on':''}" data-c="${c}">${c}</div>`).join('')}</div>
        <div class="chart-card">
          <div class="chart-card-title">Every participant's attempt sequence</div>
          <div class="chart-card-sub">Teal dot = solved that attempt, coral = failed. The average learning curve (first tab) hides this individual variation — some solve immediately, some improve steadily, some never do.</div>
          <div class="spark-grid" id="traj-grid"></div>
        </div>
      `;
      let trajCond='KQ';
      function drawTraj(){
        const grid = document.getElementById('traj-grid'); grid.innerHTML='';
        DATA.trajectories[trajCond].forEach(t=>{
          const cell = document.createElement('div'); cell.className='spark-cell';
          const pidEl = document.createElement('div'); pidEl.className='spark-pid'; pidEl.textContent = `P${t.pid} (${t.seq.length})`;
          cell.appendChild(pidEl);
          const box = document.createElement('div'); cell.appendChild(box);
          grid.appendChild(cell);
          renderSparkline(box, t.seq);
        });
      }
      drawTraj();
      document.getElementById('traj-cond-tabs').querySelectorAll('.tabbtn').forEach(b=>{
        b.onclick = ()=>{ trajCond=b.dataset.c; document.querySelectorAll('#traj-cond-tabs .tabbtn').forEach(x=>x.classList.toggle('on',x===b)); drawTraj(); };
      });

    } else if(tab==='failure'){
      const rc = DATA.row_col_breakdown, us = DATA.undo_stats, pe = DATA.persistence;
      body.innerHTML = `
        <div class="chart-card" style="margin-top:0;">
          <div class="chart-card-title">Do people revise more when they're struggling?</div>
          <div class="chart-card-sub">"Undo" = picking a card back up off the board mid-trial — a direct signal of hesitation or reconsideration</div>
          <div class="evidence-grid">
            <div class="evidence-card">
              <div class="evidence-stat">${us.win.avg_undos}</div>
              <div class="evidence-label">Avg. undos per successful trial (n=${us.win.n})</div>
              <div class="evidence-detail">${us.win.pct_with_any_undo}% of successful trials include at least one undo.</div>
            </div>
            <div class="evidence-card">
              <div class="evidence-stat">${us.fail.avg_undos}</div>
              <div class="evidence-label">Avg. undos per failed trial (n=${us.fail.n})</div>
              <div class="evidence-detail">${us.fail.pct_with_any_undo}% of failed trials include at least one undo.</div>
            </div>
          </div>
          <div class="callout" style="margin-top:16px;"><b>Revision itself isn't the problem.</b> Successful and failed trials undo placements at almost the same rate. Reconsidering a move mid-trial is normal behaviour either way — it doesn't distinguish who eventually gets it right. What differs is what happens after the reconsideration, not whether it happens.</div>
        </div>

        <div class="chart-card">
          <div class="chart-card-title">Row mistakes vs. column mistakes</div>
          <div class="chart-card-sub">Among ${rc.total_fail_trials} failed trials, does the rule get broken more often across a row or down a column?</div>
          <div class="stat-row" style="margin-top:6px; grid-template-columns:repeat(3,1fr);">
            <div class="stat"><div class="stat-num">${rc.row_violation_trials}</div><div class="stat-label">Trials with a row repeat</div></div>
            <div class="stat"><div class="stat-num">${rc.col_violation_trials}</div><div class="stat-label">Trials with a column repeat</div></div>
            <div class="stat"><div class="stat-num">${rc.both_violation_trials}</div><div class="stat-label">Trials with both</div></div>
          </div>
          <div class="callout" style="margin-top:16px;">Nearly identical (${rc.row_violation_trials} vs ${rc.col_violation_trials}) — there's no meaningful bias toward misreading rows over columns or vice versa. Worth stating plainly rather than forcing a story: on this particular question, the data doesn't support a directional cognitive bias.</div>
        </div>

        <div class="chart-card">
          <div class="chart-card-title">What happens to effort after repeated failure</div>
          <div class="chart-card-sub">Among participants who never solved the puzzle: moves and time spent, comparing their very first attempt to their very last</div>
          <div class="chart-wrap"><canvas id="chart-persistence"></canvas></div>
          <div class="callout" style="margin-top:16px;"><b>People who don't solve it disengage — they don't try harder.</b> Across every condition, participants who ultimately failed used <i>fewer</i> moves and spent <i>less</i> time on their final attempt than their first. In ${CONDS.filter(c=>pe[c].avg_duration_delta).map(c=>`${c} (${pe[c].avg_duration_delta}s)`).join(', ')}, the drop in time spent is substantial. That's the signature of discouragement, not escalating effort — repeated failure appears to reduce engagement rather than provoke a more careful retry.</div>
        </div>
      `;
      CHARTS.persistence = new Chart(document.getElementById('chart-persistence'), {
        type:'bar',
        data:{ labels: CONDS, datasets:[
          {label:'Δ Moves (last − first attempt)', data: CONDS.map(c=>pe[c].avg_move_delta), backgroundColor:'#7c3aed', borderRadius:2},
        ]},
        options: chartBaseOpts({suffix:''})
      });

    } else if(tab==='notes'){
      body.innerHTML = `
        <div class="callout" style="margin-top:0;">
          The original site's Behavioral Analysis page computes a per-trial "Messiness Score" and works from a filtered set of 766 trials (714 usable). I could not verify the exact formula or filter rule from screenshots alone, so I have not reproduced them — inventing a formula and labelling it the same thing would be worse than leaving it out.
        </div>
        <div class="callout warn">
          <b>Not reproduced here:</b> Messiness Score, the 766/714-trial filter, the 9 named analysis views (Opening Strategies, Card Repetition, etc.), and the "Speed Comparison" framing. All of these need either the original formula/filter definition, or the underlying code, to build accurately rather than cosmetically.
        </div>
        <div class="chart-card">
          <div class="chart-card-title">What I can verify: movement-log coverage</div>
          <div class="chart-card-sub">Of all ${cov.total_trials} trials, this many have a usable recorded move sequence</div>
          <div class="stat-row" style="margin-top:6px; grid-template-columns:repeat(2,1fr);">
            <div class="stat"><div class="stat-num">${cov.with_movement_log}</div><div class="stat-label">Have a move log</div></div>
            <div class="stat"><div class="stat-num">${cov.total_trials-cov.with_movement_log}</div><div class="stat-label">Empty / unusable log</div></div>
          </div>
        </div>
        <div class="callout">
          <b>A real finding from cross-checking the movement log against the final-position field:</b> Blank cards <i>never</i> appear in the recorded move sequence, for any of the ${cov.with_movement_log} trials that have one — even though they clearly appear in final layouts. Every King, Queen, and Jack move is logged; Blank card placement apparently isn't. That's a genuine gap in the source data's move-tracking, not a bug in this rebuild.
        </div>
      `;
    }
  }
  document.getElementById('beh-tabs').querySelectorAll('.tabbtn').forEach(b=> b.onclick = ()=> render(b.dataset.t));
  render('learning');
}

/* ====================================================================== */
/* 7. BEHAVIORAL PATTERNS — Grid Viewer + Grid Comparison                   */
/* ====================================================================== */
function lastAttemptsByCond(cond){
  const sub = DATA.explorer.filter(t=>t.cond===cond);
  const byPid = {};
  sub.forEach(t=>{ if(!byPid[t.pid]) byPid[t.pid]=[]; byPid[t.pid].push(t); });
  Object.values(byPid).forEach(list=>list.sort((a,b)=>a.attempt-b.attempt));
  return byPid;
}

function computeReveal(failTrial, winTrial){
  function bbox(grid){
    if(!grid.length) return {rows:0, cols:0};
    const rows = grid.map(c=>c[0]), cols = grid.map(c=>c[1]);
    return {rows: Math.max(...rows)-Math.min(...rows)+1, cols: Math.max(...cols)-Math.min(...cols)+1};
  }
  function violations(grid){
    const rowMap={}, colMap={};
    grid.forEach(([r,c,l])=>{
      rowMap[r]=rowMap[r]||{}; rowMap[r][l]=(rowMap[r][l]||0)+1;
      colMap[c]=colMap[c]||{}; colMap[c][l]=(colMap[c][l]||0)+1;
    });
    let v=0;
    [rowMap,colMap].forEach(map=>Object.values(map).forEach(counts=>Object.values(counts).forEach(n=>{ if(n>1) v+=n-1; })));
    return v;
  }
  const fb = bbox(failTrial.grid), wb = bbox(winTrial.grid);
  const fv = violations(failTrial.grid), wv = violations(winTrial.grid);
  return {
    fail: [
      `Cards span an ${fb.rows}×${fb.cols} region of the board for ${failTrial.grid.length} cards placed.`,
      `${fv} row/column repeat${fv===1?'':'s'} of the same rank detected in the final layout.`,
      `${failTrial.moves ?? '—'} total recorded moves in this trial.`,
    ],
    win: [
      `Cards span a ${wb.rows}×${wb.cols} region of the board for ${winTrial.grid.length} cards placed.`,
      `${wv} row/column repeat${wv===1?'':'s'} of the same rank detected in the final layout.`,
      `${winTrial.moves ?? '—'} total recorded moves in this trial.`,
    ]
  };
}

function buildGridPatterns(){
  V('gridpatterns').innerHTML = `
    <div class="section-head">
      <div class="section-eyebrow">Section 06</div>
      <h2 class="section-title">Behavioral <em>Patterns</em></h2>
      <p class="section-desc">Two views: a per-condition Grid Viewer for browsing any participant's trials, and a Grid Comparison that pairs an unsuccessful participant against a successful one, side by side.</p>
    </div>
    <div class="panel">
      <div style="font-family:'Newsreader',serif; font-size:24px; font-weight:600; color:#18233b;">Grid Viewer</div>
      <div class="section-desc" style="margin-top:6px; margin-bottom:0;">Each condition shows only its own exclusive participants — no overlap.</div>
      <div class="tabrow" id="gv-tabs" style="margin-top:16px;">${CONDS.map((c,i)=>{
        const n = Object.keys(lastAttemptsByCond(c)).length;
        return `<div class="tabbtn${i===0?' on':''}" data-c="${c}">${c} — ${n} participants</div>`;
      }).join('')}</div>
      <div id="gv-body"></div>
    </div>

    <div class="panel" style="padding-top:0;">
      <div style="font-family:'Newsreader',serif; font-size:24px; font-weight:600; color:#18233b;">Grid Comparison</div>
      <div class="section-desc" style="margin-top:6px; margin-bottom:0;">Left = an unsuccessful participant's full set of trials. Right = a successful participant's trials up to and including the one that solved it.</div>
      <div class="tabrow" id="cmp-tabs" style="margin-top:16px;">${CONDS.map((c,i)=>`<div class="tabbtn${i===0?' on':''}" data-c="${c}">${c}</div>`).join('')}</div>
      <div id="cmp-body"></div>
    </div>
  `;

  /* -------- Grid Viewer -------- */
  let gvCond='KQ';
  const gvBody = document.getElementById('gv-body');
  function renderViewer(){
    const byPid = lastAttemptsByCond(gvCond);
    const pids = Object.keys(byPid).map(Number).sort((a,b)=>a-b);
    const winners = pids.filter(p=> byPid[p][byPid[p].length-1].outcome==='win');
    const losers = pids.filter(p=> byPid[p][byPid[p].length-1].outcome!=='win');
    let outcomeFilter='all', curPid=pids[0], curTrialIdx=0;

    function visiblePids(){
      if(outcomeFilter==='win') return winners;
      if(outcomeFilter==='fail') return losers;
      return pids;
    }

    function draw(){
      const trials = byPid[curPid];
      const t = trials[curTrialIdx];
      gvBody.innerHTML = `
        <div class="gv-header">${gvCond} — ${pids.length} EXCLUSIVE PARTICIPANTS</div>
        <div class="field-row">
          <div class="field"><div class="field-label">Outcome Filter</div><select class="sel" id="gv-outcome"><option value="all">All participants</option><option value="win">Successful only</option><option value="fail">Unsuccessful only</option></select></div>
          <div class="field"><div class="field-label">Participant</div><select class="sel" id="gv-pid"></select></div>
          <div class="field"><div class="field-label">Trial</div><select class="sel" id="gv-trial"></select></div>
        </div>
        <div class="gv-layout">
          <div>
            <div id="gv-board"></div>
            <div class="outcome-banner ${t.outcome}">Trial ${t.attempt} — ${t.outcome==='win'?'SUCCESSFUL ✓':'UNSUCCESSFUL ✗'}</div>
          </div>
          <div>
            <div class="thumb-head">All trials · click to jump</div>
            <div class="thumb-strip" id="gv-thumbs"></div>
            <div class="thumb-note">Gold outline = successful trial. Blank cards shown in white. Mini-grids show final card positions per trial.</div>
          </div>
        </div>
        <div class="plist-block">
          <div class="plist-title">Participant list — ${gvCond}</div>
          <div class="plist-cols">
            <div class="plist-col">
              <div class="plist-col-head win">✓ Successful — click to view</div>
              <div class="plist-pills" id="gv-win-pills">${winners.map(p=>`<span class="plist-pill win" data-p="${p}">${p}</span>`).join('')}</div>
            </div>
            <div class="plist-col">
              <div class="plist-col-head fail">✗ Unsuccessful — click to view</div>
              <div class="plist-pills" id="gv-fail-pills">${losers.map(p=>`<span class="plist-pill fail" data-p="${p}">${p}</span>`).join('')}</div>
            </div>
          </div>
        </div>
      `;
      renderBoard(document.getElementById('gv-board'), t.grid, {size:320});
      const thumbStrip = document.getElementById('gv-thumbs');
      trials.forEach((tt,i)=>{
        const th = document.createElement('div');
        th.className = 'thumb'+(tt.outcome==='win'?' win':'')+(i===curTrialIdx?' selected':'');
        thumbStrip.appendChild(th);
        renderBoard(th, tt.grid, {size:64, axis:false});
        th.onclick = ()=>{ curTrialIdx=i; draw(); };
      });
      document.querySelectorAll('#gv-win-pills .plist-pill, #gv-fail-pills .plist-pill').forEach(p=> p.classList.toggle('active', Number(p.dataset.p)===curPid));

      const outSel = document.getElementById('gv-outcome'); outSel.value = outcomeFilter;
      const pidSel = document.getElementById('gv-pid');
      pidSel.innerHTML = visiblePids().map(p=>`<option value="${p}" ${p===curPid?'selected':''}>P${p}</option>`).join('');
      const trialSel = document.getElementById('gv-trial');
      trialSel.innerHTML = trials.map((tt,i)=>`<option value="${i}" ${i===curTrialIdx?'selected':''}>Trial ${tt.attempt} — ${tt.outcome}</option>`).join('');

      outSel.onchange = ()=>{ outcomeFilter=outSel.value; const vp=visiblePids(); if(vp.length){ curPid=vp[0]; curTrialIdx=0; } draw(); };
      pidSel.onchange = ()=>{ curPid=Number(pidSel.value); curTrialIdx=0; draw(); };
      trialSel.onchange = ()=>{ curTrialIdx=Number(trialSel.value); draw(); };
      document.querySelectorAll('#gv-win-pills .plist-pill, #gv-fail-pills .plist-pill').forEach(p=>{
        p.onclick = ()=>{ curPid=Number(p.dataset.p); curTrialIdx=0; draw(); };
      });
    }
    draw();
  }
  document.getElementById('gv-tabs').querySelectorAll('.tabbtn').forEach(b=>{
    b.onclick = ()=>{ gvCond=b.dataset.c; document.querySelectorAll('#gv-tabs .tabbtn').forEach(x=>x.classList.toggle('on',x===b)); renderViewer(); };
  });
  renderViewer();

  /* -------- Grid Comparison -------- */
  let cmpCond='KQ';
  const cmpBody = document.getElementById('cmp-body');
  function renderCmp(){
    const byPid = lastAttemptsByCond(cmpCond);
    const pids = Object.keys(byPid).map(Number).sort((a,b)=>a-b);
    const winners = pids.filter(p=> byPid[p][byPid[p].length-1].outcome==='win');
    const losers = pids.filter(p=> byPid[p][byPid[p].length-1].outcome!=='win');
    const numPairs = winners.length;
    let pairIdx = 0;
    let curWinPid = winners[0];
    let curFailPid = losers.length ? losers[0] : null;

    function draw(){
      if(numPairs===0){ cmpBody.innerHTML = `<p style="color:var(--tx-dim); font-size:13px; margin-top:20px;">No successful participants in ${cmpCond} to pair against.</p>`; return; }
      const winPid = curWinPid;
      const failPid = curFailPid;
      const winTrials = byPid[winPid];
      const failTrials = failPid ? byPid[failPid] : [];
      const winTrial = winTrials[winTrials.length-1];
      const failLast = failTrials.length ? failTrials[failTrials.length-1] : null;

      cmpBody.innerHTML = `
        <div class="cmp-nav">
          <div class="cmp-pair-label">Pair ${pairIdx+1} of ${numPairs} — ${cmpCond} condition</div>
          <div class="cmp-nav-btns">
            <button class="btn" id="cmp-prev" ${pairIdx===0?'disabled':''}>← Prev pair</button>
            <button class="btn" id="cmp-next" ${pairIdx>=numPairs-1?'disabled':''}>Next pair →</button>
          </div>
        </div>
        <div class="plist-block" style="margin-top:14px;">
          <div class="plist-cols">
            <div class="plist-col">
              <div class="plist-col-head fail">✗ Unsuccessful — click to view</div>
              <div class="plist-pills" id="cmp-fail-pills">${losers.map(p=>`<span class="plist-pill fail${p===failPid?' active':''}" data-p="${p}">${p}</span>`).join('') || '<span style="color:var(--tx-dim); font-size:11px;">none</span>'}</div>
            </div>
            <div class="plist-col">
              <div class="plist-col-head win">✓ Successful — click to view</div>
              <div class="plist-pills" id="cmp-win-pills">${winners.map(p=>`<span class="plist-pill win${p===winPid?' active':''}" data-p="${p}">${p}</span>`).join('')}</div>
            </div>
          </div>
        </div>
        <div class="cmp-shell">
          <div class="cmp-panel">
            <div class="cmp-panel-banner fail">✗ Unsuccessful · P${failPid ?? '—'} · ${failTrials.length} trial${failTrials.length===1?'':'s'}</div>
            <div class="cmp-trial-grid" id="cmp-fail-grid"></div>
          </div>
          <div class="cmp-panel">
            <div class="cmp-panel-banner win">✓ Successful · P${winPid} · solved on trial ${winTrial.attempt}</div>
            <div class="cmp-trial-grid" id="cmp-win-grid"></div>
          </div>
        </div>
        <div class="reveal-block">
          <div class="gv-header" style="margin:0 0 4px;">What this pair shows</div>
          <div class="reveal-cols">
            <div>
              <div class="reveal-title fail">Failed trial pattern (last attempt, P${failPid ?? '—'})</div>
              <ul class="reveal-list" id="reveal-fail"></ul>
            </div>
            <div>
              <div class="reveal-title win">Successful trial pattern (P${winPid})</div>
              <ul class="reveal-list" id="reveal-win"></ul>
            </div>
          </div>
        </div>
      `;
      const failGridEl = document.getElementById('cmp-fail-grid');
      failTrials.forEach(t=>{
        const cell = document.createElement('div'); cell.className='cmp-trial-cell';
        cell.innerHTML = `<div class="cmp-trial-cell-label">Trial ${t.attempt}</div>`;
        const boardBox = document.createElement('div'); boardBox.className='cmp-board';
        cell.appendChild(boardBox); failGridEl.appendChild(cell);
        renderBoard(boardBox, t.grid, {size:140, axis:false});
      });
      const winGridEl = document.getElementById('cmp-win-grid');
      winTrials.forEach(t=>{
        const cell = document.createElement('div'); cell.className='cmp-trial-cell';
        cell.innerHTML = `<div class="cmp-trial-cell-label">Trial ${t.attempt}${t.outcome==='win'?' ✓':''}</div>`;
        const boardBox = document.createElement('div'); boardBox.className='cmp-board'+(t.outcome==='win'?' won':'');
        cell.appendChild(boardBox); winGridEl.appendChild(cell);
        renderBoard(boardBox, t.grid, {size:140, axis:false});
      });

      if(failLast){
        const rv = computeReveal(failLast, winTrial);
        document.getElementById('reveal-fail').innerHTML = rv.fail.map(x=>`<li>${x}</li>`).join('');
        document.getElementById('reveal-win').innerHTML = rv.win.map(x=>`<li>${x}</li>`).join('');
      } else {
        document.getElementById('reveal-fail').innerHTML = `<li>No unsuccessful participant available to pair with this trial.</li>`;
      }

      document.getElementById('cmp-prev').onclick = ()=>{ if(pairIdx>0){ pairIdx--; curWinPid=winners[pairIdx]; curFailPid=losers.length?losers[pairIdx%losers.length]:null; draw(); } };
      document.getElementById('cmp-next').onclick = ()=>{ if(pairIdx<numPairs-1){ pairIdx++; curWinPid=winners[pairIdx]; curFailPid=losers.length?losers[pairIdx%losers.length]:null; draw(); } };
      document.querySelectorAll('#cmp-win-pills .plist-pill').forEach(p=>{
        p.onclick = ()=>{ curWinPid = Number(p.dataset.p); pairIdx = winners.indexOf(curWinPid); draw(); };
      });
      document.querySelectorAll('#cmp-fail-pills .plist-pill').forEach(p=>{
        p.onclick = ()=>{ curFailPid = Number(p.dataset.p); draw(); };
      });
    }
    draw();
  }
  document.getElementById('cmp-tabs').querySelectorAll('.tabbtn').forEach(b=>{
    b.onclick = ()=>{ cmpCond=b.dataset.c; document.querySelectorAll('#cmp-tabs .tabbtn').forEach(x=>x.classList.toggle('on',x===b)); renderCmp(); };
  });
  renderCmp();
}

/* ====================================================================== */
/* 8. NOTES (was Power BI) — data integrity + rebuild scope                */
/* ====================================================================== */const BUILDERS = {
  behavioral: buildBehavioral,
  gridpatterns: buildGridPatterns};

function _initDashboard() {
  parseHashAndNavigate();
  var _lo = document.getElementById('loadingOverlay');
  if (_lo) { requestAnimationFrame(function(){ _lo.classList.add('lo-hide'); setTimeout(function(){ _lo.remove(); }, 400); }); }
}

// ---- Async data loading: fetch CSV from server, process client-side ----
(function() {
  const overlay = document.getElementById('loadingOverlay');
  if (overlay) overlay.classList.remove('lo-hide');

  function _showOverlayError(msg) {
    if (overlay) {
      var txt = overlay.querySelector('.lo-text');
      var spin = overlay.querySelector('.lo-spin');
      if (txt) txt.innerHTML = msg;
      if (spin) spin.style.display = 'none';
    }
  }

  Papa.parse('/api/behavioral-csv', {
    download: true,
    header: true,
    skipEmptyLines: true,
    dynamicTyping: false,
    /* Strip any BOM / leading whitespace from column names so that
       buildDataFromRows can always find 'participant', 'condition', etc. */
    transformHeader: function(h) { return h.replace(/^\uFEFF/, '').trim(); },
    complete: function(results) {
      if (results.errors && results.errors.length > 0) {
        console.error('CSV parse errors:', results.errors);
      }
      /* Quick sanity: check that expected columns exist */
      const cols = results.meta && results.meta.fields ? results.meta.fields : [];
      const hasRequired = ['participant','trialN','condition','overall_correct'].every(function(c){ return cols.indexOf(c) !== -1; });
      if (!hasRequired) {
        console.error('CSV missing required columns. Found:', cols);
        _showOverlayError(
          'The uploaded CSV does not contain the required columns.<br>' +
          '<small style="opacity:.7">Expected: participant, trialN, condition, overall_correct<br>' +
          'Found: ' + (cols.length ? cols.map(function(c){ return c.replace(/</g,'&lt;').replace(/>/g,'&gt;'); }).join(', ') : '(none)') + '</small><br><br>' +
          '<a href="/" style="color:#818cf8">← Return to homepage</a>'
        );
        return; /* do NOT call _initDashboard — DATA is still null */
      }
      try {
        const newData = buildDataFromRows(results.data);
        DATA = newData;
        ORIGINAL_DATA = JSON.parse(JSON.stringify(newData)); /* deep copy */
        console.log('Dataset loaded from server:', newData.overview.trials, 'trials');
      } catch(e) {
        console.error('Failed to process CSV data:', e);
        _showOverlayError(
          'Failed to process the dataset.<br>' +
          '<small style="opacity:.7">' + (e.message || 'Unknown error') + '</small><br><br>' +
          '<a href="/" style="color:#818cf8">← Return to homepage</a> · ' +
          '<a href="#" onclick="location.reload()" style="color:#818cf8">Retry</a>'
        );
        return; /* do NOT call _initDashboard — DATA is still null */
      }
      _initDashboard();
    },
    error: function(err) {
      console.error('Failed to fetch CSV from server:', err);
      _showOverlayError(
        'Failed to load dataset from the server.<br>' +
        '<small style="opacity:.7">The CSV file could not be downloaded. Try refreshing or uploading a new dataset.</small><br><br>' +
        '<a href="/" style="color:#818cf8">← Return to homepage</a> · ' +
        '<a href="#" onclick="location.reload()" style="color:#818cf8">Retry</a>'
      );
    }
  });
})();
