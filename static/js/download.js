/**
 * Jungle Media Downloader - Download Flow & Cookie Polling Manager
 * Handles iframe-based download dispatch, cookie polling (dl_<token>),
 * and probe verification without navigating the browser page.
 */

function parseCookies(cookieStr) {
  const cookies = {};
  if (!cookieStr) return cookies;
  cookieStr.split(';').forEach(c => {
    const parts = c.trim().split('=');
    if (parts.length >= 2) {
      cookies[parts[0]] = parts.slice(1).join('=');
    }
  });
  return cookies;
}

function hasDownloadCookie(token, cookieStr) {
  const cookies = parseCookies(cookieStr !== undefined ? cookieStr : document.cookie);
  return Boolean(cookies[`dl_${token}`]);
}

function clearDownloadCookie(token) {
  document.cookie = `dl_${token}=; path=/; max-age=0; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
}

function buildStreamUrl(token, formatId, filename, mediaType) {
  return `/api/stream?token=${encodeURIComponent(token)}&format_id=${encodeURIComponent(formatId)}&filename=${encodeURIComponent(filename)}&type=${encodeURIComponent(mediaType || 'video')}`;
}

function triggerDownloadFrame(streamUrl) {
  let frame = document.getElementById('downloadFrame');
  if (!frame) {
    frame = document.createElement('iframe');
    frame.id = 'downloadFrame';
    frame.style.display = 'none';
    frame.setAttribute('aria-hidden', 'true');
    document.body.appendChild(frame);
  }
  frame.src = streamUrl;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    parseCookies,
    hasDownloadCookie,
    clearDownloadCookie,
    buildStreamUrl,
    triggerDownloadFrame
  };
}

