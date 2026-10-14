# Explorar las curvas guardadas

[English](NOTEBOOK.md) · [Notebook](notebooks/inspect_curves.ipynb) · [Diccionario de salida](OUTPUT.es.md)

El notebook lee **solo un CSV de curvas ya generado**, normalmente `filled_history.csv`.
No carga propios originales, mapping ni archivos EEX, no ejecuta el relleno, no ajusta
precios y no escribe datos. Incluye gráficos Plotly y selectores en tres pestañas separadas.

Para **ejecutar y registrar experimentos de parámetros**, utiliza el otro
[notebook de backtest + MLflow](notebooks/backtest_mlflow.ipynb) y [su guía](MLFLOW.es.md).
Ese notebook carga inputs y guarda informes de experimentos; esta página describe únicamente
el visor de curvas ya guardadas.

El notebook de experimentos incorpora esas mismas tres vistas para **candidatos guardados
en MLflow**. Elige experimento → ejecución padre → candidato → calibración/validación y carga
la curva guardada. Admite todos los tipos/tenors guardados y abre runs anteriores sin repetir
el relleno. Cada candidato guarda calibración; solo el ganador guarda validación. Las curvas
mantienen originales visibles: evalúa precisión con métricas ocultadas, no con originales
reproducidos en el gráfico. El registro debía estar activo al crear el run; no se recalculan
archivos antiguos ausentes con inputs actuales. [La guía del visor de experimentos](MLFLOW.es.md#saved-curves)
detalla controles y rutas de los archivos.
Para consultar runs de forma independiente, ejecuta solo la celda editable de ajustes de
la sección 1 y la celda del visor de la sección 8 de `backtest_mlflow.ipynb`. No necesita
preparar el dataset, disponer de input/EEX real ni ejecutar otro tuning: el visor arranca
o reutiliza el servicio local. Sus descargas son temporales y se eliminan tras cargarse.

## Arrancar en este u otro ordenador

Copia el repositorio y el CSV que quieras consultar. Se necesita Python 3.11 o posterior.
Desde la raíz del proyecto:

```powershell
uv sync --group notebook
uv run --group notebook jupyter lab notebooks/inspect_curves.ipynb
```

El grupo opcional `notebook` añade JupyterLab, ipywidgets y Plotly; no cambia las dependencias
del motor. Importar `vwaps.visualization` no arranca ningún servidor.

Sin uv, activa un entorno Python y ejecuta:

```powershell
python -m pip install "numpy>=1.26" "pandas>=2.2" "openpyxl>=3.1" "jupyterlab>=4" "ipywidgets>=8" "plotly>=5"
python -m jupyter lab notebooks/inspect_curves.ipynb
```

Selecciona el kernel Python de ese entorno y ejecuta las celdas en orden. Ambos notebooks
usan inglés en explicaciones, interfaces, código, comentarios y docstrings. Las guías
Markdown independientes están disponibles en inglés y español. Ninguna de las dos opciones
de instalación del visor requiere los inputs originales.

### Abrirlo en Visual Studio Code

Abre la **carpeta raíz del proyecto**, no solo el archivo del notebook. Instala las extensiones
**Python** y **Jupyter** de Microsoft si todavía no están disponibles. En el notebook, arriba
a la derecha, pulsa **Seleccionar kernel → Seleccionar otro kernel → Entornos de Python**
y elige `.venv`. En Windows su intérprete es `.venv\Scripts\python.exe`; en Linux/macOS,
`.venv/bin/python`. El intérprete de la terminal y el kernel del notebook pueden ser distintos:
comprueba ambos si aparecen errores de importación.

Si no aparece el entorno, ejecuta desde la raíz:

```powershell
uv sync --group notebook
uv run --group notebook python -m ipykernel install --user --name vwaps --display-name "Python (VWAPS .venv)"
```

Después ejecuta **Developer: Reload Window** desde la paleta de comandos de VS Code.
Selecciona **Python (VWAPS .venv)** en el selector de kernels de Jupyter. Este registro es local:
repítelo en el otro ordenador y cuando cambies la ubicación del proyecto. No copies `.venv`
entre ordenadores; recréalo con `uv sync --group notebook`.

Para verificar el intérprete, ejecuta en una celda:

```python
import sys
print(sys.executable)
```

Debe apuntar al Python de `.venv` en este proyecto. **`.venv` es una carpeta con Python y sus
dependencias; `.env` es un archivo de variables de entorno y este notebook no lo necesita.**
Si un aviso menciona literalmente `.env`, revisa la extensión o configuración que lo solicita;
crear ese archivo no selecciona el kernel ni instala librerías. Estas instrucciones siguen la
[gestión de kernels de VS Code](https://code.visualstudio.com/docs/datascience/jupyter-kernel-management).

## Elegir el archivo

La celda de rutas está preparada para la prueba local con
`output/notebook_validation/filled_history.csv`, un **CSV sintético que no se incluye en
GitHub**. En otro ordenador debes sustituir `OUTPUT_PATH` por el CSV que hayas generado
o copiado. La propia celda contiene ejemplos comentados para ambas situaciones.

Para crear ese ejemplo en cualquier ordenador, desde la raíz ejecuta
`uv run python examples/generate_notebook_demo.py`. Genera datos sintéticos con las familias
Day, Weekend, BOW, Week, BOM, Month, Quarter, Season y Year; no necesita inputs reales ni EEX.
El script es independiente del notebook y guarda su CSV en la ruta de prueba anterior.

Si hace falta, edita la primera celda de código:

```python
PROJECT_ROOT = None
CONFIG_PATH = None
OUTPUT_PATH = "output/filled_history.csv"
```

Se busca la raíz del repositorio desde el directorio actual y sus padres: funciona arrancando
desde la raíz o desde `notebooks/`. Si Jupyter arranca en otra carpeta, indica `PROJECT_ROOT`.
El notebook no contiene rutas particulares de una máquina.

- `OUTPUT_PATH` explícito tiene prioridad. Puede ser un CSV o una carpeta que contenga
  `filled_history.csv`. Las rutas relativas explícitas se resuelven desde la raíz del proyecto.
- Si no lo indicas, se usa `paths.output_dir` del TOML indicado en `CONFIG_PATH`, o del
  `config.toml` de la raíz. Las rutas internas del TOML se resuelven desde la carpeta del TOML.
- Si no existe el TOML predeterminado, se intenta `output/filled_history.csv`. Si indicas
  expresamente un TOML inexistente, se informa del error.
- Si el CSV falta o está vacío, se muestra ayuda y no se crean selectores. Esquemas o fechas
  inválidos, texto no numérico en precios y aliases contradictorios producen errores explícitos.

Tras cambiar una ruta, vuelve a ejecutar la celda de carga y las siguientes. La carga se hace
una vez; repite esas celdas para ver una salida más reciente. Usa una copia del CSV si otro
proceso lo está escribiendo durante la consulta.

## Tres vistas distintas

**Single date — una fecha.** Selecciona la identidad completa `(product, region, unit)` y la fecha de referencia.
La selección inicial es **Full curve (all contract types)**: muestra juntos todos los contratos
guardados para esa curva y fecha. El selector **View:** permite limitarse a Month, Quarter,
Year o cualquier otro tipo presente en el CSV, conservando todos sus vencimientos. No hay
un límite de dos trimestres ni de seis meses. Compara el `price` final con `eex_settle`.
Opcionalmente muestra marcadores de
`own_vwap` y la línea `price_before_shape`. La tabla incluye origen, flags, `eex_asof`,
antigüedad de EEX y estado de shape cuando estén disponibles.

El eje horizontal muestra contratos (`D+1`, `M+1`, `Q+1`, `Cal+1`, etc.), agrupados por tipo y
ordenados por entrega. Cada contrato tiene su propia posición, incluso si un mes, trimestre
y año empiezan el mismo día. La separación entre puntos es uniforme: **no representa duración
ni distancia temporal**. Las fechas exactas aparecen al pasar el cursor; el fin es exclusivo.
Las líneas conectan puntos dentro de cada tipo y se interrumpen entre tipos, señalados por
separadores verticales. La leyenda permite ocultar/mostrar cada serie para todos los tipos.

El texto sobre el gráfico cuenta los contratos de cada familia. Se muestran todas las filas
guardadas, incluidos huecos sin precio; no se inventan Q+3 o Cal+1 si no están en el CSV.
El ejemplo anterior tenía únicamente seis meses y dos trimestres; el ejemplo ampliado incluye
las nueve familias. Tras regenerar un CSV o actualizar el notebook, reinicia el kernel y ejecuta
todas las celdas para recargar los datos y las funciones.

**Range means — medias por rango.** Elige fechas inicial/final inclusivas, alineación y pulsa
**Compare range**. Por defecto se sigue la misma entrega absoluta aunque cambie su etiqueta. La
alineación por tenor relativo mezcla entregas intencionadamente: M+1 en septiembre y M+1 en
octubre son contratos distintos. Se muestra un aviso y el contador `n_delivery_periods`.
Esta pestaña también respeta **View:**: curva completa o todos los vencimientos de un tipo.
El eje separa cada contrato; en alineación absoluta usa etiquetas de entrega y en relativa,
etiquetas de tenor. Las líneas siguen separadas por tipo.

Para cada punto, ambas medias usan **solo fechas de referencia con precio final y EEX finitos**.
Cada fecha emparejada pesa lo mismo: no se pondera por volumen ni horas de entrega. Un precio
ausente no se convierte en cero; los ceros y negativos válidos sí cuentan. Distintos puntos
pueden tener fechas y tamaños de muestra diferentes: no se exige una intersección global de
fechas entre todos los contratos. Una cotización EEX antigua puede repetirse en varias fechas
de referencia, como corresponde al peso diario; consulta su fecha de publicación.

**Fixed delivery evolution — evolución de una entrega.** Elige un contrato absoluto y sigue su precio entre fechas,
aunque cambie de etiqueta relativa. Esta vista usa todo su historial guardado, independientemente
del rango de la pestaña de medias. Los cambios pueden proceder de nueva información, otra
ruta de cálculo o shape; el gráfico no diagnostica por sí solo la causa.

## Contadores e interpretación

El resumen cuenta observaciones únicas guardadas, precios finales/EEX disponibles, parejas,
precios finales ausentes y originales/estimados finales. Los originales ajustados (`own+shape`)
son un subgrupo de los estimados. Son categorías de la salida del motor, no números de filas
físicas del input ni operaciones. `own_vwap` puede agregar filas originales equivalentes.

La tabla del rango incluye `n_observations`, `n_price`, `n_eex`, `n_paired`, número de entregas,
medias emparejadas y diferencia media. Sin fechas emparejadas, ambas medias quedan vacías.
La cobertura mide la proporción de **filas guardadas** con precio final finito; no cuenta
contratos que falten completamente del CSV. No es una medida de precisión predictiva.

Cada fecha, identidad completa, tipo y entrega absoluta cuenta una vez. Las etiquetas
equivalentes aparecen en `tenor_aliases` y no duplican pesos ni contadores. Si los aliases
tienen valores contradictorios se rechazan; no se promedian para ocultar la diferencia.
Tipos distintos siguen siendo distintos.

Con shape en `audit`, `price` sigue siendo el precio aceptado sin aplicar la propuesta.
`price_before_shape` es el precio previo a esa etapa, no el precio hipotético propuesto.
El notebook no aplica shape ni inventa sus campos cuando falten. Admite outputs de shape
apagado sin columnas opcionales. Las definiciones están en [OUTPUT.es.md](OUTPUT.es.md).

## Esquema admitido

Se requieren `reference_date`, `product`, `region`, `unit`, `tenor`, `kind`, `delivery_start`,
`delivery_end`, `price`, `eex_settle` y `source`. Usa un CSV filled, no `enriched_history.csv`.
Región/unidad vacías son valores literales de identidad; si faltan esas columnas se rechaza
el archivo. Regenera outputs antiguos con el motor actual en vez de adivinar regiones o monedas.

Se aceptan fechas ISO y día/mes/año. Los campos shape, `own_vwap`, `eex_asof` y otros diagnósticos
son opcionales. Si falta la fecha EEX se muestra como desconocida: no se presume que sea reciente.
Todos los precios conservan la unidad de la curva seleccionada.

Si aparecen textos pero no selectores/gráficos, comprueba que instalaste el grupo notebook y
seleccionaste el kernel de ese entorno. Las pruebas de los helpers no requieren Jupyter.

Comprobación de ejecución: todas las celdas y callbacks interactivos funcionaron sin errores
con output sintético de siete fechas, tres identidades y las nueve familias admitidas, incluidas
la curva completa y las medias con alineación por periodo absoluto y por etiqueta relativa.
