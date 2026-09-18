# -*- coding: utf-8 -*-
"""
Agrega area_ha/n_poligonos por bioma do GeoPackage de Unidades de Conservacao
(cruzamento com VegSec 2022-2024 qualificada) -- e grande demais (~598 MB) para
eu acessar diretamente daqui.

Le SO os atributos (sem geometria, via pyogrio read_geometry=False), entao e
rapido mesmo nesse arquivo grande. Gera um CSV pequeno que pode ser
compartilhado de volta para eu terminar a planilha consolidada.

Uso:
    conda activate geo
    python agregar_areas_uc_2022_2024_qualificado.py
"""

import os

import pandas as pd
import pyogrio

PASTA = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\Cruzamento_Projetos_VegSec"
ARQUIVO = "Unidades_Conservacao_CNUC20260507_x_VegSec_2022-2024_qualificado.gpkg"
SAIDA_CSV = os.path.join(PASTA, "resumo_areas_UC_2022_2024_qualificado.csv")


def main():
    caminho = os.path.join(PASTA, ARQUIVO)
    arquivo_base = ARQUIVO.replace("_x_VegSec_2022-2024_qualificado.gpkg", "")
    layers = list(pyogrio.list_layers(caminho)[:, 0])
    linhas = []
    for camada in layers:
        info = pyogrio.read_info(caminho, layer=camada)
        fields = list(info["fields"])
        cols = [c for c in ["vs_bioma", "area_ha"] if c in fields]
        print(f"lendo {ARQUIVO} / {camada} (so atributos, sem geometria)...")
        df = pyogrio.read_dataframe(caminho, layer=camada, columns=cols, read_geometry=False)
        agr = (
            df.groupby("vs_bioma", dropna=False)
            .agg(n_poligonos=("area_ha", "size"), area_ha=("area_ha", "sum"))
            .reset_index()
        )
        agr["arquivo"] = arquivo_base
        agr["camada"] = camada
        linhas.append(agr)
        print(f"    {len(df)} feicoes lidas / {agr['area_ha'].sum():,.1f} ha")

    resultado = pd.concat(linhas, ignore_index=True)
    resultado = resultado.rename(columns={"vs_bioma": "bioma"})
    resultado = resultado[["arquivo", "camada", "bioma", "n_poligonos", "area_ha"]]
    resultado.to_csv(SAIDA_CSV, index=False, encoding="utf-8-sig")
    print(f"\nGravado: {SAIDA_CSV} ({len(resultado)} linhas)")
    print(resultado.to_string(index=False))


if __name__ == "__main__":
    main()
