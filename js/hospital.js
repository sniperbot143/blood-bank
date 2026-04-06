// Hospital interface logic
const $ = (id) => document.getElementById(id);

function renderEmergencies() {
  const alerts = DB.get(K.sos, []).filter(a => a.notifyHospital !== false);
  const active = alerts.filter(a => a.status === 'Active' || a.status === 'Responding');
  $('statEmergencies').textContent = active.length;

  $('emergencyList').innerHTML = alerts.slice(0, 20).map(a => `
    <li class="alert-item ${a.status === 'Active' ? 'urgent' : ''}">
      <div class="alert-head">
        <strong>Emergency - ${esc(a.user)}</strong>
        <span class="time">${timeAgo(a.createdAt)}</span>
      </div>
      <div class="alert-body">
        Phone: ${esc(a.phone)} &middot; Location: ${esc(a.location)}<br/>
        Blood Group: ${esc(a.blood)} &middot; Medical Info: ${esc(a.medical)}
      </div>
      <div class="alert-actions">
        <button onclick="dispatchToEmergency('${a.id}', '${esc(a.location)}')">Send Ambulance</button>
        <button onclick="markTreated('${a.id}')">Mark Treated</button>
        <span class="badge ${a.status === 'Active' ? 'open' : a.status === 'Resolved' ? 'resolved' : 'investigating'}">${a.status}</span>
      </div>
    </li>
  `).join('') || '<li class="alert-item"><div class="alert-body">No medical emergencies.</div></li>';
}

function dispatchToEmergency(sosId, location) {
  const unit = $('ambUnit').value;
  DB.push(K.ambulance, { id: uid(), unit, target: location, sosId, time: now() });
  DB.update(K.sos, sosId, { status: 'Responding' });
  const free = Math.max(0, parseInt($('statAmbulances').textContent) - 1);
  $('statAmbulances').textContent = free;
  renderEmergencies();
  renderAmbLog();
}

function markTreated(sosId) {
  DB.update(K.sos, sosId, { status: 'Resolved' });
  const treated = parseInt($('statTreated').textContent) + 1;
  $('statTreated').textContent = treated;
  renderEmergencies();
  renderPatients();
}

// Ambulance dispatch (manual)
$('dispatchAmb').onclick = () => {
  const unit = $('ambUnit').value;
  const target = $('ambTarget').value.trim();
  if (!target) return;
  const patient = $('ambPatient').value.trim();
  DB.push(K.ambulance, { id: uid(), unit, target, patient: patient || 'Unknown', time: now() });
  $('ambTarget').value = ''; $('ambPatient').value = '';
  const free = Math.max(0, parseInt($('statAmbulances').textContent) - 1);
  $('statAmbulances').textContent = free;
  renderAmbLog();
};

function renderAmbLog() {
  const log = DB.get(K.ambulance, []).slice(0, 10);
  $('ambLog').innerHTML = log.map(d =>
    `<li><span><strong>${esc(d.unit)}</strong> &rarr; ${esc(d.target)}${d.patient ? ' (Patient: ' + esc(d.patient) + ')' : ''}</span><span class="time">${timeAgo(d.time)}</span></li>`
  ).join('') || '<li class="muted">No dispatches yet.</li>';
}

function renderPatients() {
  const alerts = DB.get(K.sos, []).filter(a => a.notifyHospital !== false);
  $('patientList').innerHTML = alerts.filter(a => a.status === 'Resolved' || a.status === 'Responding').map(a => `
    <li class="alert-item">
      <div class="alert-head">
        <strong>${esc(a.user)}</strong>
        <span class="badge ${a.status === 'Resolved' ? 'resolved' : 'investigating'}">${a.status === 'Resolved' ? 'Treated' : 'In Transit'}</span>
      </div>
      <div class="alert-body">
        Phone: ${esc(a.phone)} &middot; Blood: ${esc(a.blood)} &middot; Medical: ${esc(a.medical)}
      </div>
    </li>
  `).join('') || '<li class="alert-item"><div class="alert-body">No patient records yet.</div></li>';
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

function onDataChange() { renderEmergencies(); renderPatients(); }

// Auto-refresh every 3 seconds
setInterval(() => { renderEmergencies(); renderPatients(); }, 3000);

// Init
renderEmergencies();
renderAmbLog();
renderPatients();
