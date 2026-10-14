# Qué observaciones deberían servir de ancla a un precio de power ausente

[English](ANCHORS.md) · [Algoritmo](ALGORITMO.md) · [Guía de backtest](BACKTEST.es.md) · [Capa de forma](SHAPE.es.md)

**Nota de investigación, no un modelo nuevo de producción.** Un precio observado puede ayudar
a completar unos contratos y perjudicar otros. Ni «usar todas las observaciones cercanas» ni
«no mezclar nunca familias» gana universalmente. Distinguimos el programa actual, los
contraejemplos controlados y la evaluación propuesta. No se implementan nuevos parámetros de elegibilidad.

## 1. Qué información intentamos trasladar

LOCAL traslada el **ajuste observado respecto a EEX**, no el precio propio directamente.
En modo aditivo, un mes propio a 110 frente a EEX 100 aporta un ajuste +10. Un objetivo con
EEX 150 puede usar parte de ese +10 según distancia, volumen, tipo y shrinkage. No copia
simplemente 110. El modo ratio traslada un ajuste proporcional.

La pregunta importante es: **¿el ajuste propio−EEX de esta observación ayuda a predecir el
ajuste propio−EEX del objetivo?** La correlación entre precios EEX responde a otra pregunta.
En `Own = EEX + basis`, una correlación elevada entre dos series EEX no determina la relación
entre sus basis. Ambos precios de mercado pueden moverse juntos mientras las muestras de
operaciones propias tienen desviaciones independientes.

EEX ya aporta la referencia de mercado de cada objetivo. Una correlación solo EEX podría
evaluarse como información previa de relevancia, pero no identifica por sí sola la mejor
ancla propia ni valida un método de relleno.

## 2. Qué hace LOCAL actualmente

Para una fecha e identidad `(product, region, unit)`, los pares propios/EEX válidos reciben
pesos por logaritmo del volumen, tipo y distancia logarítmica al centro de la entrega.
El mismo tipo tiene factor 1; otro tipo usa normalmente `other_kind_weight=0.6`.
La distancia no mide solapamiento de entrega. La suma de pesos también determina cuánto se
modera el ajuste: `w=W/(W+shrink_k)`.

Filtrar una ancla cambia **tres cosas**: qué ajustes se mezclan, la fuerza total de la evidencia
y, posiblemente, la ruta completa de cálculo. Un ajuste LOCAL válido minúsculo puede impedir
que entre el respaldo calculado con precios históricos EEX.

Con correlation activada se aprenden relaciones entre **sorpresas del basis por grupo de
contratos**, no relaciones independientes solo EEX para cada pareja M+1/M+4. Una sorpresa
es el ajuste observado hoy menos su media histórica anterior. La correlación modifica un
factor de ponderación; no crea otro estimador de precios ni aprende una prohibición por familia.

## 3. Experimentos controlados: contraejemplos útiles, no un ganador

El artefacto local `output/anchor_family_review/run_experiment.py` utilizó el motor real
mediante una subclase experimental: **cuatro escenarios × cuatro políticas = 16 ejecuciones,
208 comprobaciones**. Inputs y verdades ocultas eran sintéticos. Fecha: 30-09-2026;
volumen de anclas 100; modo aditivo forzado; `tau_log=0.5`,
`other_kind_weight=0.6` y `shrink_k=1`. Historia, correlation, cross, arbitrage y shape
estaban apagados. Nueve publicaciones EEX completas permitían el respaldo normal de cinco
precios EWMA y nueve spreads.

| Política experimental | Qué cambia |
|---|---|
| `current` | Pesos actuales. |
| `block_short_to_month_plus` | Peso cero desde anclas del grupo short hacia objetivos month/quarter/long. |
| `same_kind_only` | Solo contribuye el mismo tipo: Month ayuda a Month, no a Quarter. |
| `containment_cross_kind` | Bloquea short→long; permite distintos tipos Month/Quarter/Year solo si una entrega contiene la otra. Entre periodos del mismo tipo mantiene los pesos actuales. |

Son nombres del experimento, no claves del config. El prototipo filtra pesos; no convierte
los trimestres en ecuaciones de agregación. Los pesos LOCAL estrictamente del mismo tipo
ya pueden obtenerse con `other_kind_weight=0` y correlation apagada, pero eso no garantiza
aislamiento de familias en todo el pipeline. Las anclas observadas aquí son Day y Month;
esta prueba no evalúa empíricamente Quarter/Year como familias de origen.

