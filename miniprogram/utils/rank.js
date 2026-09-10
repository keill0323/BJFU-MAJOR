/**
 * 段位显示工具
 * S 段带等级称谓：>=10 星 金S，>=25 星 钻S，=50 星 魔王S
 * 显示格式「等级 + S + 星数 + 星」，如 金S10星、钻S25星、魔王S50星
 */
function rankDisplay(rank) {
  if (!rank) return ''
  const m = /^S(\d+)$/.exec(rank)
  if (m) {
    const n = parseInt(m[1], 10)
    if (n >= 50) return '魔王S' + n + '星'
    if (n >= 25) return '钻S' + n + '星'
    if (n >= 10) return '金S' + n + '星'
    return 'S' + n + '星'
  }
  return rank
}

// All badge names are local allowlisted assets; no user text becomes a path.
// D has one level. C/B/A each have base, + and ++ levels.
function rankBadge(rank) {
  let key = 'unranked'
  let stars = null
  const standard = typeof rank === 'string' && /^(D|[CBA](?:\+{1,2})?)$/.exec(rank)
  const starMatch = typeof rank === 'string' && /^S(\d+)$/.exec(rank)
  if (standard) {
    key = rank.charAt(0).toLowerCase() + (rank.endsWith('++') ? '-double-plus' : rank.endsWith('+') ? '-plus' : '')
  } else if (rank === 'S') {
    key = 's'
  } else if (starMatch && Number(starMatch[1]) <= 50) {
    stars = Number(starMatch[1])
    key = stars >= 50 ? 's-demon' : stars >= 25 ? 's-diamond' : stars >= 10 ? 's-gold' : 's'
  }
  return {
    key,
    icon: '/images/ranks/' + key + '.svg',
    label: key === 'unranked' ? '待认证' : rankDisplay(rank),
    stars,
    progress: stars === null ? 0 : stars * 2
  }
}

module.exports = { rankDisplay, rankBadge }
