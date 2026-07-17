const state = {
  connected: false,
  courses: [],
  stats: {},
  manifest: {},
  content: {},
  activeCourse: null,
  activeFolder: '',
  selected: new Map(),
  activeAsset: null,
  eventSource: null,
  operationBusy: false,
};

const $ = id => document.getElementById(id);

function escapeHtml(value) {
  const element = document.createElement('span');
  element.textContent = value == null ? '' : String(value);
  return element.innerHTML;
}

function formatSize(bytes) {
  let size = Number(bytes) || 0;
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (size < 1024 || unit === 'TB') return `${size.toFixed(size < 10 && unit !== 'B' ? 1 : 0)} ${unit}`;
    size /= 1024;
  }
  return '0 B';
}

function formatDate(value) {
  if (!value) return 'nunca';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'nunca' : date.toLocaleString('es-CL', { dateStyle: 'medium', timeStyle: 'short' });
}

const MIME_LABELS = {
  'application/pdf': 'PDF',
  'application/zip': 'ZIP',
  'application/x-zip-compressed': 'ZIP',
  'application/x-7z-compressed': '7Z',
  'application/msword': 'DOC',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
  'application/vnd.ms-excel': 'XLS',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
  'application/vnd.ms-powerpoint': 'PPT',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PPTX',
  'text/plain': 'TXT',
  'text/html': 'HTML',
  'video/mp4': 'MP4',
  'image/jpeg': 'JPG',
  'image/png': 'PNG',
};

function fileTypeLabel(asset) {
  if (asset.asset_type === 'embedded') return 'ADJUNTO';
  if (MIME_LABELS[asset.mime]) return MIME_LABELS[asset.mime];
  const extension = String(asset.name || '').split('.').pop();
  return extension && extension !== asset.name ? extension.toUpperCase() : 'ARCHIVO';
}

function assetKey(courseId, ref) { return `${courseId}:${ref}`; }

function todayLabel() {
  const label = $('todayLabel');
  if (label) label.textContent = new Date().toLocaleDateString('es-CL', { day: '2-digit', month: 'short' }).toUpperCase();
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let body = {};
  try { body = await response.json(); } catch {}
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

// Authentication -----------------------------------------------------------

async function checkAuth() {
  todayLabel();
  loadStorageStatus();
  try {
    const data = await api('/api/auth/status');
    if (data.connected) setConnected(data.user, data.restored);
    else loadDashboard();
  } catch { loadDashboard(); }
}

function setConnected(user, restored = false) {
  state.connected = true;
  $('statusDot').className = 'status-dot on';
  $('statusText').textContent = 'Conectado';
  $('userInfo').textContent = user ? user.name : 'Cuenta institucional';
  $('userInfo').hidden = false;
  $('btnConnect').hidden = true;
  $('btnDisconnect').hidden = false;
  $('authGate').hidden = true;
  if (restored) showToast('Sesión restaurada desde el archivo local');
  loadDashboard();
}

async function disconnect() {
  try { await api('/api/auth/disconnect', { method: 'POST' }); } catch {}
  state.connected = false;
  state.courses = [];
  state.content = {};
  state.selected.clear();
  $('statusDot').className = 'status-dot off';
  $('statusText').textContent = 'Sin conexión';
  $('userInfo').hidden = true;
  $('btnConnect').hidden = false;
  $('btnDisconnect').hidden = true;
  $('authGate').hidden = false;
  renderCourses();
}

function showConnectModal() {
  $('connectModal').hidden = false;
  loadConsoleScript();
}

function closeConnectModal() { $('connectModal').hidden = true; }

function switchTab(tabId) {
  document.querySelectorAll('.connect-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.tab === tabId));
  document.querySelectorAll('.connect-content').forEach(tab => tab.classList.toggle('active', tab.id === tabId));
}

async function connectManual() {
  const input = $('cookieInput');
  const errorBox = $('manualError');
  errorBox.hidden = true;
  try {
    const data = await api('/api/auth/connect_manual', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cookies: input.value.trim() }) });
    if (!data.success) throw new Error(data.error || 'Cookies inválidas');
    input.value = '';
    closeConnectModal();
    setConnected(data.user);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

async function loadConsoleScript() {
  try { $('consoleScript').textContent = (await api('/api/auth/script')).script; } catch { $('consoleScript').textContent = '// No se pudo cargar el script'; }
}

async function copyConsoleScript() {
  const text = $('consoleScript').textContent;
  try { await navigator.clipboard.writeText(text); showToast('Script copiado'); } catch { showToast('No se pudo copiar el script'); }
}

async function reloadSession() {
  try {
    const data = await api('/api/auth/connect', { method: 'POST' });
    if (!data.success) throw new Error('No se encontró una sesión válida');
    closeConnectModal();
    setConnected(data.user);
  } catch (error) { showToast(error.message); }
}

// Storage -------------------------------------------------------------------

function showStorageModal() { $('storageModal').hidden = false; loadStorageStatus(); }
function closeStorageModal() { $('storageModal').hidden = true; }

function updateStorageStatus(data) {
  const labels = { local: 'Este equipo', onedrive: 'OneDrive', custom: 'Otra carpeta' };
  $('btnStorage').textContent = `Almacenamiento · ${labels[data.kind] || 'Elegir'}`;
  $('storageName').textContent = labels[data.kind] || 'Destino activo';
  $('storageRoot').textContent = data.root || 'Sin seleccionar';
  $('storagePath').value = data.root || '';
  $('storageState').textContent = data.exists ? `${formatSize(data.free_bytes)} disponibles` : 'El destino se creará al guardar el primer archivo.';
}

async function loadStorageStatus() {
  try { updateStorageStatus(await api('/api/storage/status')); } catch (error) { $('storageError').textContent = error.message; $('storageError').hidden = false; }
}

async function selectStorage(kind) {
  $('storageError').hidden = true;
  const path = kind === 'custom' ? $('storagePath').value.trim() : '';
  try {
    const data = await api('/api/storage/select', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, path }) });
    if (data.task_id) {
      closeStorageModal();
      showOperations('Cambiando almacenamiento');
      connectOperation(data.task_id, data.total);
    } else {
      updateStorageStatus(data);
      showToast('Destino de almacenamiento actualizado');
    }
  } catch (error) { $('storageError').textContent = error.message; $('storageError').hidden = false; }
}

