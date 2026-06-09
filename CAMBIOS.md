# Registro de Cambios — appv2.py
## Entregable Final: Dashboard de Resiliencia Energética y Continuidad

---

### Arquitectura General

La aplicación fue reescrita completamente sobre `appv2.py` (el archivo `app.py` original fue descartado).
El principio de diseño central es **UX progresivo**: la Sección 1 es siempre visible y reactiva;
las Secciones 2-4 se activan condicionalmente según lo que el usuario configure.

---

### Sección 1 — Diagnóstico Climático (Siempre Visible)

**Cambios respecto a app.py:**
- Se separaron los inputs de geolocalización y geometría **fuera del formulario** para que la gráfica de irradiancia se actualice de forma reactiva al mover los sliders de Tilt/Azimut, sin necesidad de presionar el botón de simulación.
- Se implementó la función `calcular_tilt_optimo(lat)` que devuelve `|latitud|` redondeada como ángulo óptimo para instalación fija, y se usa como valor por defecto del slider de inclinación.
- Se creó `calcular_seccion1(lat, lon, alt, tz, tilt, azimut)` decorada con `@st.cache_data` que:
  - Genera 96 timesteps del 21 de junio (solsticio de verano, día de mayor irradiancia).
  - Usa `pvlib.location.Location.get_clearsky(model='ineichen')` para obtener GHI/DNI/DHI de forma dinámica (corrige el bug del `airmass_absolute=1.5` hardcodeado en app.py).
  - Transpone GHI→POA con `pvlib.irradiance.get_total_irradiance()` al tilt/azimut elegido.
  - Devuelve `kwh_1panel`: la producción estimada de un panel estándar (1.95 m², η=21.8%) ese día.
- KPIs siempre visibles: ángulo óptimo calculado, kWh/día de un panel, irradiancia pico estimada.
- La gráfica muestra las cuatro curvas (GHI, DNI, DHI, POA) en colores diferenciados con el tema ámbar del dashboard.

---

### Sección 2 — Balance Solar vs. Demanda Industrial (Condicional tras simulación)

**Cambios respecto a app.py:**
- Se añadió selector `modo_demanda` (radio button) en la barra lateral:
  - **"Histórico STREGER"**: usa `generar_demanda_historica_streger()`, que escala el perfil sintético para que los kWh mensuales coincidan con los 13 meses de consumo real del recibo CFE (mayo 2026). Demanda máxima fijada en 80 kW con multiplicador 80, semilla `np.random.seed(42)`.
  - **"Sintético"**: permite configurar demanda máxima y factor de planta libremente.
- Se corrigió la falta de semilla aleatoria en `generar_demanda_industrial()` añadiendo `np.random.seed(42)` para resultados reproducibles entre ejecuciones.
- El DataFrame de resultados incluye columnas: `Demanda_kW`, `Demanda_kWh`, `POA_Original_W_m2`, `Generacion_Con_Sombra_kW`, `Generacion_Sin_Sombra_kW`, `Fraccion_Sombra_Arreglo`, `Generacion_Energia_kWh`, `Excedente_Deficit_kW`, `Ahorro_Monetario`, `Mes`, `Mes_Num`.
- Se corrigió el cálculo de masa de aire en `simular_sistema_fv()` usando `pvlib.location.Location` en lugar del valor fijo `airmass_absolute=1.5`.
- Se añadió sombreado inter-filas via `pvlib.shading.shaded_fraction1d()` con parámetros configurables: distancia entre filas, longitud de panel, número de filas.
- **KPI Row 1**: Energía Solar Anual, Consumo Planta Anual, Potencia DC instalada, Generación FV pico.
- **KPI Row 2**: Cobertura Solar (%), Balance Neto (kWh).
- **Módulo Express**: checkbox `"¿Deseas ingresar el costo promedio por kWh?"` → si activo muestra 3 KPIs: costo actual anual, costo con paneles, ahorro estimado.
- **Gráfica de series temporales** con filtro de fechas (slider range).
- **Histograma mensual** de barras agrupadas: Demanda vs Generación FV por mes.

---

### Sección 3 — Ingeniería de Resiliencia ante Apagones (Condicional)

**Cambios respecto a app.py (nueva sección):**
- Se añade slider `horas_respaldo` (0–24 h, default 0). La sección solo se activa cuando `horas_respaldo > 0`.
- Input `carga_critica_kw` visible únicamente cuando `horas_respaldo > 0`.
- Cálculos con parámetros de tecnología LiFePO4:
  - `DOD_LIFEPO4 = 0.80` — profundidad de descarga máxima recomendada.
  - `CAPACIDAD_MODULO = 5.0` kWh — módulo estándar de referencia.
  - `CICLOS_MIN = 4000`, `CICLOS_MAX = 5000` — rango de ciclos garantizados.
  - `capacidad_requerida_kwh = horas_respaldo × carga_critica_kw`
  - `capacidad_instalada_kwh = requerida / DOD` (sobre-dimensionamiento por DoD)
  - `n_modulos = ceil(instalada / 5.0)`
  - `vida_min_anos = round(4000/365)`, `vida_max_anos = round(5000/365)` (≈ 11–14 años)
