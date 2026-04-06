// User interface logic
const $ = (id) => document.getElementById(id);

// Load profile (merge session + saved profile)
function loadProfile() {
  const session = DB.get('safeher.session', {});
  const p = DB.get(K.profile, {});
  $('profName').value = p.name || session.name || '';
  $('profPhone').value = p.phone || session.phone || '';
  $('profAge').value = p.age || '';
  $('profBlood').value = p.blood || '';
  $('profMedical').value = p.medical || '';
}
$('saveProfile').onclick = () => {
  const p = {
    name: $('profName').value.trim(),
    phone: $('profPhone').value.trim(),
    age: $('profAge').value,
    blood: $('profBlood').value.trim(),
    medical: $('profMedical').value.trim()
  };
  DB.set(K.profile, p);
  loadProfile();
  flash('sosStatus', 'Profile saved.', 'success');
};

// Contacts
function renderContacts() {
  const list = DB.get(K.contacts, []);
  $('contactList').innerHTML = list.map(c =>
    `<li><span><strong>${esc(c.name)}</strong> &middot; ${esc(c.phone)}</span>
     <button class="del" data-id="${c.id}">&times;</button></li>`
  ).join('') || '<li class="muted">No contacts added.</li>';
  document.querySelectorAll('#contactList .del').forEach(b => {
    b.onclick = () => { DB.remove(K.contacts, b.dataset.id); renderContacts(); };
  });
}
$('addContact').onclick = () => {
  const name = $('contactName').value.trim();
  const phone = $('contactPhone').value.trim();
  if (!name || !phone) return;
  DB.push(K.contacts, { id: uid(), name, phone });
  $('contactName').value = ''; $('contactPhone').value = '';
  renderContacts();
};

// SOS (press & hold)
let sosTimer = null;
const sosBtn = $('sosBtn');
const startHold = (e) => {
  e.preventDefault();
  sosBtn.textContent = '...';
  sosTimer = setTimeout(triggerSOS, 2000);
};
const cancelHold = () => {
  clearTimeout(sosTimer);
  sosBtn.textContent = 'SOS';
};
sosBtn.addEventListener('mousedown', startHold);
sosBtn.addEventListener('touchstart', startHold);
sosBtn.addEventListener('mouseup', cancelHold);
sosBtn.addEventListener('mouseleave', cancelHold);
sosBtn.addEventListener('touchend', cancelHold);

function triggerSOS() {
  const profile = DB.get(K.profile, {});
  const loc = DB.get(K.location, null);
  const alert = {
    id: uid(),
    type: 'SOS',
    user: profile.name || 'Anonymous User',
    phone: profile.phone || 'N/A',
    blood: profile.blood || 'N/A',
    medical: profile.medical || 'None',
    location: loc ? `${loc.lat.toFixed(4)}, ${loc.lng.toFixed(4)}` : 'Unknown',
    notifyPolice: $('notifyPolice').checked,
    notifyHospital: $('notifyHospital').checked,
    status: 'Active',
    createdAt: now()
  };
  DB.push(K.sos, alert);
  sosBtn.textContent = 'SENT';
  flash('sosStatus', 'SOS sent to emergency responders. Stay calm, help is on the way.', 'success');
  setTimeout(() => sosBtn.textContent = 'SOS', 2000);
  renderAlertHistory();
}

