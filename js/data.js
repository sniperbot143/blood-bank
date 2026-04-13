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
//   CITY COORDS & DISTANCE
// ═══════════════════════════════════════════
const CITY_COORDS = {
  'mumbai': { lat: 19.0760, lng: 72.8777 },
  'delhi': { lat: 28.7041, lng: 77.1025 },
  'new delhi': { lat: 28.6139, lng: 77.2090 },
  'pune': { lat: 18.5204, lng: 73.8567 },
  'bangalore': { lat: 12.9716, lng: 77.5946 },
  'bengaluru': { lat: 12.9716, lng: 77.5946 },
  'chennai': { lat: 13.0827, lng: 80.2707 },
  'kolkata': { lat: 22.5726, lng: 88.3639 },
  'hyderabad': { lat: 17.3850, lng: 78.4867 },
  'ahmedabad': { lat: 23.0225, lng: 72.5714 },
  'jaipur': { lat: 26.9124, lng: 75.7873 },
  'lucknow': { lat: 26.8467, lng: 80.9462 },
  'surat': { lat: 21.1702, lng: 72.8311 },
  'kanpur': { lat: 26.4499, lng: 80.3319 },
  'nagpur': { lat: 21.1458, lng: 79.0882 },
  'indore': { lat: 22.7196, lng: 75.8577 },
  'thane': { lat: 19.2183, lng: 72.9781 },
  'bhopal': { lat: 23.2599, lng: 77.4126 },
  'visakhapatnam': { lat: 17.6868, lng: 83.2185 },
  'patna': { lat: 25.5941, lng: 85.1376 },
  'vadodara': { lat: 22.3072, lng: 73.1812 },
  'ghaziabad': { lat: 28.6692, lng: 77.4538 },
  'ludhiana': { lat: 30.9010, lng: 75.8573 },
  'agra': { lat: 27.1767, lng: 78.0081 },
  'nashik': { lat: 19.9975, lng: 73.7898 },
  'faridabad': { lat: 28.4089, lng: 77.3178 },
  'meerut': { lat: 28.9845, lng: 77.7064 },
  'rajkot': { lat: 22.3039, lng: 70.8022 },
  'varanasi': { lat: 25.3176, lng: 82.9739 },
  'amritsar': { lat: 31.6340, lng: 74.8723 },
  'allahabad': { lat: 25.4358, lng: 81.8463 },
  'prayagraj': { lat: 25.4358, lng: 81.8463 },
  'ranchi': { lat: 23.3441, lng: 85.3096 },
  'coimbatore': { lat: 11.0168, lng: 76.9558 },
  'jabalpur': { lat: 23.1815, lng: 79.9864 },
  'gwalior': { lat: 26.2183, lng: 78.1828 },
  'vijayawada': { lat: 16.5062, lng: 80.6480 },
  'jodhpur': { lat: 26.2389, lng: 73.0243 },
  'madurai': { lat: 9.9252, lng: 78.1198 },
  'raipur': { lat: 21.2514, lng: 81.6296 },
  'kota': { lat: 25.2138, lng: 75.8648 },
  'chandigarh': { lat: 30.7333, lng: 76.7794 },
  'guwahati': { lat: 26.1445, lng: 91.7362 },
  'mysore': { lat: 12.2958, lng: 76.6394 },
  'mysuru': { lat: 12.2958, lng: 76.6394 },
  'gurgaon': { lat: 28.4595, lng: 77.0266 },
  'gurugram': { lat: 28.4595, lng: 77.0266 },
  'noida': { lat: 28.5355, lng: 77.3910 }
};

function getCityCoords(city) {
  if (!city) return null;
  return CITY_COORDS[city.trim().toLowerCase()] || null;
}

