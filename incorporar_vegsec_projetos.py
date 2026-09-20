# -*- coding: utf-8 -*-
"""
incorporar_vegsec_projetos.py
=============================
Cria, para os projetos de restauracao do IBAMA (Recooperar) e do ICMBio, um NOVO arquivo
GeoPackage em que cada poligono ORIGINAL (inteiro, sem recorte) recebe como atributos a
vegetacao secundaria (VS) que existe dentro dele, nas duas versoes do computo:

    vs22q    = VS 2022 qualificada
    vs2224q  = VS 2022-2024 qualificada

Os poligonos continuam sendo os originais: o computo pode usar a area total do projeto e,
ao mesmo tempo, qualquer analise pode saber quais projetos tem VS e quanta.

Fonte da VS: os cruzamentos espaciais JA executados (pedacos VS x projeto) em
    Cruzamento_Espacial_Vegetacao_Secundaria\\VS-Projetos_Restauracao\\
        <projeto>_x_VegSec_2022_qualificada.gpkg
        <projeto>_x_VegSec_2022-2024_qualificada.gpkg
Nao le os arquivos grandes de VS.

Como os pedacos sao ligados ao poligono original
------------------------------------------------
Os cruzamentos nao trazem um identificador do poligono original, e os atributos dos
projetos nao sao unicos (ha registros com atributos identicos, e as camadas do GEF
Pampa quase nao tem atributos). Por isso a ligacao e GEOMETRICA: para cada poligono
original, intersecta-se com ele todos os pedacos da mesma camada e une-se o resultado
por bioma. Isso:
  * nao depende de chave de atributo;
  * nao conta em dobro quando ha poligonos originais duplicados/sobrepostos (cada um
    recebe a sua propria VS, sem somar pedacos de outro poligono);
  * mede a VS dentro do poligono como area geodesica (GRS80, igual ao resto do projeto).

Campos acrescentados a cada camada
----------------------------------
  arquivo_orig, camada_orig, fid_orig   rastreio do poligono original
  area_ha_geo                           area geodesica GRS80 do poligono inteiro (ha)
  geom_reparada                         1 se a geometria original era invalida e foi reparada
  Para cada versao (prefixo vs22q_ ou vs2224q_):
    <p>_tem         1 se ha VS dentro do poligono, 0 se nao
    <p>_area_ha     area de VS dentro do poligono (ha)
    <p>_pct         VS como % da area do poligono
    <p>_n_pol       n. de poligonos de VS que tocam o projeto
    <p>_ha_<bioma>  area de VS por bioma da VS (amazonia, caatinga, cerrado,
                    mata_atlantica, pampa, pantanal); a soma e <p>_area_ha

Saidas (PASTA_SAIDA)
--------------------
  <projeto>_com_VegSec.gpkg   (4 arquivos; mesmas camadas dos originais, EPSG:4674, MultiPolygon 2D)
  resumo_Projetos_com_VegSec.csv   (uma linha por camada, com conferencias)

Uso (ambiente conda 'geo'):
    conda activate geo
    python incorporar_vegsec_projetos.py
"""
import os
import re
import sqlite3
import sys
import time
import unicodedata
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyproj import CRS, Geod
from shapely.errors import GEOSException
from shapely.strtree import STRtree

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------------------
# CONFIGURACAO
# ----------------------------------------------------------------------------
RAIZ = os.environ.get(
    "PLANAVEG_RAIZ",
    r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE",
)
PASTA_CRUZ = os.path.join(RAIZ, "Cruzamento_Espacial_Vegetacao_Secundaria", "VS-Projetos_Restauracao")
PASTA_SAIDA = os.environ.get("PLANAVEG_SAIDA", os.path.join(RAIZ, "Projetos_com_VegSec"))

