"""Outbound de Scalerics Fidelidad (pedido de Juan, 23/9).

Scalerics Fidelidad es el sistema de puntos que se le vende a restaurantes: el
cliente suma puntos por compra y los canjea por promos, y además ve la carta
digital. Lo vende un socio aparte, y Outbound + Inteligencia comercial pasan a
ser SU espacio de trabajo: los prospectos son restaurantes de Municipio CH y
Carrasco, no los comercios del padrón.

Por eso vive en tablas propias (`fid_*`) y no en `businesses`: allá hay leads de
Meta, clientes y el padrón de las campañas de mail, y nada de eso tiene que
mezclarse con los restaurantes del socio (ni al revés).

La idea central: **cada llamada deja agendada la siguiente**. El vendedor no
anota "llamar el jueves" en ningún lado: elige cómo salió la llamada y la regla
de `RESULTADOS` decide cuándo vuelve a aparecer en su cola.

Todas las fechas se guardan en hora de Montevideo, como texto
'YYYY-MM-DD HH:MM'. Es lo que ve el vendedor y lo que agrupa el mapa de
horarios; guardar UTC obligaba a convertir en cada consulta.
"""

import csv
import io
import json
import math
import re
import sqlite3
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

from services.seg_leads import MVD, ahora_mvd

ROL_VENDEDOR = "Vendedor Fidelidad"

ESTADOS = ["sin_contactar", "contactado", "reunion_agendada", "reunion_hecha",
           "piloto", "cerrado", "descartado"]
ESTADO_LABEL = {
    "sin_contactar": "Sin contactar", "contactado": "Contactado",
    "reunion_agendada": "Reunión agendada", "reunion_hecha": "Reunión hecha",
    "piloto": "Piloto", "cerrado": "Cerrado", "descartado": "Descartado",
}
# Lo que probablemente termina en cierre desde cada etapa. Es la ponderación
# del pipeline: una estimación explícita, no un dato. Se ajusta acá.
PROBABILIDAD = {"contactado": 0.05, "reunion_agendada": 0.2,
                "reunion_hecha": 0.45, "piloto": 0.7}

ZONAS = ["Municipio CH", "Carrasco"]
# Lo que recorre `main.py scrape-fidelidad`, barrio por barrio: Google Maps
# devuelve mejor "restaurantes en Pocitos" que "restaurantes en Municipio CH".
BARRIOS = {
    "Municipio CH": ["Pocitos", "Punta Carretas", "Villa Biarritz", "Parque Batlle",
                     "Villa Dolores", "Tres Cruces", "La Blanqueada", "Buceo"],
    "Carrasco": ["Carrasco", "Carrasco Norte", "Barra de Carrasco"],
}
# Para saber si una ficha de Maps es un lugar de comida. La búsqueda ya pide
# restaurantes, pero Maps mete farmacias y supermercados del barrio.
_COMIDA = ("restaur", "parrill", "pizz", "sushi", "hambur", "burger", "cafe", "bar", "comida",
           "cocina", "bistro", "brunch", "panader", "helad", "pasta", "chivit", "tapas", "marisc",
           "vegan", "asador", "cervec", "empanad", "rotiser", "delivery", "confiter", "almuerz")


def es_de_comida(categoria_maps: str | None) -> bool:
    t = normalizar(categoria_maps)
    return not t or any(c in t for c in _COMIDA)


def prospecto_desde_maps(data: dict, barrio: str) -> dict:
    """La ficha que junta el scraper, en el formato de `crear_prospecto`."""
    return {"nombre": data.get("name"), "barrio": barrio, "zona": normalizar_zona(None, barrio),
            "tipo": data.get("category"), "direccion": data.get("address"), "telefono": data.get("phone"),
            "rating": data.get("rating"), "resenas": data.get("review_count"), "maps_url": data.get("maps_url"),
            "notas": f"Traído de Google Maps buscando en {barrio}: verificá el barrio."}
DIAS_PILOTO = 30
DIAS_REACTIVAR = 90

CONFIG_DEFAULT = {"precio_usd": 150, "meta_llamadas_dia": 25,
                  "meta_reuniones_semana": 8, "meta_cierres_mes": 5,
                  "meta_mrr": 1500, "comision_pct": 0}

MOTIVOS = ["Precio", "Ya tiene sistema", "No ve el valor", "Lo decide otro",
           "Cierra el local", "Otro"]

# Qué puede pasar según en qué etapa está el prospecto. `pide` es lo que el
# formulario exige antes de guardar: 'fecha' (próxima llamada), 'reunion'
# (fecha de la reunión) o 'motivo'. `estado` es la etapa a la que pasa; None la
# deja como está. La próxima llamada la calcula `proxima_llamada`.
RESULTADOS = {
    "llamada": [
        {"id": "no_atendio", "label": "No atendió", "desc": "Vuelve a tu cola mañana, a otra hora"},
        {"id": "llamar_despues", "label": "Pidió que llame", "desc": "Elegís día y hora", "pide": "fecha", "efectivo": True},
        {"id": "info_whatsapp", "label": "Mandar info por WhatsApp", "desc": "Te lo recuerda en 2 días", "efectivo": True},
        {"id": "reunion", "label": "Agendar reunión ✓", "desc": "Pasa a «Reunión agendada»", "pide": "reunion", "estado": "reunion_agendada", "efectivo": True},
        {"id": "no_es_dueno", "label": "No es el dueño", "desc": "Anotá con quién hablar · mañana de nuevo"},
        {"id": "no_interesa", "label": "No le interesa", "desc": "Elegís el motivo · vuelve en 90 días", "pide": "motivo", "estado": "descartado", "efectivo": True},
    ],
    "reunion": [
        {"id": "piloto", "label": "Se hizo · arranca piloto ✓", "desc": f"Piloto de {DIAS_PILOTO} días · seguimiento en 2 semanas", "estado": "piloto", "efectivo": True},
        {"id": "lo_piensa", "label": "Se hizo · lo piensa", "desc": "Elegís cuándo volver a llamar", "pide": "fecha", "estado": "reunion_hecha", "efectivo": True},
        {"id": "reprogramar", "label": "No se hizo · reprogramar", "desc": "Elegís la nueva fecha", "pide": "reunion", "estado": "reunion_agendada"},
        {"id": "no_interesa", "label": "No le interesa", "desc": "Elegís el motivo", "pide": "motivo", "estado": "descartado", "efectivo": True},
    ],
    "piloto": [
        {"id": "cerro", "label": "Cerró ✓", "desc": "Pasa a cliente, suma al MRR", "estado": "cerrado", "efectivo": True},
        {"id": "seguimiento", "label": "Seguimiento del piloto", "desc": "Elegís cuándo volver a llamar", "pide": "fecha", "efectivo": True},
        {"id": "no_atendio", "label": "No atendió", "desc": "Vuelve mañana, a otra hora"},
        {"id": "no_sigue", "label": "No sigue", "desc": "Elegís el motivo", "pide": "motivo", "estado": "descartado", "efectivo": True},
    ],
}
# Un contacto "efectivo" es una llamada en la que se habló con quien decide.
# Es la tasa que importa: "no atendió" y "no es el dueño" no avanzan la venta.
EFECTIVOS = {r["id"] for grupo in RESULTADOS.values() for r in grupo if r.get("efectivo")}


def grupo_de_resultados(estado: str) -> str:
    if estado in ("reunion_agendada",):
        return "reunion"
    if estado == "piloto":
        return "piloto"
    return "llamada"


def resultado(estado: str, rid: str) -> dict | None:
    return next((r for r in RESULTADOS[grupo_de_resultados(estado)] if r["id"] == rid), None)


# ── fechas ───────────────────────────────────────────────────────────────────

FMT = "%Y-%m-%d %H:%M"


def ahora() -> datetime:
    """Hora de Montevideo sin tz: es lo que se guarda y se compara."""
    return ahora_mvd().replace(tzinfo=None, second=0, microsecond=0)


def fmt(dt: datetime | None) -> str | None:
    return dt.strftime(FMT) if dt else None


def parse_dt(texto) -> datetime | None:
    if not texto:
        return None
    texto = str(texto).strip().replace("T", " ")
    for largo, f in ((16, FMT), (10, "%Y-%m-%d")):
        try:
            return datetime.strptime(texto[:largo], f)
        except ValueError:
            continue
    return None


