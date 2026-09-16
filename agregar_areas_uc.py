# -*- coding: utf-8 -*-
"""
Agrega area_ha por bioma/fonte dos 2 GeoPackages de Unidades de Conservacao
(sao grandes demais - ~680/694 MB - para eu acessar diretamente daqui).

Le SO os atributos (sem geometria, via pyogrio read_geometry=False), entao e
rapido mesmo nesses arquivos grandes. Gera um CSV pequeno que pode ser
compartilhado de volta para eu terminar a planilha consolidada.

Uso:
    conda activate geo
    python agregar_areas_uc.py
"""

import os

import pandas as pd
import pyogrio

PASTA = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\Cruzamento_Projetos_VegSec"
ARQUIVOS = [
    "Unidades_Conservacao_CNUC20260507_x_VegSec_2022.gpkg",
    "Unidades_Conservacao_CNUC20260507_x_VegSec_2022-2024.gpkg",
]
SAIDA_CSV = os.path.join(PASTA, "resumo_areas_UC.csv")

BIOMAS_VALIDOS = {"Amazonia", "Cerrado", "Caatinga", "Mata_Atlantica", "Pampa", "Pantanal"}


def col_bioma(fields):
    for c in ("vs_bioma_2", "vs_bioma"):
        if c in fields:
            return c
    return None


def col_fonte(fields):
    for c in ("vs_fonte_2", "vs_fonte"):
        if c in fields:
            return c
    return None


def main():
    linhas = []
    for nome_arquivo in ARQUIVOS:
        caminho = os.path.join(PASTA, nome_arquivo)
        versao = "2022-2024" if "2022-2024" in nome_arquivo else "2022"
        arquivo_base = nome_arquivo.replace("_x_VegSec_2022-2024.gpkg", "").replace(
            "_x_VegSec_2022.gpkg", ""
        )
        layers = list(pyogrio.list_layers(caminho)[:, 0])
        for camada in layers:
            info = pyogrio.read_info(caminho, layer=camada)
            fields = list(info["fields"])
            cb = col_bioma(fields)
            cf = col_fonte(fields)
            cols = [c for c in [cb, cf, "area_ha"] if c]
            print(f"lendo {nome_arquivo} / {camada} (so atributos, sem geometria)...")
            df = pyogrio.read_dataframe(caminho, layer=camada, columns=cols, read_geometry=False)
            if cb is None:
                df["_bioma"] = "?"
            else:
                df = df.rename(columns={cb: "_bioma"})
            if cf is None:
                df["_fonte"] = versao
            else:
                df = df.rename(columns={cf: "_fonte"})
            invalidos = set(df["_bioma"].unique()) - BIOMAS_VALIDOS
            if invalidos:
                print(f"    !! AVISO valores de bioma inesperados: {invalidos}")
            agr = (
                df.groupby(["_bioma", "_fonte"], dropna=False)
                .agg(n_poligonos=("area_ha", "size"), area_ha=("area_ha", "sum"))
                .reset_index()
            )
            agr["arquivo"] = arquivo_base
            agr["camada"] = camada
            agr["versao"] = versao
            linhas.append(agr)
            print(f"    {len(df)} feicoes lidas")

    resultado = pd.concat(linhas, ignore_index=True)
    resultado = resultado.rename(columns={"_bioma": "bioma", "_fonte": "fonte_vegsec"})
    resultado = resultado[["versao", "arquivo", "camada", "bioma", "fonte_vegsec", "n_poligonos", "area_ha"]]
    resultado.to_csv(SAIDA_CSV, index=False, encoding="utf-8-sig")
    print(f"\nGravado: {SAIDA_CSV} ({len(resultado)} linhas)")


if __name__ == "__main__":
    main()
