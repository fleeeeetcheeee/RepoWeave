import {defineConfig} from '@playwright/test';
import path from 'node:path';

const live = process.env.REPOWEAVE_LIVE_DEMO === '1';
const root = process.env.REPOWEAVE_BROWSER_DEMO_ROOT || path.resolve('..', '.repoweave', 'demos', `${live ? 'live' : 'e2e'}-${Date.now()}`);
process.env.REPOWEAVE_BROWSER_DEMO_ROOT = root;
const mode = live ? '--live' : '--test';
export default defineConfig({
  testDir: './tests', workers: 1, retries: 0, timeout: live ? 600_000 : 180_000,
  expect: {timeout: live ? 300_000 : 90_000},
  use: {baseURL: 'http://127.0.0.1:5173', viewport: {width: 1440, height: 1000},
    trace: 'retain-on-failure', screenshot: 'only-on-failure', channel: 'chrome'},
  webServer: [
    {command: `../.venv/bin/python ../scripts/dashboard_backend.py api --root "${root}" ${mode}`,
      url: 'http://127.0.0.1:8000/health', reuseExistingServer: false, timeout: 30_000},
    {command: `../.venv/bin/python ../scripts/dashboard_backend.py worker --root "${root}" ${mode}`,
      wait: {stdout: /worker-ready/}, timeout: 30_000},
    {command: 'npm run dev', url: 'http://127.0.0.1:5173', reuseExistingServer: false, timeout: 30_000},
  ],
});
