// Biglietto interattivo lato client. Click su una quota = aggiunge leg.
// Click su "Suggerisci AI" = POST /ai-improve, mostra confronto side-by-side.

(function () {
  const STORE = 'betterai.userslip.v1';

  const $ = sel => document.querySelector(sel);
  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined) e.textContent = txt;
    return e;
  };

  function load() {
    try { return JSON.parse(localStorage.getItem(STORE) || '[]'); }
    catch (e) { return []; }
  }
  function save(legs) {
    localStorage.setItem(STORE, JSON.stringify(legs));
  }

  function render() {
    const legs = load();
    const list = $('#ubs-legs'); if (!list) return;
    list.innerHTML = '';
    if (!legs.length) {
      list.appendChild(el('li', 'ubs-empty', 'Clicca una quota qualsiasi per iniziare.'));
    } else {
      legs.forEach((l, i) => {
        const li = el('li', 'ubs-leg');
        const teams = el('div', 'teams', l.teams);
        const row = el('div', 'r');
        row.appendChild(el('span', 'sel', labelOf(l.selection)));
        row.appendChild(el('span', 'odd', '× ' + Number(l.odds).toFixed(2)));
        const rm = el('button', 'rm', '×');
        rm.title = 'Rimuovi';
        rm.addEventListener('click', () => {
          const cur = load();
          cur.splice(i, 1);
          save(cur);
          render();
          applyHighlight();
        });
        li.appendChild(teams); li.appendChild(row); li.appendChild(rm);
        list.appendChild(li);
      });
    }
    const total = legs.reduce((a, l) => a * Number(l.odds), 1);
    $('#ubs-count').textContent = legs.length;
    $('#ubs-total').textContent = '× ' + total.toFixed(2);
    const stake = Number($('#ubs-stake').value || 0);
    $('#ubs-potential').textContent = (total * stake).toFixed(2) + ' €';
  }

  // Etichette umane dai codici selection (mini duplicazione del label python)
  const SEL_LABELS = {
    'R:1': '1', 'R:X': 'X', 'R:2': '2',
    'R:1X': '1X', 'R:X2': 'X2', 'R:12': '12',
    'BTTS:Y': 'GOAL', 'BTTS:N': 'NOGOAL',
    'TG:O1.5': 'OVER 1.5', 'TG:U1.5': 'UNDER 1.5',
    'TG:O2.5': 'OVER 2.5', 'TG:U2.5': 'UNDER 2.5',
    'TG:O3.5': 'OVER 3.5', 'TG:U3.5': 'UNDER 3.5',
  };
  function labelOf(sel) { return SEL_LABELS[sel] || sel; }

  function applyHighlight() {
    const legs = load();
    const picked = new Set(legs.map(l => l.match_id + '|' + l.selection));
    document.querySelectorAll('td.pickable').forEach(td => {
      const tr = td.closest('tr');
      if (!tr) return;
      const key = tr.dataset.mid + '|' + td.dataset.sel;
      td.classList.toggle('picked', picked.has(key));
    });
  }

  function onCellClick(ev) {
    const td = ev.target.closest('td.pickable');
    if (!td) return;
    const tr = td.closest('tr');
    if (!tr || !tr.dataset.mid) return;
    const odds = Number(td.dataset.odds);
    if (!odds || !isFinite(odds)) return;  // "-" / NaN -> ignora
    const match_id = Number(tr.dataset.mid);
    const selection = td.dataset.sel;
    const teams = tr.dataset.teams;
    const cur = load();
    // Un esito per match: sostituisci se esiste
    const idx = cur.findIndex(l => l.match_id === match_id);
    const entry = { match_id, selection, odds, teams };
    if (idx >= 0) {
      if (cur[idx].selection === selection) {
        cur.splice(idx, 1);           // click sullo stesso esito = rimuove
      } else {
        cur[idx] = entry;              // cambio esito
      }
    } else {
      cur.push(entry);
    }
    save(cur);
    render();
    applyHighlight();
  }

  function showCompare(payload) {
    const box = $('#ai-compare');
    if (!box) return;
    box.style.display = 'block';
    box.innerHTML = '';
    const makeCol = (title, side, subtitle) => {
      const c = el('div', 'cmp-col ' + side);
      c.appendChild(el('header', 'cmp-head', title));
      if (subtitle) c.appendChild(el('div', 'cmp-sub muted', subtitle));
      return c;
    };
    const legsHtml = (legs) => {
      const ul = el('ul', 'ubs-legs cmp-legs');
      legs.forEach(l => {
        const li = el('li', 'ubs-leg' + (l.changed ? ' changed' : ''));
        li.appendChild(el('div', 'teams', l.match));
        const r = el('div', 'r');
        r.appendChild(el('span', 'sel', l.label));
        r.appendChild(el('span', 'odd', '× ' + Number(l.odds).toFixed(2)));
        const p = l.prob != null ? Math.round(l.prob * 100) + '%' : '-';
        r.appendChild(el('span', 'muted', 'p ' + p));
        li.appendChild(r);
        ul.appendChild(li);
      });
      return ul;
    };
    const u = payload.user;
    const a = payload.ai;
    const c1 = makeCol('La tua', 'mine',
      'quota × ' + u.total.toFixed(2));
    c1.appendChild(legsHtml(u.legs));
    const act1 = el('div', 'cmp-act');
    const b1 = el('button', 'primary', 'Mantieni la mia');
    b1.addEventListener('click', () => saveSlip(u.legs, 'manuale'));
    act1.appendChild(b1);
    c1.appendChild(act1);

    const c2 = makeCol('Proposta AI', 'ai',
      'quota × ' + a.total.toFixed(2) + ' · ' +
      a.changed + ' leg modificat' + (a.changed === 1 ? 'a' : 'e'));
    c2.appendChild(legsHtml(a.legs));
    if (payload.notes && payload.notes.length) {
      const notes = el('ul', 'cmp-notes');
      payload.notes.forEach(n => notes.appendChild(el('li', '', n)));
      c2.appendChild(notes);
    }
    const act2 = el('div', 'cmp-act');
    const b2 = el('button', 'primary', 'Accetta AI');
    b2.addEventListener('click', () => saveSlip(a.legs.map(l => ({
      match_id: l.match_id, selection: l.selection,
      odds: l.odds, source_id: l.source_id,
    })), 'manuale+ai'));
    act2.appendChild(b2);
    c2.appendChild(act2);

    const cancel = el('button', 'ghost cmp-cancel', 'Annulla');
    cancel.addEventListener('click', () => { box.style.display = 'none'; });

    box.appendChild(c1);
    box.appendChild(c2);
    box.appendChild(cancel);
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function onAI() {
    const legs = load();
    if (!legs.length) { alert('Biglietto vuoto.'); return; }
    const btn = $('#ubs-ai');
    btn.disabled = true; btn.textContent = 'Penso...';
    try {
      const resp = await fetch('/ai-improve', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ legs }),
      });
      const data = await resp.json();
      if (data.error) { alert(data.error); return; }
      showCompare(data);
    } catch (e) {
      alert('Errore: ' + e.message);
    } finally {
      btn.disabled = false; btn.textContent = 'Suggerisci AI →';
    }
  }

  async function saveSlip(legs, strategy) {
    const stake = Number($('#ubs-stake').value || 2);
    try {
      const resp = await fetch('/salva-biglietto', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ legs, strategy, stake }),
      });
      const data = await resp.json();
      if (data.error) { alert(data.error); return; }
      alert('Schedina #' + data.slip_id + ' salvata (quota × ' + data.total + ').');
      localStorage.removeItem(STORE);
      render(); applyHighlight();
      $('#ai-compare').style.display = 'none';
    } catch (e) {
      alert('Errore salvataggio: ' + e.message);
    }
  }

  function init() {
    if (!document.getElementById('userbslip')) return;
    document.body.addEventListener('click', onCellClick);
    $('#ubs-ai').addEventListener('click', onAI);
    $('#ubs-save').addEventListener('click', () => {
      const legs = load();
      if (legs.length) saveSlip(legs, 'manuale');
    });
    $('#ubs-clear').addEventListener('click', () => {
      localStorage.removeItem(STORE);
      render(); applyHighlight();
      $('#ai-compare').style.display = 'none';
    });
    $('#ubs-stake').addEventListener('input', render);
    render();
    applyHighlight();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
