# Diccionario de salidas

Versión inglesa: [OUTPUT.md](OUTPUT.md). Reglas de cálculo: [ALGORITMO.md](ALGORITMO.md). Capa final opcional: [SHAPE.es.md](SHAPE.es.md).

Todas las claves de curva utilizan **fecha de referencia + product + region + unit + etiqueta de tenor**. Una región o unidad vacía es un valor literal de identidad. Los precios y errores absolutos usan la `unit` de su fila; no se convierten divisas. Los CSV usan UTF-8 con BOM, separador de coma y cabecera. Una celda numérica o de fecha vacía significa no disponible o no aplicable, nunca cero. Los precios cero y negativos son valores finitos válidos.

## Archivos

Las rutas siguientes son relativas a `[paths].output_dir`. Los archivos aparecen cuando el comando correspondiente tiene resultados; una comprobación de consistencia vacía puede no crear archivo.

```text
output/
  enriched_history.csv          Originales y puntos añadidos de las fechas procesadas
  enriched/YYYY-MM-DD.csv        Mismo esquema enriquecido para una fecha
  filled_history.csv            Puntos objetivo del motor de las fechas procesadas
  filled/YYYY-MM-DD.csv          Mismo esquema del motor para una fecha
  consistency_history.csv       Comparaciones disponibles de agregado frente a partes
  backtest_loo.csv               Predicciones individuales ocultando observaciones
  backtest_report.csv            Métricas del backtest
  tuning_calibration.csv         Puntuaciones de calibración de cada candidato
  tuning_validation.csv          Puntuaciones posteriores del candidato seleccionado
  tuning_selected.json           Propuesta de parámetros y metadatos de evaluación
  configurations/<id>.json       Documentos de contexto referenciados por las filas de curva
  _logs/vwaps_YYYY-MM-DD.log      Registro de ejecución de comandos
```

`daily` y `refill` conservan en el enriquecido los originales, incluidas curvas sin mapping, off o helper. `catchup` actualiza exclusivamente grupos fecha/curva `fill` activos pendientes y conserva intactos los grupos completos. No añade originales de curvas ajenas a esos grupos. Un objetivo procesado con `source=missing` ya cuenta como procesado. Recalcular sustituye en los históricos los grupos fecha/curva seleccionados, sin añadir otra versión. No existe un archivo `latest.csv`.

Catchup rechaza filas objetivo filled/enriched existentes con otro `eex_offset_days` dentro
del alcance activo de fechas/curvas/targets solicitado. Un offset antiguo ausente o vacío
equivale a cero. Para cambiar de política, usa refill explícito con el offset deseado u otra
carpeta de output; catchup no reetiqueta ni recalcula en silencio precios ya completados.

`make-synthetic` escribe `synthetic_vwaps.csv` y `synthetic_truth.csv` en su destino seleccionado; se describen al final. Los logs son registros de texto con fecha/hora, nivel y mensaje, no tablas de precios.

## Salida enriquecida: tabla original con procedencia

`enriched_history.csv` y los enriquecidos diarios comienzan con **todas las columnas originales**, con su nombre y orden originales. Se conservan sus valores y las filas duplicadas. Los nombres físicos de los campos lógicos `reference_date`, `product`, `region`, `unit`, `tenor`, `vwap`, `volume` siguen `[vwap_columns]`; por ejemplo, `tenor2` y `total_volume` pueden ser los nombres de entrada configurados. También se conserva una columna índice de origen o cualquier campo propio. Estos campos arbitrarios no tienen un diccionario universal fijo: mantienen el significado del proveedor.

| Campo de entrada o categoría | Fila original | Fila nueva añadida |
|---|---|---|
| Fecha de referencia | Se conserva su representación; ISO y día/mes/año se interpretan coherentemente | Formato según la primera representación utilizable de fecha en la entrada |
| Producto, región, unidad | Se conservan los valores originales | Identidad normalizada exacta de la curva |
| Tenor (`tenor2`, si se configura así) | Etiqueta original, incluso fuera de objetivos | Etiqueta objetivo configurada |
| VWAP (`vwap`, si se configura así) | Valor original, incluido texto inválido o vacío | Mismo precio que `curve_price`, o vacío si falta |
| Volumen (`total_volume`, si se configura así), `n_trades`, IDs de operación, índice/campos propios | Se conservan | Vacíos; nunca se inventan a partir del precio ni se copian de otra operación |
| `country`, `classification`, `currency`, `area`, `profile` | Se conservan | Solo se copian si ese campo tiene un único valor distinto no vacío en los originales de esta identidad completa |
| `weekday`, si existe | Se conserva | Día de la semana en inglés de la fecha de referencia |
| `periodicity_2`, si existe | Se conserva | `Daily`, `Weekend`, `BOW`, `Weekly`, `BOM`, `Monthly`, `Quarterly`, `Seasonal` o `Annual`, según el tipo de objetivo |

Los nombres de entrada `data_origin`, `estimation_method` y los que empiezan por `curve_` están reservados y se rechazan para evitar sobrescrituras silenciosas.

### Columnas específicas del enriquecido

| Columna | Tipo y valores | Significado |
|---|---|---|
| `data_origin` | Texto: `original`, `estimated`, `missing` | Procedencia del precio utilizable `curve_price`. Un VWAP original finito sigue siendo `original` salvo que shape cambie expresamente su precio utilizable, que pasa a `estimated`; su celda bruta se conserva, también si vale cero o negativo. Una fila original inválida puede recibir una estimación en `curve_price`. |
| `estimation_method` | Texto; reglas más abajo | `none` en todo original válido sin cambiar y en todo precio enriquecido no disponible; `shape_adjusted_original` si shape cambia el precio original; en otro caso, método que proporciona el precio. |
| `curve_reference_date` | Fecha ISO | Fecha interpretada, independiente del formato original. |
| `curve_product`, `curve_region`, `curve_unit` | Texto | Identidad normalizada de la curva. |
| `curve_tenor` | Texto | Etiqueta original en filas originales; etiqueta objetivo en filas añadidas. |
| `curve_price` | Número o vacío | Precio original, estimado o ausente utilizable. Es el campo que debe usarse cuando la columna VWAP original contiene texto inválido. |
| `curve_source` | Valores de source del motor | `own` para un original válido sin cambiar, aunque el motor no calcule esa fila; `own+shape` si se ajustó su precio de curva. En otro caso, source del motor o `missing`. |
| `curve_row_type` | Texto: `original`, `original_invalid`, `added` | Indica si la fila física existía, tenía VWAP inutilizable o se añadió. Independiente de la procedencia del precio. |
| `curve_flags` | Texto separado por `;` o vacío | Flags del motor más flags del enriquecido, descritos abajo. |

