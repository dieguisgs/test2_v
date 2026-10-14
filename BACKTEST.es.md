# Evaluar el relleno: forma de curva, backtest y variantes locales

[English](BACKTEST.md) · [Algoritmo](ALGORITMO.md) · [Configuración y comandos](README.es.md)

Esta guía distingue el comportamiento implementado de las propuestas que todavía no existen.
La capa shape del apartado final está implementada y apagada por defecto; los experimentos y alternativas indicados como propuestas siguen pendientes.

El [notebook y la guía MLflow](MLFLOW.es.md) permiten editar listas de candidatos, registrar
experimentos locales, ejecutar una demo sintética y utilizar un grid ampliado del modelo.
Aquí `tune` se refiere al comando CLI con sus seis campos originales salvo indicación expresa.
Ambos usan el mismo criterio de cobertura primero y validación cronológica solo del ganador.
El ejemplo del notebook reserva cinco fechas; el valor predeterminado del CLI sigue siendo veinte.

Para empezar por las dudas prácticas: [configuración por producto](#backtest-product-settings),
[qué y cuántos precios se ocultan](#backtest-masking), [qué significan las métricas](#backtest-metrics),
[cómo se elige al ganador](#backtest-selection) y [qué no demuestra la evaluación](#backtest-limits).

## 1. Qué significa «mejor relleno»

El objetivo principal es recuperar precios propios que faltan. EEX aporta información, pero
**la respuesta contra la que se mide el error es el propio ocultado**, o la verdad sintética
guardada antes de crear huecos. Un resultado más parecido a EEX no es necesariamente mejor.

Hay varias dimensiones: precisión, cobertura, errores grandes, estabilidad y coherencia entre
contratos. Conviene medirlas por separado. Una curva visualmente suave puede equivocarse mucho;
una curva con pendientes o cambios estacionales no es incorrecta por tenerlos. Tampoco debe
imponerse automáticamente que todos los VWAPs de contratos distintos cuadren entre sí.

## 2. Qué forma produce LOCAL actualmente

Hay **un estimador local de base**: media de ajustes Own−EEX o Own/EEX−1, ponderada por
volumen, tipo y distancia logarítmica entre puntos medios de entrega. Después modera el ajuste
mediante `shrink_k`. Ratio/aditivo cambian la fórmula del ajuste, no la geometría de esa distancia.
`correlation=true` puede modificar el factor entre grupos; conserva el mismo estimador.

La prueba con referencia 30-09-2026, M+1 propio=110/EEX=100 y Q+2 propio=120/EEX=150 muestra:

| Mes | EEX | Relleno local aditivo |
|---|---:|---:|
| Enero 2027 | 150 | 132.239522 |
| Febrero 2027 | 150 | 128.569541 |
| Marzo 2027 | 150 | 130.903094 |

Aquí el centro del trimestre enero–marzo coincide con el centro de febrero. La señal trimestral
negativa recibe más fuerza allí y aparece un valle que no estaba en EEX. Es consecuencia de
representar un contrato de entrega por su punto medio, no evidencia observada de que febrero
deba ser más barato. Además, sus meses promedian por horas **130.637302**, mientras el trimestre
original permanece **120**. El comprobador informa esa diferencia sin reconciliarla.

Esta lógica es una hipótesis de estimación defendible, pero no garantiza una forma económica
correcta ni consistencia entre agregados. Un trimestre representa una entrega completa, no un
precio puntual en su centro. Al visualizar, ordena los meses por entrega y representa los
trimestres como intervalos/agregados: unir M+1…M+10,Q+1,Q+2 en una sola línea mezclaría periodos
solapados. El [ejemplo auditado](ALGORITMO.md#local-month-quarter-example) contiene los pesos.

## 3. Qué hacen hoy backtest y tune

### 3.1. La idea, explicada como una prueba de producto

Imagina una tabla de precios con casillas completas y vacías. En una casilla realmente vacía
no conocemos la respuesta: no podemos comprobar directamente si rellenarla con 100 o con
110 es correcto. En una casilla completa sí tenemos una respuesta observada. La tapamos,
pedimos al algoritmo que la reconstruya y después destapamos el número para medir el error.

Esto responde a **«¿cómo reconstruye un precio observado cuando se lo retiro y mantengo
las demás pistas disponibles?»**. Es útil porque compara configuraciones contra una respuesta
común y comprobable. Su capacidad para representar huecos reales depende de que esas pruebas
se parezcan a los huecos que habrá que rellenar. Un precio líquido observado y un vencimiento
que nunca tiene operaciones no son necesariamente situaciones equivalentes.

La respuesta del examen es **el precio propio observado**, no EEX. Si el propio vale 120,
EEX 112, el candidato A estima 119 y B estima 114, sus errores absolutos son 1 y 6; EEX tiene
error 8. A acierta más en ese caso aunque se aleje más de EEX. El VWAP observado es una
referencia medible de nuestras operaciones; no por ello es un precio teórico perfecto ni
necesariamente simultáneo con los settlements de todos los demás contratos.

| Herramienta | Qué hace | Qué decisión toma |
|---|---|---|
| `backtest` | Oculta conocidos uno por uno y evalúa una configuración, además de diagnósticos por técnica. | No busca automáticamente parámetros ni reserva por sí solo un tramo para elegir ganador. |
| `tune` | Repite ese examen para una rejilla de candidatos en fechas anteriores y valida al ganador en fechas posteriores. | Propone una configuración común para los productos evaluados. |
| Notebook MLflow | Ejecuta esa búsqueda, permite una rejilla ampliada y guarda parámetros, métricas y artefactos. | Utiliza el criterio de `tune`; MLflow registra y muestra, no decide qué significa «mejor». |

La búsqueda no modifica automáticamente la configuración de producción. Los ejemplos
numéricos de este apartado son didácticos; no son resultados obtenidos con datos reales.

<a id="backtest-product-settings"></a>

### 3.2. Configuración global, individual y búsqueda por producto

La curva es la identidad exacta **`(product, region, unit)`**. El nombre de producto no
basta para distinguir regiones o unidades; Month y Quarter son familias de esa misma curva.

Producción admite `[run].configuration_mode="global"` o `"individual"`. Global ignora las
celdas opcionales de modelo del mapping. Individual aplica las no vacías sobre el TOML;
las vacías heredan. Se admiten los 33 controles escalares y la lista `tenors`, y cada fila
calculada registra los valores efectivos. El mapping puede ser CSV o la primera hoja Excel.
Guarda **un valor fijo por parámetro y curva**, no mallas de candidatos ni perfiles cerrados.
La columna `profile` conserva el significado de perfil de entrega. Véase
[CONFIGURATION.es.md](CONFIGURATION.es.md) para todas las columnas, comandos y ejemplos.

La base histórica y su evaluación siguen separadas por curva y modo de ajuste. Usar la
misma memoria no mezcla todos los precios propios. CROSS puede conectar curvas mediante
sorpresas de bases propias e históricos, no mediante sus precios de relleno ya calculados.

El alcance del experimento es otra elección del notebook/JSON:

| Opción | Configuración fija de partida | Qué selecciona |
|---|---|---|
| `backtest` configurado | La configuración efectiva de producción de cada curva | Nada: evalúa los valores actuales. |
| Búsqueda MLflow global | Valores globales; ignora excepciones de modelo del Excel | Un candidato común para las curvas `fill` seleccionadas. |
| Búsqueda MLflow individual | Base efectiva de cada curva según el selector de producción | Un candidato por identidad, manteniendo las otras curvas fijas como helpers. |

En el notebook se indican `SEARCH_SCOPE`, `SELECTED_CURVES`, `PARAMETER_GRID` y, si hace
falta, `PRODUCT_GRIDS`. `SEARCH_PLAN_PATH` permite cargar un JSON con el plan. Una malla
específica de producto **sustituye** a la común para ese producto; no añade sus ejes por
mezcla. Los campos no explorados mantienen su valor fijo. Se pueden buscar los 33 controles
escalares; objetivos, identidades, disponibilidad operativa y convenciones quedan fijos.
La búsqueda no cambia el Excel ni el plan. El CLI `tune` conserva sus seis campos: usa el
notebook para campañas individuales y mallas ampliadas.

Ejemplo: cuatro candidatos y dos productos. En global se prueban cuatro configuraciones
comunes y se elige una. En individual se prueban cuatro para A y cuatro para B, eligiendo
una para cada uno. `MAX_TRIALS` limita cada búsqueda, no el total de la campaña. No se
exploran todas las combinaciones conjuntas posibles de valores para A y B.

Las búsquedas individuales comparten el corte cronológico obtenido de la unión de fechas
de los productos seleccionados. Cada producto evaluado necesita evidencia suficiente antes
y después del corte; si no la tiene se informa del error, sin inventar un ganador. Cada
búsqueda necesita predicciones comunes en al menos dos fechas de calibración y evidencia
EEX utilizable en validación. Las curvas activas no seleccionadas siguen como helpers,
sin puntuar como productos objetivo.

La campaña individual terminada también ejecuta juntos todos los ganadores con parámetros
fijos y registra una comprobación combinada, incluidas sus interacciones CROSS. Usa **las
mismas fechas reservadas** y no vuelve a optimizar. Es una comprobación conjunta, no un
segundo examen final independiente. Cambiar repetidamente la malla después de mirar esas
fechas hace que dejen de ser validación intacta. La [guía MLflow](MLFLOW.es.md) explica
registros, curvas guardadas y formato del plan. El resultado es una propuesta; no se
despliega automáticamente.

<a id="backtest-masking"></a>

### 3.3. ¿Cuántos reales oculta? Uno cada vez, pasando por todos los elegibles

La técnica se llama **leave-one-out (LOO)**: dejar uno fuera. No escoge un porcentaje aleatorio
ni mantiene ocultos simultáneamente todos los precios que va evaluando.

Para un producto y una fecha con M+1, M+2, M+3 y Q+1 elegibles hace estas cuatro pruebas:

| Prueba independiente | Precio propio ocultado | Propios que siguen visibles ese día |
|---|---|---|
| 1 | M+1 | M+2, M+3 y Q+1 |
| 2 | M+2 | M+1, M+3 y Q+1 |
| 3 | M+3 | M+1, M+2 y Q+1 |
| 4 | Q+1 | M+1, M+2 y M+3 |

En cada prueba retira el precio del periodo tanto de los propios como de las anclas de ambos
modos antes de decidir ratio/aditivo. Ejecuta el pipeline configurado para los objetivos,
incluidas las capas habilitadas y shape si corresponde, y busca la predicción del contrato
ocultado. El precio retirado no puede actuar como original protegido en shape. El resto de
contratos y el histórico anterior utilizable siguen disponibles.

Después de cada prueba vuelve a partir de los datos del día: las ocultaciones **no se
acumulan**. Ocultar M+2 no significa haber perdido también M+1 porque se examinó antes.
Las operaciones se hacen en memoria; no borran los precios de los CSV de entrada.

**Se oculta un periodo físico completo.** Si dos etiquetas representan exactamente las mismas
fechas de entrega, se agrupan y se retiran juntas para no dejar una copia de la respuesta.
Por ejemplo, el 29 de septiembre, para Base y convención diaria natural, D+1 y BOM entregan
ambos desde el 30 de septiembre hasta el 1 de octubre (fin excluido). No se puede tapar una
etiqueta y conservar la otra como pista. Las observaciones equivalentes se consolidan con
pesos de volumen positivo; volumen cero o desconocido usa peso 1. Esa consolidación cuenta
como un caso, no como varias pruebas independientes.

Un trimestre y uno de sus meses **no** son alias: sus periodos son distintos. Por eso Q+1
puede seguir visible cuando se evalúa uno de sus meses. Si están disponibles los otros dos,
puede haber mucha más información que en un hueco real que afecte a todo el trimestre.

### 3.4. Qué precios entran en el examen y cuáles quedan fuera

No todo valor presente en el CSV se convierte automáticamente en un caso de evaluación:

Esta lista describe `backtest` configurado y el CLI `tune` de seis campos. Las campañas
MLflow fijan la población sin los dos últimos filtros, como se explica debajo de la lista.

1. La identidad debe estar activa como `fill`, con referencia EEX asignada. `helper` puede
   aportar información, pero no se evalúa como curva de salida; `off` queda excluido.
2. Tiene que haber precio propio finito, tenor interpretable y horas de entrega positivas
   según el perfil y la zona horaria del mapping.
3. Debe existir un precio EEX finito para ese periodo: directo o reconstruible por el motor
   de referencias, respetando la fecha de corte y la antigüedad permitida.
4. Si el volumen consolidado es conocido, debe superar o igualar `method.min_volume`.
   Se suman los volúmenes conocidos: todos desconocidos dejan el total desconocido, pero
   un cero conocido más otros desconocidos deja total cero. El total desconocido no se
   rechaza por este filtro. Por defecto el mínimo es 0.
5. Si `method.max_anchor_dev > 0`, se exige que
   `abs(own - eex) / max(abs(eex), ratio_eex_floor)` no supere ese límite. Por defecto es 0,
   que desactiva este filtro.

Las guardas que impiden usar una observación como ancla de **ratio** no la eliminan por sí
solas del conjunto evaluado. Ser un caso del examen y ser una pista admisible para una fórmula
son dos decisiones distintas. Los originales se conservan aunque no sirvan como anclas.

**La lista `targets.tenors` no limita actualmente qué conocidos examina LOO.** Si el objetivo
de salida contiene solo meses, pero también hay D+1 elegibles en el input, esos diarios pueden
participar en la evaluación. Dentro de una curva cada caso pesa igual en el MAE: muchos diarios
pueden influir más que unos pocos calendarios. El peso igual por curva no equilibra familias.

En un `backtest` directo, los filtros configurados de volumen y desviación definen la
elegibilidad. La campaña MLflow fija en cambio su población de examen desactivando esos dos
filtros para seleccionar las verdades. Los valores de cada candidato siguen filtrando
las anclas visibles y el aprendizaje de su propio modelo: subir `min_volume` o endurecer
`max_anchor_dev` no permite borrar respuestas difíciles del examen. Las guardas ratio
también afectan al estimador sin eliminar verdades evaluadas. Identidades, calendario,
objetivos y disponibilidad EEX permanecen fijos en la malla. Por eso la campaña puede
examinar más casos que un backtest directo filtrado con los mismos parámetros. El CLI
`tune` de seis campos conserva sus filtros configurados fijos. Una búsqueda API ampliada
también fija la población si su malla varía esos filtros o el umbral ratio.

Actualmente **no existe un argumento para ocultar dos precios a la vez, el 40 %, una familia
completa o varios días consecutivos**. Esas pruebas están propuestas en el apartado 5. Tampoco
se mide con este LOO el caso de rellenar un objetivo sin ninguna referencia EEX construible.

### 3.5. Fechas, número de pruebas e histórico disponible

En `backtest`, `--from` y `--to` delimitan las fechas a evaluar. En el notebook se usan
`START_DATE` y `END_DATE`, ambos incluidos. El tuning identifica las fechas distintas con
observaciones propias válidas y tenors interpretables de curvas activas `fill`, y reserva las
últimas `VALIDATION_DAYS`. Son fechas con observaciones, **no días naturales ni número de
precios**. La división por fechas se hace antes de conocer cuántos casos pasarán todos los
requisitos EEX/filtros del apartado anterior; no garantiza igual número de casos por fecha.

Ejemplo de una curva, con 20 fechas de observación y cuatro periodos elegibles en cada una:

| Parte | Fechas | Casos ocultados por candidato | Uso |
|---|---:|---:|---|
| Calibración | Las primeras 15 | 15 × 4 = 60 | Comparar combinaciones y elegir una. |
| Validación con `VALIDATION_DAYS=5` | Las últimas 5 | 5 × 4 = 20 | Evaluar únicamente la combinación elegida. |

Con seis combinaciones se ejecutan **6 × 60 = 360 casos de calibración** y **20 de validación**
para el ganador. Se trata de 80 observaciones distintas examinadas repetidamente, no de 380
precios independientes. Son casos intentados; una configuración puede dejar algunos sin
predicción. Las filas de métodos diagnósticos y las curvas completas generan otros recuentos.

| Control | Qué controla | Qué no controla |
|---|---|---|
| `START_DATE` / `END_DATE` | Intervalo de referencia evaluado. | No eliminan necesariamente el histórico anterior usado para aprender. |
| `VALIDATION_DAYS` | Últimas fechas de observación reservadas para el ganador; ejemplo del notebook: 5. CLI `--validation-days`: 20 por defecto. | No es un porcentaje de precios ocultos. |
| `PARAMETER_GRID` | Listas de valores que se combinan para crear candidatos. | No define el patrón de huecos. |
| `MAX_TRIALS` | Límite de combinaciones por búsqueda/producto; si se supera, se rechaza el grid. | No reduce aleatoriamente precios ni recorta el examen. |
| `EEX_OFFSET_DAYS` | Disponibilidad EEX simulada; `None` conserva el TOML. | No cambia la fecha de entrega del objetivo ni es una dimensión del grid. |

El tuning exige `warmup_days=0`, que significa **usar todo el pasado original suministrado**,
no «no usar pasado». Los parámetros de caducidad, olvido y elegibilidad siguen limitando qué
evidencia resulta utilizable. El backtest de una sola configuración sí admite el calentamiento
limitado configurado; puede entonces diferir de una ejecución con más historia.

Las predicciones son cronológicas. El precio ocultado de hoy no se incorpora al aprendizaje
antes de predecirlo. Con offset 0 el motor actualiza después de las predicciones del día; con
offset negativo libera las parejas históricas cuando su fecha ya es admisible. Los originales
de días anteriores de validación pueden alimentar días posteriores, sin cambiar los parámetros
elegidos. Así se simula que seguimos recibiendo datos, no que esos precios permanezcan
ausentes durante toda la validación. El apartado 3.9 detalla la disponibilidad EEX.

<a id="backtest-metrics"></a>

### 3.6. Qué mide, con un ejemplo calculado

Para evaluar el comportamiento completo usa **`pipeline_configured`**. Los métodos `local_*`,
`hist_*`, `blend_*` y otros del fichero LOO son diagnósticos: no todos reproducen las mismas
capas, guardas, fallback o ajuste de forma de producción. Un método diagnóstico puede tener
predicciones en casos diferentes de otro; comparar sus medias sin revisar los casos puede
confundir facilidad del examen con calidad.

Supongamos tres casos de una misma curva, todos predichos, con precios en EUR/MWh:

| Caso | Propio ocultado | Predicción | EEX de referencia | Error modelo: predicción − propio | Error absoluto modelo |
|---|---:|---:|---:|---:|---:|
| 1 | 100 | 103 | 110 | +3 | 3 |
| 2 | 120 | 116 | 110 | −4 | 4 |
| 3 | 80 | 85 | 90 | +5 | 5 |

- **MAE**: media del error absoluto: `(3 + 4 + 5) / 3 = 4 EUR/MWh`. Describe cuánto se
  equivoca de media sin permitir que los errores positivos y negativos se cancelen.
- **RMSE**: raíz de la media de errores al cuadrado:
  `sqrt((3² + (−4)² + 5²) / 3) = 4.08248 EUR/MWh`. Da más importancia a errores grandes.
- **Sesgo (`bias`)**: media del error con signo: `(3 − 4 + 5) / 3 = +1.33333 EUR/MWh`.
  Positivo significa que estima por encima en promedio. Un sesgo de cero puede esconder
  errores grandes opuestos: +20 y −20 se cancelan en sesgo, pero tienen MAE 20.
- **MAE EEX**: `(10 + 10 + 10) / 3 = 10 EUR/MWh`. También se compara contra el propio.
- **Score en este ejemplo de una curva**: `4 / 10 = 0.4`. El error medio absoluto es el 40 %
  del de EEX, una reducción del 60 %. No significa «40 % de aciertos».
- **`normalized_skill`**: `1 − score = 0.6`. Es otra forma de expresar la misma comparación;
  no es una métrica independiente ni una probabilidad.

Cada caso pesa igual en estas medias. El volumen puede influir en la estimación y en la
consolidación de observaciones equivalentes, pero **no pondera el MAE del examen**. El ranking
usa el score tras aplicar el filtro de cobertura; RMSE y sesgo son diagnósticos, no criterios
adicionales de desempate.

**Cobertura** responde a otra pregunta: «¿en cuántos casos consiguió dar un precio?». Si hay
100 propios elegibles con EEX y el modelo predice 90, la cobertura es `90 / 100 = 90 %`.
Los diez sin predicción no reciben error cero ni una penalización monetaria inventada. Se
registran como no cubiertos y la selección trata esa cobertura por separado.

En los informes de tuning/MLflow:

| Columna | Significado |
|---|---|
| `n_baseline` | Número de casos elegibles con propio ocultado y benchmark EEX. Es el denominador de cobertura. |
| `n_available` | Casos en los que el pipeline produce una predicción finita. |
| `n_missing` | `n_baseline − n_available`. |
| `coverage` | `n_available / n_baseline`; 0.9 equivale al 90 %. |
| `n_paired` | Casos usados para calcular errores: en calibración, los predichos por todos los candidatos; en validación, los predichos por el ganador. |
| `n_paired_dates` | Fechas distintas representadas en esos casos de error. |
| `n_curves` | Identidades representadas en esos casos; no número de tenors. |
| `mae_model`, `rmse_model`, `bias_model` | Errores del pipeline sobre esos casos, en las filas por unidad. |
| `mae_eex`, `rmse_eex`, `bias_eex` | Mismas medidas para EEX y contra los mismos propios. |

El informe global `overall` deja vacías las métricas absolutas y utiliza el score sin unidad.
Las filas `unit` muestran los errores en su propia unidad; no se mezcla un MAE EUR/MWh con
otro GBP/MWh como si fueran lo mismo. Si el ganador no predice ningún caso de validación,
la cobertura es cero y los errores/score no son evaluables, no cero.

El comando `backtest` guarda `backtest_loo.csv` y `backtest_report.csv`; el esquema de ese
resumen diagnóstico es distinto del informe de tuning. Una observación puede producir varias
filas LOO, una por método: contar todas esas filas no equivale a contar precios ocultados.
La cobertura del examen tampoco es la cobertura de todos los huecos reales ni la «cobertura
de filas» que muestra el visor de curvas completas.

<a id="backtest-selection"></a>

### 3.7. Cómo elige una configuración ganadora, exactamente

La elección implementada sigue este orden:

1. Ejecuta todos los candidatos sobre las fechas de calibración y comprueba que comparten
   los mismos casos base, propios ocultados, benchmark EEX y política de disponibilidad.
2. Cuenta sus predicciones finitas. Solo pueden ganar los que alcanzan la **máxima cobertura**.
3. Para comparar errores usa los casos que **todos los candidatos** predijeron, incluidos
   los candidatos que no pueden ganar por tener menor cobertura. Esa intersección debe
   contener casos de al menos dos fechas.
4. Para cada identidad calcula `MAE_modelo / MAE_EEX` en esos casos comunes.
5. Promedia los cocientes dando el mismo peso a cada identidad. Entre los candidatos con
   máxima cobertura gana el menor score. Un empate exacto conserva el primero del grid.
6. Evalúa únicamente al ganador sobre las fechas posteriores reservadas. Esos errores
   no participan en la selección ni ajustan automáticamente sus parámetros.

Por tanto, es **una prioridad por cobertura y después por error relativo**, no una suma
ponderada de todas las cualidades posibles de una curva.

**Ejemplo de dos productos.** Supón dos curvas EUR/MWh con igual número de casos:

| Curva | MAE EEX | MAE modelo | Cociente |
|---|---:|---:|---:|
| A | 10 | 6 | 0.6 |
| B | 1 | 1 | 1.0 |

El score global es `(0.6 + 1.0) / 2 = 0.8`, y `normalized_skill=0.2`. Sin embargo, el MAE
absoluto conjunto baja de `(10 + 1) / 2 = 5.5` a `(6 + 1) / 2 = 3.5`, una reducción del
36.36 %, no del 20 %. El score resume mejoras relativas por curva, no una reducción universal
del error monetario. Una curva con pocos casos pesa igual que otra con muchos; dentro de
cada curva pesan igual los casos, sin equilibrar familias de tenor ni fechas.

**Ejemplo de prioridad de cobertura.** A predice 99 de 100 casos con MAE 1; B predice los
100 con MAE 1000 en los casos comunes. B puede ganar por cobertura aunque su error sea enorme.
Así funciona el criterio actual: no existe un umbral que diga «ese relleno adicional no
compensa». Su conveniencia depende del coste real de abstenerse frente al de dar un mal precio.

**Ejemplo de un efecto de la intersección.** En una misma curva, tres casos de fechas distintas
tienen todos error absoluto EEX 10. Estos son los errores absolutos de tres candidatos:

| Candidato | Caso 1 | Caso 2 | Caso 3 | Cobertura |
|---|---:|---:|---:|---:|
| A | 0 | 100 | 0 | 3/3 |
| B | 10 | 0 | 0 | 3/3 |
| C | 0 | Sin predicción | 0 | 2/3 |

Si solo participan A y B, el MAE común es 33.3333 para A y 3.3333 para B: gana B.
Al añadir C, el caso 2 desaparece de la intersección y solo se puntúan 1 y 3. A tiene MAE 0
y B MAE 5: ahora gana A. C no puede ganar por su menor cobertura, pero **su participación
cambia qué errores deciden el ganador**. Los errores de predicciones fuera de la intersección
no entran en el score de selección aunque esas predicciones cuenten para cobertura. Es una
limitación del criterio actual que hay que tener presente al ampliar un grid.

**EEX perfecto o casi perfecto.** Si `MAE_EEX=0`, el cociente se define como 1 cuando el
modelo también acierta todo, e infinito si comete algún error. Si el error EEX es muy pequeño,
el cociente puede crecer mucho: `0.1 / 0.001 = 100` aunque el error absoluto del modelo sea
0.1. Por eso deben leerse también MAE, tamaños de muestra y cobertura, no solo el score.
Las métricas no finitas no se convierten en ceros; el registro MLflow conserva su estado en
los artefactos aunque no aparezcan como una métrica numérica ordinaria.

EEX se usa aquí como **benchmark de comparación**, incluso si la regla de producción impide
copiar el settlement como respaldo. Esta comparación no permite concluir que el pipeline
supera al fallback EEX suavizado: ese candidato tendría que evaluarse expresamente.

### 3.8. Curvas completas y predicciones ocultadas responden preguntas distintas

El [notebook MLflow](MLFLOW.es.md) permite ver curvas completas guardadas para cada candidato
en calibración y para el ganador en validación. Conservan los propios disponibles del día:
**no son curvas LOO con el precio evaluado retirado**. Revisa en esos gráficos forma,
diferencias mes/trimestre, cambio de mes y evolución temporal; mide precisión con métricas
y predicciones ocultadas. Reproducir un original visible en el gráfico no es acertar un backtest.

Con `LOG_PREDICTIONS=True`, las hijas guardan `curves/calibration_filled.csv` y, en la ganadora,
`curves/validation_filled.csv`, además de las predicciones emparejadas. `False` omite tanto
predicciones individuales como curvas completas. El visor lee archivos persistidos, incluidos
experimentos anteriores, sin recalcular con datos actuales. Si un run antiguo no guardó
curvas, genera otro experimento para obtenerlas; no se reconstruye el archivo ausente como
si fuera la salida original de aquella ejecución.

### 3.9. Disponibilidad EEX: escenario fijo de evaluación

`eex.offset_days=0` permite publicaciones hasta la referencia T; `-1` solo hasta T-1 día
natural o anteriores. Elígelo en el TOML, utiliza `--eex-offset-days -1` en `backtest`/`tune`
o `EEX_OFFSET_DAYS=-1` en el notebook. No es un candidato de ninguno de los grids. El corte
afecta al benchmark EEX, referencias del pipeline y ventanas de precios/spreads del fallback.
Las entregas objetivo siguen resolviéndose desde T.

Con offset negativo, el histórico libera parejas originales Propio_h/EEX_h solo si `h<T`
y `h<=T+offset_days`, conservando h para decaimiento/caducidad. Nunca aprende una pareja de
fechas distintas. LOCAL e HIST siguen disponibles; la sorpresa actual de CROSS exige EEX
del mismo día, por lo que no aporta ajuste cross actual con offset negativo, incluso con
`layer_cross=True`. Las covarianzas cross sí aprenden parejas históricas exactas; los pesos
de correlación local también siguen siendo utilizables.

En lunes con offset -1, el corte es domingo. La publicación del viernes tiene tres días de
antigüedad respecto al lunes: `max_stale_days=2` la rechaza y cero en el TOML significa sin límite.
Revisa `eex_offset_days`, `eex_cutoff_date`, `eex_asof` en LOO/predicciones emparejadas y la
política de disponibilidad en los metadatos de tuning.

Compara 0 y -1 en ejecuciones separadas, identificadas, con las mismas fechas previstas y
el mismo enmascarado. Un score menor no demuestra por sí solo mejor modelo: cambian la
referencia EEX y posiblemente los propios evaluables. Informa cobertura y claves comunes;
empareja expresamente los casos antes de atribuir diferencias a precisión. Las fechas del
CSV son una convención diaria, no un registro de disponibilidad intradía o revisiones históricas.

<a id="backtest-limits"></a>

### 3.10. Qué demuestra esta prueba y qué falta para evaluar otros tipos de huecos

| Pregunta | Qué permite afirmar la implementación actual |
|---|---|
| ¿Reconstruye bien un propio cuando retiro ese periodo y conservo las demás pistas? | Se mide en los casos elegibles de las fechas elegidas. |
| ¿Cuál de estas combinaciones gana? | Una gana bajo la prioridad cobertura/score y el conjunto común descritos; no es necesariamente la mejor fuera de ese examen. |
| ¿Cada producto tiene su propio ganador? | En búsqueda global hay uno común; en búsqueda individual hay uno por identidad y una comprobación conjunta posterior. |
| ¿Funciona cuando faltan tres meses juntos o no hay propios durante una semana? | Este enmascarado no lo reproduce directamente. |
| ¿Funciona para objetivos sin EEX construible? | Esos casos no entran en el examen LOO actual. |
| ¿Evalúa solo los tenors que me interesan producir? | No necesariamente: también examina otros propios elegibles del input. |
| ¿Premia que meses y trimestres cuadren, o que no haya saltos extraños? | No como términos directos del ranking. Shape puede cambiar las predicciones, pero se puntúan sus errores, no su apariencia. |
| ¿Demuestra que los parámetros funcionarán en todos los mercados y estaciones? | No. Hay un único corte temporal por ejecución y no se informa incertidumbre del ganador. |
| ¿Los tests y la demo sintética prueban precisión sobre mis datos reales? | Verifican comportamiento y escenarios controlados; no acreditan esa precisión real. |

Ocultar conocidos sigue siendo útil: proporciona respuestas contra las que medir. La limitación
está en **qué situaciones representa el examen**. Si la producción tiene muchos huecos amplios
o persistentes, hacen falta pruebas de esos patrones, manteniendo los valores ocultos también
durante el aprendizaje histórico. No basta con aumentar el número de combinaciones del grid.

Una validación posterior ayuda a comprobar si la elección se sostiene fuera de las fechas que
la eligieron. Si se consulta repetidamente ese resultado para cambiar el grid, deja de ser una
comprobación independiente de todas esas decisiones humanas. El apartado 5 describe ampliaciones
propuestas: varios cortes temporales, bloques ausentes, evaluación por familias y horizontes,
errores extremos y coherencia. **Son propuestas, no argumentos disponibles actualmente.**

Para auditar esta explicación en el código: [preparación y ocultación](src/vwaps/fill.py),
[selección y métricas](src/vwaps/tuning.py), [informes del backtest](src/vwaps/cli.py) y
[registro de experimentos](src/vwaps/experiments.py). Ampliar esta guía no cambia el algoritmo.

## 4. Qué parámetros se pueden comparar

La rejilla del comando CLI admite seis campos:
`basis_mode`, `tau_log`, `shrink_k`, `layer_hist`, `layer_correlation`, `layer_cross`.
Por defecto solo varía los tres modos de basis; las otras dimensiones requieren listas explícitas.

El [notebook de experimentos](MLFLOW.es.md) admite los 33 controles escalares, incluidos
memoria, fallback, shape, filtros de anclas y guardas ratio; esa guía enumera los nombres
Python exactos. Su población de examen se fija independientemente de los filtros candidatos,
como explica el apartado 3.4. Las indicaciones «fijo en tune» o «solo entra en la rejilla»
de la tabla siguiente se refieren exclusivamente al CLI de seis campos. Targets,
convenciones, disponibilidad y otras opciones operativas siguen fuera de ambas mallas.

| Familia | Controles actuales | Qué permiten estudiar |
|---|---|---|
| Alcance local | `method.tau_log`, `other_kind_weight`, `shrink_k` | Alcance entre vencimientos, mezcla de tipos y fuerza de la señal local. Solo tau/shrink entran en la rejilla actual. |
| Volumen y observaciones | `method.min_volume`, `max_anchor_dev` | Qué anclas se admiten. La transformación `ln(1+volumen)` está fijada en código: no existe un selector de ponderación por volumen. |
| Fórmula y estabilidad | `method.basis_mode`, `ratio_eex_floor`, `max_ratio_deviation` | Diferencia absoluta/proporcional y guardas numéricas. Solo basis_mode entra en la rejilla. |
| Memoria propia | `layers.hist`; `method.ewma_halflife_days`, `hist_max_age_days`, `hist_auto_min_obs` | Permitir historia, rapidez de adaptación, caducidad y evidencia para hist-auto. Solo el modo de capa entra en la rejilla. |
| EEX suavizado | `eex_fallback.price_method`, `price_window`, `ewma_halflife`, `spread_window`, `anchor_months` | Media/EWMA, publicaciones de precios/spreads, intensidad exponencial y meses ancla. Todos quedan fijos dentro de tune. |
| Correlación local | `layers.correlation`; `correlation.halflife_days`, `prior_obs` | Activación y memoria/confianza de pesos aprendidos. Solo activación entra en la rejilla. |
| Ayuda entre curvas | `layers.cross`; `cross.min_corr`, `min_obs`, `halflife_days` | Activación, admisibilidad y memoria de relaciones. Solo activación entra en la rejilla. |
| Disponibilidad y rutas | `eex.offset_days`, `max_stale_days`; `layers.local`, `arbitrage` | Corte de publicaciones, antigüedad, ajuste local y reconstrucción. Disponibilidad fija dentro de cada grid; local/arbitrage solo varían si se solicita en el notebook ampliado. |

Los parámetros no incluidos en la rejilla requieren una comparación adicional controlada;
cambiar el TOML y ejecutar tune varias veces no garantiza por sí solo que los universos de
evaluación sigan siendo iguales. El límite predeterminado de la rejilla es 50 combinaciones.

**No todo lo configurable se debe optimizar por error.** Mapping, unidades, zona horaria,
convenciones, columnas y rutas deben representar correctamente los datos. La lista objetivo
define lo que se quiere producir y debe mantenerse comparable. `warmup_days=0` permite usar
todo el pasado suministrado; reducirlo cambia la evidencia. `eex.warn_stale_days` afecta al aviso,
no al precio. `eex.max_stale_days=0` significa sin límite, no solo publicaciones del mismo día.

## 5. Pruebas que añadiría para comparar estrategias

**Lo siguiente es un protocolo propuesto, no nuevas opciones ya implementadas.**

Generar curvas sintéticas completas y guardar su verdad antes de ocultar datos. Si el escenario
supone consistencia, construir sus Q/Cal a partir de la misma verdad de meses por horas; en otro
escenario se pueden introducir diferencias explícitas entre agregados. Probar varios mecanismos:
basis aditivo, proporcional, pendiente por vencimiento, estacionalidad, cambios bruscos, ceros y
negativos. Repetir semillas. Ningún generador demuestra por sí solo precisión real.

El generador sintético actual ya tiene memoria temporal, factores por grupo y días vacíos,
pero no una estructura específica de basis por estación/vencimiento, y su volumen aleatorio
no determina el ruido del precio. No basta para decidir si otra ponderación de volumen es mejor.

Usar exactamente las mismas máscaras para todos los candidatos:

- Un hueco aislado, como el backtest actual.
- Dos o tres meses consecutivos, o todos los meses de un trimestre.
- Solo M+1 y Q+2 visibles, para reproducir la duda concreta del experimento.
- Toda una familia o varios días completos sin precios propios.
- Memoria insuficiente, caducada o tras un cambio de nivel; cambio de mes/trimestre.

Las ausencias persistentes deben retirarse del input **antes de preparar y actualizar el
histórico de cada día**, incluidas las curvas ayudantes afectadas. Ocultar solo una predicción
y después dejar que su verdad entrene HIST/cross no reproduciría esa ausencia.

Calibrar sobre fechas anteriores, congelar parámetros y evaluar en otras posteriores. Repetir
con varios puntos de corte; conservar un tramo final sin usarlo para elegir. El principio de
separación temporal evita entrenar con el futuro de lo evaluado; véase la documentación oficial
de [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
El proyecto no utiliza esa clase ni implementa hoy esta repetición de cortes.

Medir por identidad, familia, plazo y patrón de ausencia, no solo una media global:

| Medida | Estado y lectura |
|---|---|
| MAE, RMSE, sesgo y cobertura | Ya disponibles en distintos informes; revisar casos comunes y denominador. Menor error, sesgo cercano a cero y cobertura suficiente. |
| Error extremo, por ejemplo percentil 95 | Propuesto: detectar métodos con una buena media y fallos ocasionales grandes. |
| Error de spreads entre meses | Propuesto: compara diferencias estimadas con diferencias de la verdad oculta. |
| Trimestre frente a sus meses | Ya hay diagnóstico de consistencia; no participa en el score de tune. |
| Curvatura y estabilidad del ajuste | Propuesto: medir saltos del basis y evolución temporal. No premiar una curva plana ni eliminar estacionalidad legítima. |
| Cambio de etiqueta M+1/M+2 | Propuesto: seguir también el mismo periodo absoluto para no interpretar un cambio de contrato como un salto artificial. |

## 6. Otras lógicas LOCAL que merece la pena comparar

Son alternativas de diseño pendientes, no mejoras demostradas:

| Alternativa | Idea | Límite que debe medirse |
|---|---|---|
| Cercanía por días naturales | Sustituir distancia logarítmica por distancia en días con un alcance interpretable. | También necesita decidir cómo representa los contratos largos; no garantiza consistencia. |
| Solapamiento de entrega | Dar peso al trimestre según cuánto cubra la entrega del mes; usar distancia para contratos no solapados. | Hay que normalizar por horas para no favorecer contratos largos por tamaño. No impone por sí sola que los meses sumen el trimestre. |
| Mismo tipo primero | Usar meses para meses y consultar otros tipos cuando falte evidencia suficiente. | Puede perder información útil; hace falta definir qué es evidencia suficiente. La separación estricta ya se consigue con other_kind_weight=0 y correlation=false, pero el respaldo jerárquico todavía no existe. |
| Interpolar el ajuste mensual | Interpolar basis entre anclas mensuales, con una política explícita fuera de ellas. | Un trimestre no es una ancla mensual puntual. Pocas anclas y extrapolación requieren respaldo. |
| Otra ponderación de volumen | Comparar pesos iguales, logaritmo actual o influencia limitada. | Confirmar primero qué mide total_volume: MW, MWh u otra unidad. Normalizar por horas solo tendría sentido si la definición lo justifica. |
| Ajuste conjunto de contratos | Ya implementado opcionalmente como shape, con penalizaciones suaves y límites; no como otro estimador LOCAL. | Originales incompatibles pueden dejar residuos. Solo se mueven originales con permiso explícito; véase la capa implementada al final. |

Para interpolación mensual, PCHIP es una posible alternativa a splines que pueden introducir
sobreoscilaciones: preserva la monotonía de los datos interpolados, según su
[documentación oficial](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html).
Eso no garantiza monotonía del precio final al combinarlo con EEX ni coherencia de agregados.
No está incorporado al proyecto.

Empezaría comparando el local actual con **distancia natural** y **solapamiento de entrega**,
y probaría pesos iguales frente al logaritmo de volumen. Después estudiaría la memoria y el
respaldo EEX donde realmente se usan. Ajustar todas las opciones a la vez dificultaría atribuir
la mejora y aumentaría el riesgo de elegir una combinación que solo funciona en la muestra.

## 7. Qué se puede ejecutar hoy

Con input, mapping y EEX configurados para la máquina de destino:

```powershell
python run.py backtest
python run.py tune --basis-modes auto,ratio,additive --hist-modes off,auto --validation-days 20 --max-trials 50
```

El segundo comando prueba seis combinaciones si no se varían las otras dimensiones. Requiere
al menos 22 fechas de observaciones válidas y dos fechas de calibración con predicciones comunes,
además de evidencia evaluable en validación. El experimento de dos anclas de un único día no
cumple ese requisito y no ha producido un ganador de tune.

Lee `backtest_loo.csv`, `backtest_report.csv`, `tuning_calibration.csv`,
`tuning_validation.csv` y `tuning_selected.json` en el output configurado. Tune propone
parámetros y registra la configuración de partida; **no modifica config.toml**.

<a id="shape-investigation"></a>

## 8. Investigación de la forma: evidencia y conclusión

**Fuentes primarias consultadas.** Callegaro et al. (2022), sección 5.2 y apéndice A,
suavizan la corrección para conservar la estacionalidad y relacionan precios con promedios
sobre las entregas. Esto respalda investigar un ajuste por intervalos, en lugar de representar
todo un trimestre únicamente por su centro. [Artículo completo](https://onlinelibrary.wiley.com/doi/full/10.1002/asmb.2645).

Fleten y Lemming (2003) combinan información de mercado, forma estacional y un objetivo de
suavidad con restricciones de precios. Su propuesta no se limita a dibujar una línea agradable.
[Publicación original](https://www.sciencedirect.com/science/article/pii/S0140988303000392).

**Aplicación opcional implementada:** la capa shape regulariza ajustes mensuales sobre EEX,
con penalizaciones suaves de coherencia; véase [su alcance actual](#optional-shape-layer). La idea es penalizar cambios bruscos del ajuste
y representando cada Q/Cal mediante el promedio por horas de sus entregas. Usar EEX como
referencia de forma es nuestra adaptación, no una mejora demostrada por esos artículos para
este input. No exigir una curva plana, monótona ni universalmente positiva.

**Prueba de sensibilidad ejecutada.** Sobre las mismas dos anclas sintéticas, sin historia,
cross, correlation ni arbitrage, se probaron las 27 combinaciones aditivas de:

- `tau_log`: 0.25, 0.5, 1.
- `shrink_k`: 0.5, 1, 2.
- `other_kind_weight`: 0, 0.2, 0.6.

Los originales M+1=110 y Q+2=120 se mantuvieron. Se define valle interior estrictamente como
`febrero < min(enero, marzo)`; no basta con que febrero esté por debajo de la media de sus
vecinos, pues eso también puede suceder en una secuencia monótona curvada.

| Resultado del experimento | Qué demuestra |
|---|---|
| 18 de 18 combinaciones con peso cruzado 0.2/0.6 tienen mínimo interior en febrero | El efecto persiste en esta rejilla; no es exclusivo del valor por defecto. |
| Las 9 combinaciones con peso cruzado 0 no tienen ese mínimo | Se elimina aquí al dejar de utilizar Q+2 para los meses, sacrificando esa información. |
| El promedio mensual supera al trimestre original entre 6.979617 y 35.411089 | Estos parámetros no imponen la igualdad trimestral. No son errores frente a verdad mensual conocida. |

Por ejemplo, con tau=0.5 y shrink=1, cambiar el peso cruzado de 0.6 a 0 cambia enero–marzo
de **132.239522, 128.569541, 130.903094** a **151.054476, 150.681660, 150.474199**.
La segunda secuencia ya no tiene valle, pero su promedio **150.738738** se aleja más del
trimestre propio 120. Una forma aparentemente más regular no identifica por sí sola un ganador.

En este escenario hay además una razón algebraica. Sean `a` y `b` los pesos no negativos
del mes y trimestre, con `a+b>0`, y `k=shrink_k>0`. Sin historia/cross:

```text
ajuste = (10×a − 30×b)/(a+b+k)
ajuste + 30 = (40×a + 30×k)/(a+b+k) > 0
```

Con EEX=150 en esos meses, cada precio estimado supera 120. El caso a=0 tampoco alcanza
120 mientras k>0 y exista evidencia local. No basta con variar pesos para imponer una
ecuación que el estimador no contiene. Esta demostración se limita a estas anclas y supuestos.

**Controles que introduciría antes de seleccionar otro refill.** Evaluar los errores sobre
huecos ocultados junto con: spreads entre entregas, cambios bruscos del basis, agregación por
horas, sensibilidad a una sola ancla y estabilidad del mismo contrato absoluto entre fechas.
Informar qué parte procede de originales y cuál de estimaciones. Los umbrales necesitan
calibración por curva y horizonte; un salto no debe rechazarse automáticamente si hay evidencia
propia que lo explique. Actualmente solo parte de esto existe en el informe de consistencia.

Para un output que conserva VWAPs históricos, una discrepancia de agregación no prueba una
oportunidad de arbitraje: los contratos pueden haberse operado a distintos momentos o con
distintas muestras. EEX, por su parte, publica settlements entre sus datos de cierre diario.
[EEX End-of-Day](https://www.eex.com/en/market-data/eex-group-datasource/end-of-day-prices).
La necesidad de imponer igualdades exactas dependería de si se busca una curva de valoración
coherente o completar observaciones conservando sus diferencias; esa es una decisión de uso.

Los artefactos de esta revisión local están en `output/anchor_influence_test/`:
`run_shape_sensitivity.py`, `shape_sensitivity.csv` y `shape_sensitivity.json`.
La carpeta output está ignorada por Git y esos archivos no forman parte del repositorio
publicado. El estudio verifica sensibilidad y estructura, **no predicción real ni parámetros
óptimos**, y no cambia el comportamiento de producción.

<a id="rollover-audit"></a>

## 9. Cambio de mes y trimestre: qué se conserva y qué puede saltar

Hay que distinguir cambiar de etiqueta de cambiar de precio para una misma entrega.
El 30-09-2026, M+1 significa octubre y M+2 significa noviembre. El 01-10-2026, M+1 significa
noviembre. El motor resuelve estas fechas y busca el mismo periodo absoluto en las publicaciones
EEX utilizadas. Para estudiar estabilidad se siguen `delivery_start` y `delivery_end`, junto
con identidad y perfil; comparar únicamente M+1 entre fechas compararía contratos distintos.

**Prueba ejecutada con el motor actual.** Se suministraron nueve publicaciones EEX sintéticas,
terminando el 30 de septiembre, ninguna publicación el 1 de octubre y ningún precio propio.
Se desactivaron historia, cross, correlation y arbitrage en la configuración del experimento.
El respaldo conserva sus valores por defecto: EWMA de cinco publicaciones, vida media dos,
media simple de nueve spreads y `anchor_months=2`. La configuración de producción no se editó.

| Misma entrega absoluta | 30-09-2026 | 01-10-2026 | Diferencia |
|---|---:|---:|---:|
| Noviembre 2026 | M+2: 114.000000, cascada | M+1: 116.659473, EWMA directa | +2.659473 |
| Enero–marzo 2027 | Q+2: 156.659473, EWMA directa | Q+1: 156.659473, EWMA directa | 0 |

En las nueve publicaciones, octubre vale siempre 100 y los spreads noviembre menos octubre
son 10, 11, ..., 18. El 30 de septiembre, noviembre se calcula como
`100 + media(10,...,18) = 114`. El 1 de octubre ya entra entre los meses promediados directamente:
la EWMA de sus últimos cinco precios, 114, 115, 116, 117 y 118, vale **116.659473**. Los pesos
normalizados, del más antiguo al reciente, son 0.088947075, 0.125790159, 0.177894149,
0.251580318 y 0.355788298. El último settlement sigue siendo 118 y `eex_asof` sigue siendo
30-09-2026 en ambas estimaciones. No hay nueva información que explique esa diferencia.

**La causa es un cambio de fórmula al cambiar el calendario.** El límite entre promedio directo
y cascada, y el mes desde el que parte la cascada, se calculan desde la fecha de referencia.
También puede verse afectado un mes que continúa en cascada si cambia su mes de partida.
El control trimestral no cambia porque aplica la media directa al mismo periodo y las mismas
publicaciones en ambas fechas. Esto no prueba que todos los trimestres sean siempre estables:
con nuevos datos o distintas capas pueden cambiar. Sí demuestra que este salto concreto
no es un error de emparejamiento ni un movimiento del contrato observado.

Otros efectos que conviene distinguir:

- **LOCAL:** sus pesos dependen del tiempo restante hasta la entrega. Pueden variar aunque los
  precios de las anclas no cambien. Además, una ancla propia de ayer no es una ancla local de hoy.
- **HIST:** no se reinicia al cambiar de mes. Conserva ajustes por tipo, grupo y conjunto de la
  curva; no guarda una memoria independiente para cada entrega absoluta o estación del año.
  Transferir esa diferencia a otra entrega es una hipótesis del modelo que hay que evaluar.
- **Disponibilidad:** una ventana incompleta impide el respaldo. Con `eex.max_stale_days=0`
  en el TOML no hay límite de antigüedad admitida; el aviso de tres días no bloquea el relleno.

**Qué mejoraría primero, sin cambiarlo en esta revisión:** investigar una regla de respaldo
que controle estos cambios de fórmula y evaluar su continuidad manteniendo fijos contratos
y publicaciones. Medir por separado los cambios explicados por nueva información, por
envejecimiento de la evidencia y por cambio de ruta. No imponer que todos los precios queden
congelados ni que la curva sea plana. Hay que contrastar cualquier alternativa con precisión,
cobertura y forma, además de este caso de calendario.

La reproducción está en `output/rollover_audit/run_audit.py`; `comparisons.csv`, `filled.csv`
y `summary.json` registran valores, métodos y supuestos. Se ejecuta desde la raíz con
`.venv/Scripts/python.exe -B output/rollover_audit/run_audit.py`. Los cálculos manuales coinciden
con el motor, que no informó errores. Son artefactos locales ignorados por Git. La prueba
confirma una limitación de estabilidad; no mide el error frente a precios propios reales ni
modifica el algoritmo.

<a id="optional-shape-layer"></a>

## 10. Capa opcional de forma implementada y cómo validarla

La capa está implementada y **apagada por defecto**. Se ejecuta después del refill y combina
cambios pequeños, segundas diferencias del ajuste mensual aditivo `precio-eex_settle` y
residuos de agregación Quarter/Year ponderados por horas. Usa EEX bruto admitido actualmente,
no la EWMA del respaldo. La regularización necesita tres meses consecutivos con el mismo
`eex_asof`; la agregación necesita los tres/doce meses completos, pero no requiere EEX.
No rellena missing ni ajusta Day/Week/Season/BOM/BOW.

Los modos son `off`, `audit` (solo propuestas) y `adjust` (aplica una solución validada y
limitada). Por defecto los precios propios de la curva son fijos. La opción explícita
`adjust_originals=true` permite moverlos con mayor penalización de fidelidad; el input bruto
siempre queda intacto. Los originales cambiados pasan a `estimated`/`own+shape`/
`shape_adjusted_original`: no se presentan como observaciones. Los estimados modificados
conservan la procedencia previa y reciben `+shape`.

Los controles y valores iniciales implementados son `shape.mode="off"`,
`shape.adjust_originals=false`, `shape.smoothness_weight=1.0`,
`shape.coherence_weight=10.0`, `shape.coherence_tolerance=0.01`, `shape.max_abs_adjustment=10.0` y
`shape.original_weight=10.0`. Son valores de partida, no un óptimo calibrado.
[SHAPE.es.md](SHAPE.es.md) detalla objetivo, límites, consola y ejemplos. El solver convexo
con límites usa NumPy y valida condiciones KKT; si falla conserva precios previos.
La coherencia es suave: originales incompatibles o límites estrechos pueden dejar residuos
aunque la resolución sea correcta. Confidence no se usa como varianza.

**Agregación y forma no son lo mismo.** Restar 10.637302 a cada mes del ejemplo anterior
lleva el promedio por horas a 120, pero conserva todos los spreads y el valle de febrero.
Además supera el límite inicial de movimiento de 10. Es una demostración aritmética, no la
salida esperada del nuevo solver. Hay que evaluar ambos objetivos conjuntamente; los alias
y varias estimaciones que comparten anclas no son evidencia independiente.

Resolver una fecha no exige historia adicional; siguen vigentes la memoria y las ventanas
EEX del refill. Para elegir penalizaciones y límites sí hace falta evaluación histórica con
bloques ausentes, fechas posteriores, estaciones, horizontes y cambios de mes, sobre el mismo
universo. Un número de años no certifica solapamiento ni cobertura suficientes.

`pipeline_configured` evalúa shape después de ocultar el periodo y todos sus alias: la verdad
oculta no puede actuar como restricción original de shape. Historia y cross siguen aprendiendo
solo de originales reales. El CLI mantiene sus seis dimensiones y shape fijo; el
[notebook de experimentos](MLFLOW.es.md) sí puede incluir explícitamente los campos shape
en su grid. Compara sobre observaciones emparejadas: errores, extremos, cobertura, residuos,
tamaño de cambios y movimiento de propios.
Ocultar bloques y validar múltiples orígenes siguen siendo protocolos propuestos, no nuevas
funcionalidades de esta capa.

La motivación para suavizar la corrección conservando estacionalidad aparece en la sección
5.2, observación 10 de [Callegaro et al.](https://onlinelibrary.wiley.com/doi/full/10.1002/asmb.2645).
Nuestra adaptación con EEX no demuestra mejora predictiva. No impone una curva plana,
monótona ni positiva. No están implementadas una plantilla EEX suavizada conjuntamente ni
regularización temporal. La coherencia entre vencimientos no corrige por sí sola el
[salto del mismo contrato al cambiar de mes](#rollover-audit): requiere una prueba temporal
independiente y no debe esconderse mediante un gráfico más suave.

**La tolerancia de coherencia es diagnóstica, no una restricción obligatoria.**
`shape.coherence_tolerance=0.01` compara el valor absoluto del residuo
promedio-mensual-menos-padre con 0.01 en la unidad de precio de la curva. Subirla relaja la
comprobación y bajarla la endurece. No cambia el optimizador ni el precio aceptado.
Penalizaciones suaves, originales protegidos y límites de movimiento pueden dejar un resultado
fuera del margen. Audit señala la propuesta; adjust señala el resultado publicado. El trace
informa cada agregado y el resultado global, o null si no hay agregado aplicable. El mismo
valor numérico se interpreta en la unidad de cada curva; no es un porcentaje ni convierte monedas.

Véanse [el alcance de power y la muestra EEX local](SHAPE.es.md#por-qué-es-específico-de-power-y-qué-mostró-la-muestra-eex): concordancia agregada no exige meses iguales ni constituye una regla universal para todos los activos.

## 11. Influencia DayAhead: comportamiento actual y límites de validación

La auditoría sintética aislada usa el 30-09-2026, D+1 propio a 100 frente a EEX 80,
volumen 100, modo aditivo, sin historia y los parámetros actuales de distancia/tipo/shrink.
Con EEX objetivo 120, esa ancla de +20 produce **M+1=121.069779, M+4=120.028826 y
Q+2=120.017899**. La influencia se vuelve pequeña, pero no desaparece. Al existir ajuste
LOCAL, no entra el respaldo EEX suavizado: una influencia mínima puede cambiar la ruta
de cálculo. Es comportamiento existente, no una mejora predictiva demostrada.

La correlación actual aprende **sorpresas del basis propio−EEX entre grupos de contratos**;
no es un modelo solo EEX ni una relación individual M+1 frente a M+4. La revisión EEX usó
42 publicaciones, del 10-08 al 06-10-2026: los cambios diarios de abril 2027 correlacionaron
aproximadamente 0.81–0.85 con contratos fijos de noviembre–febrero, sobre 41 pares.
Sin verdad histórica propia no permite clasificar rellenos ni validar un ancla ganadora.
Elegibilidad por familia y un previo basado en EEX validado contra propios siguen siendo
propuestas. Los artefactos están en `output/local_anchor_review/`, ignorado por Git;
la auditoría no cambió la lógica de producción.
