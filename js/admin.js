/* ═══════════════════════════════════════════
   Admin App – Analytics, oversight, settings
   ═══════════════════════════════════════════ */

const session = requireRole('admin');
if (session) {
  $('greeting').textContent = 'Admin';
  init();
}

function init() {
  renderOverview();
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
  if (name === 'overview') renderOverview();
  else if (name === 'users') renderUsers();
  else if (name === 'hospitals') renderHospitals();
  else if (name === 'subscriptions') renderSubscriptions();
  else if (name === 'orders') renderAllOrders();
  else if (name === 'inventory') renderAdminInventory();
  else if (name === 'logins') renderLoginLogs();
}

function badgeClass(g) { return g.replace('+','plus').replace('-','minus'); }

// ═══════════════════════════════════════════
//   OVERVIEW
// ═══════════════════════════════════════════

function renderOverview() {
  const users = getUsers().filter(u => u.role === 'user');
  const hospitals = getHospitals();
  const subs = getSubscriptions();
  const activeSubs = subs.filter(s => s.status === 'active' && s.expiresAt > Date.now());
  const orders = getOrders();
  const logs = getLoginLogs();
  const totalRevenue = subs.reduce((sum, s) => sum + s.amount, 0);
  const totalBlood = getInventory().reduce((sum, i) => sum + i.units, 0);

  $('statsGrid').innerHTML = `
    <div class="stat-card"><div class="stat-number">${users.length}</div><div class="stat-label">Retail Users</div></div>
    <div class="stat-card"><div class="stat-number">${hospitals.length}</div><div class="stat-label">Hospitals</div></div>
    <div class="stat-card"><div class="stat-number">${activeSubs.length}</div><div class="stat-label">Active Subscriptions</div></div>
    <div class="stat-card"><div class="stat-number">&#x20B9;${totalRevenue.toLocaleString('en-IN')}</div><div class="stat-label">Total Revenue</div></div>
    <div class="stat-card"><div class="stat-number">${orders.length}</div><div class="stat-label">Total Orders</div></div>
    <div class="stat-card"><div class="stat-number">${totalBlood}</div><div class="stat-label">Total Blood Units</div></div>
    <div class="stat-card"><div class="stat-number">${logs.length}</div><div class="stat-label">Total Logins</div></div>
    <div class="stat-card"><div class="stat-number">${hospitals.filter(h => h.verified).length}</div><div class="stat-label">Verified Hospitals</div></div>
  `;

  // Recent logins
  const recentLogs = logs.slice(-5).reverse();
  $('recentLogins').innerHTML = recentLogs.length ? recentLogs.map(l => `
    <div class="recent-item">
      <span class="recent-name">${esc(l.name)}</span>
      <span class="recent-role badge-sm ${l.role}">${l.role}</span>
      <span class="recent-time">${timeAgo(l.at)}</span>
    </div>`).join('') : '<p class="muted">No logins yet.</p>';

  // Recent orders
  const recentOrd = orders.slice(-5).reverse();
  $('recentOrders').innerHTML = recentOrd.length ? recentOrd.map(o => `
    <div class="recent-item">
      <span class="blood-badge sm ${badgeClass(o.bloodGroup)}">${esc(o.bloodGroup)}</span>
      <span class="recent-name">${esc(o.userName)}</span>
      <span class="status-badge sm status-${o.status}">${o.status}</span>
      <span class="recent-time">${timeAgo(o.createdAt)}</span>
    </div>`).join('') : '<p class="muted">No orders yet.</p>';
}

// ═══════════════════════════════════════════
//   USERS
// ═══════════════════════════════════════════

function renderUsers() {
  const users = getUsers().filter(u => u.role === 'user');
  const tbody = document.querySelector('#usersTable tbody');
  tbody.innerHTML = users.map(u => `
    <tr>
      <td>${esc(u.name)}</td>
      <td>${esc(u.email)}</td>
      <td>${esc(u.phone || '–')}</td>
      <td>${fmtDate(u.createdAt)}</td>
    </tr>`).join('');
}

// ═══════════════════════════════════════════
//   HOSPITALS
// ═══════════════════════════════════════════

