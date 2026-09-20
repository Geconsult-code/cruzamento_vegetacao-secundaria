# -*- coding: utf-8 -*-
"""
cruzar_embargos_vegsec.py
=========================
Cruzamento espacial entre a Vegetacao Secundaria (VS) QUALIFICADA (INPE) e as
Areas Embargadas do IBAMA (PANGIA) de 2025 e de 2026.

Uso no computo Planaveg 2026: os embargos PANGIA entram como "Outros projetos"
(camada 2, criterio Intencionalidade) somente pela parte que intersecta VS -- e
nao pela extensao total dos poligonos. Este script gera essas intersecoes.

ENTRADAS (pasta GEOPACKAGE)
  IBAMA_Areas_Embargadas_PANGIA20251023_Poligonos.gpkg   (poligonos limpos: sem pontos, sem registros sem geometria)
  IBAMA_Areas_Embargadas_PANGIA20260920_Poligonos.gpkg   (idem)
  Vegetacao_Secundaria_INPE\\
     VS_2022_TerraBrasilis_Vegetacao_Secundaria_Qualificada_Brasil.gpkg               (6 camadas, uma por bioma)
     VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Amazonia.gpkg
     VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Cerrado.gpkg

DUAS VERSOES DE VS (somente qualificadas; as versoes brutas nao sao usadas)
  2022_qualificada       : os 6 biomas da VS 2022 qualificada
  2022-2024_qualificada  : Caatinga, Mata Atlantica, Pampa e Pantanal da VS 2022 qualificada
                           + Amazonia e Cerrado da VS 2024 qualificada (substituem os de 2022)

SAIDAS (Cruzamento_Espacial_Vegetacao_Secundaria\\VS-Areas_Embargadas)
  IBAMA_Areas_Embargadas_PANGIA20251023_x_VegSec_2022_qualificada.gpkg
  IBAMA_Areas_Embargadas_PANGIA20251023_x_VegSec_2022-2024_qualificada.gpkg
  IBAMA_Areas_Embargadas_PANGIA20260920_x_VegSec_2022_qualificada.gpkg
  IBAMA_Areas_Embargadas_PANGIA20260920_x_VegSec_2022-2024_qualificada.gpkg
  resumo_Embargos_VegSec.csv   (n de pedacos e area por embargo x versao x fonte x bioma)
Cada GeoPackage tem UMA camada  <camada do embargo>_vegsec  com um registro por
PEDACO (embargo x poligono de VS), com:
  - todos os campos do embargo
  - idx_embargo             : posicao (0..n-1) do poligono no arquivo _Poligonos (rastreabilidade)
  - area_ha_projeto_orig    : area geodesica do poligono de embargo inteiro (ha)
  - vs_*                    : campos do poligono de VS (vs_ano, vs_area_ha, vs_id, vs_bioma, vs_fonte, ...)
  - area_ha                 : area geodesica do PEDACO (embargo intersecao VS), elipsoide GRS80 (ha)

ATENCAO: embargos podem se sobrepor entre si. A soma de area_ha conta duas vezes a
VS que cai em dois embargos sobrepostos; a area sem duplicacao (uniao) sai na etapa
de hierarquia do computo, nao aqui.

METODO (mesmo padrao dos outros cruzamentos do projeto)
  - Embargos processados em blocos espaciais (celulas de CELULA_GRAUS graus, pelo
    centroide); para cada bloco a VS e lida so na caixa do bloco (indice espacial do
    GeoPackage). Nunca dissolve nem le a VS inteira.
  - Reparo de geometria com make_valid antes de intersectar; intersecao em lote com
    fallback poligono a poligono (make_valid e grid_size 1e-9/1e-7/1e-5) para
    TopologyException do GEOS.
  - Area geodesica GRS80 (pyproj.Geod), igual ao resto do projeto.
  - Cada camada de VS e cruzada UMA vez por arquivo de embargo; as duas versoes sao
    montadas a partir dos mesmos pedacos.
  - Grava em arquivo .partial e so renomeia no fim: se interromper (Ctrl+C, queda de
    energia), o arquivo final nunca fica pela metade. Retoma por arquivo de embargo
    (pula os que ja tem _done_<nome>.txt).

USO (ambiente conda 'geo')
    conda activate geo
    python cruzar_embargos_vegsec.py

Para testar primeiro, ajuste SOMENTE_ESTES = ["PANGIA20260920"]; para refazer um
resultado, coloque a chave em REFAZER.
"""

