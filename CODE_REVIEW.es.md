# Revisión del código y correcciones

Fecha de revisión: **2026-10-07**. [English version](CODE_REVIEW.md).

La revisión encontró defectos de implementación capaces de cambiar precios, saltarse una
exclusión, distorsionar los informes de evaluación o dañar resultados guardados. Las
correcciones descritas tienen pruebas de regresión. Es una revisión de ingeniería con
reproducciones sintéticas e inspección del código; **no certifica** la precisión de los
precios propios estimados. El input propio real no está disponible en este equipo.

## 1. Alcance y cómo interpretar el informe

Se revisaron el motor, duplicados y alias, vinculación con EEX, reconstrucción de contratos,
configuración, fechas CSV/XLSX, selección de días, publicación, backtest/tuning y notebook.
Además de revisar las pruebas existentes, se prepararon reproducciones que mostraban el
fallo antes de corregirlo.

Las prioridades describen el impacto: **P1**, posible pérdida o sustitución de resultados
válidos; **P2**, cálculo, filtro o informe incorrecto en un caso admitido; **P3**, un caso
numérico o de informe menos frecuente. No son una valoración financiera del riesgo ni una
afirmación de que ya se han encontrado todos los errores posibles.

Se mantiene el modelo LOCAL existente. Las restricciones por familias, horizontes y selección
de anclas mediante correlación siguen siendo investigación, explicada en
[ANCHORS.es.md](ANCHORS.es.md). La capa opcional de forma sigue siendo un ajuste separado,
limitado y con condiciones blandas.

## 2. Hallazgos y correcciones del motor

| Hallazgo | Ejemplo y consecuencia | Corrección implementada |
|---|---|---|
| P2: el promedio de duplicados dependía del orden | Tres precios 90,100,110 con volumen desconocido/cero podían dar 97,5,100 o102,5 en lugar de 100. También sucedía al mezclar volúmenes positivos y desconocidos. | Agregar el conjunto completo con pesos efectivos separados. El volumen positivo finito aporta su peso; en otro caso se usa 1 para promediar. El volumen informado se conserva aparte y sigue siendo desconocido si todos lo son. |
| P2: el primer alias original decidía la familia | Cuando D+1 y BOM cubren la misma entrega, invertir las filas podía cambiar el tipo del ancla, su histórico y la estimación de otro objetivo:106,083542 frente a104,824008 en la reproducción. | Elegir un tipo representativo determinista, ordenar/eliminar repeticiones de las etiquetas y usar en LOO una etiqueta compatible con ese tipo. Se preserva el input original. |
| P2: arbitrage podía reutilizar un original excluido | Un M+2 de 900 con poco volumen quedaba excluido si solo se pedía Q+1, pero podía volver a entrar si también se incluían los meses entre los objetivos. Q+1 pasaba a360,751471. | Aplicar la exclusión del periodo a las dos vías por las que llegan contratos a la reconstrucción. El original se conserva en el output, pero no aporta evidencia al cálculo. |
| P2: el filtro de volumen dependía de encontrar EEX | Un original sin cotización EEX podía saltarse la comprobación de volumen mínimo y entrar después en arbitrage. | Comprobar el volumen conocido antes de buscar EEX. El volumen bajo conocido queda excluido; el volumen desconocido conserva su elegibilidad documentada. |
| P2: una cabecera Peak de cero horas bloqueaba un residual válido | Agosto 2026 Peak tenía 252 horas. Su primer fin de semana aportaba cero horas Peak del mes y el resto conservaba las 252. Si el mes valía 100, ese resto también debía valer 100. | Admitir la cabecera de cero horas sin exigirle precio. Si la cabecera tiene horas positivas, sigue necesitando evidencia. Se conservan las protecciones de Peak Day/Weekend. |

El constructor de `CurveFiller` también valida toda la configuración antes de calcular,
incluido el uso directo desde Python. Véanse [fill.py](src/vwaps/fill.py),
[pricer.py](src/vwaps/pricer.py) y las [pruebas del motor](tests/test_engine_audit.py).

