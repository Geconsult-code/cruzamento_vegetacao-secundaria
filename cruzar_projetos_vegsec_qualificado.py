# -*- coding: utf-8 -*-
"""
Cruzamento espacial: layers de PROJETOS x VEGETACAO SECUNDARIA 2022 QUALIFICADA (INPE).

PLANAVEG / INCRA-CAR - Geoconsult

Este script e a NOVA RODADA do cruzamento espacial, substituindo temporariamente
cruzar_projetos_vegsec.py (que gerava as versoes "2022" e "2022-2024"). Mudancas
em relacao aquele script, conforme instrucao recebida:

  1) Fonte de vegetacao secundaria: SO a versao 2022 QUALIFICADA (polygons de VS
     com area < 2 ha ja removidos por filtro espacial feito externamente), arquivo
     VS_2022_TerraBrasilis_Vegetacao_Secundaria_Qualificada_Brasil.gpkg, camadas
     Vegetacao_Secundaria_<Bioma>_2022_qualificada (6 biomas). NAO USA dados de
     2024 (Amazonia/Cerrado) -- decisao explicita do cliente de aguardar a versao
     qualificada de 2024 antes de decidir como tratar esses 2 biomas.
  2) SO UMA versao de saida por arquivo de projeto (nao mais duas: "2022" e
     "2022-2024"). Nome do gpkg de saida: <nome_arquivo>_x_VegSec_qualificado.gpkg
  3) Lista de arquivos de projeto agora tem 7 arquivos (adicionado
     Pro-Manguezal_IBAMA20260508.gpkg, camada unica 'Pro_Manguezal_IBAMA20260508',
     7.661 feicoes MultiPolygon, CRS ja em EPSG:4674, campos Id/ORIG_FID/area_ha).

Para cada arquivo de projeto e gerado UM GeoPackage:
    <nome_arquivo>_x_VegSec_qualificado.gpkg
dentro de PASTA_SAIDA (Cruzamento_Projetos_VegSec). Cada gpkg tem uma camada de
saida para CADA camada original do arquivo de projeto (nome "<camada_original>_vegsec"),
com o cruzamento espacial (interseccao real) contra os 6 biomas de vegetacao
secundaria QUALIFICADA, e o campo area_ha (area geodesica GRS80).

METODO (igual ao script anterior, ver cruzar_projetos_vegsec.py para detalhes):
  - interseccao CONTROLADA: sjoin(predicate='intersects') + interseccao par-a-par
    via shapely, imune a 'mixed-dimension geometry'.
  - _reparar() / _so_poligonos(): reparo resiliente de geometria invalida.
  - area_ha: pyproj.Geod(ellps='GRS80').geometry_area_perimeter() (geodesica).
  - leitura da vegetacao secundaria por BBOX (pyogrio bbox filter).
  - _colunas_unicas_ci(): sanitiza colisao de nomes de campo case-insensitive
    (bug real encontrado na rodada anterior, ver historico).
  - area_ha ORIGINAL do projeto (quando existe) e preservada como
    area_ha_projeto_orig antes do cruzamento sobrescrever area_ha com a area do
    PEDACO da interseccao.
  - grava primeiro num arquivo temporario fora da pasta do Dropbox, so move para
    o destino final quando o gpkg estiver completo (evita locks/erros de gravacao
    incremental direto na pasta sincronizada).

USO:
    conda activate geo
    python cruzar_projetos_vegsec_qualificado.py

Por padrao roda so o arquivo NOVO (Pro-Manguezal, o unico ainda nao testado com
este pipeline) para validar antes do lote completo. Confira no console que:
  - o CRS foi lido corretamente (deve vir direto em EPSG:4674, sem precisar do
    fallback sqlite/ESRI morph);
  - as 6 camadas de vegsec qualificada foram encontradas e cruzadas sem erro de
    campo (nao deve aparecer nenhum aviso "colunas renomeadas por colisao", mas
    se aparecer nao e erro -- e so o saneamento automatico ja usado antes).
Depois de validar, esvaziar SOMENTE_ESTES para rodar os 7 arquivos.
"""