// Dashboard ----------------------------------------------------------------

async function loadDashboard() {
  try {
    const requests = [api('/api/stats'), api('/api/manifest')];
    if (state.connected) requests.push(api('/api/courses'));
    const [stats, manifest, courses = []] = await Promise.all(requests);
    state.stats = stats;
    state.manifest = manifest;
    if (state.connected) state.courses = courses;
    updateDashboardStats();
    renderCourses();
  } catch (error) {
    showToast(`No se pudo cargar el archivo: ${error.message}`);
  }
}

function updateDashboardStats() {
  const stats = state.stats || {};
  $('metricSize').textContent = stats.total_size_fmt || '0 B';
  $('metricFiles').textContent = `${stats.total_files || 0} archivos`;
  $('metricCourses').textContent = stats.courses_count || state.courses.length || 0;
  $('metricVerified').textContent = stats.verified_files != null ? `${stats.verified_files}` : '--';
  const issues = (stats.pending_files || 0) + (stats.corrupt_files || 0);
  $('metricIssues').textContent = issues ? `${issues} pendientes o con errores` : 'sin incidencias';
  const checked = stats.manifest_checked || 0;
  const verified = stats.verified_files || 0;
  const percent = checked ? Math.round(verified / checked * 100) : 0;
  $('healthPercent').textContent = checked ? `${percent}%` : '--';
  $('healthMeter').style.width = `${percent}%`;
  $('auditLabel').textContent = `Última auditoría: ${formatDate(stats.last_audit)}`;
  const status = $('healthStatus');
  status.className = 'status-pill ' + (stats.corrupt_files ? 'error' : stats.pending_files ? 'pending' : checked ? 'verified' : 'neutral');
  status.textContent = stats.corrupt_files ? 'Revisar' : stats.pending_files ? 'Pendiente' : checked ? 'Verificado' : 'Esperando';
  $('healthMessage').textContent = checked ? `${verified} de ${checked} archivos tienen una copia local íntegra.` : 'Ejecuta una auditoría para comprobar la integridad del archivo local.';
}

function courseSummary(course) {
  const files = Number(course.file_count || 0);
  const verified = Number(course.verified_files || 0);
  const errors = Number(course.error_files || 0);
  return { files, verified, errors, percent: files ? Math.round(verified / files * 100) : 0, status: errors ? 'error' : verified < files ? 'pending' : files ? 'complete' : 'neutral' };
}

