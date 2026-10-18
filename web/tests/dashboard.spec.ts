import {test, expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import path from 'node:path';

test('real engine: capture, benchmark, promote, old decode, cloud receipt and refresh', async ({page, request}, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  const live = process.env.REPOWEAVE_LIVE_DEMO === '1';
  const root = process.env.REPOWEAVE_BROWSER_DEMO_ROOT!;
  const exports = path.join(root, 'exports'); mkdirSync(exports, {recursive: true});
  const receipt: Record<string, unknown> = {mode: live ? 'LIVE Factory and Daytona' : 'MOCK Factory and Daytona; real engine', root};
  const api = 'http://127.0.0.1:8000';
  const screenshot = async (name: string) => {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({path: path.join(exports, name), fullPage: true});
  };
  const start = async (name: string) => {
    const previous = new URL(page.url()).searchParams.get('run');
    await page.getByRole('button', {name, exact: true}).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('run')).not.toBe(previous);
  };
  const waitFinished = async () => {
    await expect(page.getByTestId('run-status')).toHaveText(/^(completed|promoted|awaiting-approval|completed-without-improvement)$/);
  };
  const currentRun = async () => {
    const id = new URL(page.url()).searchParams.get('run')!;
    const response = await request.get(`${api}/runs/${id}`);
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const decode = (archive: string, name: string) => {
    const python = path.resolve('..', '.venv/bin/python');
    const restored = path.join(exports, name);
    const env = {PATH: process.env.PATH!, REPOWEAVE_DATA_DIR: path.join(exports, 'decoder-data')};
    execFileSync(python, ['-m', 'repoweave.cli', 'unpack', archive, '-o', restored], {cwd: exports, env, stdio: 'pipe'});
    execFileSync(python, ['-m', 'repoweave.cli', 'verify', path.join(root, 'owned-fixture'), restored], {cwd: exports, env, stdio: 'pipe'});
    expect(readFileSync(path.join(restored, 'module_0.py'))).toEqual(readFileSync(path.join(root, 'owned-fixture/module_0.py')));
  };
  try {
    await page.goto('/');
    await page.getByRole('button', {name: 'Capture snapshot'}).click();
    await expect(page.getByRole('heading', {name: 'Repository structure'})).toBeVisible();
    await expect(page.getByRole('img', {name: 'Repository import graph'})).toBeVisible();
    await page.getByText('Excluded entries (1)', {exact: true}).click();
    await expect(page.getByText('repository-metadata/build/data', {exact: false})).toBeVisible();
    await screenshot('repository.png');
    await page.getByLabel('Use active policy').uncheck();
    await page.getByLabel('Codec', {exact: true}).selectOption('stored');
    await start('Create archive');
    await waitFinished();
    const initial = await currentRun(); receipt.initial_run = initial.id;
    await expect(page.getByText('✓ Exact reconstruction verified')).toBeVisible();
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('link', {name: 'Download archive'}).click();
    const download = await downloadPromise;
    const oldArchive = path.join(exports, 'original.rpw'); await download.saveAs(oldArchive);
    decode(oldArchive, 'original-decoded');
    // Close one real event replay early, then require a cursor-based reconnect.
    const eventCursors: number[] = [];
    let cutStream = false;
    await page.route('**/runs/*/events?*', async route => {
      const url = new URL(route.request().url());
      eventCursors.push(Number(url.searchParams.get('after')));
      if (cutStream) {await route.continue(); return;}
      cutStream = true;
      url.searchParams.set('follow', 'false');
      const response = await request.get(url.toString());
      await route.fulfill({response, headers: {...response.headers(),
        'access-control-allow-origin': 'http://127.0.0.1:5173'}});
    });
    await start('Run benchmark');
    await waitFinished();
    await expect.poll(() => eventCursors.some(cursor => cursor > 0)).toBeTruthy();
    await page.unroute('**/runs/*/events?*');
    receipt.sse_reconnected_after_sequence = Math.max(...eventCursors);
    await expect(page.getByRole('rowheader', {name: 'tar-zstd', exact: true})).toBeVisible();
    await expect(page.getByText('Exact bytes', {exact: true})).toHaveCount(4);
    receipt.benchmark_run = (await currentRun()).id;
    await screenshot('benchmarks.png');
    await page.getByText('Optimization settings', {exact: true}).click();
    await start('Start optimization');
    await expect(page.getByTestId('run-status')).toHaveText('awaiting-approval');
    await expect(page.getByRole('button', {name: 'Approve & promote'})).toBeEnabled();
    receipt.local_optimization = (await currentRun()).id;
    const beforeRefresh = page.url(); await page.reload();
    await expect(page.getByTestId('run-status')).toHaveText('awaiting-approval');
    expect(page.url()).toBe(beforeRefresh);
    await expect(page.locator('.timeline li')).not.toHaveCount(0);
    await page.getByRole('button', {name: 'Approve & promote'}).click();
    await expect(page.getByTestId('run-status')).toHaveText('promoted');
    await screenshot('optimization.png');
    const promoted = await currentRun();
    const active = await (await request.get(`${api}/repositories/${promoted.repository_id}/policy`)).json();
    expect(active.policy_id).toBe(promoted.reports.decision.candidate_id);
    decode(oldArchive, 'old-decoded-after-promotion');
    await start('Create archive');
    await waitFinished();
    const packed = await currentRun(); expect(packed.policy).toEqual(active.policy);
    const file = packed.artifacts.find((a: {kind: string}) => a.kind === 'archive');
    writeFileSync(path.join(exports, 'promoted.rpw'), await (await request.get(`${api}/artifacts/${file.id}`)).body());
    decode(path.join(exports, 'promoted.rpw'), 'promoted-decoded');
    // A new snapshot has no active policy; existing snapshots must optimize from their active parent.
    writeFileSync(path.join(root, 'owned-fixture', 'v4-empty.txt'), '');
    await page.getByRole('button', {name: 'Capture snapshot'}).click();
    await expect(page.getByRole('heading', {name: 'Repository structure'})).toBeVisible();
    await page.getByLabel('Use active policy').uncheck();
    const formats = [];
    let v4Snapshot = '';
    for (const [codec, metadata] of [['zstd', 'compressed'], ['zstd', 'compact'], ['lzma', 'compact']]) {
      await page.getByLabel('Codec', {exact: true}).selectOption(codec);
      await page.getByLabel('Metadata', {exact: true}).selectOption(metadata);
      await page.getByLabel('Shared compression blocks').selectOption('8388608');
      await start('Create archive');
      await waitFinished();
      const v4 = await currentRun();
      v4Snapshot = v4.repository_id;
      expect(v4.policy).toMatchObject({codec, metadata_encoding: metadata, solid_block_bytes: 8388608});
      const artifact = v4.artifacts.find((a: {kind: string}) => a.kind === 'archive');
      const data = await (await request.get(`${api}/artifacts/${artifact.id}`)).body();
      expect(data.readUInt16BE(8)).toBe(4);
      expect(data[18]).toBe(metadata === 'compressed' ? 0 : 1);
      const name = `v4-${codec}-${metadata}`;
      const archive = path.join(exports, `${name}.rpw`);
      writeFileSync(archive, data);
      decode(archive, `${name}-decoded`);
      formats.push({codec, metadata, run_id: v4.id, bytes: data.length});
    }
    receipt.v4_formats = formats;
    await page.getByText('Optimization settings', {exact: true}).click();
    await page.getByLabel('Experiment execution').selectOption('daytona');
    await expect(page.getByRole('button', {name: 'Start optimization'})).toBeDisabled();
    await page.getByLabel(/I approve uploading snapshot/).check();
    await start('Start optimization');
    await waitFinished();
    let cloud = await currentRun();
    expect(cloud.policy).toMatchObject({codec: 'lzma', metadata_encoding: 'compact', solid_block_bytes: 8388608});
    await expect(page.getByRole('heading', {name: 'Remote reconstruction showcase'})).toBeVisible();
    expect(cloud.reports['independent-decode'].snapshot_id).toBe(v4Snapshot);
    expect(cloud.sandboxes).toEqual([{role: 'decoder', status: 'deleted'}, {role: 'experiment', status: 'deleted'}]);
    if (cloud.status === 'awaiting-approval') {
      await page.getByRole('button', {name: 'Approve & promote'}).click();
      await expect(page.getByTestId('run-status')).toHaveText('promoted'); cloud = await currentRun();
    } else {
      await expect(page.getByRole('button', {name: 'Approve & promote'})).toBeDisabled();
      await expect(page.getByText('No verified improvement. The active policy was retained.')).toBeVisible();
    }
    for (const artifact of cloud.artifacts) {
      const response = await request.get(`${api}/artifacts/${artifact.id}`);
      expect(response.ok()).toBeTruthy();
      writeFileSync(path.join(exports, artifact.id + (artifact.kind === 'archive' ? '.rpw' : '.json')), await response.body());
    }
    receipt.cloud_run = cloud.id; receipt.cloud_status = cloud.status; receipt.sandboxes = cloud.sandboxes;
    await page.reload(); await expect(page.getByTestId('run-status')).toHaveText(cloud.status);
    await screenshot('cloud.png');
    await page.setViewportSize({width: 390, height: 844});
    await screenshot('mobile.png');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    // Demo view: the six onstage beats for the cloud run, then presenter navigation.
    await page.setViewportSize({width: 1440, height: 1000});
    await page.getByRole('navigation', {name: 'Workspace views'}).getByRole('button', {name: /Demo/}).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('run')).toBe(cloud.id);
    for (const title of ['Captured input', 'Proposal and measured result', 'Remote experiment',
      'Independent decoder receipt', 'Sandbox cleanup', 'Portable archive']) {
      await expect(page.getByRole('heading', {name: title, exact: true})).toBeVisible();
    }
    await expect(page.getByText('✓ Byte-for-byte match with the original snapshot')).toBeVisible();
    await expect(page.getByText('✓ Deleted', {exact: true})).toHaveCount(2);
    await expect(page.getByText('Ran in a Daytona sandbox', {exact: true})).toBeVisible();
    await expect(page.getByRole('link', {name: 'Download archive'})).toHaveAttribute('href', new RegExp(`/artifacts/${cloud.artifacts.find((a: {kind: string}) => a.kind === 'archive').id}$`));
    const demoDownloadPromise = page.waitForEvent('download');
    await page.getByRole('link', {name: 'Download archive'}).click();
    const demoDownload = await demoDownloadPromise;
    const demoArchive = path.join(exports, demoDownload.suggestedFilename());
    await demoDownload.saveAs(demoArchive);
    await expect(page.locator('#beat-6')).toContainText(`repoweave unpack ${demoDownload.suggestedFilename()} -o restored`);
    decode(demoArchive, 'demo-download-decoded');
    await page.getByLabel('Demo run').selectOption(String(receipt.local_optimization));
    await expect(page.getByText('Ran on this computer', {exact: true})).toBeVisible();
    await page.getByLabel('Demo run').selectOption(cloud.id);
    await expect(page.getByText('Ran in a Daytona sandbox', {exact: true})).toBeVisible();
    await page.getByRole('button', {name: 'Present demo'}).click();
    await expect(page.locator('.sidebar')).toBeHidden();
    await expect(page.locator('.beat:visible')).toHaveCount(1);
    await expect(page.getByRole('button', {name: 'Previous beat'})).toBeDisabled();
    expect(new URL(page.url()).searchParams.get('present')).toBe('1');
    await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('button', {name: /Independent decoder receipt/})).toHaveAttribute('aria-current', 'step');
    await expect(page.locator('#beat-4')).toBeInViewport({ratio: 0.5});
    await expect(page.locator('#beat-4')).toHaveCSS('opacity', '1');
    await page.screenshot({path: path.join(exports, 'demo-present.png')});
    await page.getByRole('button', {name: 'Next beat'}).click();
    await expect(page.locator('#beat-5')).toBeVisible();
    await expect(page.locator('#beat-4')).toBeHidden();
    await page.getByRole('button', {name: 'Next beat'}).click();
    await expect(page.getByRole('button', {name: 'Next beat'})).toBeDisabled();
    await page.getByRole('button', {name: 'Previous beat'}).click();
    await expect(page.locator('#beat-5')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.sidebar')).toBeVisible();
    await screenshot('demo.png');
    await page.setViewportSize({width: 390, height: 844});
    await screenshot('demo-mobile.png');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.getByRole('navigation', {name: 'Workspace views'}).getByRole('button', {name: /Archive/}).click();
    await page.getByLabel('Run history', {exact: true}).selectOption(formats[0].run_id);
    await expect(page.getByTestId('run-status')).toHaveText('completed');
    await expect(page.getByRole('link', {name: 'Download archive'})).toBeVisible();
    await page.emulateMedia({reducedMotion: 'reduce'});
    // A presenter-only URL opens Demo, and local runs never claim cloud evidence.
    await page.goto(`/?present=1&run=${receipt.local_optimization}`);
    await expect(page.locator('.sidebar')).toBeHidden();
    await page.getByRole('button', {name: /Remote experiment/}).click();
    await expect(page.getByText('Ran on this computer', {exact: true})).toBeVisible();
    await page.getByRole('button', {name: /Independent decoder receipt/}).click();
    await expect(page.locator('#beat-4')).toContainText('there is no fresh-sandbox decoder receipt');
    await page.getByRole('button', {name: /Sandbox cleanup/}).click();
    await expect(page.locator('#beat-5')).toContainText('created no sandboxes');
    await expect(page.getByText('✓ Deleted', {exact: true})).toHaveCount(0);
    await page.getByRole('button', {name: /Captured input/}).click();
    await expect(page.locator('#beat-1')).toHaveCSS('animation-name', 'none');
    await page.keyboard.press('PageDown');
    await expect(page.getByRole('button', {name: /Proposal and measured result/})).toHaveAttribute('aria-current', 'step');
    await page.keyboard.press('PageUp');
    await expect(page.getByRole('button', {name: /Captured input/})).toHaveAttribute('aria-current', 'step');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    // A receipt for another snapshot must never show the verification check mark.
    await page.route(`**/runs/${cloud.id}`, async route => {
      const altered = structuredClone(cloud);
      altered.reports['independent-decode'].snapshot_id = 'different-snapshot';
      altered.sandboxes = [{role: 'decoder', status: 'delete-failed'}];
      await route.fulfill({json: altered, headers: {'access-control-allow-origin': 'http://127.0.0.1:5173'}});
    });
    await page.goto(`/?view=demo&run=${cloud.id}`);
    await expect.poll(() => new URL(page.url()).searchParams.get('repo')).toBe(cloud.repository_id);
    await expect(page.getByText('Receipt names a different snapshot. Not verified.')).toBeVisible();
    await expect(page.getByText('✓ Byte-for-byte match with the original snapshot')).toHaveCount(0);
    await expect(page.locator('#beat-5')).toContainText('delete-failed');
    await expect(page.getByText('✓ Deleted', {exact: true})).toHaveCount(0);
    expect(errors).toEqual([]);
    receipt.status = 'passed';
  } finally {
    writeFileSync(path.join(root, 'browser-receipt.json'), JSON.stringify(receipt, null, 2));
    await testInfo.attach('demo receipt', {path: path.join(root, 'browser-receipt.json'), contentType: 'application/json'});
  }
});