import os
import re
import shutil
import sqlite3
import tempfile
import time
import traceback
import warnings

# Dropbox sincroniza a pasta de saida em segundo plano e pode travar o arquivo
# .gpkg brevemente durante gravacoes incrementais. Aumenta o timeout do SQLite
# para o GDAL tentar de novo em vez de falhar na hora. Precisa ser setado ANTES
# de importar geopandas/pyogrio.
os.environ.setdefault("OGR_SQLITE_BUSY_TIMEOUT", "30000")  # ms

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from pyproj import Geod
from shapely.ops import unary_union

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ============================== CONFIG ==============================

PASTA_PROJETOS = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE"
PASTA_VEGSEC_INPE = os.path.join(PASTA_PROJETOS, "INPE_Vegetacao_Secundaria")
PASTA_SAIDA = os.path.join(PASTA_PROJETOS, "Cruzamento_Projetos_VegSec")

# Fonte UNICA: VS 2022 QUALIFICADA (filtro de area minima 2 ha ja aplicado
# externamente). NAO usar dados de 2024 nesta rodada.
VEGSEC_QUALIFICADA = os.path.join(
    PASTA_VEGSEC_INPE, "VS_2022_TerraBrasilis_Vegetacao_Secundaria_Qualificada_Brasil.gpkg"
)

BIOMAS = ["Amazonia", "Cerrado", "Caatinga", "Mata_Atlantica", "Pampa", "Pantanal"]
LAYER_VEGSEC_QUALIFICADA = {b: f"Vegetacao_Secundaria_{b}_2022_qualificada" for b in BIOMAS}

CRS_ALVO = "EPSG:4674"  # SIRGAS2000 geografico - padrao do projeto
GEOD = Geod(ellps="GRS80")

PROJETOS = [
    "IBAMA_Projetos_Recooperar_2025_com_area.gpkg",
    "IBAMA_Projetos_Recooperar_2026_com_area.gpkg",
    "ICMBio_Projetos_GEF_Terrestre_2026_com_area.gpkg",
    "ICMBio_Projetos_Restauracao_2026_com_area.gpkg",
    "Pro-Manguezal_IBAMA20260508.gpkg",
    "Terras_Indigenas_FUNAI20260507.gpkg",
    "Unidades_Conservacao_CNUC20260507.gpkg",
]

# Validacao: comeca so pelo arquivo NOVO (Pro-Manguezal, ainda nao testado com
# este pipeline; pequeno, 7.661 feicoes, CRS ja correto -- bom primeiro teste).
# Esvaziar (SOMENTE_ESTES = []) para rodar todos os 7 arquivos.
SOMENTE_ESTES = ["Pro-Manguezal_IBAMA20260508.gpkg"]

SUFIXO_SAIDA = "qualificado"  # <nome_arquivo>_x_VegSec_qualificado.gpkg

# ============================== GEOMETRIA (padrao do projeto) ==============================


def _so_poligonos(geom):
    """Extrai so a parte poligonal (Polygon/MultiPolygon) de uma geometria, descarta
    linha/ponto (protege contra 'mixed-dimension' apos make_valid/interseccao)."""
    if geom is None or geom.is_empty:
        return None
    gt = geom.geom_type
    if gt in ("Polygon", "MultiPolygon"):
        return geom
    if gt == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
        if not polys:
            return None
        u = unary_union(polys)
        return u if u.geom_type in ("Polygon", "MultiPolygon") else None
    return None


def _reparar(geom):
    """Repara geometria de forma resiliente: valida -> passa; senao make_valid
    (protegido por try/except); se lancar excecao, cai para buffer(0); se ainda
    falhar, descarta (retorna None) em vez de derrubar o processamento inteiro."""
    if geom is None or geom.is_empty:
        return None
    try:
        if geom.is_valid:
            return geom
    except Exception:
        pass
    try:
        g2 = shapely.make_valid(geom)
        g2 = _so_poligonos(g2)
        if g2 is not None and not g2.is_empty:
            return g2
    except Exception:
        pass
    try:
        g3 = geom.buffer(0)
        g3 = _so_poligonos(g3)
        if g3 is not None and not g3.is_empty:
            return g3
    except Exception:
        pass
    return None