**Las demás columnas del motor de la sección siguiente se copian con el prefijo `curve_`**, salvo `data_origin` y `estimation_method`, que siguen las reglas del enriquecido. La traza normal completa incluye además `curve_area`, `curve_profile`, `curve_kind`, `curve_period`, `curve_delivery_start`, `curve_delivery_end`, `curve_hours`, `curve_confidence`, `curve_basis_mode`, `curve_configured_basis_mode`, `curve_configuration_mode`, `curve_configuration_id`, `curve_configuration_parameters`, `curve_own_vwap`, `curve_own_volume`, `curve_eex_settle`, `curve_eex_method`, `curve_eex_asof`, `curve_eex_offset_days`, `curve_eex_cutoff_date`, `curve_eex_fallback_trace`, `curve_basis`, `curve_basis_local`, `curve_basis_hist`, `curve_cross_adj`, `curve_local_weight`, `curve_anchors`, `curve_cross_from` y **`curve_flag`**. Si el motor no produjo filas, solo están garantizadas las columnas fijas del enriquecido; las trazas opcionales dependen de la tabla del motor recibida.

`curve_flag` copia sin cambios el `flag` singular del motor. `curve_flags` es el campo combinado del enriquecido. Son distintos. En originales válidos, `curve_price`, `curve_source` y, si existen, `curve_own_vwap`/`curve_own_volume` describen esa fila original concreta. Un ajuste original permitido de shape cambia solo el precio utilizable y su procedencia; quedan disponibles el dato bruto y sus diagnósticos propios. Las demás trazas describen el punto correspondiente del motor: pueden estar vacías o referirse a un agregado de originales con el mismo periodo de entrega. Que exista una fila original no significa que se hayan calculado todos sus diagnósticos.

Ejemplo ilustrativo, no observaciones reales de mercado; las tres filas pertenecen a una curva en EUR/MWh. `tenor2` es aquí el nombre configurado para tenor:

| `tenor2` | `vwap` original/de salida | `curve_price` | `data_origin` | `curve_row_type` | `curve_source` | `estimation_method` |
|---|---:|---:|---|---|---|---|
| M+1 | 100 | 100 | original | original | own | none |
| M+2 | 105 | 105 | estimated | added | eex+local | ratio_local |
| M+3 | invalid | vacío | missing | original_invalid | missing | none |

La tercera fila recibe también `original_vwap_missing_or_invalid`. Si pudiera estimarse, su `vwap` original seguiría siendo `invalid`; `curve_price` contendría la estimación y `data_origin` pasaría a `estimated`.

## Salida filled: las 39 columnas base del motor

Estas columnas aparecen en `filled_history.csv` y los filled diarios. Hay una fila por etiqueta objetivo resoluble y curva/fecha `fill` activa; shape activa también incluye etiquetas propias Month/Quarter/Year para contexto. Un objetivo no resoluble no genera fila. Varias etiquetas pueden representar el mismo periodo de entrega.

