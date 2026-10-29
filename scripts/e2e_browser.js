// Browser end-to-end check. Needs a FRESH database (it starts from the empty state).
//   make reset-demo && make dev   (in another terminal)
//   AEGIS_E2E_PASSWORD=... node scripts/e2e_browser.js
// Requires: npm i -D playwright && npx playwright install chromium
const { chromium } = require('playwright');
const BASE = process.env.AEGIS_E2E_URL || BASE;
const PW = process.env.AEGIS_E2E_PASSWORD;
const OUT = process.env.AEGIS_E2E_SHOTS || 'e2e-screenshots';
require('fs').mkdirSync(OUT, { recursive: true });
if (!PW) { console.error('Set AEGIS_E2E_PASSWORD to the admin password'); process.exit(2); }
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  p.on('pageerror', e => errors.push('PAGEERROR ' + e.message));
  global.P=p; const shot = (n) => p.screenshot({ path: `${OUT}/${n}.png`, fullPage: true });
  await p.goto(BASE + '/');
  await p.fill('input[autocomplete=current-password]', PW);
  await p.click('button[type=submit]');
  await p.waitForSelector('text=No activity recorded yet');
  await shot('01-overview-empty');
  await p.click('button:has-text("Run the quick demo")');
  await p.waitForURL(/runs\//);
  await p.waitForSelector('text=Metrics', { timeout: 30000 });
  await p.waitForFunction(() => !document.querySelector('.progress') && document.body.innerText.includes('Reproducibility'), null, { timeout: 60000 });
  await p.waitForTimeout(800);
  await shot('02-run-detail');
  // expand a failed/attacked row
  await p.click('text=Indirect injection in an inbox message');
  await p.waitForTimeout(300);
  await shot('03-run-expanded');
  await p.click('text=Open trace >> nth=0');
  await p.waitForSelector('text=Timeline');
  await p.waitForTimeout(500);
  await shot('04-trace');
  // runtime session
  await p.goto('' + BASE + '/runtime');
  await p.waitForSelector('text=Run an agent through SENTINEL');
  await p.click('button:has-text("Start session")');
  await p.waitForSelector('text=Final result', { timeout: 20000 });
  await p.waitForTimeout(500);
  await shot('05-runtime-session');
  // direct call -> approval
  await p.selectOption('select >> nth=3', 'inventory.adjust_stock');
  await p.fill('textarea.code', '{"sku":"SKU-101","delta":5}');
  await p.click('button:has-text("Evaluate and run")');
  await p.waitForSelector('text=Needs approval');
  await p.waitForTimeout(800);
  await p.click('button:text-is("Approve")');
  await p.waitForTimeout(500);
  await p.click('button[aria-pressed]:text-is("Approved")');
  await p.waitForSelector('text=Execute approved action');
  await p.click('text=Execute approved action');
  await p.waitForTimeout(800);
  await shot('06-runtime-approvals');
  await p.goto('' + BASE + '/live'); await p.waitForSelector('text=Event detail'); await p.waitForTimeout(800);
  await p.click('table.t tbody tr >> nth=0'); await p.waitForTimeout(200); await shot('07-live');
  // second run guarded and investigation
  await p.goto('' + BASE + '/tests'); await p.waitForSelector('text=Start a test run');
  await p.selectOption('select >> nth=0', { label: 'Ops assistant v2 (guarded)' });
  await p.click('button:has-text("Start test run")');
  await p.waitForURL(/runs\//); await p.waitForTimeout(1500); await p.waitForFunction(() => !document.querySelector('.progress') && document.body.innerText.includes('Reproducibility'), null, { timeout: 90000 });
  await p.waitForTimeout(500);
  await p.click('button:has-text("Open investigation")');
  await p.waitForURL(/investigations\//); await p.waitForSelector('text=Regression comparison');
  const sel = p.locator('select[aria-label="Baseline run"]');
  const opts = await sel.locator('option').allTextContents();
  await sel.selectOption({ index: opts.findIndex(o => o.includes('ops-naive')) });
  await p.waitForSelector('text=shared scenarios'); await p.waitForTimeout(500);
  await shot('08-investigation');
  await p.click('button:has-text("Generate report")'); await p.waitForURL(/reports\//); await p.waitForTimeout(1200); await shot('09-report');
  for (const [path, name] of [['/analytics','10-analytics'],['/policies','11-policies'],['/agents','12-agents'],['/traces','13-traces'],['/integrations','14-integrations'],['/settings','15-settings'],['/','16-overview-full'],['/privacy','17-privacy']]) {
    await p.goto(BASE + path); await p.waitForTimeout(1200); await shot(name);
  }
  await p.emulateMedia({ colorScheme: 'dark' }); await p.goto(BASE + '/'); await p.waitForTimeout(1200); await shot('18-overview-dark');
  await p.goto('' + BASE + '/analytics'); await p.waitForTimeout(1000); await shot('19-analytics-dark');
  await p.setViewportSize({ width: 390, height: 844 }); await p.goto('' + BASE + '/runtime'); await p.waitForTimeout(1000); await shot('20-mobile');
  const sw = await p.evaluate(() => document.documentElement.scrollWidth); console.log('mobile scrollWidth', sw);
  console.log('ERRORS', JSON.stringify(errors.slice(0, 20)));
  if (errors.length || sw > 390) { console.error('E2E FAILED'); process.exit(1); }
  console.log('E2E PASSED');
  await b.close();
})().catch(async e => { console.error('FAILED', e.message); try{await global.P.screenshot({path:`${OUT}/fail.png`,fullPage:true}); console.log('URL', global.P.url());}catch(x){} process.exit(1); });
