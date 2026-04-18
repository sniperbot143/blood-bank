/* ═══════════════════════════════════════════
   Hospital App – Manage inventory & orders
   ═══════════════════════════════════════════ */

const session = requireRole('hospital');
let hospital = null;

if (session) {
  $('greeting').textContent = session.name;
  hospital = getHospitalByUserId(session.userId);
  if (!hospital) {
    // Hospital profile doesn't exist yet – create skeleton
    hospital = registerHospital({ userId: session.userId, name: session.name + "'s Hospital", type: 'Hospital', city: '', address: '', phone: session.phone || '', contactPerson: session.name });
  }
  init();
}

function init() {
  renderInventory();
  renderOrders();
  loadProfile();
  renderSubscription();
  checkSubWarning();
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
  if (name === 'orders') renderOrders();
  if (name === 'inventory') renderInventory();
  if (name === 'subscription') renderSubscription();
}

function badgeClass(g) { return g.replace('+','plus').replace('-','minus'); }

function checkSubWarning() {
  const sub = getActiveSubscription(hospital.id);
  $('subWarning').style.display = sub ? 'none' : '';
}

// ═══════════════════════════════════════════
//   BLOOD INVENTORY
// ═══════════════════════════════════════════

function renderInventory() {
  const inv = getInventoryByHospital(hospital.id);
  if (!inv.length) {
    $('invGrid').innerHTML = '<div class="no-results">No blood stock added yet. Use the form below to add stock.</div>';
    return;
  }
  $('invGrid').innerHTML = inv.map(i => `
    <div class="inv-card">
      <div class="inv-card-top">
        <div class="blood-badge ${badgeClass(i.group)}">${esc(i.group)}</div>
        <button class="btn-remove-stock" onclick="handleRemoveStock('${esc(i.group)}')" title="Remove">&#x2716;</button>
      </div>
      <div class="inv-stat"><span class="inv-label">Units</span><span class="inv-value">${i.units}</span></div>
      <div class="inv-stat"><span class="inv-label">Price</span><span class="inv-value">&#x20B9;${Number(i.pricePerUnit).toLocaleString('en-IN')}</span></div>
      <div class="inv-updated">Updated ${timeAgo(i.updatedAt)}</div>
    </div>`).join('');
}

function handleAddStock() {
  const group = $('invGroup').value;
  const units = parseInt($('invUnits').value);
  const price = parseInt($('invPrice').value);
  if (isNaN(units) || units < 0) { alert('Enter valid units.'); return; }
  if (isNaN(price) || price < 0) { alert('Enter valid price.'); return; }
  upsertInventory(hospital.id, group, units, price);
  renderInventory();
  $('invUnits').value = '0';
  $('invPrice').value = '0';
}

function handleRemoveStock(group) {
  if (!confirm('Remove ' + group + ' from inventory?')) return;
  removeInventory(hospital.id, group);
  renderInventory();
}

// ═══════════════════════════════════════════
//   INCOMING ORDERS
// ═══════════════════════════════════════════

const ORDER_ACTIONS = {
  pending: [
    { label: 'Confirm', status: 'confirmed', note: 'Order confirmed by hospital' },
    { label: 'Reject', status: 'rejected', note: 'Order rejected by hospital', danger: true }
  ],
  confirmed: [
    { label: 'Book Courier', status: 'courier_booked', note: 'Medical courier booked for sample pickup' }
  ],
  courier_booked: [
    { label: 'Sample In Transit', status: 'sample_transport', note: 'Sample picked up, courier en route to blood bank' }
  ],
  sample_transport: [
    { label: 'Start Sampling', status: 'sampling', note: 'Sample received, blood sampling initiated' }
  ],
  sampling: [
    { label: 'Mark Tested', status: 'tested', note: 'Blood sample tested and approved' }
  ],
  tested: [
    { label: 'Mark Delivered', status: 'delivered', note: 'Blood delivered to patient via medical courier' }
  ]
};