| Columna | Tipo / valores | Significado y casos vacíos |
|---|---|---|
| `reference_date` | Fecha ISO | Fecha de valoración/observación de entrada. |
| `product` | Texto | Identificador normalizado exacto del producto mapeado. |
| `region` | Texto, puede estar vacío | Componente literal de región de la identidad. |
| `unit` | Texto, puede estar vacío | Componente literal de unidad de la identidad y unidad de precios/errores. |
| `area` | Texto | Etiqueta de mercado del mapping. |
| `profile` | Texto | Etiqueta de perfil del mapping, habitualmente `Base` o `Peak`. Las horas siguen la convención hours del mapping, que es independiente. |
| `tenor` | Texto | Etiqueta relativa configurada: `M+1`, `WE`, `BOM`, `Q+1`, `Cal+1`, etc. |
| `kind` | Texto | `Day`, `Weekend`, `BOW`, `Week`, `BOM`, `Month`, `Quarter`, `Season`, `Year`. |
| `period` | Texto | Periodo absoluto: fecha, `WE YYYY-Www`, `YYYY-Www`, `YYYY-MM`, `YYYY-Qn`, `Sum-YY`, `Win-YY/YY`, `Cal-YYYY` o `BOW/BOM inicio..último-día`. |
| `delivery_start` | Fecha ISO | Inicio inclusivo de entrega. |
| `delivery_end` | Fecha ISO | Fin exclusivo de entrega. |
| `hours` | Número, >=0 | Horas según perfil, zona horaria y tipo de objetivo; incorpora las reglas aplicables de calendario/cambio horario. |
| `price` | Número finito o vacío | Precio del motor en `unit`; vacío cuando `source=missing`. |
| `source` | Texto | `own`, `eex+local`, `eex+hist`, `eex+cross`, `eex+smooth`, `arbitrage`, `missing`; los source cambiados por shape reciben `+shape`, incluido `own+shape`; véase más abajo. |
| `confidence` | Número, 0..1, o vacío tras cambio shape | Diagnóstico heurístico, **no probabilidad ni precisión calibrada**. Own=1, missing=0, arbitrage=0.4. Con EEX usa 0.4 para el respaldo EEX suavizado o `0.5+0.4*local_weight`; multiplica por 0.85 si el contrato no es exacto y por 0.9 si el settlement es anterior; redondea a tres decimales. |
| `data_origin` | Texto | `original` para own, `estimated` para precio calculado, incluido el respaldo EEX suavizado, y `missing` en otro caso. |
| `estimation_method` | Texto | `none` para own, `unavailable` para missing o método de cálculo descrito abajo. |
| `basis_mode` | Texto: `ratio`, `additive` | Modo efectivo seleccionado para este objetivo. En filas own/missing/eex+smooth no implica que se haya aplicado un ajuste. |
| `configured_basis_mode` | Texto: `auto`, `ratio`, `additive` | Configuración solicitada antes de la selección por objetivo. |
| `configuration_mode` | Texto: `global`, `individual` | Política usada para resolver parámetros; no es el modo ratio/aditivo. |
| `configuration_id` | Texto: SHA-256, 64 caracteres hexadecimales | Huella de los 34 campos efectivos del modelo. Iguales valores producen igual ID; no incluye rutas, inputs, código ni disponibilidad EEX. |
| `configuration_parameters` | JSON | Valores completos de los 33 controles escalares y `tenors`, incluidos los heredados. Permite auditar la configuración real de esa curva; no es una malla de candidatos. |
| `configuration_context_id` | Texto: SHA-256 | Huella del mapping y contexto operativo del receptor y, con CROSS activo, de configuraciones de helpers activos. Documento completo: `configurations/<id>.json` dentro del directorio de salida configurado. |
| `own_vwap` | Número o vacío | Precio propio del periodo, posiblemente agregado de duplicados/alias; vacío si no hay contrato propio utilizable con horas positivas. |
| `own_volume` | Número o vacío | Volumen asociado al periodo propio; puede estar agregado o no disponible. |
| `eex_settle` | Número o vacío | Precio EEX del periodo, exacto o reconstruido. No necesariamente un settlement cotizado directamente. |
| `eex_method` | Texto o vacío | `exact`, `strip` o `residual` de la referencia EEX. Si falla smoothing y el arbitraje resuelve, conserva este método si ya había referencia EEX; sin referencia, recibe el método de reconstrucción. `estimation_method=contract_*` identifica siempre la construcción final de arbitraje. |
| `eex_asof` | Fecha ISO o vacío | Publicación EEX admitida, nunca posterior a `eex_cutoff_date`. Puede preceder a `reference_date` o existir sin precio para este objetivo. Vacía si ninguna publicación cumple disponibilidad/antigüedad. |
| `eex_offset_days` | Entero <=0 | Offset de disponibilidad aplicado en días naturales. 0 permite publicación en la referencia; -1 la excluye. No desplaza las fechas de entrega. |
| `eex_cutoff_date` | Fecha ISO | Última fecha de publicación permitida: `reference_date + eex_offset_days`. Es un límite, no exige publicación en esa fecha. Tiene sentido aunque no haya precio EEX disponible. |
| `eex_fallback_trace` | JSON o vacío | Evidencia completa del respaldo `eex+smooth`; ver diccionario JSON siguiente. Vacío en otras ramas. |
| `basis` | Número o vacío | Ajuste final aplicado al EEX, después del límite ratio si procede. Cero significa ajuste real de cero; se distingue del vacío en own/arbitrage/missing/eex+smooth. |
| `basis_local` | Número o vacío | Ajuste local de las anclas de hoy antes de combinar; vacío si no está disponible o está desactivado. |
| `basis_hist` | Número o vacío | Ajuste histórico admitido por la política histórica; vacío si no existe, ha caducado, está desactivado o lo rechaza la evaluación automática. |
| `cross_adj` | Número o vacío | Ajuste entre curvas basado en sorpresas actuales elegibles y relaciones aprendidas; vacío si no existe. |
| `local_weight` | Número, 0..1, o vacío | Peso local de la combinación: `W/(W+shrink_k)` si hay evidencia local y cero en otro caso. Vacío si no se calculó ajuste EEX. |
| `anchors` | Texto separado por comas o vacío | Etiquetas de las **tres entradas de ancla con mayor peso local**, no todas las que contribuyen. Una entrada puede contener varias etiquetas alias. No exporta pesos ni la trazabilidad completa. |
| `cross_from` | Texto separado por comas o vacío | Etiquetas de otras curvas utilizadas con correlación redondeada a dos decimales: `product [region='...', unit='...'](0.85)`. No es una tabla de betas ni pesos. |
| `flag` | Texto separado por `;` o vacío | Condiciones del motor descritas abajo. Excluir un precio propio como ancla no lo cambia; la capa shape sí puede hacerlo con permiso explícito. |

En `basis`, `basis_local`, `basis_hist` y `cross_adj`, **ratio se expresa como fracción**: `0.05` significa +5%, y `price = eex_settle * (1 + basis)`. En additive se usa la unidad del precio: `0.05` significa +0.05 EUR/MWh en una curva EUR/MWh, y `price = eex_settle + basis`. No deben combinarse ajustes de distintos modos como si tuvieran la misma unidad. `local_weight` es adimensional en ambos modos. Los precios propios pueden tener vacíos los diagnósticos del ajuste. Con shape activa estas fórmulas explican `price_before_shape`; el precio final incorpora además `shape_adjustment`.

Ejemplo: la fila del lunes 05-10-2026 con offset -1 tiene `eex_cutoff_date=2026-10-04`.
Si la última publicación admitida es el viernes, `eex_asof=2026-10-02`: tiene **tres** días
de antigüedad respecto a `reference_date`, no dos respecto al corte. `max_stale_days=2`
la rechaza. En el enriquecido son `curve_eex_offset_days`, `curve_eex_cutoff_date` y
`curve_eex_asof`; pueden quedar vacíos en filas sin diagnósticos correspondientes del motor.

### JSON de auditoría del respaldo EEX suavizado

`eex_fallback_trace` contiene JSON en una celda CSV; en enriquecido se llama
`curve_eex_fallback_trace`. Está vacío fuera de un respaldo completado. Un fallo de ventana
se indica por `eex_fallback_unavailable`, sin inventar un cálculo parcial. Para leerlo con
Python, usa `json.loads(celda)` cuando la celda no esté vacía.

