/* ═══════════════════════════════════════════
   User App – Anonymous availability + auto supplier
   ═══════════════════════════════════════════ */

const session = requireRole('user');
if (session) {
  $('greeting').textContent = 'Hi, ' + session.name;
  init();
}

function init() {
  // Populate city autocomplete
  populateCityList();

  // Load saved location
  const savedCity = getUserLocation();
  if (savedCity) {
    $('userCity').value = savedCity;
    $('oCity').value = savedCity;
  }

  $('oPhone').value = session.phone || '';
  $('filterGroup').addEventListener('change', renderAvailability);
  $('oGroup').addEventListener('change', updateOrderPreview);
  $('oUnits').addEventListener('input', updateOrderPreview);
  $('oCity').addEventListener('input', updateOrderPreview);

  renderAvailability();
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
  if (name === 'myorders') renderMyOrders();
  if (name === 'search') renderAvailability();
}

function populateCityList() {
  const list = $('cityList');
  list.innerHTML = Object.keys(CITY_COORDS)
    .map(c => c.charAt(0).toUpperCase() + c.slice(1))
    .map(c => `<option value="${c}">`).join('');
}

function badgeClass(g) { return g.replace('+','plus').replace('-','minus'); }

function updateLocation() {
  const city = $('userCity').value.trim();
  setUserLocation(city);
  $('oCity').value = city;
  renderAvailability();
}

// ═══════════════════════════════════════════
//   ANONYMOUS AVAILABILITY (no hospital names)
// ═══════════════════════════════════════════

