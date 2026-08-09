/* ManhwaShorts Studio - dashboard client.
 *
 * Plain ES modules-free JS on purpose: no build step, no bundler, so the local
 * install has one fewer moving part. All DOM insertion uses textContent or
 * explicit element creation rather than innerHTML with interpolated data, so
 * user-supplied text (script lines, filenames) can never execute as markup.
 */
'use strict';

const state = {
  user: null,
  projectId: null,
  script: null,
  renderPoll: null,
  analysis: null,
  settingsLoaded: false,
  gpuAvailable: false,
  busy: new Set(),
};

/* ---------- UX guards ----------
 *
 * Two problems this solves on a slow machine:
 *
 * 1. Double submits. A user who does not see instant feedback clicks again, and
 *    a second render/draft is queued. `withBusy` disables the control, shows a
 *    spinner, and refuses re-entry for the same key.
 * 2. Silent waits. Any action over ~200ms gets a visible spinner, so nothing
 *    ever looks frozen.
 */
async function withBusy(button, label, fn) {
  const key = button ? button.id || label : label;
  if (state.busy.has(key)) return undefined;
  state.busy.add(key);

  let original = '';
  if (button) {
    original = button.textContent;
    button.disabled = true;
    clear(button);
    button.appendChild(el('span', 'spinner'));
    button.appendChild(document.createTextNode(label || original));
  }
  try {
    return await fn();
  } finally {
    state.busy.delete(key);
    if (button) {
      button.disabled = false;
      clear(button);
      button.textContent = original;
    }
  }
}

async function loadQCOverrides() {
  const list = $('qc-override-history');
  if (!list) return;
  clear(list);
  const events = await api(`/api/projects/${state.projectId}/quality/overrides`);
  if (!events.length) {
    list.appendChild(el('p', 'hint', 'Belum ada override.'));
    return;
  }
  events.forEach((event) => {
    const item = el('div', 'item');
    item.appendChild(el('div', 'item-title', `${event.quality_code} · ${event.actor_id || 'unknown actor'}`));
    item.appendChild(el('div', 'item-meta', `${event.created_at} · ${event.before_passed} → ${event.after_passed}`));
    item.appendChild(el('div', 'item-meta', event.reason));
    list.appendChild(item);
  });
}

async function loadQCHistory() {
  const list = $('qc-history');
  if (!list) return;
  clear(list);
  const snapshots = await api(`/api/projects/${state.projectId}/quality/history`);
  if (!snapshots.length) {
    list.appendChild(el('p', 'hint', 'Belum ada snapshot QC.'));
    return;
  }
  snapshots.forEach((snapshot) => {
    const item = el('div', 'item');
    const summary = snapshot.report && snapshot.report.summary ? snapshot.report.summary : {};
    item.appendChild(el('div', 'item-title', `${snapshot.passed ? 'LULUS' : 'GAGAL'} · ${snapshot.created_at}`));
    item.appendChild(el('div', 'item-meta', `errors: ${summary.errors || 0} · warnings: ${summary.warnings || 0}`));
    list.appendChild(item);
  });
}

/* ---------- quality ---------- */

function $(id) { return document.getElementById(id); }

function toast(message, kind = 'ok', ms = 4200) {
  const el = $('toast');
  el.textContent = message;
  el.className = 'toast ' + kind;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, ms);
}