| Clave JSON | Significado |
|---|---|
| `reference_date`, `eex_asof` | Fecha calculada y última publicación admitida |
| `eex_offset_days`, `eex_cutoff_date` | Política de disponibilidad y última publicación permitida para ambas ventanas de precios y spreads |
| `target` | Objeto `kind, delivery_start, delivery_end` del periodo absoluto objetivo; fin exclusivo |
| `price_method`, `price_window`, `ewma_halflife`, `spread_window`, `anchor_months` | Los cinco controles efectivos usados, incluidos overrides |
| `spread_input` | `raw_same_publication_prices`: precios EEX crudos de la misma publicación para cada diferencia |
| `price_average.period` | Periodo promediado: objetivo si se promedia directamente, o último mes ancla si hay cascada |
| `price_average.value` | Media ponderada inicial |
| `price_average.observations[]` | Una entrada por publicación, en orden cronológico |
| `observations[].trade_date, price, eex_method, weight` dentro de `price_average` | Fecha, precio del periodo, reconstrucción exact/strip/residual y peso normalizado; los pesos suman 1 |
| `spread_steps[]` | Eslabones consecutivos de la cascada mensual; lista vacía en promedio directo |
| `spread_steps[].from_period, to_period` | Periodos absolutos anterior y siguiente, cada uno con `kind/delivery_start/delivery_end` |
| `spread_steps[].mean_spread` | Media simple del spread; misma unidad que el precio |
| `spread_steps[].price_before, price_after` | Valor acumulado antes y después de sumar ese spread |
| `spread_steps[].observations[]` | Evidencia de cada publicación para ese eslabón |
| `trade_date, from_price, to_price` dentro de cada observación de spread | Fecha compartida y precios crudos de ambos meses |
| `from_eex_method, to_eex_method` | Cómo se obtuvo cada precio: exact/strip/residual |
| `spread, weight` | Diferencia `to_price−from_price` y peso `1/spread_window` |

Auditoría: suma `price×weight` para obtener `price_average.value`; en cascada suma los
`spread×weight` de cada eslabón y comprueba cada `price_after`. El último valor debe ser
`price_before_shape` con shape activa, o `price` sin ella. No lo reconstruyas con `eex_settle*(1+basis)`: el settlement conserva la referencia
sin suavizar y `basis/local_weight` están vacíos. Consulta la sección 16 de [ALGORITMO.md](ALGORITMO.md)
para ventanas completas, M0/M1, ejemplo numérico y límites. Igualdad numérica con el último
settlement puede ocurrir; no significa que se haya copiado como respaldo directo.

### Fuentes y métodos

| `source` | Significado |
|---|---|
| `own` | VWAP propio del mismo periodo de entrega, incluidos alias de tenor admitidos. |
| `eex+local` | EEX con ajuste donde predomina la parte local; también puede contribuir histórico/cross. |
| `eex+hist` | EEX con ajuste donde predomina el histórico; también puede contribuir evidencia local. |
| `eex+cross` | EEX con ajuste donde predomina el previo e interviene información entre curvas. |
| `eex+smooth` | Respaldo EEX de medias completas de precios y, para meses lejanos, spreads. Nunca copia directa del settlement. |
| `arbitrage` | Reconstrucción final opcional con contratos propios/estimados disponibles cuando no hay referencia EEX utilizable o no puede suavizarse su respaldo sin ajuste. Desactivada por defecto; se activa con `arbitrage = true` en `[layers]`. No garantiza una curva completa libre de arbitraje. |
| `missing` | Sin estimación disponible, incluido el caso de cero horas de entrega. |

El orden depende de la información disponible, no de una clasificación intrínseca de calidad.
El basis histórico es un ajuste respecto a EEX: requiere una referencia EEX utilizable y una
historia permitida. `arbitrage = false` no desactiva la construcción de esa referencia mediante
`exact`/`strip`/`residual`; solo desactiva la reconstrucción posterior con contratos propios/estimados.
Los nueve casos se explican con ejemplos en la sección 0.9 de [ALGORITMO.md](ALGORITMO.md).

`source` resume la categoría. Para conocer componentes deben consultarse `estimation_method` y los diagnósticos. Los métodos del motor se construyen exactamente así:

- `none`: precio propio; `unavailable`: precio del motor ausente.
- `ratio_` o `additive_`, seguido de los componentes disponibles en este orden fijo: `local`, `history`, `cross`, separados por guiones bajos. Ejemplos: `additive_local_history` o `ratio_history_cross`. Describe componentes disponibles en el cálculo, no asegura que cada aportación numérica sea distinta de cero.
- `eex_price_simple`, `eex_price_ewma`: promedio directo del periodo. `eex_month_cascade_simple`, `eex_month_cascade_ewma`: media del mes ancla más spreads simples. El sufijo indica la media de precios, no la de spreads.
- `contract_exact`, `contract_strip`, `contract_residual`: reconstrucción final cuando `arbitrage` está activado. `exact` usa el periodo coincidente; `strip` combina periodos contiguos ponderados por horas; `residual` obtiene la cola de un contrato mayor usando su tramo inicial cubierto. Sus componentes pueden ser precios propios o ya estimados.
- El enriquecido convierte todo método no disponible en `none`. Si una fila añadida/original inválida recibe precio con `source=own`, el método es `own_equivalent_period`; se marca `estimated` porque esa fila física no contenía precio original utilizable. Reutiliza un periodo de entrega equivalente de la misma identidad y fecha de referencia, no el precio de ayer; no es arbitraje y sigue disponible con esa capa apagada. Todo original válido sin cambiar lleva `none`; un original ajustado por shape lleva `shape_adjusted_original`.

### Flags

Se unen mediante `;`. Vacío significa que no se indicó ninguna de estas condiciones. Pueden coexistir varios.

| Flag | Ubicación | Significado |
|---|---|---|
| `anchor_excluded` | Motor y enriquecido | El periodo propio se rechazó como ancla por los filtros configurados. No sustituye el precio original. En auto, la inestabilidad exclusiva de ratio se trata por modo y no genera necesariamente este flag. |
| `zero_delivery_hours` | Motor y enriquecido | El objetivo no tiene horas según su convención; el motor devuelve missing. El enriquecido conserva los originales. |
| `auto_additive_low_eex` | Motor y enriquecido | Auto eligió additive porque el valor absoluto del EEX objetivo está por debajo de `ratio_eex_floor`. |
| `auto_additive_no_ratio_anchors` | Motor y enriquecido | Auto eligió additive porque hoy solo existen anclas aditivas utilizables. |
| `auto_additive_history_only` | Motor y enriquecido | No hay anclas utilizables hoy y solo el histórico aditivo es utilizable según la política histórica. |
| `eex_fallback_unavailable` | Motor y enriquecido | No se pudo completar la ventana del respaldo EEX; missing por defecto, o reconstrucción final si `arbitrage` está activado. Puede persistir tras arbitraje válido. |
| `ratio_adjustment_limited` | Motor y enriquecido | El basis ratio final se limitó a `max_ratio_deviation`. |
| `unmapped_product` | Flags combinados del enriquecido | No existe mapping para la identidad completa product/region/unit. |
| `mapping_off` | Flags combinados del enriquecido | El mapping está desactivado. |
| `mapping_unassigned` | Flags combinados del enriquecido | El mapping no está off pero no tiene archivo EEX asignado. |
| `mapping_helper` | Flags combinados del enriquecido | El mapping asignado solo actúa como helper. |
| `original_vwap_missing_or_invalid` | Flags combinados del enriquecido | El VWAP de la fila física original es vacío, inválido o no finito. |
| `unit_missing` | Flags combinados, filas añadidas | La unidad de identidad está vacía; no se ha inventado otra. |
| `metadata_ambiguous:<column>` | Flags combinados, filas añadidas | Hay más de un valor original no vacío de ese metadato para la identidad; se deja vacío. `<column>` es country, classification, currency, area o profile. |