# chave -> arquivo original, base do nome dos cruzamentos, nome da saida
PROJETOS = {
    "IBAMA_Recooperar_2025": dict(
        arquivo=os.path.join(RAIZ, "IBAMA_Projetos_Recooperar_2025_com_area.gpkg"),
        base_cruz="IBAMA_Projetos_Recooperar_2025",
        saida="IBAMA_Projetos_Recooperar_2025_com_VegSec.gpkg",
    ),
    "IBAMA_Recooperar_2026": dict(
        arquivo=os.path.join(RAIZ, "IBAMA_Projetos_Recooperar_2026_com_area.gpkg"),
        base_cruz="IBAMA_Projetos_Recooperar_2026",
        saida="IBAMA_Projetos_Recooperar_2026_com_VegSec.gpkg",
    ),
    "ICMBio_GEF_Terrestre_2026": dict(
        arquivo=os.path.join(RAIZ, "ICMBio_Projetos_GEF_Terrestre_2026_com_area.gpkg"),
        base_cruz="ICMBio_Projetos_GEF_Terrestre_2026",
        saida="ICMBio_Projetos_GEF_Terrestre_2026_com_VegSec.gpkg",
    ),
    "ICMBio_Restauracao_2026": dict(
        arquivo=os.path.join(RAIZ, "ICMBio_Projetos_Restauracao_2026_com_area.gpkg"),
        base_cruz="ICMBio_Projetos_Restauracao_2026",
        saida="ICMBio_Projetos_Restauracao_2026_com_VegSec.gpkg",
    ),
}
SOMENTE_ESTES = []   # ex.: ["IBAMA_Recooperar_2026"]; vazio = todos

# prefixo dos campos -> rotulo da versao no nome dos arquivos de cruzamento
VERSOES = {
    "vs22q": "2022_qualificada",
    "vs2224q": "2022-2024_qualificada",
}
BIOMAS = ["Amazonia", "Caatinga", "Cerrado", "Mata_Atlantica", "Pampa", "Pantanal"]
MIN_INTER_HA = 1e-6          # intersecoes menores que isto (0,01 m2) sao tratadas como simples toque
LOTE_INTERSECAO = 4000
CRS_ALVO = "EPSG:4674"
GEOD = Geod(ellps="GRS80")
USADAS = {}          # arquivo de cruzamento -> camadas usadas (conferencia final)


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


# ----------------------------------------------------------------------------
# GEOMETRIA
# ----------------------------------------------------------------------------
def area_ha(geoms):
    out = np.zeros(len(geoms), dtype="float64")
    for i, g in enumerate(geoms):
        if g is None or g.is_empty:
            continue
        try:
            out[i] = abs(GEOD.geometry_area_perimeter(g)[0]) / 10000.0
        except Exception:
            out[i] = np.nan
    return out


def so_poligonos(g):
    if g is None or g.is_empty:
        return None
    t = g.geom_type
    if t in ("Polygon", "MultiPolygon"):
        return g
    if t == "GeometryCollection":
        partes = []
        for p in g.geoms:
            q = so_poligonos(p)
            if q is not None:
                partes.extend(list(q.geoms) if q.geom_type == "MultiPolygon" else [q])
        if not partes:
            return None
        return partes[0] if len(partes) == 1 else shapely.MultiPolygon(partes)
    return None


def reparar(geoms):
    """Devolve (geometrias reparadas, vetor booleano 'era invalida')."""
    arr = np.array(geoms, dtype=object)
    validas = np.array([g is not None and not g.is_empty for g in arr])
    invalidas = np.zeros(len(arr), dtype=bool)
    if validas.any():
        invalidas[validas] = ~shapely.is_valid(arr[validas])
    for i in np.where(invalidas)[0]:
        try:
            arr[i] = shapely.make_valid(arr[i])
        except GEOSException:
            arr[i] = arr[i].buffer(0)
    for i in np.where(validas)[0]:
        if arr[i].geom_type not in ("Polygon", "MultiPolygon"):
            arr[i] = so_poligonos(arr[i])
    return arr, invalidas


def para_multi(g):
    if g is None or g.is_empty:
        return g
    if g.geom_type == "Polygon":
        return shapely.MultiPolygon([g])
    return g


def _inter_um(a, b):
    tentativas = (
        lambda: shapely.intersection(a, b),
        lambda: shapely.intersection(shapely.make_valid(a), shapely.make_valid(b)),
        lambda: shapely.intersection(a, b, grid_size=1e-9),
        lambda: shapely.intersection(a, b, grid_size=1e-7),
        lambda: shapely.intersection(a, b, grid_size=1e-5),
    )
    for t in tentativas:
        try:
            return t()
        except GEOSException:
            continue
    return None


