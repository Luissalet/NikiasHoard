# Nikias's Hoard

Estudio de coloreado para impresión 3D multicolor: reparte unos pocos colores por una figura ya terminada y escribe los archivos que el laminador abre ya pintados. [English](README.md)

![Nikias's Hoard](app-icon.png)

## Qué es

El estudio de coloreado de la familia Hoard. Abre una figura de impresión terminada y de un solo color (un STL) y
decide en cuál de unos pocos colores (cuatro por defecto) va cada triángulo, a partir de un modelo con texturas de la
misma figura o de imágenes del personaje, que busca él mismo si la figura no trae ninguna. El resultado es el archivo
digital: un 3MF pintado que el laminador abre con sus colores; con qué material se imprime cada uno lo decide quien lo
imprime. La figura original se conserva. Los umbrales de regiones y manchas y el tamaño del pincel del navegador
se miden en proporción a la figura.

## Qué hace

- **Biblioteca**: se le indican las carpetas con las figuras. Lista cada STL agrupado por carpeta, con su foto si
  una carpeta de fotos tiene una con el mismo nombre. Los archivos `<figura> flat.stl` y `<figura> pivot.stl` salen
  dentro de su figura como variantes, y `<figura> base.stl` o `<figura> <algo>.stl` como sus piezas sueltas.
- **Proyecto**: al abrir una figura suelda sus triángulos, la mide y la corta en regiones. No copia ni cambia nada en
  la carpeta de la figura.
- **Regiones**: una figura fundida no tiene materiales, pero donde se juntan dos partes (una manga con un brazo, un
  guante con su puño, una línea grabada) la superficie se pliega hacia dentro. Esos pliegues separan las regiones.
  Un contorno grabado casi nunca cierra del todo (se desvanece al tocar otro), así que los pliegues se ensanchan hasta
  cerrar los huecos estrechos antes de separar: la cara, cada ojo, el interior de una oreja o una cinta salen como
  regiones propias aunque su línea tenga cortes. Dos trozos pequeños que ese cierre separa sin que haya nada grabado
  entre ellos (el blanco de un ojo pellizcado entre el párpado y el iris) vuelven a ser una pieza. El detalle 1-5 va de partes grandes a cada línea grabada (4 por
  defecto). Las regiones son lo que recibe color, del modelo o de un clic.
- **Color desde un modelo con texturas.** Añade un OBJ, GLB, glTF o DAE. Las referencias FBX y `.blend` usan un conversor local que conserva la pose guardada. Nikias alinea el modelo con la figura y mide el encaje. Toma muestras de color
  de la textura cerca de cada triángulo, las reduce a la paleta de impresión e indica si la paleta cubre los
  colores dominantes de la referencia. El modo automático usa la transferencia directa cuando el modelo encaja
  y recurre al modelo de visión en los demás casos.
- **Referencias del personaje.** Suelta, pega o selecciona imágenes de tus carpetas. Usa **Buscar imagen del personaje**
  para buscar por nombre y colección. Según la colección, la búsqueda usa ilustraciones oficiales, la imagen
  principal de la wiki de la colección o una enciclopedia. El resultado conserva el enlace a su página de origen
  y se puede quitar. El coloreado automático busca una imagen si faltan referencias y la opción está activada en Ajustes.
- **Color dirigido por el modelo de visión**: el modelo mira las referencias, elige la paleta, escribe qué color lleva
  cada parte del personaje (pelo, cada prenda, piel, ojos, suelas, lo que sostiene) y después ve la figura
  región a región (la figura en arcilla con una sola región marcada, desde el lado donde más se ve, con un primer
  plano para las pequeñas) y dice qué parte es y, por esa tabla, qué color lleva. Las regiones que encuentra mezcladas, o que
  esconden un color que nadie recibió (los ojos dentro de una cabeza), se cortan más fino y se vuelven a preguntar.
  Las partes pequeñas que desentonan con todo lo que las rodea (un dedo rosa en un guante blanco) se le vuelven a
  enseñar en primer plano para que diga si son un rasgo propio o más de lo que las rodea. Las referencias le llegan
  una a una y en grande, las tuyas antes que la buscada en internet.
  Luego mira su propio coloreado junto a las referencias, con el número de cada región escrito encima, y corrige las
  partes que ve mal. Lo que decidió y por qué queda en el proyecto. Una respuesta ilegible del modelo se pide otra
  vez; si la revisión falla, se pierde la revisión y no el coloreado.
- **Paleta**: los colores se comparan en CIELAB y se reducen con un agrupamiento ponderado que deja a un color
  pequeño pero distinto conservar el suyo. Se puede cambiar un color o su nombre, añadir o fundir colores y elegir
  cuál es el de la pieza (el color 1, el que toman los triángulos sin pintar). Un color que no acaba en ninguna cara
  se quita.
- **Pintar a mano**: con «Pieza» (la herramienta con la que se abre una figura ya coloreada) la pieza bajo el cursor
  se ilumina y un clic la pinta entera con el color activo; arrastrar sigue girando la figura, las teclas 1-9 eligen
  color y Mayús + clic pinta solo la mancha. «Mancha» rellena la mancha de un solo color bajo el cursor, o se arrastra un
  pincel cuyo tamaño es un porcentaje del de la figura; Alt + clic coge un color; se deshacen los últimos 20 cambios.
- **Limpieza**: los bordes entre colores se enderezan y las manchas más estrechas que un porcentaje del tamaño de la
  figura (1,2 % por defecto) se funden con los colores que las rodean. Cada color muestra su parte de la superficie
  y sus manchas.
- **Vistas**: color, regiones y arcilla, en la vista 3D y como imágenes desde cualquier lado o
  ampliadas sobre un punto (sombreadas con la geometría, así que las líneas grabadas se ven como se imprimirán).
- **Exportación.** Genera un 3MF pintado (`bambu` o `prusa`), un 3MF pintado para laminar con perfiles externos
  (`orca`), un 3MF de color estándar (`color`), un OBJ con un material por color o una superficie STL por color.
  La exportación incluye la paleta en texto. Las variantes flat y pivot que encajan con la figura pueden heredar
  sus colores. Los archivos se guardan en `Multicolor` junto a la figura o en la carpeta configurada.
- **Mallas separadas por color.** La exportación 3MF puede guardar una malla por color como objetos (`parts`),
  piezas de un objeto (`parts-bambu`) o volúmenes de un objeto (`parts-prusa`). El color con más superficie forma
  el núcleo. Los demás colores forman capas exteriores de grosor configurable, que se reducen en zonas finas.
  La exportación comprueba e indica si cada malla está cerrada. El cierre depende de la geometría de origen
  y de la opción `repair`.
- **Imprimibilidad**: se comprueba si la figura tiene aristas abiertas, aristas compartidas por más de dos caras,
  caras con la normal al revés, piezas con las normales hacia dentro, caras repetidas o sin área y motas, y se
  listan sus piezas sueltas con las que quedarían en el aire. Por defecto, al exportar los sólidos se repara la superficie
  (se quitan caras repetidas, sin área y motas, se orienta cada pieza hacia fuera y se cierran los bordes abiertos).
  Cada pieza suelta (una base, unas gafas) se puede escribir también como un STL propio apoyado en la plataforma.

## Conexión con Faustus y Hoard Hub

El relevo de producción parte de una malla cerrada y genera un 3MF coloreado. Para laminar a través del Hub,
usa `result_export` con `formats=["orca"]` e indica al laminador los perfiles de filamento en el orden de la paleta.

`faustus-plugin.json` (id `nikias`) registra la app como plugin de Faustus: Hoard Hub la arranca cuando hace falta en
el puerto 5252 y comprueba `/api/health`. Lo mismo que hacen los botones está disponible para los asistentes por MCP
(`mcp_server.py`, stdio); `figure_look` y `source_look` devuelven imágenes.

| Grupo | Herramientas |
| --- | --- |
| Biblioteca y proyectos | `status`, `library_scan`, `project_create`, `project_list`, `project_get`, `project_rename`, `project_delete` |
| Referencias | `source_find`, `source_fetch`, `source_add`, `source_remove`, `source_look` |
| Coloreado | `regions_set`, `color_start`, `job_status`, `job_cancel` |
| Paleta y pintura | `palette_set`, `paint`, `paint_undo`, `clean` |
| Vistas y archivos | `figure_look`, `result_export`, `print_check` |
| Ajustes | `settings_get`, `settings_set` |

Nikias toma además de la biblioteca de skills de la familia los procedimientos que su trabajo necesita y se los da a
los agentes que se conectan (botón «Skills»).

## Arrancar

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m nikias_hoard
```

Escucha en http://127.0.0.1:5252. Los datos viven en `data/` (`NIKIAS_DATA_DIR` para moverlos). Las carpetas de
figuras, referencias y fotos se indican en Ajustes. Para referencias FBX y `.blend`, Nikias busca un conversor local.
Indica la ruta de su ejecutable en Ajustes si hace falta. Hoard Link proporciona el modelo de visión de la familia.
En Ajustes también puedes indicar la dirección de otra API de chat compatible. Consulta [FAUSTUS.md](FAUSTUS.md)
para ver los detalles de configuración.
Ejecuta las pruebas con `python -m pytest`.

## Límites

- El color va por triángulo: el borde entre dos colores sigue los triángulos de la figura, así que es tan fino como
  la malla. En esculpidos densos es más fino que una boquilla.
- La transferencia directa está pensada para un modelo con texturas que encaje con la figura en la misma pose.
  Un modelo en otra pose puede servir de referencia visual.
- El modelo de visión decide por regiones. Un cambio de color que ningún pliegue ni línea grabada marca (un dibujo
  sobre una superficie lisa) no es una región: se pinta con el pincel o sale de un modelo que encaje.
- El 3MF pintado asigna un número de color a cada triángulo. Elige los perfiles de filamento correspondientes en
  el laminador con la paleta exportada. Los formatos de sólidos separados usan un núcleo de un solo color y capas
  exteriores coloreadas.
- La imagen que se busca en internet es la principal de la página con el nombre de la figura: si el nombre del archivo
  no es el del personaje, puede traer otra cosa; se ve en Referencias y se quita.
- El modelo de visión tarda varios minutos por figura (una pregunta por cada docena de regiones más las revisiones).

## Dónde está cada cosa

- `nikias_hoard/engine/`: `mesh.py` (STL, soldado, vecinos), `regions.py` (pliegues y regiones), `source.py`
  (muestreo de un modelo con texturas, colocación sobre la figura, paso de color), `palette.py` (CIELAB, reducción), `labels.py` (suavizado, islas, pincel), `render.py` (imágenes), `export.py` (3MF, OBJ, STL), `printable.py` (comprobar y reparar), `solids.py` (separación por color), `export_parts.py` (las mallas separadas como 3MF),
  `modelio.py` (lectores de modelos con texturas), `pose.py` (paso de color parte a parte para un modelo en otra pose;
  todavía sin conectar), `blender.py` + `blender_export.py` (FBX y `.blend`).
- `nikias_hoard/director.py` (la dirección del modelo de visión), `llm.py` (su cliente), `references.py` (la imagen
  del personaje buscada por nombre).
- `nikias_hoard/services.py` (biblioteca, proyectos, referencias, trabajos, paleta, pintura, exportación),
  `agent_tools.py` (el catálogo), `api/` (rutas), `static/` (el estudio), `store.py` (proyectos en disco).
- Datos: `data/settings.json`, `data/projects/<id>/` (`project.json`, malla soldada, regiones, colores, imágenes de
  referencia), `data/mcp-token`.
- Tests: `tests/test_engine.py`, `tests/test_app.py`.

## Licencia

[MIT](LICENSE). Consulta los [avisos de terceros](THIRD_PARTY_NOTICES.md).
