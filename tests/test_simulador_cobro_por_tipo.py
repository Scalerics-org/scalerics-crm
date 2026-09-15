"""Forma de cobro por tipo de venta en el simulador (pedido de Juan, 15/9).

"Las webs se venden de una y cuenta todo como si se cobra siempre la mitad."
Cada tipo (web, ecommerce, a medida) elige "Todo al confirmar" o "Mitad ahora
y mitad al entregar". Lo de mitad entra el porcentaje al confirmar en el mes
de la venta y el resto `mesesEntrega` meses después. Defaults: web entera, el
resto en dos partes.

La cuenta corre de verdad en node, recortando el bloque puro igual que
test_simulador_calculo.py.
"""

import json
import re
import shutil
import sqlite3
import subprocess

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db

HTML = dashboard.DASHBOARD_HTML
INICIO = "// ── sim: calculo puro (inicio) ──"
FIN = "// ── sim: calculo puro (fin) ──"

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

_AYUDAS = """
function assert(c, m) { if (!c) { throw new Error(m || 'assert'); } }
function igual(a, b, m) {
  if (typeof a !== 'number' || Math.abs(a - b) > 1e-6) {
    throw new Error((m || 'igual') + ': ' + a + ' != ' + b);
  }
}
function contiene(texto, pedazo) {
  if (String(texto).indexOf(pedazo) < 0) {
    throw new Error('"' + texto + '" no contiene "' + pedazo + '"');
  }
}
// Solo un tipo vendido, para ver su forma de cobro aislada.
function soloUno(e, tipo, cantidad) {
  e.ventas.webs = 0; e.ventas.ecommerce = 0; e.ventas.aMedida = 0;
  e.ventas[tipo] = cantidad;
}
function escenario(cambios) {
  const e = simEscenarioBase(null);
  if (cambios) cambios(e);
  return e;
}
"""


def _correr(cuerpo: str, tmp_path) -> None:
    bloque = HTML[HTML.index(INICIO):HTML.index(FIN)]
    archivo = tmp_path / "cobro.js"
    archivo.write_text(bloque + "\n" + _AYUDAS + "\n" + cuerpo, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr


# ── la cuenta ────────────────────────────────────────────────────────────────

@sin_node
def test_los_defaults_web_entera_y_el_resto_en_dos_partes(tmp_path):
    _correr("""
      assert(SIM_DEFAULTS.cobros.formas.web === 'todo', 'la web se vende de una');
      assert(SIM_DEFAULTS.cobros.formas.ecommerce === 'mitad');
      assert(SIM_DEFAULTS.cobros.formas.aMedida === 'mitad');
      const base = simEscenarioBase(null);
      base.cobros.formas.web = 'mitad';
      assert(SIM_DEFAULTS.cobros.formas.web === 'todo', 'la base es una copia');
    """, tmp_path)


@sin_node
def test_todo_al_confirmar_cae_entero_en_el_mes_de_la_venta(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => {
        soloUno(e, 'webs', 2); e.cobros.mesesEntrega = 2; e.cobros.mesesProyeccion = 4;
      }));
      igual(r.facturadoProyectos, 1400);
      igual(r.cobroDeNuevos, 1400); igual(r.quedaDeEsteMes, 0);
      igual(r.porTipo[0].alConfirmar, 1400); igual(r.porTipo[0].alEntregar, 0);
      assert(r.meses.length === 4, 'cuatro meses');
      r.meses.forEach(m => { igual(m.alConfirmar, 1400); igual(m.alEntregar, 0, 'mes ' + m.mes); });
      igual(r.meses[0].caja, r.cajaDelMes, 'el primer mes es la caja del mes');

      // El mismo ecommerce, pasado a "todo", tambien entra entero.
      const ecom = simCalcular(escenario(e => { soloUno(e, 'ecommerce', 2); e.cobros.formas.ecommerce = 'todo'; }));
      igual(ecom.cobroDeNuevos, 2200); igual(ecom.quedaDeEsteMes, 0);
    """, tmp_path)


@sin_node
def test_mitad_y_mitad_reparte_entre_la_venta_y_la_entrega(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => {
        soloUno(e, 'ecommerce', 2); e.cobros.mesesEntrega = 2; e.cobros.mesesProyeccion = 4;
      }));
      igual(r.cobroDeNuevos, 1100); igual(r.quedaDeEsteMes, 1100);
      igual(r.porCobrarAdelante, 1100);
      // Mes 1 y 2 solo lo de confirmar; desde el 3 llega la mitad de lo vendido dos meses antes.
      const entregas = r.meses.map(m => m.alEntregar);
      assert(JSON.stringify(entregas) === JSON.stringify([0, 0, 1100, 1100]), JSON.stringify(entregas));
      igual(r.meses[0].entra, 1100); igual(r.meses[2].entra, 2200);
      igual(r.meses[0].caja, r.cajaDelMes);
      igual(r.meses[3].saldo, r.cajaActual + r.meses.reduce((a, m) => a + m.caja, 0));

      // Otro porcentaje al confirmar: 30 ahora, 70 al entregar.
      const p30 = simCalcular(escenario(e => { soloUno(e, 'aMedida', 1); e.cobros.porcentajeAlFirmar = 30; }));
      igual(p30.cobroDeNuevos, 600); igual(p30.quedaDeEsteMes, 1400);
      igual(p30.meses[1].alEntregar, 1400, 'con entrega a 1 mes llega en el mes 2');

      // Entrega en el mismo mes: las dos mitades caen este mes.
      const cero = simCalcular(escenario(e => { soloUno(e, 'ecommerce', 2); e.cobros.mesesEntrega = 0; }));
      igual(cero.cobroDeNuevos, 2200); igual(cero.quedaDeEsteMes, 0);
      cero.meses.forEach(m => igual(m.alEntregar, 0));
    """, tmp_path)


