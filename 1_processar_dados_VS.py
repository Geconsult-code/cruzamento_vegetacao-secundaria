# -*- coding: utf-8 -*-
r"""
1_processar_dados_VS.py — passo 1 do workflow numerado do repositório
cruzamento_vegetacao-secundaria.

Recorta a vegetação secundária (INPE, Vegetacao_Secundaria_2022.gpkg, 6
camadas por bioma) para cada UF, gerando um único arquivo/camada por
estado com todos os polígonos de VS que tocam aquele estado — biomas
concatenados, mantendo na tabela de atributos a separação do bioma a que
cada polígono pertence e o ano da VS.

O recorte é por bounding box (mesmo padrão já validado em
cruzar_vegsec_selecionados.py — recorte por bbox, não por limite
administrativo exato). O bbox de cada UF é a união dos limites
(total_bounds) dos imóveis já processados pelo repositório
analise_conformidade_sicar-incra
(<UF>_Imoveis_Selecionados_Habilitados.gpkg + <UF>_Imoveis_Privados_{Analisados,Nao_Analisados}.gpkg, em
Analise_Conformidade\dados_saída_<UF>\<UF>_geopackage\) — evita depender
de uma malha de limites estaduais à parte.

Saída (no mesmo diretório dos outros dados do repositório de
análise_conformidade_sicar-incra, ao lado de <UF>_Imoveis_Privados_*.gpkg
etc.):
  Analise_Conformidade\dados_saída_<UF>\<UF>_geopackage\<UF>_Vegetacao_Secundaria_2022.gpkg
    layer: VS_<UF>
    campos: bioma, classe, ano, geometry

Consumido pelo passo 2 (2_cruzamento_espacial_VS.py), que passa a ler esse
arquivo já pré-recortado por UF em vez de reler os 6 biomas inteiros do
INPE a cada categoria/tipo de imóvel.

Resiliente/retomável no nível de UF (não sub-UF): guarda em
_progresso_processar_VS.json quais estados já foram concluídos; rodar de
novo pula os que já têm saída gravada, a menos que a UF esteja em
REFAZER. Não há orçamento de tempo por chamada (BUDGET_S) aqui porque o
volume de dados por item é o mesmo já processado de uma só vez, sem
chunking, pelo desenho original de cruzar_vegsec_selecionados.py (leitura
do bioma inteiro por bbox do estado) — só que agora uma única vez por UF,
em vez de uma vez por UF×categoria×tipo.

COMO USAR
---------
Ambiente com GeoPandas/pyogrio (mesmo do restante do repositório):
    python 1_processar_dados_VS.py
Valide com SOMENTE_ESTES = ["AC"]; depois esvazie para todos.
"""

from __future__ import annotations

import os
import json

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection

# =============================== CONFIG ===============================
BASE_ROOT = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR"
PASTA_ANALISE = os.path.join(BASE_ROOT, "Analise_Conformidade")
VEG_DIR = os.path.join(BASE_ROOT, "Vegetacao_Secundaria")
VEG_GPKG = os.path.join(VEG_DIR, "Vegetacao_Secundaria_2022.gpkg")

# Deixe vazio para TODOS os estados; ou liste siglas (ex.: ["AC"]) para validar.
SOMENTE_ESTES: list[str] = []
# UFs para forcar reprocessar mesmo com saida ja existente; vazio = nenhuma.
REFAZER: list[str] = []

CATEGORIAS_IMOVEIS = ["Habilitados", "Analisados", "Nao_Analisados"]
CRS_TRAB = "EPSG:4674"
# =====================================================================

UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
]

PROGRESSO = os.path.join(PASTA_ANALISE, "_progresso_processar_VS.json")


def biomas_disponiveis() -> dict:
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


