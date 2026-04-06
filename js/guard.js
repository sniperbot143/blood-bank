// Route guard - protects pages based on login session and role
(function () {
  const session = DB.get('safeher.session', null);

  // Not logged in -> redirect to login
  if (!session) {
    window.location.href = 'index.html';
    return;
  }

  // Check role matches the page
  const page = window.location.pathname.split('/').pop();
  const allowedPages = {
    user: 'user.html',
    police: 'police.html',
    hospital: 'hospital.html'
  };

  if (allowedPages[session.role] !== page) {
    window.location.href = allowedPages[session.role] || 'index.html';
    return;
  }

  // Set user info in topbar
  const userInfo = document.getElementById('userInfo');
  if (userInfo) {
    userInfo.textContent = session.name || session.email;
  }

  // Logout button
  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) {
    logoutBtn.onclick = () => {
      localStorage.removeItem('safeher.session');
      window.location.href = 'index.html';
    };
  }
})();
