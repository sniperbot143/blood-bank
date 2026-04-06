// Authentication logic
const $ = (id) => document.getElementById(id);

const AUTH_KEY = 'safeher.users';
const SESSION_KEY = 'safeher.session';

// Check if already logged in - redirect to dashboard
(function checkSession() {
  const session = DB.get(SESSION_KEY, null);
  if (session) {
    const pages = { user: 'user.html', police: 'police.html', hospital: 'hospital.html' };
    const target = pages[session.role];
    // Only redirect if not already on the target page
    if (target && !window.location.href.includes(target)) {
      window.location.href = target;
    }
  }
})();

// Wait for DOM to be ready
document.addEventListener('DOMContentLoaded', () => {

  // Toggle login/register forms
  const showRegBtn = document.getElementById('showRegister');
  const showLogBtn = document.getElementById('showLogin');
  const loginBox = document.getElementById('loginBox');
  const registerBox = document.getElementById('registerBox');

  if (showRegBtn) {
    showRegBtn.onclick = (e) => {
      e.preventDefault();
      loginBox.style.display = 'none';
      registerBox.style.display = 'block';
    };
  }
  if (showLogBtn) {
    showLogBtn.onclick = (e) => {
      e.preventDefault();
      registerBox.style.display = 'none';
      loginBox.style.display = 'block';
    };
  }

  // Show/hide role-specific fields on login form
  document.querySelectorAll('input[name="role"]').forEach(r => {
    r.onchange = () => {
      document.getElementById('policeFields').style.display = r.value === 'police' ? 'flex' : 'none';
      document.getElementById('hospitalFields').style.display = r.value === 'hospital' ? 'flex' : 'none';
    };
  });

  // Show/hide role-specific fields on register form
  document.querySelectorAll('input[name="regRole"]').forEach(r => {
    r.onchange = () => {
      document.getElementById('regPoliceFields').style.display = r.value === 'police' ? 'flex' : 'none';
      document.getElementById('regHospitalFields').style.display = r.value === 'hospital' ? 'flex' : 'none';
    };
  });

  // Register
  document.getElementById('registerBtn').onclick = () => {
    const name = document.getElementById('regName').value.trim();
    const email = document.getElementById('regEmail').value.trim().toLowerCase();
    const phone = document.getElementById('regPhone').value.trim();
    const pass = document.getElementById('regPass').value;
    const confirm = document.getElementById('regConfirm').value;
    const role = document.querySelector('input[name="regRole"]:checked').value;

    if (!name || !email || !phone || !pass) {
      return showError('regError', 'All fields are required.');
    }
    if (!email.includes('@')) {
      return showError('regError', 'Enter a valid email.');
    }
    if (pass.length < 6) {
      return showError('regError', 'Password must be at least 6 characters.');
    }
    if (pass !== confirm) {
      return showError('regError', 'Passwords do not match.');
    }

    const users = DB.get(AUTH_KEY, []);
    if (users.find(u => u.email === email)) {
      return showError('regError', 'Email already registered. Please login.');
    }

    const user = { id: uid(), name, email, phone, password: pass, role, createdAt: now() };

    if (role === 'police') {
      user.badge = document.getElementById('regPoliceBadge').value.trim();
      user.station = document.getElementById('regPoliceStation').value.trim();
    } else if (role === 'hospital') {
      user.hospitalName = document.getElementById('regHospitalName').value.trim();
      user.department = document.getElementById('regHospitalDept').value.trim();
    }

    users.push(user);
    DB.set(AUTH_KEY, users);

    const session = { id: user.id, name: user.name, email: user.email, role: user.role, phone: user.phone };
    DB.set(SESSION_KEY, session);

    if (role === 'user') {
      DB.set(K.profile, { name: user.name, phone: user.phone });
    }

    const pages = { user: 'user.html', police: 'police.html', hospital: 'hospital.html' };
    window.location.href = pages[role] || 'user.html';
  };

  // Login
  document.getElementById('loginBtn').onclick = () => {
    const email = document.getElementById('loginEmail').value.trim().toLowerCase();
    const pass = document.getElementById('loginPass').value;
    const role = document.querySelector('input[name="role"]:checked').value;

    if (!email || !pass) {
      return showError('loginError', 'Enter email and password.');
    }

    const users = DB.get(AUTH_KEY, []);
    const user = users.find(u => u.email === email && u.password === pass && u.role === role);

    if (!user) {
      return showError('loginError', 'Invalid credentials or wrong role selected.');
    }

    const session = { id: user.id, name: user.name, email: user.email, role: user.role, phone: user.phone };
    DB.set(SESSION_KEY, session);

    const pages = { user: 'user.html', police: 'police.html', hospital: 'hospital.html' };
    window.location.href = pages[role] || 'user.html';
  };

});

function showError(id, msg) {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.className = 'status error';
  setTimeout(() => { el.textContent = ''; el.className = 'status'; }, 4000);
}
