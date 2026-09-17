
# -*- coding: utf-8 -*-
r"""
1_processar_dados_VS_qualificada.py

Adaptado de 1_processar_dados_VS.py para usar a nova versao QUALIFICADA
(pre-filtrada por area >= 2ha) da vegetacao secundaria 2022:
  GEOPACKAGE\INPE_Vegetacao_Secundaria\VS_2022_TerraBrasilis_Vegetacao_Secundaria_Qualificada_Brasil.gpkg
  (6 camadas por bioma, sufixo "_qualificada"; campos: id, ano,
  idade_ponderada, area_ha, ... SEM campo 'classe' -- ja e so vegetacao
  secundaria por definicao da camada).

Recorta por bbox (uniao dos imoveis Habilitados/Analisados/Nao_Analisados
da UF, agora nos arquivos Maio2026), gera 1 arquivo por UF:
  GEOPACKAGE\Cadastro Ambiental Rural_Maio2026\_veg_qualificada_uf\<UF>_Vegetacao_Secundaria_2022_Qualificada.gpkg
  layer VS_<UF>_qualificada; campos bioma, ano, area_ha_vs, geometry
"""
import os
import json

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection

VEG_GPKG = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\INPE_Vegetacao_Secundaria\VS_2022_TerraBrasilis_Vegetacao_Secundaria_Qualificada_Brasil.gpkg"
CAR_DIR = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\Cadastro Ambiental Rural_Maio2026"
OUT_DIR = os.path.join(CAR_DIR, "_veg_qualificada_uf")
os.makedirs(OUT_DIR, exist_ok=True)

SOMENTE_ESTES = []
REFAZER = []

UFS = ["AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT","PA","PB","PR","PE","PI",
       "RJ","RN","RS","RO","RR","SC","SP","SE","TO"]
CRS_TRAB = "EPSG:4674"
PROGRESSO = os.path.join(OUT_DIR, "_progresso_processar_VS_qualificada.json")


def biomas_disponiveis():
    out = {}
    for cam in pyogrio.list_layers(VEG_GPKG)[:, 0]:
        nome = cam.replace("Vegetacao_Secundaria_", "").replace("_2022_qualificada", "")
        out[nome] = cam
    return out


def _so_poligonos(geom):
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    if isinstance(geom, GeometryCollection):
        polis = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
        if not polis:
            return None
        if len(polis) == 1:
            return polis[0]
        partes = []
        for p in polis:
            if isinstance(p, MultiPolygon):
                partes.extend(p.geoms)
            else:
                partes.append(p)
        return MultiPolygon(partes)
    return None


def _reparar(geom):
    if geom is None or geom.is_empty:
        return None
    if geom.is_valid:
        return geom
    try:
        return shapely.make_valid(geom)
    except Exception:
        pass
    try:
        g = geom.buffer(0)
        if g is not None and not g.is_empty:
            return g
    except Exception:
        pass
    return None


def limpar(gdf):
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_TRAB)
    elif str(gdf.crs).upper() not in ("EPSG:4674",):
        gdf = gdf.to_crs(CRS_TRAB)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
    gdf["geometry"] = gdf.geometry.apply(_reparar)
    gdf = gdf[gdf.geometry.notna()].copy()
    gdf["geometry"] = gdf.geometry.apply(_so_poligonos)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    return gdf