def verificar_e_reparar(gdf, rotulo=""):
    """Verifica validade de cada geometria e repara quando necessario. Imprime um
    resumo (invalidas encontradas / reparadas / descartadas por irreparaveis)."""
    n0 = len(gdf)
    if n0 == 0:
        return gdf
    invalidas = 0
    reparadas = 0
    descartadas = 0
    geoms_novas = []
    for g in gdf.geometry:
        valida = False
        try:
            valida = g is not None and not g.is_empty and g.is_valid
        except Exception:
            valida = False
        if valida:
            geoms_novas.append(g)
            continue
        invalidas += 1
        g2 = _reparar(g)
        if g2 is None:
            descartadas += 1
        else:
            reparadas += 1
        geoms_novas.append(g2)
    gdf = gdf.copy()
    gdf["geometry"] = geoms_novas
    gdf = gdf[gdf.geometry.notna()]
    if invalidas:
        print(f"    [{rotulo}] geometria: {n0} feicoes, {invalidas} invalidas "
              f"({reparadas} reparadas, {descartadas} descartadas por irreparaveis)")
    return gdf


def area_ha_geodesica(geom):
    """Area geodesica em hectares (GRS80), mesmo padrao do resto do projeto
    (equivalente ao QgsDistanceArea elipsoidal)."""
    if geom is None or geom.is_empty:
        return 0.0
    try:
        area_m2, _ = GEOD.geometry_area_perimeter(geom)
        return abs(area_m2) / 10000.0
    except Exception:
        return 0.0


# ============================== CRS ROBUSTO ==============================


