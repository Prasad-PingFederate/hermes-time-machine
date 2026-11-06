import test from 'node:test';
import assert from 'node:assert/strict';
import plugin from '../desktop/plugin.js';

test('desktop/plugin.js exports metadata and activates with ctx', async () => {
  assert.equal(plugin.id, 'time-machine');
  assert.equal(plugin.name, 'Hermes Time Machine');
  assert.equal(typeof plugin.activate, 'function');

  const registered = [];
  const stubCtx = {
    register: (item) => registered.push(item),
    rest: async () => ({
      snapshots: [
        {
          id: 'snap-xss-1',
          tool_name: '<script>alert(1)</script>',
          session_id: 'sess<xss>',
          checkpoint_hash: 'hash<xss>',
          created_at: 1700000000,
          status: 'ok',
        },
      ],
      total_storage_bytes: 1048576,
    }),
  };

  plugin.activate(stubCtx);
  assert.equal(registered.length, 3);

  const areas = registered.map((r) => r.area);
  assert.ok(areas.includes('statusBar.right'), 'Missing statusBar.right');
  assert.ok(areas.includes('rail.right'), 'Missing rail.right');
  assert.ok(areas.includes('pane'), 'Missing pane Studio route');

  // Stub DOM container
  const container = {
    _html: '',
    get innerHTML() {
      return this._html;
    },
    set innerHTML(val) {
      this._html = val;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  };

  const rail = registered.find((r) => r.id === 'time-machine-rail');
  assert.ok(rail && typeof rail.mount === 'function');
  const unmount = rail.mount(container);

  // Wait for async snapshot load and render
  await new Promise((resolve) => setTimeout(resolve, 60));

  assert.ok(container.innerHTML.length > 0, 'Container was not rendered');
  assert.ok(
    !container.innerHTML.includes('<script>alert(1)</script>'),
    'Unescaped script payload found in HTML'
  );
  assert.ok(
    container.innerHTML.includes('&lt;script&gt;alert(1)&lt;/script&gt;'),
    'Sanitized HTML entity missing'
  );

  if (typeof unmount === 'function') unmount();
});