Los flags de motivo auto se emiten al calcular una estimación basada en EEX, no simplemente porque una fila propia tenga modo efectivo additive. La falta/antigüedad del EEX también se registra en logs; no existe un flag específico de EEX antiguo en este esquema. Debe compararse `eex_asof` con `reference_date`.

## Consistencia

`consistency_history.csv` solo contiene comparaciones disponibles: Quarter frente a meses, Season frente a trimestres y Year frente a trimestres. La ausencia de fila significa que no se produjo esa comparación, no prueba consistencia. Con shape off se informan diferencias sin reconciliar precios. Shape activa añade etapas y penalizaciones suaves; no garantiza igualdad.

| Columnas | Significado |
|---|---|
| `reference_date`, `product`, `region`, `unit` | Fecha e identidad completa. |
| `tenor`, `period` | Etiqueta del objetivo agregado y periodo absoluto. |
| `price`, `source` | Precio agregado y source del motor. |
| `from_parts` | Precio reconstruido con periodos menores ponderados por horas. |
| `deviation` | `price - from_parts`, en la unidad de la curva. |

## Backtest

`backtest_loo.csv` contiene predicciones individuales leave-one-out. Se oculta cada periodo propio elegible antes de predecirlo con la configuración; las alternativas diagnósticas pueden tener distinta disponibilidad. Las filas comparten observado pero cambian de método.

| Columna | Significado / valores |
|---|---|
| `reference_date`, `product`, `region`, `unit`, `tenor`, `kind` | Fecha, identidad completa y contrato oculto. `tenor` puede combinar etiquetas alias propias. |
| `eex_offset_days`, `eex_cutoff_date`, `eex_asof` | Offset fijo, corte calculado y publicación EEX realmente admitida en ese caso. LOO requiere una observación propia evaluable con EEX. |
| `group` | `short` (Day/Weekend/BOW/Week), `month` (BOM/Month), `quarter` (Quarter), `long` (Season/Year). |
| `own` | Precio propio oculto, posiblemente agregado por periodo de entrega. |
| `volume` | Volumen propio asociado; puede estar vacío. |
| `n_other_anchors` | Anclas restantes del conjunto resumen configurado; no cuenta todas las filas originales ni indica pesos por método. |
| `configured_basis_mode` | Configuración `auto`, `ratio` o `additive`. |
| `configuration_mode`, `configuration_id`, `configuration_parameters`, `configuration_context_id` | Configuración y contexto efectivos de la curva, con el mismo significado que en filled. Identifican el procedimiento configurado; algunos métodos diagnósticos pueden omitir deliberadamente sus interruptores. |
| `method` | `eex`, `pipeline_configured` o `local_<mode>`, `hist_<mode>`, `blend_<mode>`, `local_corr_<mode>`, `blend_corr_<mode>`, `hist_cross_<mode>`, con `<mode>` ratio/additive. Aparecen cuando hay datos y se ejecuta su rama. |
| `basis_mode` | Modo efectivo ratio/additive; vacío en la referencia `eex`. |
| `pred` | Predicción en `unit`. |
| `error` | `pred - own`; positivo significa sobreestimación. |

`pipeline_configured` aplica la configuración real, incluida shape activa, después de ocultar la observación y sus alias. Las alternativas con nombre propio son comparaciones diagnósticas: no todas siguen del mismo modo los interruptores/límites del cálculo desplegado.

El offset negativo retrasa el aprendizaje con parejas históricas exactas; no entrena mezclando
el propio de hoy con EEX anterior. CROSS sigue exigiendo EEX del mismo día para su sorpresa
actual, por lo que no aporta ajuste cross actual con offsets negativos. Véase la
[sección 20 del algoritmo](ALGORITMO.md).

`backtest_report.csv` agrupa por `unit`, `method`, `group`; `group=ALL` reúne los grupos de contratos dentro de la misma unidad. Las métricas absolutas mantienen esa unidad.

| Columna | Significado |
|---|---|
| `unit`, `method`, `group` | Claves de agrupación de las métricas. |
| `n` | Número de predicciones del método/grupo. |
| `mae`, `bias`, `rmse` | Error absoluto medio, error firmado medio y raíz del error cuadrático medio. |
| `n_paired` | Predicciones emparejadas con EEX por fecha/identidad completa/tenor. |
| `mae_paired`, `mae_eex_paired` | MAE del método y de EEX exactamente sobre esas observaciones comunes. Vacío si no existe métrica emparejada. |
| `mae_improvement` | `mae_eex_paired - mae_paired`; positivo indica mejora frente a EEX. |

Se redondean las métricas a cuatro decimales. `backtest --truth` muestra, sin guardar otro informe, `unit`, `source`, `n`, `mae`, `bias` frente a la verdad sintética, excluyendo celdas con precio propio.

## Calibración de parámetros

`tuning_calibration.csv` informa de todos los candidatos de una rejilla finita en fechas anteriores. `tuning_validation.csv` informa únicamente del ganador en fechas posteriores reservadas. Los parámetros quedan fijados antes de validar; las observaciones originales anteriores sí pueden actualizar el histórico cronológicamente. Ninguna salida modifica la configuración de producción.

La disponibilidad queda fija dentro de cada grid. Los metadatos incluyen `eex_offset_days`,
`eex_availability_policy` y `eex_staleness_origin`; el snapshot del config también contiene
`eex_offset_days`. Describen las publicaciones permitidas y que la antigüedad se mide desde
la referencia. Distintos offsets pertenecen a escenarios separados, no a filas de un grid.