@sin_node
def test_mezcla_de_tipos(tmp_path):
    """Defaults: 2 webs de 700 enteras, 2 ecommerce de 1.100 y 1 a medida de
    2.000 en dos partes, entrega a un mes. Sale 2.530 por mes."""
    _correr("""
      const r = simCalcular(escenario());
      const t = {}; r.porTipo.forEach(x => { t[x.clave] = x; });
      igual(t.web.alConfirmar, 1400); igual(t.web.alEntregar, 0);
      igual(t.ecommerce.alConfirmar, 1100); igual(t.ecommerce.alEntregar, 1100);
      igual(t.aMedida.alConfirmar, 1000); igual(t.aMedida.alEntregar, 1000);
      igual(r.cobroDeNuevos, 3500); igual(r.quedaDeEsteMes, 2100);
      igual(r.cobroDeNuevos + r.quedaDeEsteMes, r.facturadoProyectos, 'no se pierde ni se inventa plata');
      assert(r.meses.length === 6, 'seis meses por defecto');
      igual(r.meses[0].entra, 3500); igual(r.meses[0].caja, 970); igual(r.meses[0].saldo, 970);
      igual(r.meses[1].entra, 5600); igual(r.meses[1].caja, 3070); igual(r.meses[1].saldo, 4040);
      assert(r.primerMesNegativo === null);

      // A medida pasa a entero: entra 1.000 mas este mes y 1.000 menos al entregar.
      const medida = simCalcular(escenario(e => { e.cobros.formas.aMedida = 'todo'; }));
      igual(medida.cobroDeNuevos, 4500); igual(medida.quedaDeEsteMes, 1100);

      // Con la capacidad topeada, cada tipo se escala igual y respeta su forma.
      const tope = simCalcular(escenario(e => { e.ventas.webs = 6; }));
      const w = tope.porTipo[0], ec = tope.porTipo[1];
      igual(w.alConfirmar, 4200 * 6 / 9); igual(w.alEntregar, 0);
      igual(ec.alConfirmar, 1100 * 6 / 9); igual(ec.alEntregar, 1100 * 6 / 9);
    """, tmp_path)


@sin_node
def test_pendientes_y_aporte_entran_solo_el_primer_mes(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => {
        e.pendientes = [{nombre: 'Viejo', monto: 500, activo: true}, {nombre: 'Sin fecha', monto: 900, activo: false}];
        e.palancas.aporteJavier = true;
      }));
      igual(r.meses[0].pendientes, 500); igual(r.meses[0].aporte, 1000);
      igual(r.meses[1].pendientes, 0); igual(r.meses[1].aporte, 0);
      igual(r.meses[0].caja, r.cajaDelMes);
    """, tmp_path)


@sin_node
def test_el_primer_mes_con_la_caja_en_negativo(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => {
        e.ventas.webs = 0; e.ventas.ecommerce = 0; e.ventas.aMedida = 0; e.caja.cajaActual = 3000;
      }));
      igual(r.meses[0].saldo, 470); igual(r.meses[1].saldo, -2060);
      assert(r.primerMesNegativo === 2, 'quiebre: ' + r.primerMesNegativo);
      contiene(simTextoMeses(r), 'La caja queda en negativo en el mes 2.');
      contiene(simMesesHtml(r), 'sim-negativo');

      const nada = simCalcular(escenario(e => { e.cobros.mesesProyeccion = 0; }));
      assert(nada.meses.length === 0 && simTextoMeses(nada) === '');
      contiene(simMesesHtml(nada), 'Poné cuántos meses');
    """, tmp_path)