| Supuesto sintético | Resultado real del motor | Qué enseña |
|---|---|---|
| Todos comparten basis +12; se observan D+1 y M+1 | Q+1 verdadero 112; actual 103.2764; mismo tipo 100 | Prohibir toda mezcla puede eliminar información compartida útil. |
| Basis diario +40, mensual +10, trimestral −20 y anual −25 | Q+1 verdadero 80; actual 102.8942; mismo tipo 100 | El ajuste mensual puede apuntar en dirección contraria. Bloquearlo tampoco descubre el −20 ausente. |
| Solo octubre–noviembre tienen prima +30; se observa octubre | Enero M+4 verdadero 100; actual y mismo tipo 103.1634 | Un filtro por familia no evita una transferencia inadecuada entre meses cercanos y lejanos. |
| Misma prima localizada, con verdad trimestral/anual coherente por horas | Cal+1 verdadero 100: actual 100.3291 y contención 100. Q+1 verdadero 119.8959: actual 108.0713 y mismo tipo 100 | La relación entre entregas importa; eliminar todo Month→Quarter también puede perjudicar. |

El escenario de familias separadas representa deliberadamente relaciones distintas entre
VWAPs observados, no una superficie única de valoración coherente. El de prima localizada
obtiene los agregados a partir de meses y horas reales. Todos los objetivos ocultos recibieron
estimación finita porque las ventanas del respaldo estaban completas; no garantiza conservar
cobertura con datos reales escasos. Las comprobaciones verifican mecánica, no precisión real.

## 4. Un ancla diaria débil puede cambiar la referencia temporal EEX

En el cuarto escenario EEX sube durante nueve publicaciones: 80, 82, …, 96. El único D+1
propio es 98, con basis +2. El método actual calcula **M+4=96.002883** y
**Cal+1=96.000453**. El pequeño ajuste lejano parte, sin embargo, del EEX actual 96.

Al bloquear esa ancla diaria entra el respaldo **93.318945**, la EWMA normalizada con vida
media 2 de los últimos cinco precios 88, 90, 92, 94 y 96. Casi toda la diferencia de unos
2.68 procede de cambiar la referencia temporal, no de retirar la pequeña corrección propia.

Los mismos datos visibles llevan a conclusiones opuestas con dos verdades ocultas posibles:

| Precios ocultos de los cuatro contratos más largos | MAE actual | MAE bloqueando short |
|---|---:|---:|
| Permanecen en 90 | 6.031306 | 3.318945 |
| Siguen el EEX actual +2 hasta 98 | 1.968694 | 4.681055 |

El D+1 observado sigue siendo 98 en ambos mundos; solo cambian los objetivos desconocidos.
El motor nunca recibe esas verdades. El ejemplo demuestra por qué una muestra solo EEX no
resuelve qué ruta debería ganar. Revisar esta transición cuando la evidencia es débil es una
propuesta, no una corrección implementada por el experimento.

## 5. La elegibilidad propuesta debería considerar dirección y horizonte

Una posibilidad sería decidir primero si una ancla es relevante para un objetivo y después
ponderar las anclas admitidas. **No está implementada como política nueva.**

- D+1→periodos cortos cercanos y D+1→el siguiente año son preguntas distintas: una alteración
  puntual no tiene por qué representar un ajuste persistente de toda la curva.
- Month→Month cercano y Month→Month lejano también difieren, aunque compartan tipo.
- Una observación de octubre cubre parte de Q4; una de Q4 informa de un promedio de tres
  meses. Ninguna dirección identifica todos los precios mensuales ausentes.
- Un mes dentro de un año puede informar del agregado sin determinar los otros once.
  La ausencia de solapamiento tampoco demuestra que no exista un basis común de mercado.

Son hipótesis que contrastar, no prohibiciones demostradas. Una regla por dirección,
familia y horizonte debería ser explícita y auditable, sin asumir un corte universal o
relevancia simétrica.

**Trampa de selección auto.** Actualmente `auto` consulta las listas de anclas por modo
antes de aplicar los pesos locales del objetivo. Supón M+2 con EEX 100. M+1 propio 0/EEX 100
es un ancla aditiva válida, pero no ratio. Un Day propio 110/EEX 100 sí admite ratio, aunque
una política propuesta lo excluiría para M+2. Si solo se pone su peso a cero después,
auto puede elegir ratio y quedarse sin contribución local ratio utilizable. Una implementación
real de elegibilidad necesitaría filtrar **las listas ratio y additive antes de escoger modo**,
no solo multiplicar el peso final por cero. Las 16 ejecuciones, forzadas a additive, no prueban
esta interacción.

