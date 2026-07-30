window._isAdmin = false; // default until /api/me resolves
// ========== Sidebar mobile ==========
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-backdrop').classList.toggle('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-backdrop').classList.remove('open');
}

// ========== Panel switching (registro OCP) ==========
// Cada crm-<panel>.js se auto-registra con registerPanel(id, init). showPanel
// hace lo mismo que el switch hardcodeado anterior: muestra el contenedor,
// marca el nav activo y llama al init del panel registrado.
const CRM_PANELS = {};
function registerPanel(id, init) { CRM_PANELS[id] = init; }
let activePanel = 'cola';
function showPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById(name + '-panel').classList.add('active');
  const sideNav = document.getElementById('nav-' + name);
  if (sideNav) sideNav.classList.add('active');
  activePanel = name;
  _syncMobileNav(name);
  closeSidebar();
  const init = CRM_PANELS[name];
  if (init) init();
}
