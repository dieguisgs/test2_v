# Relleno de curvas VWAP


[Diccionario de outputs](OUTPUT.es.md) · [Output dictionary](OUTPUT.md)
[English](README.md) · [Algoritmo](ALGORITMO.md) · [Algorithm in English](ALGORITHM.md)

[Repositorio en GitHub](https://github.com/dieguisgs/test1_v)

Completa los precios que faltan para cada **fecha de referencia, producto, región, unidad y
tenor objetivo**, utilizando VWAPs observados y settlements de EEX. El output enriquecido
conserva filas y columnas originales, añade puntos ausentes e indica origen y método del precio.

Los nueve casos de procedencia del precio, con ejemplos, se explican en la sección 0.9 de [ALGORITMO.md](ALGORITMO.md).

[La investigación sobre anclas](ANCHORS.es.md) explica cuándo Day/Month pueden ayudar o
perjudicar otros periodos, con ejemplos controlados y límites de validación. Las políticas
de elegibilidad propuestas son investigación, no opciones nuevas del config de producción.

La identidad de una curva es **`(product, region, unit)`**. Un mismo nombre en otra región o
unidad tiene mapeo, anclas, historia y resultados independientes. Los valores originales se
conservan; el cruce interno recorta espacios exteriores sin cambiar mayúsculas ni escritura.
Región/unidad vacías son valores literales, nunca comodines. El producto no puede estar vacío.

**Etapa posterior opcional:** [shape](SHAPE.es.md) está implementada y apagada por defecto. Audita o ajusta precios finitos existentes. Solo puede mover precios propios con `shape.adjust_originals=true`; conserva las columnas del input.


**Configuración global o individual implementada:** `[run].configuration_mode` elige `global` (predeterminado, ignora celdas individuales) o `individual` (aplica por curva las celdas no vacías del mapping CSV/Excel). Se pueden sustituir los 33 controles escalares y la lista `tenors`; no son perfiles cerrados. [CONFIGURATION.es.md](CONFIGURATION.es.md) explica las 34 columnas, herencia, prioridad del CLI, diario/refill/catchup y trazabilidad. La búsqueda global o por producto de MLflow es una elección independiente: el Excel guarda una configuración fija, y el notebook/JSON guarda las mallas.

## Configuración mínima para empezar

No necesitas ajustar las 60 entradas. **19 son rutas, nombres de columnas y zonas horarias**;
no son parámetros estadísticos. Para arrancar, revisa `[paths]`, genera y revisa el mapping,
elige `[targets].tenors` y deja `method.basis_mode = "auto"`. Mantén los demás valores
iniciales, incluidas `correlation = false`, `cross = false`, `arbitrage = false` y `shape.mode = "off"`.

La referencia completa documenta controles avanzados para auditoría y cambios deliberados.
`tune` admite seis controles del modelo, pero sin opciones adicionales compara solo los tres
modos y mantiene el resto fijo. No busca automáticamente entre todas las entradas del TOML.

Para editar listas de parámetros, arrancar MLflow local automáticamente y guardar comparaciones,
usa [el notebook de backtest](notebooks/backtest_mlflow.ipynb) y su [guía MLflow](MLFLOW.es.md).
Empieza con una demo sintética autónoma y seis combinaciones; después admite tus datos reales
en el otro ordenador. Su grid ampliado incluye alcance local, memoria histórica, promedios EEX
y shape; la guía detalla los campos admitidos y los límites de evaluación.
Instálalo con `uv sync --group notebook --group experiment`. Propone parámetros sin aplicarlos
al config ni a las curvas de producción.
Ambos notebooks, incluidas sus explicaciones, código y textos de interfaz, están íntegramente
en inglés. La documentación Markdown independiente está disponible en inglés y español.

El notebook de experimentos también [abre curvas guardadas por run, candidato y etapa](MLFLOW.es.md#saved-curves),
con las mismas vistas de curva completa, medias por rango y evolución de una entrega.
Con `LOG_PREDICTIONS=True`, todos los candidatos guardan curvas de calibración y solo el
ganador guarda las de validación. Conservan originales visibles: la precisión se mide con
los ocultados. [La explicación del criterio](MLFLOW.es.md#selection-explained) aclara por qué
manda la cobertura, cómo se promedia el error modelo/EEX por curva y por qué la forma se revisa aparte.

## Instalación

En la máquina donde están los datos reales, clona el repositorio o copia sus archivos de
código y documentación a una carpeta de trabajo. No hace falta ningún archivo comprimido:

```powershell
git clone https://github.com/dieguisgs/test1_v.git
cd test1_v
```

Requiere Python 3.11 o posterior. Desde la carpeta del proyecto, utiliza una de estas opciones:

```powershell
uv sync
```

o un entorno virtual con pip:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install "numpy>=1.26" "pandas>=2.2" "openpyxl>=3.1" "pytest>=8.0"
```

Los ejemplos siguientes usan el `python` de ese entorno. En PowerShell, actívalo con `.venv\Scripts\Activate.ps1`, utiliza directamente `.venv\Scripts\python.exe` o antepón `uv run` a los comandos si utilizas uv.

Crea el entorno en la máquina de destino; no copies `.venv` de otro ordenador. Para los dos
notebooks y MLflow local instala los grupos opcionales con
`uv sync --group notebook --group experiment` y selecciona ese entorno como kernel.
Las explicaciones, el código y la interfaz de los notebooks están en inglés; las guías
Markdown están disponibles en inglés y español.

## Configurar el input real

Hay **un archivo principal de configuración, `config.toml`**, en el repositorio de código.
La ruta inicial es `data/vwaps.xlsx`: cámbiala por el input real acumulado de la máquina de
destino. Todos los comandos habituales `python run.py ...` utilizan ese único config.
Para una prueba independiente, usa expresamente `--vwap data/synthetic_vwaps.csv` si has
generado ese fichero; el valor por defecto nunca selecciona la demo silenciosamente.

No se publican los inputs reales, los datos EEX, el mapping local, el entorno virtual ni las
salidas generadas. Incorpora tus archivos de entrada y EEX en la máquina de destino, configura
sus rutas como se explica abajo y genera/revisa allí el mapping. El ejemplo ficticio incluido
en el repositorio tiene filas desactivadas; no sustituye las asignaciones de tus productos.

Edita `[paths]` en la configuración elegida:

```toml
[paths]
vwap_input = "data/vwaps.xlsx"
mapping = "mappings/products.csv"
eex_curves_dir = "../eex_scraper/output/curves/POWER"
output_dir = "output"
```

Las rutas son relativas al archivo de configuración; también se admiten rutas absolutas. El input puede ser CSV o Excel, incluido un patrón como `data/vwaps_*.csv`. `--vwap` cambia esa ruta para un comando concreto.

El input tiene formato largo, con una observación por fila:

| Columnas | Significado |
|---|---|
| `reference_date`, `product`, `region`, `unit`, `tenor2`, `vwap` | Fecha, identidad completa de curva, tenor y precio: columnas obligatorias. Región/unidad deben existir, aunque corresponda un valor vacío literal. Fechas válidas; los precios vacíos o inválidos pueden completarse. |
| `total_volume` | Volumen opcional para combinar observaciones y ponderar anclas. |
| `weekday`, `country`, `classification`, `periodicity_2`, `n_trades` | Otros metadatos: se conservan junto con todas las columnas originales. Región/unidad son claves de identidad, no solo metadatos descriptivos. |

Ejemplos de tenors: `D+1`, `WE`, `M+1`, `Q+1` y `Cal+1`. Ajusta `[vwap_columns]` si tus columnas tienen otros nombres y `[targets]` para los tenors que quieres completar. Los archivos de EEX necesitan `tradeDate`, `maturityType`, `deliveryStart` y `settlPx`.

`curves/POWER/<área>/Base.csv` y `Peak.csv` son archivos largos con el histórico acumulado
localmente de cada producto EEX, no solo una fecha. Es normal que una fecha se repita: tiene
varios contratos de entrega. El scraper reconstruye estas vistas desde los contratos de
`table_data/`; no añade simplemente filas al final del CSV de curvas. Un fichero por
área/producto encaja con el tamaño pequeño actual y el lector VWAP. No hace falta separarlo
por días; pasar en el futuro a particiones mensuales o Parquet exigiría adaptar escritura y lectura.

La lista objetivo se edita directamente en la configuración, sin cambiar Python:

```toml
[targets]
tenors = ["D+1", "D+2", "WE", "M+1", "M+2", "M+3", "Q+1", "Cal+1"]
```

Edita la sección existente para ampliar o reducir la lista global. En modo global se aplica a todos los productos `fill`; en individual cada identidad puede sustituirla mediante una celda JSON `tenors` del mapping. Se conservan los originales fuera de la lista, pero no se crean sus filas ausentes. Incluir un tenor permite estimarlo cuando hay datos suficientes; no garantiza que pueda calcularse.

## Elegir el modo de cálculo

Edita la sección `[method]` existente de `config.toml`:

```toml
[method]
basis_mode = "auto"
```

| Valor | Cálculo |
|---|---|
| `auto`, por defecto | Elige ratio o additive para cada objetivo con el árbol siguiente |
| `ratio` | Estima `basis = Own/EEX − 1` y calcula `precio = EEX × (1 + basis)`; no cambia automáticamente |
| `additive` | Estima `basis = Own − EEX` y calcula `precio = EEX + basis`; no cambia automáticamente |

En auto se aplica la primera regla que se cumpla:

1. Si `abs(EEX del objetivo) < ratio_eex_floor`, usa additive (`auto_additive_low_eex`).
2. Si no, cuando no quedan anclas ratio pero sí additive, usa additive
   (`auto_additive_no_ratio_anchors`).
3. Si no, cuando ninguno tiene anclas y solo additive tiene historia vigente y permitida por
   `[layers].hist`, usa additive (`auto_additive_history_only`).
4. En el resto de los casos, usa ratio.

Los VWAPs brutos originales, incluidos cero y negativos, siempre se conservan. El precio final propio también queda fijo salvo permiso explícito de shape para modificar originales. Las anclas
ratio exigen `abs(EEX) >= ratio_eex_floor`, `Own/EEX > 0` y
`abs(Own/EEX − 1) <= max_ratio_deviation`; additive admite pares finitos que superen los
filtros comunes de volumen/desviación. −10/−12 puede ser un ancla ratio válida; 2/0 no.
Un objetivo auto próximo a cero elige additive aunque otras anclas permitan usar ratio.
Con un ajuste ratio disponible, EEX objetivo = 0 da 0. Sin componentes de ajuste se usa el respaldo suavizado, aunque el modo seleccionado sea ratio.

`max_ratio_deviation` también puede rechazar anclas ratio aunque sus precios sean distintos de
cero y tengan el mismo signo. Auto puede usar después sus diferencias aditivas si pasan los
filtros comunes. `max_ratio_deviation` protege solo ratio; `max_anchor_dev` filtra ambos modos
y está apagado por defecto. Auto no detecta outliers ni elige el modelo local de menor error.

Cada modo tiene sus propias historias, puntuaciones y estadísticas de correlación/cross.
No se suma un porcentaje directamente a un precio. `[layers].hist = "auto"` es otra opción:
decide si ayuda usar la historia dentro de cada modo, no cuál de las dos fórmulas elegir.

Por ejemplo, Own = 2 frente a EEX = 0 aporta una diferencia aditiva de 2. Con objetivo EEX = 1,
sin historia, suma de pesos locales W = 1 y `shrink_k = 1`, auto elige additive y reduce esa
diferencia con `w = 0.5`: precio = `1 + 0.5 × 2 = 2`. Sin esa reducción, la fórmula pura daría 3.
W depende en realidad del volumen y la distancia de entrega. El documento del algoritmo
detalla las ponderaciones y el aprendizaje; auto es una regla que debe validarse, no una
garantía de menor error predictivo.

## Respaldo EEX suavizado cuando no hay ajuste propio

Este respaldo sustituye la antigua devolución directa del settlement. **Solo interviene
si no hay componente local, histórico permitido ni cross utilizable** y existe una referencia
EEX actual admitida por la regla de antigüedad. Los originales y los ajustes aprendidos siguen
su cálculo habitual. Un ajuste disponible cuyo valor sea exactamente cero sigue siendo un
ajuste: no se cambia de rama por coincidir numéricamente con EEX.

En lenguaje de negocio: cuando no sabemos cómo desplazar EEX hacia tu nivel, evitamos copiar
una sola publicación. Estimamos el nivel a partir de varias publicaciones del mismo contrato;
para meses lejanos, trasladamos desde un mes cercano las diferencias históricas entre meses.
No usamos operaciones propias ni estimaciones anteriores para entrenar este respaldo.

### Ventanas completas, contratos fijos y medias

Se toman las últimas publicaciones distintas del archivo EEX hasta el corte
`reference_date + eex.offset_days`, terminando en el último snapshot permitido por `eex.max_stale_days`. Son **observaciones
publicadas**, no días naturales: repetir un settlement antiguo en varios días sin publicación
no aumenta la muestra. Nunca se toman publicaciones futuras.

Cada precio se refiere al **mismo periodo absoluto de entrega** en todas las publicaciones.
Para cada fecha, Pricer puede obtenerlo por contrato exacto, strip o residuo, con las horas
del perfil. Si falta ese periodo en cualquiera de las últimas N publicaciones globales del
archivo, la ventana falla: no se salta la fecha para buscar una más antigua, no se arrastra
un precio y no se utiliza una ventana parcial.

- `simple`: media aritmética con pesos `1/N`.
- `ewma`, por defecto: media exponencial **finita y normalizada** de N observaciones,
  `peso_j = 2^(-edad_j/halflife) / suma_pesos`; edad 0 es la última publicación,
  1 la anterior, etc. No es la EWMA recursiva del basis propio.
- `ewma_halflife=2` significa que una publicación dos observaciones anterior recibe la mitad
  del peso sin normalizar de la última. No significa dos días naturales.

### Meses: nivel cercano y diferencias entre meses

Con `anchor_months=2`, los meses ancla son **M0, mes natural de la fecha de referencia, y M1**.
Cada uno se promedia por separado durante `price_window=5` publicaciones cuando se solicita.
M2 y siguientes parten del **último mes ancla, M1**, y añaden sucesivamente:

```text
precio(M2) = media_precio(M1) + media_simple_9(EEX(M2) − EEX(M1))
precio(M3) = precio(M2)       + media_simple_9(EEX(M3) − EEX(M2))
...
```

Cada spread utiliza ambos precios de la **misma publicación**, durante las últimas
`spread_window=9` publicaciones. Los spreads son diferencias de precios EEX crudos; no se
sustituye el spread del último día por uno previamente suavizado. Así se evita volver a suavizar un dato ya promediado
y se mantienen separadas la media del nivel y la media de spreads. La media de spreads es siempre simple, incluso con precios EWMA.

Para calcular M3 se necesita la ventana completa de M1 y las ventanas completas M1→M2 y
M2→M3. No hace falta la media de M0, porque su cálculo es independiente. Si falla un eslabón,
no se publica la cascada incompleta. Los meses se cuentan desde la fecha de referencia,
no desde la fecha de una publicación antigua ni desde el primer tenor de targets.
Con `anchor_months=1`, la cascada parte de M0; con 3, parte de M2.

Day, Week, Weekend, Quarter, Season, Year, BOM y BOW se promedian **directamente para su propio
periodo absoluto** durante la ventana de precios. No se sustituyen automáticamente por los
meses suavizados. Por tanto, el método conserva diferencias mensuales históricas pero no
fuerza que un trimestre coincida con el promedio de los meses; las inconsistencias se reportan.

### Ejemplo reproducible: 5 publicaciones de precios y 9 de spreads

Fecha de referencia: 30-09-2026; M1=octubre, M2=noviembre, M3=diciembre. Precios didácticos:

| Publicación | Octubre | Noviembre | Diciembre | Nov−Oct | Dic−Nov |
|---|---:|---:|---:|---:|---:|
| 18-09 | 92 | 102 | 103 | 10 | 1 |
| 21-09 | 94 | 105 | 107 | 11 | 2 |
| 22-09 | 96 | 108 | 111 | 12 | 3 |
| 23-09 | 98 | 111 | 115 | 13 | 4 |
| 24-09 | 100 | 114 | 119 | 14 | 5 |
| 25-09 | 102 | 117 | 123 | 15 | 6 |
| 28-09 | 104 | 120 | 127 | 16 | 7 |
| 29-09 | 106 | 123 | 131 | 17 | 8 |
| 30-09 | 108 | 126 | 135 | 18 | 9 |

Las últimas cinco observaciones de octubre son 100,102,104,106,108. Su media simple es 104.
Con EWMA de vida media 2, los pesos normalizados, del más antiguo al reciente, son
0.088947075, 0.125790159, 0.177894149, 0.251580318 y 0.355788298: media **105.318945**.
El spread medio Nov−Oct de las nueve publicaciones es **14** y Dic−Nov es **5**.
Resultado EWMA: M1 **105.318945**, M2 **119.318945**, M3 **124.318945**.
Con simple: M1 **104**, M2 **118**, M3 **123**.

No se publica noviembre=126 solo por ser el último settlement. Si el cálculo suavizado
coincide con el settlement por casualidad —por ejemplo precios constantes— se conserva
esa coincidencia: no se añade una perturbación artificial. Ventanas largas pueden retrasar
cambios de régimen; ventanas cortas pueden seguir más ruido. Mantener los meses absolutos
evita mezclar etiquetas que rotan, pero no garantiza modelar toda la estacionalidad ni mejorar
la predicción. Se necesita validación real y medir también cobertura.

### Los cinco controles y sus alternativas de CLI

```toml
[eex_fallback]
price_method = "ewma"
price_window = 5
ewma_halflife = 2.0
spread_window = 9
anchor_months = 2
```

| Clave | Defecto | Qué cambia y qué límites tiene |
|---|---|---|
| `eex_fallback.price_method` | `"ewma"` | `simple` da el mismo peso a las N observaciones; `ewma` favorece las recientes. Solo esas dos opciones. No cambia la media simple de spreads |
| `eex_fallback.price_window` | `5` | Entero ≥2. Mayor usa más publicaciones y puede suavizar más, pero exige mayor cobertura; menor responde con menos memoria. No son días naturales |
| `eex_fallback.ewma_halflife` | `2.0` | Finito >0. Mayor reparte más peso hacia observaciones antiguas; menor concentra en las recientes. Solo cambia precios con método ewma; debe seguir siendo válido con simple |
| `eex_fallback.spread_window` | `9` | Entero ≥2. Ventana completa de diferencias mensuales simultáneas. Mayor usa más historia y exige más cobertura. Solo interviene en cascadas mensuales |
| `eex_fallback.anchor_months` | `2` | Entero ≥1 desde M0. Mayor promedia más meses individualmente y retrasa el inicio de la cascada; no es un número de VWAPs propios |

Estos controles no afectan a originales ni a precios con ajuste local/histórico/cross.
Las ventanas de precio y spread son independientes: no se exige que una sea mayor que la otra.
La antigüedad máxima controla el último snapshot permitido, no convierte las ventanas en
ventanas de días naturales. Las 60 claves del config incluyen estos cinco controles.

En `daily`, `refill`, `catchup`, `backtest` y `tune` se pueden sustituir para una ejecución:

| Opción | Control sustituido |
|---|---|
| `--eex-price-method simple` o `ewma` | `price_method` |
| `--eex-price-window N` | `price_window` |
| `--eex-ewma-halflife H` | `ewma_halflife` |
| `--eex-spread-window N` | `spread_window` |
| `--eex-anchor-months N` | `anchor_months` |

Por ejemplo, `python run.py daily --eex-price-method simple --eex-price-window 5`.
Si se omiten, se usa TOML; no se modifica el archivo. En tune estos valores quedan fijos
para todos los candidatos: no añaden dimensiones a la rejilla de seis controles.

### Resultado, auditoría y actualización de salidas anteriores

La fuente es `eex+smooth`; los métodos son `eex_price_simple`, `eex_price_ewma`,
`eex_month_cascade_simple` y `eex_month_cascade_ewma`. `eex_settle` conserva la referencia
EEX sin suavizar para auditoría. `basis` y `local_weight` quedan vacíos: no describen esta
media. `basis_mode` indica la selección previa ratio/additive, **no una fórmula aplicada
al respaldo suavizado**. El JSON `eex_fallback_trace` —`curve_eex_fallback_trace` en
enriquecido— contiene periodos, fechas, precios, pesos y cada spread: permite rehacer el cálculo.

Sin ventana completa se marca `eex_fallback_unavailable` y, por defecto, queda `missing`.
Si se habilita la reconstrucción final de contratos, se intenta esa vía; sin una construcción
válida, queda `missing`. El flag puede persistir aunque
el arbitraje consiga precio. No se vuelve al settlement crudo. El settlement sin ajustar
sigue siendo el benchmark `eex` del backtest y de hist-auto; benchmark y salida de producción
son conceptos distintos.

Para aplicar este cambio a resultados antiguos, ejecuta `refill --from ... --to ...` o
`daily --date ...` sobre las fechas elegidas. Catchup solo recupera grupos pendientes:
una fila existente, incluso `missing` o una estimación de una versión anterior, ya cuenta
procesada y no se recalcula solo por actualizar el programa.


## Referencia completa de configuración

Los valores siguientes son los del `config.toml` entregado. Edita sus secciones existentes,
sin duplicar secciones TOML. No hace falta cambiar Python. Las rutas relativas parten de la
carpeta del config, salvo `eex_file` del mapeo, que parte de `eex_curves_dir`.

### Rutas (`[paths]`)

| Parámetro | Valor entregado | Efecto de cambiarlo |
|---|---|---|
| `vwap_input` | `data/vwaps.xlsx` | Elige originales acumulados CSV/Excel o un patrón entre comillas; no apuntes al output enriquecido |
| `mapping` | `mappings/products.csv` | Elige el mapeo (product, region, unit)→EEX que se genera/lee |
| `eex_curves_dir` | `../eex_scraper/output/curves/POWER` | Elige la raíz con CSV largos de EEX, por ejemplo `DE/Base.csv` |
| `output_dir` | `output` | Carpeta donde se escriben curvas diarias/acumuladas, enriquecidos, informes y logs |

### Capas (`[layers]`)

| Parámetro | Defecto | Efecto de cambiarlo |
|---|---|---|
| `local` | `true` | `false` apaga el ajuste con anclas de hoy; los originales se conservan |
| `hist` | `"auto"` | `"on"` usa historia vigente; `"off"` la desactiva; `"auto"` compara errores anteriores frente a EEX, por separado para cada modo/grupo |
| `correlation` | `false` | `true` sustituye pesos fijos entre grupos por relaciones medidas entre sorpresas, con un peso previo de respaldo |
| `cross` | `false` | `true` permite sorpresas de otros productos correlacionados; necesita historia interna y EEX del objetivo, con estadísticas separadas por modo |
| `arbitrage` | `false` | `true` activa la reconstrucción final con contratos propios/estimados disponibles cuando no hay referencia EEX calculable o no puede suavizarse el respaldo EEX sin ajuste |

Apagar capas no elimina originales. `hist = off` no apaga por sí solo cross: cross puede usar
su historia interna para calcular sorpresas, sin sumar esa media al precio. Elegir additive
en auto no enciende una capa local/hist/cross apagada.

Para activar la reconstrucción final, cambia a `arbitrage = true` en la sección `[layers]`
existente. Con el valor inicial `false`, la referencia EEX sigue calculándose mediante
`exact`, `strip` y `residual`; solo se desactiva la reconstrucción posterior con contratos
propios o ya estimados. El histórico guarda un **basis respecto a EEX**, por lo que necesita
una referencia EEX utilizable y una historia permitida. Este orden depende de la información
disponible; no clasifica un método como intrínsecamente mejor que otro.

`own_equivalent_period` (own reused) es otro caso: completa una etiqueta equivalente usando
un precio propio de la misma identidad, fecha de referencia y periodo de entrega. No arrastra
el precio de ayer ni depende del interruptor `arbitrage`.

### Disponibilidad y antigüedad EEX (`[eex]`)

| Parámetro | Defecto | Efecto de cambiarlo |
|---|---|---|
| `offset_days` | `0` | Entero no positivo en días naturales. Solo admite publicaciones EEX con fecha <= `reference_date + offset_days`. `0` permite la fecha de referencia; `-1` la excluye. |
| `max_stale_days` | `0` | 0 admite cualquier publicación anterior; N positivo rechaza curvas con más de N días naturales de antigüedad; nunca selecciona EEX futuro |
| `warn_stale_days` | `3` | A esta antigüedad el mensaje pasa a ERROR; informa del problema, pero este umbral no rechaza por sí mismo la curva ni hace fallar la ejecución |

Para una curva del miércoles 07-10-2026, `offset_days=0` permite la publicación del 7 si está
en los datos; si falta, toma la última anterior. `offset_days=-1` pone el corte en el 6 aunque
el archivo ya contenga el 7. El lunes 05-10, `-1` significa domingo 04-10: si no se publica
el fin de semana, se selecciona el viernes 02-10. Son días naturales, no «retroceder una
publicación disponible». Se rechazan los valores positivos.

La antigüedad siempre es `reference_date - eex_asof`, **no corte menos publicación**. Ese
viernes tiene tres días de antigüedad el lunes: `max_stale_days=2` lo rechaza y `0` sigue
significando sin límite. El mismo corte limita las ventanas completas de precios/spreads
EEX. Los objetivos `M+1`, etc. siguen resolviéndose respecto a la fecha original de referencia:
en octubre `M+1` sigue siendo noviembre aunque la publicación EEX utilizada sea de septiembre.

Historia y covarianzas solo aprenden parejas exactas `propio_h / EEX_h` o `propio_h - EEX_h`
de la misma fecha. Un offset negativo retrasa su disponibilidad: para predecir T se exige
`h<T` y fecha de publicación `h<=T+offset_days`. Nunca aprende mezclando el propio de hoy
con una publicación EEX anterior. El log indica referencia, corte y publicación real; el
output incorpora `eex_offset_days`, `eex_cutoff_date` y `eex_asof`, con prefijo `curve_` en
el enriquecido.

Con offset negativo, LOCAL puede comparar las anclas propias de hoy con el EEX anterior
permitido e HIST usar las parejas anteriores ya disponibles. **CROSS no tiene sorpresa actual
utilizable**: esa rama sigue exigiendo EEX del mismo día para no interpretar el retraso de
publicación como un movimiento propio. Sus coeficientes pueden aprender parejas exactas con
retraso, pero no aporta ajuste cross actual con offset negativo. Los pesos aprendidos de
correlación local sí pueden seguir utilizándose.

Edita `offset_days` en la sección `[eex]` existente o sustitúyelo para una ejecución:

```powershell
python run.py daily --date 2026-10-07 --eex-offset-days -1
python run.py backtest --from 2026-09-01 --to 2026-10-06 --eex-offset-days -1
```

La opción existe en `daily`, `refill`, `catchup`, `backtest` y `tune`. El notebook utiliza
`EEX_OFFSET_DAYS=None` para respetar el config, o `0`/`-1` explícitos. Es un escenario fijo de
disponibilidad, **no un parámetro del grid**. Compáralos en experimentos separados y revisa
cobertura: excluir una publicación puede cambiar qué observaciones se pueden evaluar.

`catchup` sigue completando grupos pendientes. Si encuentra filas objetivo existentes de
filled/enriched con otro offset dentro del alcance activo solicitado, se detiene: recalcula
expresamente con `refill --eex-offset-days ...` o usa otra carpeta de output. Un offset ausente
o vacío en ficheros antiguos equivale a `0`; otras fechas, curvas y targets fuera de ese alcance
no bloquean la ejecución.

Se usan las fechas de publicación del CSV suministrado, no un reloj intradía ni un archivo de
versiones históricas. No demuestra a qué hora estuvo disponible un settlement o una revisión.

### Zonas horarias del borrador de mapeo (`[timezones]`)

| Parámetro | Defecto | Efecto de cambiarlo |
|---|---|---|
| `default` | `"Europe/Berlin"` | Zona de entrega por defecto para nuevas filas propuestas del mapeo |
| `GB` | `"Europe/London"` | Excepción para el borrador de GB |
| `IE` | `"Europe/Dublin"` | Excepción para IE |
| `PT` | `"Europe/Lisbon"` | Excepción para PT |
| `GR` | `"Europe/Athens"` | Excepción para GR |
| `RO` | `"Europe/Bucharest"` | Excepción para RO |
| `BG` | `"Europe/Sofia"` | Excepción para BG |
| `FI` | `"Europe/Helsinki"` | Excepción para FI |

Puedes añadir otras áreas. Una `timezone` explícita del mapeo manda; si está vacía se utiliza
el respaldo configurado. Cambiar esta sección no sobrescribe zonas explícitas revisadas. Base incluye los cambios de hora. Peak Day/Weekend
cuenta 12 horas todos los días; los demás tipos Peak cuentan solo lunes a viernes.

### Objetivos y convenciones (`[targets]`, `[conventions]`)

| Parámetro | Defecto | Efecto de cambiarlo |
|---|---|---|
| `targets.tenors` | `D+1..D+3`, `WE..WE+3`, `BOW`, `W+1..W+4`, `BOM`, `M+1..M+10`, `Q+1..Q+8`, `Sum+1..Sum+3`, `Win+1..Win+3`, `Cal+1..Cal+3` | Lista global de etiquetas objetivo; individual permite sustituirla por curva. Se conservan originales fuera de ella. |
| `conventions.day` | `"calendar"` | `"business"` interpreta D+n contando lunes a viernes; no se incorpora un calendario de festivos |
| `conventions.weekend_offset` | `0` | Desplaza WE+n propio este número de fines de semana al resolver la entrega |

Los rangos de la tabla abrevian la lista entregada; en TOML se escriben las etiquetas una a una,
como en el ejemplo inicial. Añadir un tenor no garantiza cobertura. `detect-conventions` ayuda
a comparar las interpretaciones.

### Cálculo e historia (`[method]`)

| Parámetro | Defecto | Efecto de cambiarlo |
|---|---|---|
| `basis_mode` | `"auto"` | Utiliza el árbol por objetivo o fuerza `"ratio"` / `"additive"` |
| `min_volume` | `0` | Un volumen conocido inferior no sirve de ancla; volumen desconocido no equivale a estar por debajo de un umbral conocido; este filtro de anclas no cambia originales |
| `tau_log` | `0.5` | Un valor positivo mayor permite más influencia de anclas lejanas |
| `other_kind_weight` | `0.6` | Peso de anclas de otro tipo de contrato, antes del ajuste de correlación entre grupos si está activado |
| `shrink_k` | `1.0` | Un valor positivo mayor reduce `w = W/(W+k)` y favorece la historia permitida o el ajuste cero si no hay respaldo |
| `ewma_halflife_days` | `10` | Un valor positivo mayor hace más lenta la actualización de las historias ratio/additive; cuenta días con observaciones válidas, no días transcurridos |
| `max_anchor_dev` | `0` | 0 apaga este filtro común; N positivo excluye anclas con `abs(Own−EEX)/max(abs(EEX),ratio_eex_floor) > N`; entre valores positivos, aumentarlo admite más desviaciones |
| `ratio_eex_floor` | `1.0` | Mínimo positivo de abs(EEX) para anclas ratio; en auto, un objetivo por debajo elige additive. Está en unidades de precio y no modifica EEX |
| `max_ratio_deviation` | `1.0` | Máximo positivo de abs(Own/EEX−1) en anclas ratio y del ajuste ratio final absoluto; no limita diferencias aditivas |
| `hist_max_age_days` | `60` | Caducidad positiva en días naturales: se elimina cada media tipo/grupo/global cuando pasan más de estos días desde su última observación válida |
| `hist_auto_min_obs` | `10` | Masa efectiva de comparaciones antes de decidir en hist-auto; antes se permite la historia vigente. Aumentarlo retrasa la decisión |

Ambos modelos históricos aprenden de anclas originales con EEX del mismo día, después de
predecir ese día. Los estimados nunca alimentan historia. Si falta la media de un tipo, se
puede usar la de su grupo/global vigente dentro del mismo modo. Excluir una observación ratio
por EEX próximo a cero no descarta su diferencia aditiva válida. Revisa las unidades y valida
con datos reales antes de cambiar límites; auto no demuestra que una diferencia aditiva grande
sea una buena predicción.

### Correlación (`[correlation]`) y ayuda entre productos (`[cross]`)

| Parámetro | Defecto | Efecto de cambiarlo |
|---|---|---|
| `correlation.halflife_days` | `20` | Un valor mayor conserva más tiempo evidencia de sorpresas emparejadas; cada actualización descuenta los días naturales desde la pareja anterior |
| `correlation.prior_obs` | `8` | Un valor mayor mantiene más tiempo el peso cerca de `other_kind_weight` antes de que domine la correlación medida |
| `cross.min_corr` | `0.5` | Correlación mínima más alta para admitir un producto ayudante |
| `cross.min_obs` | `8` | Masa efectiva mínima más alta de observaciones conjuntas antes de admitir la ayuda |
| `cross.halflife_days` | `20` | Memoria de correlación y β del ayudante, descontando los intervalos naturales entre parejas |

Estos parámetros afectan a la predicción cuando la capa correspondiente está activa. Los
ayudantes pueden ser productos mapeados como `fill` o `helper`. Sorpresas, covarianzas y β
se mantienen separadas por modo.

### Calentamiento (`[run]`)

| Parámetro | Defecto | Efecto de cambiarlo |
|---|---|---|
| `configuration_mode` | `"global"` | `global` ignora celdas de modelo del mapping; `individual` aplica valores no vacíos por curva. Véase [CONFIGURATION.es.md](CONFIGURATION.es.md). |
| `warmup_days` | `0` | 0 reconstruye desde toda la historia original suministrada; N positivo limita el calentamiento a N días naturales y puede hacer que daily difiera de un refill más largo |

Esta opción no recupera archivos que falten. En la máquina de destino deben estar los
originales acumulados y la historia EEX correspondiente.

### Asignación de columnas (`[vwap_columns]`)

| Parámetro | Columna por defecto | Efecto de cambiarlo |
|---|---|---|
| `reference_date` | `"reference_date"` | Lee las fechas de observación de esa columna; obligatoria y con fechas válidas |
| `product` | `"product"` | Lee el nombre, primer componente de identidad; obligatorio y no vacío |
| `region` | `"region"` | Lee la región exacta de identidad; columna obligatoria, vacío literal |
| `unit` | `"unit"` | Lee la unidad exacta de identidad; columna obligatoria, sin conversión ni comodines |
| `tenor` | `"tenor2"` | Lee las etiquetas relativas de entrega; obligatoria |
| `vwap` | `"vwap"` | Lee los precios; columna obligatoria, aunque los valores vacíos/inválidos se conservan para enriquecerlos |
| `volume` | `"total_volume"` | Lee los volúmenes para anclas; la columna puede faltar |

Cambiar estas asignaciones modifica la interpretación, no elimina las columnas originales del output.

## Generar y revisar el mapeo

Ejecuta una vez en la máquina de destino:

```powershell
python run.py mapping
```

Revisa `mappings/products.csv` antes de rellenar. **Cada fila nueva se propone con `use = off`,
aunque se haya encontrado un CSV EEX**. Activa expresamente las identidades deseadas: `fill`
calcula esa curva, `helper` aporta ayuda sin publicar su propia curva calculada y `off` la
excluye del cálculo. Comprueba cada `eex_file`, identidad/perfil, `hours` y `timezone`. Las
unidades y divisas deben coincidir con EEX; el programa no las convierte. Las identidades
sin mapear se indican y no se procesan; sus originales permanecen en el enriquecido.

`fill`/`helper` requieren además `eex_file` no vacío: si no hay archivo asignado, la identidad
se excluye y se señala con `mapping_unassigned` aunque `use` esté activado. Es distinto de
tener una ruta asignada cuyo archivo no exista o no contenga cotizaciones para esa fecha:
esa identidad sí está activa, se avisa de los datos ausentes y puede usar VWAPs propios.
La reconstrucción final también está disponible si se activa expresamente `arbitrage`.

**El mapeo se edita a mano**, con un editor de texto o Excel. La cabecera es
`product,region,unit,use,area,profile,eex_file,hours,timezone,comment`. Conserva las identidades
exactas de tres componentes del input y el formato CSV UTF-8 separado por comas; `eex_file`
es relativo a la carpeta POWER configurada. Un nombre puede repetirse si cambia región/unidad;
repetir toda la identidad es un error. Relanzar `mapping` conserva decisiones existentes y
añade identidades nuevas con `off`.

Un mapeo antiguo sin columnas región/unidad solo se migra mediante `mapping` si cada fila
antigua tiene exactamente una identidad compatible en el input. Se conservan su EEX y demás
ajustes. Con cero o varios candidatos, termina con error y no reescribe el archivo: añade
filas explícitas a mano. Una clave presente pero vacía es literal, no una coincidencia sin
especificar. El propio input debe tener las columnas región/unidad configuradas.

Un mapeo de pruebas AT/DE/FR/NL es una demo, no una lista completa de mercados. Genera y revisa
las identidades del input de destino. Los originales sin mapeo o con `off` permanecen en el
enriquecido, sin generarles curvas estimadas.

Los bloques GB del input, como `GB_Other_Block_1_2` o `GB_Other_Block_3_4`, no tienen calendario
ni fuente de bloque implementados y se proponen como `off`. `TB2` no significa «bloque 2»:
no los asignes a ciegas a TB2, Base o Peak. Para soportarlos hace falta definir exactamente
las horas de entrega y una fuente de precios correspondiente.

## Ejecutar

Después de ajustar las rutas de `config.toml` en la máquina donde están el código y los datos reales:

```powershell
python run.py mapping
python run.py daily
python run.py catchup
python run.py refill --from 2026-09-01 --to 2026-09-30
python run.py daily --date 2026-09-30
```

`daily` y `refill` comparten el mismo motor. Mantén `warmup_days = 0` y proporciona originales
acumulados para reconstruir la misma historia; solo las filas de hoy pierden esa memoria.
Las reejecuciones sustituyen resultados por fecha/producto/region/unit, conservando otras
identidades, incluidas otras regiones/unidades del mismo producto.

Los resultados antiguos sin `region`/`unit` en la curva o `curve_region`/`curve_unit` en el
enriquecido se rechazan antes de escribir resultados. Usa un `output_dir` nuevo y reconstruye
con `refill`; no se deducen identidades antiguas ni se fusionan silenciosamente. El log sí
puede escribirse.

### Elegir entre los tres usos

| Necesidad | Comando | Alcance |
|---|---|---|
| Calcular hoy o revisar una fecha | `daily` o `daily --date YYYY-MM-DD` | Recalcula la fecha, aunque tenga resultados |
| Recuperar días/curvas pendientes | `catchup` | Solo grupos fecha/identidad fill pendientes hasta hoy |
| Reconstruir un intervalo o enriquecer todo su input | `refill --from YYYY-MM-DD --to YYYY-MM-DD` | Recalcula el rango solicitado |

Catchup empieza en la primera fecha disponible de input/EEX salvo `--from`; sin fechas,
requiere un inicio explícito. `--to` es hoy por defecto y no admite futuro ni rango invertido.
Considera laborables y fechas observadas de input activo/EEX, incluidos fines de semana.
Un grupo está pendiente si un objetivo resoluble falta en **cualquiera** de los históricos
calculado/enriquecido. Un registro presente `missing` ya cuenta como procesado. Recalcula
la curva completa del grupo pendiente y conserva grupos completos. Para actualizar estos
con nuevas cotizaciones, usa daily/refill. Si no hay curvas fill activas o pendientes, no escribe.

Catchup solo incorpora originales de grupos fill pendientes; no exporta por primera vez
helper/off/sin mapeo. Daily/refill enriquecen todos los originales del rango. Los tres
reconstruyen memoria desde originales acumulados, nunca desde estimaciones guardadas.
El esquema de históricos y archivos afectados se valida antes de escribir resultados.

### Opciones de la línea de comandos

| Opción | Comandos / defecto | Efecto |
|---|---|---|
| `--config RUTA` | Global opcional; defecto `config.toml` | Permite otro config para uso avanzado; se escribe **antes** del comando |
| `--vwap RUTA_O_PATRÓN` | `mapping`, `daily`, `catchup`, `refill`, `backtest`, `tune`, `detect-conventions`; defecto input configurado | Cambia el input de esa ejecución; entrecomilla patrones con comodines |
| `--date YYYY-MM-DD` | `daily`; defecto fecha local de la máquina | Elige una fecha de referencia |
| `--eex-offset-days N` | `daily`, `refill`, `catchup`, `backtest`, `tune`; defecto `[eex].offset_days=0` | Offset no positivo del corte de publicación en días naturales; `-1` excluye EEX de T. Escenario fijo, no dimensión del grid. |
| `--from YYYY-MM-DD`, `--to YYYY-MM-DD` | `refill`, `backtest`; por defecto se deducen de los datos disponibles | Acota el rango solicitado; los datos anteriores pueden seguir calentando la memoria |
| `--from YYYY-MM-DD`, `--to YYYY-MM-DD` | `catchup`; inicio inferido, final hoy | Recupera solo pendientes; no admite final futuro ni rango invertido |
| `--last N` | `status`; defecto 10 | Número de fechas a mostrar |
| `--truth RUTA` | `backtest`; ausente por defecto | Compara con una verdad sintética correspondiente a esos datos |
| `--areas LISTA` | `make-synthetic`; defecto `DE,FR,NL,AT` | Áreas sintéticas separadas por comas |
| `--profiles LISTA` | `make-synthetic`; defecto `Base,Peak` | Perfiles sintéticos |
| `--from`, `--to` | `make-synthetic`; defecto 2000-01-01 a 2100-01-01 | Filtra las fechas EEX disponibles para generar sintéticos |
| `--seed N` | `make-synthetic`; defecto 7 | Semilla aleatoria de los sintéticos |
| `--mode ratio` o `--mode additive` | `make-synthetic`; defecto ratio | Elige cómo generar los sintéticos; **no sustituye el `basis_mode` del relleno** |
| `--out RUTA` | `make-synthetic`; defecto `data` | Carpeta de salida sintética |

Por ejemplo, `python run.py daily --date 2026-09-30 --vwap "C:/datos/vwaps_*.csv"` utiliza
el config habitual con otro input. Si se necesita un config opcional, se escribe
`python run.py --config C:/ajustes/personalizado.toml daily --date 2026-09-30`; no hace falta
crear un segundo config para el uso habitual. Sustituye las fechas por las cubiertas en destino.

## Leer el output

Los resultados principales son `output/enriched_history.csv` y `output/enriched/YYYY-MM-DD.csv`.

| Columna | Significado |
|---|---|
| `curve_price` | Precio final utilizable. Lee esta columna cuando el `vwap` original estuviera vacío o fuera inválido. |
| `data_origin` | `original`, `estimated` o `missing`. |
| `estimation_method` | Por ejemplo, `ratio_local`, `ratio_local_history`, `additive_local`, `eex_price_ewma`, `contract_strip`, `contract_residual` o `none`. |
| `curve_row_type` | `original`, `original_invalid` o `added`. |
| `curve_product`, `curve_region`, `curve_unit` | Identidad completa normalizada usada en los cruces; los valores originales quedan intactos. |
| `curve_basis_mode` | Modo seleccionado; no implica aplicar basis a originales o al respaldo `eex+smooth`. |
| `curve_configured_basis_mode` | Opción solicitada: `auto`, `ratio` o `additive`. |
| `curve_flags` | Incluye el motivo de selección auto cuando se eligió additive para una estimación con EEX. |
| Otras columnas `curve_*` | Claves de referencia, fuentes, detalles del cálculo y avisos. |

Se conservan los valores originales, los duplicados y los tenors fuera de los objetivos configurados. Un `vwap` original inválido no se sobrescribe: su estimación aparece en `curve_price`. Las filas añadidas llevan la estimación en ambas columnas. Los puntos que no pueden resolverse quedan como `missing`, sin inventar precios, volúmenes ni número de operaciones.

`filled_history.csv`, `filled/` y `consistency_history.csv` contienen la curva calculada y sus diagnósticos. Un fallo interno de cálculo detectado devuelve un código de salida distinto de cero sin sustituir los CSV de resultados; revisa `output/_logs/`.

## Validar

```powershell
python -B -m pytest -p no:cacheprovider
python run.py backtest
```

Las pruebas y los datos sintéticos validan la mecánica, no demuestran la precisión sobre tus datos reales de mercado. Es una base de complejidad media que utiliza observaciones propias, EEX, ajustes locales/históricos y reconstrucción final opcional. Mantén `cross = false`, `correlation = false` y `arbitrage = false` hasta que la validación con datos reales justifique activar esas capas. Los documentos del algoritmo enlazados explican los cálculos y sus límites.

El backtest empareja por fecha/producto/region/unit/tenor. Los informes de error y la detección
de convenciones separan unidades, sin promediar errores EUR/MWh y GBP/MWh. Compara
`pipeline_configured` con EEX sobre las mismas observaciones mediante `n_paired` y `mae_improvement`.


## Ajustar parámetros con originales reales: `tune`

La [guía de backtest, forma de curva y alternativas locales](BACKTEST.es.md) separa lo que
se puede ejecutar hoy de las mejoras propuestas, explica las familias de parámetros y
documenta la investigación de formas introducidas por el relleno.

**La referencia correcta para medir el error es tu VWAP propio ocultado, no EEX.** EEX es
una entrada y una comparación de referencia. Ejemplo: propio real 120, EEX 100; candidato A
predice 102 (error 18), candidato B predice 118 (error 2). B es mejor aunque se aleje de EEX.
El programa no busca que tu curva se parezca más a la curva EEX.

En cada observación evaluada, oculta ese punto propio, lo predice con el pipeline configurado
y compara contra el valor propio que realmente existía. No se esconde toda la curva del día.
Necesitas originales y EEX que coincidan en fechas y periodos de entrega; un scraper similar
sin ese solapamiento no basta. El comando informa o falla si no hay evidencia emparejada
suficiente: no inventa métricas para las fechas sin datos comparables.

```powershell
python run.py tune
python run.py tune --from 2026-08-01 --to 2026-09-30 --validation-days 10 --basis-modes auto,ratio,additive --tau-log 0.3,0.5 --shrink-k 0.5,1,2 --max-trials 50
```

Las fechas son ejemplos: utiliza el periodo cubierto por los originales y EEX de la máquina
real. **Requiere `run.warmup_days=0`**; no cambia esa opción automáticamente. Con 10 días
reservados exige al menos 12 fechas de observaciones propias válidas, y al menos dos fechas
de calibración con comparaciones realmente emparejadas. El ejemplo prueba 18 combinaciones.

| Opción de tune | Defecto | Efecto |
|---|---|---|
| `--from`, `--to` | Primera/última fecha del input | Intervalo del que se separan calibración y validación; el histórico anterior sigue disponible |
| `--vwap RUTA_O_PATRÓN` | Input configurado | Elige originales acumulados para esta evaluación |
| `--basis-modes` | `auto,ratio,additive` | Fórmulas candidatas, separadas por comas |
| `--tau-log` | Solo el valor configurado | Lista de alcances locales positivos finitos, por ejemplo `0.3,0.5` |
| `--shrink-k` | Solo el valor configurado | Lista de reducciones locales positivas finitas, por ejemplo `0.5,1,2` |
| `--hist-modes` | Solo el valor configurado | Lista de `auto,on,off` para permitir historia |
| `--correlation` | Solo el valor configurado | Lista `off,on` para comparar la extensión de pesos |
| `--cross` | Solo el valor configurado | Lista `off,on` para comparar ayuda entre curvas |
| `--validation-days` | `20` | Número positivo de últimas fechas distintas con observaciones válidas que se reservan; no días naturales consecutivos |
| `--max-trials` | `50` | Límite positivo del producto cartesiano; si se supera, rechaza la rejilla en lugar de truncarla |

Se exploran **solo esas seis opciones de modelo**. Los demás controles siguen como estén en
config. Con los valores por defecto se prueban tres modos, conservando los otros cinco campos.
Cambiar parámetros como caducidad o volumen requiere una comparación explícita adicional;
tune no explora automáticamente todo el inventario de configuración.

**Cómo decide cuál es mejor: primero cobertura, después error comparable.**

1. Ordena las fechas propias elegibles de curvas fill activas y reserva las últimas N para
   validación. Calibra todas las combinaciones sobre las anteriores. El holdout no elige ganador.
2. Define un universo idéntico de observaciones con VWAP ocultado y benchmark EEX disponible.
   Los casos sin benchmark EEX no entran en este objetivo relativo. Para cada candidato cuenta
   `n_baseline`, `n_available` (predicciones finitas), `n_missing=n_baseline−n_available` y
   `coverage=n_available/n_baseline`. Las abstenciones por ventanas incompletas son visibles,
   no se eliminan silenciosamente del denominador.
3. Solo son elegibles los candidatos con **máxima cobertura de calibración**. Calcula precisión
   sobre la **intersección de predicciones de todos los candidatos**, incluidos los de menor
   cobertura: son exactamente los mismos casos para todos. `n_paired` puede ser menor que
   `n_available`. Exige al menos dos fechas distintas en esa intersección; si no las hay,
   aborta y pide revisar datos/rejilla en lugar de comparar muestras distintas.
4. Dentro de la máxima cobertura, gana el menor promedio por curva de `MAE_modelo/MAE_EEX`,
   ambos contra el **mismo VWAP propio ocultado**. Cada curva pesa igual. Score 1 iguala EEX;
   0.7 significa un 30 % menos de error normalizado medio por curva, no un 30 % menos de MAE
   global en moneda. Si EEX es perfecto: cociente 1 si el modelo también, infinito si falla.
   Empate de cobertura y score: primer candidato de la rejilla.
5. Evalúa **solo al ganador** en las fechas posteriores, con parámetros fijos. Su memoria
   avanza causalmente con originales observados de fechas anteriores, incluso dentro del
   holdout. El score utiliza sus pares disponibles y reporta cobertura respecto a todo el
   universo EEX. Si no predice ninguno, cobertura 0, `n_paired=0`, errores y score no
   evaluables (vacíos/NaN en CSV, null en JSON); no se inventa un score ni se oculta el fallo
   de cobertura. Las ventanas incompletas son abstenciones; un error interno sigue abortando.

Por ejemplo, un candidato que cubre 90 de 100 casos tiene prioridad sobre otro que cubre
80, aunque este segundo tenga menor error. Entre dos que cubren 90 se compara score en la
intersección común de toda la rejilla. Por ello hay que leer **cobertura y precisión juntas**,
y no asumir que la mejor cifra aislada es el candidato elegido. Los cinco parámetros
`eex_fallback` quedan fijos en la búsqueda, incluidos overrides CLI; no son nuevas dimensiones.

Escribe `output/tuning_calibration.csv`, `tuning_validation.csv` y `tuning_selected.json`.
Los CSV muestran score, `normalized_skill=1-score`, número de parejas y errores MAE/RMSE/sesgo
por unidad; no suma errores absolutos de EUR/MWh y GBP/MWh. El JSON contiene el cambio propuesto,
la configuración base completa, el patrón de input y metadatos con fechas previstas y realmente
emparejadas. Guarda también los archivos de entrada, EEX y mapping para reproducir la evaluación:
una ruta/configuración por sí sola no conserva sus contenidos.

**No edita `config.toml` ni los históricos de curvas.** Revisa la validación y, si la decisión
de negocio la acepta, copia manualmente los valores seleccionados a las secciones existentes.
Un fallo de cualquier candidato aborta la evaluación; no se publica una selección parcial.

Es el mejor candidato **entre los probados, con máxima cobertura de calibración y mejor score común**, no una garantía para el futuro.
La validación oculta puntos que sí tuvieron precio: puede favorecer periodos líquidos y no
demuestra el comportamiento cuando falta una curva diaria entera, ni conoce la verdad de los
huecos reales. No reajustes repetidamente contra el mismo tramo reservado y después lo
presentes como evidencia independiente. Reserva nuevas fechas para evaluar cambios posteriores.

Los settlements EEX ausentes/no numéricos/no finitos se excluyen con aviso conservando los válidos. Un archivo presente corrupto o una fuente EEX no vacía sin settlements utilizables aborta la publicación. Un precio calculado no finito también hace fallar el cálculo. Los archivos ausentes y los CSV válidos con solo cabecera mantienen el comportamiento sin EEX documentado; repetir el cálculo con menos evidencia puede sustituir estimaciones anteriores.

<a id="shape-layer"></a>

## Capa opcional de forma: auditar o ajustar después del refill

Implementada y desactivada por defecto (`shape.mode="off"`). Busca conjuntamente cambiar
poco el refill, regularizar segundas diferencias de `precio - eex_settle` mensual y reducir
discrepancias entre Quarter/Year y sus meses ponderados por horas. Usa EEX bruto admitido
actualmente y una publicación común para cada trío mensual consecutivo; no usa la EWMA del
respaldo como plantilla. Un agregado completo puede aportar coherencia aunque no haya EEX.

Solo participan precios finitos de Month/Quarter/Year completos. Un trimestre necesita sus
tres meses y un año sus doce; no se inventan precios ausentes. Con shape activa se incluyen
etiquetas propias de esos tipos fuera de los objetivos cuando corresponda. Los alias se
deduplican. Por defecto los precios propios son fijos; `adjust_originals=true` permite moverlos
con límites y mayor peso de fidelidad, conservando las columnas del input. `curve_price` es
el precio final; un original ajustado pasa a `estimated`, source `own+shape` y método
`shape_adjusted_original`.

`audit` registra propuestas sin cambiar precios finales; `adjust` aplica una solución
validada con límites. La coherencia es una penalización suave, no una igualdad obligatoria.
Observaciones incompatibles o límites estrechos pueden dejar residuos. Si falla el solver se
conservan los precios previos. Los cambiados tienen confidence vacío; basis y anclas describen
el resultado previo. No se exige positividad, no se entrena con precios ajustados y no se
garantiza mejor predicción ni continuidad al cambiar el calendario.

### Configuración completa [shape]

Estas siete entradas completan las 60 claves del config. Los valores iniciales no están
calibrados. [SHAPE.es.md](SHAPE.es.md) detalla fórmulas, ejemplos, diagnóstico e histórico.

| Clave | Inicial | Significado y efecto |
|---|---|---|
| `shape.mode` | `"off"` | `off`, `audit` o `adjust`. Mientras está off, los demás controles no afectan a precios. |
| `shape.adjust_originals` | `false` | Booleano. True permite mover precios propios elegibles de la curva; conserva el input bruto. En audit solo cambian propuestas. |
| `shape.smoothness_weight` | `1.0` | Finito, ≥0. Mayor favorece segundas diferencias menores del ajuste mensual Own−EEX. Cero elimina la penalización. No actúa donde falta una referencia válida de tres meses. |
| `shape.coherence_weight` | `10.0` | Finito, ≥0. Mayor favorece concordancia entre agregado y promedio por horas. Cero elimina la penalización. No actúa sobre agregados incompletos. |
| `shape.coherence_tolerance` | `0.01` | Finito, ≥0. Margen diagnóstico absoluto del residuo agregado, en la unidad de precio. Mayor relaja la comprobación; no cambia precios ni impone una restricción. Sin términos agregados aplicables no hay veredicto global. |
| `shape.max_abs_adjustment` | `10.0` | Finito, >0. Movimiento absoluto total máximo por nodo. Mayor permite más cambio, no necesariamente mejores precios. Revisa la unidad; no hay conversión de moneda. |
| `shape.original_weight` | `10.0` | Finito, ≥1. Peso de fidelidad de originales móviles frente a 1 en estimados. Mayor dificulta mover originales. No tiene efecto sobre su movimiento cuando están fijos. |


Las siete opciones funcionan en `daily`, `refill`, `catchup`, `backtest` y `tune`:
`--shape-mode off|audit|adjust`, `--shape-adjust-originals on|off`,
`--shape-smoothness-weight`, `--shape-coherence-weight`, `--shape-coherence-tolerance`,
`--shape-max-abs-adjustment` y `--shape-original-weight`. Solo afectan a esa ejecución.

```powershell
python run.py daily --date 2026-09-30 --shape-mode audit
python run.py refill --from 2026-09-01 --to 2026-09-30 --shape-mode adjust --shape-adjust-originals off
python run.py daily --date 2026-09-30 --shape-mode adjust --shape-adjust-originals on --shape-original-weight 20
```

Shape no necesita una ventana histórica adicional para resolver una fecha; siguen vigentes
los requisitos propios/EEX del refill. Calibra intensidades con evaluación histórica real.
`pipeline_configured` del backtest incluye la capa después de ocultar el periodo evaluado;
tune conserva sus seis controles y mantiene shape fijo en el snapshot de configuración.
Recalcula grupos existentes con daily/refill tras cambiar ajustes; catchup no vuelve a
procesar un missing ya presente. [OUTPUT.es.md](OUTPUT.es.md#shape-output) documenta columnas
y consistencia antes/después/propuesta.

## Notebook interactivo de resultados

[NOTEBOOK.es.md](NOTEBOOK.es.md) explica el notebook portátil para elegir curva/fecha y
consultar rangos. Lee `filled_history.csv` desde la carpeta de output configurada; no necesita
el input VWAP original para inspeccionar resultados guardados. Compara precios antes/después
de shape y EEX, alineando por defecto periodos absolutos de entrega; las etiquetas relativas
son otra opción y pueden representar contratos diferentes entre fechas.

```powershell
uv sync --group notebook
uv run --group notebook jupyter lab notebooks/inspect_curves.ipynb
```

**La tolerancia de coherencia es diagnóstica, no una restricción obligatoria.**
`shape.coherence_tolerance=0.01` compara el valor absoluto del residuo
promedio-mensual-menos-padre con 0.01 en la unidad de precio de la curva. Subirla relaja la
comprobación y bajarla la endurece. No cambia el optimizador ni el precio aceptado.
Penalizaciones suaves, originales protegidos y límites de movimiento pueden dejar un resultado
fuera del margen. Audit señala la propuesta; adjust señala el resultado publicado. El trace
informa cada agregado y el resultado global, o null si no hay agregado aplicable. El mismo
valor numérico se interpreta en la unidad de cada curva; no es un porcentaje ni convierte monedas.

Esta capa por horas es específica de los contratos de power admitidos. No debe trasladarse a curvas agrícolas u otros activos sin validar sus entregas; véanse [el alcance y la muestra EEX observada](SHAPE.es.md#por-qué-es-específico-de-power-y-qué-mostró-la-muestra-eex).

## Revisión de código y ejecución fiable

[CODE_REVIEW.es.md](CODE_REVIEW.es.md) recoge defectos reproducidos, correcciones, pruebas y
límites pendientes. Se han reforzado la agregación de aliases, las validaciones, la protección
de archivos y las métricas. La [sección 18 del algoritmo](ALGORITMO.md#18-resultados-reproducibles-e-integridad-de-ejecución)
explica las reglas operativas actuales. Pasar tests no demuestra precisión en huecos reales.

El notebook inicia en **Full curve (all contract types)**, con todos los contratos guardados para la curva y
fecha seleccionadas. Para reproducir aquí o en otro ordenador el ejemplo de nueve familias:
`uv run python examples/generate_notebook_demo.py`. Después reinicia el kernel y ejecuta todas
las celdas. El CSV generado es sintético y no se publica en GitHub.
