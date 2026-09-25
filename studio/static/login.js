document.getElementById('login').addEventListener('submit', async event => {
  event.preventDefault();
  const field = document.getElementById('key');
  const error = document.getElementById('error');
  error.textContent = '';
  try {
    const response = await fetch('/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key: field.value})});
    field.value = '';
    if (!response.ok) { error.textContent = response.status === 429 ? 'Too many attempts. Wait one minute.' : 'Access key was not accepted.'; return; }
    location.replace('/');
  } catch (_) { error.textContent = 'Cannot reach Studio. Check the connection.'; }
});
