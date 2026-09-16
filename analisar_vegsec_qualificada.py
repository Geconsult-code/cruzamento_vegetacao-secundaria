# -*- coding: utf-8 -*-
"""
Analisa e compara os arquivos de vegetacao secundaria (INPE) na pasta
INPE_Vegetacao_Secundaria: area (ha) e numero de poligonos por bioma, para cada
arquivo (bruto x qualificado/filtrado por area minima).

Objetivo: antes de rodar o cruzamento espacial com os projetos, comparar:
  - VS_2022_TerraBrasilis_Vegetacao_Secundaria_Brasil.gpkg              (2022 BRUTO, 6 biomas)
  - VS_2022_TerraBrasilis_Vegetacao_Secundaria_Qualificada_Brasil.gpkg  (2022 QUALIFICADO, 6 biomas)
  - VS_2024_Terraclass_Vegetacao_Secundaria_Bioma_Amazonia.gpkg          (2024 BRUTO, Amazonia)
  - VS_2024_Terraclass_Vegetacao_Secundaria_Bioma_Cerrado.gpkg           (2024 BRUTO, Cerrado)
  - VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Cerrado.gpkg (2024 QUALIFICADO, Cerrado)

Area calculada via reprojecao para CRS de area equivalente (EPSG:6933) -- metodo
ja validado no projeto como equivalente a area geodesica GRS80 (diferenca ~0,003%
observada anteriormente), e MUITO mais rapido que calcular geodesico poligono a
poligono quando ha milhoes de feicoes (aqui e uma soma vetorizada, nao um loop).

Gera um CSV: resumo_vegsec_qualificada.csv (arquivo, layer, bioma_detectado, n_poligonos, area_ha)

Uso:
    conda activate geo
    python analisar_vegsec_qualificada.py

Pode demorar alguns minutos nos arquivos maiores (2022 tem ~1,4 milhao de
poligonos so na Amazonia) -- há print de progresso por camada.
"""

import os
import time

import geopandas as gpd
import pandas as pd
import pyogrio

PASTA = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\INPE_Vegetacao_Secundaria"
SAIDA_CSV = os.path.join(PASTA, "resumo_vegsec_qualificada.csv")

ARQUIVOS = [
    "VS_2022_TerraBrasilis_Vegetacao_Secundaria_Brasil.gpkg",
    "VS_2022_TerraBrasilis_Vegetacao_Secundaria_Qualificada_Brasil.gpkg",
    "VS_2024_Terraclass_Vegetacao_Secundaria_Bioma_Amazonia.gpkg",
    "VS_2024_Terraclass_Vegetacao_Secundaria_Bioma_Cerrado.gpkg",
    "VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Cerrado.gpkg",
]

BIOMAS = ["Amazonia", "Cerrado", "Caatinga", "Mata_Atlantica", "Pampa", "Pantanal"]
# tokens alternativos que podem aparecer no nome da camada (sem acento, varias grafias)
BIOMA_TOKENS = {
    "Amazonia": ["amazonia", "amazon"],
    "Cerrado": ["cerrado"],
    "Caatinga": ["caatinga"],
    "Mata_Atlantica": ["mata_atlantica", "mataatlantica", "mata atlantica"],
    "Pampa": ["pampa"],
    "Pantanal": ["pantanal"],
}


def detectar_bioma(nome_layer):
    nl = nome_layer.lower()
    for bioma, tokens in BIOMA_TOKENS.items():
        for t in tokens:
            if t in nl:
                return bioma
    return "?"


def analisar_layer(caminho, nome_layer):
    t0 = time.time()
    print(f"  lendo camada {nome_layer!r} ...", flush=True)
    gdf = gpd.read_file(caminho, layer=nome_layer, engine="pyogrio")
    n = len(gdf)
    crs_original = gdf.crs
    geo_ea = gdf.geometry.to_crs("EPSG:6933")
    area_ha = geo_ea.area / 10000.0
    soma_ha = float(area_ha.sum())
    geom = gdf.geometry
    n_nulas = int(geom.isna().sum())
    n_vazias = int(geom[geom.notna()].is_empty.sum()) if n_nulas < len(geom) else 0
    n_geom_nula = n_nulas + n_vazias
    dt = time.time() - t0
    print(f"    {n:,} poligonos / {soma_ha:,.1f} ha  (CRS original: {crs_original}, {dt:.1f}s)")
    if n_geom_nula:
        print(f"    !! aviso: {n_geom_nula} geometrias nulas/vazias nessa camada")
    return n, soma_ha


def main():
    linhas = []
    for nome_arquivo in ARQUIVOS:
        caminho = os.path.join(PASTA, nome_arquivo)
        if not os.path.exists(caminho):
            print(f"!! arquivo nao encontrado, pulando: {caminho}")
            continue
        print(f"\n{'=' * 90}\n{nome_arquivo}\n{'=' * 90}")
        layers = list(pyogrio.list_layers(caminho)[:, 0])
        print(f"  camadas encontradas: {layers}")
        for nome_layer in layers:
            bioma = detectar_bioma(nome_layer)
            if bioma == "?":
                print(f"    !! aviso: nao consegui identificar o bioma pelo nome da camada {nome_layer!r}")
            n, soma_ha = analisar_layer(caminho, nome_layer)
            linhas.append(
                {
                    "arquivo": nome_arquivo,
                    "layer": nome_layer,
                    "bioma_detectado": bioma,
                    "n_poligonos": n,
                    "area_ha": soma_ha,
                }
            )

    resultado = pd.DataFrame(linhas)
    resultado.to_csv(SAIDA_CSV, index=False, encoding="utf-8-sig")
    print(f"\nGravado: {SAIDA_CSV} ({len(resultado)} linhas)")
    print("\nResumo:")
    print(resultado.to_string(index=False))


if __name__ == "__main__":
    main()