@sin_node
def test_una_forma_invalida_toma_la_del_default(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => { e.cobros.formas = {web: 'cualquiera', ecommerce: null}; }));
      assert(r.porTipo[0].forma === 'todo' && r.porTipo[1].forma === 'mitad' && r.porTipo[2].forma === 'mitad',
             JSON.stringify(r.porTipo));
      igual(r.cobroDeNuevos, 3500);
      const sinCobros = simCalcular({ventas: {webs: 1}});
      assert(sinCobros.porTipo[0].forma === 'todo');
    """, tmp_path)


@sin_node
def test_un_escenario_guardado_conserva_sus_formas_y_uno_viejo_sigue_igual(tmp_path):
    _correr("""
      const guardado = simEscenarioAbierto(JSON.parse(JSON.stringify(
        escenario(e => { e.cobros.formas.ecommerce = 'todo'; }))));
      assert(guardado.cobros.formas.web === 'todo' && guardado.cobros.formas.ecommerce === 'todo');
      igual(simCalcular(guardado).cobroDeNuevos, 1400 + 2200 + 1000);

      // Antes del 15/9 no habia formas y todo se cobraba en dos partes: se abre igual.
      const viejo = simEscenarioAbierto({cobros: {porcentajeAlFirmar: 50}});
      igual(simCalcular(viejo).cobroDeNuevos, 2800);
    """, tmp_path)


@sin_node
def test_los_textos_dicen_cuanto_entra_y_cuando(tmp_path):
    _correr("""
      const r = simCalcular(escenario());
      const t = {}; r.porTipo.forEach(x => { t[x.clave] = x; });
      assert(simTextoCobroTipo(t.web, r) === 'Entra todo en el mes de la venta: USD 1.400.', simTextoCobroTipo(t.web, r));
      assert(simTextoCobroTipo(t.ecommerce, r) === 'Este mes entran USD 1.100 y USD 1.100 al entregar, 1 mes después.',
             simTextoCobroTipo(t.ecommerce, r));
      contiene(simTextoArrastre(r), 'USD 2.100 de lo que vendés este mes (entra al entregar, 1 mes después)');
      const sinWebs = simCalcular(escenario(e => { e.ventas.webs = 0; }));
      assert(simTextoCobroTipo(sinWebs.porTipo[0], sinWebs) === 'Sin ventas de este tipo este mes.');
      const dos = simCalcular(escenario(e => { e.cobros.mesesEntrega = 2; }));
      contiene(simTextoCobroTipo(dos.porTipo[1], dos), '2 meses después');
      const html = simMesesHtml(r);
      contiene(html, '<td>Este mes</td>'); contiene(html, '<td>Mes 2</td>'); contiene(html, 'De entregas');
    """, tmp_path)


# ── la pantalla ──────────────────────────────────────────────────────────────

def test_cada_tipo_tiene_su_select_de_cobro():
    panel = HTML[HTML.index('id="simulador-panel"'):HTML.index("<!-- ======= METRICS PANEL ======= -->")]
    for clave in ("web", "ecommerce", "aMedida"):
        m = re.search(r'<select id="sim-forma-' + clave + r'" class="sim-in sim-in-forma" data-sim-forma="'
                      + clave + r'">(.*?)</select>', panel, re.S)
        assert m, clave
        assert '<option value="todo">Todo al confirmar</option>' in m.group(1)
        assert '<option value="mitad">Mitad ahora y mitad al entregar</option>' in m.group(1)
        assert f'id="sim-cobro-{clave}"' in panel
    assert 'data-sim="cobros.mesesEntrega"' in panel
    assert 'id="sim-meses"' in panel and "Caja mes a mes" in panel


@pytest.fixture
def app(tmp_path, monkeypatch):
    db = str(tmp_path / "cobro.db")
    init_db(db)
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def cli(app):
    db = app.config["_DB"]
    uid = create_user(db, name="socio", email="socio@scalerics.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)",
                       ("rol-sim", json.dumps(["simulador"]))).lastrowid
    conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
    conn.commit()
    conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "socio"
    return c


def test_get_raiz_abre_con_200_y_trae_los_selects(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    assert 'data-sim-forma="web"' in pagina
    assert "Mitad ahora y mitad al entregar" in pagina


def test_la_forma_de_cobro_se_guarda_con_el_escenario(cli):
    datos = {"version": 1, "cobros": {"porcentajeAlFirmar": 50, "mesesEntrega": 2,
                                      "mesesProyeccion": 6,
                                      "formas": {"web": "todo", "ecommerce": "todo",
                                                 "aMedida": "mitad"}}}
    r = cli.post("/api/simulador/escenarios", json={"nombre": "Webs de una", "datos": datos})
    assert r.status_code in (200, 201), r.get_json()
    eid = r.get_json()["id"]
    leido = cli.get(f"/api/simulador/escenarios/{eid}").get_json()
    assert leido["datos"]["cobros"]["formas"] == datos["cobros"]["formas"]
    assert leido["datos"]["cobros"]["mesesEntrega"] == 2