def _crs_via_sqlite_esri_morph(caminho_gpkg, nome_layer):
    """Fallback para camadas em que o GDAL nao resolveu o CRS automaticamente
    (ex.: srs_id customizado nao mapeado para EPSG, WKT1 estilo ESRI). Le o WKT
    direto das tabelas gpkg_contents/gpkg_spatial_ref_sys do proprio geopackage e
    tenta corrigir via ESRI->OGC morph (osgeo.osr) antes de repassar ao pyproj."""
    try:
        con = sqlite3.connect(caminho_gpkg)
        cur = con.cursor()
        cur.execute("SELECT srs_id FROM gpkg_contents WHERE table_name = ?", (nome_layer,))
        row = cur.fetchone()
        if not row:
            con.close()
            return None
        srs_id = row[0]
        cur.execute("SELECT definition FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (srs_id,))
        row2 = cur.fetchone()
        con.close()
        if not row2 or not row2[0]:
            return None
        wkt = row2[0]
        try:
            from osgeo import osr

            srs = osr.SpatialReference()
            srs.ImportFromWkt(wkt)
            srs.MorphFromESRI()
            wkt2 = srs.ExportToWkt()
            import pyproj

            return pyproj.CRS.from_wkt(wkt2)
        except Exception:
            import pyproj

            return pyproj.CRS.from_wkt(wkt)
    except Exception:
        return None


def ler_camada_projeto(caminho, nome_layer):
    """Le uma camada de projeto e garante CRS valido, reprojetado para CRS_ALVO."""
    gdf = gpd.read_file(caminho, layer=nome_layer, engine="pyogrio")
    if gdf.crs is None:
        print(f"    [{nome_layer}] CRS nao reconhecido pelo GDAL, tentando via sqlite/ESRI morph...")
        crs = _crs_via_sqlite_esri_morph(caminho, nome_layer)
        if crs is None:
            print(f"    [{nome_layer}] !! NAO FOI POSSIVEL DETERMINAR O CRS. "
                  f"ASSUMINDO {CRS_ALVO} -- CONFERIR MANUALMENTE!")
            gdf = gdf.set_crs(CRS_ALVO, allow_override=True)
        else:
            print(f"    [{nome_layer}] CRS resolvido via sqlite/ESRI morph: {crs.name}")
            gdf = gdf.set_crs(crs, allow_override=True)
    if gdf.crs.to_string() != CRS_ALVO:
        gdf = gdf.to_crs(CRS_ALVO)
    # Muitos dos arquivos de projeto ja trazem seu proprio campo 'area_ha' (area do
    # poligono INTEIRO do projeto). O cruzamento grava um 'area_ha' NOVO (area so do
    # pedaco resultante da interseccao) -- para nao apagar silenciosamente a area
    # original, ela e preservada com outro nome antes de prosseguir.
    for c in list(gdf.columns):
        if c != "geometry" and c.lower() == "area_ha":
            gdf = gdf.rename(columns={c: "area_ha_projeto_orig"})
    return gdf


def _colunas_unicas_ci(colunas):
    """Garante nomes de coluna unicos ignorando maiusculas/minusculas (o GeoPackage
    trata nomes de campo como case-insensitive, mesmo que o pandas nao) e limitados a
    63 caracteres (limite do GeoPackage). Colisoes viram <nome>_2, <nome>_3 etc."""
    vistas = {}
    novas = []
    for c in colunas:
        base = str(c)[:60]
        chave = base.lower()
        if chave not in vistas:
            vistas[chave] = 1
            novas.append(base)
        else:
            vistas[chave] += 1
            candidato = f"{base}_{vistas[chave]}"
            while candidato.lower() in vistas:
                vistas[chave] += 1
                candidato = f"{base}_{vistas[chave]}"
            vistas[candidato.lower()] = 1
            novas.append(candidato)
    return novas


# ============================== LEITURA DE VEG. SECUNDARIA POR BBOX ==============================


def ler_vegsec_bbox(caminho, nome_layer, bbox):
    """Le so as feicoes de vegetacao secundaria dentro do bbox (pyogrio bbox filter),
    evitando carregar o arquivo inteiro."""
    try:
        gdf = gpd.read_file(caminho, layer=nome_layer, bbox=bbox, engine="pyogrio")
    except Exception as e:
        print(f"    !! erro lendo {nome_layer} (bbox): {e}")
        return gpd.GeoDataFrame(geometry=[], crs=CRS_ALVO)
    if len(gdf) == 0:
        return gdf
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_ALVO, allow_override=True)
    elif gdf.crs.to_string() != CRS_ALVO:
        gdf = gdf.to_crs(CRS_ALVO)
    return gdf


def fontes_vegsec():
    """Retorna dict {bioma: (caminho_gpkg, nome_layer, fonte)}. Fonte UNICA nesta
    rodada: VS 2022 qualificada, para os 6 biomas."""
    fontes = {}
    for b in BIOMAS:
        fontes[b] = (VEGSEC_QUALIFICADA, LAYER_VEGSEC_QUALIFICADA[b], "2022_qualificada")
    return fontes


# ============================== INTERSECCAO CONTROLADA ==============================


