import streamlit as st
import pandas as pd
import numpy as np
import pvlib
from pvlib import shading
import plotly.graph_objects as go
from timezonefinder import TimezoneFinder

# Configuración de página de Streamlit
st.set_page_config(page_title="Motor Fotovoltaico & Demanda Industrial", layout="wide")

st.title("Motor de Jensen: Simulación Fotovoltaica vs. Demanda Industrial")
st.markdown("Herramienta interactiva para dimensionamiento de sistemas FV y análisis de cobertura de carga quinceminutal anual.")

# =============================================================================
# FUNCIONES DE SIMULACIÓN (BACKEND)
# =============================================================================

def generar_demanda_industrial(demanda_max, factor_planta, tiempos):
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


def simular_sistema_fv(lat, lon, alt, tz, tilt, azimuth, area, ef, n_paneles, tiempos, distancia_filas, longitud_panel, num_filas):
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
        solar_azimuth=sol['azimuth'],
        dni=clearsky['dni'],
        ghi=clearsky['ghi'],
        dhi=clearsky['dhi']
    )
    
    poa_original = irradiance_total['poa_global']
    
    altura_panel = longitud_panel * np.sin(np.radians(tilt))
    
    # 1. Calcular sombreado 1D por fila
    shaded_frac_por_fila = pvlib.shading.shaded_fraction1d(
        solar_zenith=sol['apparent_zenith'],
        solar_azimuth=sol['azimuth'],
        axis_azimuth=azimuth - 90,          # Eje longitudinal de las filas (Este-Oeste)
        shaded_row_rotation=tilt,           # En arreglos fijos, la rotación es el Tilt
        collector_width=altura_panel,       # Longitud inclinada del panel (m)
        pitch=distancia_filas,              # Separación entre filas (m)
        axis_tilt=0,                        # Terreno plano
        surface_to_axis_offset=0,           # Sin desfase de tubo de torque
        cross_axis_slope=0                  # Terreno sin pendiente lateral
    )
    
    # 2. Promedio del arreglo considerando la primera fila libre de sombras
    sombra_total_arreglo = ((num_filas - 1) * shaded_frac_por_fila) / num_filas
    
    # 3. Aplicar pérdidas por sombra al POA
    poa_con_sombra = poa_original * (1 - sombra_total_arreglo)
    
    # 4. Calcular ambas potencias (en kW)
    potencia_sin_sombra_kw = (poa_original * area * ef * n_paneles) / 1000
    potencia_con_sombra_kw = (poa_con_sombra * area * ef * n_paneles) / 1000
    
    # Energías quinceminutales (usamos la que tiene sombras como la real oficial)
    energia_kwh = potencia_con_sombra_kw * (15 / 60)
    
    return poa_original, potencia_con_sombra_kw, potencia_sin_sombra_kw, sombra_total_arreglo, energia_kwh

def obtener_zona_horaria(lat, lon):
    tf = TimezoneFinder()
    zona = tf.timezone_at(lng=lon, lat=lat)
    return zona if zona else 'UTC'

# =============================================================================
# INTERFAZ DE USUARIO (STREAMLIT SIDEBAR)
# =============================================================================

st.sidebar.header("🛠️ Configuración de Parámetros")