function renderOrders() {
  const orders = getOrdersBySupplier(hospital.id).sort((a, b) => b.createdAt - a.createdAt);
  if (!orders.length) {
    $('ordersList').innerHTML = '<div class="no-results">No incoming orders yet.</div>';
    return;
  }

  $('ordersList').innerHTML = orders.map(o => {
    const actions = ORDER_ACTIONS[o.status] || [];
    return `
    <div class="card order-card-hosp">
      <div class="order-header">
        <div>
          <span class="blood-badge sm ${badgeClass(o.bloodGroup)}">${esc(o.bloodGroup)}</span>
          <strong>${o.unitsNeeded} unit(s)</strong>
        </div>
        <span class="status-badge status-${o.status}">${o.status.toUpperCase()}</span>
      </div>
      <div class="order-details">
        <div><span class="label">Patient:</span> ${esc(o.patientName)}</div>
        <div><span class="label">Ordered by:</span> ${esc(o.userName)} (${esc(o.userPhone)})</div>
        <div><span class="label">Admitted at:</span> ${esc(o.admittedHospitalName)}</div>
        <div><span class="label">Ordered:</span> ${fmtDate(o.createdAt)}</div>
        ${o.notes ? `<div><span class="label">Notes:</span> ${esc(o.notes)}</div>` : ''}
      </div>
      ${actions.length ? `
      <div class="order-actions">
        ${actions.map(a => `<button class="${a.danger ? 'btn-danger' : 'btn-primary'} btn-sm" onclick="handleOrderAction('${o.id}','${a.status}','${a.note}')">${a.label}</button>`).join('')}
      </div>` : ''}
      <div class="order-timeline">
        <h4>Timeline</h4>
        ${o.history.map(h => `
          <div class="timeline-item">
            <span class="timeline-dot status-${h.status}"></span>
            <span class="timeline-status">${h.status}</span>
            <span class="timeline-time">${fmtDate(h.at)}</span>
            ${h.note ? `<span class="timeline-note">– ${esc(h.note)}</span>` : ''}
          </div>`).join('')}
      </div>
    </div>`;
  }).join('');
}

function handleOrderAction(orderId, status, note) {
  updateOrderStatus(orderId, status, note);
  renderOrders();
}

// ═══════════════════════════════════════════
//   HOSPITAL PROFILE
// ═══════════════════════════════════════════

function loadProfile() {
  if (!hospital) return;
  $('pName').value = hospital.name || '';
  $('pType').value = hospital.type || 'Hospital';
  $('pCity').value = hospital.city || '';
  $('pAddress').value = hospital.address || '';
  $('pPhone').value = hospital.phone || '';
  $('pLandline').value = hospital.landline || '';
  $('pContact').value = hospital.contactPerson || '';
  $('pHours').value = hospital.hours || '';
  if (!hospital.verified) $('profileNotVerified').style.display = '';
}

function saveProfile() {
  updateHospital(hospital.id, {
    name: $('pName').value.trim(),
    type: $('pType').value,
    city: $('pCity').value.trim(),
    address: $('pAddress').value.trim(),
    phone: $('pPhone').value.trim(),
    landline: $('pLandline').value.trim(),
    contactPerson: $('pContact').value.trim(),
    hours: $('pHours').value.trim()
  });
  hospital = getHospitalByUserId(session.userId);
  alert('Profile saved!');
}

// ═══════════════════════════════════════════
//   SUBSCRIPTION
// ═══════════════════════════════════════════

function renderSubscription() {
  const sub = getActiveSubscription(hospital.id);
  if (sub) {
    const daysLeft = Math.ceil((sub.expiresAt - Date.now()) / 86400000);
    $('subStatus').innerHTML = `
      <div class="card sub-active-card">
        <h3>Active Subscription</h3>
        <div class="sub-detail"><span class="label">Plan:</span> <strong>${esc(sub.plan)}</strong></div>
        <div class="sub-detail"><span class="label">Amount:</span> &#x20B9;${Number(sub.amount).toLocaleString('en-IN')}/month</div>
        <div class="sub-detail"><span class="label">Started:</span> ${fmtDate(sub.startedAt)}</div>
        <div class="sub-detail"><span class="label">Expires:</span> ${fmtDate(sub.expiresAt)} (${daysLeft} days left)</div>
      </div>`;
  } else {
    $('subStatus').innerHTML = '<div class="alert alert-warning">No active subscription. Choose a plan below to make your inventory visible to users.</div>';
  }
}

function handleSubscribe(plan, amount) {
  if (!confirm('Subscribe to ' + plan + ' plan for \u20B9' + amount.toLocaleString('en-IN') + '/month?')) return;
  addSubscription(hospital.id, plan, amount);
  renderSubscription();
  checkSubWarning();
  alert('Subscribed to ' + plan + ' plan! Your inventory is now visible to users.');
}
