// Guest phone app for the robot companion. No libraries, no storage: everything lives in memory (the session id is kept in the URL
// fragment only so a page refresh can resume it; the fragment is never sent to a server).
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const token = params.get('t') || '';
  let ws = null, sessionId = (location.hash.match(/s=([A-Za-z0-9_-]+)/) || [])[1] || null, retry = 0, wantJoin = false;
  let places = [], mapInfo = null, mapImg = null, snap = null, selected = null, firstDist = null, pulse = 0;
  const STATE_TEXT = { IDLE: ['Ready', ''], AWAITING_INPUT: ['Ready', 'ok'], PREPARING: ['Getting ready', 'warn'], NAVIGATING: ['On the way', 'warn'],
    ARRIVED: ['Arrived', 'ok'], BLOCKED: ['Problem', 'bad'], STOPPED: ['Stopped', 'warn'], RETURNING_HOME: ['Returning', ''], HOLD: ['Paused', 'bad'] };

  function chip(text, cls) { const c = $('chip'); c.textContent = text; c.className = 'chip ' + (cls || ''); }
  function send(m) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); }
  function say(text, who) {
    const d = document.createElement('div'); d.className = 'msg ' + who; d.textContent = text; const c = $('chat'); c.appendChild(d);
    while (c.children.length > 40) c.removeChild(c.firstChild);
    c.scrollTop = c.scrollHeight;
    if (who === 'robot' && $('speak').checked && 'speechSynthesis' in window) { try { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance(text)); } catch (e) { /* ignore */ } }
  }

  // ------------------------------------------------------------------ connection
  function connect() {
    ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host);
    ws.onopen = () => { retry = 0; chip('connected', 'ok'); if (sessionId) send({ type: 'resume', session: sessionId }); else if (wantJoin) doJoin(); else { $('joinBtn').disabled = !token; $('joinMsg').textContent = token ? 'Press Start to meet the robot.' : 'Please scan the QR code on the robot.'; } };
    ws.onmessage = (e) => { let m; try { m = JSON.parse(e.data); } catch (x) { return; } onMessage(m); };
    ws.onclose = () => { chip('reconnecting…', 'warn'); setTimeout(connect, Math.min(1000 * ++retry, 5000)); };
  }
  function doJoin() { send({ type: 'join', token: token, nickname: $('nick').value.trim(), lang: (navigator.language || 'en').slice(0, 2) }); }

  function onMessage(m) {
    if (m.type === 'joined') {
      sessionId = m.session; history.replaceState(null, '', location.pathname + location.search + '#s=' + sessionId);
      places = m.places; mapInfo = m.map; $('robotName').textContent = m.robot_name || 'Robot companion';
      $('joinCard').hidden = true; $('ended').hidden = true; $('app').hidden = false;
      if (mapInfo) { mapImg = new Image(); mapImg.onload = draw; mapImg.src = mapInfo.url; }
      renderPlaces(); resizeCanvas(); requestAnimationFrame(loop);
    } else if (m.type === 'join_rejected') {
      sessionId = null; history.replaceState(null, '', location.pathname + location.search);
      $('app').hidden = true; $('joinCard').hidden = false; $('joinMsg').textContent = m.reason; $('joinBtn').disabled = !token; chip('not joined', 'bad');
    } else if (m.type === 'say') { say(m.text, 'robot'); }
    else if (m.type === 'state') { onState(m); }
    else if (m.type === 'session_ended') {
      sessionId = null; history.replaceState(null, '', location.pathname + location.search); $('app').hidden = true; $('ended').hidden = false;
      $('endedMsg').textContent = m.reason || 'Goodbye!'; chip('ended', '');
    } else if (m.type === 'error') { say(m.message, 'robot'); }
  }

  function onState(s) {
    snap = s;
    const t = STATE_TEXT[s.state] || [s.state, '']; chip(t[0], t[1]);
    const moving = s.state === 'NAVIGATING' || s.state === 'PREPARING';
    $('stop').hidden = !moving; $('finish').hidden = moving; $('chatForm').querySelector('button[type=submit]').disabled = s.state === 'HOLD';
    const p = $('progress');
    if (s.state === 'NAVIGATING') {
      p.hidden = false; const lbl = labelOf(s.destination);
      if (s.distance_remaining != null) { if (firstDist == null || s.distance_remaining > firstDist) firstDist = s.distance_remaining; $('barFill').style.width = Math.max(4, Math.round(100 * (1 - s.distance_remaining / Math.max(firstDist, 0.1)))) + '%';
        $('progressText').textContent = 'Heading to ' + lbl + ' — ' + s.distance_remaining.toFixed(1) + ' m to go'; }
      else { $('barFill').style.width = '4%'; $('progressText').textContent = 'Starting towards ' + lbl + '…'; }
    } else { p.hidden = true; firstDist = null; }
    if (s.state !== 'AWAITING_INPUT' && s.state !== 'ARRIVED' && s.state !== 'BLOCKED' && s.state !== 'STOPPED') { selected = null; $('goBar').hidden = true; }
    renderPlaces();
    if (s.robot) { const b = s.robot.battery; if (b != null && b < 0.25) chip((t[0]) + ' · battery ' + Math.round(b * 100) + '%', 'bad'); }
  }
  function labelOf(name) { const p = places.find((x) => x.name === name); return p ? p.label : name; }

  // ------------------------------------------------------------------ places list + selection
  function renderPlaces() {
    const box = $('places'); box.textContent = '';
    const can = snap && ['AWAITING_INPUT', 'ARRIVED', 'BLOCKED', 'STOPPED'].includes(snap.state);
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
  $('goBtn').onclick = () => { if (selected) send({ type: 'select', destination: selected }); selected = null; $('goBar').hidden = true; };
  $('goCancel').onclick = () => { selected = null; $('goBar').hidden = true; draw(); };

  // ------------------------------------------------------------------ chat / buttons / speech
  $('chatForm').onsubmit = (e) => { e.preventDefault(); const v = $('msg').value.trim(); if (!v) return; say(v, 'me'); send({ type: 'say', text: v }); $('msg').value = ''; };
  $('stop').onclick = () => send({ type: 'stop' });
  $('finish').onclick = () => send({ type: 'end' });
  $('joinBtn').onclick = () => { wantJoin = true; $('joinBtn').disabled = true; doJoin(); };
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SR) {
    $('mic').hidden = false; const rec = new SR(); rec.lang = navigator.language || 'en-US'; rec.interimResults = false; rec.maxAlternatives = 1;
    rec.onresult = (e) => { const v = e.results[0][0].transcript; $('msg').value = v; $('chatForm').requestSubmit(); };
    rec.onend = () => $('mic').classList.remove('active'); rec.onerror = () => $('mic').classList.remove('active');
    $('mic').onclick = () => { try { rec.start(); $('mic').classList.add('active'); } catch (e) { /* already listening */ } };
  }

  // ------------------------------------------------------------------ map: places as dots with rings, robot as an arrow
  const cv = $('map'), g = cv.getContext('2d');
  let view = null;                                            // {x0, y0, scale} canvas px per metre; world (x0, y0) = bottom-left
  function resizeCanvas() {
    const w = cv.clientWidth || 320, d = window.devicePixelRatio || 1, h = Math.round(Math.min(w * 0.9, 440));
    cv.width = w * d; cv.height = h * d; cv.style.height = h + 'px'; g.setTransform(d, 0, 0, d, 0, 0); cv._w = w; cv._h = h; fitView(); draw();
  }
  function fitView() {
    if (!places.length) return;
    const xs = places.map((p) => p.x), ys = places.map((p) => p.y); const pad = 1.6;
    let minx = Math.min.apply(null, xs) - pad, maxx = Math.max.apply(null, xs) + pad, miny = Math.min.apply(null, ys) - pad, maxy = Math.max.apply(null, ys) + pad;
    const s = Math.min(cv._w / (maxx - minx), cv._h / (maxy - miny)), cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
    view = { scale: s, x0: cx - cv._w / s / 2, y0: cy - cv._h / s / 2 };
  }
  const W2S = (x, y) => [(x - view.x0) * view.scale, cv._h - (y - view.y0) * view.scale];
  function draw() {
    if (!view || $('app').hidden) return;
    g.clearRect(0, 0, cv._w, cv._h); g.fillStyle = '#cdcdcd'; g.fillRect(0, 0, cv._w, cv._h);
    if (mapImg && mapImg.complete && mapInfo) {                                      // map image: world rect of the canvas -> source pixels
      const m = mapInfo, sx = (view.x0 - m.x0) / m.res, sw = cv._w / view.scale / m.res, sh = cv._h / view.scale / m.res, sy = m.h - (view.y0 - m.y0) / m.res - sh;
      g.imageSmoothingEnabled = false; g.drawImage(mapImg, sx, sy, sw, sh, 0, 0, cv._w, cv._h);
    }
    // 1 m grid hint along the bottom
    const bx = 12, by = cv._h - 12; g.strokeStyle = '#14232b'; g.lineWidth = 3; g.beginPath(); g.moveTo(bx, by); g.lineTo(bx + view.scale, by); g.stroke();
    g.fillStyle = '#14232b'; g.font = '12px system-ui'; g.fillText('1 m', bx, by - 6);
    const boxes = [];                                                                  // label rectangles already placed (collision avoidance)
    const hit = (r) => boxes.some((q) => r.x < q.x + q.w && r.x + r.w > q.x && r.y < q.y + q.h && r.y + r.h > q.y);
    g.font = '600 12px system-ui';
    const pts = places.map((p) => { const [x, y] = W2S(p.x, p.y); return { p: p, x: x, y: y }; });
    pts.forEach((o) => boxes.push({ x: o.x - 16, y: o.y - 16, w: 32, h: 32 }));       // keep labels off the dots
    pts.forEach((o) => {
      const p = o.p, x = o.x, y = o.y, isTarget = snap && snap.destination === p.name, isHere = snap && snap.at === p.name && !isTarget, isSel = selected === p.name;
      const col = isTarget ? '#f08c1d' : isHere ? '#1a9b5a' : '#2563eb';
      g.lineWidth = 3; g.strokeStyle = col; g.globalAlpha = .55; g.beginPath(); g.arc(x, y, isTarget ? 15 + 4 * Math.sin(pulse) : 15, 0, 7); g.stroke(); g.globalAlpha = 1;     // ring
      if (isSel) { g.lineWidth = 2; g.strokeStyle = '#14232b'; g.beginPath(); g.arc(x, y, 21, 0, 7); g.stroke(); }
      g.fillStyle = col; g.strokeStyle = '#fff'; g.lineWidth = 2; g.beginPath(); g.arc(x, y, 7, 0, 7); g.fill(); g.stroke();                                                 // dot
      const lab = p.label, tw = g.measureText(lab).width, th = 14;
      const spots = [[x + 14, y - 14], [x - 14 - tw, y - 14], [x + 14, y + 18], [x - 14 - tw, y + 18], [x - tw / 2, y - 24], [x - tw / 2, y + 30]];
      let pos = spots.find((q) => { const r = { x: q[0] - 2, y: q[1] - th + 2, w: tw + 4, h: th + 2 }; return q[0] >= 2 && q[0] + tw <= cv._w - 2 && q[1] - th >= 0 && q[1] <= cv._h - 2 && !hit(r); }) || spots[0];
      boxes.push({ x: pos[0] - 2, y: pos[1] - th + 2, w: tw + 4, h: th + 2 });
      g.lineWidth = 4; g.strokeStyle = 'rgba(255,255,255,.92)'; g.strokeText(lab, pos[0], pos[1]); g.fillStyle = '#14232b'; g.fillText(lab, pos[0], pos[1]);
    });
    const r = snap && snap.robot && snap.robot.pose;
    if (r && r.frame === 'map') {                                                     // robot arrow
      const [x, y] = W2S(r.x, r.y); g.save(); g.translate(x, y); g.rotate(-r.yaw); g.fillStyle = '#111'; g.strokeStyle = '#fff'; g.lineWidth = 2;
      g.beginPath(); g.moveTo(13, 0); g.lineTo(-9, 8); g.lineTo(-5, 0); g.lineTo(-9, -8); g.closePath(); g.fill(); g.stroke(); g.restore();
    }
  }
  function loop() { pulse += 0.12; draw(); if (!$('app').hidden) requestAnimationFrame(loop); }
  cv.addEventListener('pointerdown', (e) => {
    if (!view || !snap || !['AWAITING_INPUT', 'ARRIVED', 'BLOCKED', 'STOPPED'].includes(snap.state)) return;
    const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top; let best = null, bd = 28;
    places.forEach((p) => { const [x, y] = W2S(p.x, p.y), d = Math.hypot(x - px, y - py); if (d < bd) { bd = d; best = p.name; } });
    if (best) choose(best);
  });
  window.addEventListener('resize', resizeCanvas);

  connect();
})();
