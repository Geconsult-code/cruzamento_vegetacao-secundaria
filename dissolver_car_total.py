"""
dissolver_car_total.py

Gera uma camada com a AREA DISSOLVIDA de TODOS os imoveis cadastrados no
SICAR/CAR por estado, SEPARADA em duas categorias -- "analisado" e
"nao_analisado" -- para facilitar interseccoes posteriores. Cobre todo o
CAR (coerentes e incoerentes com o INCRA, ou seja, SEM nenhum filtro de
conformidade SICARxINCRA). Imoveis "Cancelado" ja ficam de fora porque nao
entram nos gpkg <UF>_analisados/<UF>_trabalho gerados pela etapa 'preparar'
do pacote conformidade (processar_lote.py).

Fontes por estado (pasta _saida_<UF> dentro de INCRA-CAR):
  <UF>_analisados.gpkg  -> camada AREA_IMOVEL -> categoria "analisado"
  <UF>_trabalho.gpkg    -> camada AREA_IMOVEL (Em Analise + Aguardando)
                            -> categoria "nao_analisado"

Dentro de cada categoria os imoveis sao dissolvidos entre si (a fronteira
entre imoveis da MESMA categoria desaparece, geometria simplificada).

PRIORIDADE entre categorias: "analisado" tem prioridade e fica intacto.
Depois de dissolvida, a geometria "nao_analisado" e RECORTADA (diferenca)
pela geometria "analisado" da mesma UF, para eliminar qualquer sobreposicao
remanescente entre as duas categorias -- mesmo principio ja usado no filtro
'sobrepoe_analisado' do pipeline de conformidade (Analisado nunca e
removido/alterado; quem sobrepoe cede).

Saida: UM unico GeoPackage com UMA camada 'CAR_total_UF' -- ate 54 feicoes
(2 por UF: analisado + nao_analisado), com campos:
  uf                sigla do estado
  categoria         "analisado" ou "nao_analisado"
  n_imoveis_origem  n. de registros de origem daquela categoria (antes do dissolve/recorte)
  area_ha           area geodesica (GRS80) do poligono final, em hectares
                     (para "nao_analisado", ja e a area POS-recorte)

Uso: ajustar CONFIG abaixo e rodar no ambiente conda 'geo' (mesmo ambiente
do pacote 'conformidade' / processar_lote.py).

    python dissolver_car_total.py

Resiliente: se uma UF falhar, o erro fica registrado e o lote segue para a
proxima (mesmo padrao do processar_lote.py). Rodar de novo so processa as
UFs que ainda nao tem as DUAS categorias completas na saida (ou as listadas
em REFAZER) -- nao refaz o que ja foi gravado. As duas categorias de uma UF
sao sempre (re)processadas juntas, porque o recorte do nao_analisado
depende do analisado da mesma UF.
"""

import time
import traceback
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import Geod
from shapely.geometry import MultiPolygon
from shapely.ops import unary_union

try:
    from shapely import make_valid as _make_valid
except ImportError:  # shapely < 2.0
    from shapely.validation import make_valid as _make_valid