**Límite importante:** hacer determinista la agregación de alias observados no hace que
todos los alias sin observar utilicen el mismo estimador. D+1 y BOM todavía pueden recibir
precios distintos para las mismas fechas porque sus tipos activan rutas distintas de
LOCAL/HIST/fallback. Las pruebas del motor reproducen esta diferencia. La comparación con
verdad sintética ahora rechaza predicciones incompatibles para un mismo periodo físico;
no escoge una en silencio ni las cuenta dos veces. Rediseñar esa selección por tipo sigue
siendo una tarea de modelo pendiente.

## 3. Entradas, configuración y publicación

| Hallazgo | Comportamiento anterior | Corrección implementada |
|---|---|---|
| P1: un EEX presente pero ilegible/mal formado se trataba como ausente | Una repetición podía terminar con éxito y sustituir una estimación previa 125,4489 por `missing`. | Un fallo al cargar un fichero EEX presente aborta antes de publicar resultados. También se rechaza una entrada no vacía sin precios finitos o sin contratos admitidos. La ausencia/vacío deliberados se tratan por separado. |
| P1: las escrituras directas podían truncar el histórico o mezclar versiones | Un fallo inyectado dejó en 24 bytes un histórico de 745; otros ficheros podían contener ya datos nuevos. | Preparar primero todos los CSV/textos, bloquear escritores concurrentes, guardar copias anteriores, reemplazar cada fichero de forma atómica e intentar restaurar ante un fallo de publicación. |
| P2: se podía perder un fin de semana solicitado | `daily` podía acabar sin generar el sábado indicado; refill podía omitir un sábado con originales inválidos. | Incluir la fecha diaria explícita y las fechas originales activas aunque no aporten anclas válidas. |
| P2: un duplicado EEX contradictorio se resolvía por la última fila | Los precios 100 y999 para la misma publicación/entrega daban uno u otro según el orden. | Rechazar duplicados finitos contradictorios; una repetición idéntica no añade evidencia. |
| P2: las conversiones de configuración cambiaban la intención | El texto `"false"` podía convertirse en verdadero; un tenor escrito como texto podía partirse en caracteres; se aceptaban valores no finitos o límites inválidos. | Validar tipos originales, números finitos, modos, límites y sintaxis de tenors, sin conversiones silenciosas de estos casos. |
| P2: fechas numéricas de Excel se convertían en 1970 | Números como 45931 o20260928 podían interpretarse como nanosegundos Unix. | Rechazar fechas numéricas ambiguas y valores con zona horaria. Compartir el parser de fecha de calendario entre input propio, EEX y metadatos de periodos del comparador. |

La verdad sintética opcional se valida antes de publicar el backtest. El JSON de tuning se
prepara en el mismo lote que sus CSV. El bloqueo protege la fase de leer, combinar y escribir,
no solamente el cambio de nombre final. Véanse [publication.py](src/vwaps/publication.py),
[pruebas de publicación](tests/test_io_publication.py),
[configuración](tests/test_config_validation.py) y [fechas](tests/test_input_dates.py).

### Límites de la publicación

- El reemplazo es atómico **por fichero**. El conjunto no es una transacción del sistema de
  archivos: un apagado o una terminación forzada entre reemplazos puede dejar generaciones distintas.
- Se intenta restaurar ante errores ordinarios, `KeyboardInterrupt` y `SystemExit`.
  Una regresión cubre incluso la interrupción posterior al cambio de nombre pero anterior
  al retorno de la llamada Python. Tras restaurar, se propaga la interrupción original.
  Un fallo de limpieza no oculta el error principal. Si también falla la
  restauración, se conservan las copias de recuperación y se indican sus rutas.
- El notebook no adquiere el bloqueo de escritura al leer; puede observar una publicación en curso.
- Se ha ejecutado el código en Windows. Existe una rama de bloqueo POSIX, pero no se ha
  ejecutado en Linux durante esta revisión.
- Se siguen admitiendo un fichero ausente y un CSV EEX válido con solo cabecera como entradas
  sin datos. Recalcular con ellas todavía puede sustituir estimaciones anteriores por `missing`;
  no se ha añadido una regla general que conserve el precio antiguo cuando faltan datos actuales.
  Una entrada no vacía sin ninguna cotización finita/admitida se rechaza. Si hay precios válidos
  e inválidos mezclados, se conservan los válidos con una advertencia. Esto no comprueba la
  plausibilidad económica de cada cotización.

