
# -*- coding: utf-8 -*-
r"""
2_cruzamento_espacial_VS_qualificado.py

Adaptado de 2_cruzamento_espacial_VS.py para a nova estrutura de dados
(Maio2026) e para a VS 2022 QUALIFICADA (filtro >=2ha aplicado ANTES da
interseccao, ja no arquivo de origem).

Fontes:
  Tema (APP/RL/AUR "Selecionados"):
    GEOPACKAGE\Cadastro Ambiental Rural_Maio2026\<UF>_CAR_Imoveis_Selecionados_<categoria>.gpkg
    layer CAR_<UF>_<tipo>_Selecionados_<categoria>
    (Habilitados ja vinha assim; Analisados/Nao_Analisados foram
    incorporadas nesta mesma rodada a partir da rodada anterior do
    pipeline -- ver 0_incorporar_app_rl_aur_analisados.py)
  Vegetacao secundaria 2022 qualificada, ja recortada por UF (passo 1b):
    GEOPACKAGE\Cadastro Ambiental Rural_Maio2026\_veg_qualificada_uf\<UF>_Vegetacao_Secundaria_2022_Qualificada.gpkg
    layer VS_<UF>_qualificada (campos: bioma, ano, area_ha_vs)

Saida (3 arquivos, sufixo _Qualificado no NOME DO ARQUIVO -- layers
mantem o mesmo nome de antes):
  GEOPACKAGE\Cruzamento CAR Vegetacao Secundaria\VS_2022_Imoveis_Selecionados_Habilitados_Qualificado.gpkg
     layers: VS_APP_Habilitados, VS_RL_Habilitados, VS_AUR_Habilitados
  ...Analisados_Qualificado.gpkg / ...Nao_Analisados_Qualificado.gpkg (mesmo padrao)

Campos gravados por poligono: uf, cod_imovel, tipo, bioma, ano,
des_condic, selecao_final, area_ha (area da interseccao, geodesica
GRS80 -- NAO confundir com area_ha_vs da VS antes do cruzamento).

Resiliente/retomavel via _progresso_vegsec_selecionados_qualificado.json
(mesmo padrao do script original: guarda o ultimo indice processado por
item uf::categoria::tipo). Cada chamada de rodada() processa até um
orcamento de tempo (BUDGET_S) e para -- rodar de novo continua.
"""

import os
import json
import time

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import Geod
import shapely
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection

# =============================== CONFIG ===============================
CAR_DIR = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\Cadastro Ambiental Rural_Maio2026"
VEG_DIR = os.path.join(CAR_DIR, "_veg_qualificada_uf")
CRUZ_DIR = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\Cruzamento CAR Vegetacao Secundaria"

PROGRESSO = os.path.join(CAR_DIR, "_progresso_vegsec_selecionados_qualificado.json")

CATEGORIAS = ["Habilitados", "Analisados", "Nao_Analisados"]
TIPOS = ["APP", "RL", "AUR"]

ARQUIVO_ORIGEM = {
    "Habilitados": "{uf}_CAR_Imoveis_Selecionados_Habilitados.gpkg",
    "Analisados": "{uf}_CAR_Imoveis_Selecionados_Analisados.gpkg",
    "Nao_Analisados": "{uf}_CAR_Imoveis_Selecionados_Nao_Analisados.gpkg",
}

