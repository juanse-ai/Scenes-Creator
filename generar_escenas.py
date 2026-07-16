#!/usr/bin/env python3
"""Genera escenas de OBS para el Fail Fast Show a partir de un Scene Collection
plantilla y un JSON de temas.

Uso:
    python3 generar_escenas.py base-scenes.json scenes.json -o output.json
"""

import argparse
import copy
import datetime
import json
import os
import re
import sys
import uuid
from urllib.request import pathname2url

VARIANTES = ["DUO", "IMAGEN", "NATALIA", "JUANSE"]

# OBS no soporta rutas relativas: importa las rutas tal cual están escritas.
# Todas las rutas del show (la imagen de cada escena, etc.) se anclan a esta
# raíz, que apunta a la carpeta real del show en disco. Así la imagen de la
# escena N queda como <RAIZ_SHOW>/images/N.png. Cámbiala aquí (o con --root)
# si el show vive en otra ruta o en otro computador.
RAIZ_SHOW = "/Users/mache/Content/fail-fast-show"

# El HTML del overlay elige su layout por query string (?scene=...&who=...).
# El modo elegido con los botones via "Interact" es estado en memoria del
# navegador y NO se guarda en el Scene Collection, así que cada fuente de
# overlay debe cargar la URL con su parámetro fijo.
#
# Ruta absoluta del HTML del overlay en disco. Cámbiala en este único lugar
# para apuntar a otra ubicación. Las fuentes de overlay quedan como archivo
# local (is_local_file=True + local_file apuntando aquí). A diferencia del
# resto de rutas del show, esta NO se re-rootea a la raíz compartida: se
# escribe tal cual en el output.
OVERLAY_LOCAL_FILE = "/Users/mache/Content/fail-fast-show/overlay/failfast-overlay.html"
OVERLAY_QUERY = {
    "Overlay Duo": "scene=duo",
    "Overlay Imagen 1:1": "scene=image",
    "Overlay Solo Juanse": "scene=solo&who=js",
    "Overlay Solo Natalia": "scene=solo&who=cata",
}
TEMA_PLANTILLA = "1"
GRUPO_TITULOS = "Titulos"
FUENTE_TITLE = "Title"
FUENTE_SUBTITLE = "Subtitle"
FUENTE_FOTOS = "FOTOS"


