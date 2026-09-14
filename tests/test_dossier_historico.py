"""La linea de comparacion: como viene el periodo contra todo lo anterior.

Juan, textual: "quiero que las graficas se comparen con el historico, para saber
si estamos mas arriba/abajo". Una barra sola no dice si 18 dolares por lead esta
bien; contra un historico de 12 dice que empeoro.

Dos decisiones que son la diferencia entre que el numero sirva o mienta:

1. **El historico es lo de ANTES del periodo, no todo.** Si mirando setiembre el
   promedio incluyera setiembre, el periodo se estaria comparando contra si
   mismo y la diferencia se achicaria sola. Cuanto mas grande el periodo contra
   la historia, mas se achica: en el limite, mirar "todo" daria diferencia cero
   siempre.

2. **El CPL historico es plata total sobre leads totales, no el promedio de los
   CPL semanales.** Promediar ratios le da el mismo peso a una semana de 2 leads
   que a una de 30. Es el mismo error que sacar el promedio de dos porcentajes
   con denominadores distintos.
"""

import pytest

from database import _connect, init_db
from services.dossier import historico


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, cuando, eventos=()):
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, phone, source, scraped_at) "
            "VALUES (?,?,?,?)", (cuando, cuando, "meta", cuando))
        for estado in eventos:
            conn.execute("INSERT INTO lead_events (lead_id, new_status) VALUES (?,?)",
                         (cur.lastrowid, estado))
        conn.commit()
    finally:
        conn.close()


def _gasto(db, fecha, spend, impresiones=0, clics=0, campana="UY"):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_insights (date, campaign_id, campaign_name, spend, "
            "currency, impressions, clicks, leads) VALUES (?,?,?,?,?,?,?,?)",
            (fecha, str(abs(hash((fecha, campana))) % 10**9), campana, spend,
             "USD", impresiones, clics, 0))
        conn.commit()
    finally:
        conn.close()


# ── Lo que entra y lo que no ───────────────────────────────────────────────

def test_no_mira_nada_del_periodo_que_se_esta_viendo(db):
    """El corte es estricto: el dia de `desde` ya es periodo, no historia."""
    _gasto(db, "2026-08-31", 100.0)
    _lead(db, "2026-08-31 10:00:00")
    _gasto(db, "2026-09-01", 999.0)
    _lead(db, "2026-09-01 10:00:00")
    h = historico(db, "2026-09-01")
    assert h["gasto"] == 100.0
    assert h["leads"] == 1


def test_sin_nada_anterior_no_hay_historico(db):
    """Mirando el primer mes con datos no hay contra que comparar. Devolver
    ceros dibujaria una linea en cero que parece un dato y no lo es."""
    _lead(db, "2026-03-05 10:00:00")
    h = historico(db, "2026-03-01")
    assert h["hay"] is False
    assert h["cpl"] is None


def test_dice_de_cuando_a_cuando_es(db):
    """La linea tiene que poder decir "promedio hasta el 31/8": un promedio sin
    periodo no se puede discutir."""
    _gasto(db, "2026-03-10", 50.0)
    _lead(db, "2026-05-20 10:00:00")
    h = historico(db, "2026-09-01")
    assert h["hay"] is True
    assert h["desde"] == "2026-03-10"
    assert h["hasta"] == "2026-08-31"


# ── La trampa del promedio de ratios ───────────────────────────────────────

def test_el_cpl_es_plata_total_sobre_leads_totales(db):
    """Una semana cara y flaca contra una barata y gorda.

    Semana 1: 100 dolares, 2 leads  -> CPL 50
    Semana 2: 100 dolares, 18 leads -> CPL 5,56
    El promedio de los dos CPL da 27,78. El CPL de verdad es 200/20 = 10.
    Si el panel dibujara 27,78 como "historico", casi cualquier semana futura
    pareceria buenisima.
    """
    _gasto(db, "2026-03-02", 100.0)
    for i in range(2):
        _lead(db, f"2026-03-0{2 + i} 10:00:00")
    _gasto(db, "2026-03-09", 100.0)
    for i in range(18):
        _lead(db, f"2026-03-09 {10 + i % 12}:{i:02d}:00")

    h = historico(db, "2026-04-01")
    assert h["leads"] == 20
    assert h["gasto"] == 200.0
    assert h["cpl"] == 10.0          # no 27.78