function renderAvailability() {
  const userCity = $('userCity').value.trim() || getUserLocation();
  const data = getAnonymousAvailability(userCity);
  const filterG = $('filterGroup').value;
  const groups = filterG ? [filterG] : BLOOD_GROUPS;

  const cards = groups.map(g => {
    const d = data[g];
    if (d.totalUnits === 0) {
      return `
        <div class="avail-card empty">
          <div class="avail-head">
            <div class="blood-badge ${badgeClass(g)}">${g}</div>
            <div class="avail-title">
              <h3>${g} Blood Group</h3>
              <span class="avail-sub">Currently unavailable</span>
            </div>
          </div>
        </div>`;
    }

    // Build distance bands list
    const bandOrder = ['In your city', 'Within 10 km', 'Within 50 km', 'Within 100 km', 'Within 250 km', 'Within 500 km', 'Within 1000 km', 'Over 1000 km', 'Distance unknown'];
    const bandsHtml = bandOrder
      .filter(b => d.bands[b])
      .map(b => `
        <div class="band-row">
          <span class="band-label">&#x1F4CD; ${b}</span>
          <span class="band-units">${d.bands[b].units} unit${d.bands[b].units !== 1 ? 's' : ''}</span>
          ${d.bands[b].minPrice !== null ? `<span class="band-price">from &#x20B9;${Number(d.bands[b].minPrice).toLocaleString('en-IN')}/unit</span>` : ''}
        </div>`).join('');

    const distLabel = userCity
      ? (d.nearestKm !== null ? `Nearest: ${d.nearestKm === 0 ? 'In your city' : d.nearestKm + ' km away'}` : 'Distance unknown')
      : 'Set your location to see distance';

    return `
      <div class="avail-card available">
        <div class="avail-head">
          <div class="blood-badge ${badgeClass(g)}">${g}</div>
          <div class="avail-title">
            <h3>${g} Blood Group</h3>
            <span class="avail-sub"><strong>${d.totalUnits} units</strong> available &middot; from &#x20B9;${Number(d.minPrice).toLocaleString('en-IN')}/unit</span>
          </div>
          <div class="avail-distance">${distLabel}</div>
        </div>
        <div class="bands-list">${bandsHtml}</div>
        <button class="btn-primary btn-sm" onclick="quickOrder('${g}')">Order ${g}</button>
      </div>`;
  }).join('');

  $('availabilityList').innerHTML = cards || '<div class="no-results">No blood available right now.</div>';
}

function quickOrder(group) {
  switchTab('order');
  $('oGroup').value = group;
  updateOrderPreview();
}

// ═══════════════════════════════════════════
//   ORDER – Auto supplier matching
// ═══════════════════════════════════════════

function updateOrderPreview() {
  const group = $('oGroup').value;
  const units = parseInt($('oUnits').value) || 0;
  const city = $('oCity').value.trim();
  const preview = $('orderPreview');
  const costSection = $('costBreakdown');

  if (!group || !units || !city) {
    preview.style.display = 'none';
    costSection.style.display = 'none';
    return;
  }

  const match = pickBestSupplier(city, group, units);
  if (!match) {
    preview.style.display = '';
    preview.className = 'order-preview no-match';
    preview.innerHTML = `&#x26A0; No blood bank currently has ${units} unit(s) of ${group} available. You can still place the order and we will try to arrange it.`;
    costSection.style.display = 'none';
    return;
  }

  const distLabel = match.distance === 0 ? 'in your city' : (match.distance + ' km away');
  preview.style.display = '';
  preview.className = 'order-preview match';
  preview.innerHTML = `&#x2705; <strong>${units} unit(s)</strong> of <strong>${group}</strong> available <strong>${distLabel}</strong>`;

  // Cost breakdown
  const bloodCost = match.price * units;
  const testLow = 200, testHigh = 500;
  const transportLow = 150, transportHigh = 300;
  const coordFee = 250;
  const totalLow = bloodCost + testLow + transportLow + coordFee;
  const totalHigh = bloodCost + testHigh + transportHigh + coordFee;

  costSection.style.display = '';
  $('costRows').innerHTML = `
    <div class="cost-row"><span>Blood processing (${units} unit${units > 1 ? 's' : ''} x &#x20B9;${match.price.toLocaleString('en-IN')})</span><span>&#x20B9;${bloodCost.toLocaleString('en-IN')}</span></div>
    <div class="cost-row"><span>Sample testing</span><span>&#x20B9;${testLow}–${testHigh}</span></div>
    <div class="cost-row"><span>Medical transport</span><span>&#x20B9;${transportLow}–${transportHigh}</span></div>
    <div class="cost-row"><span>Coordination fee</span><span>&#x20B9;${coordFee}</span></div>
  `;
  $('costTotal').innerHTML = `<span>Estimated Total</span><span>&#x20B9;${totalLow.toLocaleString('en-IN')} – &#x20B9;${totalHigh.toLocaleString('en-IN')}</span>`;
}

function handlePlaceOrder() {
  const group = $('oGroup').value;
  const units = parseInt($('oUnits').value);
  const patient = $('oPatient').value.trim();
  const phone = $('oPhone').value.trim();
  const city = $('oCity').value.trim();
  const admittedName = $('oAdmittedHospital').value.trim();

  if (!group || !units || !patient || !phone || !city || !admittedName) {
    showAlert('orderError', 'Please fill in all required fields.');
    return;
  }

  // Auto-pick best supplier (nearest with stock)
  const match = pickBestSupplier(city, group, units);

  const order = placeOrder({
    userId: session.userId,
    userName: session.name,
    userPhone: phone,
    userCity: city,
    bloodGroup: group,
    unitsNeeded: units,
    patientName: patient,
    admittedHospitalId: '',                     // user-typed, not from list
    admittedHospitalName: admittedName,
    supplierHospitalId: match ? match.hospital.id : '',
    supplierHospitalName: match ? match.hospital.name : 'Pending assignment',
    distanceKm: match ? match.distance : null,
    notes: $('oNotes').value.trim()
  });

  const distMsg = match
    ? `Blood will be arranged from ${match.distance === 0 ? 'a provider in your city' : 'a provider ' + match.distance + ' km away'}.`
    : 'We are searching for a provider with the requested blood.';

  showAlert('orderSuccess', `Order placed! Order ID: ${order.id}. ${distMsg}`);

  // Reset form
  $('oPatient').value = '';
  $('oGroup').value = '';
  $('oUnits').value = '1';
  $('oAdmittedHospital').value = '';
  $('oNotes').value = '';
  $('orderPreview').style.display = 'none';
}

// ═══════════════════════════════════════════
//   MY ORDERS (no hospital names except admitted)
// ═══════════════════════════════════════════

function renderMyOrders() {
  const orders = getOrdersByUser(session.userId).sort((a, b) => b.createdAt - a.createdAt);
  if (!orders.length) {
    $('myOrdersList').innerHTML = '<div class="no-results">No orders yet. Go to "Order Blood" to place your first order.</div>';
    return;
  }

  $('myOrdersList').innerHTML = orders.map(o => {
    const distLabel = (o.distanceKm !== null && o.distanceKm !== undefined)
      ? (o.distanceKm === 0 ? 'In your city' : o.distanceKm + ' km away')
      : 'Distance pending';
    return `
    <div class="card order-status-card">
      <div class="order-header">
        <div>
          <span class="blood-badge sm ${badgeClass(o.bloodGroup)}">${esc(o.bloodGroup)}</span>
          <strong>${o.unitsNeeded} unit(s)</strong> for <strong>${esc(o.patientName)}</strong>
        </div>
        <span class="status-badge status-${o.status}">${o.status.toUpperCase()}</span>
      </div>
      <div class="order-details">
        <div><span class="label">Patient admitted at:</span> ${esc(o.admittedHospitalName)}</div>
        <div><span class="label">Source:</span> ${distLabel}</div>
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
    </div>`;
  }).join('');
}

// ── Helpers ──
function showAlert(id, msg) {
  const el = $(id);
  el.innerHTML = msg;
  el.style.display = '';
  setTimeout(() => el.style.display = 'none', 7000);
}