async function api(path, options = {}) {
  const opts = Object.assign({ headers: {} }, options);
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch (_) { data = { detail: text }; }
  }
  if (!response.ok) {
    const detail = (data && data.detail) || `HTTP ${response.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function fmt(seconds) {
  const value = Number(seconds) || 0;
  return value.toFixed(2) + 's';
}

/* ---------- health & voices ---------- */

async function loadHealth() {
  try {
    const health = await api('/api/health');
    const badge = $('health-badge');
    const ok = health.status === 'ok';
    badge.textContent = ok
      ? `siap · tts:${health.tts_provider}`
      : `terbatas (${health.problems.length} masalah)`;
    badge.className = 'badge ' + (ok ? 'ok' : 'bad');
    if (!ok) badge.title = health.problems.join(' | ');
  } catch (err) {
    $('health-badge').textContent = 'server tidak merespons';
    $('health-badge').className = 'badge bad';
  }
}

async function loadVoices() {
  try {
    const data = await api('/api/voices');
    const select = $('p-voice');
    clear(select);
    data.voices.forEach((voice) => {
      const option = el('option', null, voice.label);
      option.value = voice.id;
      select.appendChild(option);
    });
  } catch (_) { /* voices are non-critical */ }
}

/* ---------- auth ---------- */

async function afterLogin(user) {
  state.user = user;
  $('user-badge').textContent = user.email;
  $('user-badge').hidden = false;
  $('logout-btn').hidden = false;
  $('auth-section').hidden = true;
  $('studio').hidden = false;
  await loadVoices();
  await loadByok();
  await loadEncoders();
  await loadProjects();
}

async function checkSession() {
  try {
    const user = await api('/api/auth/me');
    await afterLogin(user);
  } catch (_) {
    $('auth-section').hidden = false;
    $('studio').hidden = true;
  }
}

function authPayload() {
  return {
    email: $('auth-email').value.trim(),
    password: $('auth-password').value,
    name: $('auth-name').value.trim(),
  };
}

$('auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = authPayload();
  try {
    const user = await api('/api/auth/login', { method: 'POST', body: { email: body.email, password: body.password } });
    toast('Berhasil masuk.', 'ok');
    await afterLogin(user);
  } catch (err) { toast(err.message, 'error'); }
});

$('register-btn').addEventListener('click', async () => {
  const body = authPayload();
  if (!body.email || body.password.length < 8) {
    toast('Email wajib dan password minimal 8 karakter.', 'error');
    return;
  }
  try {
    const user = await api('/api/auth/register', { method: 'POST', body });
    toast('Akun dibuat.', 'ok');
    await afterLogin(user);
  } catch (err) { toast(err.message, 'error'); }
});

$('logout-btn').addEventListener('click', async () => {
  await api('/api/auth/logout', { method: 'POST' });
  state.user = null;
  state.projectId = null;
  $('logout-btn').hidden = true;
  $('user-badge').textContent = '';
  $('user-badge').hidden = true;
  $('encoder-badge').hidden = true;
  $('studio').hidden = true;
  $('auth-section').hidden = false;
  state.settingsLoaded = false;
  toast('Sudah keluar.', 'ok');
});

/* ---------- projects ---------- */

async function loadProjects() {
  const projects = await api('/api/projects');
  const select = $('project-select');
  clear(select);
  if (!projects.length) {
    const option = el('option', null, '— belum ada proyek —');
    option.value = '';
    select.appendChild(option);
    $('workspace').hidden = true;
    return;
  }
  projects.forEach((project) => {
    const label = `${project.title} [${project.status}]`;
    const option = el('option', null, label);
    option.value = project.id;
    select.appendChild(option);
  });
  if (!state.projectId || !projects.some((p) => p.id === state.projectId)) {
    state.projectId = projects[0].id;
  }
  select.value = state.projectId;
  $('workspace').hidden = false;

  // Show the selected project's settings, so the user can confirm they are
  // working on the right chapter without opening the edit form.
  const current = projects.find((p) => p.id === state.projectId);
  const meta = $('project-meta');
  clear(meta);
  if (current) {
    const line = el('div', 'item-meta');
    line.appendChild(el('span', 'badge info', current.status));
    line.appendChild(document.createTextNode(
      ` ${current.manhwa_title || '(tanpa judul manhwa)'}`
      + (current.chapter ? ` ch.${current.chapter}` : '')
      + ` · target ${current.target_duration}s`
      + ` · ${current.narration_style} · spoiler ${current.spoiler_level}`
      + ` · voice ${current.voice_id}`));
    meta.appendChild(line);
  }

  await loadProjectDetail();
}

$('project-select').addEventListener('change', async (event) => {
  state.projectId = event.target.value || null;
  if (state.projectId) await loadProjectDetail();
});

$('refresh-projects').addEventListener('click', () => loadProjects().catch((e) => toast(e.message, 'error')));

$('new-project-toggle').addEventListener('click', () => {
  const form = $('project-form');
  form.hidden = !form.hidden;
});

$('project-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = {
    title: $('p-title').value.trim(),
    manhwa_title: $('p-manhwa').value.trim(),
    chapter: $('p-chapter').value.trim(),
    target_duration: Number($('p-duration').value) || 41,
    template: $('p-template').value,
    narration_style: $('p-style').value,
    spoiler_level: $('p-spoiler').value,
    voice_id: $('p-voice').value || 'en',
    cta_text: $('p-cta').value.trim(),
  };
  try {
    const project = await api('/api/projects', { method: 'POST', body });
    toast('Proyek dibuat.', 'ok');
    $('project-form').reset();
    $('project-form').hidden = true;
    state.projectId = project.id;
    await loadProjects();
  } catch (err) { toast(err.message, 'error'); }
});

async function loadProjectDetail() {
  // Run the independent fetches concurrently. Serially this was 8 round trips
  // before the UI settled, which is noticeable on a slow box.
  await Promise.all([
    loadAssets(),
    loadAnalysis(),
    loadScript(),
    loadTimeline(),
    loadQuality(),
    loadQCOverrides(),
    loadQCHistory(),
  ]);
  $('srt-link').href = `/api/projects/${state.projectId}/subtitles.srt`;
  await Promise.all([
    loadRenders(),
    loadRenderHistory(),
    loadScriptVersions(),
    loadPublications(),
  ]);
}

/* ---------- assets ---------- */

function rightsPayload() {
  return {
    rights_owner: $('r-owner').value.trim(),
    license_type: $('r-license').value,
    source_name: $('r-source').value.trim(),
    attribution: $('r-attr').value.trim(),
    declared: $('r-declared').checked,
  };
}

async function loadAssets() {
  const assets = await api(`/api/projects/${state.projectId}/assets`);
  const list = $('assets-list');
  clear(list);
  $('asset-count').textContent = String(assets.length);
  if (!assets.length) {
    list.appendChild(el('div', 'empty',
      'Belum ada materi. Tambahkan teks atau unggah panel di atas.'));
    return;
  }
  assets.forEach((asset) => {
    const item = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', asset.original_filename || asset.type));
    const dims = asset.width ? ` · ${asset.width}x${asset.height}` : '';
    const kb = Math.max(1, Math.round(asset.size_bytes / 1024));
    main.appendChild(el('div', 'item-meta', `${asset.type} · ${kb} KB${dims}`));
    const declared = asset.rights_status === 'declared' || asset.rights_status === 'verified';
    const badge = el('span', 'badge ' + (declared ? 'ok' : 'bad'),
      declared ? `hak: ${asset.rights_status}` : 'hak belum dideklarasikan');
    item.appendChild(main);
    item.appendChild(badge);

    const del = el('button', 'btn secondary', 'Hapus');
    del.type = 'button';
    del.addEventListener('click', async () => {
      if (!confirm(`Hapus ${asset.original_filename}?`)) return;
      try {
        await api(`/api/projects/${state.projectId}/assets/${asset.id}`, { method: 'DELETE' });
        toast('Aset dihapus.', 'ok');
        await loadAssets();
      } catch (err) { toast(err.message, 'error'); }
    });
    item.appendChild(del);
    list.appendChild(item);
  });
}

$('text-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = { text: $('src-text').value, title: 'recap.txt', rights: rightsPayload() };
  try {
    await api(`/api/projects/${state.projectId}/assets/text`, { method: 'POST', body });
    toast('Teks ditambahkan.', 'ok');
    $('src-text').value = '';
    await loadAssets();
  } catch (err) { toast(err.message, 'error'); }
});

$('upload-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = $('up-files');
  if (!input.files.length) { toast('Pilih berkas dulu.', 'error'); return; }
  const form = new FormData();
  Array.from(input.files).forEach((file) => form.append('files', file));
  const rights = rightsPayload();
  Object.keys(rights).forEach((key) => form.append(key, rights[key]));
  try {
    const created = await api(`/api/projects/${state.projectId}/assets/upload`, { method: 'POST', body: form });
    toast(`${created.length} berkas diunggah.`, 'ok');
    input.value = '';
    await loadAssets();
  } catch (err) { toast(err.message, 'error'); }
});

/* ---------- draft & script ---------- */

$('draft-btn').addEventListener('click', async () => {
  const button = $('draft-btn');
  button.disabled = true;
  $('draft-summary').textContent = 'Membuat draft… analisis, naskah, voice-over, timeline.';
  try {
    const draft = await api(`/api/projects/${state.projectId}/draft`, { method: 'POST' });
    $('draft-summary').textContent =
      `Naskah v${draft.script_version} · estimasi ${fmt(draft.estimated_duration)} · ` +
      `audio ${fmt(draft.audio_duration)} · ${draft.segments} segmen · ` +
      `${draft.scenes} scene · ${draft.cues} subtitle`;
    toast('Draft siap ditinjau.', 'ok');
    await loadProjectDetail();
  } catch (err) {
    $('draft-summary').textContent = '';
    toast(err.message, 'error');
  } finally { button.disabled = false; }
});

$('regen-script-btn').addEventListener('click', async () => {
  try {
    await api(`/api/projects/${state.projectId}/script`, { method: 'POST', body: { keep_locked: true, hook_count: 3 } });
    toast('Naskah dibuat ulang (bagian terkunci dipertahankan).', 'ok');
    await loadScript();
  } catch (err) { toast(err.message, 'error'); }
});

async function loadScript() {
  const container = $('script-sections');
  const hooks = $('hook-options');
  const warnings = $('script-warnings');
  clear(container); clear(hooks); clear(warnings);
  const status = $('script-status');
  clear(status);
  let script;
  try {
    script = await api(`/api/projects/${state.projectId}/script`);
  } catch (_) {
    container.appendChild(el('div', 'empty',
      'Belum ada naskah. Jalankan “Buat draft otomatis” di langkah 2.'));
    return;
  }
  state.script = script;

  // Note: the script row carries no similarity figure — that ratio is computed
  // by the policy gate and reported as a quality check, so it is shown in step 6
  // rather than invented here.
  const approved = Boolean(script.approved_at);
  status.appendChild(el('span', 'badge ' + (approved ? 'ok' : 'muted'),
    approved ? `disetujui v${script.version}` : `draft v${script.version}`));
  status.appendChild(el('span', 'badge info', `mesin: ${script.generator}`));
  status.appendChild(document.createTextNode(
    ` estimasi ${fmt(script.estimated_duration)} · ${script.word_count} kata`));

  if (script.hook_options.length) {
    hooks.appendChild(el('h3', null, 'Pilihan hook'));
    script.hook_options.forEach((hook, index) => {
      const label = el('label', 'check');
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'hook';
      radio.value = String(index);
      radio.checked = index === script.selected_hook;
      radio.addEventListener('change', () => {
        const first = container.querySelector('textarea[data-section="hook"]');
        if (first) first.value = hook;
      });
      label.appendChild(radio);
      label.appendChild(document.createTextNode(hook));
      hooks.appendChild(label);
    });
  }

  script.sections.forEach((section) => {
    const block = el('div', 'section-block');
    const header = el('header');
    header.appendChild(el('span', 'section-label', section.section));
    const meta = el('span', 'item-meta', `${fmt(section.estimated_duration)} · sumber: ${(section.citations || []).join(', ') || 'belum ada'}`);
    header.appendChild(meta);
    block.appendChild(header);

    const textarea = document.createElement('textarea');
    textarea.rows = 3;
    textarea.value = section.text || '';
    textarea.dataset.section = section.section;
    textarea.setAttribute('aria-label', `Naskah bagian ${section.section}`);
    block.appendChild(textarea);

    const lockLabel = el('label', 'check');
    const lock = document.createElement('input');
    lock.type = 'checkbox';
    lock.checked = Boolean(section.locked);
    lock.dataset.lock = section.section;
    lockLabel.appendChild(lock);
    lockLabel.appendChild(document.createTextNode('Kunci bagian ini'));
    block.appendChild(lockLabel);

    container.appendChild(block);
  });

  (script.warnings || []).forEach((warning) => {
    warnings.appendChild(el('div', 'chk ' + (warning.severity || 'warning'), warning.message));
  });

  // `approved` is already computed above for the status badge.
  $('approve-script-btn').textContent = approved
    ? `Disetujui (v${script.version})`
    : 'Setujui naskah';
  $('approve-script-btn').disabled = approved;
}

function collectSections() {
  const container = $('script-sections');
  return Array.from(container.querySelectorAll('textarea')).map((textarea) => {
    const name = textarea.dataset.section;
    const lock = container.querySelector(`input[data-lock="${name}"]`);
    const original = (state.script.sections || []).find((s) => s.section === name) || {};
    return {
      section: name,
      text: textarea.value,
      locked: Boolean(lock && lock.checked),
      citations: original.citations || [],
    };
  });
}

$('save-script-btn').addEventListener('click', async () => {
  try {
    await api(`/api/projects/${state.projectId}/script`, {
      method: 'PATCH',
      body: { sections: collectSections() },
    });
    toast('Naskah disimpan. Persetujuan direset karena ada perubahan.', 'ok');
    await loadScript();
  } catch (err) { toast(err.message, 'error'); }
});

$('approve-script-btn').addEventListener('click', async () => {
  try {
    await api(`/api/projects/${state.projectId}/script/approve`, {
      method: 'POST',
      body: { editorial_review_confirmed: true },
    });
    toast('Naskah disetujui.', 'ok');
    await loadScript();
  } catch (err) { toast(err.message, 'error'); }
});

$('voice-btn').addEventListener('click', async () => {
  const button = $('voice-btn');
  button.disabled = true;
  try {
    const segments = await api(`/api/projects/${state.projectId}/voice`, { method: 'POST', body: { speed: 1.15 } });
    toast(`Voice-over dibuat: ${segments.length} segmen.`, 'ok');
    await api(`/api/projects/${state.projectId}/timeline`, { method: 'POST' });
    await loadTimeline();
  } catch (err) { toast(err.message, 'error'); }
  finally { button.disabled = false; }
});

/* ---------- timeline ---------- */

$('timeline-btn').addEventListener('click', async () => {
  try {
    await api(`/api/projects/${state.projectId}/timeline`, { method: 'POST' });
    toast('Timeline dibangun ulang.', 'ok');
    await loadTimeline();
  } catch (err) { toast(err.message, 'error'); }
});

async function loadTimeline() {
  const list = $('timeline-list');
  clear(list);
  const scenes = await api(`/api/projects/${state.projectId}/timeline`);
  if (!scenes.length) {
    list.appendChild(el('p', 'hint', 'Belum ada scene.'));
  } else {
    scenes.forEach((scene) => {
      const item = el('div', 'item');
      const main = el('div', 'item-main');
      main.appendChild(el('div', 'item-title', `#${scene.order_index + 1} ${scene.section}`));
      main.appendChild(el('div', 'item-meta',
        `${scene.start_time.toFixed(2)}s → ${scene.end_time.toFixed(2)}s · motion ${scene.motion_mode || scene.effect}` +
        ` · ROI ${scene.roi_label || 'wide'} · ${scene.motion_reason || 'director-selected'}` +
        (scene.asset_id ? '' : ' · TANPA GAMBAR')));
      item.appendChild(main);
      list.appendChild(item);
    });
  }

  const cues = await api(`/api/projects/${state.projectId}/subtitles`);
  $('cue-count').textContent = String(cues.length);
  const cueList = $('cues-list');
  clear(cueList);
  cues.forEach((cue) => {
    const item = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', cue.text));
    main.appendChild(el('div', 'item-meta', `${cue.start_time.toFixed(2)}s → ${cue.end_time.toFixed(2)}s`));
    item.appendChild(main);
    cueList.appendChild(item);
  });
}