function populateTerms() {
  const select = $('termFilter');
  const current = select.value;
  const terms = [...new Set(state.courses.map(course => course.term).filter(Boolean))].sort();
  select.innerHTML = '<option value="">Todos los periodos</option>' + terms.map(term => `<option value="${escapeHtml(term)}">${escapeHtml(term)}</option>`).join('');
  select.value = terms.includes(current) ? current : '';
}

function renderCourses() {
  populateTerms();
  const search = ($('courseSearch')?.value || '').toLowerCase().trim();
  const term = $('termFilter')?.value || '';
  const filter = $('statusFilter')?.value || '';
  const visible = state.courses.filter(course => {
    const summary = courseSummary(course);
    const haystack = `${course.name || ''} ${course.display_id || ''} ${course.term || ''}`.toLowerCase();
    return (!search || haystack.includes(search)) && (!term || course.term === term) && (!filter || summary.status === filter);
  });
  $('courseCount').textContent = `${visible.length} curso${visible.length === 1 ? '' : 's'}`;
  const grid = $('coursesGrid');
  if (!state.connected) { grid.innerHTML = '<div class="empty-state">Conecta tu cuenta para cargar el índice de cursos.</div>'; return; }
  if (!visible.length) { grid.innerHTML = '<div class="empty-state">No hay cursos que coincidan con estos filtros.</div>'; return; }
  grid.innerHTML = visible.map((course, index) => {
    const summary = courseSummary(course);
  const statusText = summary.status === 'complete' ? 'Archivo local OK' : summary.status === 'pending' ? 'Pendientes locales' : summary.status === 'error' ? 'Con errores' : 'Sin archivos locales';
  const cardMeta = summary.files ? `${summary.verified} archivos locales` : 'Sin archivos locales';
    return `<article class="course-card" data-course-card="${escapeHtml(course.id)}" onclick="openCourse('${escapeHtml(course.id)}')">
      <div><div class="course-card-top"><span class="section-kicker">${String(index + 1).padStart(2, '0')} / ${escapeHtml(course.term || 'SIN PERIODO')}</span><span class="status-pill ${summary.status === 'complete' ? 'verified' : summary.status === 'error' ? 'error' : summary.status === 'pending' ? 'pending' : 'neutral'}">${statusText}</span></div>
      <h3>${escapeHtml(course.name || 'Curso sin nombre')}</h3><span class="course-code">${escapeHtml(course.display_id || course.id || 'SIN CÓDIGO')}</span></div>
      <div class="course-card-foot"><span class="course-card-meta">${cardMeta}<span class="course-card-sep"> · </span>${formatSize(course.total_size || 0)}</span><button class="course-card-action" aria-label="Abrir curso">→</button></div>
    </article>`;
  }).join('');
}

function showDashboard() {
  $('dashboardView').hidden = false;
  $('courseWorkspace').hidden = true;
  state.activeCourse = null;
  state.activeAsset = null;
  clearSelection();
}

// Course explorer ----------------------------------------------------------

async function openCourse(courseId, forceRefresh = true) {
  const course = state.courses.find(item => item.id === courseId);
  if (!course) return;
  state.activeCourse = course;
  state.activeFolder = '';
  $('dashboardView').hidden = true;
  $('courseWorkspace').hidden = false;
  $('workspaceName').textContent = course.name || 'Curso sin nombre';
  $('workspaceTerm').textContent = course.term || 'SIN PERIODO';
  $('workspaceCode').textContent = course.display_id || course.id;
  $('workspaceState').className = 'status-pill neutral';
  $('workspaceState').textContent = 'Cargando';
  $('courseTree').innerHTML = '<div class="loading-state"><span class="loader"></span>Descubriendo contenido...</div>';
  $('filesTable').innerHTML = '<tr><td colspan="6" class="table-state"><span class="loader"></span>Cargando archivos...</td></tr>';
  try {
    if (forceRefresh || !state.content[courseId]) state.content[courseId] = await api(`/api/courses/${encodeURIComponent(courseId)}/content`);
    renderWorkspace();
  } catch (error) {
    $('courseTree').innerHTML = `<div class="empty-state">No se pudo cargar: ${escapeHtml(error.message)}</div>`;
    $('filesTable').innerHTML = '<tr><td colspan="6" class="table-empty">No se pudo cargar el contenido del curso.</td></tr>';
  }
}

