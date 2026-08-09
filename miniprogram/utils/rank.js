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

module.exports = { rankDisplay }