/* ---------- quality ---------- */

$('quality-btn').addEventListener('click', async () => {
  try {
    const summary = await api(`/api/projects/${state.projectId}/quality`, { method: 'POST' });
    renderQuality(summary);
    toast(summary.can_publish ? 'Semua pemeriksaan lolos.' : `${summary.errors} error harus diperbaiki.`,
      summary.can_publish ? 'ok' : 'error');
  } catch (err) { toast(err.message, 'error'); }
});

function renderQuality(summary) {
  $('quality-summary').textContent =
    `${summary.total} pemeriksaan · ${summary.errors} error · ${summary.warnings} warning · ` +
    (summary.can_publish ? 'boleh dipublikasikan' : 'BELUM boleh dipublikasikan');
  const list = $('quality-list');
  clear(list);
  (summary.checks || []).forEach((check) => {
    if (check.passed && check.severity === 'info') return;
    const row = el('div', 'chk ' + check.severity);
    row.appendChild(el('strong', null, check.code + ': '));
    row.appendChild(document.createTextNode(check.message));
    if (!check.passed && check.severity === 'warning') {
      const button = el('button', 'btn-link', 'Terima dengan alasan');
      button.type = 'button';
      button.addEventListener('click', async () => {
        const reason = prompt(`Alasan menerima "${check.code}":`);
        if (!reason || reason.trim().length < 5) { toast('Alasan minimal 5 karakter.', 'error'); return; }
        try {
          await api(`/api/projects/${state.projectId}/quality/override`,
            { method: 'POST', body: { code: check.code, reason: reason.trim() } });
          toast('Warning diterima dan dicatat.', 'ok');
          await Promise.all([loadQuality(), loadQCOverrides()]);
        } catch (err) { toast(err.message, 'error'); }
      });
      row.appendChild(document.createTextNode(' '));
      row.appendChild(button);
    }
    list.appendChild(row);
  });
}