with st.sidebar.form(key="formulario_parametros"):
    
    with st.sidebar.expander("1. Geolocalización", expanded=True):
        latitud = st.number_input("Latitud (°)", value=19.4791, step=0.1, format="%.4f")
        longitud = st.number_input("Longitud (°)", value=-96.9500, step=0.1, format="%.4f")
        altitud = st.number_input("Altitud (msnm)", value=1210, step=1)

    zona_horaria = obtener_zona_horaria(latitud, longitud)

    with st.sidebar.expander("2. Especificaciones de Paneles", expanded=True):
        potencia_w = st.number_input("Potencia de un panel (W)", value=425, step=5)
        eficiencia = st.slider("Eficiencia del panel (%)", min_value=10.0, max_value=30.0, value=21.8, step=0.1) / 100.0
        area_panel = st.number_input("Área del panel (m²)", value=1.95, step=0.1)
        cantidad_paneles = st.number_input("Número total de paneles", min_value=1, value=216, step=10)

    with st.sidebar.expander("3. Geometría de Instalación", expanded=True):
        inclinacion = st.slider("Inclinación / Tilt (°)", min_value=0, max_value=90, value=25)
        azimut = st.slider("Orientación / Azimut (°)", min_value=0.00, max_value=360.00, value=164.78, help="180° indica orientación al Sur")
        
    with st.sidebar.expander("4. Configuración de Arreglo (Sombreado)", expanded=True):
        distancia_filas = st.number_input("Distancia entre filas (m)", value=1.15, step=0.1)
        longitud_panel = st.number_input("Longitud física del panel (m)", value=1.134, step=0.1)
        num_filas = st.number_input("Número de filas", value=9, step=1)

    with st.sidebar.expander("5. Perfil de Demanda Planta", expanded=True):
        demanda_maxima = st.number_input("Demanda máxima (kW)", value=80, step=5)
        factor_planta = st.slider("Factor de carga", min_value=0.00, max_value=1.00, value=0.60, step=0.01)
        
    with st.sidebar.expander("6. Parámetros Económicos", expanded=True):
        precio_kwh = st.number_input("Precio de la energía ($ / kWh)", min_value=0.0, value=2.82, step=0.01, format="%.2f")
        tipo_moneda = st.selectbox("Divisa", ["MXN ($)", "USD ($)"], index=0)

    # Botón de ejecución controlado
    st.sidebar.markdown("---")
    ejecutar_simulacion = st.form_submit_button(label="🚀 Iniciar Simulación", use_container_width=True)

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
        poa, pot_fv_kw, pot_sin_sombra_kw, frac_sombra, env_fv_kwh = simular_sistema_fv(
            latitud, longitud, altitud, zona_horaria, inclinacion, azimut, 
            area_panel, eficiencia, cantidad_paneles, tiempos, distancia_filas, longitud_panel, num_filas
        )
        
        dem_kw, dem_kwh = generar_demanda_industrial(
            demanda_maxima, factor_planta, tiempos
        )
        
        # Estructuración de datos finales
        df = pd.DataFrame({
            'Fecha_Hora': tiempos,
            'Demanda_kW': dem_kw,
            'Demanda_kWh': dem_kwh,
            'POA_Original_W_m2': poa,
            'Generacion_Con_Sombra_kW': pot_fv_kw,
            'Generacion_Sin_Sombra_kW': pot_sin_sombra_kw,
            'Fraccion_Sombra_Arreglo': frac_sombra,
            'Generacion_Energia_kWh': env_fv_kwh,
            'Excedente_Deficit_kW': pot_fv_kw - dem_kw
        })
        
        # ... (Aquí ya se calculó tu DataFrame 'df' con 'Generacion_Energia_kWh') ...
        
        # Cálculo Económico Basado en la Generación Real (Con Sombra)
        df['Ahorro_Monetario'] = df['Generacion_Energia_kWh'] * precio_kwh
        
        # Extraer mes y año para agrupaciones mensuales utilizando el índice temporal o la columna Fecha_Hora
        df['Mes'] = df['Fecha_Hora'].dt.strftime('%m - %B')
        
        # Resumen Anual Total
        ahorro_anual_total = df['Ahorro_Monetario'].sum()
        energia_anual_total = df['Generacion_Energia_kWh'].sum()
        
        # Agrupación Mensual para el análisis detallado
        df_mensual = df.groupby('Mes').agg({
            'Generacion_Energia_kWh': 'sum',
            'Ahorro_Monetario': 'sum'
        }).reset_index()
        
        # Guardar en el session_state para que las gráficas lo lean de forma estable
        
        st.session_state.energia_anual = energia_anual_total
        st.session_state.ahorro_anual = ahorro_anual_total
        st.session_state.df_mensual = df_mensual
        st.session_state.divisa = tipo_moneda.split(" ")[0]
        
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

# =============================================================================
# NUEVA SECCIÓN: ANÁLISIS ECONÓMICO Y DE AHORROS
# =============================================================================
st.subheader("Análisis de Impacto Económico ")

# 1. Fila de Métricas Financieras Clave
col_econ1, col_econ2, col_econ3 = st.columns(3)

with col_econ1:
    st.metric(
        label="Tarifa Eléctrica Base",
        value=f"{precio_kwh:.2f} {st.session_state.divisa}/kWh"
    )

with col_econ2:
    st.metric(
        label="Ahorro Económico Anual Estimado",
        value=f"{st.session_state.ahorro_anual:,.2f} {st.session_state.divisa}",
    )