function renderHospitals() {
  const hospitals = getHospitals();
  if (!hospitals.length) {
    $('hospitalsList').innerHTML = '<div class="no-results">No hospitals registered.</div>';
    return;
  }

  $('hospitalsList').innerHTML = hospitals.map(h => {
    const sub = getActiveSubscription(h.id);
    const inv = getInventoryByHospital(h.id);
    return `
    <div class="card hosp-admin-card">
      <div class="hosp-admin-header">
        <div>
          <h3>${esc(h.name)}</h3>
          <span class="type-badge">${esc(h.type || 'Hospital')}</span>
          <span class="badge-sm ${h.verified ? 'verified' : 'pending'}">${h.verified ? 'Verified' : 'Pending'}</span>
          ${sub ? `<span class="badge-sm sub-active">${sub.plan}</span>` : '<span class="badge-sm no-sub">No Sub</span>'}
        </div>
        <div>
          ${!h.verified ? `<button class="btn-primary btn-sm" onclick="verifyHospital('${h.id}')">Verify</button>` : `<button class="btn-text btn-sm" onclick="unverifyHospital('${h.id}')">Revoke</button>`}
        </div>
      </div>
      <div class="hospital-meta">
        <span>&#x1F4CD; ${esc(h.address || '')}, ${esc(h.city || '')}</span>
        ${h.phone ? `<span>&#x1F4F1; ${esc(h.phone)}</span>` : ''}
        ${h.contactPerson ? `<span>&#x1F464; ${esc(h.contactPerson)}</span>` : ''}
      </div>
      ${inv.length ? `
      <div class="blood-list" style="margin-top:8px">
        ${inv.map(i => `
          <div class="blood-item compact">
            <div class="blood-badge sm ${badgeClass(i.group)}">${esc(i.group)}</div>
            <span>${i.units} units</span>
            <span class="muted">&#x20B9;${Number(i.pricePerUnit).toLocaleString('en-IN')}/u</span>
          </div>`).join('')}
      </div>` : '<p class="muted" style="margin-top:8px">No inventory</p>'}
    </div>`;
  }).join('');
}

function verifyHospital(id) {
  updateHospital(id, { verified: true });
  renderHospitals();
  renderOverview();
}

function unverifyHospital(id) {
  if (!confirm('Revoke verification for this hospital?')) return;
  updateHospital(id, { verified: false });
  renderHospitals();
  renderOverview();
}

// ═══════════════════════════════════════════
//   SUBSCRIPTIONS
// ═══════════════════════════════════════════

function renderSubscriptions() {
  const subs = getSubscriptions();
  const hospitals = getHospitals();
  const totalRevenue = subs.reduce((sum, s) => sum + s.amount, 0);

  $('totalRevenue').innerHTML = `Total Revenue: <strong>&#x20B9;${totalRevenue.toLocaleString('en-IN')}</strong> from <strong>${subs.length}</strong> subscription(s)`;

  const orders = getOrders();
  const tbody = document.querySelector('#subsTable tbody');
  tbody.innerHTML = subs.map(s => {
    const h = hospitals.find(h => h.id === s.hospitalId);
    const isActive = s.status === 'active' && s.expiresAt > Date.now();
    // Estimate revenue from spread: coordination fee (250) * orders fulfilled by this hospital
    const hospOrders = orders.filter(o => o.supplierHospitalId === s.hospitalId && (o.status === 'delivered' || o.status === 'tested'));
    const spreadRevenue = hospOrders.length * 250;
    return `
    <tr>
      <td>${h ? esc(h.name) : 'Unknown'}</td>
      <td>${esc(s.plan)}</td>
      <td>&#x20B9;${Number(s.amount).toLocaleString('en-IN')}</td>
      <td><span class="status-badge sm ${isActive ? 'status-confirmed' : 'status-rejected'}">${isActive ? 'Active' : 'Expired'}</span></td>
      <td>${fmtDate(s.startedAt)}</td>
      <td>${fmtDate(s.expiresAt)}</td>
      <td>&#x20B9;${spreadRevenue.toLocaleString('en-IN')} (${hospOrders.length} order${hospOrders.length !== 1 ? 's' : ''})</td>
    </tr>`;
  }).join('');
}

// ═══════════════════════════════════════════
//   ALL ORDERS
// ═══════════════════════════════════════════

function renderAllOrders() {
  const orders = getOrders().sort((a, b) => b.createdAt - a.createdAt);
  if (!orders.length) {
    $('allOrdersList').innerHTML = '<div class="no-results">No orders yet.</div>';
    return;
  }

  $('allOrdersList').innerHTML = orders.map(o => `
    <div class="card order-card-admin">
      <div class="order-header">
        <div>
          <span class="blood-badge sm ${badgeClass(o.bloodGroup)}">${esc(o.bloodGroup)}</span>
          <strong>${o.unitsNeeded} unit(s)</strong> – ${esc(o.patientName)}
        </div>
        <span class="status-badge status-${o.status}">${o.status.toUpperCase()}</span>
      </div>
      <div class="order-details">
        <div><span class="label">User:</span> ${esc(o.userName)} (${esc(o.userPhone)})</div>
        <div><span class="label">Admitted at:</span> ${esc(o.admittedHospitalName)}</div>
        <div><span class="label">Supplier:</span> ${esc(o.supplierHospitalName)}</div>
        <div><span class="label">Ordered:</span> ${fmtDate(o.createdAt)}</div>
        <div><span class="label">Last update:</span> ${fmtDate(o.updatedAt)}</div>
        ${o.notes ? `<div><span class="label">Notes:</span> ${esc(o.notes)}</div>` : ''}
      </div>
    </div>`).join('');
}

// ═══════════════════════════════════════════
//   BLOOD STOCK (Admin view – all hospitals)
// ═══════════════════════════════════════════

function renderAdminInventory() {
  const coll = getCollectiveInventory();

  $('adminBloodSummary').innerHTML = BLOOD_GROUPS.map(g => {
    const d = coll[g];
    return `
      <div class="summary-card ${d.totalUnits > 0 ? 'available' : 'empty'}">
        <div class="blood-badge ${badgeClass(g)}">${g}</div>
        <div class="summary-info">
          <span class="summary-units">${d.totalUnits} units</span>
          <span class="summary-providers">${d.providers.length} provider(s)</span>
        </div>
      </div>`;
  }).join('');

  // Per-hospital breakdown
  const hospitals = getHospitals();
  $('adminProviderList').innerHTML = hospitals.map(h => {
    const inv = getInventoryByHospital(h.id);
    if (!inv.length) return '';
    return `
      <div class="card">
        <h3>${esc(h.name)} <span class="type-badge">${esc(h.type || '')}</span> <span class="muted">– ${esc(h.city || '')}</span></h3>
        <div class="blood-list">
          ${inv.map(i => `
            <div class="blood-item compact">
              <div class="blood-badge sm ${badgeClass(i.group)}">${esc(i.group)}</div>
              <span>${i.units} units</span>
              <span class="muted">&#x20B9;${Number(i.pricePerUnit).toLocaleString('en-IN')}/u</span>
              <span class="muted">${timeAgo(i.updatedAt)}</span>
            </div>`).join('')}
        </div>
      </div>`;
  }).join('');
}

// ═══════════════════════════════════════════
//   LOGIN LOGS
// ═══════════════════════════════════════════

function renderLoginLogs() {
  const logs = getLoginLogs().slice().reverse();
  const tbody = document.querySelector('#loginsTable tbody');
  tbody.innerHTML = logs.map(l => `
    <tr>
      <td>${esc(l.name)}</td>
      <td>${esc(l.email)}</td>
      <td><span class="badge-sm ${l.role}">${l.role}</span></td>
      <td>${fmtDate(l.at)}</td>
    </tr>`).join('');
}

// ═══════════════════════════════════════════
//   SETTINGS
// ═══════════════════════════════════════════

function saveAdminSettings() {
  alert('Settings saved!');
}

function handleResetData() {
  if (!confirm('This will wipe ALL data and re-seed demo data. Are you sure?')) return;
  Object.values(DB_KEYS).forEach(k => localStorage.removeItem(k));
  seedAll();
  alert('Demo data restored. Reloading...');
  window.location.reload();
}