- Cuatro KPI cards con tema morado: capacidad requerida, capacidad instalada, módulos necesarios, vida útil.
- Caja de especificaciones técnicas: tabla informativa con todos los parámetros del banco BESS propuesto.

---

### Sección 4 — Evaluación Financiera de Continuidad de Negocio (Condicional)

**Cambios respecto a app.py (nueva sección):**
- Activación mediante `st.expander("🔓 Activar análisis de rentabilidad avanzado")`.
- **Inputs**:
  - `costo_sistema_solar` — inversión total solar (default auto-calculado: kWp × $15,000 MXN/kWp).
  - `reparaciones_anuales` — costo histórico anual por apagones (default $150,000 MXN/año).
  - `costo_bess` — cotización real del banco de baterías (pre-llenado desde Sección 3 si aplica: `n_modulos × $18,000 MXN/módulo`).
  - `tasa_descuento` — slider 0–25%, default 8% (TIIE aproximada), para cálculo de VPN.
- **Cálculos financieros**:
  - Flujos anuales discretos a 25 años para dos escenarios: Solo Solar y Solar + BESS.
  - Cashflow acumulado via `np.cumsum()`.
  - Payback simple: primer año en que el flujo acumulado ≥ 0.
  - VPN: `Σ [flujo_t / (1+r)^t]` para t = 0..25.
- **KPI Row**: Inversión total, Payback Solar, Payback Solar+BESS, VPN Solar (con color verde/rojo).
- **Gráfica de cashflow dual-eje**:
  - Barras (eje izquierdo): flujo anual discreto por escenario (rojo año 0 = inversión, ámbar/morado años 1-25 = ahorros). BESS se muestra como barras incrementales apiladas.
  - Líneas (eje derecho): cashflow acumulado con marcador de punto de equilibrio (línea punteada en y=0).
  - `barmode='relative'` para permitir barras negativas.
- **Trade-off Tecnológico**:
  - Costo del kWh solar a lo largo de la vida útil: `costo_solar / (ahorro_energía/precio × 25 años)`.
  - Comparativa BESS: `ahorro_reparaciones` vs `amortización_anual_BESS` con ratio y recomendación automática (verde/rojo).
  - Responde a las preguntas del entregable: ¿más paneles o enfriamiento activo? ¿paneles FV o BESS como prioridad?

---

### Constantes y Datos de STREGER S.A. (Coatepec, Veracruz)

Precargados en el código para el modo "Histórico STREGER":

| Constante | Valor | Fuente |
|-----------|-------|--------|
| `STREGER_KWH_MENSUAL` | Diccionario 12 meses | Recibo CFE Mayo 2026 (historial 13 meses) |
| `STREGER_DEMANDA_MAX_KW` | 80 kW | Recibo CFE Mayo 2026 |
| `STREGER_PRECIO_MEDIO` | $2.67 MXN/kWh | Análisis recibo (todos los cargos / kWh) |
| Tarifa | GDMTO | Gran Demanda Media Tensión Ordinaria |
| Multiplicador medidor | 80× | Recibo CFE Mayo 2026 |
| Consumo mayo 2026 | 9,520 kWh | Recibo CFE Mayo 2026 |
| Factura mayo 2026 | $29,591 MXN | Recibo CFE Mayo 2026 |

---

### Correcciones de Bugs Respecto a app.py

| Bug | Descripción | Corrección |
|-----|-------------|------------|
| `airmass_absolute=1.5` hardcodeado | La masa de aire no variaba con la posición solar ni la altitud | Reemplazado por `pvlib.location.Location.get_clearsky('ineichen')` |
| `np.random.seed()` ausente | La curva de demanda cambiaba en cada recarga | Añadido `np.random.seed(42)` en `generar_demanda_industrial` y `generar_demanda_historica_streger` |
| `st.session_state.get('divisa', 'MXN')` | KeyError en primera carga antes de simulación | Uso de `.get()` con valor por defecto |

---

### Archivos Modificados

- `appv2.py` — Único archivo modificado. `app.py` no fue tocado (discartado por decisión de equipo).

### Archivos de Referencia (solo lectura)

- `CONTEXTO/Requisitos Entregable 1.htm`
- `CONTEXTO/Requisitos Entregable 2.htm`
- `CONTEXTO/Recibo CFE Mayo 2026.pdf`
- `CONTEXTO/Analisis Recibo Mayo 2026.pdf`