async function loadQuality() {
  try {
    const checks = await api(`/api/projects/${state.projectId}/quality`);
    const errors = checks.filter((c) => !c.passed && c.severity === 'error');
    const warnings = checks.filter((c) => !c.passed && c.severity === 'warning');
    renderQuality({
      total: checks.length,
      errors: errors.length,
      warnings: warnings.length,
      can_publish: errors.length === 0 && checks.length > 0,
      checks,
    });
  } catch (_) { /* nothing yet */ }
}

/* ---------- render ---------- */

/* Encoder picker. Options come from a real probe on the server, so an entry is
 * only offered when that backend actually works on this machine. */
async function loadEncoders() {
  const select = $('render-encoder');
  const note = $('encoder-note');
  try {
    const data = await api('/api/encoders');
    clear(select);
    clear(note);

    const auto = el('option', null,
      data.gpu_available ? 'Otomatis (pakai GPU)' : 'Otomatis (CPU)');
    auto.value = 'auto';
    select.appendChild(auto);

    for (const e of data.encoders) {
      const option = el('option', null,
        e.available ? e.label : `${e.label} — tidak tersedia`);
      option.value = e.key;
      // Keep unavailable backends visible but unselectable: seeing *why* a GPU
      // is missing is more useful than the option silently not being there.
      option.disabled = !e.available;
      if (!e.available && e.detail) option.title = e.detail;
      select.appendChild(option);
    }
    select.value = data.configured || 'auto';

    const active = data.active;
    note.appendChild(document.createTextNode(
      data.gpu_available
        ? `GPU terdeteksi. Aktif: ${active.label}.`
        : `Tidak ada GPU yang bisa dipakai; render jalan di CPU. ${active.reason}`));
    state.gpuAvailable = data.gpu_available;

    // Mirror it in the top bar so the encoder is visible without scrolling.
    const badge = $('encoder-badge');
    badge.textContent = data.gpu_available ? `GPU · ${active.encoder}` : 'CPU';
    badge.className = 'badge ' + (data.gpu_available ? 'ok' : 'muted');
    badge.hidden = false;
  } catch (err) {
    note.textContent = 'Tidak bisa memeriksa encoder: ' + err.message;
  }
}

async function startRender(kind) {
  const button = kind === 'final' ? $('render-btn') : $('preview-btn');
  button.disabled = true;
  try {
    const encoder = $('render-encoder').value || 'auto';
    const profile = $('render-profile')?.value || 'Balanced';
    const job = await api(`/api/projects/${state.projectId}/render`, {
      method: 'POST', body: { kind, encoder, profile },
    });
    toast(`Render ${kind} masuk antrean.`, 'ok');
    $('render-progress').hidden = false;
    pollRender(job.id);
  } catch (err) {
    toast(err.message, 'error');
    button.disabled = false;
  }
}

$('render-btn').addEventListener('click', () => startRender('final'));
$('preview-btn').addEventListener('click', () => startRender('preview'));

function pollRender(jobId) {
  clearInterval(state.renderPoll);
  state.renderPoll = setInterval(async () => {
    try {
      const job = await api(`/api/projects/${state.projectId}/render/${jobId}`);
      $('render-progress').value = job.progress;
      $('render-status').textContent = `${job.status} · ${job.progress}% · ${job.stage || ''}`;
      if (job.status === 'succeeded' || job.status === 'failed') {
        clearInterval(state.renderPoll);
        $('render-btn').disabled = false;
        $('preview-btn').disabled = false;
        $('render-progress').hidden = true;
        if (job.status === 'succeeded') {
          // Say which encoder ran. A fallback must never be silent: the user
          // asked for a GPU and deserves to know it was not used.
          const enc = job.encoder_hardware ? `GPU ${job.encoder}` : `CPU ${job.encoder}`;
          const fallback = job.encoder_fell_back ? ` · ${job.encoder_reason}` : '';
          $('render-status').textContent =
            `Selesai · ${fmt(job.duration)} · ${job.width}x${job.height} · ${enc}`
            + ` · sha ${job.checksum.slice(0, 12)}${fallback}`;
          if (job.encoder_fell_back) {
            toast('Render selesai di CPU: GPU yang diminta tidak tersedia.', 'error', 7000);
          }
          showOutput(job);
          toast('Render selesai.', 'ok');
        } else {
          $('render-status').textContent = `Gagal (${job.error_code}): ${job.error_message}`;
          showRetry(job);
          toast('Render gagal.', 'error');
        }
        await loadQuality();
      }
    } catch (err) {
      clearInterval(state.renderPoll);
      $('render-btn').disabled = false;
      $('preview-btn').disabled = false;
      toast(err.message, 'error');
    }
  }, 2000);
}

function showOutput(job) {
  const box = $('render-output');
  clear(box);
  const url = `/api/projects/${state.projectId}/download/${job.id}`;
  const video = document.createElement('video');
  video.controls = true;
  video.src = url;
  box.appendChild(video);
  const link = el('a', 'btn secondary', 'Unduh MP4');
  link.href = url;
  link.setAttribute('download', '');
  box.appendChild(el('div', 'row-actions')).appendChild(link);
}