def bbox_uf(uf):
    xmins, ymins, xmaxs, ymaxs = [], [], [], []
    fontes = [
        (f"{uf}_CAR_Imoveis_Selecionados_Habilitados.gpkg", f"CAR_{uf}_Imoveis_Habilitados"),
        (f"{uf}_CAR_Imoveis_Selecionados_Analisados.gpkg", f"CAR_{uf}_Imoveis_Selecionados_Analisados"),
        (f"{uf}_CAR_Imoveis_Selecionados_Nao_Analisados.gpkg", f"CAR_{uf}_Imoveis_Selecionados_Nao_Analisados"),
    ]
    for nome_arquivo, layer in fontes:
        caminho = os.path.join(CAR_DIR, nome_arquivo)
        if not os.path.exists(caminho):
            continue
        try:
            info = pyogrio.read_info(caminho, layer=layer)
        except Exception:
            continue
        if info.get("features", 0) == 0 or info.get("total_bounds") is None:
            continue
        minx, miny, maxx, maxy = info["total_bounds"]
        xmins.append(minx); ymins.append(miny); xmaxs.append(maxx); ymaxs.append(maxy)
    if not xmins:
        return None
    return (min(xmins), min(ymins), max(xmaxs), max(ymaxs))


def processar_uf(uf):
    saida = os.path.join(OUT_DIR, f"{uf}_Vegetacao_Secundaria_2022_Qualificada.gpkg")

    bbox = bbox_uf(uf)
    if bbox is None:
        return {"uf": uf, "aviso": "sem imoveis processados (bbox indisponivel)"}

    veg_layers = biomas_disponiveis()
    partes = []
    for bioma, cam_veg in veg_layers.items():
        try:
            veg = gpd.read_file(VEG_GPKG, layer=cam_veg, bbox=bbox)
        except Exception:
            continue
        if len(veg) == 0:
            continue
        veg = limpar(veg)
        if len(veg) == 0:
            continue
        cols = [c for c in ["ano", "area_ha"] if c in veg.columns]
        veg = veg[cols + ["geometry"]].copy()
        if "area_ha" in veg.columns:
            veg = veg.rename(columns={"area_ha": "area_ha_vs"})
        veg["bioma"] = bioma
        partes.append(veg)

    if not partes:
        return {"uf": uf, "aviso": "nenhum poligono de VS qualificada no bbox da UF"}

    todos = gpd.GeoDataFrame(pd.concat(partes, ignore_index=True), crs=CRS_TRAB)
    cols_finais = [c for c in ["bioma", "ano", "area_ha_vs"] if c in todos.columns] + ["geometry"]
    todos = todos[cols_finais]
    if "ano" in todos.columns:
        todos["ano"] = pd.to_numeric(todos["ano"], errors="coerce").astype("Int64")
    if "bioma" in todos.columns:
        todos["bioma"] = todos["bioma"].astype("string")

    if os.path.exists(saida):
        os.remove(saida)
    todos.to_file(saida, layer=f"VS_{uf}_qualificada", driver="GPKG")

    return {"uf": uf, "poligonos": int(len(todos)), "saida": saida}


def carregar_progresso():
    if os.path.isfile(PROGRESSO):
        with open(PROGRESSO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_progresso(p):
    with open(PROGRESSO, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


def main():
    alvos = SOMENTE_ESTES if SOMENTE_ESTES else UFS
    prog = carregar_progresso()
    print(f"Estados a processar ({len(alvos)}): {', '.join(alvos)}")
    sucesso, falhas = [], []
    for uf in alvos:
        if prog.get(uf, {}).get("concluido") and uf not in REFAZER:
            print(f"[{uf}] ja processado, pulando")
            continue
        print(f"[{uf}] recortando VS qualificada...", flush=True)
        try:
            r = processar_uf(uf)
            if r.get("aviso"):
                print(f"       (aviso: {r['aviso']})", flush=True)
            else:
                print(f"       poligonos={r['poligonos']}", flush=True)
            prog[uf] = {"concluido": True, **r}
            salvar_progresso(prog)
            sucesso.append(uf)
        except Exception as e:
            print(f"       !! ERRO: {e}", flush=True)
            falhas.append((uf, str(e)))
    print(f"\nSucesso ({len(sucesso)}): {', '.join(sucesso) or '(nenhum)'}")
    if falhas:
        print(f"Falhas ({len(falhas)}):")
        for uf, msg in falhas:
            print(f"  {uf}: {msg}")


if __name__ == "__main__":
    main()
