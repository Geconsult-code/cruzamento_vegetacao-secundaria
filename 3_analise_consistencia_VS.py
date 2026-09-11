# -*- coding: utf-8 -*-
r"""
3_analise_consistencia_VS.py — passo 3 do workflow numerado do repositório
cruzamento_vegetacao-secundaria.

(Renomeado/adaptado de relatorio_vegsec_selecionados.py — agora audita os
três arquivos de saída do passo 2, um por categoria de imóvel, em vez de
só os dois anteriores.)

Gera o relatorio de consistencia (JSON, mesmo nome do geopackage) dos tres
arquivos de saida do cruzamento vegetacao secundaria x APP/RL/AUR
"Selecionados":
  Vegetacao_Secundaria\VS_2022_Imoveis_Selecionados_Habilitados.gpkg
  Vegetacao_Secundaria\VS_2022_Imoveis_Selecionados_Analisados.gpkg
  Vegetacao_Secundaria\VS_2022_Imoveis_Selecionados_Nao_Analisados.gpkg

Para cada layer (VS_APP/RL/AUR_<categoria>): projecao (epsg), numero total de
poligonos, numero de geometrias invalidas/sem geometria, area total (ha) e
area por UF e por bioma - usando a coluna area_ha ja calculada (geodesica,
GRS80) na hora do cruzamento, agregada via SQL direto no sqlite do
geopackage (rapido, sem reabrir/reprojetar geometria).

A contagem de invalidas e feita separadamente (via OGR IsValid(), em blocos
de FID) porque isso exige ler a geometria. Resiliente/retomavel via
_progresso_relatorio_vegsec.json.

Os JSONs gerados aqui alimentam diretamente o passo 4
(4_validacao_resultados_VS.py), que le a lista de camadas com geometrias
invalidas e as corrige.
"""

import os
import json
import sqlite3
import datetime
import time

from osgeo import ogr, gdal

gdal.UseExceptions()

BASE_ROOT = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR"
VEG_DIR = os.path.join(BASE_ROOT, "Vegetacao_Secundaria")

ARQUIVOS = {
    "Habilitados": os.path.join(VEG_DIR, "VS_2022_Imoveis_Selecionados_Habilitados.gpkg"),
    "Analisados": os.path.join(VEG_DIR, "VS_2022_Imoveis_Selecionados_Analisados.gpkg"),
    "Nao_Analisados": os.path.join(VEG_DIR, "VS_2022_Imoveis_Selecionados_Nao_Analisados.gpkg"),
}

PROGRESSO = os.path.join(VEG_DIR, "_progresso_relatorio_vegsec.json")

CHUNK = 100000
BUDGET_S = 35


def epsg_da_layer(path, layer):
    ds = ogr.Open(path)
    lyr = ds.GetLayerByName(layer)
    srs = lyr.GetSpatialRef()
    epsg = srs.GetAuthorityCode(None) if srs else None
    ds = None
    return int(epsg) if epsg else None


def total_feicoes(path, layer):
    ds = ogr.Open(path)
    lyr = ds.GetLayerByName(layer)
    n = lyr.GetFeatureCount()
    ds = None
    return n


def agregados_sql(path, layer):
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute(f'SELECT COUNT(*), COALESCE(SUM(area_ha),0) FROM "{layer}"')
    total_n, total_area = cur.fetchone()
    cur.execute(f'SELECT uf, COUNT(*), COALESCE(SUM(area_ha),0) FROM "{layer}" GROUP BY uf ORDER BY uf')
    por_uf = {row[0]: {"num_poligonos": row[1], "area_ha": round(row[2], 4)} for row in cur.fetchall()}
    cur.execute(f'SELECT bioma, COUNT(*), COALESCE(SUM(area_ha),0) FROM "{layer}" GROUP BY bioma ORDER BY bioma')
    por_bioma = {row[0]: {"num_poligonos": row[1], "area_ha": round(row[2], 4)} for row in cur.fetchall()}
    con.close()
    return total_n, round(total_area, 4), por_uf, por_bioma


