"use strict";
// Original local fixture pilot. The model chooses an enumerated action; it never
// supplies a selector, script, URL, or executable text. Production pages are not connected.
function pilotFingerprint(frame) { return JSON.stringify(frame); }
function pilotFrame(root) {
  return {
    query: root.querySelector('[data-pilot-query]').value,
    filtered: root.querySelector('[data-pilot-results]').dataset.filtered === 'true',
    visibleItems: [...root.querySelectorAll('[data-pilot-item]')].filter(n => !n.hidden).map(n => n.textContent.trim()),
  };
}
function pilotActions(frame) {
  if (frame.query !== 'blue') return ['set_query_blue', 'wait'];
  if (!frame.filtered) return ['apply_filter', 'wait'];
  return ['finish', 'wait'];
}
function pilotVerified(frame) { return frame.filtered && frame.query === 'blue' && frame.visibleItems.length === 1 && frame.visibleItems[0] === 'Blue desk'; }
function pilotLease(frame, action, now = Date.now()) {
  if (!pilotActions(frame).includes(action)) throw new Error('Action is not eligible in this observed state.');
  return {fingerprint:pilotFingerprint(frame), action, expires:now+15000, consumed:false};
}
function consumePilotLease(lease, current, now = Date.now()) {
  if (lease.consumed || now >= lease.expires || lease.fingerprint !== pilotFingerprint(current) || !pilotActions(current).includes(lease.action)) throw new Error('Stale, expired or already-used action. Observe the page again.');
  lease.consumed = true;
  return lease.action;
}
function executePilot(root, lease) {
  const action = consumePilotLease(lease, pilotFrame(root));
  if (action === 'set_query_blue') {
    const input = root.querySelector('[data-pilot-query]'); input.value = 'blue'; input.dispatchEvent(new Event('input', {bubbles:true}));
  } else if (action === 'apply_filter') {
    root.querySelector('[data-pilot-filter]').click();
  } else if (action === 'finish' && !pilotVerified(pilotFrame(root))) {
    throw new Error('DONE rejected: the independent result check failed.');
  }
  return {action, completed:action === 'finish' && pilotVerified(pilotFrame(root))};
}
function initBrowserPilot() {
  const root = $('#browser-pilot-fixture');
  let busy = false, generation = 0;
  const log = text => root.querySelector('[data-pilot-log]').textContent = text;
  root.querySelector('[data-pilot-query]').addEventListener('input', () => {
    root.querySelector('[data-pilot-results]').dataset.filtered = 'false';
  });
  root.querySelector('[data-pilot-filter]').addEventListener('click', () => {
    const query = root.querySelector('[data-pilot-query]').value.toLowerCase();
    for (const item of root.querySelectorAll('[data-pilot-item]')) item.hidden = !item.textContent.toLowerCase().includes(query);
    root.querySelector('[data-pilot-results]').dataset.filtered = 'true';
  });
  bind('#browser-pilot-reset', 'click', () => {
    generation++; root.querySelector('[data-pilot-query]').value = '';
    root.querySelector('[data-pilot-results]').dataset.filtered = 'false';
    for (const item of root.querySelectorAll('[data-pilot-item]')) item.hidden = false;
    log('Reset. Goal: show only the Blue desk, then independently verify the result.');
  });
  bind('#browser-pilot-step', 'click', async () => {
    if (busy) return; busy = true;
    const epoch = generation, observedAt = Date.now(), frame = pilotFrame(root), options = pilotActions(frame), stamp = pilotFingerprint(frame);
    let action = options[0], reason = 'Deterministic eligible action. No model request.';
    const button = $('#browser-pilot-step'); button.disabled = true;
    try {
      if ($('#browser-pilot-model').checked) {
        log('Waiting for a bounded proposal. Editing the fixture invalidates it.');
        const result = await api('/api/browser-pilot', {profile:$('#decision-lab-profile').value, frame});
        action = result.action; reason = result.reason;
      }
      if (epoch !== generation || stamp !== pilotFingerprint(pilotFrame(root))) throw new Error('Page changed while deciding. Proposal discarded.');
      const result = executePilot(root, pilotLease(frame, action, observedAt));
      log(`${reason}\nExecuted: ${result.action}. ${result.completed ? 'Independent DOM check passed: only Blue desk is visible.' : 'Not complete; inspect the new state before the next step.'}`);
    } catch (error) { log(error.message); }
    finally { busy = false; button.disabled = false; }
  });
  $('#decision-lab-dialog').addEventListener('close', () => { generation++; });
}
