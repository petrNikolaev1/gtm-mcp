const dark = {
  pageBg: '#1a1a1a', cardBg: '#252526', cardBorder: '#333',
  text1: '#d4d4d4', text2: '#b0b0b0', text3: '#969696', text4: '#858585', text5: '#6e6e6e', text6: '#4e4e4e',
  divider: '#2d2d2d', inputBg: '#3c3c3c', inputBorder: '#505050',
  badgeBg: '#2d2d2d', badgeText: '#858585',
  btnPrimaryBg: '#d4d4d4', btnPrimaryText: '#1e1e1e',
  btnHover: '#2d2d2d',
  activeTab: '#37373d', activeTabText: '#d4d4d4',
  link: '#569cd6',
  success: '#4ec9b0', danger: '#f14c4c', warning: '#cca700', info: '#569cd6',
}
const light = {
  pageBg: '#fafafa', cardBg: '#ffffff', cardBorder: '#e0e0e0',
  text1: '#1a1a1a', text2: '#333', text3: '#555', text4: '#777', text5: '#999', text6: '#bbb',
  divider: '#eee', inputBg: '#fff', inputBorder: '#ddd',
  badgeBg: '#f0f0f0', badgeText: '#777',
  btnPrimaryBg: '#333', btnPrimaryText: '#fff',
  btnHover: '#eee',
  activeTab: '#e8e8e8', activeTabText: '#333',
  link: '#2563eb',
  success: '#16a34a', danger: '#dc2626', warning: '#d97706', info: '#2563eb',
}
export function themeColors(isDark: boolean) {
  return isDark ? dark : light
}