function showRetry(job) {
  const box = $('render-output');
  clear(box);
  const button = el('button', 'btn', 'Coba render ulang');
  button.type = 'button';
  button.addEventListener('click', async () => {
    try {
      const next = await api(`/api/projects/${state.projectId}/render/${job.id}/retry`, { method: 'POST' });
      $('render-progress').hidden = false;
      pollRender(next.id);
    } catch (err) { toast(err.message, 'error'); }
  });
  box.appendChild(button);
}

async function loadRenders() {
  try {
    const jobs = await api(`/api/projects/${state.projectId}/render`);
    const done = jobs.find((j) => j.status === 'succeeded' && j.kind === 'final');
    if (done) {
      $('render-status').textContent =
        `Render terakhir: ${fmt(done.duration)} · ${done.width}x${done.height}`;
      showOutput(done);
    } else {
      clear($('render-output'));
      $('render-status').textContent = '';
    }
  } catch (_) { /* none yet */ }
}

/* ---------- publish ---------- */

$('metadata-btn').addEventListener('click', async () => {
  try {
    const meta = await api(`/api/projects/${state.projectId}/metadata`);
    $('pub-title').value = meta.title;
    $('pub-desc').value = meta.description;
    $('pub-tags').value = meta.tags.join(', ');
    toast('Metadata diisi dari naskah. Silakan sunting.', 'ok');
  } catch (err) { toast(err.message, 'error'); }
});

$('connect-yt-btn').addEventListener('click', async () => {
  try {
    const data = await api('/api/youtube/connect');
    window.location.href = data.authorization_url;
  } catch (err) { toast(err.message, 'error'); }
});

$('publish-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = {
    video_title: $('pub-title').value.trim(),
    description: $('pub-desc').value.trim(),
    tags: $('pub-tags').value.split(',').map((t) => t.trim()).filter(Boolean),
    privacy_status: $('pub-privacy').value,
    confirm_public: $('pub-confirm').checked,
  };
  try {
    const publication = await api(`/api/projects/${state.projectId}/publish`, { method: 'POST', body });
    $('publish-status').textContent =
      `Status: ${publication.upload_status} · privasi ${publication.privacy_status} · ` +
      `id ${publication.youtube_video_id || '(dry-run)'}`;
    toast('Unggahan diproses.', 'ok');
  } catch (err) {
    $('publish-status').textContent = 'Gagal: ' + err.message;
    toast(err.message, 'error');
  }
});

/* ---------- BYOK: bring your own key (v1.1) ----------
 *
 * The API key lives in a password field, is sent once, and is never echoed back
 * by the server. After a successful save the field is cleared so the value does
 * not sit in the DOM (or in a browser's autofill) longer than necessary.
 */

const byok = {
  catalog: null,     // { llm: [...], tts: [...] }
  models: [],        // models returned by the last successful test
  testedFor: null,   // "kind|provider|base" the model list belongs to
};

function byokSpec() {
  if (!byok.catalog) return null;
  const kind = $('byok-kind').value;
  const provider = $('byok-provider').value;
  return (byok.catalog[kind] || []).find((p) => p.key === provider) || null;
}

/** Reset the model picker whenever the key or endpoint changes. */
function byokInvalidateModels(message) {
  byok.models = [];
  byok.testedFor = null;
  const select = $('byok-model');
  clear(select);
  select.appendChild(el('option', null, message || '— tes kunci dulu untuk memuat model —'));
  select.value = '';
  select.disabled = true;
  $('byok-save').disabled = true;
}

function byokRenderProviders() {
  const kind = $('byok-kind').value;
  const select = $('byok-provider');
  clear(select);
  for (const spec of (byok.catalog[kind] || [])) {
    const option = el('option', null, spec.label);
    option.value = spec.key;
    select.appendChild(option);
  }
  byokRenderProviderNote();
}

function byokRenderProviderNote() {
  const spec = byokSpec();
  const note = $('byok-provider-note');
  clear(note);
  if (!spec) return;

  const bits = [];
  if (spec.custom_endpoint) {
    bits.push('Penyedia ini wajib pakai base URL sendiri.');
  } else if (spec.default_base_url) {
    bits.push(`Default: ${spec.default_base_url}`);
  }
  if (spec.notes) bits.push(spec.notes);
  note.appendChild(document.createTextNode(bits.join(' · ')));

  if (spec.console_url) {
    note.appendChild(document.createTextNode(' '));
    const link = el('a', null, 'Ambil API key');
    link.href = spec.console_url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    note.appendChild(link);
  }
  $('byok-base').placeholder = spec.custom_endpoint
    ? 'wajib, contoh http://127.0.0.1:11434/v1'
    : (spec.default_base_url || 'otomatis dari penyedia');
}

async function byokLoadCatalog() {
  byok.catalog = await api('/api/credentials/providers');
  byokRenderProviders();
}

/** Show which provider each stage will really use. */
async function byokLoadActive() {
  const active = await api('/api/credentials/active');
  const box = $('byok-active');
  clear(box);

  const rows = [
    ['Analisa & naskah', active.llm],
    ['Narasi suara', active.tts],
  ];
  for (const [label, res] of rows) {
    const line = el('div', 'item-meta');
    const badge = el('span', 'badge ' + (res.source === 'byok' ? 'ok' : 'muted'),
      res.source === 'byok' ? 'kunci kamu' : 'offline');
    line.appendChild(badge);
    line.appendChild(document.createTextNode(
      ` ${label}: ${res.reason}${res.model ? ' · model ' + res.model : ''}`));
    box.appendChild(line);
  }
}