// Haversine distance in km
function calcDistance(city1, city2) {
  if (!city1 || !city2) return null;
  if (city1.trim().toLowerCase() === city2.trim().toLowerCase()) return 0;
  const c1 = getCityCoords(city1);
  const c2 = getCityCoords(city2);
  if (!c1 || !c2) return null;
  const R = 6371;
  const toRad = d => d * Math.PI / 180;
  const dLat = toRad(c2.lat - c1.lat);
  const dLng = toRad(c2.lng - c1.lng);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(c1.lat)) * Math.cos(toRad(c2.lat)) * Math.sin(dLng / 2) ** 2;
  return Math.round(R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
}

// Format distance as a human label
function formatDistance(km) {
  if (km === null || km === undefined) return 'Distance unknown';
  if (km === 0) return 'In your city';
  if (km < 10) return 'Within 10 km';
  if (km < 50) return 'Within 50 km';
  if (km < 100) return 'Within 100 km';
  if (km < 250) return 'Within 250 km';
  if (km < 500) return 'Within 500 km';
  if (km < 1000) return 'Within 1000 km';
  return 'Over 1000 km';
}

// User saved location helpers
function getUserLocation() { return localStorage.getItem('bb.userLocation') || ''; }
function setUserLocation(city) { localStorage.setItem('bb.userLocation', city); }

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

// Anonymous availability for users (no hospital names) – grouped by blood group with distance bands
function getAnonymousAvailability(userCity) {
  const hospitals = getHospitals().filter(h => h.verified);
  const inv = getInventory();
  const result = {};
  BLOOD_GROUPS.forEach(g => {
    result[g] = {
      totalUnits: 0,
      providerCount: 0,
      nearestKm: null,
      bands: {},      // { 'Within 10 km': { units, count, minPrice }, ... }
      minPrice: null
    };
  });

  hospitals.forEach(h => {
    const sub = getActiveSubscription(h.id);
    if (!sub) return;
    const km = calcDistance(userCity, h.city);
    const band = formatDistance(km);

    const hInv = inv.filter(i => i.hospitalId === h.id && i.units > 0);
    hInv.forEach(i => {
      const r = result[i.group];
      r.totalUnits += i.units;
      r.providerCount += 1;
      if (km !== null && (r.nearestKm === null || km < r.nearestKm)) r.nearestKm = km;
      if (r.minPrice === null || i.pricePerUnit < r.minPrice) r.minPrice = i.pricePerUnit;
      if (!r.bands[band]) r.bands[band] = { units: 0, count: 0, minPrice: null };
      r.bands[band].units += i.units;
      r.bands[band].count += 1;
      if (r.bands[band].minPrice === null || i.pricePerUnit < r.bands[band].minPrice) r.bands[band].minPrice = i.pricePerUnit;
    });
  });
  return result;
}

// Pick the best supplier hospital for a user order (nearest with enough stock)
function pickBestSupplier(userCity, bloodGroup, unitsNeeded) {
  const hospitals = getHospitals().filter(h => h.verified);
  const inv = getInventory();
  const candidates = [];
  hospitals.forEach(h => {
    const sub = getActiveSubscription(h.id);
    if (!sub) return;
    const stock = inv.find(i => i.hospitalId === h.id && i.group === bloodGroup);
    if (!stock || stock.units < unitsNeeded) return;
    const km = calcDistance(userCity, h.city);
    candidates.push({ hospital: h, distance: km === null ? 99999 : km, price: stock.pricePerUnit });
  });
  if (!candidates.length) return null;
  candidates.sort((a, b) => a.distance - b.distance || a.price - b.price);
  return candidates[0];
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
    userCity: data.userCity || '',
    bloodGroup: data.bloodGroup,
    unitsNeeded: data.unitsNeeded,
    patientName: data.patientName,
    admittedHospitalId: data.admittedHospitalId,
    admittedHospitalName: data.admittedHospitalName,
    supplierHospitalId: data.supplierHospitalId,
    supplierHospitalName: data.supplierHospitalName,
    distanceKm: data.distanceKm !== undefined ? data.distanceKm : null,
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