function flattenAssets(nodes, folder = '') {
  const assets = [];
  for (const node of nodes || []) {
    const nextFolder = node.type === 'folder' ? `${folder}/${node.title}`.replace(/^\//, '') : folder;
    if (node.file) assets.push({ ...node.file, downloaded: node.downloaded, course_id: state.activeCourse.id, content_id: node.id, handler: node.handler, title: node.title, modified: node.modified, location: folder || 'Raíz', asset_type: 'file' });
    for (const attachment of node.attachments || []) assets.push({ ...attachment, course_id: state.activeCourse.id, content_id: node.id, handler: node.handler, title: node.title, modified: attachment.modified || node.modified, location: `${folder || 'Raíz'} / adjuntos`, asset_type: 'embedded' });
    assets.push(...flattenAssets(node.children, nextFolder));
  }
  return assets;
}

function renderWorkspace() {
  const assets = flattenAssets(state.content[state.activeCourse.id]);
  state.activeAssets = assets;
  const summary = { files: assets.length, verified: assets.filter(asset => asset.downloaded).length };
  Object.assign(state.activeCourse, {
    file_count: summary.files,
    verified_files: summary.verified,
    total_size: assets.reduce((total, asset) => total + (Number(asset.size) || 0), 0),
  });
  const hasFiles = summary.files > 0;
  $('workspaceState').className = `status-pill ${!hasFiles ? 'neutral' : summary.verified === summary.files ? 'verified' : 'pending'}`;
  $('workspaceState').textContent = !hasFiles ? 'Sin archivos descargables' : `${summary.verified}/${summary.files} verificados`;
  $('syncCourseButton').disabled = !hasFiles || summary.verified === summary.files;
  renderTree();
  renderFiles();
}

function renderTree() {
  const nodes = state.content[state.activeCourse.id] || [];
  const buttons = [];
  const walk = (items, path = '', depth = 0) => {
    for (const node of items || []) {
      if (node.type !== 'folder') continue;
      const current = `${path}/${node.title}`.replace(/^\//, '');
      const count = (state.activeAssets || []).filter(asset => asset.location === current || asset.location.startsWith(`${current} /`)).length;
      buttons.push(`<button class="tree-item ${state.activeFolder === current ? 'active' : ''}" style="padding-left:${7 + depth * 10}px" onclick="setFolder('${encodeURIComponent(current)}')"><span class="tree-icon tree-icon-folder" aria-hidden="true"></span><span>${escapeHtml(node.title)}</span><span class="tree-count">${count}</span></button>`);
      walk(node.children, current, depth + 1);
    }
  };
  walk(nodes);
  $('courseTree').innerHTML = !(state.activeAssets || []).length
    ? '<div class="tree-empty">Este curso no contiene archivos descargables.</div>'
    : `<button class="tree-item ${!state.activeFolder ? 'active' : ''}" onclick="setFolder('')"><span class="tree-icon tree-icon-root" aria-hidden="true"></span><span>Todo el curso</span><span class="tree-count">${(state.activeAssets || []).length}</span></button>${buttons.join('')}`;
}

function setFolder(folder) { state.activeFolder = decodeURIComponent(folder); renderTree(); renderFiles(); }
function expandAllFolders() { state.activeFolder = ''; renderTree(); renderFiles(); }

function renderFiles() {
  const search = ($('fileSearch')?.value || '').toLowerCase().trim();
  const assets = (state.activeAssets || []).filter(asset => (!state.activeFolder || asset.location === state.activeFolder || asset.location.startsWith(`${state.activeFolder} /`)) && (!search || `${asset.name} ${asset.location} ${asset.mime}`.toLowerCase().includes(search)));
  $('filesTitle').textContent = assets.length ? `${assets.length} archivo${assets.length === 1 ? '' : 's'}` : 'Sin archivos descargables';
  $('filesTable').innerHTML = assets.length ? assets.map(asset => {
    const key = assetKey(asset.course_id, asset.ref);
    const checked = state.selected.has(key);
    const status = asset.downloaded ? 'verified' : 'pending';
    return `<tr class="${checked ? 'selected' : ''}" data-asset-key="${escapeHtml(key)}" onclick="inspectAsset('${escapeHtml(key)}')"><td class="check-col" onclick="event.stopPropagation()"><input type="checkbox" ${checked ? 'checked' : ''} onchange="toggleAsset('${escapeHtml(key)}', this.checked)"></td><td class="file-name" title="${escapeHtml(asset.name)}">${escapeHtml(asset.name)}</td><td class="file-location" title="${escapeHtml(asset.location)}">${escapeHtml(asset.location)}</td><td class="file-type">${fileTypeLabel(asset)}</td><td>${formatSize(asset.size)}</td><td><span class="state-label ${status}">${asset.downloaded ? 'Verificado' : 'Pendiente'}</span></td></tr>`;
  }).join('') : '<tr><td colspan="6" class="table-empty">No hay archivos descargables en esta vista.</td></tr>';
  $('selectVisible').checked = assets.length > 0 && assets.every(asset => state.selected.has(assetKey(asset.course_id, asset.ref)));
}

function findAsset(key) { return (state.activeAssets || []).find(asset => assetKey(asset.course_id, asset.ref) === key); }

function inspectAsset(key) {
  const asset = findAsset(key);
  if (!asset) return;
  state.activeAsset = asset;
  const manifestEntry = state.manifest?.courses?.[asset.course_id]?.files?.[asset.ref] || {};
  $('inspector').innerHTML = `<div class="section-kicker">INSPECTOR DE ARCHIVO</div><h3>${escapeHtml(asset.name)}</h3><span class="status-label ${asset.downloaded ? 'verified' : 'pending'}">${asset.downloaded ? 'Copia verificada' : 'Pendiente de descarga'}</span><span class="inspector-label">UBICACIÓN</span><p class="inspector-value">${escapeHtml(asset.location)}</p><span class="inspector-label">TAMAÑO ESPERADO</span><p class="inspector-value">${formatSize(asset.size)}</p><span class="inspector-label">ORIGEN</span><p class="inspector-value">${asset.asset_type === 'embedded' ? 'Adjunto embebido en contenido' : 'Archivo directo de Blackboard'}</p><span class="inspector-label">SHA-256</span><p class="inspector-value hash-value">${escapeHtml(manifestEntry.sha256 || 'Se calculará al verificar')}</p><span class="inspector-label">RUTA LOCAL</span><p class="inspector-value">${escapeHtml(asset.path || 'Pendiente')}</p>`;
  document.querySelectorAll('tbody tr').forEach(row => row.classList.toggle('selected', row.dataset.assetKey === key));
}

function toggleAsset(key, checked) {
  const asset = findAsset(key);
  if (!asset) return;
  if (checked) state.selected.set(key, asset); else state.selected.delete(key);
  renderFiles();
  updateSelectionDock();
}

function toggleVisible(checked) {
  const search = ($('fileSearch')?.value || '').toLowerCase().trim();
  (state.activeAssets || []).filter(asset => (!state.activeFolder || asset.location === state.activeFolder || asset.location.startsWith(`${state.activeFolder} /`)) && (!search || `${asset.name} ${asset.location} ${asset.mime}`.toLowerCase().includes(search))).forEach(asset => {
    const key = assetKey(asset.course_id, asset.ref);
    if (checked) state.selected.set(key, asset); else state.selected.delete(key);
  });
  renderFiles(); updateSelectionDock();
}

function clearSelection() { state.selected.clear(); renderFiles(); updateSelectionDock(); }
function updateSelectionDock() { $('selectionDock').hidden = state.selected.size === 0; $('selectionCount').textContent = `${state.selected.size} seleccionado${state.selected.size === 1 ? '' : 's'}`; $('selectionWeight').textContent = `${formatSize([...state.selected.values()].reduce((sum, asset) => sum + (Number(asset.size) || 0), 0))} estimados`; }

// Operations ----------------------------------------------------------------

function toDownloadItem(asset) {
  return { course_id: asset.course_id, content_id: asset.content_id, handler: asset.handler, title: asset.title || asset.name, term_name: state.activeCourse?.term || state.courses.find(course => course.id === asset.course_id)?.term || '', course_name: state.activeCourse?.name || state.courses.find(course => course.id === asset.course_id)?.name || '', file_ref: asset.ref, file_name: asset.name, file_size: asset.size || 0, file_url: asset.url || '', file_path: asset.path || '', asset_type: asset.asset_type || 'file', modified: asset.modified || null };
}

async function syncSelected() { await startSync([...state.selected.values()].map(toDownloadItem), 'Sincronización seleccionada'); }
async function syncCourse() { if (state.activeCourse) await startSync((state.activeAssets || []).filter(asset => !asset.downloaded).map(toDownloadItem), 'Sincronización de curso'); }

async function syncAllMissing() {
  if (!state.connected) { showConnectModal(); return; }
  if (state.operationBusy) return;
  showOperations('Sincronización');
  setOperationPhase('Analizando', 'Consultando cursos y archivos en Blackboard...');
  $('operationLog').innerHTML = '<div>Revisando contenido remoto y archivos locales...</div>';
  try {
    await new Promise(resolve => requestAnimationFrame(resolve));
    const missing = await api('/api/courses/missing', { method: 'POST' });
    if (!missing.length) {
      finishOperation('No hay archivos pendientes');
      return;
    }
    await startSync(missing, `Sincronizar ${missing.length} pendientes`);
  } catch (error) { showOperations('Sincronización'); failOperation(error.message); }
}

async function startSync(items, title) {
  if (!items.length) { showToast('No hay archivos pendientes en esta vista'); return; }
  showOperations(title);
  setOperationPhase('En cola', `${items.length} elementos listos; iniciando descarga...`);
  try {
    const response = await api('/api/download', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items }) });
    connectOperation(response.task_id, items.length);
  } catch (error) { failOperation(error.message); }
}

