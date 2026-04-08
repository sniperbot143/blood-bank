/* ═══════════════════════════════════════════════════════
   Blood Bank Platform – Shared Data Layer (localStorage)
   ═══════════════════════════════════════════════════════ */

const DB_KEYS = {
  users: 'bb.users',
  hospitals: 'bb.hospitals',
  inventory: 'bb.inventory',
  orders: 'bb.orders',
  subscriptions: 'bb.subscriptions',
  loginLogs: 'bb.loginLogs',
  session: 'bb.session',
  settings: 'bb.settings'
};

// ── Generic storage ──
function dbGet(key) {
  try { return JSON.parse(localStorage.getItem(key)) || null; } catch { return null; }
}
function dbSet(key, val) { localStorage.setItem(key, JSON.stringify(val)); }

// ── ID & time helpers ──
function genId() { return Math.random().toString(36).slice(2, 10) + Date.now().toString(36); }
function now() { return Date.now(); }
function timeAgo(ts) {
  if (!ts) return '';
  const diff = Date.now() - ts;
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h ago';
  const d = Math.floor(h / 24);
  return d + 'd ago';
}
function fmtDate(ts) {
  return new Date(ts).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ── HTML escape ──
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

// ── $ shortcut ──
const $ = id => document.getElementById(id);

// ═══════════════════════════════════════════
//   USERS
// ═══════════════════════════════════════════
function getUsers() { return dbGet(DB_KEYS.users) || []; }
function saveUsers(list) { dbSet(DB_KEYS.users, list); }
function findUser(email) { return getUsers().find(u => u.email === email); }

function registerUser(data) {
  const users = getUsers();
  if (users.find(u => u.email === data.email)) return { ok: false, msg: 'Email already registered.' };
  const user = { id: genId(), ...data, createdAt: now() };
  users.push(user);
  saveUsers(users);
  return { ok: true, user };
}

// ═══════════════════════════════════════════
//   AUTH / SESSION
// ═══════════════════════════════════════════
function login(email, password) {
  const user = findUser(email);
  if (!user || user.password !== password) return { ok: false, msg: 'Invalid email or password.' };
  dbSet(DB_KEYS.session, { userId: user.id, role: user.role, email: user.email, name: user.name, loginAt: now() });
  logLogin(user);
  return { ok: true, user };
}

function logout() {
  localStorage.removeItem(DB_KEYS.session);
  window.location.href = 'index.html';
}

function getSession() { return dbGet(DB_KEYS.session); }

function requireRole(role) {
  const s = getSession();
  if (!s || s.role !== role) { window.location.href = 'index.html'; return null; }
  return s;
}

// ── Login logs (for admin analytics) ──
function logLogin(user) {
  const logs = dbGet(DB_KEYS.loginLogs) || [];
  logs.push({ userId: user.id, email: user.email, name: user.name, role: user.role, at: now() });
  dbSet(DB_KEYS.loginLogs, logs);
}
function getLoginLogs() { return dbGet(DB_KEYS.loginLogs) || []; }

// ═══════════════════════════════════════════
//   HOSPITALS (registered hospital accounts)
// ═══════════════════════════════════════════
function getHospitals() { return dbGet(DB_KEYS.hospitals) || []; }
function saveHospitals(list) { dbSet(DB_KEYS.hospitals, list); }

function registerHospital(data) {
  const list = getHospitals();
  const h = { id: genId(), ...data, createdAt: now(), verified: false };
  list.push(h);
  saveHospitals(list);
  return h;
}

function getHospitalByUserId(userId) {
  return getHospitals().find(h => h.userId === userId);
}

function updateHospital(id, updates) {
  const list = getHospitals();
  const idx = list.findIndex(h => h.id === id);
  if (idx >= 0) { Object.assign(list[idx], updates); saveHospitals(list); }
}

// ═══════════════════════════════════════════
//   SUBSCRIPTIONS
// ═══════════════════════════════════════════
function getSubscriptions() { return dbGet(DB_KEYS.subscriptions) || []; }
function saveSubscriptions(list) { dbSet(DB_KEYS.subscriptions, list); }

function addSubscription(hospitalId, plan, amount) {
  const subs = getSubscriptions();
  subs.push({ id: genId(), hospitalId, plan, amount, status: 'active', startedAt: now(), expiresAt: now() + 30 * 86400000 });
  saveSubscriptions(subs);
}

function getActiveSubscription(hospitalId) {
  return getSubscriptions().find(s => s.hospitalId === hospitalId && s.status === 'active' && s.expiresAt > now());
}

// ═══════════════════════════════════════════
//   BLOOD INVENTORY
// ═══════════════════════════════════════════
const BLOOD_GROUPS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'];

function getInventory() { return dbGet(DB_KEYS.inventory) || []; }
function saveInventory(list) { dbSet(DB_KEYS.inventory, list); }

function getInventoryByHospital(hospitalId) {
  return getInventory().filter(i => i.hospitalId === hospitalId);
}

function upsertInventory(hospitalId, group, units, pricePerUnit) {
  const inv = getInventory();
  const idx = inv.findIndex(i => i.hospitalId === hospitalId && i.group === group);
  if (idx >= 0) {
    inv[idx].units = units;
    inv[idx].pricePerUnit = pricePerUnit;
    inv[idx].updatedAt = now();
  } else {
    inv.push({ id: genId(), hospitalId, group, units, pricePerUnit, updatedAt: now() });
  }
  saveInventory(inv);
}

function removeInventory(hospitalId, group) {
  saveInventory(getInventory().filter(i => !(i.hospitalId === hospitalId && i.group === group)));
}

// Collective inventory for user view (across all verified hospitals with active subs)
function getCollectiveInventory() {
  const hospitals = getHospitals().filter(h => h.verified);
  const inv = getInventory();
  const result = {};
  BLOOD_GROUPS.forEach(g => { result[g] = { totalUnits: 0, providers: [] }; });

  hospitals.forEach(h => {
    const sub = getActiveSubscription(h.id);
    if (!sub) return;
    const hInv = inv.filter(i => i.hospitalId === h.id && i.units > 0);
    hInv.forEach(i => {
      if (result[i.group]) {
        result[i.group].totalUnits += i.units;
        result[i.group].providers.push({ hospitalId: h.id, hospitalName: h.name, city: h.city, units: i.units, price: i.pricePerUnit, updatedAt: i.updatedAt });
      }
    });
  });
  return result;
}

// ═══════════════════════════════════════════
//   ORDERS
// ═══════════════════════════════════════════
// Status flow: pending → confirmed → sampling → tested → delivered / rejected
function getOrders() { return dbGet(DB_KEYS.orders) || []; }
function saveOrders(list) { dbSet(DB_KEYS.orders, list); }

function placeOrder(data) {
  const orders = getOrders();
  const order = {
    id: genId(),
    userId: data.userId,
    userName: data.userName,
    userPhone: data.userPhone,
    bloodGroup: data.bloodGroup,
    unitsNeeded: data.unitsNeeded,
    patientName: data.patientName,
    admittedHospitalId: data.admittedHospitalId,
    admittedHospitalName: data.admittedHospitalName,
    supplierHospitalId: data.supplierHospitalId,
    supplierHospitalName: data.supplierHospitalName,
    status: 'pending',
    notes: data.notes || '',
    createdAt: now(),
    updatedAt: now(),
    history: [{ status: 'pending', at: now(), note: 'Order placed by user' }]
  };
  orders.push(order);
  saveOrders(orders);
  return order;
}

function updateOrderStatus(orderId, status, note) {
  const orders = getOrders();
  const o = orders.find(o => o.id === orderId);
  if (!o) return;
  o.status = status;
  o.updatedAt = now();
  o.history.push({ status, at: now(), note: note || '' });
  saveOrders(orders);
}

function getOrdersByUser(userId) { return getOrders().filter(o => o.userId === userId); }
function getOrdersBySupplier(hospitalId) { return getOrders().filter(o => o.supplierHospitalId === hospitalId); }

// ═══════════════════════════════════════════
//   SEED DEMO DATA
// ═══════════════════════════════════════════
function seedAll() {
  // Admin account
  const admin = { id: 'admin1', name: 'Admin', email: 'admin@bloodbank.com', password: 'admin123', role: 'admin', phone: '+91 99999 00000', createdAt: now() };

  // Hospital accounts
  const hospUser1 = { id: 'huser1', name: 'Dr. Kavita Shah', email: 'citycare@hospital.com', password: 'hosp123', role: 'hospital', phone: '+91 98765 43210', createdAt: now() - 30 * 86400000 };
  const hospUser2 = { id: 'huser2', name: 'Dr. Ravi Kumar', email: 'lotus@hospital.com', password: 'hosp123', role: 'hospital', phone: '+91 99222 33445', createdAt: now() - 20 * 86400000 };
  const hospUser3 = { id: 'huser3', name: 'Duty Officer', email: 'seva@hospital.com', password: 'hosp123', role: 'hospital', phone: '+91 99111 22334', createdAt: now() - 10 * 86400000 };

  // Retail user accounts
  const user1 = { id: 'ruser1', name: 'Rupesh Patil', email: 'rupesh@gmail.com', password: 'user123', role: 'user', phone: '+91 88888 77777', createdAt: now() - 5 * 86400000 };
  const user2 = { id: 'ruser2', name: 'Priya Sharma', email: 'priya@gmail.com', password: 'user123', role: 'user', phone: '+91 77777 66666', createdAt: now() - 3 * 86400000 };

  saveUsers([admin, hospUser1, hospUser2, hospUser3, user1, user2]);

  // Hospitals
  const h1 = { id: 'hosp1', userId: 'huser1', name: 'CityCare Blood Bank', type: 'Blood Bank', city: 'Mumbai', address: '12, MG Road, Mumbai 400001', phone: '+91 98765 43210', landline: '022-4000-1122', hours: '24x7', contactPerson: 'Ms. Kavita Shah', verified: true, createdAt: now() - 30 * 86400000 };
  const h2 = { id: 'hosp2', userId: 'huser2', name: 'Lotus Heart Hospital', type: 'Hospital', city: 'Pune', address: 'Opp. River View, Pune 411001', phone: '+91 99222 33445', landline: '020-2333-7788', hours: '9am – 9pm', contactPerson: 'Blood Desk - Ravi', verified: true, createdAt: now() - 20 * 86400000 };
  const h3 = { id: 'hosp3', userId: 'huser3', name: 'Seva Multispeciality', type: 'Hospital', city: 'Delhi', address: 'Ring Road, Sector 5, Delhi 110001', phone: '+91 99111 22334', landline: '011-4555-6677', hours: '24x7', contactPerson: 'Duty Officer', verified: true, createdAt: now() - 10 * 86400000 };
  saveHospitals([h1, h2, h3]);

  // Subscriptions
  saveSubscriptions([
    { id: 'sub1', hospitalId: 'hosp1', plan: 'Premium', amount: 5000, status: 'active', startedAt: now() - 15 * 86400000, expiresAt: now() + 15 * 86400000 },
    { id: 'sub2', hospitalId: 'hosp2', plan: 'Basic', amount: 2000, status: 'active', startedAt: now() - 10 * 86400000, expiresAt: now() + 20 * 86400000 },
    { id: 'sub3', hospitalId: 'hosp3', plan: 'Premium', amount: 5000, status: 'active', startedAt: now() - 5 * 86400000, expiresAt: now() + 25 * 86400000 }
  ]);

  // Blood inventory
  const inv = [
    { id: 'inv1', hospitalId: 'hosp1', group: 'O+', units: 18, pricePerUnit: 2200, updatedAt: now() - 2 * 3600000 },
    { id: 'inv2', hospitalId: 'hosp1', group: 'A+', units: 7, pricePerUnit: 2400, updatedAt: now() - 9 * 3600000 },
    { id: 'inv3', hospitalId: 'hosp1', group: 'AB-', units: 2, pricePerUnit: 3000, updatedAt: now() - 25 * 3600000 },
    { id: 'inv4', hospitalId: 'hosp2', group: 'B+', units: 10, pricePerUnit: 2300, updatedAt: now() - 1800000 },
    { id: 'inv5', hospitalId: 'hosp2', group: 'O-', units: 5, pricePerUnit: 2800, updatedAt: now() - 2 * 3600000 },
    { id: 'inv6', hospitalId: 'hosp3', group: 'A-', units: 4, pricePerUnit: 2500, updatedAt: now() - 5 * 3600000 },
    { id: 'inv7', hospitalId: 'hosp3', group: 'AB+', units: 6, pricePerUnit: 2600, updatedAt: now() - 12 * 3600000 }
  ];
  saveInventory(inv);

  // Sample orders
  const orders = [
    {
      id: 'ord1', userId: 'ruser1', userName: 'Rupesh Patil', userPhone: '+91 88888 77777',
      bloodGroup: 'O+', unitsNeeded: 2, patientName: 'Rajesh Patil',
      admittedHospitalId: 'hosp2', admittedHospitalName: 'Lotus Heart Hospital',
      supplierHospitalId: 'hosp1', supplierHospitalName: 'CityCare Blood Bank',
      status: 'confirmed', notes: 'Urgent - surgery scheduled tomorrow',
      createdAt: now() - 86400000, updatedAt: now() - 43200000,
      history: [
        { status: 'pending', at: now() - 86400000, note: 'Order placed by user' },
        { status: 'confirmed', at: now() - 43200000, note: 'Order confirmed by supplier' }
      ]
    }
  ];
  saveOrders(orders);

  // Login logs
  dbSet(DB_KEYS.loginLogs, [
    { userId: 'ruser1', email: 'rupesh@gmail.com', name: 'Rupesh Patil', role: 'user', at: now() - 4 * 86400000 },
    { userId: 'ruser2', email: 'priya@gmail.com', name: 'Priya Sharma', role: 'user', at: now() - 2 * 86400000 },
    { userId: 'huser1', email: 'citycare@hospital.com', name: 'Dr. Kavita Shah', role: 'hospital', at: now() - 86400000 },
    { userId: 'huser2', email: 'lotus@hospital.com', name: 'Dr. Ravi Kumar', role: 'hospital', at: now() - 3600000 },
    { userId: 'admin1', email: 'admin@bloodbank.com', name: 'Admin', role: 'admin', at: now() - 1800000 }
  ]);
}

// Auto-seed if no users exist
if (!dbGet(DB_KEYS.users) || dbGet(DB_KEYS.users).length === 0) {
  seedAll();
}
