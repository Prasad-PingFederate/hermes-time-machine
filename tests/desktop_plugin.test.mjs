import test from 'node:test';
import assert from 'node:assert/strict';
import { host } from '@hermes/plugin-sdk';
import plugin, {
  reducePaneState,
  initialPaneState,
  diffEndpoint,
  previewRestoreBody,
  confirmRestoreBody,
  fileChangeLabel,
} from '../desktop/plugin.js';

test('desktop/plugin.js exports metadata and activates with ctx', async () => {
  assert.equal(plugin.id, 'time-machine');
  assert.equal(plugin.name, 'Hermes Time Machine');
  assert.equal(typeof plugin.activate, 'function');
  assert.equal(typeof plugin.register, 'function');

  const registered = [];
  const stubCtx = {
    registerMany: (items) => registered.push(...items),
    register: (item) => registered.push(item),
    rest: async (path) => {
      if (path === '/status') {
        return {
          snapshot_count: 3,
          self_heal_available: false,
          store_size_bytes: 2048,
        };
      }
      return {
        snapshots: [
          {
            id: 'snap-test-1',
            tool_name: 'write_file',
            session_id: 'test_session',
            checkpoint_hash: 'abc1234567890def',
            created_at: 1700000000,
            status: 'ok',
            modified_files_count: 1,
          },
        ],
      };
    },
  };

  plugin.activate(stubCtx);
  assert.ok(registered.length >= 2, `Expected at least 2 registrations, got ${registered.length}`);

  const areas = registered.map((r) => r.area);
  assert.ok(areas.includes('statusBar.right'), 'Missing statusBar.right registration');
  assert.ok(areas.includes('panes'), 'Missing panes registration');
  assert.ok(!areas.includes('rail.right'), 'Found invalid rail.right area');
  assert.ok(!areas.includes('pane'), 'Found invalid legacy pane area');

  // Verify contributions use render: () => <ReactNode> and NOT mount(el)
  const statusItem = registered.find((r) => r.area === 'statusBar.right');
  assert.ok(statusItem, 'statusBar.right item not found');
  assert.equal(typeof statusItem.render, 'function', 'statusBar item must provide render()');
  assert.equal(statusItem.mount, undefined, 'statusBar item must not use mount()');

  const statusNode = statusItem.render();
  assert.ok(statusNode && typeof statusNode === 'object', 'statusItem.render() must return a React node');
  assert.ok(statusNode.type, 'statusNode must have a type property');

  const paneItem = registered.find((r) => r.area === 'panes');
  assert.ok(paneItem, 'panes item not found');
  assert.equal(typeof paneItem.render, 'function', 'pane item must provide render()');
  assert.equal(paneItem.mount, undefined, 'pane item must not use mount()');

  const paneNode = paneItem.render();
  assert.ok(paneNode && typeof paneNode === 'object', 'paneItem.render() must return a React node');
  assert.ok(paneNode.type, 'paneNode must have a type property');
});

test('desktop/plugin.js statusBar render handles status and self-heal states', async () => {
  const registered = [];
  const stubCtx = {
    registerMany: (items) => registered.push(...items),
    register: (item) => registered.push(item),
    rest: async () => ({
      snapshot_count: 5,
      self_heal_available: true,
      latest_snapshot: { id: 'snap-1', tool_name: 'terminal' },
    }),
  };

  plugin.activate(stubCtx);
  const statusItem = registered.find((r) => r.area === 'statusBar.right');
  assert.ok(statusItem && typeof statusItem.render === 'function');

  const node = statusItem.render();
  assert.ok(node && typeof node === 'object');
  const rendered = typeof node.type === 'function' ? node.type(node.props) : node;
  assert.equal(rendered.type, 'button');
});

