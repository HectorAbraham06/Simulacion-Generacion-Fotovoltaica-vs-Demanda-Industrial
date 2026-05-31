
import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import plotly.graph_objects as go

# Configuración de página de Streamlit
st.set_page_config(page_title="Motor Fotovoltaico & Demanda Industrial", layout="wide")

st.title("Motor de Jensen: Simulación Fotovoltaica vs. Demanda Industrial")
st.markdown("Herramienta interactiva para dimensionamiento de sistemas FV y análisis de cobertura de carga quinceminutal anual.")

# =============================================================================
# FUNCIONES DE SIMULACIÓN (BACKEND)
# =============================================================================

def generar_demanda_industrial(demanda_max, factor_planta, PF, tiempos):
    """
    Genera una curva de carga sintética anual cada 15 minutos (35,040 periodos)
    diferenciando Verano, No Verano y Fines de Semana.
    """
    # Extraer características temporales a partir de la serie con zona horaria
    mes = tiempos.month
    dia_semana = tiempos.weekday # 5: Sábado, 6: Domingo
    hora = tiempos.hour + tiempos.minute / 60.0
    
    # Inicializar arreglo base de demanda
    perfil_base = np.zeros(len(tiempos))
    
    # Definición de comportamientos típicos de planta industrial
    for i, (h, m, ds) in enumerate(zip(hora, mes, dia_semana)):
        # Determinar estacionalidad (Verano: Mayo a Septiembre)
        es_verano = 5 <= m <= 9
        es_fin_semana = ds >= 5
        
        # Perfil diario base (Turnos de trabajo de la planta: pico 8:00 a 18:00)
        if not es_fin_semana:
            # Perfil de día operativo habitual
            carga_hora = 0.4 + 0.5 * np.exp(-((h - 13)**2) / 24) # Valle en la noche, meseta en el día
        else:
            # Fin de semana (operación mínima de mantenimiento)
            carga_hora = 0.2 + 0.1 * np.sin(2 * np.pi * h / 24)
            
        # Modificador por estacionalidad (mayor aire acondicionado/procesos en verano)
        if es_verano and not es_fin_semana:
            carga_hora *= 1.25 
            
        perfil_base[i] = carga_hora

    # Normalizar y ajustar al Factor de Planta solicitado
    promedio_actual = np.mean(perfil_base)
    if promedio_actual > 0:
        perfil_base = perfil_base * (factor_planta / promedio_actual)
        
    # Forzar que el pico absoluto coincida con la Demanda Máxima
    perfil_base = (perfil_base / np.max(perfil_base)) * demanda_max
    
    # Ruido aleatorio realista del comportamiento industrial (±5%)
    ruido = np.random.normal(0, 0.05, len(tiempos))
    demanda_kw = np.clip(perfil_base + (perfil_base * ruido), 0, demanda_max)
    
    # Cálculo de Energía quinceminutal (kW * 15/60 hrs)
    demanda_kwh = demanda_kw * (15 / 60)
    
    return demanda_kw, demanda_kwh

def simular_sistema_fv(lat, lon, alt, tz, tilt, azimuth, area, ef, n_paneles, tiempos):
    """
    Ejecuta el pipeline matemático original del archivo del usuario usando pvlib.
    """
    sol = pvlib.solarposition.get_solarposition(tiempos, lat, lon)
    
    clearsky = pvlib.clearsky.ineichen(
        sol['apparent_zenith'],
        airmass_absolute=1.5,
        linke_turbidity=3,
        altitude=alt
    )
    
    irradiance_total = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=sol['apparent_zenith'],
        solar_azimuth=sol['azimuth'],\
        dni=clearsky['dni'],
        ghi=clearsky['ghi'],
        dhi=clearsky['dhi']
    )
    
    poa_global = irradiance_total['poa_global']
    potencia_kw = (poa_global * area * ef * n_paneles) / 1000
    energia_kwh = potencia_kw * (15 / 60)
    
    return poa_global, potencia_kw, energia_kwh, clearsky['ghi']

# =============================================================================
# INTERFAZ DE USUARIO (STREAMLIT SIDEBAR)
# =============================================================================

st.sidebar.header("🛠️ Configuración de Parámetros")

with st.sidebar.expander("1. Geolocalización", expanded=True):
    latitud = st.number_input("Latitud (°)", value=25.6, step=0.1, format="%.4f")
    longitud = st.number_input("Longitud (°)", value=-100.3, step=0.1, format="%.4f")
    altitud = st.number_input("Altitud (msnm)", value=500, step=10)
    zona_horaria = st.selectbox("Zona Horaria", ['Etc/GMT+6', 'Etc/GMT+7', 'America/Mexico_City'])

with st.sidebar.expander("2. Especificaciones de Paneles", expanded=True):
    potencia_w = st.number_input("Potencia de un panel (W)", value=440, step=5)
    eficiencia = st.slider("Eficiencia del panel (%)", min_value=10.0, max_value=30.0, value=22.0, step=0.5) / 100.0
    area_panel = st.number_input("Área del panel (m²)", value=2.0, step=0.1)
    cantidad_paneles = st.number_input("Número total de paneles", min_value=1, value=120, step=10)

with st.sidebar.expander("3. Geometría de Instalación", expanded=True):
    inclinacion = st.slider("Inclinación / Tilt (°)", min_value=0, max_value=90, value=25)
    azimut = st.slider("Orientación / Azimut (°)", min_value=0, max_value=360, value=180, help="180° indica orientación al Sur")