# ----------------------------- CONFIG -----------------------------------
PASTA_BASE = Path(r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR")
SAIDA = PASTA_BASE / "CAR_total_dissolvido_UF.gpkg"
CAMADA_SAIDA = "CAR_total_UF"

SOMENTE_ESTES = []   # ex.: ['AC', 'PA'] -- vazio = todos os estados encontrados (pastas _saida_*)
PULAR = []           # UFs a pular
REFAZER = []         # UFs para reprocessar (as duas categorias) mesmo se ja estiverem na saida

ARQ_ANALISADO = "{uf}_analisados.gpkg"
ARQ_NAO_ANALISADO = "{uf}_trabalho.gpkg"
# --------------------------------------------------------------------------

GEOD = Geod(ellps="GRS80")


def _so_poligonos(geom):
    """Extrai so a parte poligonal de uma geometria (descarta linha/ponto
    remanescentes de reparo de geometria invalida, dissolve ou recorte)."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if geom.geom_type == "GeometryCollection":
        partes = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if not partes:
            return None
        return unary_union(partes)
    return None


def _reparar(geom):
    """Repara geometria invalida de forma resiliente: tenta make_valid, cai
    para buffer(0) se make_valid falhar/lancar excecao, descarta se nada
    funcionar. Mesma logica usada em cruzar_vegsec.py para o caso do AM
    (make_valid as vezes lanca IllegalArgumentException em geometrias muito
    patologicas)."""
    if geom is None or geom.is_empty:
        return None
    if geom.is_valid:
        return _so_poligonos(geom)

    reparada = None
    try:
        reparada = _make_valid(geom)
    except Exception:
        reparada = None

    if reparada is None or reparada.is_empty:
        try:
            reparada = geom.buffer(0)
        except Exception:
            reparada = None

    if reparada is None or reparada.is_empty:
        return None
    return _so_poligonos(reparada)


def area_ha_geodesica(geom):
    if geom is None or geom.is_empty:
        return 0.0
    area, _ = GEOD.geometry_area_perimeter(geom)
    return abs(area) / 10_000.0


def _multipolygon(geom):
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    return geom


def dissolver_arquivo(uf, categoria, caminho):
    """Le um gpkg (camada AREA_IMOVEL), repara e dissolve TODOS os imoveis
    dele entre si (uma unica categoria -> uma unica geometria)."""
    if not caminho.exists():
        print(f"  [{uf}/{categoria}] aviso: {caminho.name} nao encontrado, pulando")
        return None, 0

    gdf = gpd.read_file(caminho, layer="AREA_IMOVEL")
    n_base = len(gdf)
    if n_base == 0:
        print(f"  [{uf}/{categoria}] sem feicoes, pulando")
        return None, 0

    geoms_ok = []
    descartadas = 0
    for geom in gdf.geometry:
        g = _reparar(geom)
        if g is None or g.is_empty:
            descartadas += 1
            continue
        geoms_ok.append(g)

    if not geoms_ok:
        print(f"  [{uf}/{categoria}] todas as geometrias descartadas, pulando")
        return None, n_base

    dissolvido = unary_union(geoms_ok)
    dissolvido = _so_poligonos(dissolvido)
    dissolvido = _multipolygon(dissolvido)
    if dissolvido is not None and descartadas:
        print(f"  [{uf}/{categoria}] {descartadas} geometrias descartadas na leitura")
    return dissolvido, n_base


def processar_uf(uf, pasta):
    t0 = time.time()
    resultados = []

    geom_analisado, n_analisado = dissolver_arquivo(
        uf, "analisado", pasta / ARQ_ANALISADO.format(uf=uf)
    )
    geom_nao_analisado, n_nao_analisado = dissolver_arquivo(
        uf, "nao_analisado", pasta / ARQ_NAO_ANALISADO.format(uf=uf)
    )

    if geom_analisado is not None:
        area_ha = area_ha_geodesica(geom_analisado)
        resultados.append({
            "uf": uf, "categoria": "analisado",
            "n_imoveis_origem": n_analisado, "area_ha": area_ha,
            "geometry": geom_analisado,
        })
        print(f"  [{uf}/analisado] {n_analisado} imoveis base -> {area_ha:,.1f} ha")

    if geom_nao_analisado is not None:
        if geom_analisado is not None:
            # PRIORIDADE: analisado fica intacto; nao_analisado e recortado
            # para eliminar sobreposicao remanescente entre as categorias.
            try:
                recortado = geom_nao_analisado.difference(geom_analisado)
            except Exception:
                # geometrias muito complexas podem falhar na 1a tentativa;
                # repara de novo (buffer(0)) e tenta uma vez mais
                ga = _reparar(geom_analisado) or geom_analisado
                gn = _reparar(geom_nao_analisado) or geom_nao_analisado
                recortado = gn.difference(ga)
            recortado = _so_poligonos(recortado)
            recortado = _multipolygon(recortado)
        else:
            recortado = geom_nao_analisado

        area_ha = area_ha_geodesica(recortado) if recortado is not None else 0.0
        if recortado is None:
            # todo o nao_analisado estava contido no analisado
            recortado = MultiPolygon([])
        resultados.append({
            "uf": uf, "categoria": "nao_analisado",
            "n_imoveis_origem": n_nao_analisado, "area_ha": area_ha,
            "geometry": recortado,
        })
        print(f"  [{uf}/nao_analisado] {n_nao_analisado} imoveis base -> {area_ha:,.1f} ha "
              f"(pos-recorte contra analisado)")

    dt = time.time() - t0
    print(f"  [{uf}] concluido em {dt:.1f}s")
    return resultados


def ler_saida_existente():
    if not SAIDA.exists():
        return None
    try:
        return gpd.read_file(SAIDA, layer=CAMADA_SAIDA)
    except Exception:
        return None


def main():
    ufs_disponiveis = sorted(
        p.name.replace("_saida_", "")
        for p in PASTA_BASE.glob("_saida_*")
        if p.is_dir()
    )
    ufs = SOMENTE_ESTES or ufs_disponiveis
    ufs = [u for u in ufs if u not in PULAR]

    existente = ler_saida_existente()
    if existente is not None:
        categorias_por_uf = existente.groupby("uf")["categoria"].apply(set).to_dict()
    else:
        categorias_por_uf = {}

    esperado = {"analisado", "nao_analisado"}
    ufs_completas = {
        uf for uf, cats in categorias_por_uf.items()
        if esperado.issubset(cats) and uf not in REFAZER
    }
    pendentes = [uf for uf in ufs if uf not in ufs_completas]

    print(f"UFs disponiveis: {len(ufs_disponiveis)} | a processar: {len(pendentes)} "
          f"| ja completas (puladas): {len(set(ufs) & ufs_completas)}")

    novos_resultados = []
    falhas = []

    for uf in pendentes:
        pasta = PASTA_BASE / f"_saida_{uf}"
        if not pasta.exists():
            print(f"  [{uf}] pasta nao encontrada: {pasta}")
            falhas.append(uf)
            continue
        try:
            r = processar_uf(uf, pasta)
            novos_resultados.extend(r)
        except Exception as e:
            print(f"  [{uf}] ERRO: {e}")
            traceback.print_exc()
            falhas.append(uf)

    if not novos_resultados:
        print("\nNada novo para gravar.")
    else:
        novo_gdf = gpd.GeoDataFrame(novos_resultados, geometry="geometry", crs="EPSG:4674")
        ufs_novas = set(novo_gdf["uf"])
        if existente is not None:
            existente_restante = existente[~existente["uf"].isin(ufs_novas)]
            final = pd.concat([existente_restante, novo_gdf], ignore_index=True)
            final = gpd.GeoDataFrame(final, geometry="geometry", crs="EPSG:4674")
        else:
            final = novo_gdf
        final = final.sort_values(["uf", "categoria"]).reset_index(drop=True)
        final.to_file(SAIDA, layer=CAMADA_SAIDA, driver="GPKG")
        print(f"\nGravado: {SAIDA} (camada {CAMADA_SAIDA}), {len(final)} feicoes")
        print(final[["uf", "categoria", "n_imoveis_origem", "area_ha"]].to_string(index=False))

    if falhas:
        print(f"\nUFs com falha: {falhas}")


if __name__ == "__main__":
    main()
