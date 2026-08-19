r"""
cruzar_vegsec.py — cruza a vegetação secundária (INPE) com as camadas
temáticas APP / RESERVA_LEGAL / USO_RESTRITO já recortadas para os imóveis
"Representante (manter)" da conformidade SICAR × INCRA.

Para cada estado (pasta _saida_<UF> em PASTA_BASE), o script:
  1. lê as camadas temáticas do conformidade_<UF>_Privado.gpkg (APPS,
     RESERVA_LEGAL, USO_RESTRITO — as que existirem);
  2. para cada bioma, lê SÓ as feições de vegetação secundária dentro do
     retângulo (bbox) das temáticas do estado — evita carregar os 6,7 GB;
  3. calcula a INTERSEÇÃO geométrica real (recorta a temática pela vegetação);
  4. calcula a área geodésica (ha) de cada pedaço (GRS80);
  5. acumula tudo e grava UM GeoPackage de saída com 3 camadas
     (APPS_vegsec, RL_vegsec, AUR_vegsec), com todos os estados juntos.

Cada polígono de saída carrega: uf, cod_imovel, tipo (APP/RL/AUR), bioma,
classe e ano (da vegetação), e area_ha (área geodésica do pedaço).

COMO USAR
---------
1. Confira os caminhos e opções na seção CONFIG.
2. Com o ambiente 'geo' ativo:
       python cruzar_vegsec.py
   Para validar, deixe SOMENTE_ESTES = ["AC"] e rode; depois esvazie a lista
   para processar todos.
"""

from __future__ import annotations

import os
import glob
import unicodedata
from datetime import datetime

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import Geod
from shapely import make_valid
from shapely.geometry import (
    Polygon, MultiPolygon, GeometryCollection)


def _so_poligonos(geom):
    """Devolve só a parte poligonal de uma geometria (descarta linha/ponto).

    make_valid pode transformar um polígono defeituoso numa coleção mista
    (polígono + linha/ponto). O overlay do GeoPandas recusa dimensão mista,
    então aqui reduzimos a geometria à sua parte de área.
    """
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
        # une tudo num MultiPolygon
        partes = []
        for p in polis:
            if isinstance(p, MultiPolygon):
                partes.extend(p.geoms)
            else:
                partes.append(p)
        return MultiPolygon(partes)
    # linha/ponto puros: sem área, descarta
    return None

# =============================== CONFIG ===============================
PASTA_BASE = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR"
GPKG_VEGSEC = os.path.join(
    PASTA_BASE, "Vegetacao_Secundaria", "Vegetacao_Secundaria.gpkg")

# Saída (um gpkg com 3 camadas, todos os estados juntos).
GPKG_SAIDA = os.path.join(PASTA_BASE, "vegsec_x_APP_RL_AUR.gpkg")

# Deixe vazio para TODOS os estados; ou liste siglas (ex.: ["AC"]) para validar.
SOMENTE_ESTES: list[str] = []

# ANEXAR = True: não apaga a saída; acrescenta os estados de SOMENTE_ESTES às
# camadas já existentes (use para completar estados que faltaram, ex.: ["AM"]).
# ANEXAR = False: regrava a saída do zero (use para rodar tudo de novo).
ANEXAR = False

# Mapeia o nome da camada temática -> rótulo de tipo e camada de saída.
TEMATICAS = {
    "APPS": ("APP", "APPS_vegsec"),
    "RESERVA_LEGAL": ("RL", "RL_vegsec"),
    "USO_RESTRITO": ("AUR", "AUR_vegsec"),
}

CRS_TRAB = "EPSG:4674"
GEOD = Geod(ellps="GRS80")
# =====================================================================


def _norm(txt: str) -> str:
    t = unicodedata.normalize("NFKD", str(txt))
    return "".join(c for c in t if not unicodedata.combining(c)).upper()


def biomas_disponiveis() -> dict[str, str]:
    """Mapeia bioma -> nome da camada, a partir das camadas do gpkg de veg."""
    out = {}
    for cam in pyogrio.list_layers(GPKG_VEGSEC)[:, 0]:
        # nome no padrão Vegetacao_Secundaria_<Bioma>_2022
        nome = cam.replace("Vegetacao_Secundaria_", "").rsplit("_", 1)[0]
        out[nome] = cam
    return out