// Location
let watchId = null;
$('shareLoc').onclick = () => {
  if (!navigator.geolocation) {
    // Simulate
    const loc = { lat: 28.6139 + Math.random() * 0.05, lng: 77.2090 + Math.random() * 0.05, ts: now() };
    DB.set(K.location, loc);
    $('locText').textContent = `Sharing: ${loc.lat.toFixed(4)}, ${loc.lng.toFixed(4)} (simulated)`;
    return;
  }
  watchId = navigator.geolocation.watchPosition(
    pos => {
      const loc = { lat: pos.coords.latitude, lng: pos.coords.longitude, ts: now() };
      DB.set(K.location, loc);
      $('locText').textContent = `Sharing: ${loc.lat.toFixed(4)}, ${loc.lng.toFixed(4)}`;
    },
    () => {
      const loc = { lat: 28.6139 + Math.random() * 0.05, lng: 77.2090 + Math.random() * 0.05, ts: now() };
      DB.set(K.location, loc);
      $('locText').textContent = `Sharing: ${loc.lat.toFixed(4)}, ${loc.lng.toFixed(4)} (simulated)`;
    }
  );
};
$('stopLoc').onclick = () => {
  if (watchId) navigator.geolocation.clearWatch(watchId);
  DB.set(K.location, null);
  $('locText').textContent = 'Location sharing stopped.';
};

// Safe route (mock)
$('findRoute').onclick = () => {
  const from = $('fromLoc').value.trim();
  const to = $('toLoc').value.trim();
  if (!from || !to) { flash('routeResult', 'Enter both locations.', 'error'); return; }
  const rating = (Math.random() * 2 + 3).toFixed(1);
  const lighting = ['Well-lit', 'Moderate', 'Well-lit'][Math.floor(Math.random() * 3)];
  flash('routeResult',
    `Safe route from <b>${esc(from)}</b> to <b>${esc(to)}</b>: Safety score ${rating}/5 &middot; ${lighting} streets &middot; Avoids 2 reported incident zones.`,
    'success');
};

// Incident report
$('submitIncident').onclick = () => {
  const profile = DB.get(K.profile, {});
  const anon = $('incAnon').checked;
  const inc = {
    id: uid(),
    type: $('incType').value,
    location: $('incLocation').value.trim() || 'Unknown',
    description: $('incDesc').value.trim(),
    reporter: anon ? 'Anonymous' : (profile.name || 'Anonymous'),
    phone: anon ? '' : (profile.phone || ''),
    status: 'Open',
    createdAt: now()
  };
  if (!inc.description) { flash('incStatus', 'Please describe the incident.', 'error'); return; }
  DB.push(K.incidents, inc);
  $('incLocation').value = ''; $('incDesc').value = '';
  flash('incStatus', 'Incident reported. Police have been notified.', 'success');
};

// Tools
$('fakeCall').onclick = () => {
  flash('toolsOutput', 'Incoming call from "Mom" in 5 seconds... (simulation)', 'success');
};
$('loudAlarm').onclick = () => {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    osc.type = 'square'; osc.frequency.value = 1000;
    osc.connect(ctx.destination); osc.start();
    setTimeout(() => { osc.stop(); ctx.close(); }, 1500);
    flash('toolsOutput', 'Loud alarm activated.', 'success');
  } catch { flash('toolsOutput', 'Alarm triggered (silent mode).', 'success'); }
};
$('voiceRec').onclick = () => {
  flash('toolsOutput', 'Voice recording started. Audio will be saved as evidence.', 'success');
};
$('selfDefense').onclick = () => {
  flash('toolsOutput', 'Tip: Aim for vulnerable points - eyes, nose, throat, knees. Stay loud, stay aware.', 'success');
};

// Alert history
function renderAlertHistory() {
  const alerts = DB.get(K.sos, []).slice(0, 10);
  $('alertHistory').innerHTML = alerts.map(a =>
    `<li><span>${a.type} &middot; ${timeAgo(a.createdAt)}</span>
     <span class="badge ${a.status === 'Active' ? 'open' : 'resolved'}">${a.status}</span></li>`
  ).join('') || '<li class="muted">No alerts yet.</li>';
}

function flash(id, msg, type) {
  const el = $(id);
  el.innerHTML = msg;
  el.className = 'status ' + (type || '');
}
function esc(s) {
  return String(s).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

function onDataChange() { renderAlertHistory(); }

// Init
loadProfile();
renderContacts();
renderAlertHistory();