OUT_ARQUIVO = {
    "Habilitados": os.path.join(CRUZ_DIR, "VS_2022_Imoveis_Selecionados_Habilitados_Qualificado.gpkg"),
    "Analisados": os.path.join(CRUZ_DIR, "VS_2022_Imoveis_Selecionados_Analisados_Qualificado.gpkg"),
    "Nao_Analisados": os.path.join(CRUZ_DIR, "VS_2022_Imoveis_Selecionados_Nao_Analisados_Qualificado.gpkg"),
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

CHUNK = 5000
BUDGET_S = 300
# =====================================================================


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
        veg_uf = os.path.join(VEG_DIR, f"{uf}_Vegetacao_Secundaria_2022_Qualificada.gpkg")
        if not os.path.exists(veg_uf):
            continue
        for cat in CATEGORIAS:
            src = os.path.join(CAR_DIR, ARQUIVO_ORIGEM[cat].format(uf=uf))
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
                info = pyogrio.read_info(src, layer=layer)
                total = int(info["features"])
                itens.append({
                    "uf": uf, "categoria": cat, "tipo": tipo,
                    "src": src, "layer": layer, "veg_uf": veg_uf, "total": total,
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


_COLS_SAIDA = ["uf", "cod_imovel", "tipo", "bioma", "ano",
               "des_condic", "selecao_final", "area_ha", "geometry"]


def _para_multipolygon(geom):
    if geom is None:
        return None
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    return geom


def _gravar_registros(gpkg_path, layer_name, registros):
    if not registros:
        return
    linhas = []
    for r in registros:
        linha = {c: r.get(c) for c in _COLS_SAIDA}
        linha["geometry"] = _para_multipolygon(r["geometry"])
        linhas.append(linha)

    gdf = gpd.GeoDataFrame(linhas, geometry="geometry", crs=CRS_TRAB)
    for c in ["uf", "cod_imovel", "tipo", "bioma", "des_condic", "selecao_final"]:
        gdf[c] = gdf[c].astype("string")
    gdf["ano"] = pd.to_numeric(gdf["ano"], errors="coerce").astype("Int64")
    gdf["area_ha"] = gdf["area_ha"].astype(float)

    existe = False
    if os.path.exists(gpkg_path):
        try:
            existe = layer_name in set(pyogrio.list_layers(gpkg_path)[:, 0])
        except Exception:
            existe = False
    modo = "a" if existe else "w"
    gdf.to_file(gpkg_path, layer=layer_name, driver="GPKG", mode=modo)


_CACHE_TEMA = {"chave": None, "tema": None}
_CACHE_VEG = {"uf": None, "veg": None}


def _carregar_veg_uf(item):
    if _CACHE_VEG["uf"] == item["uf"]:
        return _CACHE_VEG["veg"]
    veg = gpd.read_file(item["veg_uf"], layer=f"VS_{item['uf']}_qualificada")
    veg = limpar(veg)
    cols = [c for c in ["bioma", "ano"] if c in veg.columns]
    veg = veg[cols + ["geometry"]].reset_index(drop=True)
    _CACHE_VEG["uf"] = item["uf"]
    _CACHE_VEG["veg"] = veg
    return veg


def _preparar_tema(item):
    chave = f"{item['uf']}::{item['categoria']}::{item['tipo']}"
    if _CACHE_TEMA["chave"] == chave:
        return _CACHE_TEMA["tema"]

    tema = gpd.read_file(item["src"], layer=item["layer"])
    tema = limpar(tema)
    cols_tema = [c for c in ["cod_imovel", "des_condic", "selecao_final"] if c in tema.columns]
    tema = tema[cols_tema + ["geometry"]].reset_index(drop=True)

    _CACHE_TEMA["chave"] = chave
    _CACHE_TEMA["tema"] = tema
    return tema


def processar_chunk(item, lo, hi):
    tema = _preparar_tema(item)
    veg = _carregar_veg_uf(item)
    if len(tema) == 0 or lo >= len(tema):
        return 0

    tema_slim = tema.iloc[lo:hi].copy()
    if len(tema_slim) == 0:
        return 0
    tema_slim["_it"] = tema_slim.index

    registros_totais = []
    if len(veg) > 0:
        veg_slim = veg.copy()
        veg_slim["_iv"] = veg_slim.index

        pares = gpd.sjoin(tema_slim, veg_slim, how="inner", predicate="intersects")
        if len(pares) > 0:
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
                    "area_ha": area_ha_geodesica(g),
                }
                if "cod_imovel" in tema_slim.columns:
                    reg["cod_imovel"] = p.get("cod_imovel")
                if "des_condic" in tema_slim.columns:
                    reg["des_condic"] = p.get("des_condic")
                if "selecao_final" in tema_slim.columns:
                    reg["selecao_final"] = p.get("selecao_final")
                if "bioma" in veg_slim.columns:
                    reg["bioma"] = p.get("bioma")
                if "ano" in veg_slim.columns:
                    reg["ano"] = p.get("ano")
                registros_totais.append(reg)

    if registros_totais:
        gpkg_out = OUT_ARQUIVO[item["categoria"]]
        layer_out = OUT_LAYER(item["tipo"], item["categoria"])
        _gravar_registros(gpkg_out, layer_out, registros_totais)

    return len(registros_totais)


def rodada():
    t0 = time.time()
    worklist = carregar_worklist()
    prog = carregar_progresso()

    log = []
    for item in worklist:
        chave = f"{item['uf']}::{item['categoria']}::{item['tipo']}"
        st = prog.get(chave, {"fid_proximo": 0, "concluido": False, "total_pedacos": 0})
        if st["concluido"]:
            continue

        tema_item = _preparar_tema(item)
        total = len(tema_item)

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
            n_pedacos = processar_chunk(item, lo, hi)
            st["fid_proximo"] = hi
            st["total_pedacos"] += n_pedacos

            if total == 0:
                break

        st["concluido"] = True
        prog[chave] = st
        salvar_progresso(prog)
        log.append(f"[OK] {chave}: total={total} pedacos_gerados={st['total_pedacos']}")
        _CACHE_TEMA["chave"] = None
        _CACHE_TEMA["tema"] = None
        import gc
        gc.collect()

    salvar_progresso(prog)
    print("\n".join(log))
    print(f"\nRODADA completa: TODOS os {len(worklist)} itens processados. Tempo: {round(time.time()-t0,1)}s")
    return True


if __name__ == "__main__":
    while not rodada():
        pass
    print("\nLOTE COMPLETO: todos os itens (uf x categoria x tipo) processados.")
