# Capa opcional de ajuste de forma

[English](SHAPE.md) · [Algoritmo](ALGORITMO.md) · [Outputs](OUTPUT.es.md) · [Validación](BACKTEST.es.md#optional-shape-layer)

Esta capa implementada se ejecuta **después del relleno normal** y está desactivada por
defecto. Permite inspeccionar o ajustar una curva ya calculada. Busca un compromiso entre
cambiar poco, regularizar el ajuste mensual respecto a EEX y acercar trimestres/años a sus
meses. Una curva más suave no demuestra mayor precisión predictiva.

Las columnas del input, incluido cada `vwap` original, siempre se conservan en el enriquecido.
El precio final utilizable es `curve_price`. Por defecto también está protegido en observaciones
propias; `shape.adjust_originals=true` permite modificarlo expresamente. La memoria histórica
y cross siguen aprendiendo de las observaciones del input, nunca de los resultados ajustados.

## Tres modos de funcionamiento

- `off`: no cambia el cálculo anterior ni el esquema de salida.
- `audit`: calcula propuestas y diagnósticos, pero conserva los precios publicados.
- `adjust`: aplica una solución validada dentro de los límites configurados.

Empieza con `audit` para revisar qué observaciones se moverían y qué discrepancias quedarían.
Es una etapa distinta de `basis_mode=auto|ratio|additive` y de las medias de precios EEX.

## Qué participa

La capa trabaja por fecha e identidad completa `(product, region, unit)`, con el perfil,
zona horaria y horas de entrega del mapping. Considera precios finitos de periodos Month,
Quarter y Year completos. Cuando está activa, el motor incluye también etiquetas propias
de esos tipos fuera de `targets.tenors`, para incorporar agregados originales y permitir
su ajuste trazable cuando esté autorizado por la configuración.

Un trimestre necesita sus tres meses completos y un año sus doce, con horas compatibles.
No crea meses ausentes, no utiliza agregados parcialmente cubiertos ni rellena missing.
Días, semanas, temporadas, BOM y BOW quedan fuera del alcance inicial. Los alias de un mismo
periodo absoluto se deduplican: varias etiquetas no aportan evidencia ni restricciones independientes.

La regularización mensual necesita tres meses consecutivos completos, con referencias EEX
finitas y el mismo `eex_asof`. La referencia es el **`eex_settle` bruto admitido actualmente**,
que puede reconstruir el Pricer de EEX; no es la EWMA ni la cascada del respaldo. No aparece
una nueva ruta de suavizado o respaldo para esa referencia. La ausencia de EEX elimina ese
término de regularización, pero todavía puede usarse la coherencia de un trimestre/año completo.

## Un único objetivo conjunto

Sea `p_i` el precio anterior, `x_i` el propuesto, `E_m` la referencia EEX mensual y
`H_m` las horas de entrega del mes. El solver minimiza una suma de cuadrados:

```text
sum_i peso_fidelidad_i * (x_i - p_i)^2
+ smoothness_weight * sum_trios_validos [(x[m]-E[m]) - 2*(x[m+1]-E[m+1]) + (x[m+2]-E[m+2])]^2
+ coherence_weight * sum_agregados_completos [x_padre - sum_m(H_m*x_m)/sum_m(H_m)]^2
```

Aquí `x[m+1]` denota el precio del mes siguiente.
Se penaliza la segunda diferencia sobre índices mensuales consecutivos del ajuste respecto
a EEX; no se penaliza directamente la curva total ni se exigen precios iguales o positivos.
Esta diferencia aditiva admite ceros y negativos aunque el relleno previo haya usado ratio.
La estacionalidad de EEX sigue siendo referencia de forma, no la verdad que deba copiarse.

Los estimados tienen peso de fidelidad 1. Los originales que pueden moverse usan
`original_weight`. Con `adjust_originals=false` los originales son fijos; con true pueden
moverse dentro del mismo límite absoluto que los demás nodos elegibles. El límite siempre
se mide desde el precio **anterior a shape**: `abs(x_i-p_i) <= max_abs_adjustment`,
en la unidad de precio de esa curva.

La coherencia es suave. Un `coherence_weight` mayor penaliza más la discrepancia, pero ni
un trimestre original ni uno estimado tienen garantizada la igualdad con sus meses.
Originales incompatibles o límites estrechos pueden impedirla. El residuo restante es un
compromiso informado, no necesariamente un fallo del solver. Forma y agregación se resuelven
conjuntamente; no se alternan suavizados y reconciliaciones que deshagan sus efectos.

La implementación usa NumPy y un solver convexo con límites y validación KKT. Si falla el
solver o la solución es inválida, conserva los precios previos e informa el fallo. No acepta
una solución parcial inválida. No interpreta el confidence heurístico como varianza;
los precios cambiados llevan confidence vacío porque su incertidumbre no está calibrada.

**La tolerancia de coherencia es diagnóstica, no una restricción obligatoria.**
`shape.coherence_tolerance=0.01` compara el valor absoluto del residuo
promedio-mensual-menos-padre con 0.01 en la unidad de precio de la curva. Subirla relaja la
comprobación y bajarla la endurece. No cambia el optimizador ni el precio aceptado.
Penalizaciones suaves, originales protegidos y límites de movimiento pueden dejar un resultado
fuera del margen. Audit señala la propuesta; adjust señala el resultado publicado. El trace
informa cada agregado y el resultado global, o null si no hay agregado aplicable. El mismo
valor numérico se interpreta en la unidad de cada curva; no es un porcentaje ni convierte monedas.

## Configuración y opciones de consola

Son claves implementadas en `config.toml`. Los valores numéricos iniciales **no están calibrados**.

| Clave | Inicial | Significado y efecto |
|---|---|---|
| `shape.mode` | `"off"` | `off`, `audit` o `adjust`. Mientras está off, los demás controles no afectan a precios. |
| `shape.adjust_originals` | `false` | Booleano. True permite mover precios propios elegibles de la curva; conserva el input bruto. En audit solo cambian propuestas. |
| `shape.smoothness_weight` | `1.0` | Finito, ≥0. Mayor favorece segundas diferencias menores del ajuste mensual Own−EEX. Cero elimina la penalización. No actúa donde falta una referencia válida de tres meses. |
| `shape.coherence_weight` | `10.0` | Finito, ≥0. Mayor favorece concordancia entre agregado y promedio por horas. Cero elimina la penalización. No actúa sobre agregados incompletos. |
| `shape.coherence_tolerance` | `0.01` | Finito, ≥0. Margen diagnóstico absoluto del residuo agregado, en la unidad de precio. Mayor relaja la comprobación; no cambia precios ni impone una restricción. Sin términos agregados aplicables no hay veredicto global. |
| `shape.max_abs_adjustment` | `10.0` | Finito, >0. Movimiento absoluto total máximo por nodo. Mayor permite más cambio, no necesariamente mejores precios. Revisa la unidad; no hay conversión de moneda. |
| `shape.original_weight` | `10.0` | Finito, ≥1. Peso de fidelidad de originales móviles frente a 1 en estimados. Mayor dificulta mover originales. No tiene efecto sobre su movimiento cuando están fijos. |

```toml
[shape]
mode = "off"
adjust_originals = false
smoothness_weight = 1.0
coherence_weight = 10.0
coherence_tolerance = 0.01
max_abs_adjustment = 10.0
original_weight = 10.0
```

Los cinco comandos `daily`, `refill`, `catchup`, `backtest` y `tune` aceptan las siete
opciones: `--shape-mode`, `--shape-adjust-originals on|off`,
`--shape-smoothness-weight`, `--shape-coherence-weight`, `--shape-coherence-tolerance`,
`--shape-max-abs-adjustment` y `--shape-original-weight`. No reescriben el TOML.

```powershell
python run.py daily --date 2026-09-30 --shape-mode audit
python run.py refill --from 2026-09-01 --to 2026-09-30 --shape-mode adjust --shape-adjust-originals off
python run.py daily --date 2026-09-30 --shape-mode adjust --shape-adjust-originals on --shape-original-weight 20
```

Usa daily/refill para recalcular fechas existentes tras cambiar shape. Catchup considera
procesado un missing existente y no sustituye automáticamente grupos completos.

## Comparación calculada: proteger o mover un original

Este cálculo se comprobó directamente con el solver implementado. Supón enero–marzo de 2027
Base con 744, 672 y 743 horas, tres meses estimados a 100, un trimestre original a 130 y EEX
mensual a 100 con una publicación común. Mantén los pesos iniciales: forma 1, coherencia 10,
fidelidad original 10 y límite de movimiento 10.

| Modo | ¿Permite mover originales? | Meses publicados | Trimestre original publicado | Propuesta mes/trimestre |
|---|---|---|---:|---|
| `adjust` | No | 110, 110, 110 | 130 | 110 / 130 |
| `adjust` | Sí | 110, 110, 110 | 120 | 110 / 120 |
| `audit` | Sí | 100, 100, 100 | 130 | 110 / 120 |

Todos los meses alcanzan el límite +10. Al permitir modificar originales, el trimestre llega
a −10 y su precio final pasa a estimado; el input bruto sigue siendo 130. El residuo
meses-menos-trimestre baja de −30 a −20 con protección, o a −10 sin ella: no desaparece.
Los tres ajustes mensuales son iguales y la penalización de segunda diferencia es cero.
El ejemplo demuestra límites y procedencia, no una mejora predictiva.

## Leer un cambio sin perder la observación

Supón un VWAP original de 120. Sin permiso para ajustar originales, su `curve_price` sigue
siendo 120 aunque se muevan estimados cercanos. Si se permite y la solución validada lo lleva
a 122, `vwap` sigue siendo 120, pero `curve_price=122`, `data_origin=estimated`,
`source=own+shape` y `estimation_method=shape_adjusted_original`. Es un resultado posible
ilustrativo, no una promesa de que el solver elegirá 122.

Si dos filas físicas duplicadas valen 118 y 122 y su agregado del motor se mueve +2, sus
precios finales enriquecidos serán 120 y 124. Cada una conserva su dato bruto y su propio
`curve_price_before_shape`; el trace registra la resolución agregada. No se reemplazan
todos los duplicados por el mismo precio agregado.

En estimados cambiados, source y estimation_method reciben `+shape`. Audit conserva el
precio/source/método finales y solo informa propuestas. La salida activa añade valores
previos, cambios reales/propuestos, indicador de original modificado y trace JSON;
el [diccionario de outputs](OUTPUT.es.md) explica todas las columnas. Basis, anclas y trazas
del respaldo explican el cálculo **previo a shape**, no bastan para reproducir el precio ajustado.

## Histórico y validación

La resolución estructural no necesita una ventana histórica adicional a los datos del
relleno y las referencias de esa fecha. Siguen vigentes los requisitos normales de historia,
antigüedad y ventanas completas de EEX del refill. Elegir pesos y límites necesita evaluación
histórica real por estación, horizonte, bloques ausentes y cambios de calendario; un número
de años por sí solo no garantiza cobertura adecuada.

`pipeline_configured` del backtest incluye shape después de ocultar el periodo evaluado y
sus alias. La memoria continúa aprendiendo solo originales reales después de predecir.
Tune conserva sus seis dimensiones de búsqueda; shape queda fijo en toda la rejilla y se
guarda en el snapshot de configuración. Comparar distintas configuraciones de shape requiere
ejecuciones controladas sobre los mismos casos.

La capa no garantiza continuidad de un contrato fijo entre fechas. La
[auditoría del cambio de mes](BACKTEST.es.md#rollover-audit) sigue siendo una comprobación temporal
separada. Usar una plantilla EEX suavizada conjuntamente, cambiar la ruta de la cascada mensual
o añadir regularización temporal son propuestas distintas, no funcionalidades de esta capa.


## Por qué es específico de power y qué mostró la muestra EEX

La agregación se justifica cuando un contrato representa la misma entrega que sus componentes,
con especificaciones compatibles. EEX documenta la cascada entre periodos de entrega;
los perfiles y horas de este programa no se trasladan automáticamente a futuros agrícolas.
Por ejemplo, el contrato de maíz CME se define sobre 5.000 bushels, no con esta ponderación
de horas de power. Otro activo exige revisar sus contratos antes de reutilizar la regla.
[Detalles de contratos EEX](https://www.eex.com/en/trading-resources/product-specifications/contract-details-product-codes);
[Especificación de maíz CME](https://www.cmegroup.com/markets/agriculture/grains/corn/specs).

En la copia EEX local examinada, 13 archivos Base/Peak de siete áreas aportaron **1.125
comparaciones completas trimestre/meses**, con publicaciones del 10-08-2026 al 06-10-2026.
Todos los settlements trimestrales difirieron menos de 0.004396 de su promedio mensual
ponderado por horas, por tanto menos de 0.01. Ningún caso tenía el trimestre y los tres
meses iguales dentro de 0.01. Coherencia de agregación no significa curva plana.

Es una conclusión de esa muestra y sus casos completos, no una garantía universal del mercado
ni de concordancia entre VWAPs propios observados por separado. Los artefactos locales son
`output/eex_quarter_audit/summary.csv` y `comparisons.csv`, ignorados por Git y no distribuidos.
No calibran shape ni demuestran mejora al predecir precios ausentes.
