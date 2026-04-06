// Authentication logic
const $ = (id) => document.getElementById(id);

const AUTH_KEY = 'safeher.users';
const SESSION_KEY = 'safeher.session';

// Check if already logged in
(function checkSession() {
  const session = DB.get(SESSION_KEY, null);
  if (session) {
    redirectToRole(session.role);
  }
})();

// Toggle login/register forms
$('showRegister').onclick = (e) => {
  e.preventDefault();
  $('loginBox').style.display = 'none';
  $('registerBox').style.display = 'block';
};
$('showLogin').onclick = (e) => {
  e.preventDefault();
  $('registerBox').style.display = 'none';
  $('loginBox').style.display = 'block';
};

// Show/hide role-specific fields on login form
document.querySelectorAll('input[name="role"]').forEach(r => {
  r.onchange = () => {
    $('policeFields').style.display = r.value === 'police' ? 'flex' : 'none';
    $('hospitalFields').style.display = r.value === 'hospital' ? 'flex' : 'none';
  };
});

// Show/hide role-specific fields on register form
document.querySelectorAll('input[name="regRole"]').forEach(r => {
  r.onchange = () => {
    $('regPoliceFields').style.display = r.value === 'police' ? 'flex' : 'none';
    $('regHospitalFields').style.display = r.value === 'hospital' ? 'flex' : 'none';
  };
});

// Register
$('registerBtn').onclick = () => {
  const name = $('regName').value.trim();
  const email = $('regEmail').value.trim().toLowerCase();
  const phone = $('regPhone').value.trim();
  const pass = $('regPass').value;
  const confirm = $('regConfirm').value;
  const role = document.querySelector('input[name="regRole"]:checked').value;

  // Validation
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

  const user = {
    id: uid(),
    name,
    email,
    phone,
    password: pass,
    role,
    createdAt: now()
  };

  // Add role-specific info
  if (role === 'police') {
    user.badge = $('regPoliceBadge').value.trim();
    user.station = $('regPoliceStation').value.trim();
  } else if (role === 'hospital') {
    user.hospitalName = $('regHospitalName').value.trim();
    user.department = $('regHospitalDept').value.trim();
  }

  users.push(user);
  DB.set(AUTH_KEY, users);

  // Auto-login after register
  const session = { id: user.id, name: user.name, email: user.email, role: user.role, phone: user.phone };
  DB.set(SESSION_KEY, session);

  // Save profile for user role
  if (role === 'user') {
    DB.set(K.profile, { name: user.name, phone: user.phone });
  }

  redirectToRole(role);
};

// Login
$('loginBtn').onclick = () => {
  const email = $('loginEmail').value.trim().toLowerCase();
  const pass = $('loginPass').value;
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
  redirectToRole(role);
};

function redirectToRole(role) {
  const pages = { user: 'user.html', police: 'police.html', hospital: 'hospital.html' };
  window.location.href = pages[role] || 'user.html';
}

function showError(id, msg) {
  const el = $(id);
  el.textContent = msg;
  el.className = 'status error';
  setTimeout(() => { el.textContent = ''; el.className = 'status'; }, 4000);
}
