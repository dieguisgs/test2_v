# Configuración global o individual del modelo

[English](CONFIGURATION.md) · [Algoritmo](ALGORITMO.md) · [Backtest](BACKTEST.es.md) · [MLflow](MLFLOW.es.md)

Hay dos decisiones diferentes. **La configuración de producción** indica los valores fijos
con los que se calcula cada curva. **El alcance del experimento** indica si una búsqueda
selecciona un candidato común o uno distinto para cada producto. El Excel del mapping guarda
valores fijos; no guarda listas de candidatos ni recibe automáticamente al ganador.

## 1. El selector de producción

Edita la sección existente de `config.toml`:

```toml
[run]
configuration_mode = "global"
warmup_days = 0
```

| Valor | Qué hace el motor |
|---|---|
| `global` (predeterminado) | Utiliza los parámetros del TOML para todos los productos. Ignora las celdas opcionales de parámetros del mapping, incluso si contienen valores inválidos. |
| `individual` | Parte del TOML y aplica las celdas de parámetros no vacías de cada fila. Una celda vacía hereda el valor global. |

Puedes cambiarlo para una ejecución sin editar el TOML:

```powershell
python run.py daily --configuration-mode individual
python run.py refill --from 2026-09-01 --to 2026-10-06 --configuration-mode individual
python run.py catchup --configuration-mode individual
python run.py backtest --configuration-mode individual
```

Usa `--configuration-mode global` para ignorar las celdas individuales durante esa ejecución.
Eso no desactiva el mapping: siguen aplicándose identidad, `use`, archivo EEX, horas y zona
horaria. Los estados históricos siguen separados por curva en ambos modos; compartir
parámetros no mezcla los precios de todos los productos en una media histórica común.

## 2. Una fila de Excel/CSV es una configuración fija de producto

La identidad es la tupla exacta **`product + region + unit`**. Dos filas del mismo producto
con regiones o unidades diferentes pueden tener parámetros distintos. Una región vacía es
un valor literal, no un comodín. La columna `profile` conserva su significado Base/Peak u
otro perfil de entrega; no es un perfil de configuración del algoritmo.

El mapping admite `.csv` y `.xlsx`. En Excel se lee la **primera hoja**. Se mantienen sus
columnas de identificación y metadatos, y las columnas opcionales se llaman como los campos
Python del apartado 4: por ejemplo `tau_log`, `fallback_price_window` y `shape_mode`.

```powershell
python run.py mapping --with-parameters
```

Añade las 34 columnas admitidas al mapping configurado, conservando valores manuales y
metadatos. Las nuevas celdas quedan vacías. Al actualizar un Excel se conservan las otras
hojas. Para crear una copia nueva en Excel sin sustituir el archivo configurado:

```powershell
python run.py mapping --with-parameters --export mappings/products.individual.xlsx
```

Después edita la sección `[paths]` existente:

```toml
[paths]
mapping = "mappings/products.individual.xlsx"
```

Exportar no cambia automáticamente `paths.mapping`; el comando indica la ruta que debes
poner. [El ejemplo ficticio](mappings/products.individual.example.csv) contiene las 34
columnas y filas desactivadas. Sustituye identidades y asignaciones EEX por las tuyas y revisa
`use` antes de ejecutar: no es un mapping real ni una recomendación calibrada.

Supón que globalmente tienes `tau_log=0.5`, `layer_hist="auto"` y
`fallback_price_window=5`:

| Producto | Celda `tau_log` | Celda `layer_hist` | Celda `fallback_price_window` | Valores efectivos en individual |
|---|---|---|---|---|
| A | vacía | vacía | vacía | 0.5, auto, 5 |
| B | 0.8 | off | vacía | 0.8, off, 5 |
| C | vacía | vacía | 9 | 0.5, auto, 9 |

En global los tres usan 0.5, auto, 5. Estos números explican la herencia; no demuestran que
una configuración estime mejor los precios.

### Sintaxis y validación de celdas

- Vacío significa heredar. Cero y `false` son valores explícitos, no celdas vacías.
- Los booleanos usan `true` o `false`, sin importar mayúsculas; también valen los booleanos
  nativos de Excel. No uses `0`, `1`, `yes` u `off` para un booleano. `layer_hist` es un
  control de texto que sí admite específicamente `on`, `off` o `auto`.
- Los decimales usan punto, por ejemplo `0.5`. Los enteros usan `5`, no `5.0` como texto CSV.
- Las opciones de texto usan las palabras indicadas, sin añadir comillas literales a la celda.
- `tenors` usa una celda con un array JSON, por ejemplo `["M+1", "M+2", "Q+1"]`. Al guardar
  CSV se escapan sus comillas con las reglas normales del formato; lo hace la hoja de cálculo.
- Una celda escalar fija no debe contener una lista de búsqueda como `[0.3, 0.8]`.
- En individual se rechazan columnas desconocidas y valores inválidos, indicando fila y
  parámetro. Usa `comment` para notas: escribir por error `tau_logs` no debe pasar inadvertido.
  También se validan filas desactivadas, para poder activarlas después de forma consciente.