test('desktop/plugin.js openWorkspace passes render in chip click and palette run', async () => {
  const calls = [];
  const originalOpenWorkspace = host.openWorkspace;
  host.openWorkspace = (id, options) => {
    calls.push({ id, options });
  };
  try {
    const registered = [];
    const stubCtx = {
      registerMany: (items) => registered.push(...items),
      register: (item) => registered.push(item),
      rest: async () => ({
        snapshot_count: 2,
        self_heal_available: false,
      }),
    };

    plugin.activate(stubCtx);

    // 1. Status chip click
    const statusItem = registered.find((r) => r.area === 'statusBar.right');
    assert.ok(statusItem && typeof statusItem.render === 'function');
    const chipNode = statusItem.render();
    const chipRendered = typeof chipNode.type === 'function' ? chipNode.type(chipNode.props) : chipNode;
    assert.equal(typeof chipRendered.props.onClick, 'function');
    chipRendered.props.onClick();

    assert.equal(calls.length, 1);
    assert.equal(calls[0].id, 'time-machine');
    assert.equal(typeof calls[0].options.render, 'function');
    assert.doesNotThrow(() => calls[0].options.render());

    // 2. Palette run
    const paletteItem = registered.find((r) => r.area === 'palette');
    assert.ok(paletteItem && typeof paletteItem.data?.run === 'function');
    paletteItem.data.run();

    assert.equal(calls.length, 2);
    assert.equal(calls[1].id, 'time-machine');
    assert.equal(typeof calls[1].options.render, 'function');
    assert.doesNotThrow(() => calls[1].options.render());
  } finally {
    host.openWorkspace = originalOpenWorkspace;
  }
});

