# -*- coding: utf-8 -*-
r"""
2_cruzamento_espacial_VS.py — passo 2 do workflow numerado do repositório
cruzamento_vegetacao-secundaria.

(Renomeado/adaptado de cruzar_vegsec_selecionados.py — agora cruza as três
categorias de imóveis do pipeline numerado do repositório
analise_conformidade_sicar-incra: Habilitados, Analisados e Não
Analisados — em vez de só as duas últimas.)

Cruza espacialmente a vegetação secundária (INPE, já recortada por UF pelo
passo 1: 1_processar_dados_VS.py) com as camadas APP/RL/AUR "Selecionados"
já recortadas para os imóveis de cada categoria (saída do passo 5 do
repositório analise_conformidade_sicar-incra):

  Habilitados      -> <UF>_Imoveis_Privados_Habilitados.gpkg
                       layer CAR_<UF>_{APP,RL,AUR}_Selecionados_Habilitados
  Analisados       -> <UF>_Conformidade_Imoveis_Analisados.gpkg
                       layer CAR_<UF>_{APP,RL,AUR}_Selecionados_Analisados
  Nao_Analisados   -> <UF>_Conformidade_Imoveis_Nao_Analisados.gpkg
                       layer CAR_<UF>_{APP,RL,AUR}_Selecionados_Nao_Analisados

(todos em Analise_Conformidade\dados_saída_<UF>\<UF>_geopackage\)

Para cada UF x categoria x tipo (APP/RL/AUR):
  1. lê a camada temática (uma única vez por item, cacheada em memória
     enquanto o item não muda) e a vegetação secundária JÁ PRÉ-RECORTADA
     daquela UF pelo passo 1 (<UF>_Vegetacao_Secundaria.gpkg, layer
     VS_<UF> — cacheada por UF, reaproveitada entre categorias/tipos do
     mesmo estado, já que não depende deles);
  2. fatia o tema (já em memória) em blocos de CHUNK feições (por posição,
     não por FID) só para limitar o tempo/memória do passo de
     interseciona-e-grava, retomável entre chamadas de rodada();
  3. interseciona tema x vegetação (sjoin 'intersects' + shapely
     intersection), reduzindo cada resultado à sua parte poligonal
     (imune a 'mixed-dimension', mesmo padrão já validado no projeto);
  4. calcula área geodésica (ha, GRS80) de cada pedaço;
  5. grava (append, via GeoPandas/pyogrio) direto na camada de saída
     VS_<TIPO>_<categoria> dentro do gpkg final da categoria.

Saída (3 arquivos — Habilitados é novo; Não_Analisados/Analisados mantêm
o nome já em produção):
  Vegetacao_Secundaria\VS_Imoveis_Selecionados_Habilitados.gpkg
     layers: VS_APP_Habilitados, VS_RL_Habilitados, VS_AUR_Habilitados
  Vegetacao_Secundaria\VS_Imoveis_Selecionados_Analisados.gpkg
     layers: VS_APP_Analisados, VS_RL_Analisados, VS_AUR_Analisados
  Vegetacao_Secundaria\VS_Imoveis_Selecionados_Nao_Analisados.gpkg
     layers: VS_APP_Nao_Analisados, VS_RL_Nao_Analisados, VS_AUR_Nao_Analisados

Campos gravados por polígono: uf, cod_imovel, tipo, bioma, classe, ano,
des_condic, selecao_final, area_ha (des_condic/selecao_final só existem
para quem a camada temática de origem tiver essas colunas — Habilitados
não passa pela classificação de coerência do passo 4, então não tem
selecao_final).

Resiliente e retomável via _progresso_vegsec_selecionados.json (guarda o
último FID processado por item uf::categoria::tipo). Cada chamada de
rodada() processa até um orçamento de tempo (BUDGET_S) e para, para caber
no limite de tempo da ponte QGIS - rodar de novo continua de onde parou.
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
BASE_ROOT = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR"
BASE_ANALISE = os.path.join(BASE_ROOT, "Analise_Conformidade")
VEG_DIR = os.path.join(BASE_ROOT, "Vegetacao_Secundaria")

PROGRESSO = os.path.join(VEG_DIR, "_progresso_vegsec_selecionados.json")

CATEGORIAS = ["Habilitados", "Analisados", "Nao_Analisados"]
TIPOS = ["APP", "RL", "AUR"]

# Arquivo de origem (imóveis + temáticas selecionadas), por categoria, em
# dados_saída_<UF>\<UF>_geopackage\ — layer sempre CAR_<UF>_<tipo>_Selecionados_<categoria>.
ARQUIVO_ORIGEM = {
    "Habilitados": "{uf}_Imoveis_Privados_Habilitados.gpkg",
    "Analisados": "{uf}_Conformidade_Imoveis_Analisados.gpkg",
    "Nao_Analisados": "{uf}_Conformidade_Imoveis_Nao_Analisados.gpkg",
}

OUT_ARQUIVO = {
    "Habilitados": os.path.join(VEG_DIR, "VS_Imoveis_Selecionados_Habilitados.gpkg"),
    "Analisados": os.path.join(VEG_DIR, "VS_Imoveis_Selecionados_Analisados.gpkg"),
    "Nao_Analisados": os.path.join(VEG_DIR, "VS_Imoveis_Selecionados_Nao_Analisados.gpkg"),
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

CHUNK = 5000       # feições da temática por bloco (fatiamento em memória)
BUDGET_S = 300      # orçamento de tempo por chamada de rodada() (checkpoint)
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
        pasta_gpkg = os.path.join(BASE_ANALISE, f"dados_saída_{uf}", f"{uf}_geopackage")
        veg_uf = os.path.join(pasta_gpkg, f"{uf}_Vegetacao_Secundaria.gpkg")
        if not os.path.exists(veg_uf):
            continue
        for cat in CATEGORIAS:
            src = os.path.join(pasta_gpkg, ARQUIVO_ORIGEM[cat].format(uf=uf))
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


_COLS_SAIDA = ["uf", "cod_imovel", "tipo", "bioma", "classe", "ano",
               "des_condic", "selecao_final", "area_ha", "geometry"]


def _para_multipolygon(geom):
    """Forca MultiPolygon (mesmo tipo geometrico em todos os blocos gravados)."""
    if geom is None:
        return None
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    return geom


def _gravar_registros(gpkg_path, layer_name, registros):
    """registros: lista de dicts com geometry(shapely)+campos.

    Grava via GeoPandas/pyogrio (sem depender do pacote osgeo): mode='w' na
    primeira vez que a layer e criada, mode='a' (append) nas seguintes.
    """
    if not registros:
        return
    linhas = []
    for r in registros:
        linha = {c: r.get(c) for c in _COLS_SAIDA}
        linha["geometry"] = _para_multipolygon(r["geometry"])
        linhas.append(linha)

    gdf = gpd.GeoDataFrame(linhas, geometry="geometry", crs=CRS_TRAB)
    for c in ["uf", "cod_imovel", "tipo", "bioma", "classe", "des_condic", "selecao_final"]:
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
    """Vegetacao secundaria ja pre-recortada da UF (passo 1). Cacheada por
    UF, reaproveitada entre categorias/tipos do mesmo estado."""
    if _CACHE_VEG["uf"] == item["uf"]:
        return _CACHE_VEG["veg"]
    veg = gpd.read_file(item["veg_uf"], layer=f"VS_{item['uf']}")
    veg = limpar(veg)
    cols = [c for c in ["bioma", "classe", "ano"] if c in veg.columns]
    veg = veg[cols + ["geometry"]].reset_index(drop=True)
    _CACHE_VEG["uf"] = item["uf"]
    _CACHE_VEG["veg"] = veg
    return veg


def _preparar_tema(item):
    """Carrega e prepara (uma UNICA vez por item) o tema inteiro do estado.
    Fica em cache (modulo) enquanto o item nao muda."""
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
                if "classe" in veg_slim.columns:
                    reg["classe"] = p.get("classe")
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
        # libera a memoria do tema do item concluido antes de seguir pro proximo
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