El orden exacto de prioridad es:

```text
Valores del TOML
  -> celdas no vacías del mapping, solo en individual
  -> opciones explícitas de modelo pasadas por línea de comandos
  -> valores explícitos del candidato en un experimento
```

Por ejemplo, una fila puede poner `shape_adjust_originals=true`, pero pasar explícitamente
`--shape-adjust-originals off` lo sustituye durante ese comando. Omitir una opción CLI no
borra el valor del mapping. Ni el CLI ni un candidato modifican las celdas del Excel.

## 3. Diario, refill histórico y catchup usan la misma configuración

El motor resuelve la configuración completa de cada curva antes de aprender su estado
histórico o rellenar sus fechas. Aplica la misma regla al diario, refill histórico, catchup
y backtest del procedimiento configurado. Los helpers también reciben sus propios valores,
que pueden afectar a la evidencia que aportan mediante CROSS.

Cambiar una celda afecta a nuevos cálculos; no reescribe silenciosamente la historia guardada.
Catchup comprueba los identificadores del modelo y del contexto de cálculo en las filas
existentes solicitadas por curva, fecha y tenor. Si los valores son incompatibles, rechaza
mezclar configuraciones e indica hacer un refill explícito. Ampliar solo la lista objetivo
es una excepción admitida: los nuevos tenors pendientes provocan recalcular los grupos
fecha/curva afectados. Las filas globales antiguas sin ID mantienen el comportamiento
anterior de catchup; individual no puede deducir su configuración y requiere refill.
Catchup no da por actualizado un precio antiguo
solo porque la fecha ya exista. Conserva un directorio de salida separado si comparas
políticas de producción. Una fila guardada como `missing` también cuenta como procesada:
para recalcularla con información nueva hace falta `daily` o `refill`.

Cada fila calculada incluye:

| Columna | Significado |
|---|---|
| `configuration_mode` | `global` o `individual`: cómo se resolvieron los parámetros de producción. |
| `configuration_id` | Huella SHA-256 de todos los valores efectivos del modelo. |
| `configuration_parameters` | JSON con los 34 campos efectivos, incluidos los heredados. |
| `configuration_context_id` | Huella del contexto de cálculo de la curva, incluidos parámetros relevantes de helpers cuando CROSS está activo. |

En el enriquecido llevan el prefijo `curve_`. Los mismos valores producen el mismo ID aunque
cambie producto, modo o ubicación de los archivos. Cambiar un valor del modelo cambia el ID;
cambiar una ruta no. Es una huella de configuración, no del código, los inputs ni la política
de disponibilidad EEX. Conserva esas evidencias por separado; EEX mantiene sus columnas de
offset, corte y publicación utilizada. El ID de contexto separado incluye mapping,
calendario/disponibilidad y, cuando CROSS está activo, parámetros efectivos de las otras
curvas activas. Permite detectar cambios de un helper aunque los 34 valores propios del
receptor no cambien. Su documento completo se guarda en
`configurations/<configuration_context_id>.json`, dentro del directorio de salida
configurado. El contexto no conserva el contenido de los archivos de entrada.
Véase [OUTPUT.es.md](OUTPUT.es.md).

## 4. Todos los campos del modelo configurables por producto

