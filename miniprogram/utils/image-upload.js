// 相册、相机和头像开放能力的失败提示；用户主动取消选图不算故障。
function showImageError(error) {
  const message = String(error && (error.errMsg || (error.detail && error.detail.errMsg)) || '')
  if (/privacy/i.test(message)) {
    wx.showToast({ title: '未同意隐私指引，已取消选图', icon: 'none' })
  } else if (/cancel/i.test(message)) {
    return
  } else if (/auth deny|auth denied|authorize|permission/i.test(message)) {
    wx.showModal({ title: '无法使用相册或相机', content: '请检查微信和手机系统中的相册、相机权限，开启后再试。', showCancel: false })
  } else {
    wx.showToast({ title: '选图失败，请稍后重试', icon: 'none' })
  }
}

module.exports = { showImageError }