def carregar_progresso():
    if os.path.isfile(PROGRESSO):
        with open(PROGRESSO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_progresso(prog):
    with open(PROGRESSO, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=1)


def contar_invalidas_chunk(path, layer, fidcol, lo, hi):
    ds = ogr.Open(path)
    lyr = ds.GetLayerByName(layer)
    lyr.SetAttributeFilter(f'"{fidcol}" >= {lo} AND "{fidcol}" < {hi}')
    lyr.ResetReading()
    n_scan = n_inval = n_sem_geom = 0
    feat = lyr.GetNextFeature()
    while feat is not None:
        n_scan += 1
        geom = feat.GetGeometryRef()
        if geom is None:
            n_sem_geom += 1
        elif not geom.IsValid():
            n_inval += 1
        feat = lyr.GetNextFeature()
    lyr.SetAttributeFilter(None)
    ds = None
    return n_scan, n_inval, n_sem_geom


def rodada_invalidas():
    """Conta invalidas/sem-geometria de todas as layers, em blocos de FID,
    retomavel. Retorna True quando todas as layers estiverem completas."""
    t0 = time.time()
    prog = carregar_progresso()

    itens = []
    for categoria, path in ARQUIVOS.items():
        if not os.path.exists(path):
            continue
        ds = ogr.Open(path)
        for i in range(ds.GetLayerCount()):
            lyr = ds.GetLayerByIndex(i)
            layer = lyr.GetName()
            fidcol = lyr.GetFIDColumn() or "fid"
            total = lyr.GetFeatureCount()
            itens.append({"categoria": categoria, "path": path, "layer": layer,
                          "fidcol": fidcol, "total": total})
        ds = None

    log = []
    for item in itens:
        chave = f"{item['categoria']}::{item['layer']}"
        st = prog.get(chave, {"fid_proximo": 0, "concluido": False,
                              "n_invalidas": 0, "n_sem_geometria": 0, "n_escaneadas": 0})
        if st["concluido"]:
            continue
        total = item["total"]
        while st["fid_proximo"] <= total:
            if time.time() - t0 > BUDGET_S:
                prog[chave] = st
                salvar_progresso(prog)
                log.append(f"[PAUSA] {chave} em fid={st['fid_proximo']}/{total}")
                print("\n".join(log))
                print(f"\nRODADA parcial: {round(time.time()-t0,1)}s.")
                return False
            lo = st["fid_proximo"]
            hi = lo + CHUNK
            n_scan, n_inval, n_sem = contar_invalidas_chunk(item["path"], item["layer"], item["fidcol"], lo, hi)
            st["fid_proximo"] = hi
            st["n_invalidas"] += n_inval
            st["n_sem_geometria"] += n_sem
            st["n_escaneadas"] += n_scan
            if total == 0:
                break
        st["concluido"] = True
        prog[chave] = st
        salvar_progresso(prog)
        log.append(f"[OK] {chave}: escaneadas={st['n_escaneadas']} invalidas={st['n_invalidas']} sem_geom={st['n_sem_geometria']}")

    salvar_progresso(prog)
    print("\n".join(log))
    print(f"\nRODADA completa. Tempo: {round(time.time()-t0,1)}s")
    return True


def gerar_jsons():
    prog = carregar_progresso()
    for categoria, path in ARQUIVOS.items():
        if not os.path.exists(path):
            print(f"[{categoria}] {path} nao existe, pulando")
            continue
        layers_out = {}
        ds = ogr.Open(path)
        nomes = [ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())]
        ds = None
        for layer in nomes:
            epsg = epsg_da_layer(path, layer)
            total_n, total_area, por_uf, por_bioma = agregados_sql(path, layer)
            chave = f"{categoria}::{layer}"
            st = prog.get(chave, {})
            layers_out[layer] = {
                "epsg": epsg,
                "num_poligonos": total_n,
                "num_geometrias_invalidas": st.get("n_invalidas"),
                "num_sem_geometria": st.get("n_sem_geometria"),
                "area_total_ha": total_area,
                "area_por_uf": por_uf,
                "area_por_bioma": por_bioma,
            }
        saida = {
            "arquivo_geopackage": os.path.basename(path),
            "gerado_em": datetime.datetime.now().isoformat(timespec="seconds"),
            "layers": layers_out,
        }
        out_path = os.path.splitext(path)[0] + ".json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(saida, f, ensure_ascii=False, indent=1)
        print("gravado:", out_path)


if __name__ == "__main__":
    while not rodada_invalidas():
        pass
    gerar_jsons()
    print("\nRELATORIO COMPLETO.")
