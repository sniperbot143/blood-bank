// SafeHer - shared data layer (localStorage-backed)
const DB = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
    catch { return fallback; }
  },
  set(key, val) { localStorage.setItem(key, JSON.stringify(val)); },
  push(key, item) {
    const arr = DB.get(key, []);
    arr.unshift(item);
    DB.set(key, arr);
    return item;
  },
  update(key, id, patch) {
    const arr = DB.get(key, []);
    const i = arr.findIndex(x => x.id === id);
    if (i >= 0) { arr[i] = { ...arr[i], ...patch }; DB.set(key, arr); }
    return arr[i];
  },
  remove(key, id) {
    const arr = DB.get(key, []).filter(x => x.id !== id);
    DB.set(key, arr);
  }
};

const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const now = () => new Date().toISOString();
const timeAgo = (iso) => {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
};

// Keys
const K = {
  profile: 'safeher.profile',
  contacts: 'safeher.contacts',
  sos: 'safeher.sos',
  incidents: 'safeher.incidents',
  dispatch: 'safeher.dispatch',
  ambulance: 'safeher.ambulance',
  location: 'safeher.location'
};

// Cross-tab sync
window.addEventListener('storage', () => {
  if (typeof onDataChange === 'function') onDataChange();
});