import json
import os
import shutil
import sys
import time
import unicodedata
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyproj import Geod
from shapely.errors import GEOSException
from shapely.strtree import STRtree

warnings.filterwarnings("ignore", category=UserWarning)

# ----------------------------------------------------------------------------
# CONFIGURACAO
# ----------------------------------------------------------------------------
RAIZ = os.environ.get(
    "PLANAVEG_RAIZ",
    r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE",
)
PASTA_VS = os.path.join(RAIZ, "Vegetacao_Secundaria_INPE")
PASTA_SAIDA = os.path.join(RAIZ, "Cruzamento_Espacial_Vegetacao_Secundaria", "VS-Areas_Embargadas")

# chave -> (arquivo de entrada, camada de entrada, nome-base da saida)
EMBARGOS = {
    "PANGIA20251023": dict(
        arquivo=os.path.join(RAIZ, "IBAMA_Areas_Embargadas_PANGIA20251023_Poligonos.gpkg"),
        camada="IBAMA_Area_Embargada_PANGIA20251023",
        saida="IBAMA_Areas_Embargadas_PANGIA20251023",
    ),
    "PANGIA20260920": dict(
        arquivo=os.path.join(RAIZ, "IBAMA_Areas_Embargadas_PANGIA20260920_Poligonos.gpkg"),
        camada="IBAMA_Area_Embargada_PANGIA20260920",
        saida="IBAMA_Areas_Embargadas_PANGIA20260920",
    ),
}
SOMENTE_ESTES = []   # ex.: ["PANGIA20260920"]; vazio = todos
REFAZER = []         # chaves a refazer mesmo com _done_

VS_2022 = os.path.join(PASTA_VS, "VS_2022_TerraBrasilis_Vegetacao_Secundaria_Qualificada_Brasil.gpkg")
VS_2024_AM = os.path.join(PASTA_VS, "VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Amazonia.gpkg")
VS_2024_CE = os.path.join(PASTA_VS, "VS_2024_Terraclass_Vegetacao_Secundaria_Qualificada_Bioma_Cerrado.gpkg")

# id da fonte -> arquivo, camada, bioma, rotulo vs_fonte
FONTES = {
    "2022_Amazonia": (VS_2022, "Vegetacao_Secundaria_Amazonia_2022_qualificada", "Amazonia", "2022_qualificada"),
    "2022_Caatinga": (VS_2022, "Vegetacao_Secundaria_Caatinga_2022_qualificada", "Caatinga", "2022_qualificada"),
    "2022_Cerrado": (VS_2022, "Vegetacao_Secundaria_Cerrado_2022_qualificada", "Cerrado", "2022_qualificada"),
    "2022_Mata_Atlantica": (VS_2022, "Vegetacao_Secundaria_Mata_Atlantica_2022_qualificada", "Mata_Atlantica", "2022_qualificada"),
    "2022_Pampa": (VS_2022, "Vegetacao_Secundaria_Pampa_2022_qualificada", "Pampa", "2022_qualificada"),
    "2022_Pantanal": (VS_2022, "Vegetacao_Secundaria_Pantanal_2022_qualificada", "Pantanal", "2022_qualificada"),
    "2024_Amazonia": (VS_2024_AM, "VS_Amazônia_2024_2ha_2casas", "Amazonia", "2024_qualificada"),
    "2024_Cerrado": (VS_2024_CE, "vs_qualificacao_cerrado_2024_v01", "Cerrado", "2024_qualificada"),
}
VERSOES = {
    "2022_qualificada": ["2022_Amazonia", "2022_Caatinga", "2022_Cerrado", "2022_Mata_Atlantica", "2022_Pampa", "2022_Pantanal"],
    "2022-2024_qualificada": ["2024_Amazonia", "2024_Cerrado", "2022_Caatinga", "2022_Mata_Atlantica", "2022_Pampa", "2022_Pantanal"],
}

