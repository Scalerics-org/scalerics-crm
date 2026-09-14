"""Al entrar a Marketing lo primero abierto es un mes (pedido de Juan, 14/9).

Antes arrancaba en "Últimos 90 días", una ventana que corta meses a la mitad y
no se lee mes a mes. `_mkPintarPeriodo` ya muestra el navegador de mes y arma
el rango del mes en curso cuando el selector dice "mes": alcanza con que esa
sea la opcion elegida al cargar.
"""

import re

import dashboard

HTML = dashboard.DASHBOARD_HTML


def _selector() -> str:
    m = re.search(r'<select id="mk-rango"[^>]*>(.*?)</select>', HTML, re.S)
    assert m, "no encontre el selector de periodo de Marketing"
    return m.group(1)


def test_el_periodo_arranca_en_un_mes():
    opciones = re.findall(r'<option value="([^"]+)"( selected)?>', _selector())
    elegidas = [valor for valor, marcada in opciones if marcada]
    assert elegidas == ["mes"], opciones


def test_el_navegador_de_mes_se_muestra_cuando_el_periodo_es_un_mes():
    m = re.search(r"\nfunction _mkPintarPeriodo\(.*?\n\}", HTML, re.S)
    assert m, "no encontre _mkPintarPeriodo"
    cuerpo = m.group(0)
    assert "cual === 'mes'" in cuerpo
    assert "getElementById('mk-nav-mes').style.display = esMes ? '' : 'none'" in cuerpo
