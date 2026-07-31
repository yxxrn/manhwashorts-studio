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
};

/* ---------- helpers ---------- */

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
  $('logout-btn').hidden = false;
  $('auth-section').hidden = true;
  $('studio').hidden = false;
  await loadVoices();
  await loadByok();
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
  $('studio').hidden = true;
  $('auth-section').hidden = false;
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
    target_duration: Number($('p-duration').value) || 60,
    narration_style: $('p-style').value,
    spoiler_level: $('p-spoiler').value,
    voice_id: $('p-voice').value || 'id',
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
  await Promise.all([loadAssets(), loadScript(), loadTimeline(), loadQuality()]);
  $('srt-link').href = `/api/projects/${state.projectId}/subtitles.srt`;
  await loadRenders();
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
  if (!assets.length) {
    list.appendChild(el('p', 'hint', 'Belum ada materi. Tambahkan teks atau unggah panel.'));
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
  let script;
  try {
    script = await api(`/api/projects/${state.projectId}/script`);
  } catch (_) {
    container.appendChild(el('p', 'hint', 'Belum ada naskah. Jalankan “Buat draft otomatis”.'));
    return;
  }
  state.script = script;

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

  const approved = Boolean(script.approved_at);
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
    await api(`/api/projects/${state.projectId}/script/approve`, { method: 'POST' });
    toast('Naskah disetujui.', 'ok');
    await loadScript();
  } catch (err) { toast(err.message, 'error'); }
});

$('voice-btn').addEventListener('click', async () => {
  const button = $('voice-btn');
  button.disabled = true;
  try {
    const segments = await api(`/api/projects/${state.projectId}/voice`, { method: 'POST', body: { speed: 1.0 } });
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
        `${scene.start_time.toFixed(2)}s → ${scene.end_time.toFixed(2)}s · efek ${scene.effect}` +
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
          await loadQuality();
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

async function startRender(kind) {
  const button = kind === 'final' ? $('render-btn') : $('preview-btn');
  button.disabled = true;
  try {
    const job = await api(`/api/projects/${state.projectId}/render`, { method: 'POST', body: { kind } });
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
          $('render-status').textContent =
            `Selesai · ${fmt(job.duration)} · ${job.width}x${job.height} · sha ${job.checksum.slice(0, 12)}`;
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

$('byok-toggle').addEventListener('click', () => {
  const body = $('byok-body');
  body.hidden = !body.hidden;
  $('byok-toggle').setAttribute('aria-expanded', String(!body.hidden));
  $('byok-toggle').textContent = body.hidden ? 'Atur kunci' : 'Tutup';
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

/* ---------- boot ---------- */

loadHealth();
checkSession();
if (new URLSearchParams(window.location.search).get('connected')) {
  toast('Channel YouTube terhubung.', 'ok');
}
