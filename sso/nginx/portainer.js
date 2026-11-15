// Starts Portainer's "Login with OAuth" by itself, so a visitor who already
// signed in with Authelia goes straight in. Tries once per tab, so a failed
// sign-in can't loop.
(function () {
  var KEY = 'sso-auto-login';
  var timer = null;
  function onAuthPage() {
    return /^#!\/auth/.test(location.hash);
  }
  function check() {
    if (!onAuthPage()) {
      sessionStorage.removeItem(KEY);
      return;
    }
    if (sessionStorage.getItem(KEY) || timer) return;
    var tries = 0;
    timer = setInterval(function () {
      var link = document.querySelector('a[href*="/api/oidc/authorization"]');
      if (link || ++tries > 50) {
        clearInterval(timer);
        timer = null;
        if (link && onAuthPage()) {
          sessionStorage.setItem(KEY, '1');
          location.href = link.href;
        }
      }
    }, 100);
  }
  window.addEventListener('hashchange', check);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', check);
  } else {
    check();
  }
})();