def intersecao_robusta(a, b):
    out = np.empty(len(a), dtype=object)
    for i in range(0, len(a), LOTE_INTERSECAO):
        j = min(i + LOTE_INTERSECAO, len(a))
        try:
            out[i:j] = shapely.intersection(a[i:j], b[i:j])
        except GEOSException:
            for k in range(i, j):
                out[k] = _inter_um(a[k], b[k])
    return out


# ----------------------------------------------------------------------------
# LEITURA
# ----------------------------------------------------------------------------
def crs_da_camada(arquivo, camada):
    """CRS declarado no proprio GeoPackage (resolve o srs_id customizado 300001 do GDAL)."""
    con = sqlite3.connect(arquivo)
    try:
        r = con.execute(
            "select s.definition, s.organization, s.organization_coordsys_id "
            "from gpkg_geometry_columns c join gpkg_spatial_ref_sys s on s.srs_id = c.srs_id "
            "where c.table_name = ?", (camada,)).fetchone()
    finally:
        con.close()
    if r is None:
        raise RuntimeError(f"sem CRS registrado para a camada {camada}")
    definicao, org, cod = r
    if org and org.upper() == "EPSG" and cod and cod > 0:
        return CRS.from_epsg(int(cod))
    return CRS.from_wkt(definicao)


def _norm(s):
    """Compara nomes ignorando qualquer caractere nao alfanumerico ASCII.
    (o script de cruzamento troca cada letra acentuada por '_', ex.: Reparação -> Repara__o)"""
    return re.sub(r"[^0-9A-Za-z]+", "", s).lower()


def camada_do_cruzamento(arquivo, nome_orig):
    """Encontra no arquivo de cruzamento a camada '<nome>_vegsec' (o nome pode ter sido saneado)."""
    if not os.path.exists(arquivo):
        raise FileNotFoundError(arquivo)
    nomes = [n for n, _ in pyogrio.list_layers(arquivo)]
    if nome_orig + "_vegsec" in nomes:
        return nome_orig + "_vegsec"
    alvo = _norm(nome_orig + "_vegsec")
    achadas = [n for n in nomes if _norm(n) == alvo]
    if len(achadas) > 1:
        raise RuntimeError(f"mais de uma camada de cruzamento corresponde a {nome_orig}: {achadas}")
    return achadas[0] if achadas else None


def ler_original(arquivo, camada):
    g = pyogrio.read_dataframe(arquivo, layer=camada, fid_as_index=True)
    crs = crs_da_camada(arquivo, camada)
    g = g.set_crs(crs, allow_override=True)
    g["geometry"] = shapely.force_2d(g.geometry.values)
    g = g.to_crs(CRS_ALVO)
    return g


def ler_pedacos(arquivo, camada):
    c = pyogrio.read_dataframe(arquivo, layer=camada, columns=["vs_id", "vs_bioma", "vs_fonte", "area_ha"])
    if c.crs is None or c.crs.to_epsg() != 4674:
        c = c.to_crs(CRS_ALVO)
    c["geometry"] = shapely.force_2d(c.geometry.values)
    return c


