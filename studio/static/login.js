"use strict";
// Only a wrong key is reported as a wrong key; host, origin and server problems keep their own message.
function loginMessage(status, error) {
  if (status === 401) return 'Access key was not accepted.';
  if (status === 429) return error && error !== 'Authentication failed.' ? error : 'Too many sign-in attempts or active sessions. Wait one minute and try again.';
  return `Studio rejected the sign-in (${status}): ${error || 'no details'}`;
}
if (typeof document !== 'undefined') document.getElementById('login').addEventListener('submit', async event => {
  event.preventDefault();
  const field = document.getElementById('key');
  const error = document.getElementById('error');
  error.textContent = '';
  let response;
  try {
    response = await fetch('/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key: field.value})});
  } catch (_) { error.textContent = 'Cannot reach Studio. Check the connection.'; return; }
  field.value = '';
  if (!response.ok) {
    let detail = '';
    try { detail = (await response.json()).error || ''; } catch (_) { detail = ''; }
    error.textContent = loginMessage(response.status, detail);
    return;
  }
  location.replace('/');
});