CELULA_GRAUS = 2.0            # tamanho da celula do bloco espacial (graus)
MAX_EMBARGOS_POR_BLOCO = 3000
LOTE_INTERSECAO = 4000        # pares por chamada vetorizada do GEOS
MIN_PEDACO_HA = 0.0           # descarta pedacos menores que isto (0 = mantem todos nao vazios)
CRS_ALVO = "EPSG:4674"

GEOD = Geod(ellps="GRS80")

# ordem canonica dos campos vs_ na saida (os demais campos encontrados entram depois)
ORDEM_VS = [
    "vs_ano", "vs_area_ha", "vs_id", "vs_bioma", "vs_fonte", "vs_idade_ponderada",
    "vs_area_core_ha_30", "vs_area_core_ha_60", "vs_area_core_ha_90", "vs_area_core_ha_120",
    "vs_d_fractal", "vs_distancia_m", "vs_veg_vizinha_tipo", "vs_terra_indigena", "vs_uc_integral",
    "vs_uc_sustentavel", "vs_quilombola", "vs_assentamento", "vs_apa", "vs_prop_privada_sigef",
    "vs_floresta_nao_destinada", "vs_sem_registro_fund",
]


# ----------------------------------------------------------------------------
# UTILITARIOS
# ----------------------------------------------------------------------------
def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def resolver_camada(arquivo, nome):
    """Devolve o nome real da camada (tolera acento/caixa). Erro claro se nao achar."""
    camadas = [str(x) for x in pyogrio.list_layers(arquivo)[:, 0]]
    if nome in camadas:
        return nome
    for c in camadas:
        if _norm(c) == _norm(nome):
            return c
    raise RuntimeError(f"camada {nome!r} nao encontrada em {arquivo}. Camadas: {camadas}")


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
    """Mantem apenas a parte poligonal de uma geometria (make_valid/intersecao podem devolver colecoes)."""
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
    arr = np.array(geoms, dtype=object)
    invalidas = ~shapely.is_valid(arr)
    if invalidas.any():
        try:
            arr[invalidas] = shapely.make_valid(arr[invalidas])
        except GEOSException:
            for i in np.where(invalidas)[0]:
                try:
                    arr[i] = shapely.make_valid(arr[i])
                except GEOSException:
                    arr[i] = arr[i].buffer(0)
    tipos = shapely.get_type_id(arr)
    for i in np.where((tipos != 3) & (tipos != 6))[0]:   # nao e Polygon/MultiPolygon
        arr[i] = so_poligonos(arr[i])
    return arr


FALHAS_INTERSECAO = {"n": 0}


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
    FALHAS_INTERSECAO["n"] += 1
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
# ESQUEMA DE SAIDA (fixo, para poder gravar em blocos com append)
# ----------------------------------------------------------------------------
def prefixar_colunas(cols):
    return ["vs_" + c.lower() for c in cols]


def montar_esquema_vs():
    """Uniao ordenada dos campos vs_ de todas as fontes + tipo de saida por campo."""
    tipos = {}
    presente_em = {}
    for fid, (arq, cam, bioma, fonte) in FONTES.items():
        info = pyogrio.read_info(arq, layer=resolver_camada(arq, cam))
        for c, dt in zip(prefixar_colunas(info["fields"]), info["dtypes"]):
            tipos.setdefault(c, set()).add("texto" if dt in ("object", "str", "string") or str(dt).startswith(("<U", "U", "S")) else ("inteiro" if "int" in str(dt) else "real"))
            presente_em.setdefault(c, set()).add(fid)
    for c in ("vs_bioma", "vs_fonte"):
        tipos[c] = {"texto"}
        presente_em[c] = set(FONTES)
    tipos["vs_ano"] = {"texto"}          # 2022 vem inteiro e 2024-Amazonia vem texto: padroniza como texto
    esquema = {}
    for c, ts in tipos.items():
        if "texto" in ts:
            esquema[c] = "texto"
        elif len(presente_em[c]) < len(FONTES) or "real" in ts:
            esquema[c] = "real"          # ausente em alguma fonte -> vira NULL, entao real
        else:
            esquema[c] = "inteiro"
    ordem = [c for c in ORDEM_VS if c in esquema] + sorted(c for c in esquema if c not in ORDEM_VS)
    return {c: esquema[c] for c in ordem}