def area_ha_geodesica(geom) -> float:
    """Área geodésica (ha) de um polígono/multipolígono em EPSG:4674."""
    if geom is None or geom.is_empty:
        return 0.0
    a, _ = GEOD.geometry_area_perimeter(geom)
    return abs(a) / 10_000.0  # m² -> ha


def _reparar(geom):
    """Repara uma geometria inválida sem quebrar.

    Tenta make_valid; se ele lançar exceção (acontece com algumas geometrias
    do AM em certas versões do GEOS), cai para buffer(0); se ainda falhar,
    devolve None (a geometria é descartada — são pouquíssimas e irreparáveis).
    """
    if geom is None or geom.is_empty:
        return None
    if geom.is_valid:
        return geom
    try:
        return make_valid(geom)
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
    """Garante 2D válido e CRS de trabalho (reparo resiliente)."""
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_TRAB)
    elif str(gdf.crs).upper() not in ("EPSG:4674",):
        gdf = gdf.to_crs(CRS_TRAB)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
    # repara geometria a geometria, sem deixar uma inválida derrubar o estado
    n_antes = len(gdf)
    gdf["geometry"] = gdf.geometry.apply(_reparar)
    gdf = gdf[gdf.geometry.notna()].copy()
    # reduz cada geometria à sua parte poligonal (evita 'mixed-dimension')
    gdf["geometry"] = gdf.geometry.apply(_so_poligonos)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    n_descartadas = n_antes - len(gdf)
    if n_descartadas:
        print(f"       (limpeza: {n_descartadas} geometria(s) irreparável(is) "
              f"descartada(s))", flush=True)
    return gdf


def cruzar_estado(uf: str, gpkg_estado: str, veg_layers: dict[str, str],
                  acumulador: dict[str, list]) -> dict:
    """Cruza as temáticas de um estado com a vegetação; alimenta o acumulador."""
    cams_estado = set(pyogrio.list_layers(gpkg_estado)[:, 0])
    resumo = {}

    for nome_tema, (tipo, cam_saida) in TEMATICAS.items():
        if nome_tema not in cams_estado:
            continue
        tema = limpar(gpd.read_file(gpkg_estado, layer=nome_tema))
        if len(tema) == 0:
            resumo[tipo] = 0
            continue

        # bbox do estado (nas temáticas) para recortar a leitura da vegetação
        minx, miny, maxx, maxy = tema.total_bounds
        bbox = (minx, miny, maxx, maxy)

        n_pedacos = 0
        for bioma, cam_veg in veg_layers.items():
            # lê SÓ a vegetação dentro do bbox do estado (índice espacial)
            try:
                veg = gpd.read_file(GPKG_VEGSEC, layer=cam_veg, bbox=bbox)
            except Exception:
                veg = gpd.GeoDataFrame(
                    columns=["CLASSE", "ANO", "geometry"], geometry="geometry",
                    crs=CRS_TRAB)
            if len(veg) == 0:
                continue
            veg = limpar(veg)
            if len(veg) == 0:
                continue

            # interseção via sjoin + interseção par a par (imune a
            # 'mixed-dimension': controlamos e filtramos cada resultado).
            cols_tema = [c for c in ["cod_imovel"] if c in tema.columns]
            tema_slim = tema[cols_tema + ["geometry"]].copy()
            tema_slim = tema_slim.reset_index(drop=True)
            tema_slim["_it"] = tema_slim.index
            veg_cols = [c for c in ["CLASSE", "ANO"] if c in veg.columns]
            veg_slim = veg[veg_cols + ["geometry"]].copy().reset_index(drop=True)
            veg_slim["_iv"] = veg_slim.index

            # pares que realmente se intersectam (usa índice espacial)
            pares = gpd.sjoin(tema_slim, veg_slim, how="inner",
                              predicate="intersects")
            if len(pares) == 0:
                continue

            geom_tema = tema_slim.geometry.to_dict()
            geom_veg = veg_slim.geometry.to_dict()
            registros = []
            for _, p in pares.iterrows():
                try:
                    bruta = geom_tema[p["_it"]].intersection(geom_veg[p["_iv"]])
                except Exception:
                    # interseção de um par específico falhou; tenta reparar antes
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
                reg = {"geometry": g}
                if "cod_imovel" in tema_slim.columns:
                    reg["cod_imovel"] = p.get("cod_imovel")
                if "CLASSE" in veg_slim.columns:
                    reg["classe"] = p.get("CLASSE")
                if "ANO" in veg_slim.columns:
                    reg["ano"] = p.get("ANO")
                registros.append(reg)
            if not registros:
                continue
            inter = gpd.GeoDataFrame(registros, geometry="geometry", crs=CRS_TRAB)

            inter["uf"] = uf
            inter["tipo"] = tipo
            inter["bioma"] = bioma
            inter["area_ha"] = inter.geometry.apply(area_ha_geodesica)

            cols_final = ["uf", "cod_imovel", "tipo", "bioma", "classe", "ano",
                          "area_ha", "geometry"]
            cols_final = [c for c in cols_final if c in inter.columns]
            acumulador[cam_saida].append(inter[cols_final])
            n_pedacos += len(inter)

        resumo[tipo] = n_pedacos
    return resumo