test('desktop pane diff toggle and preview restore state transitions', async () => {
  assert.equal(diffEndpoint('snap 1', 'turn'), '/snapshots/snap%201/diff');
  assert.equal(diffEndpoint('snap 1', 'working_tree'), '/snapshots/snap%201/diff?compare_to=WORKING_TREE');
  assert.deepEqual(previewRestoreBody(), { dry_run: true, user_approved: false });
  assert.deepEqual(confirmRestoreBody(['a.py', 'b.tmp']), {
    file_paths: ['a.py', 'b.tmp'],
    dry_run: false,
    user_approved: true,
  });
  assert.equal(
    fileChangeLabel({ files_changed: 2, insertions: 4, deletions: 1, modified_files_count: 9 }),
    '2 file(s) +4/-1',
  );
  assert.equal(
    fileChangeLabel({ modified_files_count: 3, insertions: 0, deletions: 2 }),
    '3 file(s) +0/-2',
  );

  let state = initialPaneState();
  assert.equal(state.diffMode, 'turn');
  state = reducePaneState(state, { type: 'set_diff_mode', mode: 'working_tree' });
  assert.equal(state.diffMode, 'working_tree');
  state = reducePaneState(state, { type: 'set_diff_mode', mode: 'turn' });
  assert.equal(state.diffMode, 'turn');

  state = reducePaneState(state, {
    type: 'preview_loaded',
    snapshotId: 'snap-test-1',
    plannedRestored: ['file_a.py', '(all files)'],
    plannedUnlinked: ['stray_after.tmp'],
  });
  assert.equal(state.restore.step, 'preview');
  assert.equal(state.restore.snapshotId, 'snap-test-1');
  assert.deepEqual(state.restore.plannedRestored, ['file_a.py']);
  assert.deepEqual(state.restore.plannedUnlinked, ['stray_after.tmp']);
  assert.ok(state.restore.fullWorkspace);
  assert.deepEqual(state.restore.selected, ['file_a.py', 'stray_after.tmp']);

  state = reducePaneState(state, { type: 'toggle_file', file: 'stray_after.tmp' });
  assert.deepEqual(state.restore.selected, ['file_a.py']);
  state = reducePaneState(state, { type: 'toggle_file', file: 'notes.txt' });
  assert.deepEqual(state.restore.selected, ['file_a.py', 'notes.txt']);

  state = reducePaneState(state, { type: 'restore_confirmed', backupId: 'snap_backup_1' });
  assert.equal(state.restore.step, 'done');
  assert.equal(state.restore.backupId, 'snap_backup_1');

  state = reducePaneState(state, { type: 'cancel_restore' });
  assert.equal(state.restore, null);

  state = reducePaneState(state, {
    type: 'search_results',
    query: 'marker',
    matches: [{ snapshot_id: 'snap-test-1' }],
  });
  assert.deepEqual(state.matchIds, ['snap-test-1']);
  state = reducePaneState(state, { type: 'clear_search' });
  assert.equal(state.matchIds, null);
  assert.equal(state.searchQuery, '');

  const calls = [];
  const registered = [];
  const stubCtx = {
    previewSnapshot: {
      id: 'snap-test-1',
      tool_name: 'write_file',
      created_at: 1700000000,
      status: 'ok',
      files_changed: 2,
      insertions: 3,
      deletions: 1,
    },
    initialSnapshots: [
      {
        id: 'snap-test-1',
        tool_name: 'write_file',
        created_at: 1700000000,
        status: 'ok',
        files_changed: 2,
        insertions: 3,
        deletions: 1,
      },
    ],
    registerMany: (items) => registered.push(...items),
    register: (item) => registered.push(item),
    rest: async (path, options) => {
      calls.push({ path, options });
      if (String(path).includes('/restore')) {
        const body = options && options.body ? JSON.parse(options.body) : {};
        if (body.dry_run) {
          return {
            ok: true,
            dry_run: true,
            planned_restored_files: ['file_a.py'],
            planned_unlinked_files: ['stray_after.tmp'],
          };
        }
        return { ok: true, dry_run: false, pre_restore_backup_id: 'snap_backup_ui' };
      }
      if (String(path).includes('/diff')) {
        return { ok: true, diff: 'diff body' };
      }
      if (String(path).includes('/search')) {
        return { ok: true, matches: [{ snapshot_id: 'snap-test-1' }] };
      }
      return { snapshots: stubCtx.initialSnapshots };
    },
  };

  plugin.activate(stubCtx);
  const paneItem = registered.find((r) => r.area === 'panes');
  const paneNode = paneItem.render();
  const pane = paneNode.type(paneNode.props);

  function walk(node, acc = []) {
    if (!node) return acc;
    if (Array.isArray(node)) {
      node.forEach((kid) => walk(kid, acc));
      return acc;
    }
    if (typeof node !== 'object') return acc;
    acc.push(node);
    const kids = node.props && node.props.children;
    if (Array.isArray(kids)) kids.forEach((kid) => walk(kid, acc));
    else if (kids && typeof kids === 'object') walk(kids, acc);
    return acc;
  }

  const nodes = walk(pane);
  const byAction = (name) => nodes.find((n) => n.props && n.props['data-action'] === name);
  assert.ok(byAction('diff-turn'), 'Turn Diff toggle missing');
  assert.ok(byAction('diff-working-tree'), 'Working tree toggle missing');
  assert.ok(byAction('preview-restore'), 'Preview Restore button missing');
  assert.ok(byAction('search-checkpoints'), 'Search input missing');

  const labelNode = nodes.find((n) => n.props && n.props.children === '2 file(s) +3/-1');
  assert.ok(labelNode, 'timeline should show files_changed and insertions/deletions');

  await byAction('diff-working-tree').props.onClick();
  assert.ok(
    calls.some((c) => c.path === '/snapshots/snap-test-1/diff?compare_to=WORKING_TREE'),
    'working tree toggle should request WORKING_TREE diff',
  );

  await byAction('diff-turn').props.onClick();
  assert.ok(
    calls.some((c) => c.path === '/snapshots/snap-test-1/diff'),
    'turn toggle should request turn diff',
  );

  await byAction('preview-restore').props.onClick();
  const previewCall = calls.find((c) => String(c.path).endsWith('/restore') && c.options);
  assert.ok(previewCall, 'preview should POST');
  assert.equal(previewCall.options.method, 'POST');
  assert.deepEqual(JSON.parse(previewCall.options.body), previewRestoreBody());

  await byAction('search-checkpoints').props.onChange({ target: { value: 'marker' } });
  assert.ok(
    calls.some((c) => c.path === '/snapshots/search?q=marker'),
    'search input should query /snapshots/search',
  );
});
