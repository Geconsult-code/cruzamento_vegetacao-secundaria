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

# =============================== CONFIG ===============================
PASTA_BASE = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR"
GPKG_VEGSEC = os.path.join(
    PASTA_BASE, "Vegetacao_Secundaria", "Vegetacao_Secundaria.gpkg")

# Saída (um gpkg com 3 camadas, todos os estados juntos).
GPKG_SAIDA = os.path.join(PASTA_BASE, "vegsec_x_APP_RL_AUR.gpkg")

# Deixe vazio para TODOS os estados; ou liste siglas (ex.: ["AC"]) para validar.
SOMENTE_ESTES: list[str] = ["AC"]

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


def limpar(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Garante 2D válido e CRS de trabalho."""
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_TRAB)
    elif str(gdf.crs).upper() not in ("EPSG:4674",):
        gdf = gdf.to_crs(CRS_TRAB)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
    inval = ~gdf.geometry.is_valid
    if inval.any():
        gdf.loc[inval, "geometry"] = gdf.loc[inval, "geometry"].apply(make_valid)
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

            # interseção real (overlay); mantém atributos dos dois lados
            cols_tema = ["cod_imovel", "geometry"]
            cols_tema = [c for c in cols_tema if c in tema.columns] + ["geometry"]
            tema_slim = tema[list(dict.fromkeys(cols_tema))].copy()
            veg_cols = [c for c in ["CLASSE", "ANO"] if c in veg.columns]
            veg_slim = veg[veg_cols + ["geometry"]].copy()

            inter = gpd.overlay(tema_slim, veg_slim, how="intersection",
                                keep_geom_type=True)
            if len(inter) == 0:
                continue

            inter["uf"] = uf
            inter["tipo"] = tipo
            inter["bioma"] = bioma
            inter["area_ha"] = inter.geometry.apply(area_ha_geodesica)
            # normaliza nomes de colunas da vegetação
            if "CLASSE" in inter.columns:
                inter = inter.rename(columns={"CLASSE": "classe"})
            if "ANO" in inter.columns:
                inter = inter.rename(columns={"ANO": "ano"})

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
    if os.path.exists(GPKG_SAIDA):
        os.remove(GPKG_SAIDA)
    total_feicoes = 0
    for cam_saida, partes in acumulador.items():
        if not partes:
            continue
        junto = gpd.GeoDataFrame(pd.concat(partes, ignore_index=True),
                                 crs=CRS_TRAB)
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
