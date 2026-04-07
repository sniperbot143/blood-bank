/* ─────────────────────────────────────────────
   Blood Bank Finder – JS (localStorage backed)
   ───────────────────────────────────────────── */

// ── Helpers ──
const $ = id => document.getElementById(id);
const esc = s => {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
};
const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

// ── Storage keys ──
const KEYS = {
  hospitals: 'bbf.hospitals',
  settings: 'bbf.settings'
};

// ── Data access ──
function getData(key) {
  try { return JSON.parse(localStorage.getItem(key)) || null; } catch { return null; }
}
function setData(key, val) {
  localStorage.setItem(key, JSON.stringify(val));
}

function getHospitals() { return getData(KEYS.hospitals) || []; }
function saveHospitals(list) { setData(KEYS.hospitals, list); }
function getSettings() {
  return getData(KEYS.settings) || { appName: 'Blood Bank Finder', notice: 'Demo app. Always verify availability with the provider. Prices are handled by providers directly.' };
}
function saveSettings(s) { setData(KEYS.settings, s); applySettings(); }

// ── Time helpers ──
function timeAgo(ts) {
  if (!ts) return '';
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ago';
  const days = Math.floor(hrs / 24);
  return days + 'd ago';
}

// ── Blood badge CSS class ──
function badgeClass(group) {
  return group.replace('+', 'plus').replace('-', 'minus');
}

// ── Demo data ──
function seedDemoData() {
  const now = Date.now();
  const hospitals = [
    {
      id: uid(), name: 'Seva Multispeciality', type: 'Hospital',
      contact: 'Duty Officer', mobile: '+91 99111 22334', landline: '011-4555-6677',
      hours: '24x7', address: 'Ring Road, Sector 5', city: 'Delhi', pin: '110001',
      inventory: [
        { group: 'A-', units: 4, price: 2500, updated: now - 5 * 3600000 },
        { group: 'AB+', units: 6, price: 2600, updated: now - 12 * 3600000 }
      ]
    },
    {
      id: uid(), name: 'CityCare Blood Bank', type: 'Blood Bank',
      contact: 'Ms. Kavita Shah', mobile: '+91 98765 43210', landline: '022-4000-1122',
      hours: '24x7', address: '12, MG Road', city: 'Mumbai', pin: '400001',
      inventory: [
        { group: 'O+', units: 18, price: 2200, updated: now - 2 * 3600000 },
        { group: 'A+', units: 7, price: 2400, updated: now - 9 * 3600000 },
        { group: 'AB-', units: 2, price: 3000, updated: now - 25 * 3600000 }
      ]
    },
    {
      id: uid(), name: 'Lotus Heart Hospital', type: 'Hospital',
      contact: 'Blood Desk - Ravi', mobile: '+91 99222 33445', landline: '020-2333-7788',
      hours: '9am – 9pm', address: 'Opp. River View', city: 'Pune', pin: '411001',
      inventory: [
        { group: 'B+', units: 10, price: 2300, updated: now - 30 * 60000 },
        { group: 'O-', units: 5, price: 2800, updated: now - 2 * 3600000 }
      ]
    }
  ];
  saveHospitals(hospitals);
  saveSettings({ appName: 'Blood Bank Finder', notice: 'Demo app. Always verify availability with the provider. Prices are handled by providers directly.' });
}

// ── Init ──
(function init() {
  if (!getData(KEYS.hospitals)) seedDemoData();
  applySettings();
  renderSearch();
  renderManageList();

  // live filter
  ['filterGroup', 'filterCity', 'filterPin', 'filterName'].forEach(id => {
    $(id).addEventListener('input', renderSearch);
  });

  // admin fields
  const settings = getSettings();
  $('adminAppName').value = settings.appName;
  $('adminNotice').value = settings.notice;
  $('adminAppName').addEventListener('input', () => {
    const s = getSettings();
    s.appName = $('adminAppName').value;
    saveSettings(s);
  });
  $('adminNotice').addEventListener('input', () => {
    const s = getSettings();
    s.notice = $('adminNotice').value;
    saveSettings(s);
  });
})();

