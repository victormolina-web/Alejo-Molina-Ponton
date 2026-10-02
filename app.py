import os
import io
import json
import base64
from datetime import date
from pathlib import Path

import streamlit as st
import pandas as pd
import anthropic

# ---------------------------------------------------------
# 1. Configuración general
# ---------------------------------------------------------
st.set_page_config(
    page_title="SIMP - Productividad e IA (Claude)",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
        .stButton>button { width: 100%; height: 3em; font-size: 16px; border-radius: 8px; }
        .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    </style>
""", unsafe_allow_html=True)


def leer_secreto(nombre):
    """Lee un valor de variables de entorno o de st.secrets (nube)."""
    valor = os.environ.get(nombre, "")
    if valor:
        return valor
    rutas = [
        Path.home() / ".streamlit" / "secrets.toml",
        Path(__file__).parent / ".streamlit" / "secrets.toml",
    ]
    if Path("/mount/src").exists() or any(r.exists() for r in rutas):
        try:
            return st.secrets[nombre]
        except Exception:
            return ""
    return ""


# Código de acceso opcional (se activa si existe APP_PASSWORD)
clave_acceso = leer_secreto("APP_PASSWORD")
if clave_acceso:
    ingresada = st.text_input("🔒 Código de acceso:", type="password")
    if ingresada != clave_acceso:
        if ingresada:
            st.error("Código incorrecto.")
        st.stop()

api_key = leer_secreto("ANTHROPIC_API_KEY")
if not api_key:
    api_key = st.sidebar.text_input("Ingresa tu ANTHROPIC_API_KEY:", type="password")

# ---------------------------------------------------------
# 2. Datos base y archivos de guardado
# ---------------------------------------------------------
MINUTOS_JORNADA = 480  # 8 horas efectivas
DIR = Path(__file__).parent
ARCH_INCIDENTES = DIR / "incidentes.csv"
ARCH_RUTAS = DIR / "rutas.csv"
ARCH_EQUIPO = DIR / "equipo.csv"

RUTAS_DEFECTO = pd.DataFrame([
    {"Ruta o pila": "Pendientes Soportes",       "Casos por hora": 7.0, "Turno completo": 57},
    {"Ruta o pila": "Gestión Web",               "Casos por hora": 5.0, "Turno completo": 45},
    {"Ruta o pila": "Respuesta Escrita",         "Casos por hora": 3.0, "Turno completo": 24},
    {"Ruta o pila": "Liquidación Incapacidades", "Casos por hora": 7.0, "Turno completo": 57},
    {"Ruta o pila": "Auditoría",                 "Casos por hora": 8.0, "Turno completo": 70},
    {"Ruta o pila": "Super Salud",               "Casos por hora": 2.0, "Turno completo": 18},
    {"Ruta o pila": "Jurídico",                  "Casos por hora": 2.0, "Turno completo": 16},
])
RUTAS = list(RUTAS_DEFECTO["Ruta o pila"])
COLS_RUTAS = list(RUTAS_DEFECTO.columns)

EQUIPO_DEFECTO = pd.DataFrame({
    "Persona": [""] * len(RUTAS),
    "Ruta": RUTAS,
    "Casos realizados": [0] * len(RUTAS),
})
COLS_EQUIPO = list(EQUIPO_DEFECTO.columns)


def leer_csv(archivo, columnas):
    try:
        if archivo.exists():
            d = pd.read_csv(archivo)
            if all(c in d.columns for c in columnas):
                return d[columnas]
    except Exception:
        pass
    return None


def guardar_csv(d, archivo):
    try:
        d.to_csv(archivo, index=False)
    except Exception:
        pass


def cargar_incidentes():
    try:
        if ARCH_INCIDENTES.exists():
            d = pd.read_csv(ARCH_INCIDENTES)
            if "Ruta" not in d.columns:
                d["Ruta"] = "Todas"
            d["Ruta"] = d["Ruta"].fillna("Todas")
            d["Motivo"] = d["Motivo"].fillna("Sin motivo")
            return d[["Fecha", "Minutos", "Motivo", "Ruta"]].to_dict("records")
    except Exception:
        pass
    return []


def guardar_incidentes():
    guardar_csv(pd.DataFrame(st.session_state.incidentes,
                             columns=["Fecha", "Minutos", "Motivo", "Ruta"]), ARCH_INCIDENTES)


def registrar_incidente():
    minutos = int(st.session_state.get("inc_min") or 0)
    if minutos <= 0:
        return
    st.session_state.incidentes.append({
        "Fecha": st.session_state["fecha_analisis"].isoformat(),
        "Minutos": minutos,
        "Motivo": (st.session_state.get("inc_motivo") or "").strip() or "Sin motivo",
        "Ruta": st.session_state.get("inc_ruta") or "Todas",
    })
    guardar_incidentes()


def deshacer_incidente():
    fecha = st.session_state["fecha_analisis"].isoformat()
    for i in range(len(st.session_state.incidentes) - 1, -1, -1):
        if st.session_state.incidentes[i]["Fecha"] == fecha:
            st.session_state.incidentes.pop(i)
            break
    guardar_incidentes()


if "incidentes" not in st.session_state:
    st.session_state.incidentes = cargar_incidentes()
if "rutas_base" not in st.session_state:
    cargado = leer_csv(ARCH_RUTAS, COLS_RUTAS)
    st.session_state.rutas_base = cargado if cargado is not None else RUTAS_DEFECTO.copy()
if "equipo_base" not in st.session_state:
    cargado = leer_csv(ARCH_EQUIPO, COLS_EQUIPO)
    if cargado is not None:
        cargado = cargado.copy()
        cargado["Persona"] = cargado["Persona"].fillna("")
    st.session_state.equipo_base = cargado if cargado is not None else EQUIPO_DEFECTO.copy()

# ---------------------------------------------------------
# 3. Encabezado
# ---------------------------------------------------------
st.title("📊 SIMP: Productividad y Clasificación con Claude")

fecha_analisis = st.date_input("📅 Fecha de análisis", value=date.today(), key="fecha_analisis")
zona_metricas = st.container()   # se llena más abajo, cuando ya están los cálculos

inc_dia = [i for i in st.session_state.incidentes if i.get("Fecha") == fecha_analisis.isoformat()]
min_total = sum(int(i["Minutos"]) for i in inc_dia)
min_todas = sum(int(i["Minutos"]) for i in inc_dia if i.get("Ruta", "Todas") == "Todas")
min_disponibles = max(0, MINUTOS_JORNADA - min_todas)

st.divider()

# ---------------------------------------------------------
# 4. Incidentes
# ---------------------------------------------------------
st.subheader("⚠️ Incidentes que descuentan tiempo")
st.caption("Registra fallas del sistema u otras interrupciones. Elige 'Todas' si afectó a todas las rutas, "
           "o una ruta específica si solo afectó a esa persona.")

with st.form("form_incidente", clear_on_submit=True):
    st.number_input("Minutos perdidos (120 = 2 horas)", min_value=5,
                    max_value=MINUTOS_JORNADA, value=60, step=5, key="inc_min")
    st.selectbox("Ruta afectada", ["Todas"] + RUTAS, key="inc_ruta")
    st.text_input("Motivo (ej: caída del sistema)", key="inc_motivo")
    st.form_submit_button("Registrar incidente", on_click=registrar_incidente)

if inc_dia:
    st.dataframe(pd.DataFrame(inc_dia)[["Minutos", "Ruta", "Motivo"]], hide_index=True)
    st.button("Deshacer último incidente", on_click=deshacer_incidente)

if st.session_state.incidentes:
    st.download_button(
        "Descargar historial de incidentes (CSV)",
        data=pd.DataFrame(st.session_state.incidentes).to_csv(index=False),
        file_name="incidentes.csv",
        mime="text/csv",
    )

st.divider()

# ---------------------------------------------------------
# 5. Cuadro por ruta o pila
# ---------------------------------------------------------
st.subheader("📦 Productividad por ruta o pila")

with st.expander("⚙️ Ajustar casos por hora y turno completo"):
    df_rutas = st.data_editor(
        st.session_state.rutas_base,
        hide_index=True,
        disabled=["Ruta o pila"],
        key="tabla_rutas",
        column_config={
            "Casos por hora": st.column_config.NumberColumn(min_value=0.1, step=0.5),
            "Turno completo": st.column_config.NumberColumn(min_value=0, step=1),
        },
    )
df_rutas = df_rutas.fillna(0)
guardar_csv(df_rutas, ARCH_RUTAS)

min_ruta = {
    r: min(MINUTOS_JORNADA,
           min_todas + sum(int(i["Minutos"]) for i in inc_dia if i.get("Ruta") == r))
    for r in RUTAS
}
mins = df_rutas["Ruta o pila"].map(min_ruta).fillna(min_todas)
turno = df_rutas["Turno completo"].astype(int)
descuento = (df_rutas["Casos por hora"] * mins / 60 + 0.5).astype(int)   # redondeo normal
total = (turno - descuento).clip(lower=0)

resumen = pd.DataFrame({
    "Ruta o pila": df_rutas["Ruta o pila"],
    "Casos por hora": df_rutas["Casos por hora"],
    "Turno completo": turno,
    "Min descontados": mins.astype(int),
    "Descuento por fallas": descuento,
    "Total a realizar": total,
})
st.dataframe(
    resumen, hide_index=True,
    column_config={"Casos por hora": st.column_config.NumberColumn(format="%g")},
)

with zona_metricas:
    m1, m2, m3 = st.columns(3)
    m1.metric("Incidentes hoy", len(inc_dia), f"{min_total} min registrados", delta_color="off")
    m2.metric("Casos en turno completo", int(turno.sum()))
    m3.metric("🎯 Total a realizar hoy", int(total.sum()),
              f"-{int(descuento.sum())} casos por fallas" if descuento.sum() else "Sin descuento",
              delta_color="normal" if descuento.sum() else "off")

st.divider()

# ---------------------------------------------------------
# 6. Equipo: persona, ruta y productividad
# ---------------------------------------------------------
st.subheader("👥 Productividad por persona")
st.caption("Escribe el nombre, elige la ruta y anota los casos realizados. "
           "Puedes agregar o quitar filas con la fila vacía del final.")

equipo = st.data_editor(
    st.session_state.equipo_base,
    num_rows="dynamic",
    hide_index=True,
    key="tabla_equipo",
    column_config={
        "Persona": st.column_config.TextColumn(),
        "Ruta": st.column_config.SelectboxColumn(options=RUTAS, required=True),
        "Casos realizados": st.column_config.NumberColumn(min_value=0, step=1),
    },
)
guardar_csv(equipo, ARCH_EQUIPO)

por_ruta = resumen.set_index("Ruta o pila")
filas = []
for _, f in equipo.iterrows():
    ruta = f.get("Ruta")
    if ruta not in por_ruta.index:
        continue
    nombre = f.get("Persona")
    nombre = str(nombre).strip() if pd.notna(nombre) and str(nombre).strip() else "(sin nombre)"
    realizados = float(f["Casos realizados"]) if pd.notna(f.get("Casos realizados")) else 0.0
    meta = int(por_ruta.loc[ruta, "Total a realizar"])
    filas.append({
        "Persona": nombre,
        "Ruta o pila": ruta,
        "Meta sin fallas": int(por_ruta.loc[ruta, "Turno completo"]),
        "Descuento por fallas": int(por_ruta.loc[ruta, "Descuento por fallas"]),
        "Meta ajustada": meta,
        "Casos realizados": int(realizados),
        "Cumplimiento %": round(realizados / meta * 100) if meta > 0 else None,
    })

if filas:
    st.dataframe(
        pd.DataFrame(filas), hide_index=True,
        column_config={"Cumplimiento %": st.column_config.NumberColumn(format="%d%%")},
    )

st.divider()

# ---------------------------------------------------------
# 7. Análisis de documentos con Claude (una o varias solicitudes)
# ---------------------------------------------------------
st.subheader("🤖 Analizar documento o radicado con Claude")

SYSTEM_PROMPT = """
Eres un experto en tiempos y métodos para la medición de productividad operativa.
El texto o documento recibido puede contener UNA o VARIAS solicitudes independientes
(por ejemplo, varios radicados, peticiones o requerimientos en un mismo documento).
Identifica cada solicitud por separado, sin agrupar solicitudes distintas, y asígnala a una ruta.

Rutas válidas para el campo "ruta": __RUTAS__, u "Otro" si ninguna aplica.
Orientación:
- Pendientes Soportes: soportes pendientes de revisión o complemento.
- Gestión Web: consultas o gestiones por canal web.
- Respuesta Escrita: respuestas escritas a peticiones o reclamos.
- Liquidación Incapacidades: liquidación de incapacidades.
- Auditoría: auditorías o validaciones de auditoría.
- Super Salud: requerimientos de la Superintendencia de Salud u otros entes de control.
- Jurídico: temas jurídicos, tutelas o procesos legales.
Si eliges "Otro", estima en "tiempo_estimado_min" los minutos que tomaría esa solicitud.

No incluyas nombres, cédulas ni otros datos personales en tu respuesta.
Responde ÚNICAMENTE con JSON válido, sin markdown ni texto adicional:
{
  "resumen": "resumen del documento en máximo 3 frases",
  "solicitudes": [
    {
      "descripcion": "descripción breve de la solicitud",
      "ruta": "una de las rutas válidas u Otro",
      "tiempo_estimado_min": 15,
      "justificacion": "explicación breve"
    }
  ],
  "contiene_datos_personales": true
}
""".replace("__RUTAS__", ", ".join(RUTAS))

MAX_MB = 10


def preparar_contenido(archivo, descripcion):
    """Convierte el archivo subido en bloques de contenido para la API."""
    nombre = archivo.name.lower()
    datos = archivo.getvalue()
    bloques = []

    if nombre.endswith(".pdf"):
        bloques.append({"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.b64encode(datos).decode("utf-8")}})
    elif nombre.endswith((".png", ".jpg", ".jpeg")):
        media = "image/png" if nombre.endswith(".png") else "image/jpeg"
        bloques.append({"type": "image", "source": {
            "type": "base64", "media_type": media,
            "data": base64.b64encode(datos).decode("utf-8")}})
    elif nombre.endswith(".docx"):
        from docx import Document
        doc = Document(io.BytesIO(datos))
        partes = [p.text for p in doc.paragraphs]
        for tabla in doc.tables:
            for fila in tabla.rows:
                partes.append(" | ".join(c.text for c in fila.cells))
        bloques.append({"type": "text",
                        "text": "Contenido del documento:\n" + "\n".join(partes)[:30000]})
    else:
        texto = datos.decode("utf-8", errors="ignore")[:30000]
        bloques.append({"type": "text", "text": f"Contenido del documento:\n{texto}"})

    instruccion = "Analiza el documento adjunto e identifica todas las solicitudes que contiene."
    if descripcion.strip():
        instruccion += f" Contexto adicional: {descripcion.strip()}"
    bloques.append({"type": "text", "text": instruccion})
    return bloques


def evaluar_con_claude(contenido):
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": contenido}],
        )
        texto = response.content[0].text.strip()
        ini, fin = texto.find("{"), texto.rfind("}")
        if ini == -1 or fin == -1:
            return {"error": "Claude no devolvió un resultado válido. Intenta de nuevo."}
        return json.loads(texto[ini:fin + 1])
    except Exception as e:
        return {"error": f"Error al consultar Claude: {str(e)}"}


def _num(valor):
    try:
        return float(valor)
    except Exception:
        return 0.0


def mostrar_resultado(r, tabla_rutas):
    solicitudes = r.get("solicitudes") or []
    if not solicitudes:
        st.warning("No se identificaron solicitudes en el documento.")
        return

    params = tabla_rutas.set_index("Ruta o pila")
    filas_doc = []
    for n, s in enumerate(solicitudes, 1):
        ruta = s.get("ruta", "Otro")
        cph = float(params.loc[ruta, "Casos por hora"]) if ruta in params.index else 0.0
        if cph > 0:
            minutos, base = 60 / cph, "Casos por hora de la ruta"
        else:
            minutos, base = _num(s.get("tiempo_estimado_min")), "Estimación IA"
        filas_doc.append({"#": n, "Solicitud": s.get("descripcion", ""), "Ruta": ruta,
                          "Minutos": round(minutos, 1), "Tiempo basado en": base})

    res = pd.DataFrame(filas_doc)
    t_min = float(res["Minutos"].sum())

    if r.get("resumen"):
        st.write(r["resumen"])
    if r.get("contiene_datos_personales"):
        st.warning("El documento parece contener datos personales.")

    k1, k2 = st.columns(2)
    k1.metric("Solicitudes", len(res))
    k2.metric("Tiempo estimado", f"{t_min:.0f} min", f"{t_min / 60:.1f} h", delta_color="off")

    st.dataframe(res, hide_index=True)

    if res["Ruta"].nunique() > 1:
        st.caption("Resumen por ruta:")
        st.dataframe(
            res.groupby("Ruta", as_index=False).agg(Solicitudes=("#", "count"), Minutos=("Minutos", "sum")),
            hide_index=True,
        )

    if min_disponibles > 0:
        st.info(f"Equivale al {t_min / min_disponibles * 100:.0f}% del tiempo disponible hoy "
                f"({min_disponibles} min).")
        if t_min > min_disponibles:
            st.warning("El tiempo estimado supera el tiempo disponible de hoy.")
    else:
        st.warning("No hay tiempo disponible hoy: los incidentes cubren toda la jornada.")

    with st.expander("Ver justificaciones"):
        for n, s in enumerate(solicitudes, 1):
            st.write(f"**{n}.** {s.get('justificacion', '')}")


st.warning(
    "⚠️ Usa documentos de ejemplo o ficticios. No subas información real con "
    "nombres, cédulas o datos de salud."
)

descripcion_input = st.text_area(
    "Descripción del radicado (opcional si subes un documento):",
    placeholder="Ej: Requerimiento de Super Salud por mora en liquidación de incapacidad...",
    height=90
)

archivo_subido = st.file_uploader(
    "📎 Cargar documento (PDF, Word, TXT o imagen):",
    type=["pdf", "docx", "txt", "png", "jpg", "jpeg"]
)

if st.button("Analizar con Claude", type="primary"):
    if not api_key:
        st.error("Falta la ANTHROPIC_API_KEY (menú lateral o configuración del servidor).")
    elif archivo_subido is None and not descripcion_input.strip():
        st.warning("Escribe una descripción o carga un documento.")
    elif archivo_subido is not None and archivo_subido.size > MAX_MB * 1024 * 1024:
        st.error(f"El archivo supera {MAX_MB} MB.")
    else:
        if archivo_subido is not None:
            contenido = preparar_contenido(archivo_subido, descripcion_input)
        else:
            contenido = f"Texto a analizar (puede incluir varias solicitudes): {descripcion_input.strip()}"
        with st.spinner("Claude analizando las solicitudes..."):
            st.session_state["ultimo_resultado"] = evaluar_con_claude(contenido)

resultado = st.session_state.get("ultimo_resultado")
if resultado:
    if "error" in resultado:
        st.error(resultado["error"])
    else:
        mostrar_resultado(resultado, df_rutas)
