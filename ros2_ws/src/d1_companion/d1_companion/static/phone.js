// DEVELOPER TEST CLIENT for the companion HTTP API (docs/companion_api.md). The real guest UI is the React app in frontend/.
// Uses exactly what that app would use: POST /api/session, GET /api/state (polling), POST /api/requests, POST /api/robot/stop, DELETE /api/session.
// Draws only the LAYOUT of the destinations (dots + rings, robot arrow): the floor plan / lidar map is never sent to a guest. Memory only: no storage.
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const token = new URLSearchParams(location.search).get('t') || '';
  let access = null, places = [], snap = null, seq = 0, selected = null, firstDist = null, pulse = 0, pollTimer = null, counter = 0;
  const STATE_TEXT = { IDLE: ['Ready', ''], AWAITING_INPUT: ['Ready', 'ok'], PREPARING: ['Getting ready', 'warn'], NAVIGATING: ['On the way', 'warn'],
    ARRIVED: ['Arrived', 'ok'], BLOCKED: ['Problem', 'bad'], STOPPED: ['Stopped', 'warn'], RETURNING_HOME: ['Returning', ''], HOLD: ['Paused', 'bad'] };
  const INPUT = ['AWAITING_INPUT', 'ARRIVED', 'BLOCKED', 'STOPPED'];

  function chip(text, cls) { const c = $('chip'); c.textContent = text; c.className = 'chip ' + (cls || ''); }
  async function api(method, path, body) {
    const h = { 'Content-Type': 'application/json' }; if (access) h.Authorization = 'Bearer ' + access;
    const r = await fetch(path, { method: method, headers: h, body: body ? JSON.stringify(body) : undefined, cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer' });
    let j = null; try { j = await r.json(); } catch (e) { /* empty */ }
    return { status: r.status, body: j };
  }
  function say(text, who) {
    const d = document.createElement('div'); d.className = 'msg ' + who; d.textContent = text; const c = $('chat'); c.appendChild(d);
    while (c.children.length > 40) c.removeChild(c.firstChild);
    c.scrollTop = c.scrollHeight;
    if (who === 'robot' && $('speak').checked && 'speechSynthesis' in window) { try { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance(text)); } catch (e) { /* ignore */ } }
  }
  function ended(text) {
    clearTimeout(pollTimer); access = null; snap = null; $('app').hidden = true; $('ended').hidden = false; $('endedMsg').textContent = text; chip('ended', '');
  }

  // ------------------------------------------------------------------ session
  $('joinBtn').disabled = !token; $('joinMsg').textContent = token ? 'Press Start to meet the robot.' : 'Please scan the QR code on the robot.'; chip('ready', '');
  $('joinBtn').onclick = async () => {
    $('joinBtn').disabled = true;
    const r = await api('POST', '/api/session', { token: token, nickname: $('nick').value.trim(), lang: (navigator.language || 'en').slice(0, 2) });
    if (r.status !== 201) { $('joinMsg').textContent = (r.body && r.body.message) || 'Could not start a session.'; $('joinBtn').disabled = !token; chip('not joined', 'bad'); return; }
    access = r.body.accessToken; places = r.body.places; $('robotName').textContent = r.body.robot.name || 'Robot companion';
    $('joinCard').hidden = true; $('app').hidden = false; renderPlaces(); resizeCanvas(); requestAnimationFrame(loop); poll();
  };
  async function poll() {
    if (!access) return;
    const r = await api('GET', '/api/state?after=' + seq);
    if (r.status === 401) { ended('Your session has ended or expired.'); return; }
    if (r.status === 200) { r.body.messages.forEach((m) => say(m.text, 'robot')); seq = r.body.seq; onState(r.body); }
    pollTimer = setTimeout(poll, snap && snap.state === 'NAVIGATING' ? 600 : 1000);
  }

  function onState(s) {
    snap = s; const t = STATE_TEXT[s.state] || [s.state, '']; chip(s.robot.available ? t[0] : 'Paused', s.robot.available ? t[1] : 'bad');
    const moving = s.state === 'NAVIGATING' || s.state === 'PREPARING';
    $('stop').hidden = !moving; $('finish').hidden = moving;
    const p = $('progress');
    if (s.state === 'NAVIGATING') {
      p.hidden = false; const lbl = labelOf(s.destination);
      if (s.distance_remaining != null) { if (firstDist == null || s.distance_remaining > firstDist) firstDist = s.distance_remaining;
        $('barFill').style.width = Math.max(4, Math.round(100 * (1 - s.distance_remaining / Math.max(firstDist, 0.1)))) + '%';
        $('progressText').textContent = 'Heading to ' + lbl + ' — ' + s.distance_remaining.toFixed(1) + ' m to go'; }
      else { $('barFill').style.width = '4%'; $('progressText').textContent = 'Starting towards ' + lbl + '…'; }
    } else { p.hidden = true; firstDist = null; }
    if (!INPUT.includes(s.state)) { selected = null; $('goBar').hidden = true; }
    renderPlaces();
  }
  function labelOf(name) { const p = places.find((x) => x.name === name); return p ? p.label : name; }

  // ------------------------------------------------------------------ requests (the frontend's task model)
  async function request(text, proposed, place) {
    const cid = 'dev-' + Date.now().toString(36) + '-' + (++counter);
    const body = { text: text, source: 'typed', proposed_action: proposed || null, client_request_id: cid }; if (place) body.place = place;
    const r = await api('POST', '/api/requests', body);
    if (r.status === 401) { ended('Your session has ended or expired.'); return; }
    if (r.status >= 400) { say('Sorry, that did not work (' + ((r.body && r.body.error) || r.status) + ').', 'robot'); return; }
    const t = r.body;
    if (t.status === 'completed' && t.action !== 'GO_TO_PLACE') say(t.reply, 'robot');                  // instant answers; escorts are narrated through /api/state
    else if (t.status === 'denied' || t.status === 'failed') say(t.message || 'I cannot do that.', 'robot');
    if (t.action === 'END_SESSION') ended(t.reply);
    return t;
  }
  $('chatForm').onsubmit = (e) => { e.preventDefault(); const v = $('msg').value.trim(); if (!v) return; say(v, 'me'); request(v); $('msg').value = ''; };
  $('stop').onclick = async () => { const r = await api('POST', '/api/robot/stop'); if (r.status === 401) ended('Your session has ended or expired.'); };
  $('finish').onclick = async () => { const r = await api('DELETE', '/api/session'); ended((r.body && r.body.reply) || 'Goodbye!'); };

  function renderPlaces() {
    const box = $('places'); box.textContent = ''; const can = snap && INPUT.includes(snap.state);
    places.forEach((p) => {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = p.label; b.disabled = !can;
      if (snap && snap.destination === p.name) b.className = 'active'; if (snap && snap.at === p.name) b.className = 'here';
      b.onclick = () => choose(p.name); box.appendChild(b);
    });
  }
  function choose(name) {
    if (snap && snap.at === name && snap.state !== 'BLOCKED') { say('We are already at ' + labelOf(name) + '.', 'robot'); return; }
    selected = name; $('goLabel').textContent = 'Go to ' + labelOf(name) + '?'; $('goBar').hidden = false; draw();
  }
  $('goBtn').onclick = () => { if (!selected) return; const lbl = labelOf(selected), n = selected; selected = null; $('goBar').hidden = true; say('Take me to ' + lbl, 'me'); request('Take me to ' + lbl, 'GO_TO_PLACE', n); };
  $('goCancel').onclick = () => { selected = null; $('goBar').hidden = true; draw(); };
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SR) {
    $('mic').hidden = false; const rec = new SR(); rec.lang = navigator.language || 'en-US'; rec.interimResults = false; rec.maxAlternatives = 1;
    rec.onresult = (e) => { $('msg').value = e.results[0][0].transcript; $('chatForm').requestSubmit(); };
    rec.onend = () => $('mic').classList.remove('active'); rec.onerror = () => $('mic').classList.remove('active');
    $('mic').onclick = () => { try { rec.start(); $('mic').classList.add('active'); } catch (e) { /* already listening */ } };
  }

  // ------------------------------------------------------------------ layout of the destinations (no map): dots with rings, robot arrow
  const cv = $('map'), g = cv.getContext('2d'); let view = null;
  function resizeCanvas() {
    const w = cv.clientWidth || 320, d = window.devicePixelRatio || 1, h = Math.round(Math.min(w * 0.9, 440));
    cv.width = w * d; cv.height = h * d; cv.style.height = h + 'px'; g.setTransform(d, 0, 0, d, 0, 0); cv._w = w; cv._h = h; fitView(); draw();
  }
  function fitView() {
    if (!places.length) return;
    const xs = places.map((p) => p.x), ys = places.map((p) => p.y), pad = 1.2;
    const minx = Math.min.apply(null, xs) - pad, maxx = Math.max.apply(null, xs) + pad, miny = Math.min.apply(null, ys) - pad, maxy = Math.max.apply(null, ys) + pad;
    const s = Math.min(cv._w / (maxx - minx), cv._h / (maxy - miny)), cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
    view = { scale: s, x0: cx - cv._w / s / 2, y0: cy - cv._h / s / 2 };
  }
  const W2S = (x, y) => [(x - view.x0) * view.scale, cv._h - (y - view.y0) * view.scale];
  function draw() {
    if (!view || $('app').hidden) return;
    g.clearRect(0, 0, cv._w, cv._h); g.fillStyle = '#eef3f5'; g.fillRect(0, 0, cv._w, cv._h);
    g.strokeStyle = 'rgba(20,35,43,.08)'; g.lineWidth = 1;                                              // 1 m grid, only to give a sense of scale
    for (let x = Math.ceil(view.x0); x < view.x0 + cv._w / view.scale; x++) { const sx = W2S(x, 0)[0]; g.beginPath(); g.moveTo(sx, 0); g.lineTo(sx, cv._h); g.stroke(); }
    for (let y = Math.ceil(view.y0); y < view.y0 + cv._h / view.scale; y++) { const sy = W2S(0, y)[1]; g.beginPath(); g.moveTo(0, sy); g.lineTo(cv._w, sy); g.stroke(); }
    const rb = snap && snap.robot && snap.robot.x != null ? snap.robot : null;
    const tgt = snap && snap.destination ? places.find((p) => p.name === snap.destination) : null;
    if (rb && tgt) { const a = W2S(rb.x, rb.y), b = W2S(tgt.x, tgt.y); g.setLineDash([6, 6]); g.strokeStyle = 'rgba(240,140,29,.8)'; g.lineWidth = 2; g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke(); g.setLineDash([]); }
    const boxes = [], hit = (r) => boxes.some((q) => r.x < q.x + q.w && r.x + r.w > q.x && r.y < q.y + q.h && r.y + r.h > q.y);
    g.font = '600 12px system-ui'; const pts = places.map((p) => { const s = W2S(p.x, p.y); return { p: p, x: s[0], y: s[1] }; });
    pts.forEach((o) => boxes.push({ x: o.x - 16, y: o.y - 16, w: 32, h: 32 }));
    pts.forEach((o) => {
      const p = o.p, x = o.x, y = o.y, isT = snap && snap.destination === p.name, isH = snap && snap.at === p.name && !isT, isS = selected === p.name;
      const col = isT ? '#f08c1d' : isH ? '#1a9b5a' : '#2563eb';
      g.lineWidth = 3; g.strokeStyle = col; g.globalAlpha = .55; g.beginPath(); g.arc(x, y, isT ? 15 + 4 * Math.sin(pulse) : 15, 0, 7); g.stroke(); g.globalAlpha = 1;
      if (isS) { g.lineWidth = 2; g.strokeStyle = '#14232b'; g.beginPath(); g.arc(x, y, 21, 0, 7); g.stroke(); }
      g.fillStyle = col; g.strokeStyle = '#fff'; g.lineWidth = 2; g.beginPath(); g.arc(x, y, 7, 0, 7); g.fill(); g.stroke();
      const lab = p.label, tw = g.measureText(lab).width, th = 14;
      const spots = [[x + 14, y - 14], [x - 14 - tw, y - 14], [x + 14, y + 18], [x - 14 - tw, y + 18], [x - tw / 2, y - 24], [x - tw / 2, y + 30]];
      const pos = spots.find((q) => { const r = { x: q[0] - 2, y: q[1] - th + 2, w: tw + 4, h: th + 2 }; return q[0] >= 2 && q[0] + tw <= cv._w - 2 && q[1] - th >= 0 && q[1] <= cv._h - 2 && !hit(r); }) || spots[0];
      boxes.push({ x: pos[0] - 2, y: pos[1] - th + 2, w: tw + 4, h: th + 2 });
      g.lineWidth = 4; g.strokeStyle = 'rgba(255,255,255,.92)'; g.strokeText(lab, pos[0], pos[1]); g.fillStyle = '#14232b'; g.fillText(lab, pos[0], pos[1]);
    });
    if (rb) { const s = W2S(rb.x, rb.y); g.save(); g.translate(s[0], s[1]); g.rotate(-rb.yaw); g.fillStyle = '#111'; g.strokeStyle = '#fff'; g.lineWidth = 2;
      g.beginPath(); g.moveTo(13, 0); g.lineTo(-9, 8); g.lineTo(-5, 0); g.lineTo(-9, -8); g.closePath(); g.fill(); g.stroke(); g.restore(); }
  }
  function loop() { pulse += 0.12; draw(); if (!$('app').hidden) requestAnimationFrame(loop); }
  cv.addEventListener('pointerdown', (e) => {
    if (!view || !snap || !INPUT.includes(snap.state)) return;
    const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top; let best = null, bd = 28;
    places.forEach((p) => { const s = W2S(p.x, p.y), d = Math.hypot(s[0] - px, s[1] - py); if (d < bd) { bd = d; best = p.name; } });
    if (best) choose(best);
  });
  window.addEventListener('resize', resizeCanvas);
})();