with st.sidebar.expander("4. Perfil de Demanda Planta", expanded=True):
    demanda_maxima = st.selectbox("Demanda Máxima (kW)", [30, 50, 60], index=1)
    factor_planta = st.slider("Factor de Planta", min_value=0.50, max_value=0.70, value=0.60, step=0.01)
    factor_potencia = st.slider("Factor de Potencia (PF)", min_value=0.70, max_value=0.95, value=0.85, step=0.01)

# Botón de ejecución controlado
st.sidebar.markdown("---")
ejecutar_simulacion = st.sidebar.button("🚀 Iniciar Simulación", use_container_width=True)

# =============================================================================
# LOGICA DE CONTROL Y RENDERIZADO
# =============================================================================

# Inicializar sesión para mantener los datos de visualización estables hasta el siguiente clic
if "df_resultados" not in st.session_state:
    st.session_state.df_resultados = None
    st.session_state.resumen = None

if ejecutar_simulacion or st.session_state.df_resultados is None:
    with st.spinner("Procesando modelos físicos de radiación solar y demanda..."):
        # Definición del índice temporal anual quinceminutal (35,040 periodos)
        tiempos = pd.date_range(
            start='2026-01-01 00:00',
            end='2026-12-31 23:45',
            freq='15min',
            tz=zona_horaria
        )
        
        # Ejecución de los modelos matemáticos
        poa, pot_fv_kw, env_fv_kwh, ghi = simular_sistema_fv(
            latitud, longitud, altitud, zona_horaria, inclinacion, azimut, 
            area_panel, eficiencia, cantidad_paneles, tiempos
        )
        
        dem_kw, dem_kwh = generar_demanda_industrial(
            demanda_maxima, factor_planta, factor_potencia, tiempos
        )
        
        # Estructuración de datos finales
        df = pd.DataFrame({
            'Fecha_Hora': tiempos,
            'Demanda_kW': dem_kw,
            'Demanda_kWh': dem_kwh,
            'POA_Panel_W_m2': poa,
            'Generacion_Potencia_kW': pot_fv_kw,
            'Generacion_Energia_kWh': env_fv_kwh,
            'Excedente_Deficit_kW': pot_fv_kw - dem_kw
        })
        
        # Guardado en Session State para evitar recargas reactivas automáticas
        st.session_state.df_resultados = df
        st.session_state.resumen = {
            'energia_fv_anual': env_fv_kwh.sum(),
            'demanda_anual': dem_kwh.sum(),
            'potencia_pico': pot_fv_kw.max(),
            'potencia_disponible': cantidad_paneles * potencia_w / 1000
        }

# Recuperación de datos estables
df_analisis = st.session_state.df_resultados
resumen = st.session_state.resumen

# --- PANELES DE INFORME Y MÉTRICAS CLAVE ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Energía Solar Anual", f"{resumen['energia_fv_anual']:,.1f} kWh")
col2.metric("Consumo Planta Anual", f"{resumen['demanda_anual']:,.1f} kWh")
col3.metric("Potencia DC Instalada", f"{resumen['potencia_disponible']:.1f} kWp")
col4.metric("Generación FV Pico", f"{resumen['potencia_pico']:.2f} kW")

st.markdown("---")

# --- CONTROL DE VISUALIZACIÓN GRÁFICA ---
st.subheader("📈 Comportamiento Temporal Dinámico")
st.markdown("Filtra un rango de fechas específico para inspeccionar la interacción entre la curva de generación solar y la demanda de la planta.")

# Control de fecha para el filtro dinámico de la gráfica
col_f1, col_f2 = st.columns(2)
with col_f1:
    fecha_inicio = st.date_input("Fecha Inicio", value=pd.to_datetime("2026-03-15"))
with col_f2:
    fecha_fin = st.date_input("Fecha Fin", value=pd.to_datetime("2026-03-21"))

# Aplicar filtro de fecha sobre el DataFrame clonado localmente
df_analisis['Fecha_Solo'] = df_analisis['Fecha_Hora'].dt.date
df_filtrado = df_analisis[(df_analisis['Fecha_Solo'] >= fecha_inicio) & (df_analisis['Fecha_Solo'] <= fecha_fin)]

if not df_filtrado.empty:
    fig = go.Figure()
    
    # Trazado de Demanda de Carga
    fig.add_trace(go.Scatter(
        x=df_filtrado['Fecha_Hora'], y=df_filtrado['Demanda_kW'],
        mode='lines', name='Demanda Industrial (kW)',
        line=dict(color='#EF553B', width=2)
    ))
    
    # Trazado de Generación Solar
    fig.add_trace(go.Scatter(
        x=df_filtrado['Fecha_Hora'], y=df_filtrado['Generacion_Potencia_kW'],
        mode='lines', name='Generación Solar FV (kW)',
        line=dict(color='#636EFA', width=2.5),
        fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.15)'
    ))

    fig.update_layout(
        xaxis_title="Fecha y Hora",
        yaxis_title="Potencia Eléctrica (kW)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=20, r=20, t=40, b=20)
    )
    
    st.plotly_chart(fig, use_container_width=True)
else:
    st.warning("No hay datos disponibles para el rango de fechas seleccionado.")

# --- SECCIÓN DE EXPORTACIÓN Y DESCARGAS ---
st.markdown("---")
st.subheader("💾 Descarga de Datos Estructurados")

# Preparación del archivo csv de salida en formato String para el componente de descarga
csv_data = df_analisis.drop(columns=['Fecha_Solo']).to_csv(index=False).encode('utf-8')

st.download_button(
    label="📥 Descargar Series Temporales Completas (CSV)",
    data=csv_data,
    file_name="motor_generacion_y_demanda_anual.csv",
    mime="text/csv",
    use_container_width=True
)