Ambas tablas contienen:

| Columna | Significado / valores |
|---|---|
| `trial_id` | Posición del candidato en la rejilla, comenzando en 1. |
| `scope` | `overall` o `unit`. |
| `unit` | Unidad en filas unit; vacío en overall. |
| `n_baseline` | Universo de observaciones con verdad propia y benchmark EEX; mismo universo para candidatos de calibración. |
| `n_available`, `n_missing`, `coverage` | Predicciones finitas, abstenciones y `n_available/n_baseline` de ese candidato. |
| `eligible_for_selection` | Solo calibración: candidato con máxima cobertura, elegible para comparar score. |
| `n_paired`, `n_curves`, `n_paired_dates` | Observaciones comunes, identidades completas distintas y fechas con observaciones emparejadas. |
| `score` | Media con igual peso por curva del cociente MAE modelo / MAE EEX de cada curva. Menor es mejor; 1 iguala la referencia. Si MAE EEX=0, el cociente vale 1 si MAE modelo=0 y, en otro caso, infinito. |
| `normalized_skill` | `1 - score`; positivo indica mejora normalizada. |
| `reference_date_from`, `reference_date_to` | Primera y última fecha de observación asignada a calibración/validación; pueden abarcar más fechas que las que realmente tienen predicciones emparejadas. |
| `basis_mode` | Configuración del candidato: `auto`, `ratio`, `additive`; no es el modo efectivo por objetivo. |
| `tau_log`, `shrink_k` | Parámetros positivos del método candidato. |
| `layer_hist` | Política candidata: `auto`, `on`, `off`. |
| `layer_correlation`, `layer_cross` | Interruptores booleanos del candidato. |
| `mae_model`, `rmse_model`, `bias_model` | Errores absolutos del pipeline configurado en la unidad de filas unit; vacíos en overall para no mezclar divisas/unidades. |
| `mae_eex`, `rmse_eex`, `bias_eex` | Errores de referencia EEX con la misma regla y las mismas observaciones emparejadas. |
| `selected` | Booleano; true en el ganador de calibración y todas sus filas de validación. |

La selección prioriza máxima cobertura; entre esos candidatos minimiza score sobre la intersección de predicciones de TODOS los candidatos. `n_paired` puede ser menor que `n_available`; hacen falta dos fechas comunes. En validación solo se evalúa el ganador sobre sus pares disponibles. Con cero pares: cobertura 0 y score/errores no evaluables (NaN/vacío), sin inventar precisión.

`tuning_selected.json` contiene estas claves principales:

| Clave | Contenido |
|---|---|
| `selected_parameters` | Cambio de config propuesto: `method.{basis_mode,tau_log,shrink_k}` y `layers.{hist,correlation,cross}`. |
| `metadata` | Registro de evaluación detallado abajo. |
| `base_configuration` | Copia completa de Config: rutas resueltas, alias de campos, objetivos, parámetros del método/capas y convenciones. |
| `input_pattern` | Ruta/patrón real de VWAP utilizado. |
| `selection` | Texto que indica que solo calibración selecciona parámetros y validación se evalúa después. |
| `config_changed` | `false`; el comando no aplica automáticamente la propuesta. |

Todas las claves de `metadata`:

- `objective` = `mean_per_curve_mae_model_over_mae_eex`; `evaluated_method` = `pipeline_configured`; `baseline` = `eex`.
- `eex_offset_days`: offset fijo no positivo en días naturales; `eex_availability_policy` = `latest_publication_on_or_before_reference_date_plus_calendar_day_offset`; `eex_staleness_origin` = `reference_date`.
- `parameter_grid`: rejilla solicitada de campos y listas; `selection_scope`: máxima cobertura de calibración, después mejor score sobre casos comunes de la rejilla; `lower_score_is_better`: true; `tie_break`: `first_candidate_in_grid_order`.
- `calibration_start`, `calibration_end`, `validation_start`, `validation_end`: límites de fechas de observación asignadas. `calibration_days`, `validation_days`: número de fechas distintas asignadas, no duración en días naturales.
- `n_trials`, `selected_trial_id`, `calibration_score`, `validation_score`: número de candidatos, ID ganador y puntuaciones.
- `accuracy_scope` = `intersection_of_predictions_from_all_calibration_candidates`; `coverage_denominator` = `identical_eex_baseline_observations_with_held_out_truth`.
- `calibration_n_baseline`, `calibration_n_available`, `calibration_n_missing`, `calibration_coverage` y sus equivalentes `validation_*`: cobertura y abstenciones del ganador.
- `calibration_n_paired`, `validation_n_paired`: número de predicciones emparejadas.
- `calibration_paired_days`, `validation_paired_days`, `calibration_paired_start`, `calibration_paired_end`, `validation_paired_start`, `validation_paired_end`: fechas que realmente aportan observaciones comunes.
- `warmup_days` = 0; `validation_protocol` = `fixed_selected_parameters_with_chronological_original_history_updates`.

`base_configuration` contiene todos los campos de `Config`: los 34 del modelo listados en [CONFIGURATION.es.md](CONFIGURATION.es.md), más `base_dir`, `vwap_input`, `mapping_file`, `eex_curves_dir`, `output_dir`, `max_stale_days`, `warn_stale_days`, `eex_offset_days`, `timezones`, `day_convention`, `weekend_offset`, `warmup_days`, `vwap_columns`, `configuration_mode` y `command_overrides`. El último guarda la sustitución explícita de parámetros del modelo por CLI; no es otro Excel ni una malla. Son configuración, no resultados medidos; véanse [ALGORITMO.md](ALGORITMO.md) y [config.toml](config.toml). Las rutas se convierten en texto. Los diagnósticos no finitos se guardan como JSON `null`; los score CSV pueden mostrar `inf`/`-inf` en el caso anterior de referencia con error cero. Esto no permite precios de producción no finitos.

## Archivos sintéticos

Son datos explícitamente simulados, nunca operaciones reales. `synthetic_vwaps.csv` contiene `reference_date`, `weekday`, `product`, `country`, `region`, `classification`, `unit`, `periodicity_2`, `tenor2`, `vwap`, `total_volume`, `n_trades`. Sus significados coinciden con el diccionario de entrada anterior, pero se generan precios, volúmenes, operaciones y disponibilidad de observaciones. `reference_date` usa día/mes/año, `country` usa el área mapeada y classification es `<profile> load`.