## 4. Backtest y tuning

| Hallazgo | Resultado anterior | Corrección implementada |
|---|---|---|
| P2: originales ajustados entraban en la precisión de los huecos | Un original 100 cambiado a110 se incluía como `own+shape`, aunque el informe decía evaluar celdas sin precio propio. | Excluir contratos observados utilizando su procedencia actual y previa a la forma, incluidos sus alias. |
| P2/P3: añadir un alias cambiaba el peso de un error sintético | Errores 2 y0 daban MAE 1; un alias BOM idéntico del primer contrato cambiaba el MAE a1,3333. | Contar una vez cada entrega física si alguna de las dos tablas incluye sus fechas. Rechazar predicciones, verdades o procedencias contradictorias entre alias. |
| P3: errores grandes finitos daban estadísticas infinitas | Elevar 1e200 al cuadrado desbordaba; el redondeo de presentación también podía desbordar cerca de 1e308. | Escalar el cálculo de media/RMSE y redondear sin desbordar. Rechazar diferencias cuyo valor real no sea representable. |

Si **ninguna de las dos tablas tiene metadatos del periodo**, se mantiene la comparación
antigua por etiqueta de tenor. Si los metadatos están presentes pero incompletos/son inválidos,
se informa del error. También se rechaza que una misma etiqueta coincida con periodos distintos.
Las llamadas directas al comparador informan de columnas obligatorias ausentes con un
`ValueError` explicativo, en vez de un `KeyError` incidental.

Código y pruebas: [backtest.py](src/vwaps/backtest.py) y
[regresiones de informes](tests/test_backtest_reporting_regressions.py).

No se identificó filtración de datos futuros en las rutas LOO/tuning inspeccionadas:

- LOO retira todo el periodo ocultado, incluidos sus alias, del input propio y de las anclas
  de ambos modos antes de elegir ratio/aditivo. La forma recibe ese conjunto ya ocultado.
- Se recalculan las dependencias de los objetivos de producción antes de extraer la predicción oculta.
- El histórico se actualiza después de predecir el día y aprende de observaciones originales.
- Los candidatos se eligen con fechas anteriores; solo el elegido se evalúa después.
  Los originales de un día de validación pueden informar a los días siguientes, como en ejecución diaria.
- Tuning comprueba igualdad de claves, verdades y referencias EEX entre candidatos.

Son comprobaciones apoyadas por pruebas, no una demostración que cubra toda ejecución posible.

## 5. Notebook y demostración

Se trataron tres problemas diferentes:

1. Los datos antiguos de demostración no contenían todas las familias. El
   [generador reproducible](examples/generate_notebook_demo.py) aporta ahora datos con las
   familias y horizontes admitidos. El visor sigue sin inventar contratos ausentes.
2. Utilizar `None` para «todos los tipos» podía comportarse como un selector sin valor.
   El notebook usa un valor explícito y lo traduce al consultar los datos.
3. Plotly podía recortar contratos sin precio en un extremo al no existir un punto finito
   que fijase su posición. Se indica ahora la lista completa de categorías y el rango del eje;
   los huecos permanecen visibles y no se conectan mediante una línea (`connectgaps=False`).

Meses, trimestres y años que empiezan el mismo día mantienen posiciones distintas. Producto,
región y unidad no se mezclan. Promediar entregas absolutas y promediar etiquetas móviles son
vistas diferentes. Un gráfico agradable no demuestra precisión.
Véanse [NOTEBOOK.es.md](NOTEBOOK.es.md) y las [pruebas del visor](tests/test_visualization.py).

## 6. Estado de la verificación

El conjunto integrado final pasa **561 tests en Windows con Python 3.14.2**, y los mismos
**561 tests con Python 3.11.1**, la serie mínima de Python documentada. La segunda ejecución
usó NumPy 1.26.4, pandas 2.2.0 y openpyxl 3.1.5; produjo 30 avisos de cambios futuros de pandas
al concatenar columnas vacías/todas NA, sin tests fallidos. Son dos ejecuciones del mismo
conjunto, no 1.122 pruebas distintas. No se suman las ejecuciones parciales a ese total.

