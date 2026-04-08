/* ═══════════════════════════════════════════
   User App – Search blood, place orders
   ═══════════════════════════════════════════ */

const session = requireRole('user');
if (session) {
  $('greeting').textContent = 'Hi, ' + session.name;
  init();
}

function init() {
  renderSummary();
  renderProviders();
  populateOrderDropdowns();
  $('oPhone').value = session.phone || '';
  $('filterGroup').addEventListener('change', renderProviders);
  $('filterCity').addEventListener('input', renderProviders);
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
  if (name === 'myorders') renderMyOrders();
  if (name === 'search') { renderSummary(); renderProviders(); }
}

// ═══════════════════════════════════════════
//   SEARCH – Collective Blood Availability
// ═══════════════════════════════════════════

function badgeClass(g) { return g.replace('+','plus').replace('-','minus'); }

function renderSummary() {
  const coll = getCollectiveInventory();
  $('bloodSummary').innerHTML = BLOOD_GROUPS.map(g => {
    const d = coll[g];
    return `
      <div class="summary-card ${d.totalUnits > 0 ? 'available' : 'empty'}">
        <div class="blood-badge ${badgeClass(g)}">${g}</div>
        <div class="summary-info">
          <span class="summary-units">${d.totalUnits} units</span>
          <span class="summary-providers">${d.providers.length} provider${d.providers.length !== 1 ? 's' : ''}</span>
        </div>
      </div>`;
  }).join('');
}