def _habil(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _sumar_habiles(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def proxima_llamada(rid: str, cuando: datetime, fecha_elegida: datetime | None = None) -> datetime | None:
    """Cuándo vuelve el prospecto a la cola después de un resultado.

    - No atendió / no es el dueño: el día hábil siguiente, en la otra mitad del
      día. Si nadie atiende a las 11, a las 11 tampoco va a atender mañana.
    - Info por WhatsApp: dos días hábiles, para darle tiempo a leerlo.
    - Piloto: a las dos semanas, a mitad de la prueba.
    - No le interesa: a los 90 días, por si cambió algo.
    - Las que piden fecha usan la elegida. Reunión y cierre no dejan llamada.
    """
    if rid in ("no_atendio", "no_es_dueno"):
        hora = 16 if cuando.hour < 14 else 11
        return datetime.combine(_sumar_habiles(cuando.date(), 1), datetime.min.time()).replace(hour=hora)
    if rid == "info_whatsapp":
        return datetime.combine(_sumar_habiles(cuando.date(), 2), datetime.min.time()).replace(hour=max(10, min(cuando.hour, 18)))
    if rid == "piloto":
        return datetime.combine(_habil(cuando.date() + timedelta(days=14)), datetime.min.time()).replace(hour=15)
    if rid in ("no_interesa", "no_sigue"):
        return datetime.combine(_habil(cuando.date() + timedelta(days=DIAS_REACTIVAR)), datetime.min.time()).replace(hour=15)
    if rid in ("llamar_despues", "lo_piensa", "seguimiento"):
        return fecha_elegida
    return None


# ── texto ────────────────────────────────────────────────────────────────────

def normalizar(texto: str | None) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def digitos(tel: str | None) -> str:
    d = re.sub(r"\D", "", tel or "")
    return d[-8:] if len(d) >= 8 else d


def normalizar_zona(zona: str | None, barrio: str | None = None) -> str | None:
    """Las dos zonas del socio. Buceo (límite CH/E) va con CH; Barra de Carrasco
    (Canelones) con Carrasco. Cualquier otra cosa devuelve None: no es territorio."""
    z = normalizar(zona) + " " + normalizar(barrio)
    if "carrasco" in z:
        return "Carrasco"
    if "municipio ch" in z or any(b in z for b in (
            "pocitos", "punta carretas", "parque batlle", "villa dolores", "tres cruces",
            "blanqueada", "buceo", "villa biarritz", "parque rodo")):
        return "Municipio CH"
    return None


# Categorías para agrupar: el tipo viene libre ("Parrilla / pastas", "Sushi
# (take away)") y agrupado por texto exacto cada fila es su propio grupo.
_CATEGORIAS = [("Pizza", ("pizz",)), ("Sushi", ("sushi",)), ("Hamburguesas", ("burger", "hamburg")),
               ("Parrilla", ("parrill", "asado")), ("Café", ("cafe", "brunch", "merienda", "panad")),
               ("Bar", ("bar", "chivit", "cervec", "tapas")), ("Heladería", ("helad",))]


def categoria(tipo: str | None) -> str:
    t = normalizar(tipo)
    for nombre, claves in _CATEGORIAS:
        if any(c in t for c in claves):
            return nombre
    return "Restaurante"


# ── base ─────────────────────────────────────────────────────────────────────

def _conn(db: str) -> sqlite3.Connection:
    c = sqlite3.connect(db, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_fidelidad(conn: sqlite3.Connection) -> None:
    """Tablas de Fidelidad. Lo llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fid_prospectos (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre           TEXT NOT NULL,
            zona             TEXT,
            barrio           TEXT,
            tipo             TEXT,
            direccion        TEXT,
            telefono         TEXT,
            rating           REAL,
            resenas          INTEGER,
            maps_url         TEXT,
            notas            TEXT,
            facilidad        TEXT,
            contacto         TEXT,
            estado           TEXT NOT NULL DEFAULT 'sin_contactar',
            proxima_llamada  TEXT,
            fecha_reunion    TEXT,
            proximo_paso     TEXT,
            motivo_descarte  TEXT,
            mensual_usd      REAL,
            piloto_inicio    TEXT,
            cerrado_en       TEXT,
            primera_llamada  TEXT,
            fuente           TEXT,
            archivado        INTEGER NOT NULL DEFAULT 0,
            creado_en        TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fid_llamadas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            prospecto_id  INTEGER NOT NULL REFERENCES fid_prospectos(id) ON DELETE CASCADE,
            hecha_en      TEXT NOT NULL,
            resultado     TEXT NOT NULL,
            efectivo      INTEGER NOT NULL DEFAULT 0,
            nota          TEXT,
            proxima       TEXT,
            usuario       TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fid_cambios (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            prospecto_id  INTEGER NOT NULL REFERENCES fid_prospectos(id) ON DELETE CASCADE,
            de            TEXT,
            a             TEXT NOT NULL,
            en            TEXT NOT NULL,
            usuario       TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fid_config (
            clave  TEXT PRIMARY KEY,
            valor  TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fid_llamadas_p ON fid_llamadas(prospecto_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fid_llamadas_en ON fid_llamadas(hecha_en)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fid_cambios_p ON fid_cambios(prospecto_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fid_prospectos_prox ON fid_prospectos(proxima_llamada)")
    # La agenda del vendedor (24/9): lo que agenda y no es una reunión con un
    # restaurante (una visita, un recordatorio). Las reuniones siguen viviendo
    # en fid_prospectos.fecha_reunion: es lo que mueve la etapa.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fid_eventos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo        TEXT NOT NULL,
            inicio        TEXT NOT NULL,
            fin           TEXT NOT NULL,
            lugar         TEXT,
            notas         TEXT,
            prospecto_id  INTEGER REFERENCES fid_prospectos(id) ON DELETE SET NULL,
            usuario       TEXT,
            creado_en     TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fid_eventos_inicio ON fid_eventos(inicio)")
    for col, tipo in (("reunion_minutos", "INTEGER"), ("reunion_lugar", "TEXT")):
        try:
            conn.execute(f"ALTER TABLE fid_prospectos ADD COLUMN {col} {tipo}")
        except sqlite3.OperationalError:
            pass  # ya existe
    # El rol del socio. Solo ve estas dos pantallas; el candado real está en
    # `solo_fidelidad` (dashboard.require_login), no en el menú.
    conn.execute("INSERT OR IGNORE INTO roles (name, panel_access) VALUES (?, ?)",
                 (ROL_VENDEDOR, json.dumps(["cola", "metrics"])))


def get_config(db: str) -> dict:
    c = _conn(db)
    try:
        filas = dict(c.execute("SELECT clave, valor FROM fid_config").fetchall())
    finally:
        c.close()
    out = dict(CONFIG_DEFAULT)
    for k, v in filas.items():
        if k in out:
            try:
                out[k] = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError):
                pass
    return out


def set_config(db: str, cambios: dict) -> dict:
    limpios = {}
    for k, v in (cambios or {}).items():
        if k not in CONFIG_DEFAULT:
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        if n < 0 or (k == "comision_pct" and n > 100):
            continue
        limpios[k] = n
    c = _conn(db)
    try:
        for k, v in limpios.items():
            c.execute("INSERT INTO fid_config (clave, valor) VALUES (?, ?) "
                      "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                      (k, str(int(v)) if float(v).is_integer() else str(v)))
        c.commit()
    finally:
        c.close()
    return get_config(db)


CAMPOS_EDITABLES = ("nombre", "zona", "barrio", "tipo", "direccion", "telefono", "rating",
                    "resenas", "maps_url", "notas", "facilidad", "contacto", "proximo_paso",
                    "mensual_usd")


def _limpiar_campos(datos: dict) -> dict:
    out = {}
    for k in CAMPOS_EDITABLES:
        if k not in datos:
            continue
        v = datos[k]
        if k == "rating":
            try:
                v = round(float(str(v).replace(",", ".")), 1) if v not in (None, "") else None
            except ValueError:
                v = None
        elif k in ("resenas",):
            try:
                v = int(float(str(v).replace(".", "").replace(",", ""))) if v not in (None, "") else None
            except ValueError:
                v = None
        elif k == "mensual_usd":
            try:
                v = float(v) if v not in (None, "") else None
            except ValueError:
                v = None
        elif k == "facilidad":
            v = v if v in ("Alta", "Media", "Baja") else None
        else:
            v = (str(v).strip() or None) if v is not None else None
            if k == "telefono" and v in ("—", "-"):
                v = None
        out[k] = v
    return out


def buscar_duplicado(c: sqlite3.Connection, nombre: str, telefono: str | None, maps_url: str | None) -> int | None:
    """El mismo restaurante puede llegar del Excel, del scraper y a mano. Se
    reconoce por el link de Maps, por el teléfono o por el nombre."""
    if maps_url:
        f = c.execute("SELECT id FROM fid_prospectos WHERE maps_url = ?", (maps_url,)).fetchone()
        if f:
            return f[0]
    tel = digitos(telefono)
    nom = normalizar(nombre)
    for fila in c.execute("SELECT id, nombre, telefono FROM fid_prospectos"):
        if tel and len(tel) >= 7 and digitos(fila["telefono"]) == tel:
            return fila["id"]
        if nom and normalizar(fila["nombre"]) == nom:
            return fila["id"]
    return None


def crear_prospecto(db: str, datos: dict, fuente: str = "manual") -> tuple[int | None, str]:
    """Devuelve (id, 'creado'|'duplicado'|'fuera_de_zona'|'sin_nombre')."""
    campos = _limpiar_campos(datos)
    if not campos.get("nombre"):
        return None, "sin_nombre"
    zona = normalizar_zona(campos.get("zona"), campos.get("barrio"))
    archivado = 0
    if not zona:
        # Fuera de CH y Carrasco. No se descarta: queda guardado y oculto, igual
        # que los prospectos viejos, por si un día se abre otra zona.
        if not datos.get("guardar_fuera_de_zona"):
            return None, "fuera_de_zona"
        archivado = 1
    campos["zona"] = zona or campos.get("zona")
    estado = datos.get("estado") if datos.get("estado") in ESTADOS else "sin_contactar"
    c = _conn(db)
    try:
        dup = buscar_duplicado(c, campos["nombre"], campos.get("telefono"), campos.get("maps_url"))
        if dup:
            return dup, "duplicado"
        campos.update({"estado": estado, "fuente": fuente, "archivado": archivado,
                       "creado_en": fmt(ahora())})
        cols = ", ".join(campos)
        cur = c.execute(f"INSERT INTO fid_prospectos ({cols}) VALUES ({', '.join('?' * len(campos))})",
                        list(campos.values()))
        c.commit()
        return cur.lastrowid, "creado"
    finally:
        c.close()


def editar_prospecto(db: str, pid: int, datos: dict) -> bool:
    campos = _limpiar_campos(datos)
    if "nombre" in campos and not campos["nombre"]:
        campos.pop("nombre")
    if not campos:
        return False
    c = _conn(db)
    try:
        cur = c.execute(f"UPDATE fid_prospectos SET {', '.join(k + ' = ?' for k in campos)} WHERE id = ?",
                        list(campos.values()) + [pid])
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def get_prospecto(db: str, pid: int) -> dict | None:
    c = _conn(db)
    try:
        f = c.execute("SELECT * FROM fid_prospectos WHERE id = ?", (pid,)).fetchone()
        if not f:
            return None
        p = dict(f)
        p["llamadas"] = [dict(x) for x in c.execute(
            "SELECT * FROM fid_llamadas WHERE prospecto_id = ? ORDER BY hecha_en DESC, id DESC", (pid,))]
        p["cambios"] = [dict(x) for x in c.execute(
            "SELECT * FROM fid_cambios WHERE prospecto_id = ? ORDER BY en DESC, id DESC", (pid,))]
        return p
    finally:
        c.close()


def _cambiar_estado(c: sqlite3.Connection, p: dict, nuevo: str, cuando: datetime, usuario: str) -> dict:
    """Los efectos de entrar a cada etapa. Devuelve las columnas a actualizar."""
    if nuevo == p["estado"]:
        return {}
    c.execute("INSERT INTO fid_cambios (prospecto_id, de, a, en, usuario) VALUES (?,?,?,?,?)",
              (p["id"], p["estado"], nuevo, fmt(cuando), usuario))
    cambios = {"estado": nuevo}
    if nuevo == "piloto" and not p.get("piloto_inicio"):
        cambios["piloto_inicio"] = cuando.strftime("%Y-%m-%d")
    if nuevo == "cerrado":
        cambios["cerrado_en"] = cuando.strftime("%Y-%m-%d")
        cambios["proxima_llamada"] = None
    elif p.get("cerrado_en"):
        # Si lo sacan de Cerrado, deja de sumar al MRR desde ya.
        cambios["cerrado_en"] = None
    if nuevo != "descartado" and p.get("motivo_descarte"):
        cambios["motivo_descarte"] = None
    return cambios


def registrar_llamada(db: str, pid: int, rid: str, usuario: str, nota: str = "",
                      fecha: str | None = None, fecha_reunion: str | None = None,
                      motivo: str | None = None, cuando: datetime | None = None) -> tuple[dict | None, str | None]:
    """Guarda la llamada y deja agendada la siguiente. Devuelve (prospecto, error)."""
    cuando = cuando or ahora()
    c = _conn(db)
    try:
        f = c.execute("SELECT * FROM fid_prospectos WHERE id = ?", (pid,)).fetchone()
        if not f:
            return None, "el prospecto no existe"
        p = dict(f)
        r = resultado(p["estado"], rid)
        if not r:
            return None, "ese resultado no corresponde a esta etapa"
        pide = r.get("pide")
        fecha_dt = parse_dt(fecha)
        reunion_dt = parse_dt(fecha_reunion)
        if pide == "fecha" and not fecha_dt:
            return None, "elegí cuándo volver a llamar"
        if pide == "fecha" and fecha_dt < cuando - timedelta(minutes=5):
            return None, "la próxima llamada no puede quedar en el pasado"
        if pide == "reunion" and not reunion_dt:
            return None, "elegí la fecha y hora de la reunión"
        if pide == "motivo" and not (motivo or "").strip():
            return None, "elegí el motivo"
        prox = proxima_llamada(rid, cuando, fecha_dt)
        cambios = {"proxima_llamada": fmt(prox)}
        if not p.get("primera_llamada"):
            cambios["primera_llamada"] = fmt(cuando)
        nuevo = r.get("estado") or ("contactado" if p["estado"] == "sin_contactar" else p["estado"])
        if p["estado"] == "descartado" and not r.get("estado"):
            nuevo = "contactado"  # reactivado a los 90 días y volvió a hablar
        cambios.update(_cambiar_estado(c, p, nuevo, cuando, usuario))
        if pide == "reunion":
            cambios["fecha_reunion"] = fmt(reunion_dt)
        if pide == "motivo":
            cambios["motivo_descarte"] = motivo.strip()[:80]
        if nuevo == "cerrado":
            cambios["proxima_llamada"] = None
        c.execute("INSERT INTO fid_llamadas (prospecto_id, hecha_en, resultado, efectivo, nota, proxima, usuario) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (pid, fmt(cuando), rid, 1 if rid in EFECTIVOS else 0, (nota or "").strip()[:2000] or None,
                   cambios.get("proxima_llamada") or cambios.get("fecha_reunion"), usuario))
        if nuevo == "cerrado" and not p.get("mensual_usd"):
            cambios["mensual_usd"] = get_config_conn(c)["precio_usd"]
        c.execute(f"UPDATE fid_prospectos SET {', '.join(k + ' = ?' for k in cambios)} WHERE id = ?",
                  list(cambios.values()) + [pid])
        c.commit()
    finally:
        c.close()
    return get_prospecto(db, pid), None


def get_config_conn(c: sqlite3.Connection) -> dict:
    out = dict(CONFIG_DEFAULT)
    for k, v in c.execute("SELECT clave, valor FROM fid_config").fetchall():
        if k in out:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                pass
    return out


def mover_estado(db: str, pid: int, nuevo: str, usuario: str, fecha_reunion: str | None = None,
                 motivo: str | None = None) -> tuple[dict | None, str | None]:
    """El arrastre del tablero: cambia la etapa sin registrar una llamada."""
    if nuevo not in ESTADOS:
        return None, "etapa inválida"
    cuando = ahora()
    c = _conn(db)
    try:
        f = c.execute("SELECT * FROM fid_prospectos WHERE id = ?", (pid,)).fetchone()
        if not f:
            return None, "el prospecto no existe"
        p = dict(f)
        cambios = _cambiar_estado(c, p, nuevo, cuando, usuario)
        if reunion_dt := parse_dt(fecha_reunion):
            cambios["fecha_reunion"] = fmt(reunion_dt)
        if nuevo == "descartado":
            cambios["motivo_descarte"] = (motivo or "").strip()[:80] or p.get("motivo_descarte") or "Otro"
            cambios["proxima_llamada"] = fmt(proxima_llamada("no_interesa", cuando))
        if nuevo == "cerrado" and not p.get("mensual_usd"):
            cambios["mensual_usd"] = get_config_conn(c)["precio_usd"]
        if cambios:
            c.execute(f"UPDATE fid_prospectos SET {', '.join(k + ' = ?' for k in cambios)} WHERE id = ?",
                      list(cambios.values()) + [pid])
            c.commit()
    finally:
        c.close()
    return get_prospecto(db, pid), None


# ── puntaje y cola ───────────────────────────────────────────────────────────

def puntaje(p: dict, categorias_con_cierre: set | None = None) -> int:
    """0 a 100: a quién conviene llamar primero entre los que nunca se llamaron.

    Pesa lo que hace a un buen cliente de un sistema de puntos: clientela
    grande (reseñas), contenta (rating), que se pueda llamar (teléfono) y lo que
    el vendedor sabe (facilidad). Suma si es del tipo de local que ya cerró.
    """
    s = 0.0
    res = p.get("resenas") or 0
    s += min(35.0, 9.5 * math.log10(res + 1))
    rt = p.get("rating") or 0
    s += 25 if rt >= 4.5 else 18 if rt >= 4.2 else 12 if rt >= 4.0 else 5 if rt >= 3.5 else 0
    s += 15 if digitos(p.get("telefono")) else -40
    s += {"Alta": 20, "Media": 10}.get(p.get("facilidad") or "", 0)
    if categorias_con_cierre and categoria(p.get("tipo")) in categorias_con_cierre:
        s += 10
    return int(max(0, min(100, round(s))))


_COLS_LISTA = ("id, nombre, zona, barrio, tipo, telefono, rating, resenas, facilidad, estado, "
               "proxima_llamada, fecha_reunion, piloto_inicio, cerrado_en, motivo_descarte, "
               "mensual_usd, contacto, maps_url")


def _ultimo_resultado(c: sqlite3.Connection) -> dict:
    """Por prospecto: el último resultado y cuántos 'no atendió' seguidos lleva."""
    out = {}
    for f in c.execute("SELECT prospecto_id, resultado FROM fid_llamadas ORDER BY hecha_en, id"):
        pid, r = f[0], f[1]
        prev = out.get(pid, {"n_no_atendio": 0})
        out[pid] = {"ultimo": r, "n_no_atendio": prev["n_no_atendio"] + 1 if r == "no_atendio" else 0}
    return out


def _categorias_con_cierre(c: sqlite3.Connection) -> set:
    return {categoria(f[0]) for f in c.execute("SELECT tipo FROM fid_prospectos WHERE estado = 'cerrado'")}


def armar_hoy(db: str, cuando: datetime | None = None) -> dict:
    """La pantalla 'Mi día': la cola ordenada y los números del día."""
    cuando = cuando or ahora()
    hoy = cuando.strftime("%Y-%m-%d")
    cfg = get_config(db)
    c = _conn(db)
    try:
        ult = _ultimo_resultado(c)
        conc = _categorias_con_cierre(c)
        pend = [dict(f) for f in c.execute(
            f"SELECT {_COLS_LISTA} FROM fid_prospectos WHERE archivado = 0 "
            "AND estado != 'cerrado' AND proxima_llamada IS NOT NULL AND substr(proxima_llamada, 1, 10) <= ? "
            "ORDER BY proxima_llamada", (hoy,))]
        # Reuniones que ya pasaron y nadie cargó cómo salieron: vuelven a la cola.
        post = [dict(f) for f in c.execute(
            f"SELECT {_COLS_LISTA} FROM fid_prospectos WHERE archivado = 0 AND estado = 'reunion_agendada' "
            "AND fecha_reunion IS NOT NULL AND fecha_reunion <= ? ORDER BY fecha_reunion", (fmt(cuando),))]
        reuniones_hoy = [dict(f) for f in c.execute(
            f"SELECT {_COLS_LISTA} FROM fid_prospectos WHERE archivado = 0 AND estado = 'reunion_agendada' "
            "AND substr(fecha_reunion, 1, 10) = ? ORDER BY fecha_reunion", (hoy,))]
        nuevos = [dict(f) for f in c.execute(
            f"SELECT {_COLS_LISTA} FROM fid_prospectos WHERE archivado = 0 AND estado = 'sin_contactar' "
            "AND proxima_llamada IS NULL")]
        llam_hoy = c.execute("SELECT COUNT(*), COALESCE(SUM(efectivo), 0) FROM fid_llamadas "
                             "WHERE substr(hecha_en, 1, 10) = ?", (hoy,)).fetchone()
        lunes = (cuando.date() - timedelta(days=cuando.weekday())).strftime("%Y-%m-%d")
        reu_hoy = c.execute("SELECT COUNT(DISTINCT prospecto_id) FROM fid_cambios WHERE a = 'reunion_agendada' "
                            "AND substr(en, 1, 10) = ?", (hoy,)).fetchone()[0]
        reu_sem = c.execute("SELECT COUNT(DISTINCT prospecto_id) FROM fid_cambios WHERE a = 'reunion_agendada' "
                            "AND substr(en, 1, 10) >= ?", (lunes,)).fetchone()[0]
        efect_hist = c.execute("SELECT COUNT(*), COALESCE(SUM(efectivo), 0) FROM fid_llamadas "
                               "WHERE substr(hecha_en, 1, 10) < ?", (hoy,)).fetchone()
    finally:
        c.close()

    def _item(p, motivo=None):
        u = ult.get(p["id"], {})
        p = dict(p)
        p["ultimo_resultado"] = u.get("ultimo")
        p["n_no_atendio"] = u.get("n_no_atendio", 0)
        p["categoria"] = categoria(p.get("tipo"))
        if motivo:
            p["motivo_cola"] = motivo
        return p

    ids_post = {p["id"] for p in post}
    vencidas = [_item(p) for p in pend if p["proxima_llamada"][:10] < hoy and p["id"] not in ids_post]
    para_hoy = [_item(p) for p in pend if p["proxima_llamada"][:10] == hoy and p["id"] not in ids_post]
    post = [_item(p, "post_reunion") for p in post]
    for p in nuevos:
        p["puntaje"] = puntaje(p, conc)
        p["parecido"] = bool(conc and categoria(p.get("tipo")) in conc)
    nuevos.sort(key=lambda p: (-p["puntaje"], -(p.get("resenas") or 0)))
    hueco = max(5, int(cfg["meta_llamadas_dia"]) - llam_hoy[0] - len(vencidas) - len(para_hoy) - len(post))
    sugeridos = [_item(p) for p in nuevos[:hueco]]
    tasa_hoy = round(100 * llam_hoy[1] / llam_hoy[0]) if llam_hoy[0] else None
    tasa_hist = round(100 * efect_hist[1] / efect_hist[0]) if efect_hist[0] else None
    return {
        "hoy": hoy,
        "kpis": {
            "llamadas_hoy": llam_hoy[0], "meta_llamadas_dia": int(cfg["meta_llamadas_dia"]),
            "efectivos_hoy": llam_hoy[1], "tasa_efectivo_hoy": tasa_hoy, "tasa_efectivo_hist": tasa_hist,
            "reuniones_hoy": reu_hoy, "reuniones_semana": reu_sem,
            "meta_reuniones_semana": int(cfg["meta_reuniones_semana"]),
            "vencidas": len(vencidas), "para_hoy": len(para_hoy) + len(post),
            "sin_contactar": len(nuevos),
        },
        "grupos": {"vencidas": vencidas, "post_reunion": post, "hoy": para_hoy, "sugeridos": sugeridos},
        "reuniones_hoy": reuniones_hoy,
    }


def listar(db: str, estado: str | None = None, zona: str | None = None, cat: str | None = None,
           q: str | None = None, pagina: int = 1, por_pagina: int = 50) -> dict:
    cond, params = ["archivado = 0"], []
    if estado in ESTADOS:
        cond.append("estado = ?")
        params.append(estado)
    if zona in ZONAS:
        cond.append("zona = ?")
        params.append(zona)
    c = _conn(db)
    try:
        filas = [dict(f) for f in c.execute(
            f"SELECT {_COLS_LISTA}, notas FROM fid_prospectos WHERE {' AND '.join(cond)}", params)]
        conc = _categorias_con_cierre(c)
    finally:
        c.close()
    if cat:
        filas = [f for f in filas if categoria(f.get("tipo")) == cat]
    if q:
        nq = normalizar(q)
        filas = [f for f in filas if nq in normalizar(f"{f['nombre']} {f.get('barrio')} {f.get('tipo')} {f.get('contacto')}")]
    for f in filas:
        f["puntaje"] = puntaje(f, conc)
        f["categoria"] = categoria(f.get("tipo"))
    filas.sort(key=lambda f: (ESTADOS.index(f["estado"]) if f["estado"] in ESTADOS else 9, -f["puntaje"]))
    total = len(filas)
    paginas = max(1, math.ceil(total / por_pagina))
    pagina = min(max(1, pagina), paginas)
    return {"items": filas[(pagina - 1) * por_pagina: pagina * por_pagina],
            "total": total, "pagina": pagina, "paginas": paginas}


def armar_pipeline(db: str, zona: str | None = None, cat: str | None = None,
                   por_columna: int = 40) -> dict:
    todos = listar(db, zona=zona, cat=cat, por_pagina=100000)["items"]
    cfg = get_config(db)
    cols = []
    for e in ESTADOS:
        items = [p for p in todos if p["estado"] == e]
        if e == "sin_contactar":
            items.sort(key=lambda p: -p["puntaje"])
        else:
            items.sort(key=lambda p: (p.get("proxima_llamada") or p.get("fecha_reunion") or "9999"))
        potencial = sum((p.get("mensual_usd") or cfg["precio_usd"]) for p in items)
        cols.append({"estado": e, "label": ESTADO_LABEL[e], "total": len(items),
                     "potencial_usd": potencial, "probabilidad": PROBABILIDAD.get(e),
                     "items": items[:por_columna]})
    return {"columnas": cols, "precio_usd": cfg["precio_usd"]}


# ── agenda ───────────────────────────────────────────────────────────────────
# El calendario del vendedor. Muestra SOLO lo de Fidelidad: sus reuniones, sus
# llamadas agendadas y sus eventos. El calendario de la agencia (Google, las
# reuniones de Scalerics) no pasa por acá y el vendedor no puede leerlo
# (dashboard.require_login le corta /api/calendar).

REUNION_MINUTOS = 45
LLAMADA_MINUTOS = 15


def _validar_rango(inicio, fin=None, minutos=None):
    a = parse_dt(inicio)
    if not a or len(str(inicio).strip()) < 16:
        return None, None, "falta el día y la hora"
    try:
        b = parse_dt(fin) if fin else (a + timedelta(minutes=int(minutos or 60)))
    except (TypeError, ValueError):
        return None, None, "la duración no es un número"
    if not b or b <= a:
        return None, None, "termina antes de empezar"
    if b - a > timedelta(hours=12):
        return None, None, "dura más de 12 horas"
    return a, b, None


def _texto(v, largo):
    return (str(v).strip()[:largo] or None) if v is not None else None


def crear_evento(db: str, datos: dict, usuario: str) -> tuple[int | None, str | None]:
    titulo = _texto(datos.get("titulo"), 120)
    if not titulo:
        return None, "falta el título"
    a, b, err = _validar_rango(datos.get("inicio"), datos.get("fin"), datos.get("minutos"))
    if err:
        return None, err
    pid = datos.get("prospecto_id") or None
    c = _conn(db)
    try:
        if pid and not c.execute("SELECT 1 FROM fid_prospectos WHERE id = ?", (pid,)).fetchone():
            return None, "ese restaurante no existe"
        cur = c.execute("INSERT INTO fid_eventos (titulo, inicio, fin, lugar, notas, prospecto_id, usuario, creado_en) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (titulo, fmt(a), fmt(b), _texto(datos.get("lugar"), 200), _texto(datos.get("notas"), 2000),
                         pid, usuario, fmt(ahora())))
        c.commit()
        return cur.lastrowid, None
    finally:
        c.close()


def editar_evento(db: str, eid: int, datos: dict) -> str | None:
    c = _conn(db)
    try:
        f = c.execute("SELECT * FROM fid_eventos WHERE id = ?", (eid,)).fetchone()
        if not f:
            return "el evento no existe"
        e = dict(f)
        titulo = _texto(datos.get("titulo", e["titulo"]), 120)
        if not titulo:
            return "falta el título"
        duracion = int((parse_dt(e["fin"]) - parse_dt(e["inicio"])).total_seconds() // 60)
        a, b, err = _validar_rango(datos.get("inicio", e["inicio"]), datos.get("fin"),
                                   datos.get("minutos") or duracion)
        if err:
            return err
        c.execute("UPDATE fid_eventos SET titulo=?, inicio=?, fin=?, lugar=?, notas=? WHERE id=?",
                  (titulo, fmt(a), fmt(b), _texto(datos.get("lugar", e["lugar"]), 200),
                   _texto(datos.get("notas", e["notas"]), 2000), eid))
        c.commit()
        return None
    finally:
        c.close()


def borrar_evento(db: str, eid: int) -> bool:
    c = _conn(db)
    try:
        cur = c.execute("DELETE FROM fid_eventos WHERE id = ?", (eid,))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def agendar_reunion(db: str, pid: int, inicio: str, usuario: str, minutos=None,
                    lugar=None) -> tuple[dict | None, str | None]:
    """Agendar o mover la reunión de un restaurante desde la agenda. Si todavía
    no estaba en 'Reunión agendada', pasa a esa etapa (y queda en el historial)."""
    a, b, err = _validar_rango(inicio, None, minutos or REUNION_MINUTOS)
    if err:
        return None, err
    p = get_prospecto(db, pid)
    if not p:
        return None, "el prospecto no existe"
    if p["estado"] == "cerrado":
        return None, "ese restaurante ya es cliente"
    p, err = mover_estado(db, pid, "reunion_agendada", usuario, fecha_reunion=fmt(a))
    if err:
        return None, err
    c = _conn(db)
    try:
        c.execute("UPDATE fid_prospectos SET reunion_minutos = ?, reunion_lugar = ?, proxima_llamada = NULL "
                  "WHERE id = ?", (int((b - a).total_seconds() // 60),
                                   _texto(lugar, 200) or p.get("reunion_lugar"), pid))
        c.commit()
    finally:
        c.close()
    return get_prospecto(db, pid), None


def armar_agenda(db: str, desde: str, hasta: str, con_llamadas: bool = True) -> dict:
    """Todo lo de Fidelidad entre dos fechas (inclusive), en un solo formato."""
    d0, d1 = (desde or "")[:10], (hasta or "")[:10]
    if not (parse_dt(d0) and parse_dt(d1)) or d1 < d0:
        return {"items": [], "error": "rango inválido"}
    if (parse_dt(d1) - parse_dt(d0)).days > 62:
        d1 = (parse_dt(d0) + timedelta(days=62)).strftime("%Y-%m-%d")
    fin_txt = d1 + " 23:59"
    items = []
    c = _conn(db)
    try:
        for f in c.execute("SELECT id, nombre, barrio, direccion, telefono, fecha_reunion, reunion_minutos, "
                           "reunion_lugar FROM fid_prospectos WHERE archivado = 0 AND estado = 'reunion_agendada' "
                           "AND fecha_reunion BETWEEN ? AND ?", (d0, fin_txt)):
            a = parse_dt(f["fecha_reunion"])
            items.append({"tipo": "reunion", "id": f"r{f['id']}", "prospecto_id": f["id"],
                          "titulo": f"Reunión · {f['nombre']}", "inicio": fmt(a),
                          "fin": fmt(a + timedelta(minutes=f["reunion_minutos"] or REUNION_MINUTOS)),
                          "lugar": f["reunion_lugar"] or f["direccion"], "barrio": f["barrio"],
                          "telefono": f["telefono"]})
        if con_llamadas:
            for f in c.execute("SELECT id, nombre, barrio, telefono, proxima_llamada, estado FROM fid_prospectos "
                               "WHERE archivado = 0 AND estado NOT IN ('cerrado', 'reunion_agendada') "
                               "AND proxima_llamada BETWEEN ? AND ?", (d0, fin_txt)):
                a = parse_dt(f["proxima_llamada"])
                items.append({"tipo": "llamada", "id": f"l{f['id']}", "prospecto_id": f["id"],
                              "titulo": f"Llamar · {f['nombre']}", "inicio": fmt(a),
                              "fin": fmt(a + timedelta(minutes=LLAMADA_MINUTOS)),
                              "barrio": f["barrio"], "telefono": f["telefono"],
                              "reactivar": f["estado"] == "descartado"})
        for f in c.execute("SELECT e.*, p.nombre AS prospecto FROM fid_eventos e "
                           "LEFT JOIN fid_prospectos p ON p.id = e.prospecto_id "
                           "WHERE e.inicio <= ? AND e.fin >= ?", (fin_txt, d0)):
            items.append({"tipo": "evento", "id": f"e{f['id']}", "evento_id": f["id"],
                          "prospecto_id": f["prospecto_id"], "prospecto": f["prospecto"],
                          "titulo": f["titulo"], "inicio": f["inicio"], "fin": f["fin"],
                          "lugar": f["lugar"], "notas": f["notas"], "usuario": f["usuario"]})
    finally:
        c.close()
    items.sort(key=lambda x: (x["inicio"], x["tipo"]))
    # Superposiciones entre reuniones y eventos (las llamadas son cortas y se
    # corren solas: no cuentan). Solo contra lo propio: el vendedor no ve la
    # agenda de la agencia.
    firmes = [x for x in items if x["tipo"] != "llamada"]
    for x in firmes:
        x["choca_con"] = [y["titulo"] for y in firmes
                          if y is not x and y["inicio"] < x["fin"] and x["inicio"] < y["fin"]]
    return {"desde": d0, "hasta": d1, "items": items}


def listar_reuniones(db: str, cuando: datetime | None = None) -> dict:
    cuando = cuando or ahora()
    c = _conn(db)
    try:
        filas = [dict(f) for f in c.execute(
            f"SELECT {_COLS_LISTA}, direccion FROM fid_prospectos WHERE archivado = 0 "
            "AND estado = 'reunion_agendada' AND fecha_reunion IS NOT NULL ORDER BY fecha_reunion")]
    finally:
        c.close()
    ahora_txt = fmt(cuando)
    return {"proximas": [f for f in filas if f["fecha_reunion"] > ahora_txt],
            "sin_resultado": [f for f in filas if f["fecha_reunion"] <= ahora_txt]}


# ── inteligencia comercial ───────────────────────────────────────────────────

def _rango(periodo: str, cuando: datetime) -> tuple[date, date]:
    hoy = cuando.date()
    if periodo == "semana":
        return hoy - timedelta(days=hoy.weekday()), hoy
    if periodo == "trimestre":
        return hoy - timedelta(days=90), hoy
    if periodo == "todo":
        return date(2000, 1, 1), hoy
    return hoy.replace(day=1), hoy


def _meses_atras(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def armar_intel(db: str, periodo: str = "mes", zona: str | None = None, cat: str | None = None,
                cuando: datetime | None = None) -> dict:
    cuando = cuando or ahora()
    desde, hasta = _rango(periodo, cuando)
    d0, d1 = desde.strftime("%Y-%m-%d"), hasta.strftime("%Y-%m-%d")
    cfg = get_config(db)
    precio = cfg["precio_usd"]
    c = _conn(db)
    try:
        prosp = {f["id"]: dict(f) for f in c.execute("SELECT * FROM fid_prospectos WHERE archivado = 0")}
        llamadas = [dict(f) for f in c.execute("SELECT * FROM fid_llamadas ORDER BY hecha_en")]
        cambios = [dict(f) for f in c.execute("SELECT * FROM fid_cambios ORDER BY en")]
    finally:
        c.close()

    def _en_filtro(pid):
        p = prosp.get(pid)
        return bool(p) and (not zona or p.get("zona") == zona) and (not cat or categoria(p.get("tipo")) == cat)

    prosp = {k: v for k, v in prosp.items() if _en_filtro(k)}
    llamadas = [l for l in llamadas if l["prospecto_id"] in prosp]
    cambios = [x for x in cambios if x["prospecto_id"] in prosp]
    en_rango = lambda t: d0 <= (t or "")[:10] <= d1  # noqa: E731
    ll_p = [l for l in llamadas if en_rango(l["hecha_en"])]

    # Etapa máxima a la que llegó cada prospecto, para el embudo.
    orden = {e: i for i, e in enumerate(ESTADOS[:6])}
    maxima = {}
    for pid, p in prosp.items():
        m = orden.get(p["estado"], 0)
        for x in cambios:
            if x["prospecto_id"] == pid and x["a"] in orden:
                m = max(m, orden[x["a"]])
        maxima[pid] = m
    llamados_ever = {l["prospecto_id"] for l in llamadas}
    efectivos_ever = {l["prospecto_id"] for l in llamadas if l["efectivo"]}
    tocados = {l["prospecto_id"] for l in ll_p} | {x["prospecto_id"] for x in cambios if en_rango(x["en"])}
    base = tocados if periodo != "todo" else set(prosp)
    etapas = [("Contactados", lambda pid: pid in llamados_ever or maxima[pid] >= 1),
              ("Habló con el dueño", lambda pid: pid in efectivos_ever or maxima[pid] >= 2),
              ("Reunión agendada", lambda pid: maxima[pid] >= 2),
              ("Reunión hecha", lambda pid: maxima[pid] >= 3),
              ("Piloto", lambda pid: maxima[pid] >= 4),
              ("Cerrado", lambda pid: maxima[pid] >= 5)]
    embudo, previo = [], None
    for nombre, cond in etapas:
        n = sum(1 for pid in base if cond(pid))
        embudo.append({"etapa": nombre, "n": n,
                       "pasa": round(100 * n / previo) if previo else None})
        previo = n
    peor = None
    for a, b in zip(embudo[1:], embudo[2:]):
        if a["n"] >= 3 and b["pasa"] is not None and (peor is None or b["pasa"] < peor[2]):
            peor = (a["etapa"], b["etapa"], b["pasa"])

    cerrados = [p for p in prosp.values() if p["estado"] == "cerrado"]
    mrr = sum(p.get("mensual_usd") or precio for p in cerrados)
    cerrados_periodo = [p for p in cerrados if en_rango(p.get("cerrado_en"))]
    inicio_mes = cuando.date().replace(day=1).strftime("%Y-%m-%d")
    mrr_mes = sum(p.get("mensual_usd") or precio for p in cerrados if (p.get("cerrado_en") or "") >= inicio_mes)
    ponderado = sum((p.get("mensual_usd") or precio) * PROBABILIDAD.get(p["estado"], 0)
                    for p in prosp.values())
    n_ll = len(ll_p)
    n_ef = sum(l["efectivo"] for l in ll_p)
    reuniones_p = {x["prospecto_id"] for x in cambios if x["a"] == "reunion_agendada" and en_rango(x["en"])}
    dias_habiles = sum(1 for i in range((hasta - desde).days + 1)
                       if (desde + timedelta(days=i)).weekday() < 5) or 1
    dias_con_llamadas = len({l["hecha_en"][:10] for l in ll_p}) or 1
    ciclos = []
    for p in cerrados:
        a, b = parse_dt(p.get("primera_llamada") or p.get("creado_en")), parse_dt(p.get("cerrado_en"))
        if a and b:
            ciclos.append(max(0, (b.date() - a.date()).days))

    # Semana a semana: las últimas 8, de lunes a domingo.
    lunes = cuando.date() - timedelta(days=cuando.weekday())
    semanas = []
    for i in range(7, -1, -1):
        l0 = lunes - timedelta(weeks=i)
        l1 = l0 + timedelta(days=6)
        r = lambda t: l0.strftime("%Y-%m-%d") <= (t or "")[:10] <= l1.strftime("%Y-%m-%d")  # noqa: E731
        semanas.append({"desde": l0.strftime("%Y-%m-%d"), "etiqueta": l0.strftime("%d/%m"),
                        "llamadas": sum(1 for l in llamadas if r(l["hecha_en"])),
                        "reuniones": len({x["prospecto_id"] for x in cambios if x["a"] == "reunion_agendada" and r(x["en"])}),
                        "cierres": len({x["prospecto_id"] for x in cambios if x["a"] == "cerrado" and r(x["en"])})})

    # MRR a fin de cada mes, los últimos 6, y la proyección al ritmo actual.
    serie_mrr = []
    for i in range(5, -1, -1):
        m0 = _meses_atras(cuando.date(), i)
        m1 = _meses_atras(cuando.date(), i - 1) if i else cuando.date() + timedelta(days=1)
        tope = m1.strftime("%Y-%m-%d")
        serie_mrr.append({"mes": m0.strftime("%Y-%m"),
                          "mrr": sum(p.get("mensual_usd") or precio for p in cerrados
                                     if (p.get("cerrado_en") or "9999") < tope)})
    ultimos3 = [x["mrr"] for x in serie_mrr[-4:]]
    ritmo = (ultimos3[-1] - ultimos3[0]) / 3 if len(ultimos3) == 4 else 0
    meses_a_dic = 12 - cuando.month
    proyeccion_dic = round(mrr + max(0, ritmo) * meses_a_dic)

    # Mapa de horarios: todas las llamadas del filtro (no solo del período),
    # porque con un mes de datos cada casillero tiene dos llamadas.
    heat = defaultdict(lambda: [0, 0])
    for l in llamadas:
        dt = parse_dt(l["hecha_en"])
        if dt and dt.weekday() < 6 and 9 <= dt.hour <= 20:
            heat[(dt.weekday(), dt.hour)][0] += 1
            heat[(dt.weekday(), dt.hour)][1] += l["efectivo"]
    mapa = [{"dia": d, "hora": h, "llamadas": v[0], "pct": round(100 * v[1] / v[0])}
            for (d, h), v in sorted(heat.items())]

    def _por(clave):
        grupos = defaultdict(lambda: {"contactados": 0, "cerrados": 0, "total": 0})
        for pid, p in prosp.items():
            g = grupos[clave(p) or "—"]
            g["total"] += 1
            if pid in llamados_ever or p["estado"] != "sin_contactar":
                g["contactados"] += 1
            if p["estado"] == "cerrado":
                g["cerrados"] += 1
        out = [{"nombre": k, **v, "tasa": round(100 * v["cerrados"] / v["contactados"]) if v["contactados"] else None}
               for k, v in grupos.items()]
        out.sort(key=lambda x: (-x["contactados"], -x["total"]))
        return out

    motivos = Counter(p.get("motivo_descarte") or "Sin motivo" for p in prosp.values() if p["estado"] == "descartado")
    contactados_total = sum(1 for pid, p in prosp.items() if pid in llamados_ever or p["estado"] != "sin_contactar")
    sin_tocar = len(prosp) - contactados_total
    prom_semana = sum(s["llamadas"] for s in semanas[-5:-1]) / 4 if semanas else 0
    # Cuántos prospectos nuevos se tocan por semana: primeras llamadas.
    primeras = Counter()
    for pid in llamados_ever:
        fl = min(l["hecha_en"] for l in llamadas if l["prospecto_id"] == pid)
        primeras[(parse_dt(fl).date() - timedelta(days=parse_dt(fl).weekday())).isoformat()] += 1
    nuevos_por_semana = sum(primeras.get(s["desde"], 0) for s in semanas[-5:-1]) / 4
    semanas_para_barrer = round(sin_tocar / nuevos_por_semana) if nuevos_por_semana else None

    cierres = sorted(({"id": p["id"], "nombre": p["nombre"], "barrio": p.get("barrio"), "zona": p.get("zona"),
                       "alta": p.get("cerrado_en"), "mensual": p.get("mensual_usd") or precio,
                       "ciclo": (lambda a, b: (b.date() - a.date()).days if a and b else None)(
                           parse_dt(p.get("primera_llamada") or p.get("creado_en")), parse_dt(p.get("cerrado_en")))}
                      for p in cerrados), key=lambda x: x["alta"] or "")
    comision = round(mrr * cfg["comision_pct"] / 100, 2) if cfg["comision_pct"] else None

    datos = {
        "periodo": periodo, "desde": d0, "hasta": d1, "config": cfg,
        "kpis": {
            "mrr": mrr, "mrr_mes": mrr_mes, "cerrados": len(cerrados), "cerrados_periodo": len(cerrados_periodo),
            "ponderado": round(ponderado), "llamadas": n_ll, "efectivos": n_ef,
            "tasa_efectivo": round(100 * n_ef / n_ll) if n_ll else None,
            "llamadas_por_dia": round(n_ll / dias_con_llamadas, 1) if n_ll else 0,
            "dias_habiles": dias_habiles,
            "reuniones": len(reuniones_p),
            "llamada_a_reunion": round(100 * len(reuniones_p) / n_ll, 1) if n_ll else None,
            "llamadas_por_reunion": round(n_ll / len(reuniones_p)) if reuniones_p else None,
            "ciclo_dias": round(sum(ciclos) / len(ciclos)) if ciclos else None,
            "comision": comision,
        },
        "embudo": embudo, "peor_paso": peor, "semanas": semanas,
        "serie_mrr": serie_mrr, "proyeccion_dic": proyeccion_dic, "mapa": mapa,
        "por_zona": _por(lambda p: p.get("barrio")), "por_categoria": _por(lambda p: categoria(p.get("tipo"))),
        "motivos": [{"motivo": k, "n": v} for k, v in motivos.most_common()],
        "cobertura": {"contactados": contactados_total, "total": len(prosp), "sin_tocar": sin_tocar,
                      "semanas_para_barrer": semanas_para_barrer},
        "cierres": cierres,
    }
    datos["hallazgos"] = hallazgos(datos, prosp, cuando)
    datos["prom_llamadas_semana"] = round(prom_semana)
    return datos


def hallazgos(d: dict, prosp: dict, cuando: datetime) -> list[dict]:
    """Reglas sobre los mismos números del panel, no texto inventado. Cada una
    exige un mínimo de datos: con tres llamadas cualquier patrón es ruido."""
    out = []
    cats = [x for x in d["por_categoria"] if x["contactados"] >= 4 and x["tasa"] is not None]
    if len(cats) >= 2:
        mejor = max(cats, key=lambda x: x["tasa"])
        peor = min(cats, key=lambda x: x["tasa"])
        if mejor["tasa"] > 0 and mejor["tasa"] >= 2 * max(1, peor["tasa"]):
            pend = sum(1 for p in prosp.values() if p["estado"] == "sin_contactar" and categoria(p.get("tipo")) == mejor["nombre"])
            out.append({"tipo": "bueno", "titulo": f"{mejor['nombre']} cierra {mejor['tasa']}% contra {peor['tasa']}% de {peor['nombre']}.",
                        "texto": f"Quedan {pend} de {mejor['nombre'].lower()} sin contactar: suben en tu cola."})
    for z in d["por_zona"]:
        if z["contactados"] >= 8 and z["cerrados"] == 0:
            out.append({"tipo": "alerta", "titulo": f"{z['nombre']}: {z['contactados']} contactados y ningún cierre.",
                        "texto": "Revisá qué objeción aparece ahí; un piloto gratis de entrada puede destrabar."})
            break
    pilotos = [p for p in prosp.values() if p["estado"] == "piloto" and p.get("piloto_inicio")]
    for p in pilotos:
        ini = parse_dt(p["piloto_inicio"])
        if ini:
            dia = (cuando.date() - ini.date()).days + 1
            if DIAS_PILOTO - 5 <= dia <= DIAS_PILOTO + 3:
                out.append({"tipo": "info", "titulo": f"El piloto de {p['nombre']} termina en {max(0, DIAS_PILOTO - dia)} días.",
                            "texto": "Es el momento de llamar para cerrar."})
    franjas = [m for m in d["mapa"] if m["llamadas"] >= 5]
    if len(franjas) >= 4:
        mejor = max(franjas, key=lambda m: m["pct"])
        peor = min(franjas, key=lambda m: m["pct"])
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]
        if mejor["pct"] - peor["pct"] >= 20:
            out.append({"tipo": "info", "titulo": f"La mejor franja es el {dias[mejor['dia']]} a las {mejor['hora']} h ({mejor['pct']}% habla con el dueño).",
                        "texto": f"La peor: {dias[peor['dia']]} a las {peor['hora']} h ({peor['pct']}%)."})
    if d["peor_paso"]:
        a, b, pct = d["peor_paso"]
        out.append({"tipo": "alerta", "titulo": f"Donde más se pierde: de «{a}» a «{b}» ({pct}%).",
                    "texto": "Ahí conviene trabajar el guion."})
    vencidas = sum(1 for p in prosp.values() if p.get("proxima_llamada") and p["estado"] != "cerrado"
                   and p["proxima_llamada"][:10] < cuando.strftime("%Y-%m-%d"))
    if vencidas >= 5:
        out.append({"tipo": "malo", "titulo": f"Hay {vencidas} llamadas vencidas.",
                    "texto": "Un prospecto que pidió que lo llamen y no lo llamaste se enfría rápido."})
    return out[:6]


# ── importar y exportar ──────────────────────────────────────────────────────

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def leer_xlsx(contenido: bytes) -> list[list]:
    """Las filas de la primera hoja, con los links de las celdas resueltos.

    Sin openpyxl a propósito: no está en requirements y para leer una grilla
    alcanza con el XML. Una celda con hipervínculo ("Ver en Maps") devuelve la
    URL y no el texto.
    """
    z = zipfile.ZipFile(io.BytesIO(contenido))
    compartidos = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
            compartidos.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    hojas = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    if not hojas:
        return []
    hoja = "xl/worksheets/sheet1.xml" if "xl/worksheets/sheet1.xml" in hojas else hojas[0]
    raiz = ET.fromstring(z.read(hoja))
    links = {}
    rels_path = hoja.replace("worksheets/", "worksheets/_rels/") + ".rels"
    if rels_path in z.namelist():
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read(rels_path)).findall("rel:Relationship", _NS)}
        for h in raiz.iter(f"{{{_NS['m']}}}hyperlink"):
            rid = h.get(f"{{{_NS['r']}}}id")
            if rid in rels:
                links[h.get("ref")] = rels[rid]
    filas = []
    for row in raiz.iter(f"{{{_NS['m']}}}row"):
        vals = {}
        for cel in row.findall("m:c", _NS):
            ref = cel.get("r")
            t = cel.get("t")
            v = cel.find("m:v", _NS)
            if t == "s" and v is not None:
                val = compartidos[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in cel.iter(f"{{{_NS['m']}}}t"))
            else:
                val = v.text if v is not None else None
                f = cel.find("m:f", _NS)
                if f is not None and f.text and "HYPERLINK" in f.text.upper():
                    m = re.search(r'HYPERLINK\(\s*"([^"]+)"', f.text, re.I)
                    if m:
                        val = m.group(1)
            if ref in links:
                val = links[ref]
            vals[_col(ref)] = val
        if vals:
            filas.append([vals.get(i) for i in range(max(vals) + 1)])
    return filas


_ENCABEZADOS = {
    "nombre": ("restaurante", "nombre", "negocio", "local"),
    "zona": ("zona", "municipio"), "barrio": ("barrio",), "tipo": ("tipo", "rubro", "categoria"),
    "direccion": ("direccion",), "telefono": ("telefono", "tel", "celular"),
    "rating": ("rating",), "resenas": ("resenas", "reviews"), "maps_url": ("google maps", "maps", "link"),
    "notas": ("notas",), "facilidad": ("facilidad",), "contacto": ("contacto",),
    "estado": ("estado",), "fecha_reunion": ("fecha reunion",), "proximo_paso": ("proximo paso",),
}
_ESTADO_EXCEL = {"sin contactar": "sin_contactar", "contactado": "contactado",
                 "reunion agendada": "reunion_agendada", "reunion hecha": "reunion_hecha",
                 "piloto": "piloto", "cerrado": "cerrado", "descartado": "descartado"}


def filas_a_prospectos(filas: list[list]) -> list[dict]:
    """Encuentra la fila de encabezados (la que dice 'Restaurante' o 'Nombre')
    y mapea cada columna por su título, no por su posición."""
    for i, fila in enumerate(filas):
        titulos = [normalizar(str(x)) if x is not None else "" for x in fila]
        if any(t in ("restaurante", "nombre", "negocio") for t in titulos):
            break
    else:
        return []
    mapa = {}
    for j, t in enumerate(titulos):
        for campo, claves in _ENCABEZADOS.items():
            if campo not in mapa and any(t == k or t.startswith(k) for k in claves):
                mapa[campo] = j
                break
    out = []
    for fila in filas[i + 1:]:
        d = {campo: (fila[j] if j < len(fila) else None) for campo, j in mapa.items()}
        if not d.get("nombre") or not str(d["nombre"]).strip():
            continue
        if d.get("maps_url") and not str(d["maps_url"]).startswith("http"):
            d["maps_url"] = None
        d["estado"] = _ESTADO_EXCEL.get(normalizar(d.get("estado")), "sin_contactar")
        out.append(d)
    return out


def importar(db: str, contenido: bytes) -> dict:
    """Carga un Excel de prospectos. Los de otras zonas quedan guardados y
    archivados; los repetidos no se duplican."""
    try:
        filas = leer_xlsx(contenido)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError):
        return {"ok": False, "error": "no pude leer el archivo: tiene que ser un .xlsx"}
    prospectos = filas_a_prospectos(filas)
    if not prospectos:
        return {"ok": False, "error": "no encontré la columna 'Restaurante' o 'Nombre'"}
    cuenta = Counter()
    for d in prospectos:
        d["guardar_fuera_de_zona"] = True
        pid, que = crear_prospecto(db, d, fuente="excel")
        if que == "creado" and not normalizar_zona(d.get("zona"), d.get("barrio")):
            que = "fuera_de_zona"
        cuenta[que] += 1
    return {"ok": True, "leidos": len(prospectos), "creados": cuenta["creado"],
            "duplicados": cuenta["duplicado"], "fuera_de_zona": cuenta["fuera_de_zona"]}


def exportar_csv(db: str) -> str:
    items = listar(db, por_pagina=100000)["items"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Restaurante", "Zona", "Barrio", "Tipo", "Teléfono", "Rating", "Reseñas", "Facilidad",
                "Contacto", "Estado", "Próxima llamada", "Reunión", "Mensual USD", "Notas"])
    for p in items:
        w.writerow([p["nombre"], p.get("zona"), p.get("barrio"), p.get("tipo"), p.get("telefono"),
                    p.get("rating"), p.get("resenas"), p.get("facilidad"), p.get("contacto"),
                    ESTADO_LABEL.get(p["estado"], p["estado"]), p.get("proxima_llamada"),
                    p.get("fecha_reunion"), p.get("mensual_usd"), p.get("notas")])
    return buf.getvalue()


# ── prospectos viejos del Outbound ───────────────────────────────────────────

def archivar_outbound_viejo(conn: sqlite3.Connection) -> int:
    """Oculta la cola vieja de Outbound, UNA vez (pedido de Juan, 23/9).

    Son los comercios scrapeados (padrón sin web y discovery) que la cola
    mostraba: sin contactar o 'no le interesa'. No se borra nada: se marcan con
    `archivado_en` y `listar_leads` los saltea. Las campañas de mail no miran
    esta marca a propósito; ocultar es un cambio de pantalla, no de campañas.

    Para reactivarlos: `UPDATE businesses SET archivado_en = NULL
    WHERE archivado_en = '<la fecha de esta corrida>'`.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS fid_marcas (clave TEXT PRIMARY KEY, en TEXT)")
    if conn.execute("SELECT 1 FROM fid_marcas WHERE clave = 'archivar_outbound_viejo'").fetchone():
        return 0
    cuando = fmt(ahora())
    cur = conn.execute(
        "UPDATE businesses SET archivado_en = ? WHERE archivado_en IS NULL "
        "AND (source IS NULL OR source = 'discovery') "
        "AND (crm_status IS NULL OR crm_status IN ('sin_contactar', 'no_interesa'))", (cuando,))
    conn.execute("INSERT INTO fid_marcas (clave, en) VALUES ('archivar_outbound_viejo', ?)", (cuando,))
    conn.commit()
    return cur.rowcount


def asignar_vendedores(conn: sqlite3.Connection, emails: list[str]) -> int:
    """Pone el rol de vendedor a los usuarios de esos mails que no tengan rol.
    Corre en cada arranque y en cada alta: el que ya tiene un rol lo conserva,
    así un cambio que haga Juan en el editor de usuarios no se pisa."""
    emails = [e.strip().lower() for e in emails if e and e.strip()]
    if not emails:
        return 0
    fila = conn.execute("SELECT id FROM roles WHERE name = ?", (ROL_VENDEDOR,)).fetchone()
    if not fila:
        return 0
    cur = conn.execute(
        f"UPDATE users SET role_id = ? WHERE role_id IS NULL AND lower(email) IN ({', '.join('?' * len(emails))})",
        [fila[0]] + emails)
    conn.commit()
    return cur.rowcount


def es_vendedor(db: str, user_id) -> bool:
    if not user_id:
        return False
    c = _conn(db)
    try:
        f = c.execute("SELECT r.name FROM users u JOIN roles r ON u.role_id = r.id WHERE u.id = ?",
                      (user_id,)).fetchone()
    finally:
        c.close()
    return bool(f) and f[0] == ROL_VENDEDOR
