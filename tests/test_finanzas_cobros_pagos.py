"""Finanzas → Cobros y pagos (Juan, 16/10).

Los movimientos del rango partidos por el tilde de "hecho": arriba lo que
falta cobrar y pagar, abajo lo ya hecho. Tildar en Movimientos o acá los
cambia de lugar.
"""

import json
import re
import shutil
import subprocess

import pytest

import dashboard

HTML = dashboard.DASHBOARD_HTML


def test_la_pestana_va_entre_cobro_con_tarjeta_y_esenciales():
    tarjeta = HTML.index('id="fin-tab-tarjeta"')
    cobros = HTML.index('id="fin-tab-cobros"')
    esenciales = HTML.index('id="fin-tab-esenciales"')
    assert tarjeta < cobros < esenciales


def test_la_vista_esta_registrada():
    assert 'id="fin-vista-cobros"' in HTML
    assert "'tarjeta', 'cobros', 'balance'" in HTML
    assert "if (cual === 'cobros') loadCobrosPagos();" in HTML


def test_tildar_y_cambiar_de_mes_refrescan_la_pestana():
    marcar = re.search(r"async function finMarcarHecho\(.*?\n\}", HTML, re.S).group(0)
    assert "loadCobrosPagos()" in marcar
    finanzas = re.search(r"async function loadFinanzas\(\) \{.*?\n\}", HTML, re.S).group(0)
    assert "if (_finVistaActual === 'cobros') loadCobrosPagos();" in finanzas


@pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")
def test_parte_los_movimientos_por_el_tilde(tmp_path):
    """Corre el loadCobrosPagos real en node con un DOM y un fetch de mentira."""
    cuerpo = re.search(r"async function loadCobrosPagos\(\) \{.*?\n\}", HTML, re.S).group(0)
    filas = re.search(r"function _finCobrosFilas\(movs, vacio\) \{.*?\n\}", HTML, re.S).group(0)
    movs = [
        {"id": 1, "tipo": "ingreso", "hecho": 0, "fecha": "2026-10-01", "concepto": "Cobro Bloquera", "categoria": "servicios", "monto_usd": 500},
        {"id": 2, "tipo": "egreso", "hecho": 0, "fecha": "2026-10-02", "concepto": "Fly.io", "categoria": "servidores", "monto_usd": 30},
        {"id": 3, "tipo": "ingreso", "hecho": 1, "fecha": "2026-10-03", "concepto": "Cobro Trouville", "categoria": "servicios", "monto_usd": 200},
        {"id": 4, "tipo": "egreso", "hecho": 1, "fecha": "2026-10-04", "concepto": "Resend", "categoria": "servidores", "monto_usd": 20},
    ]
    script = """
const els = {};
const document = {getElementById: id => (els[id] = els[id] || {innerHTML: ''})};
const window = {};
const esc = s => s;
const _finUsd = n => 'US$ ' + n;
const _finSoloLectura = () => false;
const _finRango = () => ({desde: '2026-10-01', hasta: '2026-10-31'});
const fetch = async () => ({ok: true, json: async () => %s});
%s
%s
loadCobrosPagos().then(() => console.log(JSON.stringify({
  ing: els['fin-cobros-ingresos'].innerHTML, egr: els['fin-cobros-egresos'].innerHTML,
  hechos: els['fin-cobros-hechos'].innerHTML, tot: els['fin-cobros-totales'].innerHTML})));
""" % (json.dumps(movs), cuerpo, filas)
    archivo = tmp_path / "cobros.js"
    archivo.write_text(script, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)

    assert "Cobro Bloquera" in out["ing"] and "Fly.io" not in out["ing"]
    assert "Fly.io" in out["egr"] and "Cobro Bloquera" not in out["egr"]
    assert "Cobro Trouville" in out["hechos"] and "Resend" in out["hechos"]
    assert "Cobro Bloquera" not in out["hechos"] and "Fly.io" not in out["hechos"]
    # Lo de arriba se tilda para marcarlo; lo de abajo, para desmarcarlo.
    assert "finMarcarHecho(1, 1)" in out["ing"]
    assert "finMarcarHecho(3, 0)" in out["hechos"]
    for etiqueta, monto in (("Falta cobrar", 500), ("Falta pagar", 30), ("Ya cobrado", 200), ("Ya pagado", 20)):
        assert re.search(etiqueta + r".*?US\$ " + str(monto) + "<", out["tot"]), etiqueta