def aplicar_tipos(df, esquema):
    for c, t in esquema.items():
        if c not in df.columns:
            df[c] = None if t == "texto" else np.nan
        if t == "texto":
            if c == "vs_ano":
                num = pd.to_numeric(df[c], errors="coerce")
                df[c] = [str(int(v)) if pd.notna(v) else None for v in num]
            else:
                df[c] = df[c].astype(object).where(df[c].notna(), None)
        elif t == "real":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        else:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(-1).astype("int64")
    return df


# ----------------------------------------------------------------------------
# LEITURA DE VS E BLOCOS
# ----------------------------------------------------------------------------
def ler_vs(fid, bbox):
    arq, cam, bioma, fonte = FONTES[fid]
    gdf = pyogrio.read_dataframe(arq, layer=resolver_camada(arq, cam), bbox=bbox)
    if len(gdf) == 0:
        return None
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_ALVO)
    elif str(gdf.crs).upper() != CRS_ALVO and gdf.crs.to_epsg() != 4674:
        gdf = gdf.to_crs(CRS_ALVO)
    geoms = reparar(gdf.geometry.values)
    attrs = gdf.drop(columns=gdf.geometry.name)
    attrs.columns = prefixar_colunas(attrs.columns)
    attrs = attrs.loc[:, ~pd.Index(attrs.columns).duplicated()]
    attrs["vs_bioma"] = bioma
    attrs["vs_fonte"] = fonte
    ok = np.array([g is not None for g in geoms])
    return attrs[ok].reset_index(drop=True), geoms[ok]


def montar_blocos(geoms):
    c = shapely.centroid(geoms)
    x, y = shapely.get_x(c), shapely.get_y(c)
    cel = pd.Series(list(zip(np.floor(x / CELULA_GRAUS).astype(int), np.floor(y / CELULA_GRAUS).astype(int))))
    blocos = []
    for _, idx in cel.groupby(cel).groups.items():
        idx = np.array(sorted(idx, key=lambda i: x[i]))
        for k in range(0, len(idx), MAX_EMBARGOS_POR_BLOCO):
            blocos.append(idx[k:k + MAX_EMBARGOS_POR_BLOCO])
    return blocos