async function byokLoadList() {
  const rows = await api('/api/credentials');
  const list = $('byok-list');
  clear(list);

  if (!rows.length) {
    list.appendChild(el('p', 'hint', 'Belum ada kunci tersimpan.'));
    return;
  }

  for (const row of rows) {
    const item = el('div', 'item');
    const main = el('div', 'item-main');

    const kindLabel = row.kind === 'llm' ? 'LLM' : 'TTS';
    main.appendChild(el('div', 'item-title', `${row.label} · ${kindLabel}`));

    const meta = el('div', 'item-meta');
    const ok = row.status === 'verified';
    meta.appendChild(el('span', 'badge ' + (ok ? 'ok' : 'bad'), row.status));
    meta.appendChild(document.createTextNode(
      ` kunci ${row.key_hint} · model ${row.model || '(belum dipilih)'}`
      + (row.is_default ? ' · aktif' : '')));
    main.appendChild(meta);

    if (row.status_message) {
      main.appendChild(el('div', 'item-meta', row.status_message));
    }
    item.appendChild(main);

    // Switch model without re-entering the key.
    if ((row.available_models || []).length) {
      const picker = el('select');
      picker.setAttribute('aria-label', `Model untuk ${row.label}`);
      for (const model of row.available_models) {
        const option = el('option', null, model.label || model.id);
        option.value = model.id;
        if (model.id === row.model) option.selected = true;
        picker.appendChild(option);
      }
      picker.addEventListener('change', async () => {
        try {
          await api(`/api/credentials/${row.id}/model`, {
            method: 'POST', body: { model: picker.value },
          });
          toast('Model diperbarui.', 'ok');
          await byokRefreshPanels();
        } catch (err) { toast(err.message, 'error'); }
      });
      item.appendChild(picker);
    }

    const actions = el('div', 'row-actions');

    const refresh = el('button', 'btn secondary', 'Muat ulang model');
    refresh.type = 'button';
    refresh.addEventListener('click', async () => {
      refresh.disabled = true;
      try {
        const updated = await api(`/api/credentials/${row.id}/refresh`, { method: 'POST' });
        toast(updated.status === 'verified'
          ? 'Kunci masih valid, daftar model diperbarui.'
          : `Kunci bermasalah: ${updated.status_message}`,
          updated.status === 'verified' ? 'ok' : 'error');
        await byokRefreshPanels();
      } catch (err) { toast(err.message, 'error'); }
      finally { refresh.disabled = false; }
    });
    actions.appendChild(refresh);

    if (!row.is_default && row.status === 'verified') {
      const makeDefault = el('button', 'btn secondary', 'Jadikan aktif');
      makeDefault.type = 'button';
      makeDefault.addEventListener('click', async () => {
        try {
          await api(`/api/credentials/${row.id}/default`, { method: 'POST' });
          toast('Kunci ini sekarang aktif.', 'ok');
          await byokRefreshPanels();
        } catch (err) { toast(err.message, 'error'); }
      });
      actions.appendChild(makeDefault);
    }

    const remove = el('button', 'btn secondary', 'Hapus');
    remove.type = 'button';
    remove.addEventListener('click', async () => {
      if (!window.confirm(
        `Hapus kunci ${row.label} (${row.key_hint})? Kunci terenkripsi akan `
        + 'dibuang dan kamu harus memasukkannya lagi kalau ingin dipakai.')) return;
      try {
        await api(`/api/credentials/${row.id}`, { method: 'DELETE' });
        toast('Kunci dihapus.', 'ok');
        await byokRefreshPanels();
      } catch (err) { toast(err.message, 'error'); }
    });
    actions.appendChild(remove);

    item.appendChild(actions);
    list.appendChild(item);
  }
}

async function byokRefreshPanels() {
  await byokLoadActive();
  await byokLoadList();
}

async function loadByok() {
  try {
    if (!byok.catalog) await byokLoadCatalog();
    await byokRefreshPanels();
  } catch (err) {
    $('byok-active').textContent = 'Tidak bisa memuat status penyedia: ' + err.message;
  }
}

$('settings-toggle').addEventListener('click', () => {
  const body = $('settings-body');
  body.hidden = !body.hidden;
  $('settings-toggle').setAttribute('aria-expanded', String(!body.hidden));
  $('settings-toggle').textContent = body.hidden ? 'Buka pengaturan' : 'Tutup';
  // Load the heavier panels only when the section is first opened, so the
  // initial page render stays cheap on a slow machine.
  if (!body.hidden && !state.settingsLoaded) {
    state.settingsLoaded = true;
    loadEncoderTable().catch(() => {});
    loadChannels().catch(() => {});
  }
});

$('byok-kind').addEventListener('change', () => {
  byokRenderProviders();
  byokInvalidateModels();
});

$('byok-provider').addEventListener('change', () => {
  byokRenderProviderNote();
  byokInvalidateModels();
});

// Any change to the key or endpoint means the cached model list is stale.
$('byok-key').addEventListener('input', () => byokInvalidateModels());
$('byok-base').addEventListener('input', () => byokInvalidateModels());

$('byok-test').addEventListener('click', async () => {
  const key = $('byok-key').value.trim();
  const spec = byokSpec();
  const result = $('byok-test-result');
  clear(result);

  if (!key) { toast('Masukkan API key dulu.', 'error'); return; }
  if (spec && spec.custom_endpoint && !$('byok-base').value.trim()) {
    toast('Penyedia ini butuh base URL.', 'error');
    return;
  }

  const button = $('byok-test');
  button.disabled = true;
  button.textContent = 'Menghubungi penyedia…';
  try {
    const body = {
      kind: $('byok-kind').value,
      provider: $('byok-provider').value,
      api_key: key,
      base_url: $('byok-base').value.trim() || null,
    };
    const data = await api('/api/credentials/test', { method: 'POST', body });

    if (!data.ok) {
      byokInvalidateModels('— kunci ditolak —');
      result.textContent = 'Gagal: ' + data.message;
      toast('Kunci ditolak penyedia.', 'error');
      return;
    }

    byok.models = data.models || [];
    byok.testedFor = `${body.kind}|${body.provider}|${body.base_url || ''}`;

    const select = $('byok-model');
    clear(select);
    for (const model of byok.models) {
      const option = el('option', null, model.label || model.id);
      option.value = model.id;
      select.appendChild(option);
    }
    select.disabled = false;
    $('byok-save').disabled = false;
    result.textContent = `Kunci valid. ${byok.models.length} model tersedia — pilih satu lalu simpan.`;
    toast('Kunci valid, daftar model dimuat.', 'ok');
  } catch (err) {
    byokInvalidateModels();
    result.textContent = 'Gagal: ' + err.message;
    toast(err.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = 'Tes & ambil daftar model';
  }
});

$('byok-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const key = $('byok-key').value.trim();
  if (!key) { toast('Masukkan API key dulu.', 'error'); return; }
  if (!$('byok-model').value) { toast('Pilih model dulu.', 'error'); return; }

  const button = $('byok-save');
  button.disabled = true;
  try {
    const body = {
      kind: $('byok-kind').value,
      provider: $('byok-provider').value,
      api_key: key,
      base_url: $('byok-base').value.trim() || null,
      model: $('byok-model').value,
      label: (byokSpec() && byokSpec().label) || '',
    };
    await api('/api/credentials', { method: 'POST', body });

    // Clear the secret from the form as soon as it is stored.
    $('byok-key').value = '';
    byokInvalidateModels();
    $('byok-test-result').textContent = '';
    toast('Kunci disimpan terenkripsi dan langsung dipakai.', 'ok');
    await byokRefreshPanels();
  } catch (err) {
    toast(err.message, 'error');
    button.disabled = false;
  }
});