function renderProviders() {
  const coll = getCollectiveInventory();
  const groupFilter = $('filterGroup').value;
  const cityFilter = $('filterCity').value.trim().toLowerCase();

  // Build a flat list of providers with their blood
  let providers = [];
  const hospitals = getHospitals().filter(h => h.verified);

  hospitals.forEach(h => {
    const sub = getActiveSubscription(h.id);
    if (!sub) return;
    if (cityFilter && !h.city.toLowerCase().includes(cityFilter)) return;

    const inv = getInventoryByHospital(h.id).filter(i => i.units > 0);
    const filtered = groupFilter ? inv.filter(i => i.group === groupFilter) : inv;
    if (!filtered.length) return;

    providers.push({ hospital: h, inventory: filtered });
  });

  if (!providers.length) {
    $('providerList').innerHTML = '<div class="no-results">No blood available matching your filters.</div>';
    return;
  }

  $('providerList').innerHTML = providers.map(p => `
    <div class="hospital-card">
      <div class="hospital-header">
        <h3>${esc(p.hospital.name)}</h3>
        <span class="type-badge">${esc(p.hospital.type || 'Hospital')}</span>
      </div>
      <div class="hospital-meta">
        <span>&#x1F4CD; ${esc(p.hospital.address || '')}, ${esc(p.hospital.city)}</span>
        ${p.hospital.phone ? `<span>&#x1F4F1; ${esc(p.hospital.phone)}</span>` : ''}
        ${p.hospital.hours ? `<span>&#x1F552; ${esc(p.hospital.hours)}</span>` : ''}
      </div>
      <div class="blood-list">
        ${p.inventory.map(i => `
          <div class="blood-item">
            <div class="blood-badge ${badgeClass(i.group)}">${esc(i.group)}</div>
            <div class="blood-info">
              <span class="units">${i.units} units available</span>
              <span class="updated">Updated ${timeAgo(i.updatedAt)}</span>
            </div>
            <span class="blood-price">&#x20B9;${Number(i.pricePerUnit).toLocaleString('en-IN')}/unit</span>
          </div>`).join('')}
      </div>
      <div style="margin-top:12px">
        <button class="btn-primary btn-sm" onclick="quickOrder('${p.hospital.id}','${esc(p.hospital.name)}')">Order from here</button>
      </div>
    </div>`).join('');
}

function quickOrder(hospId, hospName) {
  switchTab('order');
  $('oSupplier').value = hospId;
}

// ═══════════════════════════════════════════
//   PLACE ORDER
// ═══════════════════════════════════════════

function populateOrderDropdowns() {
  const allHospitals = getHospitals();
  const verified = allHospitals.filter(h => h.verified && getActiveSubscription(h.id));

  // Admitted hospital: can be any hospital (patient could be admitted anywhere)
  $('oAdmittedHospital').innerHTML = '<option value="">Select hospital</option>' +
    allHospitals.map(h => `<option value="${h.id}">${esc(h.name)} – ${esc(h.city)}</option>`).join('');

  // Supplier: only verified hospitals with active subscription
  $('oSupplier').innerHTML = '<option value="">Select blood bank / hospital</option>' +
    verified.map(h => `<option value="${h.id}">${esc(h.name)} – ${esc(h.city)}</option>`).join('');
}

function handlePlaceOrder() {
  const group = $('oGroup').value;
  const units = parseInt($('oUnits').value);
  const patient = $('oPatient').value.trim();
  const phone = $('oPhone').value.trim();
  const admittedId = $('oAdmittedHospital').value;
  const supplierId = $('oSupplier').value;

  if (!group || !units || !patient || !phone || !admittedId || !supplierId) {
    showAlert('orderError', 'Please fill in all required fields.');
    return;
  }

  const allH = getHospitals();
  const admittedH = allH.find(h => h.id === admittedId);
  const supplierH = allH.find(h => h.id === supplierId);

  const order = placeOrder({
    userId: session.userId,
    userName: session.name,
    userPhone: phone,
    bloodGroup: group,
    unitsNeeded: units,
    patientName: patient,
    admittedHospitalId: admittedId,
    admittedHospitalName: admittedH ? admittedH.name : '',
    supplierHospitalId: supplierId,
    supplierHospitalName: supplierH ? supplierH.name : '',
    notes: $('oNotes').value.trim()
  });

  showAlert('orderSuccess', 'Order placed successfully! Order ID: ' + order.id + '. The hospital will coordinate blood sampling & testing.');
  // Reset form
  $('oPatient').value = '';
  $('oGroup').value = '';
  $('oUnits').value = '1';
  $('oAdmittedHospital').value = '';
  $('oSupplier').value = '';
  $('oNotes').value = '';
}

// ═══════════════════════════════════════════
//   MY ORDERS
// ═══════════════════════════════════════════

function renderMyOrders() {
  const orders = getOrdersByUser(session.userId).sort((a, b) => b.createdAt - a.createdAt);
  if (!orders.length) {
    $('myOrdersList').innerHTML = '<div class="no-results">No orders yet. Go to "Order Blood" to place your first order.</div>';
    return;
  }

  $('myOrdersList').innerHTML = orders.map(o => `
    <div class="card order-status-card">
      <div class="order-header">
        <div>
          <span class="blood-badge sm ${badgeClass(o.bloodGroup)}">${esc(o.bloodGroup)}</span>
          <strong>${o.unitsNeeded} unit(s)</strong> for <strong>${esc(o.patientName)}</strong>
        </div>
        <span class="status-badge status-${o.status}">${o.status.toUpperCase()}</span>
      </div>
      <div class="order-details">
        <div><span class="label">Admitted at:</span> ${esc(o.admittedHospitalName)}</div>
        <div><span class="label">Blood from:</span> ${esc(o.supplierHospitalName)}</div>
        <div><span class="label">Ordered:</span> ${fmtDate(o.createdAt)}</div>
        ${o.notes ? `<div><span class="label">Notes:</span> ${esc(o.notes)}</div>` : ''}
      </div>
      <div class="order-timeline">
        <h4>Status Timeline</h4>
        ${o.history.map(h => `
          <div class="timeline-item">
            <span class="timeline-dot status-${h.status}"></span>
            <span class="timeline-status">${h.status}</span>
            <span class="timeline-time">${fmtDate(h.at)}</span>
            ${h.note ? `<span class="timeline-note">– ${esc(h.note)}</span>` : ''}
          </div>`).join('')}
      </div>
    </div>`).join('');
}

// ── Helpers ──
function showAlert(id, msg) {
  const el = $(id);
  el.textContent = msg;
  el.style.display = '';
  setTimeout(() => el.style.display = 'none', 6000);
}
