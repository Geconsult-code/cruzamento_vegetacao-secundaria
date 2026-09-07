# -*- coding: utf-8 -*-
r"""
cruzar_vegsec_selecionados.py

Cruza espacialmente a vegetacao secundaria (INPE, Vegetacao_Secundaria_2022.gpkg)
com as camadas APP/RL/AUR "Selecionados" ja recortadas para os imoveis
selecionados da analise de conformidade SICAR x INCRA (saida da Analise_Conformidade,
arquivos <UF>_Conformidade_Imoveis_Nao_Analisados.gpkg e
<UF>_Conformidade_Imoveis_Analisados.gpkg, layers CAR_<UF>_{APP,RL,AUR}_Selecionados_<categoria>).

Para cada UF x categoria (Nao_Analisados / Analisados) x tipo (APP/RL/AUR):
  1. le a camada tematica em blocos de FID (CHUNK feicoes por vez);
  2. para cada bloco, calcula o bbox e le SO a vegetacao secundaria dentro
     desse bbox (pyogrio bbox), para cada bioma;
  3. interseciona tema x vegetacao par-a-par (sjoin 'intersects' + shapely
     intersection), reduzindo cada resultado a sua parte poligonal
     (imune a 'mixed-dimension', mesmo padrao ja validado no projeto pro AM);
  4. calcula area geodesica (ha, GRS80) de cada pedaco;
  5. grava (append, via OGR bruto) direto na camada de saida VS_<TIPO>_<categoria>
     dentro do gpkg final da categoria.

Saida (2 arquivos, nomes finais conforme docx de renomeacao):
  Vegetacao_Secundaria\VS_Imoveis_Selecionados_Nao_Analisados.gpkg
     layers: VS_APP_Nao_Analisados, VS_RL_Nao_Analisados, VS_AUR_Nao_Analisados
  Vegetacao_Secundaria\VS_Imoveis_Selecionados_Analisados.gpkg
     layers: VS_APP_Analisados, VS_RL_Analisados, VS_AUR_Analisados

Campos gravados por poligono: uf, cod_imovel, tipo, bioma, classe, ano,
des_condic, selecao_final, area_ha.

Resiliente e retomavel via _progresso_vegsec_selecionados.json (guarda o
ultimo FID processado por item uf::categoria::tipo). Cada chamada de
rodada() processa ate um orcamento de tempo (BUDGET_S) e para, para caber
no limite de tempo da ponte QGIS - rodar de novo continua de onde parou.
"""

import os
import json
import time
import unicodedata

from osgeo import ogr, gdal
import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import Geod
import shapely
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection

gdal.UseExceptions()

# =============================== CONFIG ===============================
BASE_ROOT = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR"
BASE_CONFORMIDADE = os.path.join(BASE_ROOT, "Analise_Conformidade")
VEG_DIR = os.path.join(BASE_ROOT, "Vegetacao_Secundaria")
VEG_GPKG = os.path.join(VEG_DIR, "Vegetacao_Secundaria_2022.gpkg")

PROGRESSO = os.path.join(VEG_DIR, "_progresso_vegsec_selecionados.json")

CATEGORIAS = ["Nao_Analisados", "Analisados"]
TIPOS = ["APP", "RL", "AUR"]

OUT_ARQUIVO = {
    "Nao_Analisados": os.path.join(VEG_DIR, "VS_Imoveis_Selecionados_Nao_Analisados.gpkg"),
    "Analisados": os.path.join(VEG_DIR, "VS_Imoveis_Selecionados_Analisados.gpkg"),
}


def OUT_LAYER(tipo, categoria):
    return f"VS_{tipo}_{categoria}"


UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
]

CRS_TRAB = "EPSG:4674"
GEOD = Geod(ellps="GRS80")

CHUNK = 5000      # feicoes da tematica por bloco
BUDGET_S = 600     # orcamento de tempo por chamada de rodada() (checkpoint)
# =====================================================================


def _norm(txt):
    t = unicodedata.normalize("NFKD", str(txt))
    return "".join(c for c in t if not unicodedata.combining(c)).upper()


def biomas_disponiveis():
    out = {}
    for cam in pyogrio.list_layers(VEG_GPKG)[:, 0]:
        nome = cam.replace("Vegetacao_Secundaria_", "").rsplit("_", 1)[0]
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