`synthetic_truth.csv` contiene `date` (fecha de referencia), `product`, `region`, `unit`, `tenor`, `period`, `delivery_start`, `delivery_end`, `truth` (precio completo simulado) y `eex` (precio EEX usado para generarlo). Los alias del mismo periodo comparten una sola verdad. Ambos precios usan la unidad de la curva.

<a id="shape-output"></a>

## Shape activa: 11 columnas adicionales del motor

Con `shape.mode=off` se conserva el esquema anterior de 39 columnas. Audit/adjust añaden
estas 11 (50 columnas del motor en total). En el enriquecido todas llevan prefijo `curve_`,
incluidas `curve_data_origin_before_shape` y `curve_estimation_method_before_shape`.
Una fila sin punto correspondiente del motor puede tener trazas vacías. Shape activa puede
incorporar etiquetas propias Month/Quarter/Year completas fuera de los objetivos para el
contexto del día; no inventa precios ausentes.

| Columna del motor | Significado |
|---|---|
| `price_before_shape` | Precio previo del motor; vacío en missing. En un original físico válido, `curve_price_before_shape` es su VWAP numérico individual, no el agregado del periodo. |
| `source_before_shape` | Procedencia previa. Enriquecido: `own` para cada original válido. |
| `estimation_method_before_shape` | Método previo. Enriquecido: `none` para cada original válido. |
| `data_origin_before_shape` | Origen previo. Enriquecido: `original` para cada original válido. |
| `shape_mode` | `audit` o `adjust` de esta ejecución. |
| `shape_status` | Estado descrito debajo. Un residuo no implica por sí solo fallo. |
| `shape_adjustment` | Cambio realmente aplicado, en `unit`. Cero en audit y filas sin cambio/con fallo. |
| `shape_proposed_price` | Precio propuesto validado, también en audit. Missing sigue vacío; una propuesta fallida vuelve al precio previo. En originales físicos: VWAP de la fila más delta propuesto de su periodo. |
| `shape_proposed_adjustment` | Propuesta menos precio previo; cero si no se propone movimiento válido. |
| `shape_original_modified` | True solo si un nodo original del motor cambia realmente en adjust. No significa sobrescritura del input; un alias añadido puede heredar esta traza del periodo. False en audit. |
| `shape_trace` | JSON de toda la resolución fecha/curva. Compartido entre sus filas, no evidencia independiente por fila. |

Los nodos propios cambiados tienen `source=own+shape`, `data_origin=estimated` y
`estimation_method=shape_adjusted_original`. En estimados cambiados se añade `+shape`
a source y método: por ejemplo `eex+local+shape` / `ratio_local+shape`.
Su confidence queda vacío. Las filas sin cambio y audit conservan procedencia/confidence
finales. Basis, anclas y respaldo explican `price_before_shape`; tras un cambio no bastan
para reconstruir `price`.

El `vwap` original bruto nunca cambia. Si dos duplicados físicos valen 118 y 122 y se acepta
un delta agregado +2, sus curve_price serán 120 y 124 y sus precios previos 118 y 122.
El JSON sigue describiendo el nodo agregado común. Audit aplica la misma regla a propuestas.

### Estados shape

| `shape_status` | Significado |
|---|---|
| `missing` | No había precio finito; esta capa no lo crea. |
| `out_of_scope` | Tipo fuera de Month/Quarter/Year completos. |
| `incomplete_period` | Periodo completo inválido/horas no positivas, o agregado sin los meses necesarios para coherencia activa. |
| `missing_reference` | Mes sin término aplicable y sin referencia EEX utilizable. No impide una restricción de agregado completo sin EEX. |
| `no_constraints` | Nodo sin término aplicable activado; incluye meses consecutivos ausentes o publicaciones incompatibles. |
| `original_preserved` | Nodo original participante fijado por `adjust_originals=false`. |
| `unchanged` | Resolución válida sin movimiento material del nodo. |
| `audit_proposed` | Propuesta válida distinta del previo; audit conserva precio final. |
| `adjusted` | Ajuste válido aplicado. |
| `solver_failed` | Fallo numérico/de convergencia/validación; conserva precios previos. |
| `alias_conflict` | Alias elegibles discrepan en precio u horas; se rechaza la resolución de la curva y se conservan precios. |

Los estados tienen prioridad por rama: un original fijo sin término aplicable puede indicar
`no_constraints`, no `original_preserved`. Filas missing/fuera de alcance pueden compartir
el trace del grupo.

### Diccionario JSON de shape

Lee `shape_trace` / `curve_shape_trace` con `json.loads`. Algunas trazas de fallo o sin
términos omiten campos que no llegaron a calcularse; `null` indica no disponible.

| Clave JSON | Significado |
|---|---|
| `parameters` | Siete controles efectivos, incluidas opciones de esta ejecución. |
| `reference`, `reference_description` | `eex_settle`: EEX admitido actual, no el respaldo temporal. |
| `original_policy` | `fixed` o `movable_with_penalty`. |
| `smoothness_grid` | Tres meses naturales consecutivos, índice mensual uniforme. |
| `nodes[]` | Un registro por periodo absoluto elegible deduplicado. |
| `nodes[].period` | `Kind:delivery_start/delivery_end`, fin exclusivo. |
| `nodes[].price_before, price_proposed, eex_reference` | Precios/referencia del nodo agregado; pueden diferir de una fila física duplicada. |
| `nodes[].original, alias_count, bound_hit` | Si algún alias es propio, número de alias del motor y si el cambio propuesto toca el límite. |
| `constraints[]` | Penalizaciones activadas aplicables, no igualdades exactas garantizadas. |
| `constraints[].type` | `basis_second_difference` o `month_aggregate`. |
| `constraints[].coefficients` | Mapa periodo→coeficiente: `1,-2,1` en forma; horas mensuales normalizadas positivas y padre `-1` en agregación. |
| `constraints[].weight` | Peso configurado sin normalizar. |
| `constraints[].residual_before, residual_after` | Residuo en unidad de precio previo y después de la propuesta validada; en audit “after” es propuesto. En agregación, signo promedio mensual menos padre. |
| `solver.method` | `bounded_coordinate_descent` cuando se ejecuta el solver. |
| `solver.converged, iterations, kkt_residual` | Diagnóstico de aceptación/convergencia en el problema escalado. No es confianza predictiva. |
| `solver.reason` | Motivo cuando faltan términos o falla, por ejemplo alias contradictorios o validación numérica. |
| `objective_before, objective_after` | Pérdida conjunta en unidades normalizadas, cuando se calcula. |
| `objective_price_scale, objective_weight_scale, objective_units` | Se divide la pérdida por escala de precio al cuadrado y escala de pesos; sirven para reconstruirla, no son una métrica monetaria de error. |

