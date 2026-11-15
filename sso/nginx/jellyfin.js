// Sends Jellyfin's login page straight to the SSO sign-in (once per tab, so a
// failed sign-in can't loop).
(function () {
  var KEY = 'sso-auto-login';
  function onLogin() {
    return /^#!?\/login/.test(location.hash) || /\/login(\.html)?$/.test(location.pathname);
  }
  function check() {
    if (!onLogin()) { sessionStorage.removeItem(KEY); return; }
    if (sessionStorage.getItem(KEY)) return;
    sessionStorage.setItem(KEY, '1');
    location.replace('/sso-login');
  }
  window.addEventListener('hashchange', check);
  check();
})();