def test_el_costo_por_demo_tambien_es_un_ratio(db):
    _gasto(db, "2026-03-02", 300.0)
    _lead(db, "2026-03-02 10:00:00", eventos=("demo_1",))
    _lead(db, "2026-03-03 10:00:00", eventos=("cerrado",))
    _lead(db, "2026-03-04 10:00:00")
    h = historico(db, "2026-04-01")
    # `cerrado` implica demo: son 2 demos, no 1.
    assert h["demos"] == 2
    assert h["costo_demo"] == 150.0


def test_sin_leads_historicos_el_cpl_no_es_cero(db):
    """Cero seria "sale gratis". No hay CPL: no se puede calcular."""
    _gasto(db, "2026-03-02", 100.0)
    h = historico(db, "2026-04-01")
    assert h["hay"] is True
    assert h["cpl"] is None


# ── Los promedios por semana ───────────────────────────────────────────────

def test_el_promedio_semanal_divide_por_semanas_con_actividad(db):
    """Dos semanas con gasto y una muerta en el medio.

    Se divide por 2 y no por 3: una semana en la que no corrio nada no es una
    semana floja, es una semana que no existe para esta comparacion. Meterla
    bajaria el promedio y haria parecer que cualquier semana activa esta bien.
    """
    _gasto(db, "2026-03-02", 100.0)     # semana del 2/3
    _gasto(db, "2026-03-16", 300.0)     # semana del 16/3, la del 9 sin nada
    h = historico(db, "2026-04-01")
    assert h["semanas"] == 2
    assert h["gasto_semana"] == 200.0


def test_los_leads_por_semana_cuentan_los_del_crm(db):
    _gasto(db, "2026-03-02", 10.0)
    for i in range(6):
        _lead(db, f"2026-03-0{2 + i} 10:00:00")
    h = historico(db, "2026-04-01")
    assert h["semanas"] == 1
    assert h["leads_semana"] == 6.0


def test_una_semana_con_leads_y_sin_gasto_tambien_cuenta(db):
    """Actividad es gasto O leads. Un lead que entro sin pauta esa semana
    —organico, referido— igual paso."""
    _gasto(db, "2026-03-02", 100.0)
    _lead(db, "2026-03-10 10:00:00")
    h = historico(db, "2026-04-01")
    assert h["semanas"] == 2


def test_trae_clics_e_impresiones_por_semana(db):
    _gasto(db, "2026-03-02", 100.0, impresiones=5000, clics=150)
    _gasto(db, "2026-03-09", 100.0, impresiones=3000, clics=50)
    h = historico(db, "2026-04-01")
    assert h["impresiones_semana"] == 4000.0
    assert h["clics_semana"] == 100.0


# ── Forma ──────────────────────────────────────────────────────────────────

def test_solo_mira_leads_de_meta(db):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, scraped_at) "
                     "VALUES ('x','x','discovery','2026-03-05 10:00:00')")
        conn.commit()
    finally:
        conn.close()
    assert historico(db, "2026-09-01")["hay"] is False


def test_las_claves_estan_todas_aunque_no_haya_historia(db):
    """El panel lee `h.cpl` sin preguntar si hay historia: si la clave faltara,
    reventaria el pintado entero y el panel quedaria sin graficos."""
    h = historico(db, "2026-03-01")
    for clave in ("hay", "desde", "hasta", "semanas", "leads", "demos", "gasto",
                  "cpl", "costo_demo", "leads_semana", "gasto_semana",
                  "clics_semana", "impresiones_semana"):
        assert clave in h, f"falta la clave {clave}"
