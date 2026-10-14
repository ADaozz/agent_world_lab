// Trusted executor initialization. This file is never exposed as an agent capability.
const dns = require('node:dns').promises;
const fs = require('node:fs');
const path = require('node:path');
const allowed = new Set(['https://example.com', 'https://demo.playwright.dev',
  'https://github.com', 'https://github.githubassets.com', 'https://avatars.githubusercontent.com',
  'https://raw.githubusercontent.com']);

async function permitted(raw) {
  try {
    const url = new URL(raw);
    if (!allowed.has(url.origin) || url.username || url.password) return false;
    if (url.hostname === 'raw.githubusercontent.com' && !url.pathname.startsWith('/ADaozz/agent_world_lab/')) return false;
    const addresses = await dns.lookup(url.hostname, { all: true });
    // Only trusted public origins; reject private/link-local DNS answers as an extra check.
    return addresses.length > 0 && addresses.every(({address}) => {
      if (address.includes(':')) {
        return /^[23][0-9a-f]{3}:/.test(address) && !address.startsWith('2001:db8:');
      }
      const [a,b] = address.split('.').map(Number);
      return !([0,10,127,169].includes(a) || (a===172 && b>=16 && b<=31) ||
               (a===192 && (b===168 || b===0)) || (a===100 && b>=64 && b<=127) ||
               a>=224 || (a===198 && (b===18 || b===19)));
    });
  } catch { return false; }
}

exports.permitted = permitted;
exports.default = async ({page}) => {
  const context = page.context();
  if (!context.__worldGuard) {
    context.__worldGuard = true;
    await context.routeWebSocket('**/*', ws => ws.close());
    await context.route('**/*', async route => {
      const request = route.request();
      if (!['GET','HEAD'].includes(request.method()) || !(await permitted(request.url()))) {
        await route.abort('blockedbyclient'); return;
      }
      try {
        // Never follow redirects in the network client. Redirects are refused, including trusted ones.
        const response = await route.fetch({ maxRedirects: 0, timeout: 10000 });
        if ((response.status() >= 300 && response.status() < 400) ||
            /attachment/i.test(response.headers()['content-disposition'] || '')) {
          await route.abort('blockedbyclient');
        } else {
          await route.fulfill({ response });
        }
        await response.dispose();
      } catch { await route.abort('blockedbyclient').catch(() => {}); }
    });
    context.on('page', p => {
      // Only one isolated tab; popups are closed and their requests remain context-routed.
      if (p !== page) p.close().catch(() => {});
    });
  }
  page.on('download', download => download.cancel().catch(() => {}));
  fs.writeFileSync(path.join(__dirname, 'guard-ready'), 'v1');
};