def intersecao_controlada(gdf_a, gdf_b, campos_b):
    """sjoin(predicate='intersects') + interseccao par-a-par via shapely, protegida
    contra mixed-dimension e geometrias invalidas (mesmo metodo de cruzar_vegsec.py)."""
    colunas_saida = [c for c in gdf_a.columns if c != "geometry"] + campos_b + ["geometry"]
    if len(gdf_a) == 0 or len(gdf_b) == 0:
        return gpd.GeoDataFrame(columns=colunas_saida, geometry="geometry", crs=CRS_ALVO)

    gdf_a = gdf_a.reset_index(drop=True)
    gdf_b = gdf_b.reset_index(drop=True)

    pares = gpd.sjoin(gdf_a[["geometry"]], gdf_b[["geometry"]], how="inner", predicate="intersects")
    if len(pares) == 0:
        return gpd.GeoDataFrame(columns=colunas_saida, geometry="geometry", crs=CRS_ALVO)

    linhas = []
    for idx_a, idx_b in zip(pares.index, pares["index_right"]):
        geom_a = gdf_a.geometry.iloc[idx_a]
        geom_b = gdf_b.geometry.iloc[idx_b]
        try:
            inter = geom_a.intersection(geom_b)
        except Exception:
            ga = _reparar(geom_a)
            gb = _reparar(geom_b)
            if ga is None or gb is None:
                continue
            try:
                inter = ga.intersection(gb)
            except Exception:
                continue
        inter = _so_poligonos(inter)
        if inter is None or inter.is_empty:
            continue
        row = gdf_a.drop(columns="geometry").iloc[idx_a].to_dict()
        for c in campos_b:
            row[c] = gdf_b.iloc[idx_b][c]
        row["geometry"] = inter
        linhas.append(row)

    if not linhas:
        return gpd.GeoDataFrame(columns=colunas_saida, geometry="geometry", crs=CRS_ALVO)

    out = gpd.GeoDataFrame(linhas, geometry="geometry", crs=CRS_ALVO)
    return out


# ============================== PIPELINE POR CAMADA DE PROJETO ==============================


def cruzar_camada(caminho_projeto, nome_layer):
    print(f"  -- camada {nome_layer!r} (vegetacao secundaria 2022 qualificada) --")
    t0 = time.time()

    gdf_p = ler_camada_projeto(caminho_projeto, nome_layer)
    gdf_p = verificar_e_reparar(gdf_p, rotulo=f"{nome_layer} (projeto)")
    if len(gdf_p) == 0:
        print("    sem feicoes validas, pulando.")
        return None

    bbox = tuple(gdf_p.total_bounds)

    fontes = fontes_vegsec()
    resultados = []
    for bioma, (caminho_vs, nome_layer_vs, fonte) in fontes.items():
        vs = ler_vegsec_bbox(caminho_vs, nome_layer_vs, bbox)
        if len(vs) == 0:
            continue
        vs = verificar_e_reparar(vs, rotulo=f"vegsec {bioma} ({fonte})")
        if len(vs) == 0:
            continue

        campos_vs_originais = [c for c in vs.columns if c != "geometry"]
        vs2 = vs.rename(columns={c: f"vs_{c}" for c in campos_vs_originais})
        campos_vs = [f"vs_{c}" for c in campos_vs_originais]

        inter = intersecao_controlada(gdf_p, vs2, campos_vs)
        if len(inter) == 0:
            continue
        inter["vs_bioma"] = bioma
        inter["vs_fonte"] = fonte
        resultados.append(inter)
        print(f"    {bioma} ({fonte}): {len(inter)} pedacos")

    if not resultados:
        print("    nenhuma interseccao encontrada.")
        return None

    saida = gpd.GeoDataFrame(pd.concat(resultados, ignore_index=True), crs=CRS_ALVO)

    # A vegsec qualificada pode ter campos proprios (ex.: um campo de area/flag do
    # filtro de 2ha) que, prefixados com vs_, podem colidir (ignorando maiusculas/
    # minusculas, que e como o GeoPackage compara nomes de campo) com vs_bioma/
    # vs_fonte que este script adiciona. Sem esse saneamento, a gravacao falha com
    # "Error adding field" (foi o que aconteceu na rodada anterior cruzando com VS 2024).
    colunas_antes = list(saida.columns)
    colunas_novas = _colunas_unicas_ci(colunas_antes)
    if colunas_novas != colunas_antes:
        renomeadas = {a: n for a, n in zip(colunas_antes, colunas_novas) if a != n}
        print(f"    [aviso] colunas renomeadas por colisao case-insensitive: {renomeadas}")
        saida.columns = colunas_novas

    saida["area_ha"] = saida.geometry.apply(area_ha_geodesica)
    dt = time.time() - t0
    print(f"    total: {len(saida)} pedacos / {saida['area_ha'].sum():,.1f} ha  ({dt:.1f}s)")
    return saida