# ----------------------------------------------------------------------------
# PROCESSAMENTO DE UM ARQUIVO DE EMBARGO
# ----------------------------------------------------------------------------
def processar_embargo(chave, cfg, esquema_vs):
    t0 = time.time()
    nome_camada_saida = cfg["camada"] + "_vegsec"
    parciais = {}
    finais = {}
    for versao in VERSOES:
        final = os.path.join(PASTA_SAIDA, f"{cfg['saida']}_x_VegSec_{versao}.gpkg")
        parcial = os.path.join(PASTA_SAIDA, f"{cfg['saida']}_x_VegSec_{versao}.partial.gpkg")
        for p in (parcial,):
            if os.path.exists(p):
                os.remove(p)
        parciais[versao], finais[versao] = parcial, final

    log(f"[{chave}] lendo {cfg['arquivo']}")
    camada = resolver_camada(cfg["arquivo"], cfg["camada"])
    emb = pyogrio.read_dataframe(cfg["arquivo"], layer=camada)
    if emb.crs is None:
        emb = emb.set_crs(CRS_ALVO)
    elif emb.crs.to_epsg() != 4674:
        emb = emb.to_crs(CRS_ALVO)
    n_total = len(emb)
    emb["idx_embargo"] = np.arange(n_total, dtype="int64")
    geoms = reparar(emb.geometry.values)
    sem_geom = np.array([g is None for g in geoms])
    if sem_geom.any():
        log(f"[{chave}] aviso: {int(sem_geom.sum())} embargos sem geometria poligonal valida foram ignorados")
    emb["area_ha_projeto_orig"] = area_ha(geoms)
    attrs_emb = emb.drop(columns=emb.geometry.name)
    if "area_ha" in attrs_emb.columns:
        attrs_emb = attrs_emb.rename(columns={"area_ha": "area_ha_atributo_orig"})
    attrs_emb = attrs_emb.reset_index(drop=True)
    manter = np.where(~sem_geom)[0]
    geoms_ok = geoms[manter]
    blocos = montar_blocos(geoms_ok)
    log(f"[{chave}] {n_total:,} embargos ({len(manter):,} validos) em {len(blocos)} blocos espaciais")

    colunas_emb = list(attrs_emb.columns)
    colunas_saida = colunas_emb + list(esquema_vs) + ["area_ha"]
    primeiro = {v: True for v in VERSOES}
    resumo = {}   # (versao, fonte, bioma) -> [n_pedacos, n_embargos, area_ha]
    n_pedacos = {v: 0 for v in VERSOES}
    emb_com_vs = set()

    for ib, bloco in enumerate(blocos, 1):
        tb = time.time()
        gb = geoms_ok[bloco]
        bbox = tuple(float(v) for v in shapely.total_bounds(gb))
        ini_emb = manter[bloco]                          # posicoes em attrs_emb
        pedacos = {}
        for fid in FONTES:
            lido = ler_vs(fid, bbox)
            if lido is None:
                continue
            attrs_vs, geoms_vs = lido
            if len(geoms_vs) == 0:
                continue
            arvore = STRtree(geoms_vs)
            ia, iv = arvore.query(gb, predicate="intersects")
            if len(ia) == 0:
                continue
            inter = intersecao_robusta(gb[ia], geoms_vs[iv])
            polis = inter.copy()
            tipos = shapely.get_type_id(polis)                       # None -> -1
            vazio = (tipos < 0) | shapely.is_empty(polis)
            for k in np.where(~vazio & (tipos != 3) & (tipos != 6))[0]:
                polis[k] = so_poligonos(polis[k])                    # colecoes -> so a parte poligonal
            polis[vazio] = None
            ok = np.array([g is not None for g in polis])
            if not ok.any():
                continue
            ia, iv, polis = ia[ok], iv[ok], polis[ok]
            a = area_ha(polis)
            if MIN_PEDACO_HA > 0:
                keep = a >= MIN_PEDACO_HA
                ia, iv, polis, a = ia[keep], iv[keep], polis[keep], a[keep]
            if len(ia) == 0:
                continue
            df = pd.concat(
                [attrs_emb.iloc[ini_emb[ia]].reset_index(drop=True), attrs_vs.iloc[iv].reset_index(drop=True)],
                axis=1,
            )
            df["area_ha"] = a
            df["geometry"] = polis
            pedacos[fid] = df

        for versao, fids in VERSOES.items():
            partes = [pedacos[f] for f in fids if f in pedacos]
            if not partes:
                continue
            df = pd.concat(partes, ignore_index=True)
            df = aplicar_tipos(df, esquema_vs)
            geo = np.array([g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g]) for g in df["geometry"].values], dtype=object)
            saida = gpd.GeoDataFrame(df[colunas_saida], geometry=geo, crs=CRS_ALVO)
            pyogrio.write_dataframe(
                saida, parciais[versao], layer=nome_camada_saida, driver="GPKG",
                append=not primeiro[versao],
            )
            primeiro[versao] = False
            n_pedacos[versao] += len(saida)
            emb_com_vs.update(df["idx_embargo"].unique().tolist())
            g = df.groupby(["vs_fonte", "vs_bioma"]).agg(
                n_pedacos=("area_ha", "size"), n_embargos=("idx_embargo", "nunique"), area_ha=("area_ha", "sum")
            )
            for (fonte, bioma), r in g.iterrows():
                acc = resumo.setdefault((versao, fonte, bioma), [0, 0, 0.0])
                acc[0] += int(r.n_pedacos); acc[1] += int(r.n_embargos); acc[2] += float(r.area_ha)
        if ib == 1 or ib % 10 == 0 or ib == len(blocos):
            log(f"[{chave}] bloco {ib}/{len(blocos)}: {len(bloco):,} embargos, "
                + ", ".join(f"{v}={n_pedacos[v]:,}" for v in VERSOES) + f" pedacos ({time.time()-tb:.1f}s no bloco)")

    for versao in VERSOES:
        if os.path.exists(parciais[versao]):
            if os.path.exists(finais[versao]):
                os.remove(finais[versao])
            os.replace(parciais[versao], finais[versao])
        else:
            log(f"[{chave}] {versao}: nenhum pedaco gerado (nenhum embargo intersecta VS)")
    if FALHAS_INTERSECAO["n"]:
        log(f"[{chave}] aviso: {FALHAS_INTERSECAO['n']} intersecoes irrecuperaveis foram descartadas")
    linhas = [
        dict(embargo=chave, versao=v, vs_fonte=f, vs_bioma=b, n_pedacos=r[0], n_embargos=r[1], area_ha_soma=r[2])
        for (v, f, b), r in sorted(resumo.items())
    ]
    log(f"[{chave}] concluido em {(time.time()-t0)/60:.1f} min; embargos com VS (em alguma versao): {len(emb_com_vs):,} de {n_total:,}")
    return linhas