def morir(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def advertir(msg):
    print(f"AVISO: {msg}", file=sys.stderr)


class Generador:
    def __init__(self, data):
        self.data = data
        self.uuids_usados = set()
        self.nombres_usados = set()
        for src in data.get("sources", []) + data.get("groups", []):
            if "uuid" in src:
                self.uuids_usados.add(src["uuid"])
            if "name" in src:
                self.nombres_usados.add(src["name"])

    def nuevo_uuid(self):
        while True:
            u = str(uuid.uuid4())
            if u not in self.uuids_usados:
                self.uuids_usados.add(u)
                return u

    def registrar_nombre(self, nombre):
        if nombre in self.nombres_usados:
            morir(f"el nombre '{nombre}' ya existe en el collection; no se puede crear el clon")
        self.nombres_usados.add(nombre)

    def buscar_source(self, nombre):
        for src in self.data["sources"]:
            if src.get("name") == nombre:
                return src
        return None

    def buscar_grupo(self, nombre):
        for g in self.data.get("groups", []):
            if g.get("name") == nombre:
                return g
        return None

    def buscar_escena(self, nombre):
        src = self.buscar_source(nombre)
        if src is not None and src.get("id") == "scene":
            return src
        return None


def remapear_items(items, remap):
    """Actualiza name + source_uuid de los items que referencian fuentes clonadas.

    remap: {uuid_original: (nombre_nuevo, uuid_nuevo)}
    """
    for item in items:
        ref = item.get("source_uuid")
        if ref in remap:
            nuevo_nombre, nuevo_uuid = remap[ref]
            item["name"] = nuevo_nombre
            item["source_uuid"] = nuevo_uuid


# Rutas del export que apuntan a la carpeta del show dentro del home de algún
# usuario (/Users/<usuario>/Content/fail-fast-show/...) se re-rootean a la raíz
# compartida, para que no quede ningún usuario embebido en el archivo.
RE_SHOW = re.compile(r"(?P<pre>file://)?/Users/[^/]+/Content/fail-fast-show")


def re_rootear(obj, raiz):
    if isinstance(obj, dict):
        return {k: re_rootear(v, raiz) for k, v in obj.items()}
    if isinstance(obj, list):
        return [re_rootear(v, raiz) for v in obj]
    if isinstance(obj, str):
        return RE_SHOW.sub(lambda m: (m.group("pre") or "") + raiz, obj)
    return obj


def limpiar_scripts(data):
    """Quita de modules.scripts-tool los scripts con rutas de usuario que no
    pueden re-rootearse a la raíz compartida (referencias viejas que romperían
    la portabilidad). Devuelve las rutas quitadas."""
    scripts = data.get("modules", {}).get("scripts-tool")
    if not isinstance(scripts, list):
        return []
    quitados = [s.get("path", "") for s in scripts
                if s.get("path", "").startswith("/Users/") and not RE_SHOW.match(s.get("path", ""))]
    if quitados:
        data["modules"]["scripts-tool"] = [s for s in scripts if s.get("path", "") not in quitados]
    return quitados


def corregir_overlays(gen):
    """Apunta cada fuente de overlay al archivo local del overlay
    (OVERLAY_LOCAL_FILE): is_local_file=True y local_file a esa ruta fija. Deja
    además en 'url' la URL file:// con su query string de layout.

    Devuelve la lista de overlays corregidos. Idempotente: si la fuente ya
    quedó como archivo local con la ruta y la URL correctas, no la toca.
    """
    if not os.path.exists(OVERLAY_LOCAL_FILE):
        advertir(f"el overlay no existe en disco: {OVERLAY_LOCAL_FILE}")
    base_url = "file://" + pathname2url(OVERLAY_LOCAL_FILE)
    corregidos = []
    for nombre, query in OVERLAY_QUERY.items():
        src = gen.buscar_source(nombre)
        if src is None:
            advertir(f"no existe la fuente '{nombre}'; no se corrige su layout")
            continue
        if src.get("id") != "browser_source":
            advertir(f"la fuente '{nombre}' no es un browser_source; no se toca")
            continue
        s = src["settings"]
        url = f"{base_url}?{query}"
        if (s.get("is_local_file") and s.get("local_file") == OVERLAY_LOCAL_FILE
                and s.get("url") == url):
            continue
        s["is_local_file"] = True
        s["local_file"] = OVERLAY_LOCAL_FILE
        s["url"] = url
        corregidos.append(f"{nombre} -> ?{query}")
    return corregidos


def resolver_imagen(tema, raiz, verificar_disco):
    """Resuelve la ruta de la imagen del tema (si es relativa, la ancla a la
    raíz del show) y avisa si no existe en disco."""
    imagen = tema["image"]
    if not imagen.startswith("/"):
        imagen = os.path.join(raiz, imagen)
    if verificar_disco and not os.path.exists(imagen):
        advertir(f"la imagen del tema '{tema['scene']}' no existe en disco: {imagen}")
    return imagen


def poner_imagen_slideshow(fotos, imagen):
    """Deja el slideshow 'fotos' con una única imagen (la del tema)."""
    archivos = fotos["settings"].get("files", [])
    entrada = copy.deepcopy(archivos[0]) if archivos else {"selected": False, "hidden": False}
    entrada["value"] = imagen
    entrada["uuid"] = str(uuid.uuid4())
    fotos["settings"]["files"] = [entrada]


def main():
    parser = argparse.ArgumentParser(description="Genera escenas del Fail Fast Show")
    parser.add_argument("base", help="Scene Collection exportado de OBS (JSON)")
    parser.add_argument("temas", help="JSON con la lista de temas")
    parser.add_argument("-o", "--output", required=True, help="Archivo de salida")
    parser.add_argument(
        "--root",
        default=RAIZ_SHOW,
        help=f"Raíz del show en disco (default: {RAIZ_SHOW}). Toda ruta del "
        "collection y las rutas relativas del JSON de temas se anclan a esta "
        "raíz; p.ej. la imagen de una escena queda como <root>/images/<n>.png.",
    )
    args = parser.parse_args()

    raiz = args.root.rstrip("/")
    if not raiz.startswith("/"):
        morir(f"--root debe ser una ruta absoluta: {args.root}")
    verificar_disco = os.path.isdir(raiz)
    if not verificar_disco:
        advertir(
            f"la raíz '{raiz}' no existe en esta máquina; no se verificará que "
            f"las imágenes existan. Usa --root para apuntar a la carpeta del show."
        )

    if os.path.abspath(args.output) in (os.path.abspath(args.base), os.path.abspath(args.temas)):
        morir("el archivo de salida no puede ser uno de los archivos de entrada")

    try:
        with open(args.base, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        morir(f"no se pudo leer '{args.base}': {e}")
    try:
        with open(args.temas, encoding="utf-8") as f:
            temas = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        morir(f"no se pudo leer '{args.temas}': {e}")

    if not isinstance(temas, list):
        morir("el JSON de temas debe ser una lista")
    for i, t in enumerate(temas):
        for clave in ("scene", "title", "subtitle", "image"):
            if clave not in t:
                morir(f"el tema #{i + 1} no tiene la clave '{clave}'")

    gen = Generador(data)

    # --- Validación de plantillas ---
    plantillas = {}
    for v in VARIANTES:
        nombre = f"{TEMA_PLANTILLA}---{v}"
        escena = gen.buscar_escena(nombre)
        if escena is None:
            morir(f"no existe la escena plantilla '{nombre}' en el collection base")
        plantillas[v] = escena

    grupo_base = gen.buscar_grupo(GRUPO_TITULOS)
    if grupo_base is None:
        morir(f"no existe el grupo '{GRUPO_TITULOS}' en el collection base")
    title_base = gen.buscar_source(FUENTE_TITLE)
    subtitle_base = gen.buscar_source(FUENTE_SUBTITLE)
    fotos_base = gen.buscar_source(FUENTE_FOTOS)
    if title_base is None:
        morir(f"no existe la fuente '{FUENTE_TITLE}' en el collection base")
    if subtitle_base is None:
        morir(f"no existe la fuente '{FUENTE_SUBTITLE}' en el collection base")
    if fotos_base is None:
        morir(f"no existe la fuente '{FUENTE_FOTOS}' en el collection base")
    if fotos_base.get("id") != "slideshow":
        morir(f"la fuente '{FUENTE_FOTOS}' no es un slideshow")

    scene_order = data.setdefault("scene_order", [])

    scripts_quitados = limpiar_scripts(data)

    # --- Procesar temas ---
    procesados, saltados, escenas_creadas = [], [], []
    plantilla_rellenada = None

    for tema in temas:
        suf = str(tema["scene"])
        nombres_escenas = [f"{suf}---{v}" for v in VARIANTES]
        existentes = [n for n in nombres_escenas if gen.buscar_escena(n) is not None]

        # El tema plantilla (TEMA_PLANTILLA) no se clona: sus 4 escenas ya
        # existen en el collection base y son las que se clonan para los demás
        # temas. Pero SÍ se rellenan sus fuentes base (Title/Subtitle/FOTOS) con
        # el contenido de este tema, para que la escena plantilla muestre lo que
        # dice el JSON de temas y no el texto que venía en el collection export.
        if suf == TEMA_PLANTILLA:
            if len(existentes) != len(nombres_escenas):
                morir(
                    f"el tema plantilla '{suf}' no tiene sus 4 escenas base en el "
                    f"collection ({', '.join(nombres_escenas)}); base incompleto"
                )
            title_base["settings"]["text"] = tema["title"]
            subtitle_base["settings"]["text"] = tema["subtitle"]
            poner_imagen_slideshow(fotos_base, resolver_imagen(tema, raiz, verificar_disco))
            plantilla_rellenada = suf
            continue

        if len(existentes) == len(nombres_escenas):
            saltados.append(suf)
            continue
        if existentes:
            morir(
                f"el tema '{suf}' tiene escenas parciales en el collection "
                f"({', '.join(existentes)}); no se puede continuar sin dejarlo inconsistente"
            )

        imagen = resolver_imagen(tema, raiz, verificar_disco)

        # Clonar Title y Subtitle
        title_clon = copy.deepcopy(title_base)
        title_clon["name"] = f"{FUENTE_TITLE} {suf}"
        gen.registrar_nombre(title_clon["name"])
        title_clon["uuid"] = gen.nuevo_uuid()
        title_clon["settings"]["text"] = tema["title"]

        subtitle_clon = copy.deepcopy(subtitle_base)
        subtitle_clon["name"] = f"{FUENTE_SUBTITLE} {suf}"
        gen.registrar_nombre(subtitle_clon["name"])
        subtitle_clon["uuid"] = gen.nuevo_uuid()
        subtitle_clon["settings"]["text"] = tema["subtitle"]

        # Clonar el grupo Titulos y apuntar sus hijos a los clones
        grupo_clon = copy.deepcopy(grupo_base)
        grupo_clon["name"] = f"{GRUPO_TITULOS} {suf}"
        gen.registrar_nombre(grupo_clon["name"])
        grupo_clon["uuid"] = gen.nuevo_uuid()

        remap = {
            title_base["uuid"]: (title_clon["name"], title_clon["uuid"]),
            subtitle_base["uuid"]: (subtitle_clon["name"], subtitle_clon["uuid"]),
            grupo_base["uuid"]: (grupo_clon["name"], grupo_clon["uuid"]),
        }
        remapear_items(grupo_clon["settings"].get("items", []), remap)

        # Clonar FOTOS con solo la imagen del tema
        fotos_clon = copy.deepcopy(fotos_base)
        fotos_clon["name"] = f"{FUENTE_FOTOS} {suf}"
        gen.registrar_nombre(fotos_clon["name"])
        fotos_clon["uuid"] = gen.nuevo_uuid()
        poner_imagen_slideshow(fotos_clon, imagen)
        remap[fotos_base["uuid"]] = (fotos_clon["name"], fotos_clon["uuid"])

        data["sources"].extend([title_clon, subtitle_clon, fotos_clon])
        data["groups"].append(grupo_clon)

        # Clonar las 4 escenas
        for v, nombre_escena in zip(VARIANTES, nombres_escenas):
            escena = copy.deepcopy(plantillas[v])
            escena["name"] = nombre_escena
            gen.registrar_nombre(nombre_escena)
            escena["uuid"] = gen.nuevo_uuid()
            remapear_items(escena["settings"].get("items", []), remap)
            data["sources"].append(escena)
            scene_order.append({"name": nombre_escena})
            escenas_creadas.append(nombre_escena)

        procesados.append(suf)

    # Renombrar el collection para que no choque al importar
    fecha = datetime.date.today().isoformat()
    data["name"] = f"{data.get('name', 'scenes')}-{fecha}"

    # Re-rootear a la raíz compartida toda ruta del show que traiga el export
    data = re_rootear(data, raiz)

    # Corregir los overlays DESPUÉS de re-rootear: OVERLAY_LOCAL_FILE es una
    # ruta absoluta fija que NO debe re-escribirse a la raíz compartida. Como
    # re_rootear reconstruye 'data', recreamos el generador sobre el nuevo árbol.
    gen = Generador(data)
    overlays_corregidos = corregir_overlays(gen)

    try:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.write("\n")
    except OSError as e:
        morir(f"no se pudo escribir '{args.output}': {e}")

    print("Resumen:")
    print(f"  Collection de salida:  {data['name']}")
    print(f"  Temas procesados ({len(procesados)}): {', '.join(procesados) or '-'}")
    print(f"  Tema plantilla rellenado: {plantilla_rellenada or '-'} (Title/Subtitle/FOTOS base)")
    print(f"  Temas saltados   ({len(saltados)}): {', '.join(saltados) or '-'}")
    print(f"  Escenas creadas  ({len(escenas_creadas)}): {', '.join(escenas_creadas) or '-'}")
    print(f"  Overlays corregidos ({len(overlays_corregidos)}):")
    for o in overlays_corregidos or ["    (ninguno, ya estaban corregidos)"]:
        print(f"    {o}")
    print(f"  Raíz del show: {raiz}" + ("" if verificar_disco else " (no existe en esta máquina)"))
    if scripts_quitados:
        print(f"  Scripts con ruta de usuario quitados de modules: {', '.join(scripts_quitados)}")
    print(f"  Archivo de salida: {args.output}")


if __name__ == "__main__":
    main()
