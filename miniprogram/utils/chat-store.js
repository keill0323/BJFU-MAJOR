// load 返回未经验证的快照，页面必须完成服务端 events 同步后才能展示。
const KEY = 'cs2_chat_cache_v1'
const DAY = 86400000, MAX_BYTES = 2 * 1024 * 1024
let owner = '', rooms = Object.create(null)
function decimal(value) {
  const text = String(value == null ? '0' : value)
  return /^\d+$/.test(text) ? text.replace(/^0+(?=\d)/, '') : null
}
function compare(a, b) {
  a = decimal(a) || '0'; b = decimal(b) || '0'
  return a.length === b.length ? (a === b ? 0 : a > b ? 1 : -1) : a.length > b.length ? 1 : -1
}
function copy(value) { return JSON.parse(JSON.stringify(value)) }
function bytes(value) {
  let total = 0
  for (let i = 0; i < value.length; i++) {
    const c = value.charCodeAt(i)
    if (c < 0x80) total++
    else if (c < 0x800) total += 2
    else if (c >= 0xd800 && c <= 0xdbff && i + 1 < value.length && value.charCodeAt(i + 1) >= 0xdc00 && value.charCodeAt(i + 1) <= 0xdfff) { total += 4; i++ }
    else total += 3
  }
  return total
}
function cleanMessage(message) {
  if (!message || message.pending || !['active', 'recalled'].includes(message.state)) return null
  const created = Date.parse(message.created_at), id = decimal(message.id), seq = decimal(message.seq)
  if (!id || id === '0' || !seq || !Number.isFinite(created) || created <= Date.now() - DAY) return null
  return { id, seq, sender_id: message.sender_id, client_id: message.client_id, kind: message.kind,
    content: message.state === 'active' ? message.content : null, state: message.state,
    created_at: message.created_at, read_at: message.read_at || null }
}
function cleanItems(items) {
  const byId = new Map()
  ;(Array.isArray(items) ? items : []).forEach(item => { const next = cleanMessage(item); if (next) byId.set(next.id, next) })
  return Array.from(byId.values()).sort((a, b) => compare(a.seq, b.seq)).slice(-100)
}
function trim() {
  Object.keys(rooms).forEach(cid => {
    const room = rooms[cid], before = room.items.length
    room.items = cleanItems(room.items)
    if (room.items.length && room.items.length < before) { room.has_more = true; room.next_cursor = room.items[0].seq }
  })
  const keys = Object.keys(rooms).sort((a, b) => rooms[b].time - rooms[a].time)
  keys.slice(20).forEach(cid => delete rooms[cid])
  const kept = keys.slice(0, 20)
  while (kept.length && bytes(JSON.stringify({ owner, rooms })) > MAX_BYTES) delete rooms[kept.pop()]
}
function persist() {
  if (!owner) return
  trim()
  try { wx.setStorageSync(KEY, copy({ owner, rooms })) } catch (_) {}
}
function bind(id) {
  const next = id == null ? '' : String(id)
  if (!next) { clear(); return }
  if (owner === next) { persist(); return }
  owner = next; rooms = Object.create(null)
  try {
    const saved = wx.getStorageSync(KEY)
    if (saved && saved.owner === next && saved.rooms && typeof saved.rooms === 'object') {
      Object.keys(saved.rooms).forEach(cid => {
        const room = saved.rooms[cid]
        if (!decimal(cid) || !room || typeof room !== 'object') return
        const items = cleanItems(room.items), cropped = items.length && Array.isArray(room.items) && items.length < room.items.length
        rooms[cid] = { time: Number.isFinite(room.time) ? room.time : 0, items,
          cursor: room.cursor == null ? null : decimal(room.cursor),
          next_cursor: cropped ? items[0].seq : room.next_cursor == null ? null : decimal(room.next_cursor), has_more: !!room.has_more || !!cropped }
      })
    } else wx.removeStorageSync(KEY)
  } catch (_) { rooms = Object.create(null) }
  persist()
}
function clear() { owner = ''; rooms = Object.create(null); try { wx.removeStorageSync(KEY) } catch (_) {} }
function save(cid, items, position = {}) {
  if (!owner || !decimal(cid)) return
  const nextItems = cleanItems(items), cropped = nextItems.length && nextItems.length < items.length
  rooms[String(cid)] = { time: Date.now(), items: nextItems, cursor: position.cursor == null ? null : decimal(position.cursor),
    next_cursor: cropped ? nextItems[0].seq : position.next_cursor == null ? null : decimal(position.next_cursor), has_more: !!position.has_more || !!cropped }
  persist()
}
function load(cid) {
  if (!owner) return null
  persist()
  const room = rooms[String(cid)]
  if (!room || room.cursor == null || !room.items.length || compare(room.cursor, room.items[room.items.length - 1].seq) < 0) return null
  // 会话资料、权限和摘要只能以本次服务器响应为准。
  return copy({ items: room.items, cursor: room.cursor, next_cursor: room.next_cursor, has_more: room.has_more, verified: false })
}
function forget(cid) { delete rooms[String(cid)]; persist() }
function merge(items, changes, afterSeq = null) {
  const values = new Map()
  ;(items || []).forEach(message => {
    if (!message || message.state === 'purged') return
    const id = decimal(message.id)
    if (id) values.set(id, Object.assign({}, message, { id, seq: decimal(message.seq) || '0', content: message.state === 'active' ? message.content : null }))
  })
  const bySeq = new Map()
  ;(changes || []).forEach(event => {
    const seq = decimal(event && event.seq)
    if (seq != null && (afterSeq == null || compare(seq, afterSeq) > 0)) bySeq.set(seq, event)
  })
  Array.from(bySeq.values()).sort((a, b) => compare(a.seq, b.seq)).forEach(event => {
    const id = event.message_id == null ? null : decimal(event.message_id)
    if (id && (!event.message || event.message.state === 'purged')) values.delete(id)
    else if (event.message) {
      const message = event.message, key = decimal(message.id)
      if (key) values.set(key, Object.assign({}, message, { id: key, seq: decimal(message.seq) || '0', content: message.state === 'active' ? message.content : null }))
    }
  })
  return Array.from(values.values()).sort((a, b) => compare(a.seq, b.seq))
}
module.exports = { bind, clear, save, load, forget, compare, merge }