## 6. Aislar familias exige revisar también historia y cross

Un filtro solo LOCAL no aísla todo el cálculo. La historia guarda basis por tipo, con respaldo
por grupo y `all` dentro de la curva. El histórico Month también mezcla distintos horizontes
mensuales; no sigue únicamente la historia del mismo contrato absoluto.

Cross usa sorpresas por grupo y puede recurrir a `all`. Apagar la contribución histórica al
precio no borra la memoria interna ni apaga automáticamente cross. Por tanto, una regla
«las observaciones cortas nunca informan precios largos» necesitaría un tratamiento coherente
de LOCAL, selección de modo, agrupación histórica y cross. Es una consideración de
implementación, no una petición de sustituirlos por múltiples modelos.

## 7. Regiones: separar evidencia no exige ejecuciones distintas

La identidad actual `(product, region, unit)` ya separa observaciones propias, mapping,
LOCAL e historia. Con cross apagado por defecto, un mismo refill no mezcla anclas regionales.
Ejecutar por separado resulta útil si las regiones necesitan configuraciones diferentes;
esa necesidad debe validarse, no deducirse solo del nombre del país. Cross, si se activa
expresamente, traslada sorpresas aprendidas del basis bajo sus reglas, no el precio bruto de
otra región como sustituto automático.

## 8. Qué respalda la literatura primaria

- [Lucia y Schwartz, versión de autores de *Electricity prices and power derivatives*](https://escholarship.org/content/qt12w8v7jj/qt12w8v7jj_noSplash_7b7e7e1b016e6500b1e82cd9219021fa.pdf), secciones 3–4: estacionalidad, shocks transitorios y factores de corto/largo plazo motivan estudiar el horizonte. Su modelo de un factor implica correlación perfecta; otro factor permite respuestas distintas entre vencimientos. La muestra nórdica histórica no valida nuestros VWAPs.
- [Kemper, Schmeck y Balci, *The Market Price of Risk for Delivery Periods*](https://arxiv.org/html/2002.07561v3), secciones 2 y 3.2: duración y proximidad de entrega afectan a promedios y volatilidad Samuelson. Distinta volatilidad no demuestra correlación baja ni inutilidad de un ancla. Su construcción geométrica no es nuestro relleno aritmético por horas.
- [Kiesel, Schindlmayr y Börger, *A two-factor model for the electricity forward market*](https://www.tandfonline.com/doi/abs/10.1080/14697680802126530): el resumen del editor describe periodos de entrega, estructura temporal de volatilidad y calibración a opciones. Apoya considerar esas dimensiones, no una regla de elegibilidad de producción.

La muestra solo EEX disponible tiene 42 publicaciones, del 10-08 al 06-10-2026. Compara
cambios de contratos absolutos fijos, no una serie M+1 que cambia silenciosamente de entrega.
Las correlaciones recogidas en [BACKTEST.es.md](BACKTEST.es.md) no miden transferencia del
basis propio; 41 pares de cambios de precio no validan una clasificación universal de familias.

## 9. Plan de validación limitado y útil

Mantén el método existente como referencia y compara pocas hipótesis explícitas de elegibilidad.
Usa los mismos propios ocultados e informa por identidad, familia de origen, familia objetivo
y horizonte. Incluye huecos aislados, bloques, días con solo anclas cortas y días sin propios.
Agrupa todos los alias y elimina cada alias de una observación oculta antes de cualquier
aprendizaje que pueda revelarla; el enmascarado permanente de bloques sigue siendo una herramienta propuesta.

Separa calibración anterior y evaluación posterior. Mide error contra propios, errores grandes,
cobertura, cambios de ruta y estabilidad del mismo contrato al cambiar el calendario.
Evalúa por separado historia y cross antes de afirmar aislamiento completo. Cambiar filtros
no debería mejorar el score simplemente quitando casos difíciles de su denominador.

Sin historia propia real disponible aquí, **no queda validada ninguna política ni familia
ganadora**. Más filas ayudan solo si aportan fechas, horizontes, regímenes y pares propios/EEX
relevantes.

La evidencia local está en `output/anchor_family_review/metadata.json`, `predictions.csv`,
`errors_by_target.csv`, `anchor_weights.csv` y `scenario_summary.csv`, más inputs sintéticos.
Estos artefactos y el script están ignorados por Git y no se distribuyen en el repositorio.
En un entorno que los contenga se reproducen con
`.venv/Scripts/python.exe -B output/anchor_family_review/run_experiment.py`.
