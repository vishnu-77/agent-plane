// Run against a built local console. Requires external Playwright/Chromium tooling.
// Account API fixtures never create real users or modify the running instance.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const base = process.env.CONSOLE_URL || 'http://127.0.0.1:8000/console/';
const artifacts = path.join(os.tmpdir(), 'agent-plane-public-qa');
fs.mkdirSync(artifacts, { recursive: true });
const defaultState = { users: 0, first_run: true, signup_open: true, demo_available: true, demo_token: 'fixture-demo', password_login: true, sso_available: false };

(async () => {
  const browser = await chromium.launch({ headless: true });
  let checks = 0;
  async function scenario(name, options, run) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    const writes = [];
    let signedIn = !!options.signedIn;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/v1/**', async route => {
      const req = route.request(), endpoint = new URL(req.url()).pathname;
      const respond = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (req.method() !== 'GET') writes.push(endpoint);
      if (endpoint === '/v1/auth/state') return respond(options.stateFailure ? { detail: 'offline' } : { ...defaultState, ...options.state }, options.stateFailure ? 503 : 200);
      if (endpoint === '/v1/auth/me') return signedIn
        ? respond({ user: { id: 'fixture', name: 'Fixture', email: 'fixture@example.test' }, workspaces: [], projects: options.expire ? [{ id: 'fixture-project', name: 'Fixture project', mode: 'observe', collection: {}, created_at: '', keys: 0, integrations: 0, connected: 0, rules: 0 }] : [], onboarded: !!options.expire })
        : respond({ detail: 'Not signed in' }, 401);
      if (endpoint === '/v1/auth/signup' || endpoint === '/v1/auth/login') {
        if (options.reject) return respond({ detail: 'That email and password do not match' }, 401);
        signedIn = true;
        return respond({ user: { id: 'fixture' } });
      }
      if (endpoint === '/v1/auth/logout') { signedIn = false; return respond({}); }
      return respond({ detail: 'Session ended' }, options.expire ? 401 : 404);
    });
    try {
      await run(page, writes);
      assert.deepEqual(errors, [], 'No page crashes');
      checks++;
      console.log(`PASS ${name}`);
    } finally { await context.close(); }
  }
  try {
    await scenario('home, navigation, Back, refresh, and signup to onboarding', {}, async (page, writes) => {
      await page.goto(base);
      await page.getByRole('heading', { name: /Every action.*Explicit authority/ }).waitFor();
      await page.screenshot({ path: path.join(artifacts, 'home-desktop.png'), fullPage: true });
      await page.getByRole('link', { name: 'Create an account', exact: true }).click();
      await page.getByRole('heading', { name: 'Create your account' }).waitFor();
      assert.equal(new URL(page.url()).hash, '#/signup');
      await page.reload();
      await page.getByRole('heading', { name: 'Create your account' }).waitFor();
      await page.getByRole('link', { name: 'I already have an account' }).click();
      await page.getByRole('heading', { name: 'Sign in', exact: true }).waitFor();
      await page.goBack();
      await page.getByRole('heading', { name: 'Create your account' }).waitFor();
      await page.screenshot({ path: path.join(artifacts, 'signup-desktop.png'), fullPage: true });
      await page.getByLabel('Name', { exact: true }).fill('Fixture');
      await page.getByLabel('Email', { exact: true }).fill('fixture@example.test');
      await page.getByLabel('Password', { exact: false }).fill('fixture-password-123');
      await page.getByRole('button', { name: 'Create account', exact: true }).click();
      await page.getByRole('heading', { name: 'Create a project' }).waitFor();
      assert.deepEqual(writes, ['/v1/auth/signup']);
    });
    await scenario('closed signup never presents a signup form', { state: { users: 1, first_run: false, signup_open: false } }, async page => {
      await page.goto(base + '#/signup');
      await page.getByText('Account creation is closed', { exact: false }).waitFor();
      assert.equal(await page.getByRole('button', { name: 'Create account', exact: true }).count(), 0);
      await page.getByRole('link', { name: 'I already have an account' }).click();
      await page.getByLabel('Email', { exact: true }).waitFor();
    });
    await scenario('first-run flag cannot override closed registration', { state: { signup_open: false } }, async page => {
      await page.goto(base);
      await page.getByRole('link', { name: 'Open your workspace' }).waitFor();
      assert.equal(await page.getByRole('link', { name: 'Create an account' }).count(), 0);
    });
    await scenario('SSO-only errors remain visible, including malformed encoding', { state: { sso_available: true, password_login: false } }, async page => {
      await page.goto(base + '#/?sso_error=Access%20refused%ZZ');
      await page.getByRole('heading', { name: 'Sign in', exact: true }).waitFor();
      await page.getByRole('alert').filter({ hasText: 'Access refused' }).waitFor();
      assert.equal(await page.locator('input[type=password]').count(), 0);
      await page.getByRole('button', { name: 'Continue with single sign-on' }).waitFor();
    });
    await scenario('disabled password access without SSO has an explanation', { state: { password_login: false, sso_available: false } }, async page => {
      await page.goto(base + '#/login');
      await page.getByText('Password sign-in is disabled.', { exact: false }).waitFor();
      assert.equal(await page.locator('input[type=password]').count(), 0);
    });
    await scenario('account-service failure offers retry and no broken signup form', { stateFailure: true }, async page => {
      await page.goto(base + '#/signup');
      await page.getByRole('alert').filter({ hasText: 'Cannot reach the account service' }).waitFor();
      await page.getByRole('button', { name: 'Retry connection' }).waitFor();
      assert.equal(await page.locator('input').count(), 0);
    });
    await scenario('failed login is visible and stays on login', { reject: true }, async page => {
      await page.goto(base + '#/login');
      await page.getByLabel('Email', { exact: true }).fill('fixture@example.test');
      await page.getByLabel('Password', { exact: true }).fill('incorrect-password');
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'do not match' }).waitFor();
      assert.equal(new URL(page.url()).hash, '#/login');
    });
    await scenario('authenticated visitors bypass signup', { signedIn: true }, async page => {
      await page.goto(base + '#/signup');
      await page.getByRole('heading', { name: 'Create a project' }).waitFor();
    });
    await scenario('session expiry goes to login and still permits returning home', { signedIn: true, expire: true }, async page => {
      await page.goto(base);
      await page.getByRole('alert').filter({ hasText: 'Your session ended' }).waitFor();
      await page.getByRole('link', { name: 'Back to home' }).click();
      await page.getByRole('heading', { name: /Every action.*Explicit authority/ }).waitFor();
    });
    await scenario('mobile home and signup stay within viewport; links have keyboard focus', {}, async page => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(base);
      await page.getByRole('link', { name: 'Create an account' }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: path.join(artifacts, 'home-mobile.png'), fullPage: true });
      await page.getByRole('link', { name: 'Create an account' }).click();
      await page.getByRole('heading', { name: 'Create your account' }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: path.join(artifacts, 'signup-mobile.png'), fullPage: true });
      await page.getByRole('button', { name: 'See the demo instead' }).focus();
      await page.keyboard.press('Tab');
      assert.equal(await page.getByRole('link', { name: 'Back to home' }).evaluate(el => el === document.activeElement), true);
      assert.equal(await page.getByRole('link', { name: 'Back to home' }).evaluate(el => getComputedStyle(el).outlineStyle), 'solid');
    });
    console.log(`${checks} public-page scenarios passed. Screenshots: ${artifacts}`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