def limpar(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
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


def bbox_uf(uf: str):
    """Uniao do total_bounds dos buckets de imoveis ja processados da UF
    (script 3 do repositorio analise_conformidade_sicar-incra)."""
    pasta_gpkg = os.path.join(PASTA_ANALISE, f"dados_saída_{uf}", f"{uf}_geopackage")
    xmins, ymins, xmaxs, ymaxs = [], [], [], []
    for cat in CATEGORIAS_IMOVEIS:
        # Habilitados foi renomeado de _Imoveis_Privados_ para _Imoveis_Selecionados_
        # (10/09/2026) -- os demais buckets continuam com o nome antigo.
        if cat == "Habilitados":
            nome_arquivo = f"{uf}_Imoveis_Selecionados_Habilitados.gpkg"
        else:
            nome_arquivo = f"{uf}_Imoveis_Privados_{cat}.gpkg"
        caminho = os.path.join(pasta_gpkg, nome_arquivo)
        if not os.path.exists(caminho):
            continue
        layer = f"CAR_{uf}_Imoveis_{cat}"
        try:
            info = pyogrio.read_info(caminho, layer=layer)
        except Exception:
            continue
        if info.get("features", 0) == 0 or info.get("total_bounds") is None:
            continue
        minx, miny, maxx, maxy = info["total_bounds"]
        xmins.append(minx)
        ymins.append(miny)
        xmaxs.append(maxx)
        ymaxs.append(maxy)
    if not xmins:
        return None
    return (min(xmins), min(ymins), max(xmaxs), max(ymaxs))


def processar_uf(uf: str) -> dict:
    pasta_gpkg = os.path.join(PASTA_ANALISE, f"dados_saída_{uf}", f"{uf}_geopackage")
    saida = os.path.join(pasta_gpkg, f"{uf}_Vegetacao_Secundaria_2022.gpkg")

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
        cols = [c for c in ["CLASSE", "ANO"] if c in veg.columns]
        veg = veg[cols + ["geometry"]].copy()
        veg = veg.rename(columns={"CLASSE": "classe", "ANO": "ano"})
        veg["bioma"] = bioma
        partes.append(veg)

    if not partes:
        return {"uf": uf, "aviso": "nenhum poligono de VS no bbox da UF"}

    todos = gpd.GeoDataFrame(pd.concat(partes, ignore_index=True), crs=CRS_TRAB)
    cols_finais = [c for c in ["bioma", "classe", "ano"] if c in todos.columns] + ["geometry"]
    todos = todos[cols_finais]
    if "ano" in todos.columns:
        todos["ano"] = pd.to_numeric(todos["ano"], errors="coerce").astype("Int64")
    if "bioma" in todos.columns:
        todos["bioma"] = todos["bioma"].astype("string")
    if "classe" in todos.columns:
        todos["classe"] = todos["classe"].astype("string")

    if os.path.exists(saida):
        os.remove(saida)
    todos.to_file(saida, layer=f"VS_{uf}", driver="GPKG")

    return {"uf": uf, "poligonos": int(len(todos)), "saida": saida}


def carregar_progresso() -> dict:
    if os.path.isfile(PROGRESSO):
        with open(PROGRESSO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_progresso(prog: dict) -> None:
    with open(PROGRESSO, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=1)


def main() -> int:
    alvos = SOMENTE_ESTES if SOMENTE_ESTES else UFS
    prog = carregar_progresso()
    print(f"Estados a processar ({len(alvos)}): {', '.join(alvos)}")

    sucesso, falhas = [], []
    for uf in alvos:
        if prog.get(uf, {}).get("concluido") and uf not in REFAZER:
            print(f"[{uf}] ja processado, pulando (use REFAZER pra forcar)")
            continue
        print(f"[{uf}] recortando vegetacao secundaria...", flush=True)
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

    print(f"\nSucesso ({len(sucesso)}): {', '.join(sucesso) or '—'}")
    if falhas:
        print(f"Falhas ({len(falhas)}):")
        for uf, msg in falhas:
            print(f"  {uf}: {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
