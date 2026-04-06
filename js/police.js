// Police interface logic
const $ = (id) => document.getElementById(id);

function renderSOS() {
  const alerts = DB.get(K.sos, []).filter(a => a.notifyPolice !== false);
  const active = alerts.filter(a => a.status === 'Active');
  $('statActive').textContent = active.length;

  $('sosList').innerHTML = alerts.slice(0, 20).map(a => `
    <li class="alert-item ${a.status === 'Active' ? 'urgent' : ''}">
      <div class="alert-head">
        <strong>SOS - ${esc(a.user)}</strong>
        <span class="time">${timeAgo(a.createdAt)}</span>
      </div>
      <div class="alert-body">
        Phone: ${esc(a.phone)} &middot; Location: ${esc(a.location)}<br/>
        Blood: ${esc(a.blood)} &middot; Medical: ${esc(a.medical)}
      </div>
      <div class="alert-actions">
        <button onclick="respondSOS('${a.id}')">Respond</button>
        <button onclick="resolveSOS('${a.id}')">Mark Resolved</button>
        <span class="badge ${a.status === 'Active' ? 'open' : 'resolved'}">${a.status}</span>
      </div>
    </li>
  `).join('') || '<li class="alert-item"><div class="alert-body">No SOS alerts.</div></li>';
}

function respondSOS(id) {
  DB.update(K.sos, id, { status: 'Responding' });
  renderSOS();
}
function resolveSOS(id) {
  DB.update(K.sos, id, { status: 'Resolved' });
  const resolved = DB.get(K.sos, []).filter(a => a.status === 'Resolved').length;
  $('statResolved').textContent = resolved;
  renderSOS();
}

function renderIncidents() {
  const typeFilter = $('filterType').value;
  const statusFilter = $('filterStatus').value;
  let incidents = DB.get(K.incidents, []);
  if (typeFilter) incidents = incidents.filter(i => i.type === typeFilter);
  if (statusFilter) incidents = incidents.filter(i => i.status === statusFilter);

  $('statIncidents').textContent = DB.get(K.incidents, []).filter(i => i.status === 'Open').length;

  $('incidentList').innerHTML = incidents.slice(0, 30).map(i => `
    <li class="alert-item">
      <div class="alert-head">
        <strong>${esc(i.type)} - ${esc(i.reporter)}</strong>
        <span class="time">${timeAgo(i.createdAt)}</span>
      </div>
      <div class="alert-body">
        Location: ${esc(i.location)}<br/>
        ${esc(i.description)}
        ${i.phone ? '<br/>Contact: ' + esc(i.phone) : ''}
      </div>
      <div class="alert-actions">
        <button onclick="updateIncident('${i.id}','Investigating')">Investigate</button>
        <button onclick="updateIncident('${i.id}','Resolved')">Resolve</button>
        <span class="badge ${i.status.toLowerCase()}">${i.status}</span>
      </div>
    </li>
  `).join('') || '<li class="alert-item"><div class="alert-body">No incident reports.</div></li>';
}

function updateIncident(id, status) {
  DB.update(K.incidents, id, { status });
  renderIncidents();
}

$('filterType').onchange = renderIncidents;
$('filterStatus').onchange = renderIncidents;

// Dispatch
$('dispatchBtn').onclick = () => {
  const unit = $('patrolUnit').value;
  const target = $('patrolTarget').value.trim();
  if (!target) return;
  DB.push(K.dispatch, { id: uid(), unit, target, time: now() });
  $('patrolTarget').value = '';
  renderDispatch();
};

function renderDispatch() {
  const log = DB.get(K.dispatch, []).slice(0, 10);
  $('dispatchLog').innerHTML = log.map(d =>
    `<li><span><strong>${esc(d.unit)}</strong> dispatched to ${esc(d.target)}</span><span class="time">${timeAgo(d.time)}</span></li>`
  ).join('') || '<li class="muted">No dispatches.</li>';
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

function onDataChange() { renderSOS(); renderIncidents(); }

// Auto-refresh every 3 seconds
setInterval(() => { renderSOS(); renderIncidents(); }, 3000);

// Init
renderSOS();
renderIncidents();
renderDispatch();
