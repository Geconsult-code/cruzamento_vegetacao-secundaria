# -*- coding: utf-8 -*-
"""
Analisa e compara os arquivos de vegetacao secundaria 2024 (INPE/TerraClass) na
pasta INPE_Vegetacao_Secundaria: area (ha) e numero de poligonos, BRUTO x
QUALIFICADO (filtro de area minima > 2 ha), para Amazonia e Cerrado.

Continuacao de analisar_vegsec_qualificada.py (que ja cobriu a comparacao 2022
bruto x qualificado) -- agora que o arquivo qualificado de Amazonia 2024 chegou,
falta so comparar os 4 arquivos de 2024:
  - VS_2024_Terraclass_Vegetacao_Secundaria_Bioma_Amazonia.gpkg              (BRUTO)
  - VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Amazonia.gpkg  (QUALIFICADO, novo)
  - VS_2024_Terraclass_Vegetacao_Secundaria_Bioma_Cerrado.gpkg               (BRUTO)
  - VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Cerrado.gpkg   (QUALIFICADO, ja
    analisado antes -- incluido de novo aqui so para conferencia/registro junto dos outros 3)

Area calculada via reprojecao para CRS de area equivalente (EPSG:6933) -- mesmo
metodo ja validado no projeto (equivalente a area geodesica GRS80, diferenca
~0,003%), MUITO mais rapido que geodesico poligono a poligono com milhoes de
feicoes (soma vetorizada, nao um loop).

Nomes de camada resolvidos automaticamente (pyogrio.list_layers) -- nao
confirmados de antemao, principalmente para o arquivo novo (qualificada
Amazonia).

Gera um CSV: resumo_vegsec_2024_qualificada.csv (arquivo, layer, bioma, versao,
n_poligonos, area_ha)

Uso:
    conda activate geo
    python analisar_vegsec_2024_qualificada.py

Pode demorar alguns minutos no arquivo bruto de Amazonia (~1,1 milhao de
poligonos) -- ha print de progresso por camada.
"""

import os
import time

import geopandas as gpd
import pandas as pd
import pyogrio

PASTA = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\INPE_Vegetacao_Secundaria"
SAIDA_CSV = os.path.join(PASTA, "resumo_vegsec_2024_qualificada.csv")

# (nome_arquivo, bioma, versao)
ARQUIVOS = [
    ("VS_2024_Terraclass_Vegetacao_Secundaria_Bioma_Amazonia.gpkg", "Amazonia", "bruto"),
    ("VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Amazonia.gpkg", "Amazonia", "qualificado"),
    ("VS_2024_Terraclass_Vegetacao_Secundaria_Bioma_Cerrado.gpkg", "Cerrado", "bruto"),
    ("VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Cerrado.gpkg", "Cerrado", "qualificado"),
]


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
    for nome_arquivo, bioma, versao in ARQUIVOS:
        caminho = os.path.join(PASTA, nome_arquivo)
        if not os.path.exists(caminho):
            print(f"!! arquivo nao encontrado, pulando: {caminho}")
            continue
        print(f"\n{'=' * 90}\n{nome_arquivo}  ({bioma}, {versao})\n{'=' * 90}")
        layers = list(pyogrio.list_layers(caminho)[:, 0])
        print(f"  camadas encontradas: {layers}")
        if len(layers) != 1:
            print(f"  !! aviso: {len(layers)} camadas -- usando a primeira ({layers[0]!r}), revisar manualmente")
        nome_layer = layers[0]
        n, soma_ha = analisar_layer(caminho, nome_layer)
        linhas.append(
            {
                "arquivo": nome_arquivo,
                "layer": nome_layer,
                "bioma": bioma,
                "versao": versao,
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