Se comprobaron permutaciones de filas, volúmenes mixtos/desconocidos, ceros y negativos,
reglas Peak, ocultación de periodos completos, conflictos de alias, conservación de originales,
aritmética extrema finita, EEX/configuración inválidos, fallos inyectados de escritura,
reemplazo y recuperación, bloqueo de escritores, fines de semana explícitos y contratos sin
precio en los gráficos.

Para ejecutar el conjunto completo desde la raíz:

```powershell
.venv/Scripts/python.exe -B -m pytest -q
```

Se ejecutaron todas las celdas y callbacks interactivos con el kernel registrado de la `.venv`
del proyecto y datos sintéticos de siete fechas, tres identidades de curva y los nueve tipos.
Se comprobaron ambas alineaciones de las medias, M+0 a M+15, Q+1 a Q+8 y la curva completa.
También se inspeccionó visualmente la curva renderizada en Edge sin interfaz visible.
Es evidencia local; no certifica todos los navegadores, instalaciones Jupyter, sistemas
operativos ni conjuntos de datos externos.

## 7. Decisiones de modelo y riesgos que siguen existiendo

- LOCAL puede trasladar un ancla entre familias y horizontes distantes. Ponderar por distancia
  y volumen es una regla heurística; no demuestra que todos esos traslados sean útiles.
- Un peso LOCAL positivo diminuto puede activar la rama basada en EEX actual en vez del
  fallback temporal. Un ajuste propio pequeño puede provocar un cambio de ruta mucho mayor.
- El ancla del fallback mensual cambia al cambiar el calendario. Seguir el contrato absoluto
  evita confundir etiquetas, pero no elimina por sí solo ese cambio de fórmula.
- El histórico agrupa por tipo/familia, sin aprender una corrección estacional independiente
  para cada entrega. Tener memoria configurable no garantiza que la evidencia siga siendo relevante.
- La evidencia de covarianza envejece cuando llegan nuevas observaciones emparejadas; el mero
  avance del día no vuelve a validar de forma independiente una relación antigua.
- La capa de forma utiliza `eex_settle` actual y penalizaciones blandas de agregación.
  La tolerancia informa de una discrepancia pendiente; no impone igualdad ni garantiza factibilidad.
- Los alias estimados de distinto tipo todavía pueden discrepar. El informe hace visible el
  conflicto; esta revisión no ha rediseñado la selección de estimadores para resolverlo.
- LOO no reproduce bloques ausentes persistentes ni familias/días completos sin precios.
  Priorizar cobertura y comparar sobre la intersección de candidatos siguen siendo políticas explícitas.
- La confianza es heurística, no un intervalo de error calibrado. Los precios modificados por
  forma no tienen confianza calibrada. Ganar con datos sintéticos no valida una configuración real.

Estos límites y las evaluaciones propuestas están en [ANCHORS.es.md](ANCHORS.es.md),
[BACKTEST.es.md](BACKTEST.es.md) y [SHAPE.es.md](SHAPE.es.md). Elegir el refill de producción
todavía requiere validar con precios propios reales, especialmente en cambios de calendario,
curvas con pocas anclas y cambios del comportamiento del mercado.

## 8. Reproducibilidad y trabajo operativo pendiente

El proyecto se ejecuta desde su código con Python 3.11+; no se distribuye como un paquete
compilado/instalable. `pyproject.toml`, `uv.lock` y la instalación documentada son coherentes
con ese uso. Input, capturas y salidas generadas están excluidos del repositorio; no hace falta ZIP.

Tuning guarda configuración e identificación de la entrada, pero un manifiesto más completo
incluiría hashes de datos, commit y versiones de dependencias. Automatizar CI para las
combinaciones de Python/sistema admitidas sigue siendo una mejora; pasar pruebas localmente
no equivale a una certificación multiplataforma.

Parte de las reproducciones iniciales están en `output/code_audit/`, excluido de Git. Las
pruebas de regresión, las correcciones y el generador de demostración sí proporcionan evidencia
reproducible en una copia limpia. Los artefactos locales son complementarios y no son inputs
necesarios para producción.