function applySettings() {
  const s = getSettings();
  $('appName').textContent = s.appName;
  document.title = s.appName;
  $('footerText').textContent = s.notice;
}

// ── Tab switching ──
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
  if (name === 'manage') renderManageList();
  if (name === 'find') renderSearch();
}

// ══════════════════════════════════════════════
//   FIND BLOOD
// ══════════════════════════════════════════════

function renderSearch() {
  const group = $('filterGroup').value;
  const city = $('filterCity').value.trim().toLowerCase();
  const pin = $('filterPin').value.trim();
  const name = $('filterName').value.trim().toLowerCase();

  let hospitals = getHospitals();

  // filter by name / city / pin
  if (city) hospitals = hospitals.filter(h => h.city.toLowerCase().includes(city));
  if (pin) hospitals = hospitals.filter(h => h.pin && h.pin.startsWith(pin));
  if (name) hospitals = hospitals.filter(h => h.name.toLowerCase().includes(name));

  // filter by blood group availability
  if (group) hospitals = hospitals.filter(h => h.inventory.some(i => i.group === group && i.units > 0));

  if (!hospitals.length) {
    $('searchResults').innerHTML = '<div class="no-results">No hospitals or blood banks match your search.</div>';
    return;
  }

  $('searchResults').innerHTML = hospitals.map(h => {
    const inv = group ? h.inventory.filter(i => i.group === group && i.units > 0) : h.inventory.filter(i => i.units > 0);
    return `
      <div class="hospital-card">
        <div class="hospital-header">
          <h3>${esc(h.name)}</h3>
          <span class="type-badge">${esc(h.type)}</span>
        </div>
        <div class="hospital-meta">
          <span>&#x1F4CD; ${esc(h.address || '')}, ${esc(h.city)} ${esc(h.pin || '')}</span>
          ${h.contact ? `<span>&#x1F464; ${esc(h.contact)}</span>` : ''}
          ${h.mobile ? `<span>&#x1F4F1; ${esc(h.mobile)}</span>` : ''}
          ${h.landline ? `<span>&#x260E;&#xFE0F; ${esc(h.landline)}</span>` : ''}
          ${h.hours ? `<span>&#x1F552; ${esc(h.hours)}</span>` : ''}
        </div>
        <div class="blood-list">
          ${inv.map(i => `
            <div class="blood-item">
              <div class="blood-badge ${badgeClass(i.group)}">${esc(i.group)}</div>
              <div class="blood-info">
                <span class="units">${i.units} units available</span>
                <span class="updated">Updated ${timeAgo(i.updated)}</span>
              </div>
              <span class="blood-price">&#x20B9;${Number(i.price).toLocaleString('en-IN')}/unit</span>
              <span class="blood-details" onclick='showDetail(${JSON.stringify(JSON.stringify({ hospital: h.name, type: h.type, city: h.city, address: h.address, contact: h.contact, mobile: h.mobile, landline: h.landline, hours: h.hours, group: i.group, units: i.units, price: i.price, updated: i.updated }))})'>Get Details</span>
            </div>
          `).join('')}
        </div>
      </div>`;
  }).join('');
}

function clearFilters() {
  $('filterGroup').value = '';
  $('filterCity').value = '';
  $('filterPin').value = '';
  $('filterName').value = '';
  renderSearch();
}

// ── Detail modal ──
function showDetail(jsonStr) {
  const d = JSON.parse(jsonStr);
  let overlay = document.querySelector('.modal-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    document.body.appendChild(overlay);
  }
  overlay.innerHTML = `
    <div class="modal">
      <h3>${esc(d.hospital)} – ${esc(d.group)}</h3>
      <div class="detail-row"><span class="detail-label">Type</span><span class="detail-value">${esc(d.type)}</span></div>
      <div class="detail-row"><span class="detail-label">City</span><span class="detail-value">${esc(d.city)}</span></div>
      <div class="detail-row"><span class="detail-label">Address</span><span class="detail-value">${esc(d.address || '–')}</span></div>
      <div class="detail-row"><span class="detail-label">Contact</span><span class="detail-value">${esc(d.contact || '–')}</span></div>
      <div class="detail-row"><span class="detail-label">Mobile</span><span class="detail-value">${esc(d.mobile || '–')}</span></div>
      <div class="detail-row"><span class="detail-label">Landline</span><span class="detail-value">${esc(d.landline || '–')}</span></div>
      <div class="detail-row"><span class="detail-label">Hours</span><span class="detail-value">${esc(d.hours || '–')}</span></div>
      <div class="detail-row"><span class="detail-label">Blood Group</span><span class="detail-value">${esc(d.group)}</span></div>
      <div class="detail-row"><span class="detail-label">Available</span><span class="detail-value">${d.units} units</span></div>
      <div class="detail-row"><span class="detail-label">Price</span><span class="detail-value">&#x20B9;${Number(d.price).toLocaleString('en-IN')}/unit</span></div>
      <div class="detail-row"><span class="detail-label">Last updated</span><span class="detail-value">${timeAgo(d.updated)}</span></div>
      <div class="modal-actions"><button class="btn-primary" onclick="document.querySelector('.modal-overlay').classList.remove('open')">Close</button></div>
    </div>`;
  overlay.classList.add('open');
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('open'); });
}

// ══════════════════════════════════════════════
//   MANAGE HOSPITALS
// ══════════════════════════════════════════════

function renderManageList() {
  const list = getHospitals();
  if (!list.length) {
    $('manageList').innerHTML = '<div class="no-results">No hospitals added yet. Use the form to add one.</div>';
    return;
  }
  $('manageList').innerHTML = list.map(h => `
    <div class="manage-card">
      <div class="manage-card-header">
        <h3>${esc(h.name)}</h3>
        <div class="manage-card-actions">
          <button class="btn-edit" onclick="editHospital('${h.id}')">Edit</button>
          <button class="btn-delete" onclick="deleteHospital('${h.id}')">Delete</button>
        </div>
      </div>
      <div class="manage-blood-list">
        ${h.inventory.map(i => `
          <div class="manage-blood-item">
            <div class="badge-top">
              <div class="blood-badge" style="width:28px;height:28px;font-size:11px" ><span class="blood-badge ${badgeClass(i.group)}" style="width:28px;height:28px;font-size:11px;display:inline-flex">${esc(i.group)}</span></div>
              <span class="mb-time">${timeAgo(i.updated)}</span>
            </div>
            <div><span class="mb-label">Units</span> <span class="mb-value">${i.units}</span></div>
            <div><span class="mb-label">Price</span> <span class="mb-value">&#x20B9;${Number(i.price).toLocaleString('en-IN')}</span></div>
          </div>`).join('')}
        ${!h.inventory.length ? '<span class="muted">No inventory</span>' : ''}
      </div>
    </div>`).join('');
}

// ── Inventory lines in form ──
let invLines = [];

function renderInventoryLines() {
  if (!invLines.length) {
    $('inventoryLines').innerHTML = '';
    $('noInventory').style.display = '';
    return;
  }
  $('noInventory').style.display = 'none';
  $('inventoryLines').innerHTML = invLines.map((l, i) => `
    <div class="inv-line">
      <select onchange="invLines[${i}].group=this.value">
        ${['A+','A-','B+','B-','AB+','AB-','O+','O-'].map(g => `<option ${l.group===g?'selected':''}>${g}</option>`).join('')}
      </select>
      <input type="number" min="0" value="${l.units}" placeholder="Units" onchange="invLines[${i}].units=+this.value">
      <input type="number" min="0" value="${l.price}" placeholder="Price" onchange="invLines[${i}].price=+this.value">
      <button class="btn-remove" onclick="invLines.splice(${i},1);renderInventoryLines()">&#x2716;</button>
    </div>`).join('');
}

function addInventoryLine() {
  invLines.push({ group: 'A+', units: 0, price: 0, updated: Date.now() });
  renderInventoryLines();
}

// ── Save hospital ──
function saveHospital() {
  const name = $('fName').value.trim();
  const city = $('fCity').value.trim();
  if (!name || !city) { alert('Name and City are required.'); return; }

  const entry = {
    id: $('editId').value || uid(),
    name,
    type: $('fType').value,
    contact: $('fContact').value.trim(),
    mobile: $('fMobile').value.trim(),
    landline: $('fLandline').value.trim(),
    hours: $('fHours').value.trim(),
    address: $('fAddress').value.trim(),
    city,
    pin: $('fPin').value.trim(),
    inventory: invLines.map(l => ({ ...l, updated: l.updated || Date.now() }))
  };

  const list = getHospitals();
  const idx = list.findIndex(h => h.id === entry.id);
  if (idx >= 0) list[idx] = entry; else list.push(entry);
  saveHospitals(list);

  cancelEdit();
  renderManageList();
}

function editHospital(id) {
  const h = getHospitals().find(h => h.id === id);
  if (!h) return;
  $('editId').value = h.id;
  $('fName').value = h.name;
  $('fType').value = h.type;
  $('fContact').value = h.contact || '';
  $('fMobile').value = h.mobile || '';
  $('fLandline').value = h.landline || '';
  $('fHours').value = h.hours || '';
  $('fAddress').value = h.address || '';
  $('fCity').value = h.city || '';
  $('fPin').value = h.pin || '';
  invLines = (h.inventory || []).map(i => ({ ...i }));
  renderInventoryLines();
  $('formTitle').textContent = '✏️ Edit Hospital / Blood Bank';
  $('btnCancelEdit').style.display = '';
  document.querySelector('.form-card').scrollIntoView({ behavior: 'smooth' });
}

function deleteHospital(id) {
  if (!confirm('Delete this hospital / blood bank?')) return;
  saveHospitals(getHospitals().filter(h => h.id !== id));
  renderManageList();
}

function cancelEdit() {
  $('editId').value = '';
  $('fName').value = '';
  $('fType').value = 'Blood Bank';
  $('fContact').value = '';
  $('fMobile').value = '';
  $('fLandline').value = '';
  $('fHours').value = '';
  $('fAddress').value = '';
  $('fCity').value = '';
  $('fPin').value = '';
  invLines = [];
  renderInventoryLines();
  $('formTitle').innerHTML = '&#x2795; Add Hospital / Blood Bank';
  $('btnCancelEdit').style.display = 'none';
}

// ══════════════════════════════════════════════
//   ADMIN
// ══════════════════════════════════════════════

function resetDemoData() {
  if (!confirm('This will replace all data with demo data. Continue?')) return;
  seedDemoData();
  applySettings();
  const s = getSettings();
  $('adminAppName').value = s.appName;
  $('adminNotice').value = s.notice;
  renderManageList();
  renderSearch();
  alert('Demo data restored.');
}

// ══════════════════════════════════════════════
//   EXPORT / IMPORT
// ══════════════════════════════════════════════

function exportData() {
  const data = { hospitals: getHospitals(), settings: getSettings() };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'blood-bank-data.json';
  a.click();
  URL.revokeObjectURL(a.href);
}

function importData(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function (e) {
    try {
      const data = JSON.parse(e.target.result);
      if (data.hospitals) saveHospitals(data.hospitals);
      if (data.settings) {
        saveSettings(data.settings);
        $('adminAppName').value = data.settings.appName || '';
        $('adminNotice').value = data.settings.notice || '';
      }
      renderSearch();
      renderManageList();
      alert('Data imported successfully.');
    } catch {
      alert('Invalid JSON file.');
    }
  };
  reader.readAsText(file);
  event.target.value = '';
}