def area_ha_geodesica(geom):
    if geom is None or geom.is_empty:
        return 0.0
    a, _ = GEOD.geometry_area_perimeter(geom)
    return abs(a) / 10_000.0


def carregar_worklist():
    itens = []
    for uf in UFS:
        for cat in CATEGORIAS:
            src = os.path.join(
                BASE_CONFORMIDADE, f"dados_saída_{uf}", f"{uf}_geopackage",
                f"{uf}_Conformidade_Imoveis_{cat}.gpkg")
            if not os.path.exists(src):
                continue
            try:
                cams = set(pyogrio.list_layers(src)[:, 0])
            except Exception:
                continue
            for tipo in TIPOS:
                layer = f"CAR_{uf}_{tipo}_Selecionados_{cat}"
                if layer not in cams:
                    continue
                ds = ogr.Open(src)
                lyr = ds.GetLayerByName(layer)
                total = lyr.GetFeatureCount()
                ds = None
                itens.append({
                    "uf": uf, "categoria": cat, "tipo": tipo,
                    "src": src, "layer": layer, "total": total,
                })
    itens.sort(key=lambda x: (x["categoria"], x["uf"], x["tipo"]))
    return itens


def carregar_progresso():
    if os.path.isfile(PROGRESSO):
        with open(PROGRESSO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_progresso(prog):
    with open(PROGRESSO, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=1)


def _garantir_layer_saida(gpkg_path, layer_name):
    """Cria o gpkg/layer de saida (com o schema fixo) se ainda nao existir."""
    if os.path.exists(gpkg_path):
        ds = ogr.Open(gpkg_path, update=1)
    else:
        drv = ogr.GetDriverByName("GPKG")
        ds = drv.CreateDataSource(gpkg_path)
    lyr = ds.GetLayerByName(layer_name)
    if lyr is None:
        srs = ogr.osr.SpatialReference()
        srs.ImportFromEPSG(4674)
        lyr = ds.CreateLayer(layer_name, srs=srs, geom_type=ogr.wkbMultiPolygon)
        campos = [
            ("uf", ogr.OFTString), ("cod_imovel", ogr.OFTString),
            ("tipo", ogr.OFTString), ("bioma", ogr.OFTString),
            ("classe", ogr.OFTString), ("ano", ogr.OFTInteger),
            ("des_condic", ogr.OFTString), ("selecao_final", ogr.OFTString),
            ("area_ha", ogr.OFTReal),
        ]
        for nome, tipo in campos:
            lyr.CreateField(ogr.FieldDefn(nome, tipo))
    ds = None


def _gravar_registros(gpkg_path, layer_name, registros):
    """registros: lista de dicts com geometry(shapely)+campos. Append via OGR bruto."""
    if not registros:
        return
    _garantir_layer_saida(gpkg_path, layer_name)
    ds = ogr.Open(gpkg_path, update=1)
    lyr = ds.GetLayerByName(layer_name)
    defn = lyr.GetLayerDefn()
    lyr.StartTransaction()
    for r in registros:
        feat = ogr.Feature(defn)
        wkb = shapely.to_wkb(r["geometry"])
        geom = ogr.CreateGeometryFromWkb(wkb)
        if geom.GetGeometryType() == ogr.wkbPolygon:
            geom = ogr.ForceToMultiPolygon(geom)
        feat.SetGeometry(geom)
        for campo in ("uf", "cod_imovel", "tipo", "bioma", "classe",
                      "des_condic", "selecao_final"):
            v = r.get(campo)
            if v is not None:
                feat.SetField(campo, str(v))
        if r.get("ano") is not None:
            try:
                feat.SetField("ano", int(r["ano"]))
            except Exception:
                pass
        feat.SetField("area_ha", float(r.get("area_ha") or 0.0))
        lyr.CreateFeature(feat)
    lyr.CommitTransaction()
    ds = None


def processar_chunk(item, veg_layers, fid_lo, fid_hi):
    src = item["src"]
    layer = item["layer"]
    tema = gpd.read_file(
        src, sql=f'SELECT * FROM "{layer}" WHERE fid >= {fid_lo} AND fid < {fid_hi}',
        sql_dialect="OGRSQL")
    if len(tema) == 0:
        return 0

    tema = limpar(tema)
    if len(tema) == 0:
        return 0

    minx, miny, maxx, maxy = tema.total_bounds
    bbox = (minx, miny, maxx, maxy)

    cols_tema = [c for c in ["cod_imovel", "des_condic", "selecao_final"] if c in tema.columns]
    tema_slim = tema[cols_tema + ["geometry"]].copy().reset_index(drop=True)
    tema_slim["_it"] = tema_slim.index

    registros_totais = []
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

        veg_cols = [c for c in ["CLASSE", "ANO"] if c in veg.columns]
        veg_slim = veg[veg_cols + ["geometry"]].copy().reset_index(drop=True)
        veg_slim["_iv"] = veg_slim.index

        pares = gpd.sjoin(tema_slim, veg_slim, how="inner", predicate="intersects")
        if len(pares) == 0:
            continue

        geom_tema = tema_slim.geometry.to_dict()
        geom_veg = veg_slim.geometry.to_dict()

        for _, p in pares.iterrows():
            try:
                bruta = geom_tema[p["_it"]].intersection(geom_veg[p["_iv"]])
            except Exception:
                try:
                    a = _reparar(geom_tema[p["_it"]])
                    b = _reparar(geom_veg[p["_iv"]])
                    if a is None or b is None:
                        continue
                    bruta = a.intersection(b)
                except Exception:
                    continue
            g = _so_poligonos(bruta)
            if g is None or g.is_empty:
                continue
            reg = {
                "geometry": g,
                "uf": item["uf"],
                "tipo": item["tipo"],
                "bioma": bioma,
                "area_ha": area_ha_geodesica(g),
            }
            if "cod_imovel" in tema_slim.columns:
                reg["cod_imovel"] = p.get("cod_imovel")
            if "des_condic" in tema_slim.columns:
                reg["des_condic"] = p.get("des_condic")
            if "selecao_final" in tema_slim.columns:
                reg["selecao_final"] = p.get("selecao_final")
            if "CLASSE" in veg_slim.columns:
                reg["classe"] = p.get("CLASSE")
            if "ANO" in veg_slim.columns:
                reg["ano"] = p.get("ANO")
            registros_totais.append(reg)

    if registros_totais:
        gpkg_out = OUT_ARQUIVO[item["categoria"]]
        layer_out = OUT_LAYER(item["tipo"], item["categoria"])
        _gravar_registros(gpkg_out, layer_out, registros_totais)

    return len(registros_totais)


def rodada():
    t0 = time.time()
    worklist = carregar_worklist()
    veg_layers = biomas_disponiveis()
    prog = carregar_progresso()

    log = []
    for item in worklist:
        chave = f"{item['uf']}::{item['categoria']}::{item['tipo']}"
        st = prog.get(chave, {"fid_proximo": 0, "concluido": False, "total_pedacos": 0})
        if st["concluido"]:
            continue

        total = item["total"]
        while st["fid_proximo"] <= total:
            if time.time() - t0 > BUDGET_S:
                prog[chave] = st
                salvar_progresso(prog)
                log.append(f"[PAUSA orcamento] {chave} em fid={st['fid_proximo']}/{total}")
                print("\n".join(log))
                print(f"\nRODADA parcial: {round(time.time()-t0,1)}s. Rode de novo pra continuar.")
                return False

            lo = st["fid_proximo"]
            hi = lo + CHUNK
            n_pedacos = processar_chunk(item, veg_layers, lo, hi)
            st["fid_proximo"] = hi
            st["total_pedacos"] += n_pedacos

            if total == 0:
                break

        st["concluido"] = True
        prog[chave] = st
        salvar_progresso(prog)
        log.append(f"[OK] {chave}: total={total} pedacos_gerados={st['total_pedacos']}")

    salvar_progresso(prog)
    print("\n".join(log))
    print(f"\nRODADA completa: TODOS os {len(worklist)} itens processados. Tempo: {round(time.time()-t0,1)}s")
    return True


if __name__ == "__main__":
    while not rodada():
        pass
    print("\nLOTE COMPLETO: todos os itens (uf x categoria x tipo) processados.")