function setOperationPhase(label, message) { $('operationFill').classList.add('indeterminate'); $('operationFill').style.width = '35%'; $('operationCount').textContent = label; $('operationMessage').textContent = message; }
function showOperations(title) { state.operationBusy = true; $('operationsPanel').hidden = false; $('operationTitle').textContent = title; $('operationFill').classList.remove('indeterminate'); $('operationFill').style.width = '0%'; $('operationCount').textContent = 'Preparando'; $('operationMessage').textContent = 'Preparando...'; $('operationLog').innerHTML = ''; }
function hideOperations() { $('operationsPanel').hidden = true; }
function failOperation(message) { state.operationBusy = false; $('operationFill').classList.remove('indeterminate'); $('operationMessage').textContent = message; $('operationLog').innerHTML += `<div class="error">${escapeHtml(message)}</div>`; }
function finishOperation(message) { state.operationBusy = false; $('operationFill').classList.remove('indeterminate'); $('operationFill').style.width = '100%'; $('operationCount').textContent = 'Terminado'; $('operationMessage').textContent = message; $('operationLog').innerHTML += `<div class="ok">${escapeHtml(message)}</div>`; return loadDashboard(); }

function connectOperation(taskId, total) {
  if (state.eventSource) state.eventSource.close();
  $('operationFill').classList.remove('indeterminate');
  $('operationFill').style.width = '0%';
  $('operationCount').textContent = `0/${total}`;
  $('operationMessage').textContent = 'Iniciando descargas...';
  state.eventSource = new EventSource(`/api/progress/${taskId}`);
  state.eventSource.onmessage = async event => {
    const data = JSON.parse(event.data);
    if (data.type === 'progress') { const done = data.completed || 0; $('operationFill').style.width = `${Math.min(100, done / total * 100)}%`; $('operationCount').textContent = `${done}/${total}`; }
    if (['ok', 'skip', 'error'].includes(data.type)) $('operationLog').innerHTML += `<div class="${data.type}">${escapeHtml(data.message || '')}</div>`;
    if (data.type === 'file') $('operationMessage').textContent = data.message || 'Descargando...';
    if (data.type === 'failed') {
      state.eventSource.close();
      failOperation(data.message || 'La descarga se detuvo');
      await loadDashboard();
    }
    if (data.type === 'complete') {
      state.eventSource.close();
      await finishOperation(data.message || 'Sincronización completada');
      if (state.activeCourse) openCourse(state.activeCourse.id, true);
    }
  };
  state.eventSource.onerror = () => {
    if (state.eventSource) state.eventSource.close();
    failOperation('Se perdió la conexión con la operación; revisa el registro del servidor');
  };
}

async function runAudit() {
  showOperations('Verificar archivos · SHA-256');
  try { const result = await api('/api/manifest/audit', { method: 'POST' }); finishOperation(`${result.verified || 0} archivos verificados`); } catch (error) { failOperation(error.message); }
}

// Small UI helpers ----------------------------------------------------------

function showToast(message) { const toast = $('toast'); toast.textContent = message; toast.hidden = false; clearTimeout(showToast.timer); showToast.timer = setTimeout(() => { toast.hidden = true; }, 3500); }

checkAuth();
