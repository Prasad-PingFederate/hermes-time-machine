import test from 'node:test';
import assert from 'node:assert/strict';
import { host } from '@hermes/plugin-sdk';
import plugin from '../desktop/plugin.js';

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