# ----------------------------------------------------------------------------
# VS DENTRO DE CADA POLIGONO ORIGINAL
# ----------------------------------------------------------------------------
def vs_por_poligono(geoms, pedacos):
    """
    geoms   : array de geometrias originais (validas, EPSG:4674)
    pedacos : GeoDataFrame do cruzamento (pedacos VS x projeto, EPSG:4674)
    Devolve DataFrame (indice posicional 0..n-1) com colunas: n_pol e ha_<bioma>; e um dicionario de conferencias.
    """
    n = len(geoms)
    res = {b: np.zeros(n) for b in BIOMAS}
    npol = np.zeros(n, dtype="int64")
    conf = dict(pedacos=len(pedacos), pedacos_sem_poligono=0, soma_pedacos_ha=float(pedacos["area_ha"].sum()) if len(pedacos) else 0.0)
    if len(pedacos) == 0:
        return res, npol, conf

    pg = pedacos.geometry.values
    tree = STRtree(pg)
    ok = np.array([g is not None and not g.is_empty for g in geoms])
    idx_ok = np.where(ok)[0]
    ii, jj = tree.query(geoms[idx_ok], predicate="intersects")
    ii = idx_ok[ii]
    conf["pedacos_sem_poligono"] = int(len(pedacos) - len(np.unique(jj)))

    inter = intersecao_robusta(geoms[ii], pg[jj])
    inter = np.array([so_poligonos(g) for g in inter], dtype=object)
    manter = np.array([g is not None for g in inter])
    ii, jj, inter = ii[manter], jj[manter], inter[manter]
    a = area_ha(inter)
    manter = a > MIN_INTER_HA
    ii, jj, inter = ii[manter], jj[manter], inter[manter]

    bio = pedacos["vs_bioma"].astype(str).values[jj]
    fonte = pedacos["vs_fonte"].astype(str).values[jj]
    vid = pedacos["vs_id"].astype(str).values[jj]
    df = pd.DataFrame({"i": ii, "bio": bio, "fonte": fonte, "vid": vid})
    df["g"] = inter
    for (i, b), sub in df.groupby(["i", "bio"], sort=False):
        if b not in res:
            raise RuntimeError(f"bioma inesperado no cruzamento: {b}")
        u = shapely.union_all(sub["g"].values) if len(sub) > 1 else sub["g"].values[0]
        res[b][i] = area_ha([u])[0]
    npol_s = df.drop_duplicates(["i", "fonte", "bio", "vid"]).groupby("i").size()
    npol[npol_s.index.values] = npol_s.values
    return res, npol, conf


def processar_camada(arquivo, camada, cfg):
    g = ler_original(arquivo, camada)
    n = len(g)
    geoms, invalidas = reparar(g.geometry.values)
    sem_geom = np.array([x is None or x.is_empty for x in geoms])
    area_geo = area_ha(geoms)

    # atributos originais + rastreio
    saida = g.drop(columns="geometry").copy()
    ja = {c.lower() for c in saida.columns}
    novos = ["arquivo_orig", "camada_orig", "fid_orig", "area_ha_geo", "geom_reparada"]
    for p in VERSOES:
        novos += [f"{p}_tem", f"{p}_area_ha", f"{p}_pct", f"{p}_n_pol"] + [f"{p}_ha_{b.lower()}" for b in BIOMAS]
    colisao = [c for c in novos if c.lower() in ja]
    if colisao:
        raise RuntimeError(f"campos novos colidem com campos originais em {camada}: {colisao}")
    saida["arquivo_orig"] = os.path.basename(arquivo)
    saida["camada_orig"] = camada
    saida["fid_orig"] = g.index.values.astype("int64")
    saida["area_ha_geo"] = np.round(area_geo, 6)
    saida["geom_reparada"] = invalidas.astype("int64")

    resumo = dict(camada=camada, n_poligonos=n, sem_geometria=int(sem_geom.sum()), reparadas=int(invalidas.sum()),
                  area_poligonos_ha=float(np.nansum(area_geo)))
    for p, rot in VERSOES.items():
        arq_x = os.path.join(PASTA_CRUZ, f"{cfg['base_cruz']}_x_VegSec_{rot}.gpkg")
        nome_x = camada_do_cruzamento(arq_x, camada)
        if nome_x is not None:
            USADAS.setdefault(arq_x, set()).add(nome_x)
        if nome_x is None:
            log(f"    [{p}] camada sem cruzamento (nenhum pedaco de VS): VS = 0")
            pedacos = gpd.GeoDataFrame({"vs_id": [], "vs_bioma": [], "vs_fonte": [], "area_ha": []}, geometry=[], crs=CRS_ALVO)
            resumo[f"{p}_cruzamento"] = "sem camada"
        else:
            pedacos = ler_pedacos(arq_x, nome_x)
            resumo[f"{p}_cruzamento"] = "ok"
        ha, npol, conf = vs_por_poligono(geoms, pedacos)
        total = np.sum([ha[b] for b in BIOMAS], axis=0)
        total = np.where(np.isnan(area_geo), 0.0, np.minimum(total, area_geo * (1 + 1e-9)))
        pct = np.where(area_geo > 0, total / np.where(area_geo > 0, area_geo, 1) * 100.0, 0.0)
        saida[f"{p}_tem"] = (total > 0).astype("int64")
        saida[f"{p}_area_ha"] = np.round(total, 6)
        saida[f"{p}_pct"] = np.round(pct, 4)
        saida[f"{p}_n_pol"] = npol
        for b in BIOMAS:
            saida[f"{p}_ha_{b.lower()}"] = np.round(ha[b], 6)
        resumo[f"{p}_n_com_vs"] = int((total > 0).sum())
        resumo[f"{p}_area_vs_ha"] = float(total.sum())
        resumo[f"{p}_soma_pedacos_cruzamento_ha"] = conf["soma_pedacos_ha"]
        resumo[f"{p}_pedacos"] = conf["pedacos"]
        resumo[f"{p}_pedacos_sem_poligono"] = conf["pedacos_sem_poligono"]
        log(f"    [{p}] {int((total > 0).sum()):,} de {n:,} poligonos com VS; VS = {total.sum():,.1f} ha "
            f"(pedacos do cruzamento: {conf['pedacos']:,}, soma {conf['soma_pedacos_ha']:,.1f} ha; "
            f"pedacos sem poligono: {conf['pedacos_sem_poligono']})")
        if conf["pedacos_sem_poligono"]:
            log("    ATENCAO: ha pedacos de VS que nao caem em nenhum poligono original (CRS ou versao do arquivo?)")

    geoms_out = np.array([para_multi(x) for x in geoms], dtype=object)
    gout = gpd.GeoDataFrame(saida, geometry=geoms_out, crs=CRS_ALVO)
    return gout, resumo