def main() -> int:
    if not os.path.exists(GPKG_VEGSEC):
        print(f"Vegetação não encontrada: {GPKG_VEGSEC}")
        return 1
    veg_layers = biomas_disponiveis()
    print(f"Biomas na vegetação: {', '.join(veg_layers)}")

    # estados a processar
    saidas = sorted(glob.glob(os.path.join(PASTA_BASE, "_saida_*")))
    alvos = []
    for d in saidas:
        uf = os.path.basename(d).replace("_saida_", "").upper()
        if SOMENTE_ESTES and uf not in SOMENTE_ESTES:
            continue
        gpkg = os.path.join(d, f"conformidade_{uf}_Privado.gpkg")
        if os.path.exists(gpkg):
            alvos.append((uf, gpkg))

    print(f"Estados a processar ({len(alvos)}): "
          f"{', '.join(uf for uf, _ in alvos)}")
    print(f"Início: {datetime.now():%H:%M:%S}\n")

    acumulador: dict[str, list] = {c: [] for (_, c) in TEMATICAS.values()}
    sucesso, falhas = [], []
    for uf, gpkg in alvos:
        print(f"  [{uf}] cruzando...", flush=True)
        try:
            resumo = cruzar_estado(uf, gpkg, veg_layers, acumulador)
            txt = " | ".join(f"{t}: {n}" for t, n in resumo.items())
            print(f"       {txt}", flush=True)
            sucesso.append(uf)
        except Exception as e:
            print(f"       !! ERRO: {e}", flush=True)
            falhas.append((uf, str(e)))

    # grava a saída (uma camada por tipo, todos os estados juntos)
    if not ANEXAR and os.path.exists(GPKG_SAIDA):
        os.remove(GPKG_SAIDA)
    total_feicoes = 0
    for cam_saida, partes in acumulador.items():
        if not partes:
            continue
        junto = gpd.GeoDataFrame(pd.concat(partes, ignore_index=True),
                                 crs=CRS_TRAB)
        # no modo anexar, se a camada já existe, concatena com o que há nela
        modo = "w"
        if ANEXAR and os.path.exists(GPKG_SAIDA):
            try:
                existente = gpd.read_file(GPKG_SAIDA, layer=cam_saida)
                junto = gpd.GeoDataFrame(
                    pd.concat([existente, junto], ignore_index=True),
                    crs=CRS_TRAB)
            except Exception:
                pass  # camada ainda não existe no arquivo; grava nova
        junto.to_file(GPKG_SAIDA, layer=cam_saida, driver="GPKG")
        total_feicoes += len(junto)
        print(f"\n  gravado {cam_saida}: {len(junto)} polígonos "
              f"({junto['area_ha'].sum():,.1f} ha)")

    print(f"\n{'#'*60}\n  RELATÓRIO ({datetime.now():%H:%M:%S})\n{'#'*60}")
    print(f"Sucesso ({len(sucesso)}): {', '.join(sucesso) or '—'}")
    if falhas:
        print(f"Falhas ({len(falhas)}):")
        for uf, msg in falhas:
            print(f"  {uf}: {msg}")
    print(f"Saída: {GPKG_SAIDA}  ({total_feicoes} polígonos no total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