with col_econ3:
    # Promedio mensual estimado
    ahorro_promedio_mes = st.session_state.ahorro_anual / 12
    st.metric(
        label="Ahorro Mensual Promedio",
        value=f"{ahorro_promedio_mes:,.2f} {st.session_state.divisa}"
    )

# 2. Desglose detallado mes a mes en un expansor para no saturar la pantalla
with st.expander("📊 Ver desglose y tabla de ahorros mes por mes", expanded=False):
    st.markdown("A continuación se muestra el retorno económico mensualizado calculado de forma directa:")
    
    # Formatear la tabla para una visualización profesional antes de mostrarla
    tabla_formateada = st.session_state.df_mensual.copy()
    tabla_formateada.columns = ['Mes de Simulación', 'Energía Generada (kWh)', f'Ahorro Estimado ({st.session_state.divisa})']
    
    st.dataframe(
        tabla_formateada.style.format({
            'Energía Generada (kWh)': '{:,.2f}',
            f'Ahorro Estimado ({st.session_state.divisa})': '${:,.2f}'
        }),
        use_container_width=True
    )

# --- CONTROL DE VISUALIZACIÓN GRÁFICA ---
st.subheader("📈 Generación VS. Demanda en el tiempo")
st.markdown("Filtra un rango de fechas específico para inspeccionar la interacción entre la curva de generación solar y la demanda de la planta.")
st.markdown("*(La simulación abarca valores de 2026 solamente)*")
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
        x=df_filtrado['Fecha_Hora'], y=df_filtrado['Generacion_Con_Sombra_kW'],
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


st.markdown("---")
st.subheader("📊 Módulo de Análisis Avanzado del Proyecto")
st.markdown("Utiliza el siguiente menú para evaluar parámetros específicos del comportamiento del sistema.")

# Menú desplegable pequeño para seleccionar qué analizar
opcion_grafica = st.selectbox(
    "Selecciona los datos que deseas analizar en la gráfica:",
    [
        "Comparativa: Generación Con Sombra vs. Sin Sombra",
        "Pérdidas: Fracción de Sombra Total del Arreglo",
        "Financiero: Ahorro Económico Mensualizado",
        "Recurso Solar: Matriz de Irradiancia Promedio Horaria"  # <-- NUEVA OPCIÓN
    ]
)

# Filtro de fecha reutilizado para la segunda gráfica
df_filtrado_avanzado = df_analisis[(df_analisis['Fecha_Solo'] >= fecha_inicio) & (df_analisis['Fecha_Solo'] <= fecha_fin)]