def main():
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    linhas = []
    for chave, cfg in PROJETOS.items():
        if SOMENTE_ESTES and chave not in SOMENTE_ESTES:
            continue
        log(f"[{chave}] {cfg['arquivo']}")
        destino = os.path.join(PASTA_SAIDA, cfg["saida"])
        parcial = destino + ".partial.gpkg"
        if os.path.exists(parcial):
            os.remove(parcial)
        t0 = time.time()
        for camada in pyogrio.list_layers(cfg["arquivo"])[:, 0]:
            log(f"  camada {camada}")
            gout, resumo = processar_camada(cfg["arquivo"], camada, cfg)
            pyogrio.write_dataframe(gout, parcial, layer=camada, driver="GPKG",
                                    geometry_type="MultiPolygon", promote_to_multi=True)
            resumo["arquivo"] = cfg["saida"]
            linhas.append(resumo)
        # conferencia: toda camada dos cruzamentos deve ter sido ligada a uma camada original
        for p, rot in VERSOES.items():
            arq_x = os.path.join(PASTA_CRUZ, f"{cfg['base_cruz']}_x_VegSec_{rot}.gpkg")
            todas = {n for n, _ in pyogrio.list_layers(arq_x)}
            sobra = todas - USADAS.get(arq_x, set())
            if sobra:
                log(f"  ATENCAO: camadas do cruzamento {os.path.basename(arq_x)} sem camada original correspondente: {sorted(sobra)}")
        os.replace(parcial, destino)
        log(f"[{chave}] concluido em {(time.time() - t0) / 60:.1f} min -> {destino}")

    if linhas:
        df = pd.DataFrame(linhas)
        primeiras = ["arquivo", "camada", "n_poligonos", "sem_geometria", "reparadas", "area_poligonos_ha"]
        df = df[primeiras + [c for c in df.columns if c not in primeiras]]
        csv = os.path.join(PASTA_SAIDA, "resumo_Projetos_com_VegSec.csv")
        df.to_csv(csv, index=False, encoding="utf-8-sig")
        log(f"resumo: {csv}")
        cols = ["camada", "n_poligonos", "vs22q_n_com_vs", "vs22q_area_vs_ha", "vs2224q_n_com_vs", "vs2224q_area_vs_ha"]
        print(df[cols].to_string(index=False))
    log("fim")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("interrompido pelo usuario")
        sys.exit(1)
