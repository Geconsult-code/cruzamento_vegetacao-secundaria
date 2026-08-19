r"""
diag_am.py — encontra as geometrias que causam o erro 'mixed-dimension' no AM.

Abre as camadas temáticas do AM e a vegetação (bbox do AM), e reporta, para
cada camada, quantas geometrias não são poligonais puras em cada etapa
(bruta / após make_valid / após extrair polígonos), mostrando os tipos.

Rode (ambiente 'geo' ativo):
    python diag_am.py
"""
import os
from collections import Counter

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection

BASE = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR"
GPKG_AM = os.path.join(BASE, "_saida_AM", "conformidade_AM_Privado.gpkg")
GPKG_VEG = os.path.join(BASE, "Vegetacao_Secundaria", "Vegetacao_Secundaria.gpkg")
CAM_VEG_AM = "Vegetacao_Secundaria_Amazonia_2022"


def _so_poligonos(geom):
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    if isinstance(geom, GeometryCollection):
        polis = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
        if not polis:
            return None
        partes = []
        for p in polis:
            partes.extend(p.geoms if isinstance(p, MultiPolygon) else [p])
        return MultiPolygon(partes) if len(partes) > 1 else partes[0]
    return None


def analisar(gdf, rotulo):
    print(f"\n=== {rotulo} ({len(gdf)} feições) ===")
    tipos_brutos = Counter(gdf.geometry.geom_type)
    print(f"  tipos brutos: {dict(tipos_brutos)}")
    n_invalidas = int((~gdf.geometry.is_valid).sum())
    print(f"  inválidas (bruto): {n_invalidas}")

    # após make_valid
    mv = gdf.geometry.apply(lambda g: make_valid(g) if g is not None else None)
    tipos_mv = Counter(g.geom_type for g in mv if g is not None)
    print(f"  tipos após make_valid: {dict(tipos_mv)}")

    # quantas viram coleção ou tipo não-poligonal após make_valid
    nao_poli_mv = [g.geom_type for g in mv
                   if g is not None and not isinstance(g, (Polygon, MultiPolygon))]
    print(f"  não-poligonais após make_valid: {len(nao_poli_mv)} {dict(Counter(nao_poli_mv))}")

    # após extrair polígonos
    sp = [_so_poligonos(g) for g in mv]
    restantes = [g.geom_type for g in sp
                 if g is not None and not isinstance(g, (Polygon, MultiPolygon))]
    n_none = sum(1 for g in sp if g is None)
    print(f"  após _so_poligonos: não-poligonais restantes = {len(restantes)}"
          f" {dict(Counter(restantes))}; viraram None = {n_none}")
    # verifica geometrias aninhadas (GeometryCollection dentro de coleção)
    aninhadas = 0
    for g in mv:
        if isinstance(g, GeometryCollection):
            for sub in g.geoms:
                if isinstance(sub, GeometryCollection):
                    aninhadas += 1
    if aninhadas:
        print(f"  !! coleções ANINHADAS: {aninhadas}")


print(f"AM temático: {GPKG_AM}")
import fiona
for cam in fiona.listlayers(GPKG_AM):
    if cam in ("APPS", "RESERVA_LEGAL", "USO_RESTRITO"):
        analisar(gpd.read_file(GPKG_AM, layer=cam), f"AM/{cam}")

# vegetação no bbox do AM (usa a extensão das APPS)
apps = gpd.read_file(GPKG_AM, layer="APPS")
bbox = tuple(apps.total_bounds)
print(f"\nlendo vegetação Amazônia no bbox do AM...")
veg = gpd.read_file(GPKG_VEG, layer=CAM_VEG_AM, bbox=bbox)
analisar(veg, f"VEG/Amazonia (bbox AM, {len(veg)} feições)")