if not df_filtrado_avanzado.empty:
    fig_avanzada = go.Figure()

    if opcion_grafica == "Comparativa: Generación Con Sombra vs. Sin Sombra":
        # Línea de generación ideal (Sin sombra)
        fig_avanzada.add_trace(go.Scatter(
            x=df_filtrado_avanzado['Fecha_Hora'], y=df_filtrado_avanzado['Generacion_Sin_Sombra_kW'],
            mode='lines', name='Generación Ideal (Sin Sombra)',
            line=dict(color='#2CA02C', width=2, dash='dash')
        ))
        # Línea de generación real (Con sombra por filas)
        fig_avanzada.add_trace(go.Scatter(
            x=df_filtrado_avanzado['Fecha_Hora'], y=df_filtrado_avanzado['Generacion_Con_Sombra_kW'],
            mode='lines', name='Generación Real (Con Sombra)',
            line=dict(color='#636EFA', width=2.5),
            fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.1)'
        ))
        fig_avanzada.update_layout(yaxis_title="Potencia Eléctrica (kW)")

    elif opcion_grafica == "Pérdidas: Fracción de Sombra Total del Arreglo":
        # Línea de porcentaje o fracción de sombra (va de 0 a 1)
        fig_avanzada.add_trace(go.Scatter(
            x=df_filtrado_avanzado['Fecha_Hora'], y=df_filtrado_avanzado['Fraccion_Sombra_Arreglo'] * 100,
            mode='lines', name='Área Sombreada del Arreglo (%)',
            line=dict(color='#7F7F7F', width=2),
            fill='tozeroy', fillcolor='rgba(127, 127, 127, 0.2)'
        ))
        fig_avanzada.update_layout(yaxis_title="Porcentaje de Sombra (%)", yaxis=dict(range=[0, 105]))

    elif opcion_grafica == "Financiero: Ahorro Económico Mensualizado":
        # Gráfica de barras para representar el dinero ahorrado al mes
        fig_avanzada.add_trace(go.Bar(
            x=st.session_state.df_mensual['Mes'], 
            y=st.session_state.df_mensual['Ahorro_Monetario'],
            name=f'Ahorro ({st.session_state.divisa})',
            marker_color='#2CA02C',
            text=[f"${x:,.0f}" for x in st.session_state.df_mensual['Ahorro_Monetario']],
            textposition='auto',
        ))
        fig_avanzada.update_layout(yaxis_title=f"Ahorro acumulado ({st.session_state.divisa})")

    elif opcion_grafica == "Recurso Solar: Matriz de Irradiancia Promedio Horaria":
        # 1. Extraer componentes de hora y mes del DataFrame completo de análisis
        df_completo = df_analisis.copy()
        df_completo['Hora_Str'] = df_completo['Fecha_Hora'].dt.strftime('%H:00')
        df_completo['Mes_Num'] = df_completo['Fecha_Hora'].dt.month
        
        # Mapeo de meses en español para los encabezados de la matriz
        meses_es = {1: 'Ene', 2: 'Feb', 3: 'Mar', 4: 'Abr', 5: 'May', 6: 'Jun', 
                    7: 'Jul', 8: 'Ago', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dic'}
        df_completo['Mes_Str'] = df_completo['Mes_Num'].map(meses_es)

        # 2. Agrupar por Hora y Mes para obtener el promedio de irradiancia en el plano (POA)
        df_matriz = df_completo.groupby(['Hora_Str', 'Mes_Num', 'Mes_Str'])['POA_Original_W_m2'].mean().reset_index()

        # 3. Pivotar los datos para crear el formato de matriz (Filas: Horas, Columnas: Meses)
        df_pivot = df_matriz.pivot(index='Hora_Str', columns='Mes_Num', values='POA_Original_W_m2')
        
        # Reordenar las columnas usando los nombres de los meses en español
        nombres_columnas = [meses_es[m] for m in sorted(df_pivot.columns)]
        
        # 4. Construir el Heatmap con Plotly
        fig_avanzada = go.Figure(data=go.Heatmap(
            z=df_pivot.values,
            x=nombres_columnas,
            y=df_pivot.index,
            colorscale='Jet',  # La escala 'Jet' replica perfectamente el gradiente Azul -> Verde -> Amarillo -> Rojo de tu imagen
            colorbar=dict(title="Irradiancia (W/m²)"),
            hovertemplate="Mes: %{x}<br>Hora: %{y}<br>Irradiancia Promedio: %{z:.1f} W/m²<extra></extra>",
            text=np.round(df_pivot.values, 0), # Redondeamos los valores a mostrar
            texttemplate="%{text}",            # Esto dibuja los números dentro de las celdas
            textfont=dict(size=9, color="black") # Ajuste de fuente para legibilidad
        ))

        # 5. Configuración estética del layout
        fig_avanzada.update_layout(
            title="<b>MATRIZ DE IRRADIANCIA PROMEDIO HORARIA POR MES [W/m²]</b><br>Superficie Inclinada del Proyecto",
            xaxis=dict(title="Meses", side="top"), # Coloca los meses arriba
            yaxis=dict(title="Hora del Día", autorange="reverse"), # Invierte el eje Y
            height=600, 
            margin=dict(l=40, r=40, t=80, b=40)
        )

    fig_avanzada.update_layout(
        xaxis_title="Fecha y Hora",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=20, r=20, t=40, b=20)
    )
    
    st.plotly_chart(fig_avanzada, use_container_width=True)

# --- SECCIÓN DE EXPORTACIÓN Y DESCARGAS ---
st.markdown("---")
st.subheader("Descarga de Datos Estructurados")

# Preparación del archivo csv de salida en formato String para el componente de descarga
csv_data = df_analisis.drop(columns=['Fecha_Solo']).to_csv(index=False).encode('utf-8')

st.download_button(
    label="📥 Descargar Series Temporales Completas (CSV)",
    data=csv_data,
    file_name="generacion_y_demanda_anual.csv",
    mime="text/csv",
    use_container_width=True
)


