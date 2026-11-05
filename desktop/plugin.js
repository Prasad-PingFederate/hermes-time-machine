import { host, useValue, STATUSBAR_AREAS } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useState } from 'react'

function ShieldChip() {
  const sessionId = useValue(host.state.focusedSessionId)
  const [count, setCount] = useState(0)

  useEffect(() => {
    if (!sessionId) return
    // TODO: ctx.rest(`/snapshots?session_id=${sessionId}`) then set count
    setCount(0)
  }, [sessionId])

  return jsx('button', {
    type: 'button',
    className: 'px-2 text-xs flex items-center gap-1',
    onClick: () => host.notify({ kind: 'info', message: 'Time Machine open' }),
    children: `\uD83D\uDEE1\uFE0F ${count}`,
  })
}

function TimelinePane() {
  const sessionId = useValue(host.state.focusedSessionId)
  const [snapshots, setSnapshots] = useState([])
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    if (!sessionId) return
    // TODO: ctx.rest(`/snapshots?session_id=${sessionId}`)
    setSnapshots([])
  }, [sessionId])

  return jsxs('div', {
    className: 'flex h-full flex-col p-3 text-sm',
    children: [
      jsx('div', { className: 'font-semibold mb-2', children: 'Time Machine' }),
      jsx('div', { className: 'flex-1 overflow-auto', children: 'Timeline scrubber goes here' }),
      selected && jsx('pre', { className: 'mt-2 text-xs bg-black/20 p-2 rounded', children: selected.unified_diff }),
    ],
  })
}

export default {
  id: 'hermes-time-machine',
  name: 'Time Machine',
  register(ctx) {
    // Status bar shield
    ctx.register({
      id: 'shield',
      area: STATUSBAR_AREAS.right,
      order: 100,
      render: () => jsx(ShieldChip, {}),
    })

    // Right-rail pane
    ctx.register({
      id: 'timeline',
      area: 'panes',
      title: 'Time Machine',
      data: { placement: 'right', width: '340px' },
      render: () => jsx(TimelinePane, {}),
    })

    // Full page
    ctx.register({
      id: 'studio',
      area: 'routes',
      data: { path: '/time-machine' },
      render: () => jsx('div', { className: 'p-6', children: 'Full Time Machine Studio' }),
    })
  },
}