Con shape activa, consistencia añade `shape_stage`: `before` (previo), `after` (publicado)
y también `proposed` en audit. En audit, precios before/after coinciden. El informe mantiene
sus comparaciones Quarter/meses, Season/trimestres y Year/trimestres. La penalización interna
Year/meses de shape está en `shape_trace` y no tiene por qué coincidir con esa comparación.

Las predicciones `pipeline_configured` incluyen shape tras ocultar el periodo evaluado.
Los diagnósticos `local_*`/`hist_*` no necesariamente la incluyen. Tune por CLI no añade dimensiones
de shape: guarda sus valores fijos en el snapshot. [SHAPE.es.md](SHAPE.es.md) detalla reglas
y controles.

### Diagnóstico de tolerancia agregada

`shape.coherence_tolerance` empieza en 0.01 en la unidad de la fila y solo cambia diagnósticos. Los agregados de `shape_trace.constraints` añaden los booleanos `within_tolerance_before` y `within_tolerance_after`. La traza añade `coherence_within_tolerance_before` y `coherence_within_tolerance_after`: booleanos para todos los agregados aplicables o null si no hay ninguno. En audit, “after” es la propuesta. Los flags `shape_coherence_outside_tolerance` (adjust) y `shape_proposal_outside_coherence_tolerance` (audit) señalan nodos de una relación fuera del margen. Estar fuera puede ser una solución válida con límites y penalizaciones suaves; no garantiza igualdad exacta ni aproximada.

La comparación usa `abs(residual) <= coherence_tolerance + 1e-10` para admitir redondeo numérico.

## Archivos de experimentos MLflow

El notebook guarda estos archivos en la ejecución MLflow seleccionada, separados de los
outputs de producción de `[paths].output_dir`. Cada candidato tiene una ejecución hija:

| Archivo de la hija | Cuándo se guarda | Esquema y finalidad |
|---|---|---|
| `curves/calibration_filled.csv` | Cada etapa de motor de calibración completada, con registro activo | El mismo esquema filled: **39 columnas base** más **11 de shape** si está activa (50 total). Curvas completas con originales visibles para revisión gráfica. |
| `curves/validation_filled.csv` | Solo etapa de validación completada del ganador, con registro activo | El mismo esquema filled, para el intervalo reservado. Los candidatos perdedores no tienen curva de validación. |
| `predictions/calibration_paired_predictions.csv` | Si se activa el registro individual | Verdad propia ocultada, predicciones modelo/EEX, errores, claves y metadatos de disponibilidad EEX; no es un CSV filled de curva completa. |
| `predictions/validation_paired_predictions.csv` | Solo el ganador, si se activa | Casos de validación ocultados y emparejados, no la curva con sus propios visibles. |

Las curvas completas excluyen el output de calentamiento y cubren el intervalo de la etapa.
Pueden contener fechas/targets que no sean casos individuales de LOO. Mantienen originales
visibles: **mide precisión con predicciones emparejadas/métricas y revisa forma/coherencia
con las curvas completas**. Que exista un archivo de etapa no demuestra que todo el run terminase bien.

`LOG_PREDICTIONS=False` no guarda CSVs de predicciones emparejadas ni de curvas completas.
Sí conserva informes CSV agregados y JSON de configuración/auditoría. Si un run antiguo no
tiene curvas, se informa de su ausencia; el visor no las reconstruye con datos actuales.
La [guía de archivos y visor MLflow](MLFLOW.es.md#saved-curves) detalla navegación, descargas,
selección de etapas y acceso independiente a experimentos guardados.

### Procedencia de configuración global e individual

Las cuatro columnas de configuración se generan en ambos modos, también para precios originales y ausentes calculados por el motor. El enriquecido las copia con prefijo `curve_`, incluido `curve_configuration_context_id`; pueden faltar diagnósticos en originales sin fila correspondiente del motor. Véase [CONFIGURATION.es.md](CONFIGURATION.es.md). Catchup comprueba IDs de modelo y contexto y rechaza configuraciones guardadas incompatibles. Ampliar solo objetivos está permitido y recalcula los grupos afectados con tenors pendientes. Las filas globales antiguas sin ID mantienen el comportamiento anterior; individual exige refill explícito porque no conoce sus parámetros originales.

`configuration_id` identifica los 34 valores propios de esa curva. `configuration_context_id` permite detectar además cambios relevantes de helpers, calendario y disponibilidad. Por ejemplo, cambiar la memoria histórica de un helper puede cambiar un receptor CROSS aunque su ID de modelo propio no cambie. La publicación normal guarda el contexto completo en `configurations/<configuration_context_id>.json`; consérvalo con el CSV, inputs y versión de código. Estos IDs no calculan una huella del contenido de los archivos de entrada.

| Clave del JSON de contexto | Contenido |
|---|---|
| `schema_version` | Entero `1`, versión del formato del documento. |
| `receiver` | `product`, `region`, `unit`, `use`, `area`, `profile`, `eex_file`, `hours` y `timezone` de la curva receptora. |
| `operations` | Valores fijos `eex_offset_days`, `max_stale_days`, `day_convention`, `weekend_offset` y `warmup_days`. |
| `cross_helpers` | Lista vacía si CROSS está apagado en el receptor. En otro caso, las demás curvas activas con sus campos de mapping y todos sus `model_parameters`, ordenadas por identidad. Incluye helpers potenciales aunque no aporten señal en una fecha concreta. |

Los 34 parámetros propios del receptor permanecen en `configuration_parameters`, sin duplicarse en el contexto. Así ampliar solamente objetivos conserva el ID de contexto. Hay que interpretar juntos el ID de modelo y el ID de contexto.