/* ---------- analysis (FR-03, exposed in the UI from v1.3) ----------
 *
 * The script is generated from this data, so letting the user correct a
 * misdetected character or twist here is the cheapest way to improve the final
 * video. Previously the endpoints existed but nothing called them.
 */

function linesToList(value) {
  return value.split('\n').map((s) => s.trim()).filter(Boolean);
}

async function loadAnalysis() {
  const notes = $('analysis-notes');
  clear(notes);
  let analysis;
  try {
    analysis = await api(`/api/projects/${state.projectId}/analysis`);
  } catch (_) {
    notes.textContent = 'Belum ada analisa. Jalankan “Buat draft otomatis” atau “Analisa ulang”.';
    $('event-count').textContent = '0';
    clear($('events-list'));
    return;
  }
  state.analysis = analysis;

  $('an-conflict').value = analysis.main_conflict || '';
  $('an-twist').value = analysis.twist || '';
  $('an-cliff').value = analysis.cliffhanger || '';
  $('an-characters').value = (analysis.characters || [])
    .map((c) => c.name).filter(Boolean).join('\n');
  $('an-locations').value = (analysis.locations || []).join('\n');

  const list = $('events-list');
  clear(list);
  const events = analysis.events || [];
  $('event-count').textContent = String(events.length);
  events.forEach((event) => {
    const item = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', event.text || ''));
    main.appendChild(el('div', 'item-meta', `jenis: ${event.kind || 'event'}`));
    item.appendChild(main);
    list.appendChild(item);
  });

  const lowConfidence = analysis.low_confidence_notes || [];
  if (lowConfidence.length) {
    lowConfidence.forEach((note) => {
      notes.appendChild(el('div', 'item-meta', '• ' + note));
    });
  } else {
    notes.textContent = 'Analisa siap. Tidak ada catatan ketidakpastian.';
  }
}

$('analysis-run-btn').addEventListener('click', () => withBusy(
  $('analysis-run-btn'), 'Menganalisa…', async () => {
    try {
      await api(`/api/projects/${state.projectId}/analysis`, { method: 'POST' });
      toast('Analisa selesai.', 'ok');
      await loadAnalysis();
    } catch (err) { toast(err.message, 'error'); }
  }));

$('analysis-save-btn').addEventListener('click', () => withBusy(
  $('analysis-save-btn'), 'Menyimpan…', async () => {
    // Keep the roles and citations the analyser found; only the name changed.
    const previous = (state.analysis && state.analysis.characters) || [];
    const characters = linesToList($('an-characters').value).map((name) => {
      const match = previous.find((c) => c.name === name);
      return match || { name, role: '', aliases: [] };
    });
    const body = {
      main_conflict: $('an-conflict').value.trim(),
      twist: $('an-twist').value.trim(),
      cliffhanger: $('an-cliff').value.trim(),
      characters,
      locations: linesToList($('an-locations').value),
    };
    try {
      await api(`/api/projects/${state.projectId}/analysis`, { method: 'PATCH', body });
      toast('Analisa disimpan. Buat ulang naskah agar perubahan terpakai.', 'ok');
      await loadAnalysis();
    } catch (err) { toast(err.message, 'error'); }
  }));

/* ---------- script version history (v1.3) ---------- */

async function loadScriptVersions() {
  const box = $('script-versions');
  clear(box);
  try {
    const versions = await api(`/api/projects/${state.projectId}/scripts`);
    if (!versions.length) {
      box.appendChild(el('p', 'hint', 'Belum ada versi naskah.'));
      return;
    }
    versions.forEach((version) => {
      const item = el('div', 'item');
      const main = el('div', 'item-main');
      main.appendChild(el('div', 'item-title', `Versi ${version.version}`));
      main.appendChild(el('div', 'item-meta',
        `${fmt(version.estimated_duration)} · ${version.word_count} kata`
        + ` · mesin ${version.generator}`));
      item.appendChild(main);
      item.appendChild(el('span', 'badge ' + (version.approved_at ? 'ok' : 'muted'),
        version.approved_at ? 'disetujui' : 'draft'));
      box.appendChild(item);
    });
  } catch (_) {
    box.appendChild(el('p', 'hint', 'Riwayat belum tersedia.'));
  }
}

/* ---------- render history (v1.3) ---------- */

async function loadRenderHistory() {
  const box = $('render-history');
  clear(box);
  try {
    const jobs = await api(`/api/projects/${state.projectId}/render`);
    if (!jobs.length) {
      box.appendChild(el('p', 'hint', 'Belum ada render.'));
      return;
    }
    jobs.forEach((job) => {
      const item = el('div', 'item');
      const main = el('div', 'item-main');
      main.appendChild(el('div', 'item-title', `${job.kind} · percobaan ${job.attempt}`));
      const bits = [job.status];
      if (job.render_profile) bits.push(`profile ${job.render_profile}`);
      if (job.duration) bits.push(fmt(job.duration));
      if (job.encoder) bits.push(job.encoder_hardware ? `GPU ${job.encoder}` : `CPU ${job.encoder}`);
      if (job.encoder_fell_back) bits.push('fallback ke CPU');
      if (job.error_code) bits.push(job.error_code);
      main.appendChild(el('div', 'item-meta', bits.join(' · ')));
      item.appendChild(main);

      const good = job.status === 'succeeded';
      item.appendChild(el('span', 'badge ' + (good ? 'ok' : job.status === 'failed' ? 'bad' : 'muted'),
        job.status));
      box.appendChild(item);
    });
  } catch (_) { /* none yet */ }
}

/* ---------- publish readiness + history (v1.3) ---------- */

$('readiness-btn').addEventListener('click', () => withBusy(
  $('readiness-btn'), 'Memeriksa…', loadReadiness));

async function loadReadiness() {
  const box = $('readiness-status');
  clear(box);
  try {
    // Shape from publish.can_publish(): { ready, reason, checks }.
    const data = await api(`/api/projects/${state.projectId}/publish/readiness`);
    box.appendChild(el('span', 'badge ' + (data.ready ? 'ok' : 'bad'),
      data.ready ? 'siap diunggah' : 'belum siap'));

    if (data.reason) {
      box.appendChild(el('div', 'item-meta', 'Penghalang: ' + data.reason));
    } else if (data.ready) {
      box.appendChild(document.createTextNode(' Semua prasyarat terpenuhi.'));
    }
    if (data.checks) {
      box.appendChild(el('div', 'item-meta',
        `${data.checks.total} pemeriksaan · ${data.checks.errors} error · `
        + `${data.checks.warnings} warning`));
    }
  } catch (err) {
    box.textContent = 'Tidak bisa memeriksa kesiapan: ' + err.message;
  }
}

