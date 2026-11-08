// Loaded before the first paint, so a dark page never flashes white. Reads the
// choice the interface stores under the same origin, so the guide looks the
// way the reader set the interface up.
;(function () {
  var choice = 'system'
  try {
    choice = localStorage.getItem('prtg-nats-theme') || 'system'
  } catch {
    // Storage blocked: follow the system.
  }
  var dark =
    choice === 'dark' ||
    (choice === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
})()
