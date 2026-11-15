// n8n external hook (EXTERNAL_HOOK_FILES): signs the visitor in as the n8n
// owner when they already have an Authelia session.
//
// The session is checked by asking Authelia itself with the visitor's cookie,
// so a forged header can't sign anyone in. Webhooks and forms are left alone.
const { createRequire } = require('module');

const AUTHELIA = process.env.SSO_AUTHELIA_URL || 'http://authelia:9091';
const ALLOWED = (process.env.SSO_ALLOWED_USERS || 'sagan')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);
const N8N = process.env.SSO_N8N_PATH || '/usr/local/lib/node_modules/n8n/dist/index.js';
const AUTH_COOKIE = 'n8n-auth';
const SKIP = /^\/(webhook|webhook-test|webhook-waiting|form|form-test|form-waiting|healthz|metrics)(\/|$)/;

function readCookies(header) {
  const out = {};
  for (const part of (header || '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return out;
}

async function autheliaUser(req, session) {
  const host = req.headers['x-forwarded-host'] || req.headers.host;
  const res = await fetch(`${AUTHELIA}/api/authz/auth-request`, {
    headers: {
      'X-Original-URL': `https://${host}${req.originalUrl || req.url}`,
      'X-Original-Method': req.method,
      'X-Forwarded-For':
        String(req.headers['x-forwarded-for'] || '').split(',')[0].trim() ||
        req.socket.remoteAddress ||
        '',
      Cookie: `authelia_session=${session}`,
    },
    redirect: 'manual',
    signal: AbortSignal.timeout(5000),
  });
  if (res.status !== 200) return null;
  const user = res.headers.get('remote-user');
  return user && ALLOWED.includes(user) ? user : null;
}

module.exports = {
  n8n: {
    ready: [
      async function (server) {
        const load = createRequire(N8N);
        const { Container } = load('@n8n/di');
        const { UserRepository, GLOBAL_OWNER_ROLE } = load('@n8n/db');
        const { AuthService } = load('./auth/auth.service');
        const auth = Container.get(AuthService);
        const users = Container.get(UserRepository);

        server.app.use(async (req, res, next) => {
          try {
            if (SKIP.test(req.path)) return next();
            const cookies = readCookies(req.headers.cookie);
            if (!cookies.authelia_session) return next();
            if (cookies[AUTH_COOKIE]) {
              try {
                await auth.validateCookieToken(cookies[AUTH_COOKIE]);
                return next();
              } catch {
                // expired or signed out: issue a fresh one below
              }
            }
            if (!(await autheliaUser(req, cookies.authelia_session))) return next();
            const owner = await users.findOne({
              where: { role: { slug: GLOBAL_OWNER_ROLE.slug } },
              relations: ['role'],
            });
            if (!owner) return next();
            // Authelia already checked the sign-in, so n8n's own MFA step is skipped.
            const token = auth.issueJWT(owner, true);
            res.cookie(AUTH_COOKIE, token, {
              httpOnly: true,
              sameSite: 'lax',
              secure: true,
              maxAge: 7 * 24 * 3600 * 1000,
            });
            delete cookies[AUTH_COOKIE];
            cookies[AUTH_COOKIE] = token;
            req.headers.cookie = Object.entries(cookies)
              .map(([k, v]) => `${k}=${v}`)
              .join('; ');
          } catch (error) {
            console.warn('[sso-hook] skipped:', error.message);
          }
          next();
        });

        // Run before n8n's own cookie parsing and auth checks.
        const stack = (server.app.router || server.app._router).stack;
        stack.unshift(stack.pop());
      },
    ],
  },
};