def main():
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    chaves = SOMENTE_ESTES or list(EMBARGOS)
    for c in chaves:
        if c not in EMBARGOS:
            sys.exit(f"chave desconhecida em SOMENTE_ESTES: {c}")
        if not os.path.exists(EMBARGOS[c]["arquivo"]):
            sys.exit(f"arquivo de embargo nao encontrado: {EMBARGOS[c]['arquivo']}")
    for fid, (arq, cam, _, _) in FONTES.items():
        if not os.path.exists(arq):
            sys.exit(f"arquivo de VS nao encontrado: {arq}")
        resolver_camada(arq, cam)
    log("montando esquema de campos da VS ...")
    esquema_vs = montar_esquema_vs()
    log(f"  {len(esquema_vs)} campos vs_ na saida")

    csv_resumo = os.path.join(PASTA_SAIDA, "resumo_Embargos_VegSec.csv")
    todas = []
    if os.path.exists(csv_resumo):
        todas = pd.read_csv(csv_resumo).to_dict("records")
    try:
        for chave in chaves:
            cfg = EMBARGOS[chave]
            marca = os.path.join(PASTA_SAIDA, f"_done_{cfg['saida']}.txt")
            if os.path.exists(marca) and chave not in REFAZER:
                log(f"[{chave}] ja concluido (_done_); pulando")
                continue
            linhas = processar_embargo(chave, cfg, esquema_vs)
            todas = [r for r in todas if r.get("embargo") != chave] + linhas
            pd.DataFrame(todas).to_csv(csv_resumo, index=False, encoding="utf-8-sig")
            with open(marca, "w", encoding="utf-8") as f:
                json.dump({"concluido": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
    except KeyboardInterrupt:
        log("interrompido pelo usuario; arquivos .partial descartaveis. Os arquivos ja concluidos (_done_) estao salvos.")
        return
    res = pd.DataFrame(todas)
    if len(res):
        log("\nResumo (soma das areas dos pedacos, ha):")
        print(res.groupby(["embargo", "versao"])[["n_pedacos", "area_ha_soma"]].sum().to_string(), flush=True)
    log("fim")


if __name__ == "__main__":
    main()