Se admiten **33 controles escalares más la lista objetivo**. Los valores iniciales de abajo
corresponden al TOML entregado. Una celda vacía hereda su valor actual, que puede haber cambiado.
Los números deben ser finitos. Los límites validan la entrada; no garantizan estimaciones
útiles. [La referencia del algoritmo](ALGORITMO.md#14-referencia-completa-de-configuración-qué-controla-cada-decisión)
explica los cálculos completos.

| Columna del mapping | Inicial | Valores / finalidad |
|---|---|---|
| `tenors` | Lista entregada de 40 etiquetas | Array JSON de tenors admitidos; periodos a rellenar. Configurable, pero no dimensión de búsqueda. |
| `layer_local` | true | Booleano; ajuste con anclas propias del día. |
| `layer_correlation` | false | Booleano; pesos aprendidos entre grupos de tenors. |
| `layer_cross` | false | Booleano; sorpresas de otros productos elegibles. |
| `layer_hist` | auto | `on`, `off`, `auto`; utilización del ajuste histórico. |
| `layer_arbitrage` | false | Booleano; reconstrucción contractual final admitida. |
| `basis_mode` | auto | `auto`, `ratio`, `additive`; fórmula del ajuste y regla de selección. |
| `min_volume` | 0 | Número >=0; volumen mínimo conocido de ancla. |
| `tau_log` | 0.5 | Número >0; alcance de la distancia local. |
| `other_kind_weight` | 0.6 | Número >=0; peso de otro tipo de contrato. |
| `shrink_k` | 1 | Número >0; evidencia necesaria para dar más peso a las anclas del día. |
| `ewma_halflife_days` | 10 | Número >0; memoria de la base propia en actualizaciones observadas. |
| `max_anchor_dev` | 0 | Número >=0; filtro común de desviación de anclas; cero lo desactiva. |
| `hist_auto_min_obs` | 10 | Número >=0; evidencia efectiva necesaria para history auto. |
| `ratio_eex_floor` | 1 | Número >0; umbral EEX cercano a cero para proteger ratio. |
| `max_ratio_deviation` | 1 | Número >0; máximo valor absoluto del ajuste ratio. |
| `hist_max_age_days` | 60 | Entero >=1; antigüedad máxima del ajuste histórico en días naturales. |
| `corr_halflife_days` | 20 | Número >0; olvido de evidencia emparejada entre familias. |
| `corr_prior_obs` | 8 | Número >=0; fuerza inicial frente a poca evidencia de correlación. |
| `cross_min_corr` | 0.5 | Número de -1 a 1; correlación mínima entre productos. |
| `cross_min_obs` | 8 | Número >=0; evidencia efectiva mínima entre productos. |
| `cross_halflife_days` | 20 | Número >0; olvido de relaciones aprendidas entre productos. |
| `fallback_price_method` | ewma | `simple`, `ewma`; media EEX de precios con ventana finita. |
| `fallback_price_window` | 5 | Entero >=2; ventana completa de publicaciones EEX para precios. |
| `fallback_ewma_halflife` | 2 | Número >0; semivida del peso en observaciones de publicación. |
| `fallback_spread_window` | 9 | Entero >=2; ventana de media simple para spreads mensuales. |
| `fallback_anchor_months` | 2 | Entero >=1; meses promediados independientemente desde M+0. |
| `shape_mode` | off | `off`, `audit`, `adjust`; comportamiento de forma posterior al relleno. |
| `shape_adjust_originals` | false | Booleano; permitir mover precios finales derivados de originales. |
| `shape_smoothness_weight` | 1 | Número >=0; penalización a correcciones irregulares frente a EEX. |
| `shape_coherence_weight` | 10 | Número >=0; penalización suave de agregación. |
| `shape_max_abs_adjustment` | 10 | Número >0; movimiento máximo en la unidad de precio de la curva. |
| `shape_original_weight` | 10 | Número >=1; fidelidad a originales si se permite moverlos. |
| `shape_coherence_tolerance` | 0.01 | Número >=0; tolerancia informada de agregación en unidades de precio. |

Rutas, columnas de entrada, offset/antigüedad EEX, calentamiento y convenciones de calendario
siguen siendo opciones globales de ejecución/entrada. No son dimensiones del modelo por
producto. Horas y zona horaria ya pertenecen a cada identidad del mapping. Cambiar unidades,
calendarios o disponibilidad cambia el significado de los datos y no debe utilizarse para
ganar una búsqueda de parámetros.

## 5. Las celdas de producción y las mallas de MLflow son cosas diferentes

En el notebook, una malla como `{"tau_log": [0.3, 0.8], "layer_hist": ["off", "auto"]}`
representa cuatro ensayos. En el mapping, `tau_log=0.8` y `layer_hist=auto` representan una
configuración fija. La búsqueda global propone un candidato común; la individual propone
uno por identidad exacta. Se puede reutilizar la misma malla para todos los productos; las
excepciones de la malla viven en el plan del experimento, no en la tabla de producción.

El notebook o un JSON asociado define alcance, productos y listas de candidatos. La búsqueda
global usa deliberadamente valores globales e ignora excepciones de modelo del Excel. La
individual usa la base efectiva de cada curva según el selector de producción. Los campos
explorados se sustituyen por los del candidato; los demás mantienen esa base fija. Una malla
específica sustituye a la común para ese producto, sin mezclar sus dimensiones. Se pueden
explorar los 33 controles escalares; `tenors` permanece fijo. Véase
[MLFLOW.es.md](MLFLOW.es.md) para el plan ejecutable y [BACKTEST.es.md](BACKTEST.es.md) para qué
se oculta, métricas, cobertura y limitaciones. Revisa la propuesta antes de copiar valores
fijos ganadores al mapping: el experimento no los activa automáticamente.

En una búsqueda individual se mantienen fijos los parámetros que generan señales en otras
curvas. La campaña también comprueba juntos todos los ganadores, incluidas sus interacciones
CROSS, con el mismo corte cronológico. Esa comprobación usa las mismas fechas reservadas:
no es un segundo examen final independiente ni otra búsqueda. Poder configurarlo no
demuestra precisión predictiva; un producto con poca historia puede justificar mantener
los parámetros globales en vez de ajustar una excepción propia.

Los filtros de anclas y las guardas ratio están entre los controles escalares explorables.
La campaña elige las verdades a puntuar con `min_volume` y `max_anchor_dev` desactivados,
independientemente del candidato. Sus valores siguen filtrando las pistas visibles y el
aprendizaje del modelo. Así no se borran verdades difíciles endureciendo filtros; las
abstenciones siguen afectando a cobertura e intersección común de precisión.