# ============================== MAIN ==============================


def nome_camada_saida(nome_layer):
    nome = re.sub(r"[^0-9A-Za-z_]", "_", nome_layer)
    return f"{nome}_vegsec"[:63]


def escrever_camada_com_retry(gdf, caminho_gpkg, camada, modo, tentativas=5):
    """Grava uma camada no gpkg com retry/backoff. Protege contra locks transitorios
    do SQLite (antivirus, indexador do Windows etc.) alem do OGR_SQLITE_BUSY_TIMEOUT
    ja setado globalmente."""
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            gdf.to_file(caminho_gpkg, layer=camada, driver="GPKG", mode=modo)
            return True
        except Exception as e:
            ultimo_erro = e
            print(f"    !! tentativa {tentativa}/{tentativas} falhou gravando camada "
                  f"{camada!r} ({modo}): {e}")
            time.sleep(2 * tentativa)
    print(f"    !! DESISTI de gravar a camada {camada!r} apos {tentativas} tentativas: {ultimo_erro}")
    return False


def processar_arquivo(nome_arquivo):
    caminho = os.path.join(PASTA_PROJETOS, nome_arquivo)
    base = os.path.splitext(nome_arquivo)[0]
    layers = list(pyogrio.list_layers(caminho)[:, 0])
    print(f"\n{'=' * 90}\n{nome_arquivo}  ({len(layers)} camadas: {layers})\n{'=' * 90}")

    os.makedirs(PASTA_SAIDA, exist_ok=True)

    caminho_saida = os.path.join(PASTA_SAIDA, f"{base}_x_VegSec_{SUFIXO_SAIDA}.gpkg")

    # Grava primeiro num arquivo TEMPORARIO FORA da pasta do Dropbox: gravar varias
    # camadas incrementalmente (modo 'a') direto na pasta sincronizada corre risco
    # real de o Dropbox travar o arquivo no meio. So no final, com o gpkg pronto,
    # ele e copiado de uma vez para o destino definitivo.
    caminho_tmp = os.path.join(
        tempfile.gettempdir(), f"_tmp_{base}_x_VegSec_{SUFIXO_SAIDA}_{os.getpid()}.gpkg"
    )
    if os.path.exists(caminho_tmp):
        os.remove(caminho_tmp)

    print(f"\n### VegSec 2022 qualificada -> {caminho_saida}")
    print(f"    (gravando primeiro em {caminho_tmp}, fora do Dropbox)")
    primeira = True
    for nome_layer in layers:
        try:
            saida = cruzar_camada(caminho, nome_layer)
        except Exception:
            print(f"    !! ERRO na camada {nome_layer}:")
            traceback.print_exc()
            continue
        if saida is None or len(saida) == 0:
            continue
        camada_saida = nome_camada_saida(nome_layer)
        modo = "w" if primeira else "a"
        ok = escrever_camada_com_retry(saida, caminho_tmp, camada_saida, modo)
        if ok:
            primeira = False

    if primeira:
        print("    (nenhuma camada gerou resultado -- gpkg de saida nao foi criado)")
        return

    if os.path.exists(caminho_saida):
        os.remove(caminho_saida)  # regrava do zero a cada execucao (evita duplicar camadas)
    shutil.move(caminho_tmp, caminho_saida)
    print(f"    copiado para o destino final: {caminho_saida}")


def main():
    alvos = SOMENTE_ESTES if SOMENTE_ESTES else PROJETOS
    print(f"Processando {len(alvos)} arquivo(s) de projeto: {alvos}")
    print("Fonte de vegetacao secundaria: 2022 QUALIFICADA (filtro >= 2 ha), 6 biomas.")
    t0 = time.time()
    for nome_arquivo in alvos:
        processar_arquivo(nome_arquivo)
    print(f"\nCONCLUIDO em {time.time() - t0:.1f}s.")


if __name__ == "__main__":
    main()
