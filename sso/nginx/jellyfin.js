// Keeps Jellyfin's own login page out of sight: the browser goes to the SSO sign-in
// (/sso-login) whenever it has no Jellyfin session or Jellyfin tries to show its login page.
// Tries once per tab, so a failed sign-in can't loop.
(function () {
  var KEY = 'sso-auto-login';
  function go() {
    if (sessionStorage.getItem(KEY)) return;
    sessionStorage.setItem(KEY, '1');
    location.replace('/sso-login');
  }
  function hasSession() {
    try {
      var c = JSON.parse(localStorage.getItem('jellyfin_credentials') || '{}');
      return (c.Servers || []).some(function (s) { return s && s.AccessToken && s.UserId; });
    } catch (e) { return false; }
  }
  function onLogin() {
    return /(^|[#/])login(\.html)?([?/]|$)/i.test(location.hash) || /\/login(\.html)?$/i.test(location.pathname)
      || /quickconnect|selectserver|forgotpassword/i.test(location.hash);
  }
  function check() {
    if (onLogin()) go();
    else if (hasSession()) sessionStorage.removeItem(KEY);
  }
  // 1. No session at all: go before Jellyfin draws anything.
  if (!hasSession()) { go(); return; }
  // 2. Jellyfin changes pages without reloading, by hash and by history: watch both.
  ['pushState', 'replaceState'].forEach(function (m) {
    var orig = history[m];
    history[m] = function () { var r = orig.apply(this, arguments); setTimeout(check, 0); return r; };
  });
  window.addEventListener('hashchange', check);
  window.addEventListener('popstate', check);
  setInterval(check, 1500);
  check();
})();