test('a lost submission response is visible and retries without duplicating the job', async ({page, request}) => {
  test.skip(process.env.REPOWEAVE_LIVE_DEMO === '1', 'Failure injection is local-only');
  const api = 'http://127.0.0.1:8000';
  await page.goto('/');
  await page.getByRole('button', {name: 'Capture snapshot'}).click();
  await expect(page.getByRole('heading', {name: 'Repository structure'})).toBeVisible();
  const before = (await (await request.get(`${api}/runs`)).json()).length;
  let accepted = '';
  await page.route('**/runs', async route => {
    if (route.request().method() !== 'POST' || accepted) {await route.continue(); return;}
    const response = await route.fetch();
    expect(response.status()).toBe(202);
    accepted = (await response.json()).id;
    await route.fulfill({status: 503, headers: {'content-type': 'application/json',
      'access-control-allow-origin': 'http://127.0.0.1:5173'},
      body: JSON.stringify({detail: 'Simulated lost response'})});
  });
  await page.getByRole('button', {name: 'Create archive', exact: true}).click();
  await expect(page.getByRole('alert')).toContainText('503: Simulated lost response');
  await page.getByRole('button', {name: 'Create archive', exact: true}).click();
  await expect(page.getByTestId('run-status')).toHaveText('completed');
  expect(new URL(page.url()).searchParams.get('run')).toBe(accepted);
  expect((await (await request.get(`${api}/runs`)).json()).length).toBe(before + 1);
  await expect(page.getByRole('alert')).toHaveCount(0);
});