async function loadPublications() {
  const box = $('publications-list');
  clear(box);
  try {
    const rows = await api(`/api/projects/${state.projectId}/publications`);
    if (!rows.length) {
      box.appendChild(el('div', 'empty', 'Belum ada riwayat publikasi.'));
      return;
    }
    rows.forEach((row) => {
      const item = el('div', 'item');
      const main = el('div', 'item-main');
      main.appendChild(el('div', 'item-title', row.video_title || '(tanpa judul)'));
      main.appendChild(el('div', 'item-meta',
        `${row.privacy_status} · ${row.upload_status}`
        + (row.youtube_video_id ? ` · id ${row.youtube_video_id}` : ' · dry-run')
        + (row.error_message ? ` · ${row.error_message}` : '')));
      item.appendChild(main);

      const actions = el('div', 'row-actions');
      if (row.upload_status === 'failed') {
        const retry = el('button', 'btn secondary small', 'Coba lagi');
        retry.type = 'button';
        retry.addEventListener('click', () => withBusy(retry, 'Mengulang…', async () => {
          try {
            await api(`/api/publications/${row.id}/retry`, { method: 'POST' });
            toast('Unggahan diulang.', 'ok');
            await loadPublications();
          } catch (err) { toast(err.message, 'error'); }
        }));
        actions.appendChild(retry);
      }
      if (row.youtube_video_id) {
        const stats = el('button', 'btn secondary small', 'Sinkron statistik');
        stats.type = 'button';
        stats.addEventListener('click', () => withBusy(stats, 'Menyinkron…', async () => {
          try {
            const data = await api(`/api/publications/${row.id}/stats/sync`, { method: 'POST' });
            toast(data && data.available === false
              ? 'Statistik belum tersedia (mode dry-run).'
              : 'Statistik diperbarui.', data && data.available === false ? 'error' : 'ok');
            await loadPublications();
          } catch (err) { toast(err.message, 'error'); }
        }));
        actions.appendChild(stats);
      }
      if (actions.childNodes.length) item.appendChild(actions);
      box.appendChild(item);
    });
  } catch (_) { /* none yet */ }
}

/* ---------- YouTube channels (v1.3) ---------- */

async function loadChannels() {
  const box = $('channels-list');
  clear(box);
  try {
    const rows = await api('/api/youtube/channels');
    if (!rows.length) {
      box.appendChild(el('div', 'empty', 'Belum ada channel terhubung.'));
      return;
    }
    rows.forEach((row) => {
      const item = el('div', 'item');
      const main = el('div', 'item-main');
      main.appendChild(el('div', 'item-title', row.channel_title || row.channel_id || 'Channel'));
      main.appendChild(el('div', 'item-meta',
        row.revoked ? 'akses dicabut' : 'terhubung'));
      item.appendChild(main);

      const remove = el('button', 'btn secondary small', 'Putuskan');
      remove.type = 'button';
      remove.addEventListener('click', async () => {
        if (!window.confirm(`Putuskan channel ${row.channel_title || row.channel_id}?`)) return;
        try {
          await api(`/api/youtube/channels/${row.id}`, { method: 'DELETE' });
          toast('Channel diputuskan.', 'ok');
          await loadChannels();
        } catch (err) { toast(err.message, 'error'); }
      });
      item.appendChild(remove);
      box.appendChild(item);
    });
  } catch (err) {
    box.appendChild(el('p', 'hint', 'Tidak bisa memuat channel: ' + err.message));
  }
}

$('refresh-channels-btn').addEventListener('click', () => withBusy(
  $('refresh-channels-btn'), 'Memuat…', loadChannels));

/* ---------- encoder capability table (v1.3) ---------- */

async function loadEncoderTable() {
  const box = $('encoder-table');
  clear(box);
  try {
    const data = await api('/api/encoders');
    const table = el('table');
    const head = el('tr');
    ['Encoder', 'Jenis', 'Status'].forEach((h) => head.appendChild(el('th', null, h)));
    table.appendChild(head);

    data.encoders.forEach((e) => {
      const row = el('tr');
      row.appendChild(el('td', null, e.label));
      row.appendChild(el('td', null, e.hardware ? 'GPU' : 'CPU'));
      const status = el('td');
      status.appendChild(el('span', 'badge ' + (e.available ? 'ok' : 'muted'),
        e.available ? 'siap' : 'tidak tersedia'));
      if (!e.available && e.detail) {
        status.appendChild(el('div', 'item-meta', e.detail));
      }
      row.appendChild(status);
      table.appendChild(row);
    });
    box.appendChild(table);
  } catch (err) {
    box.appendChild(el('p', 'hint', 'Tidak bisa memuat daftar encoder: ' + err.message));
  }
}

/* ---------- project actions (v1.3) ---------- */

$('duplicate-project-btn').addEventListener('click', () => withBusy(
  $('duplicate-project-btn'), 'Menduplikat…', async () => {
    if (!state.projectId) { toast('Pilih proyek dulu.', 'error'); return; }
    try {
      const copy = await api(`/api/projects/${state.projectId}/duplicate`, { method: 'POST' });
      toast('Proyek diduplikat beserta materinya.', 'ok');
      state.projectId = copy.id;
      await loadProjects();
    } catch (err) { toast(err.message, 'error'); }
  }));

$('delete-project-btn').addEventListener('click', async () => {
  if (!state.projectId) { toast('Pilih proyek dulu.', 'error'); return; }
  const select = $('project-select');
  const name = select.options[select.selectedIndex]
    ? select.options[select.selectedIndex].textContent : 'proyek ini';
  if (!window.confirm(
    `Hapus ${name}? Semua materi, naskah, dan hasil render ikut terhapus. `
    + 'Tindakan ini tidak bisa dibatalkan.')) return;
  try {
    await api(`/api/projects/${state.projectId}`, { method: 'DELETE' });
    toast('Proyek dihapus.', 'ok');
    state.projectId = null;
    await loadProjects();
  } catch (err) { toast(err.message, 'error'); }
});

$('cancel-project-btn').addEventListener('click', () => {
  $('project-form').hidden = true;
});

/* ---------- step navigation ---------- */

$('steps-nav').addEventListener('click', (event) => {
  const chip = event.target.closest('.step-chip');
  if (!chip) return;
  const target = document.getElementById(chip.dataset.target);
  if (!target) return;
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  // Move focus too, so keyboard and screen-reader users follow the jump.
  target.setAttribute('tabindex', '-1');
  target.focus({ preventScroll: true });
});

/* ---------- character counter ---------- */

$('src-text').addEventListener('input', () => {
  const count = $('src-text').value.length;
  $('src-count').textContent = String(count);
});

/* ---------- boot ---------- */

loadHealth();
checkSession();
if (new URLSearchParams(window.location.search).get('connected')) {
  toast('Channel YouTube terhubung.', 'ok');
}
